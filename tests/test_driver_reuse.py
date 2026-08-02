"""The Playwright DRIVER is shared per event loop, and held across multi-session verbs.

Starting the driver is a Node subprocess launch plus a handshake, and it dominates session setup —
measured on this project: driver 364 ms, `chromium.launch()` 81 ms, `new_context()+new_page()` 58 ms. Every
`BrowserSession` used to start and stop its own.

THE PART THAT IS EASY TO GET WRONG, and which these tests pin. A reference count alone buys nothing for
SERIAL use — one session at a time, which is exactly `run_batch`'s row loop: the count swings 1 -> 0 -> 1 and
the driver restarts every time. Measured over five serial sessions: 3129 ms with a driver per session, 3029 ms
with a naive refcount (no win), 1092 ms when a reference is HELD across the run. So the win exists only
because the batch/fleet verbs hold a `driver_scope`. `test_serial_sessions_inside_a_scope_share_one_driver`
is the test that would go quiet if someone removed those scopes while keeping the refcount.

Per EVENT LOOP, not per process: a `Playwright` object owns a transport bound to the loop that created it,
so handing it to a later `asyncio.run` would use a dead one. Nothing here may leak a driver either — an
orphaned Node process per session would be strictly worse than the cost it saves.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import playwright.async_api as pa
import pytest

import ultracua.browser as browsermod
from ultracua.browser import BrowserSession, _drivers, driver_scope


@pytest.fixture()
def starts(monkeypatch):
    """Count REAL driver starts — the thing being saved — not calls to `_acquire_driver`."""
    n = {"count": 0}
    real = pa.async_playwright

    class _Counting:
        def __init__(self) -> None:
            self._inner = real()

        async def start(self):
            n["count"] += 1
            return await self._inner.start()

    monkeypatch.setattr(browsermod, "async_playwright", _Counting)
    return n


def _ref():
    return _drivers.get(asyncio.get_running_loop())


async def test_serial_sessions_inside_a_scope_share_one_driver(starts) -> None:
    """THE load-bearing test. Three sessions, one after another — the `run_batch` shape. Without the held
    scope this is three driver starts and the whole change is a no-op."""
    async with driver_scope():
        for _ in range(3):
            s = await BrowserSession(headless=True).start()
            await s.close()
    assert starts["count"] == 1


async def test_serial_sessions_without_a_scope_each_start_their_own(starts) -> None:
    """The counterpart, stated so the reason the scopes exist is impossible to miss: a bare refcount does
    NOT survive the gap between sessions. This is the behaviour `run_batch` had before it held a scope."""
    for _ in range(3):
        s = await BrowserSession(headless=True).start()
        await s.close()
    assert starts["count"] == 3


async def test_concurrent_sessions_share_one_driver(starts) -> None:
    """The `run_all` shape. Also the race: `_acquire_driver`'s get-or-create must be atomic under one loop,
    or four coroutines each install their own entry."""
    async def _one() -> None:
        s = await BrowserSession(headless=True).start()
        await s.close()

    await asyncio.gather(*[_one() for _ in range(4)])
    assert starts["count"] == 1          # they overlap, so the count never reaches 0 between them


async def test_the_driver_is_stopped_when_the_last_reference_goes() -> None:
    """Nothing may be orphaned: an abandoned Node process per session would cost more than it saves."""
    async with driver_scope():
        s = await BrowserSession(headless=True).start()
        await s.close()
        assert _ref() is not None and _ref().pw is not None and _ref().refs == 1   # the scope still holds
    assert _ref().refs == 0
    assert _ref().pw is None             # ...and it was actually stopped, not merely dereferenced


async def test_a_session_that_fails_to_start_releases_its_reference(monkeypatch) -> None:
    """`start()` tears itself down on failure. If it leaked the driver reference, the count could never
    return to zero and the driver would outlive the loop."""
    async def _boom(self, **kw):
        raise RuntimeError("launch refused")

    monkeypatch.setattr(pa.BrowserType, "launch", _boom)
    with pytest.raises(RuntimeError):
        await BrowserSession(headless=True).start()
    ref = _ref()
    assert ref is None or ref.refs == 0


async def test_closing_twice_releases_exactly_one_reference() -> None:
    """A double close must not push the count negative and stop a driver another session is using."""
    async with driver_scope():
        s = await BrowserSession(headless=True).start()
        await s.close()
        await s.close()                  # idempotent
        assert _ref().refs == 1          # only the scope's own reference remains
        assert _ref().pw is not None     # ...so the driver is still alive and usable
        s2 = await BrowserSession(headless=True).start()
        await s2.close()


def test_a_second_event_loop_gets_its_own_driver() -> None:
    """Sync on purpose: it needs two real `asyncio.run` calls. A driver from a finished loop owns a dead
    transport, so reusing one across loops would fail — this is why the cache is keyed by loop."""
    async def _work() -> int:
        s = await BrowserSession(headless=True).start()
        try:
            await s.page.set_content("<h1>ok</h1>")
            return len(await s.page.inner_text("h1"))
        finally:
            await s.close()

    assert asyncio.run(_work()) == 2
    assert asyncio.run(_work()) == 2     # a stale cross-loop driver would raise here


async def test_a_shared_browser_session_takes_no_driver_reference(starts) -> None:
    """`parallel.run_many` supplies its own browser and owns its own driver. Such a session must not take
    a reference on the shared one — it never started it and must not be able to stop it."""
    real = pa.async_playwright()          # NOT the counting wrapper: this is the caller's own driver
    pw = await real.start()
    try:
        browser = await pw.chromium.launch(headless=True)
        s = await BrowserSession(headless=True, browser=browser).start()
        await s.close()
        ref = _ref()
        assert ref is None or ref.refs == 0
        assert starts["count"] == 0
        await browser.close()
    finally:
        await pw.stop()


async def test_run_batch_starts_one_driver_for_the_whole_batch(tmp_path: Path, monkeypatch,
                                                               starts) -> None:
    """End to end through the public verb — the reason the scope is there at all. Measured on a real
    8-row write batch: 8 driver starts -> 1, and 1328 -> 966 ms per row."""
    monkeypatch.chdir(tmp_path)
    from test_run_batch import _CheckoutSite, _record_write_flow

    from ultracua.cache import FlowCache
    from ultracua.flows import run_batch

    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        starts["count"] = 0                                   # ignore the recording's own session
        out = await run_batch(spec, [{"qty": "1"}, {"qty": "2"}, {"qty": "3"}], max_rows=3, cache=cache)
        assert [r.status for r in out.rows] == ["ok", "ok", "ok"], out.rows
        assert starts["count"] == 1                           # one driver, three rows, three browsers
    finally:
        httpd.shutdown()
        httpd.server_close()
