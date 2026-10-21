"""Serve the reasoning_engine ``{class_method, input}`` contract over HTTP."""

import inspect
import json

from agentplatform.agent_engines.templates.adk import AdkApp
from fastapi import FastAPI, HTTPException, Request, encoders, responses
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from expense_agent.app_utils import services


def _no_op_instrumentor_builder(project_id: str) -> None:
    """No-op so set_up() keeps the startup instrumentor and generate_content spans."""
    return None


def attach_reasoning_engine_routes(app: FastAPI) -> None:
    """Register reasoning_engine routes that dispatch to an AdkApp."""
    runtime: AdkApp | None = None
    streaming_methods: set[str] = set()
    sync_methods: set[str] = set()

    def get_runtime() -> AdkApp:
        nonlocal runtime, streaming_methods, sync_methods
        if runtime is None:
            from expense_agent.agent import app as adk_app

            runtime = AdkApp(
                app=adk_app,
                session_service_builder=services.get_session_service,
                artifact_service_builder=services.get_artifact_service,
                instrumentor_builder=_no_op_instrumentor_builder,
            )
            runtime.set_up()
            operations = runtime.register_operations()
            streaming_methods = set(operations.get("stream", [])) | set(
                operations.get("async_stream", [])
            )
            sync_methods = set(operations.get("", [])) | set(
                operations.get("async", [])
            )
        return runtime

    def resolve_method(class_method: str, *, streaming: bool):
        rt = get_runtime()
        allowed = streaming_methods if streaming else sync_methods
        if class_method not in allowed:
            raise HTTPException(
                status_code=404,
                detail=f"Unsupported reasoning_engine method: {class_method!r}",
            )
        return getattr(rt, class_method)

    @app.post("/api/stream_reasoning_engine")
    async def stream_query(request: Request) -> responses.StreamingResponse:
        body = await request.json()
        class_method = body.get("class_method") or "async_stream_query"
        method = resolve_method(class_method, streaming=True)
        kwargs = dict(body.get("input") or {})
        if class_method in ("stream_query", "async_stream_query"):
            kwargs.setdefault("user_id", "default-user")
        stream = (
            await method(**kwargs)
            if inspect.iscoroutinefunction(method)
            else method(**kwargs)
        )

        async def generator():
            if hasattr(stream, "__aiter__"):
                async for event in stream:
                    yield json.dumps(encoders.jsonable_encoder(event)) + "\n"
            else:
                async for event in iterate_in_threadpool(stream):
                    yield json.dumps(encoders.jsonable_encoder(event)) + "\n"

        return responses.StreamingResponse(
            content=generator(), media_type="application/json"
        )

    @app.post("/api/reasoning_engine")
    async def query(request: Request) -> responses.JSONResponse:
        body = await request.json()
        class_method = body.get("class_method") or "query"
        kwargs = dict(body.get("input") or {})

        # Support direct :query invocations (such as Pub/Sub push subscriptions)
        if class_method in ("query", "async_query", "stream_query", "async_stream_query"):
            rt = get_runtime()
            kwargs.setdefault("user_id", "default-user")
            events = [event async for event in rt.async_stream_query(**kwargs)]
            return responses.JSONResponse(
                content=encoders.jsonable_encoder({"output": events})
            )

        method = resolve_method(class_method, streaming=False)
        if inspect.iscoroutinefunction(method):
            output = await method(**kwargs)
        else:
            output = await run_in_threadpool(method, **kwargs)
        return responses.JSONResponse(
            content=encoders.jsonable_encoder({"output": output})
        )
