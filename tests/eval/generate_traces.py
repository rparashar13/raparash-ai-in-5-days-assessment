"""Generate evaluation traces for the Ambient Expense Agent workflow.

Runs all scenarios in `tests/eval/datasets/basic-dataset.json` through the local
ADK 2.0 workflow runner, intercepts `RequestInput` human-in-the-loop pauses
(automatically approving clean high-value requests and rejecting prompt-injection
security events), and saves the populated trace dataset to
`artifacts/traces/generated_traces.json`.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.adk.runners import InMemoryRunner
from google.genai import types

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

from expense_agent.agent import app  # noqa: E402


def _event_to_trace_dict(event: Any) -> dict[str, Any] | None:
    """Convert an ADK Event into the AgentEvent dict structure expected by agents-cli eval grade."""
    content_dict = None
    if event.content and event.content.parts:
        parts_list = []
        for part in event.content.parts:
            p_dump = part.model_dump(exclude_none=True, by_alias=True, mode="json")
            p_dump.pop("thoughtSignature", None)
            p_dump.pop("thought_signature", None)
            if p_dump:
                parts_list.append(p_dump)
        if parts_list:
            content_dict = {
                "role": event.content.role or "model",
                "parts": parts_list,
            }

    state_delta = None
    if event.actions and event.actions.state_delta:
        state_delta = event.actions.state_delta

    if not content_dict:
        return None

    ts = getattr(event, "timestamp", None)
    if isinstance(ts, (int, float)):
        event_time = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()
    else:
        event_time = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

    trace_event: dict[str, Any] = {
        "author": getattr(event, "author", None) or "expense_processor",
        "content": content_dict,
        "event_time": event_time,
    }
    if state_delta:
        trace_event["state_delta"] = state_delta
    return trace_event


def _find_request_input_call(events: list[Any]) -> tuple[str, dict[str, Any]] | None:
    """Find an `adk_request_input` function call in a list of ADK events."""
    for ev in events:
        if not ev.content or not ev.content.parts:
            continue
        for part in ev.content.parts:
            fc = part.function_call
            if fc and fc.name == "adk_request_input":
                return (fc.id or "human_approval", dict(fc.args or {}))
    return None


async def _run_scenario(runner: InMemoryRunner, case: dict[str, Any]) -> dict[str, Any]:
    """Run a single evaluation case through the workflow, handling HITL pauses."""
    case_id = case["eval_case_id"]
    prompt_content = types.Content.model_validate(case["prompt"])
    user_id = f"eval_{case_id}"

    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id=user_id,
    )

    # Turn 0: initial submission
    turn0_raw_events: list[Any] = []
    async for ev in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=prompt_content,
    ):
        turn0_raw_events.append(ev)

    turn0_trace_events = [
        te for te in (_event_to_trace_dict(e) for e in turn0_raw_events) if te is not None
    ]
    turns: list[dict[str, Any]] = [
        {
            "turn_index": 0,
            "turn_id": "turn_0",
            "events": turn0_trace_events,
        }
    ]

    # Check if the workflow paused for human input via RequestInput
    interrupt_info = _find_request_input_call(turn0_raw_events)
    final_events = turn0_raw_events

    if interrupt_info is not None:
        interrupt_id, fc_args = interrupt_info
        payload = fc_args.get("payload") or {}
        security_screen = payload.get("security_screen") or {}
        is_prompt_injection = bool(
            security_screen.get("prompt_injection_detected")
            or payload.get("event_type") == "SECURITY_EVENT_PROMPT_INJECTION"
        )

        # Reject prompt injections; approve clean high-value requests
        human_decision = "rejected" if is_prompt_injection else "approved"
        print(
            f"  [{case_id}] Intercepted RequestInput ({interrupt_id}): "
            f"prompt_injection={is_prompt_injection} -> sending human decision='{human_decision}'"
        )

        resume_content = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=interrupt_id,
                        name="adk_request_input",
                        response={"result": human_decision},
                    )
                )
            ],
        )

        turn1_raw_events: list[Any] = []
        async for ev in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=resume_content,
        ):
            turn1_raw_events.append(ev)

        user_resume_trace = {
            "author": "user",
            "content": resume_content.model_dump(exclude_none=True, by_alias=True, mode="json"),
            "event_time": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
        }
        turn1_trace_events = [user_resume_trace] + [
            te for te in (_event_to_trace_dict(e) for e in turn1_raw_events) if te is not None
        ]
        turns.append(
            {
                "turn_index": 1,
                "turn_id": "turn_1",
                "events": turn1_trace_events,
            }
        )
        final_events = turn1_raw_events

    # Extract final text response from the terminal turn
    final_text = ""
    for ev in reversed(final_events):
        if ev.content and ev.content.parts:
            texts = [p.text for p in ev.content.parts if p.text]
            if texts:
                final_text = "".join(texts)
                break

    populated_case = {
        "eval_case_id": case_id,
        "prompt": case["prompt"],
        "responses": [
            {
                "response": {
                    "role": "model",
                    "parts": [{"text": final_text}],
                }
            }
        ],
        "agent_data": {
            "turns": turns,
        },
    }
    print(f"  [{case_id}] Final response: {final_text}")
    return populated_case


async def main() -> None:
    dataset_path = PROJECT_ROOT / "tests" / "eval" / "datasets" / "basic-dataset.json"
    output_path = PROJECT_ROOT / "artifacts" / "traces" / "generated_traces.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(dataset_path, encoding="utf-8") as f:
        dataset = json.load(f)

    runner = InMemoryRunner(app=app)
    populated_cases = []
    print(f"Running {len(dataset['eval_cases'])} evaluation cases...")
    for case in dataset["eval_cases"]:
        populated = await _run_scenario(runner, case)
        populated_cases.append(populated)

    output_payload = {"eval_cases": populated_cases}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2)

    print(f"\nSaved {len(populated_cases)} populated traces to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
