"""Key-less heuristic provider.

Lets the full Phase 0 vertical slice (warm session -> snapshot -> decide -> act ->
verify -> trace) run end-to-end without any API key, which is useful for smoke tests and
for measuring the non-LLM parts of the latency budget in isolation.
"""

from __future__ import annotations

from typing import Optional

from ..types import Action, Observation


class MockProvider:
    async def decide(
        self, goal: str, obs: Observation, history: list[str]
    ) -> tuple[Action, Optional[float]]:
        words = [w for w in goal.lower().split() if len(w) > 2]
        best: Optional[object] = None
        best_score = 0
        for el in obs.elements:
            name = el.name.lower()
            score = sum(1 for w in words if w in name)
            if score > best_score:
                best, best_score = el, score
        if best is not None and best_score > 0:
            kind = "type" if best.role == "textbox" else "click"  # type: ignore[attr-defined]
            return (
                Action(
                    action=kind,
                    intent=f"interact with '{best.name}'",  # type: ignore[attr-defined]
                    ref=best.ref,  # type: ignore[attr-defined]
                    text="ultracua" if kind == "type" else None,
                    reasoning="mock heuristic keyword match",
                ),
                None,
            )
        return Action(action="done", intent="no obvious element matched the goal"), None
