import json
import os
import subprocess
from collections.abc import AsyncGenerator
from datetime import date, datetime
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token
from pydantic import BaseModel

from src import data_service
from src.models import AddItemRequest, UpdateQuantityRequest

load_dotenv()

app = FastAPI(title="Pantry Planner")
app.mount("/static", StaticFiles(directory="src/static"), name="static")
templates = Jinja2Templates(directory="src/templates")

ADK_APP_NAME = "app"
ADK_USER_ID = "pantry-user"


class ChatStreamRequest(BaseModel):
    message: str
    session_id: str | None = None


def _get_agent_base_url() -> str:
    return os.environ.get("AGENT_BASE_URL", "http://localhost:8000").rstrip("/")


def _get_agent_auth_headers(base_url: str) -> dict[str, str]:
    """Return Authorization header with Google-signed OIDC ID token when calling HTTPS Cloud Run."""
    headers = {"Content-Type": "application/json"}
    if not base_url.startswith("https://"):
        return headers

    try:
        token = id_token.fetch_id_token(GoogleAuthRequest(), base_url)
        if token:
            headers["Authorization"] = f"Bearer {token}"
            return headers
    except Exception:
        pass

    try:
        res = subprocess.run(
            ["gcloud", "auth", "print-identity-token"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        token = res.stdout.strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception:
        pass
    return headers


async def _create_adk_session(client: httpx.AsyncClient, base_url: str, headers: dict[str, str]) -> str:
    url = f"{base_url}/apps/{ADK_APP_NAME}/users/{ADK_USER_ID}/sessions"
    resp = await client.post(url, headers=headers, json={}, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    session_id = data.get("id")
    if not session_id:
        raise RuntimeError("ADK agent returned a session without an 'id'.")
    return session_id


def _extract_messages_from_session(session_data: dict[str, Any]) -> list[dict[str, str]]:
    """Extract ordered user and assistant text messages from an ADK Session object."""
    messages: list[dict[str, str]] = []
    for event in session_data.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        if event.get("partial"):
            continue
        content = event.get("content")
        if not isinstance(content, dict):
            continue
        role = content.get("role") or ("user" if event.get("author") == "user" else "model")
        parts = content.get("parts") or []
        texts = [
            p.get("text", "")
            for p in parts
            if isinstance(p, dict) and isinstance(p.get("text"), str) and p.get("text").strip()
        ]
        if not texts:
            continue
        combined = "\n".join(texts).strip()
        messages.append(
            {
                "role": "user" if role == "user" else "assistant",
                "text": combined,
            }
        )
    return messages


def _expiry_status(expiry_date: str | None) -> str:
    if not expiry_date:
        return "none"
    try:
        exp = datetime.strptime(expiry_date, "%Y-%m-%d").date()
    except ValueError:
        return "none"
    delta = (exp - date.today()).days
    if delta < 0:
        return "expired"
    if delta <= 3:
        return "soon"
    return "ok"


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    items = data_service.get_all_items()
    categories = data_service.get_categories_summary()
    expiring = data_service.get_expiring_soon(days=3)

    for item in items:
        item["expiry_status"] = _expiry_status(item.get("expiry_date"))

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "items": items,
            "categories": categories,
            "expiring_soon": expiring,
            "total_items": len(items),
            "total_categories": len(categories),
        },
    )


@app.get("/api/pantry")
async def list_items():
    return data_service.get_all_items()


@app.post("/api/pantry", status_code=201)
async def add_item(req: AddItemRequest):
    return data_service.add_item(
        name=req.name,
        quantity=req.quantity,
        unit=req.unit,
        category=req.category,
        expiry_date=req.expiry_date,
    )


@app.delete("/api/pantry/{item_id}")
async def remove_item(item_id: str):
    if not data_service.remove_item(item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    return {"deleted": True}


@app.put("/api/pantry/{item_id}")
async def update_quantity(item_id: str, req: UpdateQuantityRequest):
    item = data_service.update_quantity(item_id, req.quantity)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.get("/api/pantry/categories")
async def categories_summary():
    return data_service.get_categories_summary()


@app.post("/api/chat/sessions")
async def create_chat_session():
    base_url = _get_agent_base_url()
    headers = _get_agent_auth_headers(base_url)
    try:
        async with httpx.AsyncClient() as client:
            session_id = await _create_adk_session(client, base_url, headers)
            return {"session_id": session_id}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach pantry-agent: {exc}") from exc


@app.get("/api/chat/sessions/{session_id}")
async def get_chat_session(session_id: str):
    base_url = _get_agent_base_url()
    headers = _get_agent_auth_headers(base_url)
    url = f"{base_url}/apps/{ADK_APP_NAME}/users/{ADK_USER_ID}/sessions/{session_id}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=30.0)
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail="Session not found")
            resp.raise_for_status()
            session_data = resp.json()
            return {
                "session_id": session_data.get("id", session_id),
                "messages": _extract_messages_from_session(session_data),
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch session from pantry-agent: {exc}") from exc


@app.post("/api/chat/stream")
async def stream_chat(req: ChatStreamRequest):
    base_url = _get_agent_base_url()
    headers = _get_agent_auth_headers(base_url)

    async def event_generator() -> AsyncGenerator[str, None]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            session_id = req.session_id
            try:
                if session_id:
                    check_url = f"{base_url}/apps/{ADK_APP_NAME}/users/{ADK_USER_ID}/sessions/{session_id}"
                    check_resp = await client.get(check_url, headers=headers, timeout=15.0)
                    if check_resp.status_code == 404:
                        session_id = None
                if not session_id:
                    session_id = await _create_adk_session(client, base_url, headers)
            except Exception as exc:
                err_payload = json.dumps({"error": f"Failed to initialize ADK session: {exc}"})
                yield f"data: {err_payload}\n\n"
                return

            yield f"data: {json.dumps({'session_id': session_id, 'type': 'session'})}\n\n"

            run_url = f"{base_url}/run_sse"
            payload = {
                "app_name": ADK_APP_NAME,
                "user_id": ADK_USER_ID,
                "session_id": session_id,
                "new_message": {
                    "role": "user",
                    "parts": [{"text": req.message}],
                },
                "streaming": True,
            }
            stream_headers = {**headers, "Accept": "text/event-stream"}
            mutated_pantry = False

            try:
                async with client.stream("POST", run_url, headers=stream_headers, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:") :].strip()
                        if not data_str:
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        error_msg = event.get("errorMessage") or event.get("error_message")
                        if error_msg:
                            yield f"data: {json.dumps({'error': error_msg, 'session_id': session_id})}\n\n"
                            continue

                        content = event.get("content")
                        parts = content.get("parts", []) if isinstance(content, dict) else []
                        is_partial = bool(event.get("partial", False))

                        for part in parts:
                            if not isinstance(part, dict):
                                continue
                            fn_call = part.get("functionCall") or part.get("function_call")
                            if isinstance(fn_call, dict):
                                fn_name = fn_call.get("name", "")
                                if fn_name in (
                                    "add_pantry_item",
                                    "update_pantry_item_quantity",
                                    "remove_pantry_item",
                                ):
                                    mutated_pantry = True
                                yield f"data: {json.dumps({'type': 'tool_call', 'tool': fn_name, 'session_id': session_id})}\n\n"

                            text_chunk = part.get("text")
                            if isinstance(text_chunk, str) and text_chunk:
                                yield f"data: {json.dumps({'type': 'text', 'text': text_chunk, 'partial': is_partial, 'session_id': session_id})}\n\n"

                yield f"data: {json.dumps({'type': 'done', 'session_id': session_id, 'mutated_pantry': mutated_pantry})}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'error': f'Agent stream error: {exc}', 'session_id': session_id})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
