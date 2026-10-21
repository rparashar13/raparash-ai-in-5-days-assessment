"""Standalone Manager Approval Dashboard for the Ambient Expense Agent on Agent Runtime."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

import google.auth
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService
from google.auth.credentials import Credentials as GoogleBaseCredentials
from google.auth.transport.requests import AuthorizedSession
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("expense-manager-dashboard")

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "raparash-l200-sandbox-297625")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
RAW_RUNTIME_ID = os.environ.get("AGENT_RUNTIME_ID", "7543234981607243776")
AGENT_RUNTIME_ID = RAW_RUNTIME_ID.rstrip("/").split("/")[-1]
DEFAULT_USER_ID = "default-user"


class _GcloudFallbackCredentials(GoogleBaseCredentials):
    """Uses `gcloud auth print-access-token` on Cloudtop and falls back to ADC on Cloud Run."""

    def __init__(self) -> None:
        super().__init__()
        self.refresh(None)

    def refresh(self, request: Any) -> None:
        try:
            out = subprocess.check_output(
                ["gcloud", "auth", "print-access-token"], text=True, timeout=15
            ).strip()
            if out:
                self.token = out
                return
        except Exception:
            pass
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        req = google.auth.transport.requests.Request()
        creds.refresh(req)
        self.token = creds.token


def _get_gcp_credentials() -> Any:
    return _GcloudFallbackCredentials()


app = FastAPI(
    title="Expense Manager HITL Dashboard",
    description="Human-in-the-Loop approval dashboard wired to Google Cloud Agent Runtime",
)


class ActionRequest(BaseModel):
    approved: bool
    interrupt_id: str = "human_approval"
    user_id: str = DEFAULT_USER_ID


def _authed_gcp_session() -> AuthorizedSession:
    sess = AuthorizedSession(_get_gcp_credentials())
    sess.headers["x-goog-user-project"] = PROJECT_ID
    return sess


def _extract_pending_interrupt_from_dict(session: dict[str, Any]) -> dict[str, Any] | None:
    """Inspect a REST session dict's event history for an unresolved `adk_request_input` tool call."""
    events = session.get("events") or []
    unresolved_calls: dict[str, dict[str, Any]] = {}
    resolved_ids: set[str] = set()

    for ev in events:
        content = ev.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            fc = part.get("functionCall") or part.get("function_call")
            if fc and fc.get("name") == "adk_request_input":
                call_id = fc.get("id") or "human_approval"
                args = fc.get("args") or {}
                unresolved_calls[call_id] = args if isinstance(args, dict) else {}

            fr = part.get("functionResponse") or part.get("function_response")
            if fr and fr.get("name") == "adk_request_input":
                resp_id = fr.get("id") or "human_approval"
                resolved_ids.add(resp_id)

    sid = str(session.get("name", "")).rstrip("/").split("/")[-1]
    for call_id, args in unresolved_calls.items():
        if call_id not in resolved_ids:
            payload = args.get("payload") if isinstance(args.get("payload"), dict) else {}
            expense = payload.get("expense") if isinstance(payload.get("expense"), dict) else {}
            security_screen = (
                payload.get("security_screen")
                if isinstance(payload.get("security_screen"), dict)
                else {}
            )
            llm_risk_review = (
                payload.get("llm_risk_review")
                if isinstance(payload.get("llm_risk_review"), dict)
                else None
            )
            return {
                "session_id": sid,
                "user_id": session.get("userId", DEFAULT_USER_ID),
                "interrupt_id": call_id,
                "event_type": payload.get("event_type", "HIGH_VALUE_EXPENSE_REVIEW"),
                "message": args.get("message", "Pending manager review"),
                "expense": {
                    "amount": expense.get("amount", 0.0),
                    "submitter": expense.get("submitter", "unknown@company.com"),
                    "category": expense.get("category", "uncategorized"),
                    "description": expense.get("description", ""),
                    "date": expense.get("date", ""),
                },
                "security_screen": security_screen,
                "llm_risk_review": llm_risk_review,
            }
    return None


