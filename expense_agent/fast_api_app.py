"""FastAPI server for the Ambient Expense Agent (Agent Runtime + Pub/Sub + Eventarc)."""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from expense_agent.app_utils import services
from expense_agent.app_utils.a2a import attach_a2a_routes
from expense_agent.app_utils.reasoning_engine_adapter import (
    attach_reasoning_engine_routes,
)

# Load environment variables from project root and package directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)


class ShortenPubSubSubscriptionMiddleware:
    """ASGI middleware that shortens Pub/Sub subscription paths in trigger payloads."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").endswith("/trigger/pubsub"):
            await self.app(scope, receive, send)
            return

        body_chunks: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
                more_body = message.get("more_body", False)
            else:
                async def passthrough_receive() -> Message:
                    return message

                await self.app(scope, passthrough_receive, send)
                return

        raw_body = b"".join(body_chunks)
        modified_body = raw_body
        if raw_body:
            try:
                payload = json.loads(raw_body.decode("utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("subscription"), str):
                    sub = payload["subscription"].strip().strip("/")
                    if "/" in sub:
                        payload["subscription"] = sub.rsplit("/", 1)[-1]
                        modified_body = json.dumps(payload).encode("utf-8")
            except Exception:
                modified_body = raw_body

        if modified_body != raw_body:
            new_headers = []
            for k, v in scope.get("headers", []):
                if k.lower() == b"content-length":
                    new_headers.append((k, str(len(modified_body)).encode("ascii")))
                else:
                    new_headers.append((k, v))
            scope = dict(scope, headers=new_headers)

        sent = False

        async def replay_receive() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": modified_body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from expense_agent.agent import app as adk_app
    from expense_agent.agent import root_agent

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name
    await attach_a2a_routes(
        app,
        agent=root_agent,
        runner=runner,
        task_store=InMemoryTaskStore(),
        rpc_path=f"/a2a/{adk_app.name}",
    )
    yield


allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else ["*"]
)
otel_to_cloud = bool(
    os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID") or os.environ.get("K_SERVICE")
)

app: FastAPI = get_fast_api_app(
    agents_dir=str(PROJECT_ROOT),
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    trigger_sources=["pubsub", "eventarc"],
    otel_to_cloud=otel_to_cloud,
    lifespan=lifespan,
)
app.title = "ambient-expense-agent"
app.description = "ADK 2.0 Ambient Expense Agent on Google Cloud Agent Runtime"
attach_reasoning_engine_routes(app)
app.add_middleware(ShortenPubSubSubscriptionMiddleware)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
