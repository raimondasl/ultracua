"""The drift-sandbox benchmark doubles as a key-less CI gate on locator resilience: across a distribution
of realistic DOM drifts, the resilient locator must survive the cosmetic ones at 0-LLM, let the neighbor
anchor disambiguate an ambiguous twin, fail loud on a removed target, and — the invariant — NEVER silently
bind the wrong element. (The precise 100% gate is the committed baselines/drift.json; this is the floor.)
"""

from __future__ import annotations

from benchmarks.drift_sandbox import measure


async def test_drift_sandbox_resilience_and_no_wrong_binds() -> None:
    rec = await measure("scripted")
    # Hard safety invariants (all deterministic, key-less):
    assert rec["wrong_binds"] == 0              # never a silent wrong-element bind — the whole point
    assert rec["semantic_failed_loud"] is True  # a removed target fails loud, not silently
    assert rec["ambiguous_disambiguated"] is True  # the neighbor anchor picks the RIGHT twin (not /wrong)
    # A "conflict" drift (the two guess-locators disagree on identity) must NEVER silently bind one — it
    # fails loud (or heals), never lands on the /wrong decoy. This is the adversarial-review invariant.
    assert rec["conflict_no_wrongbind"] is True
    # Resilience floor (baselines/drift.json holds the precise 12/12; this lenient CI floor catches a
    # multi-drift regression without flaking on a single platform difference):
    assert rec["resilience_0llm"] >= 0.8
