"""Completion verifiers (PLAN.md tiered verification / Phase 2-4).

A verifier answers: given the goal and the final page, is the goal achieved? It lets a
*solved* flow get cached even when the agent didn't cleanly emit `done` (the failure mode
the fast tier exhibits — it solves a task but doesn't recognize completion). Bias to
caution (accuracy over hit-rate): return True only on a clear signal, None when unsure.
"""

from __future__ import annotations

from typing import Optional

from .types import Observation

_SUCCESS_SIGNALS = (
    "added to cart", "order placed", "thank you", "thanks for your order",
    "successful", "confirmed", "confirmation", "completed", "submitted",
    "your order", "payment received",
)


async def keyword_completion(goal: str, obs: Observation) -> Optional[bool]:
    """Cheap, key-less heuristic: a clear success phrase in the page text -> done."""
    text = obs.text.lower()
    if any(s in text for s in _SUCCESS_SIGNALS):
        return True
    return None


def llm_completion(router, tier: str = "strong"):
    """A reliable (paid) verifier: ask the model whether the goal is complete. Returns an
    async (goal, obs) -> Optional[bool] usable as `run_cached(verifier=...)`."""
    from .llm.types import LLMRequest, Message, TextBlock

    async def _verify(goal: str, obs: Observation) -> Optional[bool]:
        elements = "\n".join(f"  [{e.ref}] {e.role}: {e.name}" for e in obs.elements[:40])
        prompt = (
            f"GOAL: {goal}\nURL: {obs.url}\nPAGE TEXT: {obs.text}\n"
            f"INTERACTABLE ELEMENTS:\n{elements}\n\n"
            "Is the GOAL already complete on this page? Reply with exactly one word: "
            "'done' or 'not_done'."
        )
        req = LLMRequest(
            system="You judge whether a web task is complete. Reply only 'done' or 'not_done'.",
            messages=[Message("user", [TextBlock(prompt)])],
            max_tokens=8,
        )
        resp = await router.complete(req, tier=tier)
        t = resp.text().strip().lower()
        if "not_done" in t or "not done" in t:
            return False
        if "done" in t:
            return True
        return None

    return _verify
