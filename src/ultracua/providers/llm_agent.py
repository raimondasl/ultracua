"""Provider-neutral agent decision built on the multi-provider LLM abstraction.

Renders the observation into a canonical request (stable system+tools prefix for prompt
caching, volatile observation in the user turn), forces the `act` tool, and parses the
canonical `tool_use` block into an `Action`. Uses the FAST tier by default and escalates
to the STRONG tier when the fast model is unsure (give_up / no action) — model routing
with confidence-based escalation (PLAN.md §5).
"""

from __future__ import annotations

from typing import Optional

from ..llm.base import Router
from ..llm.types import LLMRequest, Message, TextBlock, ToolDef
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

_ACTION_TOOLDEF = ToolDef(
    name=ACTION_TOOL["name"],
    description=ACTION_TOOL["description"],
    input_schema=ACTION_TOOL["input_schema"],
    strict=bool(ACTION_TOOL.get("strict", False)),
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


def _parse(resp) -> Optional[Action]:
    tu = resp.tool_use("act")
    if tu is None:
        return None
    try:
        return Action(**tu.input)
    except Exception:
        return None


class LLMAgentProvider:
    def __init__(self, router: Router, tier: str = "fast") -> None:
        self.router = router
        self.tier = tier

    async def decide(
        self, goal: str, obs: Observation, history: list[str]
    ) -> tuple[Action, Optional[float]]:
        req = LLMRequest(
            system=SYSTEM,
            tools=[_ACTION_TOOLDEF],
            force_tool="act",
            messages=[Message(role="user", content=[TextBlock(text=_render(obs, goal, history))])],
            max_tokens=512,
        )
        resp = await self.router.complete(req, tier=self.tier)
        action = _parse(resp)

        # Escalate to the strong tier when the fast model is unsure.
        if (
            (action is None or action.action == "give_up")
            and self.router.has_strong
            and self.tier != "strong"
        ):
            strong_resp = await self.router.complete(req, tier="strong")
            escalated = _parse(strong_resp)
            if escalated is not None:
                action, resp = escalated, strong_resp

        if action is None:
            action = Action(action="give_up", intent="model returned no action")
        return action, resp.ttft_ms
