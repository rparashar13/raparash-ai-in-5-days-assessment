# Implementation Plan: `pantry-meal-planner` (`pantry-agent`)

## 1. Architecture & Directory Structure
We will scaffold a standalone ADK project in `pantry-agent/` using `agents-cli scaffold create`:
```text
pantry-agent/
├── pyproject.toml          # Standalone dependencies: google-adk, google-cloud-firestore
├── app/
│   ├── __init__.py         # Exports App / root_agent
│   ├── agent.py            # Root LlmAgent ("pantry_meal_planner") with instruction & tools
│   └── tools.py            # Standalone Firestore CRUD tools for "pantry_items"
└── tests/                  # Unit and evaluation tests
```

## 2. Dependencies (`pantry-agent/pyproject.toml`)
- `google-adk`
- `google-cloud-firestore>=2.19.0`

## 3. Custom Firestore Tools (`pantry-agent/app/tools.py`)
Connects lazily to Cloud Firestore `(default)` database using `os.environ.get("GOOGLE_CLOUD_PROJECT")` and Application Default Credentials. Operates on `COLLECTION = "pantry_items"` with the exact document schema used by `src/data_service.py` (`name`, `quantity`, `unit`, `category`, `expiry_date`, `created_at`):
- `get_pantry_items(category: Optional[str] = None) -> dict`: Streams all items in `pantry_items` (or filters by `category`), sorts in Python by `(category, name)`, and returns the inventory list.
- `get_expiring_items(days: int = 3) -> dict`: Streams all items and filters in Python where `expiry_date` is non-null and `<= (date.today() + timedelta(days=days)).isoformat()`.
- `add_pantry_item(name: str, quantity: float, unit: str, category: str, expiry_date: Optional[str] = None) -> dict`: Inserts a new ingredient document into `pantry_items` (`unit` in `g`, `ml`, `pcs`; `category` in `Protein`, `Grains`, `Vegetables`, `Dairy`, `Pantry Staples`).
- `update_pantry_item_quantity(name_or_id: str, quantity: float) -> dict`: Matches an existing item by document ID or case-insensitive `name` in Python and updates its `quantity`.
- `remove_pantry_item(name_or_id: str) -> dict`: Matches an existing item by document ID or case-insensitive `name` in Python and deletes the document.

## 4. Root Agent Definition (`pantry-agent/app/agent.py`)
- **Agent Type**: Single `Agent` (`LlmAgent`) named `pantry_meal_planner`
- **Model**: `gemini-2.5-flash`
- **Tools**: `[get_pantry_items, get_expiring_items, add_pantry_item, update_pantry_item_quantity, remove_pantry_item, google_search]`
- **Instruction**:
  - Always check the user's current pantry inventory (`get_pantry_items` or `get_expiring_items`) before suggesting meals or recipes.
  - Prioritize ingredients that are expiring soon (`soon`) and warn the user about any `expired` items (suggesting removal).
  - Use `add_pantry_item`, `update_pantry_item_quantity`, and `remove_pantry_item` whenever the user reports buying, using, or discarding ingredients.
  - Use `google_search` to ground recipe ideas, cooking instructions, and dietary/nutritional requests (e.g., high-protein meals).
  - Politely decline questions unrelated to food, kitchen management, nutrition, or meal planning.
