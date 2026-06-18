"""Safety integration: a mutating step (form submit) carries an Idempotency-Key on
replay, and the mutation gate refuses to blind-replay it when the page has drifted."""

from __future__ import annotations

from pathlib import Path

from ultracua.cache import FlowCache
from ultracua.flow import run_cached
from ultracua.providers.scripted import ScriptedProvider

_FIX = Path(__file__).parents[1] / "benchmarks" / "fixtures" / "mutating.html"
URL = _FIX.resolve().as_uri()
GOAL = "place the order"
STEPS = [
    {"action": "click", "role": "button", "name": "place order", "intent": "place the order"},
    {"action": "done", "intent": "order submitted"},
]


def _recorder(captured: list, drift: bool = False):
    async def prepare(session) -> None:
        async def handler(route) -> None:
            captured.append(dict(route.request.headers))
            await route.fulfill(
                status=200, content_type="text/html",
                body="<html><body>Order placed</body></html>",
            )

        await session.page.route("**/order", handler)
        if drift:
            await session.page.evaluate(
                "() => { const b = document.createElement('button'); "
                "b.textContent = 'Extra Drift'; document.body.appendChild(b); }"
            )

    return prepare


async def test_idempotency_key_injected_on_mutating_replay(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path)
    learn_caps: list = []
    replay_caps: list = []

    learn = await run_cached(
        URL, GOAL, ScriptedProvider(list(STEPS)), cache, mode="learn",
        prepare=_recorder(learn_caps), headless=True,
    )
    assert learn.success

    replay = await run_cached(
        URL, GOAL, None, cache, mode="replay", prepare=_recorder(replay_caps), headless=True
    )
    assert replay.success
    # Learn did NOT inject a key; replay DID (mutation-gated, idempotent).
    assert learn_caps and "idempotency-key" not in learn_caps[0]
    assert replay_caps and replay_caps[0].get("idempotency-key", "").startswith("uca-")


async def test_mutation_gate_blocks_blind_replay_on_drift(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path)
    learn = await run_cached(
        URL, GOAL, ScriptedProvider(list(STEPS)), cache, mode="learn",
        prepare=_recorder([]), headless=True,
    )
    assert learn.success

    caps: list = []
    replay = await run_cached(
        URL, GOAL, None, cache, mode="replay", prepare=_recorder(caps, drift=True), headless=True
    )
    assert replay.success is False  # gate refused to blind-replay the mutation under drift
    assert caps == []               # ...and the order POST was never sent


async def test_mutating_step_under_drift_is_never_healed(tmp_path: Path) -> None:
    """Phase D safety: a mutating step under page drift FAILS LOUD and an LLM is never consulted to
    re-drive the write (no heal), even when a heal provider is available."""
    from ultracua.browser import BrowserSession
    from ultracua.cache import CachedStep
    from ultracua.flow import _replay_step
    from ultracua.safety import PacingGovernor
    from ultracua.timing import StepTrace
    from ultracua.types import Action

    heal_calls: list = []

    class _SpyProvider:
        async def decide(self, goal, obs, history):
            heal_calls.append(goal)  # if this fires, an LLM re-drove the write — the bug
            ref = obs.elements[0].ref if obs.elements else None
            return Action(action="click", intent="place order", ref=ref), None

    session = await BrowserSession(headless=True).start()
    try:
        await session.goto(URL)
        step = CachedStep(intent="place the order", action="click", mutating=True,
                          precond_fingerprint="DELIBERATELY-WRONG")  # force a drift mismatch
        ok, note, did_heal = await _replay_step(
            session, step, _SpyProvider(), StepTrace(index=0), GOAL, PacingGovernor(), "scope", 0
        )
        assert ok is False and did_heal is False  # failed loud, not healed
        assert heal_calls == []                    # the heal provider was NEVER consulted
        assert "drift" in note
    finally:
        await session.close()