@app.get("/api/pending")
async def list_pending_approvals() -> JSONResponse:
    """List all sessions in VertexAiSessionService with unresolved `adk_request_input` events."""
    pending: list[dict[str, Any]] = []
    seen_sessions: set[str] = set()
    authed = _authed_gcp_session()
    base_url = (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/"
        f"projects/{PROJECT_ID}/locations/{LOCATION}/"
        f"reasoningEngines/{AGENT_RUNTIME_ID}/sessions"
    )

    try:
        resp = authed.get(base_url, timeout=30)
        resp.raise_for_status()
        sessions = (resp.json() or {}).get("sessions") or []
    except Exception as exc:
        logger.warning("REST list_sessions failed: %s", exc)
        sessions = []

    for sess_summary in sessions:
        name = sess_summary.get("name", "")
        sid = name.rstrip("/").split("/")[-1]
        if not sid or sid in seen_sessions:
            continue
        seen_sessions.add(sid)
        try:
            events_resp = authed.get(f"{base_url}/{sid}/events", timeout=30)
            events_resp.raise_for_status()
            events = (events_resp.json() or {}).get("sessionEvents") or []
            full_sess = dict(sess_summary)
            full_sess["events"] = events
            item = _extract_pending_interrupt_from_dict(full_sess)
            if item:
                pending.append(item)
        except Exception as exc:
            logger.warning("get_session_events(%s) failed: %s", sid, exc)

    return JSONResponse(content={"pending": pending, "count": len(pending)})



