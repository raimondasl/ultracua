"""Shared definition of the deterministic 'demo shop' flow used by the benchmark and tests.

Four steps across three static pages: type a query -> submit -> open a result -> add to
cart. The scripted teacher (providers/scripted.py) walks it deterministically so we can
measure cache replay with no LLM and no network.
"""

from __future__ import annotations

from pathlib import Path

_FIXTURES = Path(__file__).parent / "fixtures"
INDEX = _FIXTURES / "index.html"

GOAL = "search for a widget and add it to the cart"
SUCCESS_TEXT = "Added to cart"

# Each step: action + (role, name-substring) to locate the target + optional text/intent.
STEPS: list[dict] = [
    {"action": "type", "role": "textbox", "name": "search", "text": "widget",
     "intent": "enter the search query"},
    {"action": "click", "role": "button", "name": "search",
     "intent": "submit the search"},
    {"action": "click", "role": "link", "name": "open widget x",
     "intent": "open the widget detail page"},
    {"action": "click", "role": "button", "name": "add to cart",
     "intent": "add the widget to the cart"},
    {"action": "done", "intent": "reached the added-to-cart state"},
]


def index_url() -> str:
    return INDEX.resolve().as_uri()
