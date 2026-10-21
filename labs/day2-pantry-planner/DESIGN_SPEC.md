# DESIGN_SPEC.md — Pantry Meal Planner Agent (`pantry-agent`)

## Overview
`pantry-agent` (`pantry_meal_planner`) is a standalone ADK kitchen assistant agent that helps users inspect their pantry inventory in Cloud Firestore (`pantry_items` collection), manage ingredients through natural conversation (add, remove, update quantity, check expiring items), and discover recipe recommendations and meal plans grounded via Google Search (`google_search`).

It is architected as an independent service in `./pantry-agent/` with its own `pyproject.toml` and virtual environment, sharing only the `(default)` Cloud Firestore `pantry_items` collection with the FastAPI web dashboard in `./src/`. Once deployed as a private Cloud Run service (`pantry-agent`), the FastAPI dashboard connects to it over HTTPS via the ADK `/run_sse` streaming endpoint.

## Example Use Cases
1. **High-Protein Meal Suggestion (Local Validation Prompt)**
   - **Input**: `"What menu that I cook now for a high protein diet?"`
   - **Expected Behavior**: Calls `get_pantry_items()` to read current Firestore inventory (e.g., Chicken Breast 500g, Eggs 12 pcs), uses `google_search` to find matching high-protein recipes, and proposes concrete meal options using in-stock ingredients.
2. **Expiring Ingredient Triage**
   - **Input**: `"I want to deplete the ingredients that almost expired, what's your suggestion?"`
   - **Expected Behavior**: Calls `get_expiring_items(days=3)` to identify expiring/expired items (e.g., Spinach, Tomatoes) and suggests recipes that prioritize valid soon-to-expire ingredients while warning about already expired items.
3. **Conversational Pantry Updates**
   - **Input**: `"I just bought 250g tofu and 250g tempeh, please add these and refine your suggestion"`
   - **Expected Behavior**: Calls `add_pantry_item()` for tofu and tempeh (`category="Protein"`, `unit="g"`, `quantity=250`), persists them to Firestore `pantry_items`, and updates meal recommendations.

## Tools Required
All custom Firestore tools live in `pantry-agent/app/tools.py` and authenticate via Application Default Credentials (`google-cloud-firestore` server SDK against `(default)` database in `GOOGLE_CLOUD_PROJECT`):
1. `get_pantry_items(category: str | None = None) -> list[dict]` — Streams documents from `pantry_items` (optionally filtered by category) and sorts in Python.
2. `get_expiring_items(days: int = 3) -> list[dict]` — Streams `pantry_items` and filters in Python for items with a non-null `expiry_date` within `days` days (avoiding Firestore null-ordering pitfalls).
3. `add_pantry_item(name: str, quantity: float, unit: str, category: str, expiry_date: str | None = None) -> dict` — Adds a new document to `pantry_items` with `created_at = firestore.SERVER_TIMESTAMP`.
4. `update_pantry_item_quantity(name_or_id: str, quantity: float) -> dict` — Looks up an item by document ID or case-insensitive name match in Python and updates its `quantity`.
5. `remove_pantry_item(name_or_id: str) -> dict` — Looks up an item by document ID or case-insensitive name match in Python and deletes it from `pantry_items`.
6. `google_search` (ADK built-in tool from `google.adk.tools.google_search`) — Grounds recipe recommendations and nutritional queries using Google Search.

## Constraints & Safety Rules
- **Standalone Boundary**: `pantry-agent/` must NOT import anything from `src/` (no `from src import data_service`).
- **Firestore Query Safety**: Stream the small `pantry_items` collection and perform multi-field sorting, substring/case-insensitive name lookups, and `expiry_date` comparisons in Python to avoid composite index requirements and Firestore's `null`-first sorting behavior.
- **Topic Scope**: Only answer questions related to pantry inventory, ingredients, cooking, nutrition, and meal planning; politely decline unrelated topics in the agent instruction.
- **Private Deployment**: Deploy to Cloud Run (`us-central1`) with `--no-allow-unauthenticated` and authenticate requests via Google-signed ID tokens (or `gcloud run services proxy` locally).

## Success Criteria
1. `agents-cli lint` passes with zero errors.
2. Local smoke test (`agents-cli run "What menu that I cook now for a high protein diet?"`) and `agents-cli eval run` pass.
3. Cloud Run deployment (`pantry-agent` in `us-central1`) responds to authenticated `agents-cli run --url $AGENT_BASE_URL --mode adk "Do I still have tofu? What kind of dish I can make with it?"`.
4. FastAPI dashboard (`src/main.py` + `src/templates/dashboard.html`) streams token-by-token responses from `/run_sse` and persists only the ADK `session_id` in browser `localStorage`.

## Reference Samples
- Standard single-agent ADK (`--agent adk`) with `google_search` and custom `FunctionTool` functions deployed to Cloud Run (`--deployment-target cloud_run`).
