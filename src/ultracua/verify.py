"""Post-action verification (PLAN.md component 5, Phase 0 slice).

For now this is the cheapest tier: a rule-based state-diff (did the URL or structural
fingerprint change?). Phase 2 layers the ranked locator fallback chain, intent-keyed
re-grounding, and an LLM judge for ambiguous cases on top of this.
"""

from __future__ import annotations

from .types import Observation


def state_changed(before: Observation, after: Observation) -> bool:
    """True if the page meaningfully changed after an action."""
    return before.url != after.url or before.fingerprint != after.fingerprint
