# Agent Guidance — Ambient Expense Agent

- **Root Agent**: `root_agent` (`Workflow(name="expense_processor")`) and `app` (`App(name="expense_agent")`) in `expense_agent/agent.py`.
- **Configuration**: `MODEL = "gemini-3.1-flash-lite"` and `APPROVAL_THRESHOLD = 100.0` in `expense_agent/config.py`.
- **Ambient Server**: `expense_agent/fast_api_app.py` mounts `/apps/expense_agent/trigger/pubsub` and `/apps/expense_agent/trigger/eventarc` with `ShortenPubSubSubscriptionMiddleware` and `otel_to_cloud=False`.
- **Evaluation**: `tests/eval/generate_traces.py` and `tests/eval/eval_config.yaml`.
