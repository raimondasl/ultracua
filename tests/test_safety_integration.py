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
