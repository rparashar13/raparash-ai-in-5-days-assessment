"""Custom Cloud Firestore tools for the Pantry Meal Planner agent.

Connects to the `(default)` Firestore database in `GOOGLE_CLOUD_PROJECT` using
Application Default Credentials and operates on the `pantry_items` collection.
"""

import os
from datetime import date, datetime, timedelta
from typing import Any

from google.cloud import firestore
from google.cloud.firestore import FieldFilter

COLLECTION = "pantry_items"

_client: firestore.Client | None = None


def get_client() -> firestore.Client:
    """Return a process-wide Firestore client."""
    global _client
    if _client is None:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT is not set. Run `source .env` in this terminal."
            )
        _client = firestore.Client(project=project)
    return _client


def _expiry_status(expiry_date: str | None) -> str:
    """Compute expiry status ('expired', 'soon', 'ok', or 'none')."""
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


def _to_item(doc: Any) -> dict[str, Any]:
    """Flatten a Firestore document into the standard pantry item dictionary."""
    data = doc.to_dict() or {}
    expiry_date = data.get("expiry_date")
    return {
        "id": doc.id,
        "name": data.get("name", ""),
        "quantity": data.get("quantity", 0),
        "unit": data.get("unit", ""),
        "category": data.get("category", ""),
        "expiry_date": expiry_date,
        "expiry_status": _expiry_status(expiry_date),
    }


def get_pantry_items(category: str = "") -> dict[str, Any]:
    """Retrieve all items in the pantry, optionally filtered by category.

    Args:
        category: Optional category filter (e.g. 'Protein', 'Grains',
            'Vegetables', 'Dairy', 'Pantry Staples'). Pass an empty string
            to list all pantry items.

    Returns:
        A dictionary with 'status', 'count', and 'items' list.
    """
    col = get_client().collection(COLLECTION)
    if category and category.strip():
        docs = col.where(
            filter=FieldFilter("category", "==", category.strip())
        ).stream()
    else:
        docs = col.stream()
    items = [_to_item(doc) for doc in docs]
    items.sort(key=lambda item: (item["category"], item["name"]))
    return {"status": "success", "count": len(items), "items": items}


def get_expiring_items(days: int = 3) -> dict[str, Any]:
    """Retrieve pantry items expiring within the given number of days or already expired.

    Args:
        days: Number of days from today to check for upcoming expiration.

    Returns:
        A dictionary with 'status', 'count', and 'expiring_items' list sorted by expiry_date.
    """
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    all_items = get_pantry_items().get("items", [])
    expiring = [
        item
        for item in all_items
        if item.get("expiry_date") and item["expiry_date"] <= cutoff
    ]
    expiring.sort(key=lambda item: item["expiry_date"])
    return {"status": "success", "count": len(expiring), "expiring_items": expiring}


def add_pantry_item(
    name: str,
    quantity: float,
    unit: str,
    category: str,
    expiry_date: str = "",
) -> dict[str, Any]:
    """Add a new ingredient item to the pantry in Firestore.

    Args:
        name: Name of the ingredient (e.g., 'Tofu', 'Beef Slice').
        quantity: Numeric amount of the ingredient (e.g., 250, 500).
        unit: Measurement unit ('g', 'ml', or 'pcs').
        category: Item category ('Protein', 'Grains', 'Vegetables', 'Dairy', or 'Pantry Staples').
        expiry_date: Optional expiration date in 'YYYY-MM-DD' format, or empty string if none.

    Returns:
        A dictionary with 'status' and the created 'item'.
    """
    normalized_expiry = (
        expiry_date.strip() if expiry_date and expiry_date.strip() else None
    )
    doc_ref = get_client().collection(COLLECTION).document()
    doc_ref.set(
        {
            "name": name.strip(),
            "quantity": float(quantity),
            "unit": unit.strip(),
            "category": category.strip(),
            "expiry_date": normalized_expiry,
            "created_at": firestore.SERVER_TIMESTAMP,
        }
    )
    return {"status": "success", "item": _to_item(doc_ref.get())}


def update_pantry_item_quantity(name_or_id: str, quantity: float) -> dict[str, Any]:
    """Update the quantity of an existing pantry item by document ID or ingredient name.

    Args:
        name_or_id: The Firestore document ID or ingredient name (case-insensitive).
        quantity: New numeric quantity to set for the item.

    Returns:
        A dictionary with 'status' and the updated 'item' (or an error message if not found).
    """
    col = get_client().collection(COLLECTION)
    target = name_or_id.strip()

    direct_ref = col.document(target)
    direct_doc = direct_ref.get()
    if direct_doc.exists:
        direct_ref.update({"quantity": float(quantity)})
        return {"status": "success", "item": _to_item(direct_ref.get())}

    target_lower = target.lower()
    for doc in col.stream():
        data = doc.to_dict() or {}
        item_name = str(data.get("name", "")).strip().lower()
        if item_name == target_lower or target_lower in item_name:
            doc.reference.update({"quantity": float(quantity)})
            return {"status": "success", "item": _to_item(doc.reference.get())}

    return {
        "status": "error",
        "message": f"Pantry item '{name_or_id}' not found.",
    }


def remove_pantry_item(name_or_id: str) -> dict[str, Any]:
    """Remove an ingredient from the pantry by document ID or ingredient name.

    Args:
        name_or_id: The Firestore document ID or ingredient name (case-insensitive).

    Returns:
        A dictionary with 'status' and the removed item details (or an error message if not found).
    """
    col = get_client().collection(COLLECTION)
    target = name_or_id.strip()

    direct_ref = col.document(target)
    direct_doc = direct_ref.get()
    if direct_doc.exists:
        removed = _to_item(direct_doc)
        direct_ref.delete()
        return {"status": "success", "removed_item": removed}

    target_lower = target.lower()
    for doc in col.stream():
        data = doc.to_dict() or {}
        item_name = str(data.get("name", "")).strip().lower()
        if item_name == target_lower or target_lower in item_name:
            removed = _to_item(doc)
            doc.reference.delete()
            return {"status": "success", "removed_item": removed}

    return {
        "status": "error",
        "message": f"Pantry item '{name_or_id}' not found.",
    }
