# Pantry Meal Planner — Starter App

This is the companion repository for the **Agent-to-Agent Engineering** codelab. It contains a FastAPI web app that tracks pantry ingredients with a simple dashboard, backed by Cloud Firestore.

## Quick Start

Create the `(default)` Firestore database first, if your project doesn't have one. Omitting `--database` is what makes it the default.

```bash
gcloud firestore databases create \
  --location=us-central1 \
  --type=firestore-native
```

```bash
uv sync

# Point at your GCP project and seed the pantry_items collection.
export GOOGLE_CLOUD_PROJECT="your-project-id"
uv run python scripts/seed_firestore.py

uv run uvicorn src.main:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080` to view the pantry dashboard.

The app reaches Firestore with Application Default Credentials. Cloud Shell provides them automatically; elsewhere, run `gcloud auth application-default login`.

`scripts/seed_firestore.py` is idempotent — it clears the collection and rewrites the sample items, so it doubles as a reset button.
