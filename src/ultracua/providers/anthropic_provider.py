"""Anthropic Claude adapter (discovery / strong-tier).

Notes tied to PLAN.md and the Claude API reference:
- Forces the `act` tool via tool_choice so every turn yields a structured action.
- Streams the response so we can measure TTFT (the dominant per-step latency component).
- Caches the stable system+tools prefix via cache_control; the volatile observation goes
  in the user turn after it. (The prefix is small in Phase 0, so caching is a no-op until
  the system/tool surface grows past the model's minimum cacheable prefix — the breakpoint
  is here so the pattern is correct from the start.)
- `thinking` is omitted → off on Opus 4.8, the fast config for routine element selection.
"""

from __future__ import annotations

import time
from typing import Optional

from anthropic import AsyncAnthropic

from ..config import settings
from ..types import Action, Observation
from .base import ACTION_TOOL

SYSTEM = (
    "You are ultracua, a fast web-browsing agent. You receive the user's GOAL and a "
    "compact observation of the interactable elements currently visible on the page, "
    "each with a 'ref'. Choose the SINGLE best next action via the `act` tool.\n"
    "- Prefer the element whose role and name best match the goal.\n"
    "- Use 'type' for textboxes (include the text in 'text'); 'click' for buttons/links.\n"
    "- Use 'press' with text='Enter' to submit; 'scroll' to reveal more; 'navigate' with a URL.\n"
    "- Emit 'done' when the goal is achieved, 'give_up' if it is not achievable here.\n"
    "Keep 'intent' short and declarative — it is stored to replay this step later without an LLM."
)


def _render(obs: Observation, goal: str, history: list[str]) -> str:
    lines = [f"GOAL: {goal}", f"URL: {obs.url}", f"TITLE: {obs.title}", ""]
    if history:
        lines.append("RECENT STEPS:")
        lines.extend(f"  - {h}" for h in history[-5:])
        lines.append("")
    lines.append("INTERACTABLE ELEMENTS:")
    for e in obs.elements:
        t = f" type={e.type}" if e.type else ""
        lines.append(f"  [{e.ref}] {e.role}{t}: {e.name}"[:200])
    return "\n".join(lines)


class AnthropicProvider:
    def __init__(self, model: Optional[str] = None) -> None:
        self.model = model or settings.model
        # Reads ANTHROPIC_API_KEY from the environment.
        self.client = AsyncAnthropic()

    async def decide(
        self, goal: str, obs: Observation, history: list[str]
    ) -> tuple[Action, Optional[float]]:
        user = _render(obs, goal, history)
        t0 = time.perf_counter()
        ttft: Optional[float] = None
        async with self.client.messages.stream(
            model=self.model,
            max_tokens=512,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[ACTION_TOOL],
            tool_choice={"type": "tool", "name": "act"},
            messages=[{"role": "user", "content": user}],
        ) as stream:
            async for _event in stream:
                if ttft is None:
                    ttft = (time.perf_counter() - t0) * 1000.0
            msg = await stream.get_final_message()

        block = next((b for b in msg.content if b.type == "tool_use"), None)
        if block is None:
            return Action(action="give_up", intent="model returned no tool call"), ttft
        # block.input is already a parsed dict (Anthropic pre-parses tool args).
        return Action(**block.input), ttft
