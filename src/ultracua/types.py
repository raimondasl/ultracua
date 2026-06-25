"""Core data types shared across the agent loop, snapshot pipeline, and providers.

These are deliberately provider-neutral (PLAN.md constraint b): an `Action` is a
canonical browser action, not an Anthropic/OpenAI tool-call shape.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

ActionType = Literal[
    "click", "type", "press", "scroll", "navigate", "done", "give_up",
    "select",        # choose an <option> in a <select> by value (recorder; replay via select_option)
    "click_xy",      # vision tier: click pixel coordinates (canvas / opaque widgets)
    "webmcp_call",   # WebMCP tier: invoke a site-exposed structured tool
    "need_vision",   # agent can't find the target in the DOM -> fall to the vision tier
]


class Element(BaseModel):
    """One interactable element from a scoped snapshot."""

    ref: str  # stable-within-snapshot handle, e.g. "e12"
    role: str  # aria role or inferred role (button/link/textbox/...)
    name: str  # accessible name (label/text/placeholder)
    tag: str
    type: Optional[str] = None  # input type, if any
    value: Optional[str] = None  # current value of an input/textarea/select (so the agent
    #                              can tell it already typed) — NOT part of the fingerprint
    bbox: Optional[list[float]] = None  # [x, y, w, h] in CSS px


class Observation(BaseModel):
    """A compact, sanitized view of the page — the LLM-path input."""

    url: str
    title: str
    elements: list[Element]
    text: str = ""  # short snippet of visible page text (so the agent can read content /
    #                 confirmations / errors and judge completion), not just interactables
    webmcp_tools: Optional[list[dict]] = None  # site-exposed WebMCP tools, if any
    fingerprint: str = ""  # structural hash for verification + future cache keys


class Action(BaseModel):
    """A single canonical browser action chosen by a provider."""

    action: ActionType
    intent: str  # why — stored so this step can later be replayed/healed without an LLM
    ref: Optional[str] = None  # target element ref (click/type)
    text: Optional[str] = None  # text to type, key to press, or URL to navigate to
    reasoning: Optional[str] = None
    coords: Optional[list[int]] = None  # [x, y] pixel coords (click_xy / vision tier)
    tool: Optional[str] = None  # WebMCP tool name (webmcp_call)
    args: Optional[dict] = None  # WebMCP tool arguments (webmcp_call)


class StepResult(BaseModel):
    action: Action
    ok: bool
    state_changed: bool
    note: str = ""
