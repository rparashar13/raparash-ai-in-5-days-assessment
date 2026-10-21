# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from typing import Any, Literal

from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.events.event import Event
from google.adk.models import Gemini
from google.adk.workflow import Edge, Workflow, node
from google.genai import types
from pydantic import BaseModel, Field


MODEL = "gemini-3.1-flash-lite"


class InquiryCategory(BaseModel):
    category: Literal["shipping", "unrelated"] = Field(
        description=(
            "Determine if the user query is related to shipping (rates, tracking,"
            " delivery times, returns) or unrelated."
        )
    )


def save_query(node_input: Any):
    """Saves user query in state for downstream nodes."""
    return Event(output=node_input, state={"user_query": node_input})


categorize_agent = LlmAgent(
    name="categorize",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=5, initial_delay=1.0),
    ),
    instruction="You are an expert classifier. Categorize the user query.",
    output_key="inquiry_category",
    output_schema=InquiryCategory,
)


@node
def route_inquiry(ctx: Context, node_input: Any):
    """Routes the workflow based on the classified category."""
    category_data = ctx.state.get("inquiry_category", {})
    if isinstance(category_data, BaseModel):
        category = getattr(category_data, "category", "unrelated")
    elif isinstance(category_data, dict):
        category = category_data.get("category", "unrelated")
    else:
        category = "unrelated"
    query = ctx.state.get("user_query", "")
    return Event(output=query, route=category)


faq_agent = LlmAgent(
    name="shipping_faq",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=5, initial_delay=1.0),
    ),
    instruction="""You are an enthusiastic and playful customer support representative for a shipping company! 🚚✨ Answer user questions based ONLY on the shipping FAQ below. Do not answer questions outside of the FAQ. Use friendly emojis and always highlight our awesome FREE standard shipping threshold on orders over $50! 🎉📦

    SHIPPING FAQ:
    - Rates: Standard shipping is $5.99. Express shipping is $12.99. 🌟 Orders
      over $50 qualify for FREE standard shipping! 🎉
    - Tracking: You can track your order by entering your tracking number on
      our website's tracking page. 🔍
    - Delivery Times: Standard delivery takes 3-5 business days. Express
      delivery takes 1-2 business days. ⚡
    - Returns: We offer free returns within 30 days of delivery. Please make
      sure the item is in its original condition. 🔄
    """,
)


@node
def handle_unrelated(ctx: Context, node_input: Any):
    """Handles unrelated inquiries politely."""
    msg = (
        "I am sorry, I am a shipping customer support assistant and can only"
        " answer questions related to our shipping FAQ."
    )
    return Event(
        author="handle_unrelated",
        content=types.Content(role="model", parts=[types.Part.from_text(text=msg)]),
        output=msg,
    )


root_agent = Workflow(
    # Keep in sync with agents-cli-manifest.yaml: agents-cli derives this name
    # from the project `name:` recorded there, and telemetry reports it as
    # gen_ai.agent.name. Renaming the agent only here makes the two disagree,
    # and anything selecting traces by name stops finding this agent's.
    name="customer_support_agent",
    edges=[
        (
            "START",
            save_query,
            categorize_agent,
            route_inquiry,
            {
                "shipping": faq_agent,
                "unrelated": handle_unrelated,
            },
        ),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
