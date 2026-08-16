"""Vision fallback tier (PLAN.md Phase 4).

When the DOM/AX snapshot can't resolve a target — canvas/WebGL/opaque widgets, i.e. an
empty interactable set — take a screenshot and ask a grounding model where to click. The
result is a `click_xy` Action (pixel coords), replayed deterministically by clicking the
coords with no LLM. This is the rare, last-resort tier (brittle to layout shifts), below
WebMCP → cached selector → DOM/AX in the actuation stack.
"""

from __future__ import annotations

import base64
import time
from typing import Optional, Protocol

from .config import settings
from .obs import UsageTotals
from .types import Action


class GroundingProvider(Protocol):
    async def decide(
        self, goal: str, screenshot: bytes, viewport: dict
    ) -> tuple[Action, Optional[float]]: ...


class MockGrounding:
    """Returns scripted vision actions (click_xy / done) — for tests."""

    def __init__(self, actions: list[dict]) -> None:
        self.actions = list(actions)
        self.calls = 0

    async def decide(self, goal, screenshot, viewport):
        self.calls += 1
        a = self.actions.pop(0) if self.actions else {"action": "done", "intent": "vision done"}
        return Action(**a), None


_GROUND_TOOL = {
    "name": "ground",
    "description": "Choose where to click on this visual UI, or report done.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["click_xy", "done", "give_up"]},
            "intent": {"type": "string"},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        "required": ["action", "intent"],
    },
}


class AnthropicGrounding:
    """Claude vision grounding: screenshot + goal -> click_xy coords. Uses the Anthropic SDK
    directly (image input); needs ANTHROPIC_API_KEY. (Untested without a key.)"""

    def __init__(self, model: Optional[str] = None) -> None:
        self.model = model or settings.model
        self._client = None
        # B1/G3: this class drives the SDK directly rather than through a `Router`, so its spend was
        # invisible to every cost report — a vision-tier run read as free. Exposing `totals` (the
        # same attribute a Router carries) is enough for `UsageTotals.observe` to pick it up, and
        # it deliberately does NOT change how the client is constructed: that is R4.41's separate
        # question, and answering it here would smuggle a behaviour change into an accounting fix.
        self.totals = UsageTotals()
        self.accounting_failed = False

    def _sdk(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic()
        return self._client

    async def decide(self, goal, screenshot, viewport):
        b64 = base64.standard_b64encode(screenshot).decode("ascii")
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": (
                f"GOAL: {goal}\nThis page has no DOM-addressable elements (a canvas / visual "
                f"UI). The image is {viewport.get('width')}x{viewport.get('height')} px. Call "
                "`ground`: 'click_xy' with the x,y pixel coordinates of the target, or 'done' "
                "if the goal is already achieved."
            )},
        ]
        t0 = time.perf_counter()
        ttft: Optional[float] = None
        async with self._sdk().messages.stream(
            model=self.model,
            max_tokens=200,
            tools=[_GROUND_TOOL],
            tool_choice={"type": "tool", "name": "ground"},
            messages=[{"role": "user", "content": content}],
        ) as stream:
            async for _event in stream:
                if ttft is None:
                    ttft = (time.perf_counter() - t0) * 1000.0
            msg = await stream.get_final_message()
        # Reuse the adapter's own mapping rather than re-reading `msg.usage` here: it already
        # handles Anthropic's `cache_read_input_tokens` / `cache_creation_input_tokens` naming,
        # and a second transcription of one mapping is how two copies drift apart.
        try:
            from .llm.anthropic import from_native

            _r = from_native(msg)
            self.totals.add(_r.usage, model=_r.model or self.model)
        except Exception:  # noqa: BLE001 - accounting must never break the grounding call itself
            # ...but it must not pretend the spend was zero either. This flag makes the run report
            # `unobserved_llm_path` instead of a total that silently omits every grounding call.
            self.accounting_failed = True
        block = next((b for b in msg.content if b.type == "tool_use"), None)
        if block is None:
            return Action(action="give_up", intent="vision: no tool call"), ttft
        inp = dict(block.input)
        act = inp.get("action", "give_up")
        if act == "click_xy":
            return (
                Action(action="click_xy", intent=inp.get("intent", "vision click"),
                       coords=[int(inp.get("x", 0)), int(inp.get("y", 0))]),
                ttft,
            )
        return Action(action=act, intent=inp.get("intent", "")), ttft