@app.post("/api/action/{session_id}")
async def resolve_pending_expense(session_id: str, req: ActionRequest) -> JSONResponse:
    """Resume a paused session on Agent Runtime with the manager's approval or rejection."""
    resume_message = {
        "role": "user",
        "parts": [
            {
                "function_response": {
                    "id": req.interrupt_id,
                    "name": "adk_request_input",
                    "response": {
                        "approved": req.approved,
                        "decision": "approved" if req.approved else "rejected",
                    },
                }
            }
        ],
    }

    url = (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/"
        f"projects/{PROJECT_ID}/locations/{LOCATION}/"
        f"reasoningEngines/{AGENT_RUNTIME_ID}:streamQuery"
    )
    body = {
        "class_method": "async_stream_query",
        "input": {
            "user_id": DEFAULT_USER_ID,
            "session_id": session_id,
            "message": resume_message,
        },
    }

    try:
        authed_session = _authed_gcp_session()
        resp = authed_session.post(url, json=body, timeout=90)
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Agent Runtime error: {resp.text}",
            )

        events = []
        final_text = ""
        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                events.append(ev)
                for part in (ev.get("content") or {}).get("parts") or []:
                    if part.get("text"):
                        final_text = part["text"]
            except Exception:
                pass

        return JSONResponse(
            content={
                "status": "resumed",
                "session_id": session_id,
                "approved": req.approved,
                "decision": "approved" if req.approved else "rejected",
                "agent_response": final_text,
                "events": events,
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to resume session %s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard() -> HTMLResponse:
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Ambient Expense Agent — Manager Approval Center</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Outfit:wght@600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #070b14;
      --card-bg: rgba(17, 25, 40, 0.78);
      --border: rgba(255, 255, 255, 0.10);
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #6366f1;
      --emerald: #10b981;
      --rose: #f43f5e;
      --amber: #f59e0b;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', sans-serif;
      background-color: var(--bg);
      background-image:
        radial-gradient(at 15% 15%, rgba(99, 102, 241, 0.18) 0px, transparent 50%),
        radial-gradient(at 85% 80%, rgba(16, 185, 129, 0.14) 0px, transparent 50%);
      color: var(--text);
      min-height: 100vh;
      padding: 2.5rem 1.5rem;
    }
    .container { max-width: 1120px; margin: 0 auto; }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 2.25rem;
      padding-bottom: 1.25rem;
      border-bottom: 1px solid var(--border);
    }
    h1 {
      font-family: 'Outfit', sans-serif;
      font-size: 1.85rem;
      letter-spacing: -0.02em;
      background: linear-gradient(135deg, #fff 0%, #a5b4fc 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .subtitle { color: var(--muted); font-size: 0.92rem; margin-top: 0.35rem; }
    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.45rem 0.9rem;
      border-radius: 999px;
      background: rgba(99, 102, 241, 0.14);
      border: 1px solid rgba(99, 102, 241, 0.35);
      font-size: 0.82rem;
      color: #c7d2fe;
    }
    .dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: var(--emerald);
      box-shadow: 0 0 10px var(--emerald);
      animation: pulse 2s infinite;
    }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
      gap: 1.5rem;
    }
    .card {
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
      transition: transform 0.2s ease, border-color 0.2s ease;
    }
    .card:hover { transform: translateY(-2px); border-color: rgba(99, 102, 241, 0.45); }
    .card.security-alert { border-color: rgba(244, 63, 94, 0.55); }
    .badge {
      display: inline-block;
      padding: 0.25rem 0.65rem;
      border-radius: 6px;
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .badge-danger { background: rgba(244, 63, 94, 0.2); color: #fda4af; border: 1px solid rgba(244, 63, 94, 0.4); }
    .badge-warn { background: rgba(245, 158, 11, 0.2); color: #fcd34d; border: 1px solid rgba(245, 158, 11, 0.4); }
    .amount { font-family: 'Outfit', sans-serif; font-size: 2rem; font-weight: 700; }
    .meta { font-size: 0.85rem; color: var(--muted); line-height: 1.5; }
    .desc-box {
      background: rgba(15, 23, 42, 0.65);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.85rem;
      font-size: 0.86rem;
      color: #e2e8f0;
    }
    .actions { display: flex; gap: 0.75rem; margin-top: auto; padding-top: 0.5rem; }
    button {
      flex: 1;
      padding: 0.7rem 1rem;
      border-radius: 10px;
      border: none;
      font-weight: 600;
      font-size: 0.88rem;
      cursor: pointer;
      transition: opacity 0.2s ease, transform 0.1s ease;
    }
    button:active { transform: scale(0.98); }
    button:disabled { opacity: 0.5; cursor: wait; }
    .btn-approve { background: var(--emerald); color: #052e16; }
    .btn-reject { background: rgba(244, 63, 94, 0.18); color: #fda4af; border: 1px solid rgba(244, 63, 94, 0.45); }
    .empty-state {
      text-align: center;
      padding: 4rem 2rem;
      background: var(--card-bg);
      border: 1px dashed var(--border);
      border-radius: 16px;
      color: var(--muted);
    }
    .modal-drawer {
      position: fixed;
      top: 0; right: -460px;
      width: 440px; max-width: 92vw; height: 100vh;
      background: rgba(15, 23, 42, 0.96);
      backdrop-filter: blur(20px);
      border-left: 1px solid var(--border);
      padding: 2rem 1.5rem;
      transition: right 0.3s cubic-bezier(0.16, 1, 0.3, 1);
      z-index: 100;
      overflow-y: auto;
    }
    .modal-drawer.open { right: 0; }
    pre {
      background: #090d16;
      padding: 1rem;
      border-radius: 10px;
      border: 1px solid var(--border);
      font-size: 0.78rem;
      overflow-x: auto;
      white-space: pre-wrap;
      color: #a5b4fc;
      margin-top: 1rem;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>Manager Approval Dashboard</h1>
        <p class="subtitle">Ambient Expense Agent &bull; Human-in-the-Loop Escalation Queue</p>
      </div>
      <div class="status-pill">
        <span class="dot"></span>
        <span id="poll-status">Live Polling (5s)</span>
      </div>
    </header>

    <div id="content">
      <div class="empty-state">All caught up! No expenses are currently pending manager approval.</div>
    </div>
  </div>

  <div id="drawer" class="modal-drawer">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
      <h2 style="font-family:'Outfit',sans-serif;font-size:1.3rem;">Compliance Resolution</h2>
      <button onclick="document.getElementById('drawer').classList.remove('open')" style="flex:0;padding:0.35rem 0.75rem;background:rgba(255,255,255,0.08);color:#fff;">Close</button>
    </div>
    <p id="drawer-summary" class="meta"></p>
    <pre id="drawer-json"></pre>
  </div>

  <script>
    async function fetchPending() {
      try {
        const res = await fetch('/api/pending');
        const data = await res.json();
        renderCards(data.pending || []);
      } catch (e) {
        console.error('Polling error:', e);
      }
    }

    function renderCards(items) {
      const container = document.getElementById('content');
      if (!items.length) {
        container.innerHTML = '<div class="empty-state">All caught up! No expenses are currently pending manager approval.</div>';
        return;
      }
      container.innerHTML = '<div class="grid">' + items.map(item => {
        const exp = item.expense || {};
        const sec = item.security_screen || {};
        const review = item.llm_risk_review || {};
        const isInjection = Boolean(sec.prompt_injection_detected);
        const badgeClass = isInjection ? 'badge-danger' : 'badge-warn';
        const badgeText = isInjection ? 'Security Alert: Prompt Injection' : (review.risk_level ? `Risk: ${review.risk_level}` : 'High-Value Review');
        const summary = isInjection
          ? `LLM Bypassed. Matched patterns: ${(sec.matched_injection_patterns || []).join(', ')}`
          : (review.alert_summary || item.message || 'Requires manager sign-off.');
        return `
          <div class="card ${isInjection ? 'security-alert' : ''}" id="card-${item.session_id}">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <span class="badge ${badgeClass}">${badgeText}</span>
              <span class="meta">${exp.date || ''}</span>
            </div>
            <div class="amount">$${Number(exp.amount || 0).toLocaleString('en-US', {minimumFractionDigits: 2})}</div>
            <div class="meta">
              <strong>Submitter:</strong> ${exp.submitter || 'N/A'}<br/>
              <strong>Category:</strong> ${exp.category || 'N/A'}<br/>
              <strong>Session:</strong> <code>${item.session_id}</code>
            </div>
            <div class="desc-box">
              <strong>Description:</strong> ${exp.description || ''}<br/><br/>
              <strong>Agent Triage:</strong> ${summary}
            </div>
            <div class="actions">
              <button class="btn-approve" onclick="submitDecision('${item.session_id}', '${item.interrupt_id}', true, this)">Approve</button>
              <button class="btn-reject" onclick="submitDecision('${item.session_id}', '${item.interrupt_id}', false, this)">Reject</button>
            </div>
          </div>
        `;
      }).join('') + '</div>';
    }

    async function submitDecision(sessionId, interruptId, approved, btn) {
      const card = document.getElementById('card-' + sessionId);
      const buttons = card ? card.querySelectorAll('button') : [btn];
      buttons.forEach(b => { b.disabled = true; b.textContent = 'Processing...'; });
      try {
        const res = await fetch('/api/action/' + sessionId, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({approved, interrupt_id: interruptId})
        });
        const result = await res.json();
        document.getElementById('drawer-summary').textContent =
          `Session ${sessionId} resolved as ${result.decision ? result.decision.toUpperCase() : (approved ? 'APPROVED' : 'REJECTED')}.`;
        let parsed = result.agent_response;
        try { parsed = JSON.stringify(JSON.parse(result.agent_response), null, 2); } catch (_) {}
        document.getElementById('drawer-json').textContent = parsed || JSON.stringify(result, null, 2);
        document.getElementById('drawer').classList.add('open');
        await fetchPending();
      } catch (err) {
        alert('Action failed: ' + err);
        buttons.forEach(b => { b.disabled = false; });
      }
    }

    fetchPending();
    setInterval(fetchPending, 5000);
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)
