"""ADK 2.0 Graph Workflow for the Ambient Expense Agent."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from google.adk import Context, Event, Workflow
from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.events import RequestInput
from google.adk.models import Gemini
from google.adk.workflow import START, node
from google.genai import types
from pydantic import BaseModel, Field

from expense_agent.config import APPROVAL_THRESHOLD, MODEL


# ---------------------------------------------------------------------------
# Data Schemas
# ---------------------------------------------------------------------------


class ExpenseReport(BaseModel):
    """Normalized expense claim payload."""

    amount: float = Field(description="Expense amount in USD.")
    submitter: str = Field(default="unknown@company.com", description="Employee email.")
    category: str = Field(default="uncategorized", description="Expense category.")
    description: str = Field(default="", description="Expense description.")
    date: str = Field(default="", description="Expense date (YYYY-MM-DD).")


class RiskReview(BaseModel):
    """Structured risk analysis produced by review_agent."""

    risk_score: int = Field(description="Risk score from 1 (lowest) to 10 (highest).")
    risk_level: str = Field(description="Risk level: LOW, MEDIUM, HIGH, or CRITICAL.")
    alert_summary: str = Field(description="Concise risk alert summary for the human reviewer.")
    flags: list[str] = Field(description="Specific policy, amount, category, or anomaly flags.")
    recommendation: str = Field(description="Recommended human action: APPROVE or REJECT with justification.")


# ---------------------------------------------------------------------------
# Security Helpers: PII Redaction & Prompt-Injection Detection
# ---------------------------------------------------------------------------

# Credit card: 13 to 16 digits separated by optional spaces or hyphens.
_CREDIT_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")

# SSN: formatted XXX-XX-XXXX or unformatted 9-to-11 digit numeric identifiers
# (e.g. 123-45-6789, 123456789, or 14300000000 from the codelab test case).
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{9,11}\b")

_PROMPT_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("bypass_rules", re.compile(r"bypass\s+(?:all\s+)?rules", re.IGNORECASE)),
    ("ignore_instructions", re.compile(r"ignore\s+(?:all\s+|previous\s+|prior\s+)?(?:instructions|rules|policies)", re.IGNORECASE)),
    ("auto_approve_override", re.compile(r"auto[\s-]*approve", re.IGNORECASE)),
    ("approve_without_review", re.compile(r"approve\s+(?:this\s+)?(?:expense\s+)?(?:automatically|immediately|without\s+review)", re.IGNORECASE)),
    ("without_review", re.compile(r"without\s+(?:human\s+|manual\s+)?review", re.IGNORECASE)),
    ("system_override", re.compile(r"(?:system\s+override|system\s+prompt|disregard\s+all)", re.IGNORECASE)),
    ("do_not_flag", re.compile(r"(?:do\s+not\s+flag|skip\s+review|force\s+approve)", re.IGNORECASE)),
]


def scrub_pii(text: str) -> tuple[str, list[str]]:
    """Scrub Credit Card numbers and SSNs from text and return (sanitized_text, redacted_categories)."""
    redacted_categories: list[str] = []
    sanitized = text

    # Scrub 13-16 digit credit cards first so they aren't partially matched by the SSN regex.
    if _CREDIT_CARD_RE.search(sanitized):
        sanitized = _CREDIT_CARD_RE.sub("[REDACTED_CREDIT_CARD]", sanitized)
        redacted_categories.append("CREDIT_CARD")

    if _SSN_RE.search(sanitized):
        sanitized = _SSN_RE.sub("[REDACTED_SSN]", sanitized)
        redacted_categories.append("SSN")

    return sanitized, redacted_categories


def detect_prompt_injection(text: str) -> list[str]:
    """Detect prompt-injection patterns in text and return matched pattern names."""
    matched: list[str] = []
    for label, pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            matched.append(label)
    return matched


def _extract_expense_dict(raw_input: Any) -> dict[str, Any]:
    """Parse raw JSON string, Content object/dict, direct dict, or Pub/Sub / Eventarc envelope into an expense dict."""
    data = raw_input

    # Handle google.genai.types.Content or serialized Content dict {"parts": [...], "role": "user"}
    if isinstance(data, types.Content):
        texts = [p.text for p in (data.parts or []) if getattr(p, "text", None)]
        if texts:
            data = "".join(texts)
    elif isinstance(data, dict) and "parts" in data and isinstance(data["parts"], list):
        texts = [
            p.get("text")
            for p in data["parts"]
            if isinstance(p, dict) and p.get("text")
        ]
        if texts:
            data = "".join(texts)

    if isinstance(data, str):
        data = data.strip()
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid expense JSON payload: {data!r}") from exc

    if isinstance(data, BaseModel):
        data = data.model_dump()

    if not isinstance(data, dict):
        raise ValueError(f"Expected dict or JSON object for expense payload, got {type(data).__name__}")

    # Unwrap Pub/Sub message wrapper if present: {"message": {"data": "<base64>"}}
    if "message" in data and isinstance(data["message"], dict):
        msg_data = data["message"].get("data")
        if isinstance(msg_data, str):
            decoded = base64.b64decode(msg_data).decode("utf-8")
            data = json.loads(decoded)
        elif isinstance(msg_data, dict):
            data = msg_data

    # Unwrap ADK trigger router wrapper if present: {"data": {...}, "attributes": {...}}
    if "data" in data and isinstance(data["data"], (dict, str)):
        inner = data["data"]
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except json.JSONDecodeError:
                decoded = base64.b64decode(inner).decode("utf-8")
                inner = json.loads(decoded)
        if isinstance(inner, dict):
            data = inner

    report = ExpenseReport.model_validate(data)
    return report.model_dump()


# ---------------------------------------------------------------------------
# Workflow Nodes
# ---------------------------------------------------------------------------


@node
def parse_expense(ctx: Context, node_input: Any) -> Event:
    """Parse incoming expense payload and route based on the $100 threshold."""
    expense = _extract_expense_dict(node_input)
    amount = float(expense["amount"])
    route = "auto_approve" if amount < APPROVAL_THRESHOLD else "security_screen"
    ctx.state["expense"] = expense
    ctx.state["initial_route"] = route
    return Event(
        output=expense,
        route=route,
        state={"expense": expense, "initial_route": route},
    )


@node
def auto_approve(ctx: Context, node_input: dict[str, Any]):
    """Deterministically auto-approve expenses under $100 without invoking an LLM."""
    expense = node_input or ctx.state.get("expense", {})
    amount = float(expense.get("amount", 0.0))
    result = {
        "status": "approved",
        "decision": "approved",
        "mode": "auto_approve",
        "llm_invoked": False,
        "threshold": APPROVAL_THRESHOLD,
        "reason": (
            f"Expense of ${amount:.2f} is below the ${APPROVAL_THRESHOLD:.2f} "
            "threshold and was deterministically auto-approved in Python (no LLM called)."
        ),
        "expense": expense,
    }
    ctx.state["final_decision"] = result
    yield Event(
        message=json.dumps(result),
        state={"final_decision": result},
    )
    yield Event(output=result)


@node
def security_screen(ctx: Context, node_input: dict[str, Any]):
    """Pre-LLM security checkpoint: scrub PII and block prompt injections."""
    expense = dict(node_input or ctx.state.get("expense", {}))
    raw_description = str(expense.get("description", ""))

    # 1. Scrub PII (Credit Cards & SSNs) before anything can reach the LLM or logs.
    sanitized_description, redacted_pii = scrub_pii(raw_description)
    sanitized_expense = {**expense, "description": sanitized_description}

    # 2. Detect prompt-injection attempts on the description.
    injection_matches = detect_prompt_injection(raw_description)
    prompt_injection_detected = len(injection_matches) > 0

    security_report = {
        "redacted_pii": redacted_pii,
        "pii_scrubbed": len(redacted_pii) > 0,
        "prompt_injection_detected": prompt_injection_detected,
        "matched_injection_patterns": injection_matches,
        "llm_bypassed": prompt_injection_detected,
    }

    ctx.state["expense"] = sanitized_expense
    ctx.state["security_screen"] = security_report

    # Route directly to human_review if prompt injection is detected (bypassing LLM).
    route = "human_review" if prompt_injection_detected else "review_agent"

    screen_summary = {
        "node": "security_screen",
        "route": route,
        "security_screen": security_report,
        "sanitized_expense": sanitized_expense,
    }
    yield Event(
        message=json.dumps(screen_summary),
        state={"expense": sanitized_expense, "security_screen": security_report},
    )
    yield Event(
        output={
            "expense": sanitized_expense,
            "security_screen": security_report,
        },
        route=route,
    )


review_agent = LlmAgent(
    name="review_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    include_contents="none",
    instruction=(
        "You are an enterprise financial risk and compliance auditor reviewing a "
        "high-value employee expense claim ($100 or more). Note that any sensitive "
        "PII in the description has already been redacted by the upstream security_screen "
        "node (indicated by [REDACTED_SSN] or [REDACTED_CREDIT_CARD]).\n\n"
        "Analyze the sanitized expense payload provided in the input:\n"
        "1. Evaluate the dollar amount, category, submitter, date, and description for "
        "policy compliance or unusual risk factors.\n"
        "2. If [REDACTED_SSN] or [REDACTED_CREDIT_CARD] appears, note in the flags that "
        "PII was present in the submission and properly redacted prior to review.\n"
        "3. Produce a structured RiskReview alert for the human approver."
    ),
    output_schema=RiskReview,
    output_key="review_result",
)


@node
def human_review(ctx: Context, node_input: Any):
    """Pause workflow execution via RequestInput for human-in-the-loop approval or rejection."""
    sanitized_expense = ctx.state.get("expense", {})
    security_report = ctx.state.get("security_screen", {})
    prompt_injection_detected = bool(security_report.get("prompt_injection_detected", False))

    if prompt_injection_detected:
        event_type = "SECURITY_EVENT_PROMPT_INJECTION"
        llm_review = None
        prompt_message = (
            f"SECURITY ALERT: Prompt injection detected ({security_report.get('matched_injection_patterns')}). "
            f"LLM review was bypassed. Redacted PII: {security_report.get('redacted_pii')}. "
            f"Sanitized expense (${sanitized_expense.get('amount')} by {sanitized_expense.get('submitter')}): "
            f"{sanitized_expense.get('description')!r}. Please approve or reject."
        )
    else:
        event_type = "HIGH_VALUE_EXPENSE_REVIEW"
        llm_review = node_input if isinstance(node_input, dict) else ctx.state.get("review_result")
        prompt_message = (
            f"HIGH-VALUE EXPENSE REVIEW (${sanitized_expense.get('amount')} by {sanitized_expense.get('submitter')}): "
            f"Redacted PII: {security_report.get('redacted_pii')}. "
            f"LLM Risk Review: {json.dumps(llm_review)}. Please approve or reject."
        )

    review_packet = {
        "event_type": event_type,
        "expense": sanitized_expense,
        "security_screen": security_report,
        "llm_risk_review": llm_review,
    }
    ctx.state["review_packet"] = review_packet

    yield RequestInput(
        interrupt_id="human_approval",
        message=prompt_message,
        payload=review_packet,
    )


@node
def record_decision(ctx: Context, node_input: Any):
    """Record the human reviewer's approval or rejection decision after resumption."""
    raw_decision = node_input
    if isinstance(raw_decision, dict):
        decision_str = str(
            raw_decision.get("decision")
            or raw_decision.get("status")
            or raw_decision.get("result")
            or raw_decision
        )
    else:
        decision_str = str(raw_decision)

    normalized = decision_str.strip().lower()
    if any(tok in normalized for tok in ("reject", "denied", "deny", "false", "block")):
        final_status = "rejected"
    elif any(tok in normalized for tok in ("approve", "accept", "true", "ok", "yes")):
        final_status = "approved"
    else:
        final_status = normalized or "reviewed"

    sanitized_expense = ctx.state.get("expense", {})
    security_report = ctx.state.get("security_screen", {})
    llm_review = ctx.state.get("review_result")
    prompt_injection_detected = bool(security_report.get("prompt_injection_detected", False))

    result = {
        "status": final_status,
        "decision": final_status,
        "mode": "security_hitl_bypass_llm" if prompt_injection_detected else "llm_review_and_hitl",
        "llm_invoked": not prompt_injection_detected,
        "human_input": raw_decision,
        "expense": sanitized_expense,
        "security_screen": security_report,
        "llm_risk_review": None if prompt_injection_detected else llm_review,
    }
    ctx.state["final_decision"] = result
    yield Event(
        message=json.dumps(result),
        state={"final_decision": result},
    )
    yield Event(output=result)


# ---------------------------------------------------------------------------
# Assemble ADK 2.0 Graph Workflow & App
# ---------------------------------------------------------------------------

root_agent = Workflow(
    name="expense_processor",
    description=(
        "Ambient expense processing workflow that deterministically auto-approves "
        "claims under $100 and routes claims of $100 or more through PII scrubbing, "
        "prompt-injection defense, LLM risk analysis, and human-in-the-loop review."
    ),
    edges=[
        (START, parse_expense),
        (
            parse_expense,
            {
                "auto_approve": auto_approve,
                "security_screen": security_screen,
            },
        ),
        (
            security_screen,
            {
                "review_agent": review_agent,
                "human_review": human_review,
            },
        ),
        (review_agent, human_review),
        (human_review, record_decision),
    ],
)

app = App(
    name="expense_agent",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
