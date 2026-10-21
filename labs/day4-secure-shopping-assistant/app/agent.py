"""ADK 2.0 Shopping Assistant Agent with single-use discount code redemption."""

from __future__ import annotations

import os

from google.adk import Workflow
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.workflow import START
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

# Remediated: Initialize Gemini model without hardcoded credentials (uses env/ADC)
MODEL_NAME = os.environ.get("SHOPPING_AGENT_MODEL", "gemini-3.1-flash-lite")
model = Gemini(
    model=MODEL_NAME,
    retry_options=types.HttpRetryOptions(attempts=5, initial_delay=1.0),
)

# In-memory discount redemption store (simulating database state)
DISCOUNT_STORE: dict[str, bool] = {
    "WELCOME50": False,
    "SUMMER20": False,
}


class DiscountRequest(BaseModel):
    """Strict input validation schema for discount redemption requests."""

    code: str = Field(
        min_length=1,
        max_length=32,
        description="The discount code to redeem (e.g., WELCOME50, SUMMER20).",
    )
    user_id: str = Field(
        min_length=1,
        max_length=64,
        description="The registered user ID requesting discount redemption.",
    )


def redeem_discount(code: str, user_id: str) -> str:
    """Agent Tool: Redeem a single-use discount code for a registered user."""
    try:
        request = DiscountRequest(
            code=code.strip().upper() if isinstance(code, str) else code,
            user_id=user_id.strip() if isinstance(user_id, str) else user_id,
        )
    except ValidationError:
        return "Error: Invalid input parameters for discount redemption."

    if not request.user_id or request.user_id.lower().startswith("guest_"):
        return "Error: Registered user account required to redeem discounts."

    if request.code not in DISCOUNT_STORE:
        return "Error: Invalid discount code."

    if DISCOUNT_STORE[request.code]:
        return "Error: Discount code has already been redeemed."

    DISCOUNT_STORE[request.code] = True
    return (
        f"Success: Discount code {request.code} redeemed successfully "
        f"for user {request.user_id}."
    )


shopping_agent = LlmAgent(
    name="ShoppingHelper",
    model=model,
    instruction=(
        "You are a helpful retail shopping assistant. Use the `redeem_discount` "
        "tool to redeem single-use discount codes (such as WELCOME50 and SUMMER20) "
        "for registered users. Always report the exact tool outcome to the user."
    ),
    tools=[redeem_discount],
)

root_workflow = Workflow(
    name="shopping_assistant_workflow",
    edges=[(START, shopping_agent)],
)

root_agent = root_workflow

app = App(
    name="app",
    root_agent=root_workflow,
)
