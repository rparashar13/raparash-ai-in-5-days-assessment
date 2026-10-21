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

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.google_search_tool import GoogleSearchTool
from google.genai import types

from .tools import (
    add_pantry_item,
    get_expiring_items,
    get_pantry_items,
    remove_pantry_item,
    update_pantry_item_quantity,
)


MODEL = "gemini-3.8-flash"

google_search = GoogleSearchTool(bypass_multi_tools_limit=True)

INSTRUCTION = """You are a smart kitchen and meal-planning assistant for the Pantry Planner app.

Your responsibilities:
1. Check pantry inventory before making meal or recipe recommendations by calling `get_pantry_items` or `get_expiring_items`.
2. Help reduce food waste by prioritizing ingredients that are expiring soon (`expiry_status == 'soon'`) and warning the user about any already-expired items (`expiry_status == 'expired'`).
3. Manage the pantry through conversation whenever the user buys, uses, or discards ingredients:
   - Call `add_pantry_item` to add new ingredients (use category values: 'Protein', 'Grains', 'Vegetables', 'Dairy', or 'Pantry Staples', and unit values: 'g', 'ml', or 'pcs').
   - Call `update_pantry_item_quantity` when the user adjusts an existing ingredient's amount.
   - Call `remove_pantry_item` when the user removes or discards an ingredient.
4. Use `google_search` to ground recipe suggestions, cooking instructions, and dietary/nutritional ideas (such as high-protein meals) tailored to the available pantry ingredients.
5. Stay strictly within the scope of pantry management, cooking, recipes, nutrition, and meal planning. Politely decline unrelated questions.
"""

root_agent = Agent(
    # Keep in sync with agents-cli-manifest.yaml: agents-cli derives this name
    # from the project `name:` recorded there, and telemetry reports it as
    # gen_ai.agent.name. Renaming the agent only here makes the two disagree,
    # and anything selecting traces by name stops finding this agent's.
    name="pantry_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=INSTRUCTION,
    tools=[
        get_pantry_items,
        get_expiring_items,
        add_pantry_item,
        update_pantry_item_quantity,
        remove_pantry_item,
        google_search,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
