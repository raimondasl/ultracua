"""A deterministic, key-less "teacher" provider.

Returns a fixed sequence of actions, resolving each to a live element by (role, name
substring). Used to *learn* a flow deterministically in tests and the key-less benchmark,
so the thing under test is the cache + replay machinery — not an LLM. (A real speedup
number still requires an LLM-backed learn run; see benchmarks/bench.py.)
"""

from __future__ import annotations

from typing import Optional

from ..types import Action, Observation


class ScriptedProvider:
    def __init__(self, steps: list[dict]) -> None:
        # each step: {action, role?, name?, text?, intent?}
        self.steps = steps
        self.i = 0

    async def decide(
        self, goal: str, obs: Observation, history: list[str]
    ) -> tuple[Action, Optional[float]]:
        if self.i >= len(self.steps):
            return Action(action="done", intent="script complete"), None
        s = self.steps[self.i]
        self.i += 1
        act = s["action"]
        intent = s.get("intent", "")
        if act in ("done", "give_up"):
            return Action(action=act, intent=intent), None

        want_role = s.get("role")
        want_name = (s.get("name") or "").lower()
        ref: Optional[str] = None
        for el in obs.elements:
            if want_role and el.role != want_role:
                continue
            if want_name and want_name not in el.name.lower():
                continue
            ref = el.ref
            break
        return (
            Action(
                action=act,
                intent=intent,
                ref=ref,
                text=s.get("text"),
                reasoning="scripted",
            ),
            None,
        )
