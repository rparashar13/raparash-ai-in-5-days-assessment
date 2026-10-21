"""Pantry data access backed by Cloud Firestore.

Documents live in the `pantry_items` collection of the project's `(default)`
database. The collection name is the contract shared with the agent service —
both processes read and write these documents, neither imports the other's code.
"""

import os
from datetime import date, timedelta

from google.cloud import firestore
from google.cloud.firestore import FieldFilter

COLLECTION = "pantry_items"

_client: firestore.Client | None = None


def get_client() -> firestore.Client:
    """Return a process-wide Firestore client.

    The client is created lazily so importing this module never requires
    credentials — useful in tests and at build time.
    """
    global _client
    if _client is None:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT is not set. Run `source .env` in this terminal."
            )
        _client = firestore.Client(project=project)
    return _client


def _to_item(doc) -> dict:
    """Flatten a Firestore document into the shape the templates expect."""
    data = doc.to_dict() or {}
    return {
        "id": doc.id,
        "name": data.get("name", ""),
        "quantity": data.get("quantity", 0),
        "unit": data.get("unit", ""),
        "category": data.get("category", ""),
        "expiry_date": data.get("expiry_date"),
    }


def get_all_items() -> list[dict]:
    """Return every pantry item, sorted by category then name.

    A pantry holds tens of documents, so the whole collection is streamed and
    sorted in Python. Sorting on two fields in Firestore needs a composite
    index; at this size it buys nothing.
    """
    docs = get_client().collection(COLLECTION).stream()
    items = [_to_item(doc) for doc in docs]
    items.sort(key=lambda item: (item["category"], item["name"]))
    return items


def get_item_by_id(item_id: str) -> dict | None:
    doc = get_client().collection(COLLECTION).document(item_id).get()
    return _to_item(doc) if doc.exists else None


def get_items_by_category(category: str) -> list[dict]:
    """Query one category. Equality filters on a single field are auto-indexed."""
    docs = (
        get_client()
        .collection(COLLECTION)
        .where(filter=FieldFilter("category", "==", category))
        .stream()
    )
    items = [_to_item(doc) for doc in docs]
    items.sort(key=lambda item: item["name"])
    return items


def add_item(
    name: str,
    quantity: float,
    unit: str,
    category: str,
    expiry_date: str | None = None,
) -> dict:
    doc_ref = get_client().collection(COLLECTION).document()
    doc_ref.set(
        {
            "name": name,
            "quantity": quantity,
            "unit": unit,
            "category": category,
            "expiry_date": expiry_date,
            "created_at": firestore.SERVER_TIMESTAMP,
        }
    )
    return _to_item(doc_ref.get())


def remove_item(item_id: str) -> bool:
    doc_ref = get_client().collection(COLLECTION).document(item_id)
    if not doc_ref.get().exists:
        return False
    doc_ref.delete()
    return True


def update_quantity(item_id: str, quantity: float) -> dict | None:
    doc_ref = get_client().collection(COLLECTION).document(item_id)
    if not doc_ref.get().exists:
        return None
    doc_ref.update({"quantity": quantity})
    return _to_item(doc_ref.get())


def get_categories_summary() -> list[dict]:
    summary: dict[str, dict] = {}
    for item in get_all_items():
        entry = summary.setdefault(
            item["category"],
            {"category": item["category"], "item_count": 0, "total_quantity": 0.0},
        )
        entry["item_count"] += 1
        entry["total_quantity"] += float(item["quantity"] or 0)
    return sorted(summary.values(), key=lambda entry: entry["category"])


def get_expiring_soon(days: int = 3) -> list[dict]:
    """Items expiring within `days` days, plus anything already expired.

    Filtered in Python rather than with a `where` clause: Firestore sorts null
    before every string, so `expiry_date <= cutoff` would sweep up every item
    that has no expiry date at all.
    """
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    expiring = [
        item
        for item in get_all_items()
        if item["expiry_date"] and item["expiry_date"] <= cutoff
    ]
    expiring.sort(key=lambda item: item["expiry_date"])
    return expiring
