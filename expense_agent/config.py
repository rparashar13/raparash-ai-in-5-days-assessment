"""Configuration constants for the Ambient Expense Agent."""

import os

MODEL = os.environ.get("EXPENSE_AGENT_MODEL", "gemini-3.1-flash-lite")
APPROVAL_THRESHOLD = float(os.environ.get("EXPENSE_APPROVAL_THRESHOLD", "100.0"))
THRESHOLD = APPROVAL_THRESHOLD
