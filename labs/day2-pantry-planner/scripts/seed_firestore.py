"""Seed the pantry_items collection with sample data.

Run this once after creating the Firestore database:

    uv run python scripts/seed_firestore.py

Re-running it wipes the collection and writes the sample set again, so it also
works as a reset button if your testing leaves the pantry in a strange state.
"""

import os
import sys
from datetime import date, timedelta

from google.cloud import firestore

COLLECTION = "pantry_items"

# Expiry dates are relative to the seed date so the "expired" and "expiring
# soon" dashboard badges always have something to show.
SAMPLE_ITEMS = [
    {"name": "Chicken Breast", "quantity": 500, "unit": "g", "category": "Protein", "expires_in_days": 9},
    {"name": "Eggs", "quantity": 12, "unit": "pcs", "category": "Protein", "expires_in_days": 14},
    {"name": "Rice", "quantity": 1000, "unit": "g", "category": "Grains", "expires_in_days": None},
    {"name": "Pasta", "quantity": 500, "unit": "g", "category": "Grains", "expires_in_days": None},
    {"name": "Tomatoes", "quantity": 4, "unit": "pcs", "category": "Vegetables", "expires_in_days": 3},
    {"name": "Onions", "quantity": 3, "unit": "pcs", "category": "Vegetables", "expires_in_days": 19},
    {"name": "Spinach", "quantity": 200, "unit": "g", "category": "Vegetables", "expires_in_days": -1},
    {"name": "Milk", "quantity": 1000, "unit": "ml", "category": "Dairy", "expires_in_days": 5},
    {"name": "Olive Oil", "quantity": 500, "unit": "ml", "category": "Pantry Staples", "expires_in_days": None},
    {"name": "Salt", "quantity": 500, "unit": "g", "category": "Pantry Staples", "expires_in_days": None},
]


def main() -> int:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        print("GOOGLE_CLOUD_PROJECT is not set. Run `source .env` first.", file=sys.stderr)
        return 1

    db = firestore.Client(project=project)
    collection = db.collection(COLLECTION)

    existing = list(collection.stream())
    if existing:
        batch = db.batch()
        for doc in existing:
            batch.delete(doc.reference)
        batch.commit()
        print(f"Removed {len(existing)} existing document(s).")

    batch = db.batch()
    for item in SAMPLE_ITEMS:
        offset = item["expires_in_days"]
        expiry = (date.today() + timedelta(days=offset)).isoformat() if offset is not None else None
        batch.set(
            collection.document(),
            {
                "name": item["name"],
                "quantity": item["quantity"],
                "unit": item["unit"],
                "category": item["category"],
                "expiry_date": expiry,
                "created_at": firestore.SERVER_TIMESTAMP,
            },
        )
    batch.commit()

    print(f"Seeded {len(SAMPLE_ITEMS)} items into '{COLLECTION}' in project {project}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
