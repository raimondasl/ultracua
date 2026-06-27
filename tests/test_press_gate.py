"""Refless mutating submit (press Enter): the mutation gate anchors on the FOCUSED field's identity —
its captured locator — so replay re-resolves and re-focuses that exact element and gates on ITS enclosing
form, not the whole page. Unrelated churn (a banner/badge) no longer false-refuses a valid write, while a
change inside the submitted form still fails loud. The symmetric completion of the click/type scope gate
(#28): an Enter-submit carries no element ref, so its precondition is pinned to the field that's focused.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import ultracua.flow as flow_mod
from ultracua.browser import BrowserSession
from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.locators import focused_ref
from ultracua.providers.scripted import ScriptedProvider

_FIX = Path(__file__).parents[1] / "benchmarks" / "fixtures" / "mutating_press.html"
URL = _FIX.resolve().as_uri()
# Same login flow, but the submit dispatches its POST ASYNCHRONOUSLY (deferred fetch) — the timing that
# actually trips the header-clear race; a navigation submit queues its request synchronously and masks it.
_FIX_ASYNC = Path(__file__).parents[1] / "benchmarks" / "fixtures" / "mutating_press_async.html"
URL_ASYNC = _FIX_ASYNC.resolve().as_uri()
GOAL = "sign in"
STEPS = [
    {"action": "type", "role": "textbox", "name": "username", "text": "alice", "intent": "enter the username"},
    {"action": "press", "text": "Enter", "intent": "submit the login"},
    {"action": "done", "intent": "signed in"},
]


def _recorder(captured: list, drift: str = ""):
    """`drift`: "" none; "outside" adds a control OUTSIDE the login form (unrelated churn);
    "inside" adds an input INTO the login form (changes the write's actual context)."""

    async def prepare(session) -> None:
        async def handler(route) -> None:
            captured.append(dict(route.request.headers))
            await route.fulfill(status=200, content_type="text/html",
                                body="<html><body>signed in</body></html>")

        await session.page.route("**/login", handler)
        if drift == "outside":
            await session.page.evaluate(
                "() => { const b = document.createElement('button'); "
                "b.textContent = 'Cookie banner'; document.body.appendChild(b); }"
            )
        elif drift == "inside":
            await session.page.evaluate(
                "() => { const i = document.createElement('input'); i.name = 'otp'; "
                "document.getElementById('login-form').appendChild(i); }"
            )

    return prepare


async def test_focused_ref_returns_a_unique_ref() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content('<input data-ultracua-ref="e0" id="x">'
                                       '<button data-ultracua-ref="e1">b</button>')
        await session.page.focus("#x")
        assert await focused_ref(session.page) == "e0"
    finally:
        await session.close()


async def test_focused_ref_fails_closed_on_a_stale_duplicate_ref() -> None:
    # The snapshot re-tags survivors each step without clearing old tags: a focused field evicted from
    # this step's snapshot can carry a STALE ref that now also tags a different survivor. The capture
    # must NOT trust it (describing it would silently pin the wrong element) -> None -> whole-page gate.
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content('<input data-ultracua-ref="e0" id="y">'   # DOM-first, NOT focused
                                       '<input data-ultracua-ref="e0" id="x">')   # focused, stale dup ref
        await session.page.focus("#x")
        assert await focused_ref(session.page) is None
    finally:
        await session.close()


async def test_focused_ref_none_when_unfocused_or_unreffed() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content('<input id="x">')  # focused but no ref
        await session.page.focus("#x")
        assert await focused_ref(session.page) is None
        await session.page.evaluate("() => document.activeElement && document.activeElement.blur()")
        assert await focused_ref(session.page) is None  # nothing focused
    finally:
        await session.close()


async def test_press_submit_learns_the_focused_field_by_identity(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path)
    learn = await run_cached(URL, GOAL, ScriptedProvider(list(STEPS)), cache, mode="learn",
                             prepare=_recorder([]), headless=True)
    assert learn.success
    flow = cache.get(flow_key(GOAL, URL, "default"))
    press = next(s for s in flow.steps if s.action == "press")
    assert press.mutating is True
    # the refless submit pinned the FOCUSED field (identity) + its form scope — not just the page fp
    assert press.locator is not None and press.locator.role == "textbox"
    assert "username" in press.locator.name.lower()
    assert press.precond_scope


async def test_press_gate_allows_write_under_unrelated_churn(tmp_path: Path) -> None:
    """A banner added OUTSIDE the login form must NOT false-refuse the Enter-submit (the whole-page
    fingerprint used to)."""
    cache = FlowCache(root=tmp_path)
    learn = await run_cached(URL, GOAL, ScriptedProvider(list(STEPS)), cache, mode="learn",
                             prepare=_recorder([]), headless=True)
    assert learn.success

    caps: list = []
    replay = await run_cached(URL, GOAL, None, cache, mode="replay",
                              prepare=_recorder(caps, drift="outside"), headless=True)
    assert replay.success is True  # unrelated churn outside the form does not trip the gate
    # the Enter-submit write fired (gate allowed it, was false-refused by the page fp) AND carried the
    # idempotency key — the press act now awaits the in-flight POST so the header is still set on the wire.
    assert caps and caps[0].get("idempotency-key", "").startswith("uca-")


async def test_idempotency_key_injected_on_press_submit_replay(tmp_path: Path) -> None:
    """A refless mutating submit (press Enter) carries the Idempotency-Key on replay, exactly like a
    click-submit (tests/test_safety_integration.py::test_idempotency_key_injected_on_mutating_replay).
    This is the NAVIGATION-submit case: the form POST is queued synchronously during the keypress, so it
    already carried the key — the async-submit race guard is the deferred-fetch test below."""
    cache = FlowCache(root=tmp_path)
    learn_caps: list = []
    replay_caps: list = []

    learn = await run_cached(URL, GOAL, ScriptedProvider(list(STEPS)), cache, mode="learn",
                             prepare=_recorder(learn_caps), headless=True)
    assert learn.success

    replay = await run_cached(URL, GOAL, None, cache, mode="replay",
                              prepare=_recorder(replay_caps), headless=True)
    assert replay.success
    # Learn did NOT inject a key; replay DID (mutation-gated, idempotent) — even for a refless submit.
    assert learn_caps and "idempotency-key" not in learn_caps[0]
    assert replay_caps and replay_caps[0].get("idempotency-key", "").startswith("uca-")


async def test_idempotency_key_held_until_async_press_submit_fires(tmp_path: Path) -> None:
    """REGRESSION GUARD: when the refless submit dispatches its write ASYNCHRONOUSLY (an SPA-style fetch
    fired a tick after the keypress), the press replay must hold the Idempotency-Key on the context until
    the POST actually goes out. Before the fix, `session.act` returned the moment keyboard.press did and
    `_replay_step`'s `finally` cleared the header BEFORE the deferred POST fired — so the write replayed
    WITHOUT the dedupe key (a retried submit could double-submit). The fix awaits the in-flight write
    (page.expect_request) so the header is still live when it leaves the browser. Fails without the fix."""
    cache = FlowCache(root=tmp_path)
    learn = await run_cached(URL_ASYNC, GOAL, ScriptedProvider(list(STEPS)), cache, mode="learn",
                             prepare=_recorder([]), headless=True)
    assert learn.success

    caps: list = []
    replay = await run_cached(URL_ASYNC, GOAL, None, cache, mode="replay",
                              prepare=_recorder(caps), headless=True)
    assert replay.success
    # the DEFERRED POST carried the key — the press act awaited the in-flight write before clearing it.
    assert caps and caps[0].get("idempotency-key", "").startswith("uca-")


async def test_press_gate_blocks_write_under_form_drift(tmp_path: Path) -> None:
    """A new field added INSIDE the login form (the write's actual context) must fail loud."""
    cache = FlowCache(root=tmp_path)
    learn = await run_cached(URL, GOAL, ScriptedProvider(list(STEPS)), cache, mode="learn",
                             prepare=_recorder([]), headless=True)
    assert learn.success

    caps: list = []
    replay = await run_cached(URL, GOAL, None, cache, mode="replay",
                              prepare=_recorder(caps, drift="inside"), headless=True)
    assert replay.success is False  # the gate refused to blind-replay the submit under form drift
    assert caps == []               # ...and the login POST was never sent


# --- CLICK analog of the deferred-press idem guard ------------------------------------------------
# A FORMLESS mutating CLICK whose handler dispatches its write a tick later (setTimeout fetch-POST). The
# mutating click/type/select replay act now awaits the in-flight write (page.expect_request, BOUNDED to
# write_settle_ms) so the `finally` doesn't clear the Idempotency-Key before the deferred POST fires.
_FIX_CLICK_ASYNC = Path(__file__).parents[1] / "benchmarks" / "fixtures" / "mutating_click_async.html"
URL_CLICK_ASYNC = _FIX_CLICK_ASYNC.resolve().as_uri()
CLICK_GOAL = "place the order"
CLICK_STEPS = [
    {"action": "click", "role": "button", "name": "Place order", "intent": "place the order"},
    {"action": "done", "intent": "ordered"},
]


def _order_recorder(captured: list):
    async def prepare(session) -> None:
        async def handler(route) -> None:
            captured.append(dict(route.request.headers))
            await route.fulfill(status=200, content_type="application/json", body="{}")

        await session.page.route("**/order", handler)

    return prepare


async def test_idempotency_key_held_until_async_click_submit_fires(tmp_path: Path) -> None:
    """REGRESSION GUARD (click analog of test_idempotency_key_held_until_async_press_submit_fires): a
    FORMLESS, keyword-mutating CLICK whose handler dispatches its write ASYNCHRONOUSLY (a setTimeout
    fetch-POST a tick after the click) must hold the Idempotency-Key on the context until the deferred POST
    leaves the browser. Before the wrapper, loc.click() returned and `_replay_step`'s `finally` cleared the
    header BEFORE the deferred POST fired — so the write replayed WITHOUT the dedupe key (a retried run could
    double-submit). The mutating click act now awaits the in-flight write (page.expect_request, bounded to
    write_settle_ms). Affects LEARNED write flows — the recorder itself REFUSES deferred-click writes at
    capture time, so this is learned via ScriptedProvider, not recorded."""
    cache = FlowCache(root=tmp_path)
    learn = await run_cached(URL_CLICK_ASYNC, CLICK_GOAL, ScriptedProvider(list(CLICK_STEPS)), cache,
                             mode="learn", prepare=_order_recorder([]), headless=True)
    assert learn.success
    flow = cache.get(flow_key(CLICK_GOAL, URL_CLICK_ASYNC, "default"))
    click = next(s for s in flow.steps if s.action == "click")
    assert click.mutating is True and click.precond_scope   # keyword-gated, formless -> its section's scope

    caps: list = []
    replay = await run_cached(URL_CLICK_ASYNC, CLICK_GOAL, None, cache, mode="replay",
                              prepare=_order_recorder(caps), headless=True)
    assert replay.success
    # the DEFERRED click POST carried the key — the click act awaited the in-flight write before clearing it.
    assert caps and caps[0].get("idempotency-key", "").startswith("uca-")


async def test_idempotency_key_dropped_when_settle_below_defer(tmp_path: Path, monkeypatch) -> None:
    """LOAD-BEARING proof: with write_settle_ms BELOW the write's defer (5ms < the fixture's 50ms), the act
    stops waiting before the deferred POST fires, the `finally` clears the header, and the write replays
    WITHOUT the Idempotency-Key. Confirms it is the bounded WAIT — not merely the wrapper's existence — that
    keeps the key in the happy-path test above (a regression dropping the bound back to action_timeout_ms
    would still pass that test; this one fails)."""
    monkeypatch.setattr(flow_mod, "settings", replace(flow_mod.settings, write_settle_ms=5))
    cache = FlowCache(root=tmp_path)
    learn = await run_cached(URL_CLICK_ASYNC, CLICK_GOAL, ScriptedProvider(list(CLICK_STEPS)), cache,
                             mode="learn", prepare=_order_recorder([]), headless=True)
    assert learn.success

    caps: list = []
    replay = await run_cached(URL_CLICK_ASYNC, CLICK_GOAL, None, cache, mode="replay",
                              prepare=_order_recorder(caps), headless=True)
    assert replay.success
    # the deferred POST did NOT carry the key — the wait ended before it fired (and may not be captured at all).
    assert not (caps and caps[0].get("idempotency-key", "").startswith("uca-"))


# --- the timeout=0 "wait forever" footgun: a no-write mutating step must PROCEED, not hang ---------------
_FIX_NOOP_CLICK = Path(__file__).parents[1] / "benchmarks" / "fixtures" / "noop_mutating_click.html"
URL_NOOP_CLICK = _FIX_NOOP_CLICK.resolve().as_uri()
NOOP_GOAL = "place the order (no wire write)"


async def test_no_write_mutating_step_does_not_hang_on_zero_settle(tmp_path: Path, monkeypatch) -> None:
    """GUARD (invariant: a no-write mutating step must proceed after the bounded wait). Playwright treats an
    expect_request timeout of EXACTLY 0 as "wait forever", so a 0/negative write_settle_ms (a tuner trying to
    "disable" the wait) would make min(action_timeout_ms, 0)=0 and HANG a mutating step that fires no write.
    The bound is FLOORED to >=1ms (flow.py), so the step times out ~immediately and proceeds. This replaces
    the settings object directly with write_settle_ms=0 (bypassing the config clamp) to exercise the
    call-site floor; without it, asyncio.wait_for below trips."""
    monkeypatch.setattr(flow_mod, "settings", replace(flow_mod.settings, write_settle_ms=0))
    cache = FlowCache(root=tmp_path)
    learn = await run_cached(URL_NOOP_CLICK, NOOP_GOAL, ScriptedProvider(list(CLICK_STEPS)), cache,
                             mode="learn", headless=True)
    assert learn.success
    click = next(s for s in cache.get(flow_key(NOOP_GOAL, URL_NOOP_CLICK, "default")).steps
                 if s.action == "click")
    assert click.mutating is True   # keyword-mutating, fires NO write -> exercises the no-write wrapper path

    # Must COMPLETE, not hang — asyncio.wait_for is the tripwire (without the 1ms floor this never returns).
    replay = await asyncio.wait_for(
        run_cached(URL_NOOP_CLICK, NOOP_GOAL, None, cache, mode="replay", headless=True), timeout=60)
    assert replay.success
