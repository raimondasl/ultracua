"""Provider-neutral agent decision built on the multi-provider LLM abstraction.

Renders the observation into a canonical request (stable system+tools prefix for prompt
caching, volatile observation in the user turn), forces the `act` tool, and parses the
canonical `tool_use` block into an `Action`. Uses the FAST tier by default and escalates
to the STRONG tier when the fast model is unsure (give_up / no action) — model routing
with confidence-based escalation (PLAN.md §5).
"""

from __future__ import annotations

import json
from typing import Optional

from ..config import settings
from ..llm.base import Router
from ..llm.types import LLMRequest, Message, TextBlock, ToolDef
from ..types import Action, Observation
from .base import ACTION_TOOL

SYSTEM = (
    "You are ultracua, a fast web-browsing agent. You receive the user's GOAL and a "
    "compact observation of the interactable elements currently visible on the page, "
    "each with a 'ref'. Choose the SINGLE best next action via the `act` tool.\n"
    "- Prefer the element whose role and name best match the goal.\n"
    "- For 'click' and 'type' you MUST set 'ref' to the target element's ref from the list.\n"
    "- Use 'type' for textboxes (include the text in 'text'); 'click' for buttons/links.\n"
    "- Use 'press' with text='Enter' to submit; 'scroll' to reveal more; 'navigate' with a URL.\n"
    "- If the goal's target isn't on the current page, NAVIGATE toward it. When you know or can "
    "infer a direct URL for the target section, PREFER 'navigate' to that URL over clicking "
    "through nested or hover/flyout menus — direct URLs reproduce reliably on replay. Do NOT "
    "stop just because the answer isn't visible yet; explore first.\n"
    "- Emit 'done' ONLY when the goal is genuinely achieved (the requested data or result is "
    "present). 'give_up' is a LAST resort — only when the task is truly impossible, never merely "
    "because the answer isn't on the current page.\n"
    "- If WEBMCP TOOLS are listed, PREFER one: action='webmcp_call', tool=<name>, "
    "args=<JSON object of arguments> — it performs the task directly, no DOM clicks needed.\n"
    "- If the target is not among the elements (e.g. a canvas / visual-only area), use "
    "action='need_vision' and a vision step will locate it by pixel.\n"
    "Use PAGE TEXT and element values to judge progress: do not repeat an action that already "
    "took effect (e.g. a textbox already shows your text, or PAGE TEXT confirms success) — move "
    "on, or emit 'done' if the goal is met.\n"
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
    if obs.text:
        lines.append(f"PAGE TEXT: {obs.text}")
        lines.append("")
    if obs.webmcp_tools:
        lines.append("WEBMCP TOOLS (prefer these — they act directly):")
        for t in obs.webmcp_tools:
            lines.append(f"  - {t.get('name', '')}: {t.get('description', '')}")
        lines.append("")
    lines.append("INTERACTABLE ELEMENTS:")
    for e in obs.elements:
        t = f" type={e.type}" if e.type else ""
        v = f' value="{e.value}"' if e.value else ""
        lines.append(f"  [{e.ref}] {e.role}{t}: {e.name}{v}"[:220])
    return "\n".join(lines)


def _parse(resp) -> Optional[Action]:
    tu = resp.tool_use("act")
    if tu is None:
        return None
    data = dict(tu.input)
    # tool/args only apply to webmcp_call. On other actions the model occasionally leaks
    # raw tool-call markup into `tool`; drop it so it can't pollute the cached step.
    if data.get("action") != "webmcp_call":
        data.pop("tool", None)
        data.pop("args", None)
    # WebMCP args come back as a JSON string (strict schema) -> parse to a dict.
    if isinstance(data.get("args"), str):
        raw = data["args"].strip()
        try:
            data["args"] = json.loads(raw) if raw else None
        except Exception:
            data["args"] = None
    try:
        return Action(**data)
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
            temperature=settings.authoring_temperature,  # >0 so best-of-N draws diverse samples
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
