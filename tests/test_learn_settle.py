"""The learn-side settle: what it promises, where it is applied, and how it fails.

WHY IT EXISTS. `_author_steps` snapshots, decides, acts. On a client-rendered app that snapshot lands
on an unpainted shell -- Odoo serves 5 elements at `domcontentloaded` and settles to 80-255 around
470-860 ms -- so the agent decides from a page that is not there, re-navigates because nothing it
wants is visible (R4.118), and the loop's existing `no_progress` bail cannot stop it because
`state_changed` only asks whether the page DIFFERS and every unpainted shell differs (R4.119).

WHY A SETTLE RATHER THAN A NEW GUARD. The guard already exists. Feeding it a settled observation
re-arms it and adds nothing that can refuse, which keeps this away from D0 entirely. Measured on real
Odoo: six navigations to the same url left `no_progress` at 0 before and reached 4 -- the limit --
after.

WHY 200 ms. `readiness_probe --settle` scored candidates against a ground truth over 60 page-reps
(R4.120): `mut-quiet-200` was the cheapest that was NEVER premature, where acting immediately was
premature 28 times and "two equal element counts" 17.
"""

from __future__ import annotations

import inspect

import pytest

from ultracua import flow as flow_mod
from ultracua.browser import BrowserSession
from ultracua.config import settings


# --------------------------------------------------------------- what it promises (live)


@pytest.mark.asyncio
async def test_a_static_page_settles_quiet() -> None:
    """The common case, and the floor: a page that never mutates goes quiet after one quiet window."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content("<body><h1>static</h1><a href='#'>x</a></body>")
        assert await s.await_settled() == "quiet"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_a_page_that_never_stops_mutating_hits_the_cap() -> None:
    """THE RESIDUAL, pinned. A page with a persistent ticker never goes quiet -- none exists in the
    measured corpus, so this is the case the numbers could not speak to. On the cap the settle gives
    up and the caller proceeds, which is EXACTLY the behaviour before this mechanism existed: the
    worst case is "no better than before", never "worse"."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content(
            "<body><div id=t></div><script>setInterval(function(){"
            "document.getElementById('t').textContent = Date.now();}, 20);</script></body>")
        assert await s.await_settled(quiet_ms=200, cap_ms=600) == "cap"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_a_mutating_page_that_stops_is_waited_out() -> None:
    """The case the whole mechanism is for: mutations that stop AFTER the caller asks. A predicate
    that sampled twice and compared could return inside this burst (R4.120 measured that failing 17
    times); one that restarts its timer on every mutation cannot."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content(
            "<body><div id=t></div><script>let n=0;const h=setInterval(function(){"
            "document.getElementById('t').appendChild(document.createElement('span'));"
            "if (++n > 8) clearInterval(h);}, 30);</script></body>")
        assert await s.await_settled(quiet_ms=200, cap_ms=5000) == "quiet"
        assert await s.page.evaluate("() => document.querySelectorAll('#t span').length") >= 8, (
            "returned before the burst finished -- the quiet timer is not being restarted")
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_a_closed_page_is_unavailable_not_an_exception() -> None:
    """FAIL-OPEN. A page mid-navigation makes `evaluate` throw, and a settle that turned a transient
    into a failed step would be a regression bought with a diagnostic."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.close()
        assert await s.await_settled() == "unavailable"
    finally:
        await s.close()


# ------------------------------------------------------- where it is applied (structural)


def test_the_learn_settles_before_both_of_its_observations() -> None:
    """BOTH sites, and the second is the one that re-arms the bail. The loop-head settle is what lets
    the agent SEE the page; the verify settle is what makes `changed` mean "my action did something"
    rather than "the render advanced"."""
    src = inspect.getsource(flow_mod)
    assert 'tr.meta["settled"] = await session.await_settled()' in src, "loop-head settle missing"
    assert 'tr.meta["settled_after"] = await session.await_settled()' in src, "verify settle missing"
    head = src.index('tr.meta["settled"] = await session.await_settled()')
    assert head < src.index("obs = await session.snapshot()"), (
        "the settle must PRECEDE the observation it is settling for")


def test_replay_is_deliberately_untouched() -> None:
    """THE ASYMMETRY IS THE DESIGN, not an oversight. A server-rendered page measures
    `true_ready = 0` in every rep (R4.120), so an unconditional wait on the replay path is pure tax
    on the product's own speed claim. Replay's fix is a CONDITIONAL two-state sensor and lands
    separately; if someone adds an unconditional settle there, this fails and they have to argue it."""
    src = inspect.getsource(flow_mod)
    assert src.count("await_settled()") == 2, (
        f"expected exactly the two LEARN call sites, found {src.count('await_settled()')} -- if a "
        f"third is on the replay path, R4.120's cost measurement says to make it conditional")


def test_the_settle_window_is_the_measured_one() -> None:
    """A tuning constant nobody can source is how a measured number becomes folklore. These two came
    from `readiness_probe --settle` over 60 page-reps; the cap clears the largest observed firing."""
    assert settings.settle_quiet_ms == 200
    assert settings.settle_cap_ms == 2000
    assert settings.settle_cap_ms > settings.settle_quiet_ms, (
        "a cap at or below the quiet window makes every settle return `cap` and the mechanism inert")
