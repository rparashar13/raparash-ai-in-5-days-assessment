# Web App Integration Plan (`src/` ↔ `pantry-agent`)

## 1. Overview & Architecture

We are connecting the FastAPI dashboard in `src/` to the standalone `pantry-agent` ADK service over HTTP/HTTPS using Server-Sent Events (`/run_sse`).

- **Base URL**: Configured via `AGENT_BASE_URL` in `.env` (or environment variable):
  - **Local Development**: `http://localhost:8000` (pointed at `gcloud run services proxy` or the local ADK server on port `8000`).
  - **Production / Cloud Run**: `https://pantry-agent-195807333364.us-central1.run.app` (authenticated with a Google-signed OIDC ID token for audience `$AGENT_BASE_URL`).
- **Session Ownership**:
  - Conversation state lives exclusively inside the ADK session on `pantry-agent` (`app_name="app"`, fixed `user_id="pantry-user"`).
  - The browser stores **only** the `session_id` string in `localStorage` (`pantry_session_id`) — no transcript or message history is stored in `localStorage` or in the FastAPI web app.
  - On page load (or when opening the chat panel), if a `session_id` exists in `localStorage`, the browser calls `GET /api/chat/sessions/{session_id}` on the FastAPI app, which fetches `GET {AGENT_BASE_URL}/apps/app/users/pantry-user/sessions/{session_id}` from the ADK agent and returns the prior user/assistant messages to re-render the chat bubbles. If the session is not found (`404`), the browser clears `localStorage` and creates a fresh session on the next message.

---

## 2. ADK HTTP API Contract (from `google-agents-cli-deploy`)

1. **Create Session**:
   - `POST {AGENT_BASE_URL}/apps/app/users/pantry-user/sessions`
   - Body: `{}`
   - Returns: JSON object containing `"id"` (the `session_id`).
2. **Fetch Session History**:
   - `GET {AGENT_BASE_URL}/apps/app/users/pantry-user/sessions/{session_id}`
   - Returns: Session JSON with `"id"` and `"events"` list (`event.author`, `event.content.role`, `event.content.parts[].text`).
3. **Stream Agent Turn (`/run_sse`)**:
   - `POST {AGENT_BASE_URL}/run_sse`
   - Headers: `Content-Type: application/json`, `Accept: text/event-stream` (+ `Authorization: Bearer <ID_TOKEN>` when `AGENT_BASE_URL` starts with `https://`)
   - Body:
     ```json
     {
       "app_name": "app",
       "user_id": "pantry-user",
       "session_id": "<session_id>",
       "new_message": {
         "role": "user",
         "parts": [{"text": "<user message>"}]
       },
       "streaming": true
     }
     ```
   - Response (`text/event-stream`):
     - Each `data: {...}` line is a JSON-encoded ADK `Event`.
     - Text chunks/replies live in `event.content.parts[].text` where `event.content.role == "model"` (or `event.author != "user"`).

---

## 3. Authentication Switch (`http://` vs `https://`)

In `src/main.py`:
- Read `AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "http://localhost:8000").rstrip("/")`.
- If `AGENT_BASE_URL.startswith("https://")`:
  - Mint a Google-signed ID token using `google.oauth2.id_token.fetch_id_token(Request(), AGENT_BASE_URL)` (with fallback to `gcloud auth print-identity-token`) and attach `Authorization: Bearer <token>`.
- If `AGENT_BASE_URL.startswith("http://")` (e.g., `http://localhost:8000`):
  - Send requests without an `Authorization` header (the local proxy or local ADK server handles auth).

---

## 4. Planned File Changes

1. **`pyproject.toml`**:
   - Add `httpx>=0.27.0` and `python-dotenv>=1.0.0` so `src/main.py` can load `.env` and stream SSE responses asynchronously with `httpx.AsyncClient`.
2. **`src/main.py`**:
   - Add `POST /api/chat/sessions`: Creates a new ADK session on `{AGENT_BASE_URL}/apps/app/users/pantry-user/sessions` and returns `{"session_id": ...}`.
   - Add `GET /api/chat/sessions/{session_id}`: Fetches the ADK session from `{AGENT_BASE_URL}/apps/app/users/pantry-user/sessions/{session_id}`, extracts the conversation turns (user & model text messages), and returns `{"session_id": ..., "messages": [...]}`.
   - Add `POST /api/chat/stream`: Accepts `{"session_id": ..., "message": ...}` (creating a session automatically if `session_id` is omitted or 404s), posts to `{AGENT_BASE_URL}/run_sse`, and yields SSE `data: {"text": ..., "partial": ..., "session_id": ...}\n\n` events to the browser as tokens arrive.
3. **`src/templates/dashboard.html`**:
   - Update `.chat-status` from `"Preview — not implemented yet"` to `"Connected to ADK Pantry Agent"` and add a **"New Chat"** button in `.chat-header` to reset the session when desired.
   - Replace the placeholder message and `askAssistant()` / `sendChat()` stub with:
     - `localStorage.getItem('pantry_session_id')` session lookup and `loadSessionHistory()` on startup.
     - Streaming `fetch('/api/chat/stream', ...)` reader using `ReadableStream` + `TextDecoder` that updates the assistant reply bubble in real time as chunks arrive, using the `.chat-message.assistant` style (removing `.stub`).
     - Refreshing dashboard pantry data if the assistant mutated the pantry during the turn.
