"""The replay looks more than once for a page that has not painted. (R4.144.)

Odoo's list -> form transition is a MULTI-STAGE render: an onchange POST, then a lazily-loaded asset
bundle, then a render call ~547 ms later. **The DOM is quiet BETWEEN those stages** -- nothing to
mutate while a bundle downloads -- so mutation-quiet fires on a page that has not started its next
paint, and the single retry looks straight into the gap. Measured in a real replay, three reps,
identical every time: look 1 `quiet` misses, looks 2-4 `already-quiet` miss, look 5 binds at
719-906 ms. `odoo-create-lead` went `refused_wrongly` -> `true` 3/3.

THE FIXTURE HAS TO HAVE THE GAPS OR IT PROVES NOTHING. A page that mutates continuously is settled
by ONE wait and every cell here would pass against the old single-look code -- R4.138's lesson, where
a pane placed where the pointer already was made the central mutation a survivor. `_staged_page`
therefore paints in bursts with quiet gaps between them, which is the shape that was measured.

AND THE INERT DIRECTIONS ARE PINNED HARDEST, because they are what a poll puts at risk: a refusal
must still fail LOUD and immediately (waiting on a refusal is the measured wrong-record bind R4.115
was refuted for), and a page ALREADY quiet when asked must still skip entirely, which is what keeps
`drift_bench`'s static corpus off this path.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
import types

import pytest

from ultracua.browser import BrowserSession
from ultracua.config import settings
from ultracua import flow as flow_mod
from ultracua.flow import _retry_if_unpainted
from ultracua.locators import LocatorSpec, resolve

TARGET = LocatorSpec(role="button", name="Ready", tag="button", text="Ready")

_STAGED_JS = """
(ms) => {
  const host = document.getElementById('host');
  // BURSTS 250 ms APART, and the spacing is the whole point twice over. Wider than the 200 ms quiet
  // window, so `await_settled` returns BETWEEN them and the poll actually iterates -- tighter and
  // the entry settle simply waits until they stop, binding on look 1 and proving nothing.
  //
  // AND NO GAP LONGER THAN THE STALL WINDOW. The first draft put its last burst at 340 ms and the
  // target at 700 -- a 360 ms quiet gap against a window of `settle_stall_looks * settle_poll_ms`
  // (~300 ms) PLUS per-look settle overhead. That overhead is what a fast machine has less of, so
  // the fixture passed here and failed BOTH CI arms with `already-quiet:stalled:1`: a cell racing
  // the very guard it shares a codebase with. Bursts now continue past the target, so the stall
  // counter is reset every cycle and never approaches its limit on any machine.
  for (const t of [60, 310, 560, 810]) {
    setTimeout(function () {
      const d = document.createElement('span');
      d.textContent = 'stage';
      host.appendChild(d);
    }, t);
  }
  setTimeout(function () {
    const b = document.createElement('button');
    b.textContent = 'Ready';
    host.appendChild(b);
  }, ms);
}
"""

_AMBIGUOUS_LATER_JS = """
() => {
  const host = document.getElementById('host');
  // Quiet gaps first, so look 1 sees NOTHING and the poll is entered through the unpainted door.
  for (const t of [60, 200]) {
    setTimeout(function () {
      const d = document.createElement('span');
      d.textContent = 'stage';
      host.appendChild(d);
    }, t);
  }
  // Then TWO of them at once: the page has answered, ambiguously. A poll must stop here rather than
  // wait for one to go away -- which is precisely the wrong-record bind R4.115 measured.
  setTimeout(function () {
    for (let i = 0; i < 2; i++) {
      const b = document.createElement('button');
      b.textContent = 'Ready';
      host.appendChild(b);
    }
  }, 420);
}
"""

_CHURN_JS = """
() => setInterval(function () {
  const s = document.createElement('span');
  s.textContent = 'x';
  document.getElementById('host').appendChild(s);
}, 120)
"""


def _tr():
    return types.SimpleNamespace(meta={})


async def _staged_page(sess, *, appears_after_ms: int) -> None:
    """Paint in BURSTS with quiet gaps, the way a network-gated render does."""
    await sess.page.set_content("<div id='host'><button>Other</button></div>")
    await sess.page.evaluate(_STAGED_JS, appears_after_ms)


class _ScriptedSession:
    """A session whose settle verdicts are scripted: one ENTRY verdict, then a repeating pattern.

    It REPEATS rather than running out. A finite list that falls back to `already-quiet` on
    exhaustion silently turns "a page that keeps moving" into "a page that stopped" partway through
    the run -- which is what the first scripted draft did, stalling at look 21 and reporting the
    reset as broken when it was fine.
    """

    def __init__(self, entry, pattern):
        self._entry = entry
        self._pattern = list(pattern)
        self._i = 0
        self.asked = 0

    async def await_settled(self):
        self.asked += 1
        if self.asked == 1:
            return self._entry
        v = self._pattern[self._i % len(self._pattern)]
        self._i += 1
        return v


class _ScriptedPage:
    """The beat, with no clock in it -- so a scripted cell about the COUNTER runs instantly.

    `_ultracua_inflight = 1` because these cells are about what the poll does ONCE IT IS POLLING,
    and since 0.169.0 it only polls when something was outstanding at entry (R4.146). A stub that
    reported zero would exit before the loop and every cell below would pass without executing the
    code it names -- the inert-fixture failure this file has already had twice.
    """

    _ultracua_inflight = 1

    async def wait_for_timeout(self, _ms):
        return None


class _SleepingPage:
    """The beat with its real duration, for the one cell whose subject IS that duration.

    `_ScriptedPage` makes the beat free, which is right for the counter cells and wrong here: a cell
    measuring how long the poll watches a stopped page cannot use a stub that removes the waiting.
    """

    _ultracua_inflight = 1        # see `_ScriptedPage`: these cells are about the LOOP

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(ms / 1000.0)


class _ScriptedResolve:
    """`resolve` as a SCRIPT: a list of (bound, saw_candidates) answers, in order.

    THREE CELLS HERE USED TO DRIVE A REAL PAGE AND COUPLE A DOM CHANGE TO THE POLL'S OWN CLOCK, and
    two of them failed CI twice, each time differently. The first fixture put a 360 ms quiet gap
    against a stall window of `settle_stall_looks * settle_poll_ms` plus per-look settle OVERHEAD --
    and overhead is exactly what a fast runner has less of, so it stalled there and passed here.
    Widening the bursts fixed that and produced a second race: the target now landed DURING a look,
    the resolver saw a half-updated DOM, and the verdict came back `refused-at`.

    Both failures are the same mistake. These cells are about the poll's CONTROL FLOW -- how many
    times it looks, and what it does with each answer -- and a real page adds a clock to a property
    that has none. The cells that remain on a browser below are the ones whose subject really is a
    page: an ambiguous DOM, a static DOM, a DOM that never stops mutating.
    """

    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = 0

    async def __call__(self, page, spec, *a, **kw):
        self.calls += 1
        bound, saw = self._answers[min(self.calls - 1, len(self._answers) - 1)]
        sink = kw.get("sink")
        if sink is not None:
            sink["saw_candidates"] = saw
            sink["bound_by"] = "role+name" if bound else "none"
        return object() if bound else None


@pytest.mark.asyncio
async def test_the_target_that_appears_after_several_quiet_gaps_IS_found(monkeypatch) -> None:
    """THE DEFECT: one look lands in a gap. The measured Odoo shape needed five.

    Scripted, because the property is control flow: absent, absent, then present must return the
    element. The real-page evidence for this is the Odoo measurement in R4.144 -- three reps of a
    live replay, look 1 `quiet` missing at ~343 ms and look 5 binding at 719-906 ms -- which is
    stronger than any fixture and does not race a clock in CI.
    """
    scripted = _ScriptedResolve([(False, False), (False, False), (True, False)])
    monkeypatch.setattr(flow_mod, "resolve", scripted, raising=True)
    moving = _ScriptedSession("quiet", ["quiet"])       # the page keeps painting between looks
    tr = _tr()
    got = await flow_mod._retry_if_unpainted(
        moving, _ScriptedPage(), TARGET, tr, None, sink={"saw_candidates": False})
    assert got is not None, (
        f"a target that appeared on the third look was never returned: "
        f"{tr.meta['readiness_retry']!r} after {scripted.calls} resolve(s)")
    assert ":bound:" in tr.meta["readiness_retry"], tr.meta


@pytest.mark.asyncio
async def test_it_really_took_MORE_THAN_ONE_look(monkeypatch) -> None:
    """Without this the cell above is satisfied by a single look that got lucky.

    The recorded verdict carries the count, so the property is checkable rather than inferred.
    """
    scripted = _ScriptedResolve([(False, False), (False, False), (True, False)])
    monkeypatch.setattr(flow_mod, "resolve", scripted, raising=True)
    moving = _ScriptedSession("quiet", ["quiet"])
    tr = _tr()
    await flow_mod._retry_if_unpainted(
        moving, _ScriptedPage(), TARGET, tr, None, sink={"saw_candidates": False})
    looks = int(tr.meta["readiness_retry"].rsplit(":", 1)[1])
    assert looks == 3, (
        f"the poll bound after {looks} look(s) against a script that answers on the third -- one "
        f"look cannot refute the single-look code: {tr.meta['readiness_retry']!r}")
    assert scripted.calls == 3, f"resolve was called {scripted.calls} times, expected 3"


@pytest.mark.asyncio
async def test_a_refusal_that_APPEARS_MID_POLL_stops_it_too(monkeypatch) -> None:
    """The in-loop half of the safety guard, which the top-of-function guard does NOT cover.

    A mutation removing the per-look `saw_candidates` check survives every cell about the poll
    BINDING, and the refusal cell below enters through the top guard and never reaches the loop. So
    the shape that matters is: ABSENT on look 1, and the page ANSWERING a look later. R4.115's
    measured wrong-record bind is that timeline in reverse -- wait long enough and the competitor
    resolves itself -- so it must not be waited on.
    """
    scripted = _ScriptedResolve([(False, False), (False, True)])
    monkeypatch.setattr(flow_mod, "resolve", scripted, raising=True)
    moving = _ScriptedSession("quiet", ["quiet"])
    tr = _tr()
    got = await flow_mod._retry_if_unpainted(
        moving, _ScriptedPage(), TARGET, tr, None, sink={"saw_candidates": False})
    assert got is None, (
        "the poll kept going after the page ANSWERED -- this is the wrong-record bind R4.115 "
        "measured, arriving one look later")
    assert "refused-at" in tr.meta["readiness_retry"], tr.meta


@pytest.mark.asyncio
async def test_a_REFUSAL_stops_at_once_and_is_never_waited_out() -> None:
    """THE SAFETY DIRECTION, and the reason the loop is keyed on `saw_candidates` every look.

    R4.115 MEASURED the naive remedy binding a wrong record: with two per-row `Cancel` links and the
    recorded row hidden at t=400ms, a poll keyed on the RETURN VALUE waits for the competitor to
    disappear and then binds `/cancel/30` where `/cancel/3` was recorded. A poll must never make a
    refusal more likely to pass; the moment the page ANSWERS, this stops.
    """
    async with BrowserSession(headless=True) as sess:
        await sess.page.set_content("<button>Ready</button><button>Ready</button>")
        sink: dict = {"saw_candidates": True}
        tr = _tr()
        t0 = time.monotonic()
        got = await _retry_if_unpainted(sess, sess.page, TARGET, tr, None, sink=sink)
        elapsed = (time.monotonic() - t0) * 1000
        assert got is None, "an ambiguous page was waited out into a bind"
        assert tr.meta["readiness_retry"] == "refused", tr.meta
        assert elapsed < 150, (
            f"a refusal waited {elapsed:.0f} ms; it must fail LOUD and IMMEDIATELY, with no settle "
            f"at all -- waiting is exactly what let the competing row disappear")


@pytest.mark.asyncio
async def test_a_page_ALREADY_QUIET_when_asked_still_skips_entirely() -> None:
    """The `drift_bench` saving, which the poll must not spend.

    A page quiet BEFORE the failing resolve has not changed since it, so re-resolving is provably the
    same answer and is not free -- measured at 42 such retries costing 10.1 s on the static corpus,
    6.1% of a run. The poll is reached only after a settle that GENUINELY WAITED, so a static fixture
    never enters the loop.
    """
    async with BrowserSession(headless=True) as sess:
        await sess.page.set_content("<button>Other</button>")
        await sess.await_settled()          # make sure it is quiet before we ask
        sink: dict = {"saw_candidates": False}
        tr = _tr()
        t0 = time.monotonic()
        got = await _retry_if_unpainted(sess, sess.page, TARGET, tr, None, sink=sink)
        elapsed = (time.monotonic() - t0) * 1000
        assert got is None
        assert tr.meta["readiness_retry"] == "already-quiet:skipped", tr.meta
        assert elapsed < settings.settle_cap_ms / 2, (
            f"a static page spent {elapsed:.0f} ms in the poll; it must short-circuit before the "
            f"loop or every drift_bench row pays for a wait that can find nothing")


@pytest.mark.asyncio
async def test_a_target_that_never_appears_is_BOUNDED() -> None:
    """The budget. A page that keeps painting must not hold the replay open indefinitely."""
    async with BrowserSession(headless=True) as sess:
        await sess.page.set_content("<div id='host'></div>")
        await sess.page.evaluate(_CHURN_JS)
        # This cell is about the LOOP, and the poll only loops when something was
        # outstanding at entry (R4.146). `set_content` issues no requests, so say so.
        sess.page._ultracua_inflight = 1
        sink: dict = {"saw_candidates": False}
        tr = _tr()
        t0 = time.monotonic()
        # THE CELL BOUNDS ITS OWN WAIT, or it cannot fail in the direction it exists for. An
        # unbounded poll does not return slowly, it does not return -- so an assertion taken AFTER
        # the call never runs, and the mutation that removes the deadline hangs the suite instead of
        # going red. Measured: the first draft ran past a 600 s harness timeout.
        budget_s = (settings.settle_cap_ms * 2 + 1500) / 1000.0
        try:
            got = await asyncio.wait_for(
                _retry_if_unpainted(sess, sess.page, TARGET, tr, None, sink=sink), budget_s)
        except asyncio.TimeoutError:  # noqa: PERF203
            pytest.fail(
                f"the poll had not returned after {budget_s:.1f}s against a "
                f"{settings.settle_cap_ms} ms budget -- it is unbounded, and a page with an "
                f"animation or a live ticker would hold the replay open indefinitely")
        elapsed = (time.monotonic() - t0) * 1000
        assert got is None
        assert "still-none" in tr.meta["readiness_retry"], tr.meta
        # One settle may be in flight when the deadline passes, so the honest bound is two caps.
        assert elapsed < settings.settle_cap_ms * 2 + 1500, (
            f"the poll ran {elapsed:.0f} ms against a {settings.settle_cap_ms} ms budget")


@pytest.mark.asyncio
async def test_a_resolve_that_SUCCEEDED_never_enters_the_helper_at_all() -> None:
    """The happy path, which is the whole population on a server-rendered substrate.

    Measured: across `gitea-sort-list`, `gitea-search` and `gitea-comment` this path fired 0 times.
    """
    async with BrowserSession(headless=True) as sess:
        await sess.page.set_content("<button>Ready</button>")
        loc = await resolve(sess.page, TARGET, unique=True, sink={})
        assert loc is not None
        tr = _tr()
        t0 = time.monotonic()
        out = await _retry_if_unpainted(sess, sess.page, TARGET, tr, loc, sink={})
        assert out is loc, "a bound locator must be returned untouched"
        assert "readiness_retry" not in tr.meta, "the happy path recorded a retry verdict"
        assert (time.monotonic() - t0) * 1000 < 50


def test_the_poll_interval_is_the_measured_one() -> None:
    """A tuning constant nobody can source is how a measured number becomes folklore."""
    assert settings.settle_poll_ms == 50
    assert settings.settle_poll_ms < settings.settle_quiet_ms, (
        "a beat at or above the quiet window makes the poll slower than the thing it waits for")
    assert settings.settle_cap_ms // settings.settle_poll_ms >= 5, (
        "the budget must afford at least the five looks the measured Odoo shape needed")


@pytest.mark.asyncio
async def test_a_refusal_that_APPEARS_MID_POLL_stops_it_too() -> None:
    """The in-loop half of the safety guard, which the top-of-function guard does NOT cover.

    A mutation removing the per-look `saw_candidates` check SURVIVED the first draft of this file:
    every cell asserting the poll BINDS still passed, and the refusal cell above enters through the
    top guard and never reaches the loop. So this is the shape that matters -- absent on look 1, and
    AMBIGUOUS a moment later. R4.115's measured wrong-record bind is exactly this timeline in
    reverse: wait long enough and the competitor resolves itself. It must not be waited on.
    """
    async with BrowserSession(headless=True) as sess:
        await sess.page.set_content("<div id='host'><button>Other</button></div>")
        await sess.page.evaluate(_AMBIGUOUS_LATER_JS)
        sink: dict = {}
        first = await resolve(sess.page, TARGET, unique=True, sink=sink)
        assert first is None and sink.get("saw_candidates") is False, (
            "the fixture must start ABSENT, or this enters through the top guard like the cell above")
        tr = _tr()
        got = await _retry_if_unpainted(sess, sess.page, TARGET, tr, None, sink=sink)
        assert got is None, (
            "the poll bound a target it had already seen as AMBIGUOUS -- this is the wrong-record "
            "bind R4.115 measured, arriving one look later")
        assert "refused-at" in tr.meta["readiness_retry"], tr.meta


@pytest.mark.asyncio
async def test_the_poll_does_not_SPIN_between_looks() -> None:
    """The beat, which nothing else can see.

    Removing `wait_for_timeout` still binds and is still bounded by the deadline, so every other cell
    passes -- the mutation SURVIVED until this cell existed. What changes is the RATE: the next
    `await_settled` returns `already-quiet` instantly on a page between network-gated stages, so
    without a beat the loop walks the resolver ladder as fast as the event loop allows for the whole
    budget. The measured Odoo shape needed FIVE looks; a spinning loop takes hundreds.
    """
    async with BrowserSession(headless=True) as sess:
        await sess.page.set_content("<div id='host'></div>")   # quiet after the first settle
        await sess.page.evaluate(_CHURN_JS)
        sink: dict = {"saw_candidates": False}
        tr = _tr()
        budget_s = (settings.settle_cap_ms * 2 + 1500) / 1000.0
        try:
            await asyncio.wait_for(
                _retry_if_unpainted(sess, sess.page, TARGET, tr, None, sink=sink), budget_s)
        except asyncio.TimeoutError:  # noqa: PERF203
            pytest.fail(f"the poll had not returned after {budget_s:.1f}s")
        looks = int(tr.meta["readiness_retry"].rsplit(":", 1)[1])
        budget_looks = settings.settle_cap_ms / settings.settle_poll_ms
        assert looks <= budget_looks * 2, (
            f"the poll took {looks} looks against a budget affording about {budget_looks:.0f} -- it "
            f"is spinning the resolver ladder rather than pausing between looks")


@pytest.mark.asyncio
async def test_the_beat_bounds_how_OFTEN_the_poll_asks(monkeypatch) -> None:
    """The beat, measured as a RATE now that the stall guard is gone.

    It used to be measured as a duration, with the stall guard providing the exit. Without a pause
    the loop asks the resolver as fast as the event loop allows for the whole `settle_cap_ms`, which
    is thousands of ladder walks on a page that is not going to answer -- and a ladder walk is not
    free (42 of them priced at 10.1 s in this same helper). With the beat the count is bounded by
    the budget divided by the beat.

    `_SleepingPage` and not `_ScriptedPage`: the beat's whole subject is that it waits, and the stub
    that makes the counter cells instant would remove it.
    """
    scripted = _ScriptedResolve([(False, False)])
    monkeypatch.setattr(flow_mod, "resolve", scripted, raising=True)
    monkeypatch.setattr(
        flow_mod, "settings", dataclasses.replace(settings, settle_cap_ms=400), raising=True)
    session = _ScriptedSession("quiet", ["already-quiet"])
    tr = _tr()
    out = await flow_mod._retry_if_unpainted(
        session, _SleepingPage(), TARGET, tr, None, sink={"saw_candidates": False})
    assert out is None and ":still-none:" in tr.meta["readiness_retry"], tr.meta

    # COUNT THE SETTLES, NOT THE RESOLVES. A page that never moves keeps the INNER loop spinning,
    # and that loop asks `await_settled` -- `resolve` is called exactly once whatever the beat does,
    # so a resolve count cannot see this and the first draft of this cell passed against the
    # mutation. The mutation is what said so.
    ceiling = 400 / settings.settle_poll_ms + 3       # budget / beat, plus slack for the last wait
    assert session.asked <= ceiling, (
        f"the poll asked the page {session.asked} times inside a 400 ms budget, above the "
        f"~{ceiling:.0f} its own beat allows -- without a pause between looks it spins as fast as "
        f"the event loop permits, and every ask walks back into the settle machinery")
    assert scripted.calls == 1, (
        f"the outer loop ran {scripted.calls} times on a page that never moved; it should re-resolve "
        f"only when the page CHANGED")


@pytest.mark.asyncio
async def test_nothing_in_flight_at_entry_gives_up_after_the_FIRST_look(monkeypatch) -> None:
    """THE COST FIX (R4.146), and its placement is the whole of it.

    Measured: `drift_bench`'s 36 stalling rows read **0 outstanding at entry and never rise**,
    costing 23.4 s between them, while Odoo's list -> form transition reads **1-2 from entry** in 3
    of 3 reps. So an entry reading of zero means the browser was not mid-fetch and no further stage
    is coming -- the poll can stop, and those 23.4 s are saved.

    BUT THE FIRST LOOK STILL HAPPENS. R4.115's original mechanism is one settle plus one re-resolve,
    and it earns its keep on a page that painted DURING the settle -- which has nothing to do with
    the network. This cell asserts the resolve was CALLED, because a fix that skipped it would pass
    every cost assertion while quietly reverting the mechanism it sits inside.
    """
    scripted = _ScriptedResolve([(False, False)])
    monkeypatch.setattr(flow_mod, "resolve", scripted, raising=True)

    class _IdlePage(_ScriptedPage):
        _ultracua_inflight = 0            # nothing was outstanding when the poll began

    tr = _tr()
    out = await flow_mod._retry_if_unpainted(
        _ScriptedSession("quiet", ["already-quiet"]), _IdlePage(), TARGET, tr, None,
        sink={"saw_candidates": False})
    assert out is None
    assert ":no-inflight:" in tr.meta["readiness_retry"], (
        f"a page with nothing outstanding at entry was polled anyway: "
        f"{tr.meta['readiness_retry']!r} -- that is the 23.4 s drift_bench pays for nothing")
    assert scripted.calls == 1, (
        f"the first look was skipped ({scripted.calls} resolve calls) -- R4.115's settle-and-retry "
        f"is about a page that painted during the SETTLE and must survive this")


@pytest.mark.asyncio
async def test_something_in_flight_at_entry_is_what_LETS_it_poll(monkeypatch) -> None:
    """The contrast, on an otherwise identical script.

    Without this the cell above is satisfied by a poll that never loops at all -- and the two
    differ in exactly one attribute, which is the discriminator the finding rests on.
    """
    answers = [(False, False), (False, False), (True, False)]

    idle, busy = _ScriptedResolve(answers), _ScriptedResolve(answers)
    class _IdlePage(_ScriptedPage):
        _ultracua_inflight = 0

    monkeypatch.setattr(flow_mod, "resolve", idle, raising=True)
    tr_idle = _tr()
    out_idle = await flow_mod._retry_if_unpainted(
        _ScriptedSession("quiet", ["quiet"]), _IdlePage(), TARGET, tr_idle, None,
        sink={"saw_candidates": False})

    monkeypatch.setattr(flow_mod, "resolve", busy, raising=True)
    tr_busy = _tr()
    out_busy = await flow_mod._retry_if_unpainted(
        _ScriptedSession("quiet", ["quiet"]), _ScriptedPage(), TARGET, tr_busy, None,
        sink={"saw_candidates": False})

    assert out_idle is None and idle.calls == 1, tr_idle.meta
    assert out_busy is not None and busy.calls == 3, tr_busy.meta
    assert ":bound:" in tr_busy.meta["readiness_retry"], (
        "a page that WAS mid-fetch stopped polling -- the entry check must gate the loop, not "
        "replace it")


