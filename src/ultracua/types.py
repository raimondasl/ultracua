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
    #                              can tell it already typed) — NOT part of the fingerprint.
    #                              A password field reports a MASK, never its plaintext, and any runtime
    #                              secret passed as `capture(redact=...)` is replaced with [REDACTED] —
    #                              so "already typed" still reads true without shipping the value.
    bbox: Optional[list[float]] = None  # [x, y, w, h] in CSS px
    hint: Optional[str] = None  # WHAT AN UNNAMED CONTROL PROBABLY IS — never its accessible name.
    #                             Set only when `name` is empty. Measured (R4.131): 19-22% of the
    #                             interactables on an Odoo list page have no accessible name, and the
    #                             agent sees `button: ` with nothing after the colon — seven identical
    #                             anonymous buttons in one toolbar, one of which opens the search
    #                             panel. Sourced from `data-tooltip`, then a descendant's
    #                             `title`/`aria-label`, then an icon class, and RENDERED WITH ITS
    #                             SOURCE so the agent can weigh it.
    #
    #                             DELIBERATELY NOT PART OF `name`, and that is the whole design.
    #                             `_ACCNAME_JS` is shared by `SNAPSHOT_JS`, `DESCRIBE_JS` (the cached
    #                             locator, which replays through `get_by_role(name=...)`) and
    #                             `SCOPE_JS` (every recipe's scope fingerprint). Widening it would
    #                             invent names Playwright's accname never computes — breaking replay
    #                             binds — and would change the fingerprint of every cached flow in
    #                             every deployment. This field is observation-only: it is not in the
    #                             fingerprint basis (`role/name/tag + url`) and `Action` has no `name`
    #                             field at all, so it can never become a locator.


class Observation(BaseModel):
    """A compact, sanitized view of the page — the LLM-path input.

    SANITIZED is a real guarantee, not a label: `snapshot.capture` masks `input[type=password]` values
    and scrubs the caller-supplied `redact` terms (a spec's resolved `$secret_env` values) from BOTH
    element values and `text`, before this object exists. Redaction cannot manufacture drift — the
    fingerprint basis is role/name/tag + url only.
    """

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
