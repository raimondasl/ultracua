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
  // Three bursts before the target, each separated by a QUIET gap longer than the settle's quiet
  // window -- so `await_settled` returns between them, repeatedly, on a page still to paint.
  for (const t of [60, 200, 340]) {
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


@pytest.mark.asyncio
async def test_the_target_that_appears_after_several_quiet_gaps_IS_found() -> None:
    """THE DEFECT: one look lands in a gap. The measured shape needed five."""
    async with BrowserSession(headless=True) as sess:
        await _staged_page(sess, appears_after_ms=700)
        sink: dict = {}
        first = await resolve(sess.page, TARGET, unique=True, sink=sink)
        assert first is None and sink.get("saw_candidates") is False, (
            "the fixture is inert: the target must be ABSENT on the first look, or this cell "
            "passes against the single-look code it exists to refuse")
        tr = _tr()
        got = await _retry_if_unpainted(sess, sess.page, TARGET, tr, None, sink=sink)
        assert got is not None, (
            f"the target never bound though it appeared at 700 ms, inside the "
            f"{settings.settle_cap_ms} ms budget: {tr.meta.get('readiness_retry')!r}")
        assert ":bound:" in tr.meta["readiness_retry"], tr.meta


@pytest.mark.asyncio
async def test_it_really_took_MORE_THAN_ONE_look() -> None:
    """Without this the cell above is satisfied by a single look that got lucky on timing.

    The recorded verdict carries the count, so the property is checkable rather than inferred.
    """
    async with BrowserSession(headless=True) as sess:
        await _staged_page(sess, appears_after_ms=700)
        sink: dict = {}
        await resolve(sess.page, TARGET, unique=True, sink=sink)
        tr = _tr()
        await _retry_if_unpainted(sess, sess.page, TARGET, tr, None, sink=sink)
        verdict = tr.meta["readiness_retry"]
        looks = int(verdict.rsplit(":", 1)[1])
        assert looks >= 2, (
            f"bound on the first look, so this fixture cannot refute the single-look code: {verdict}")


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
async def test_a_page_that_STOPS_is_given_up_on_long_before_the_budget() -> None:
    """The cost direction, and `drift_bench` priced it before this cell existed.

    A first draft polled to the cap on ANY absent target: **36 genuinely-drifted rows paying
    2251 ms each, 81 s**, taking the bench from 184 s to 259 s against a 220 s budget. Those rows
    report `already-quiet` on every look -- the page is not mid-render, the element is gone. A page
    waiting on the NETWORK looks different, because each burst makes the next settle wait rather
    than answer instantly, so consecutive already-quiets are the discriminator.

    The fixture mutates ONCE (so the first settle genuinely waits and the loop is entered) and then
    stops forever, which is exactly the drifted-row shape.
    """
    async with BrowserSession(headless=True) as sess:
        await sess.page.set_content("<div id='host'><button>Other</button></div>")
        await sess.page.evaluate(
            """() => setTimeout(function () {
                 const s = document.createElement('span'); s.textContent = 'once';
                 document.getElementById('host').appendChild(s);
               }, 60)""")
        sink: dict = {}
        first = await resolve(sess.page, TARGET, unique=True, sink=sink)
        assert first is None and sink.get("saw_candidates") is False
        tr = _tr()
        t0 = time.monotonic()
        got = await _retry_if_unpainted(sess, sess.page, TARGET, tr, None, sink=sink)
        elapsed = (time.monotonic() - t0) * 1000
        assert got is None
        assert "stalled" in tr.meta["readiness_retry"], (
            f"a page that stopped mutating was polled to the budget instead of given up on: "
            f"{tr.meta['readiness_retry']!r}")
        assert elapsed < settings.settle_cap_ms, (
            f"the stall guard did not save anything: {elapsed:.0f} ms against a "
            f"{settings.settle_cap_ms} ms cap")


def test_the_stall_limit_clears_the_measured_need_with_margin() -> None:
    """A limit BELOW the measured need silently reverts the fix, and nothing about the poll's own
    success would say so -- the Odoo shape needed THREE consecutive already-quiets before its next
    render stage landed."""
    assert settings.settle_stall_looks >= 6, (
        "the stall limit must clear the 3 consecutive already-quiets the Odoo render needed, with "
        "margin -- at or below it, a network-gated render is given up on mid-flight")
    assert settings.settle_stall_looks * settings.settle_poll_ms < settings.settle_cap_ms, (
        "a stall limit that cannot be reached inside the budget is inert, and the 81 s drift_bench "
        "regression comes straight back")


_QUIET_THEN_APPEAR_JS = """
(ms) => {
  // NO intervening mutation: the page is genuinely still, and then the target arrives. That is
  // what a network fetch landing looks like, and it is the only shape in which the BEAT is
  // observable -- with bursts, every settle waits and the stall counter keeps resetting anyway.
  setTimeout(function () {
    const b = document.createElement('button');
    b.textContent = 'Ready';
    document.getElementById('host').appendChild(b);
  }, ms);
}
"""

_QUIET_BURST_QUIET_JS = """
() => {
  const host = document.getElementById('host');
  // THE ONLY SHAPE THAT NEEDS THE RESET, and three drafts missed it. `stalled` increments ONLY on
  // an `already-quiet` look, so a page that bursts continuously never increments it and removing
  // the reset changes nothing. What is required is: a quiet look (stalled -> 1), then a BURST that
  // must clear it, then another quiet stretch before the target. Traced both arms before use --
  // real binds at ~953 ms, a non-resetting counter stalls at look 2.
  setTimeout(function () {
    const d = document.createElement('span'); d.textContent = 'stage'; host.appendChild(d);
  }, 350);
  setTimeout(function () {
    const b = document.createElement('button'); b.textContent = 'Ready'; host.appendChild(b);
  }, 700);
}
"""


@pytest.mark.asyncio
async def test_the_beat_gives_a_QUIET_page_time_for_its_next_stage_to_land() -> None:
    """Why the beat is load-bearing, once the stall guard exists.

    A mutation removing `wait_for_timeout` SURVIVED the first draft, because the stall guard exits
    after six looks either way and a look-count assertion cannot see the difference. What the beat
    actually buys is TIME: six already-quiet looks span ~320 ms with it and ~18 ms without, and a
    network fetch lands in that window. So the discriminating fixture is a page that is GENUINELY
    still and then produces the target -- no bursts, because a burst makes the settle wait and
    resets the stall counter, which is a different mechanism keeping the poll alive.
    """
    async with BrowserSession(headless=True) as sess:
        await sess.page.set_content("<div id='host'><button>Other</button></div>")
        await sess.page.evaluate(_QUIET_THEN_APPEAR_JS, 500)
        sink: dict = {}
        first = await resolve(sess.page, TARGET, unique=True, sink=sink)
        assert first is None and sink.get("saw_candidates") is False
        # THE ENTRY SETTLE RETURNS AT ~344 ms HERE, so a target before that binds on look 1 and this
        # cell exercises nothing -- which is exactly how the first two drafts passed while the
        # mutation survived. Traced rather than assumed.
        tr = _tr()
        got = await _retry_if_unpainted(sess, sess.page, TARGET, tr, None, sink=sink)
        assert got is not None, (
            f"the target arrived 500 ms into a quiet stretch and the poll had already given up: "
            f"{tr.meta['readiness_retry']!r}. Six looks with no beat span ~18 ms, which is less "
            f"time than any network round trip")


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
    async def wait_for_timeout(self, _ms):   # the beat, with no clock in it
        return None


@pytest.mark.asyncio
async def test_a_burst_RESETS_the_stall_counter_so_a_slow_render_keeps_its_budget(monkeypatch) -> None:
    """The reset, driven by a SCRIPT rather than by a real page's timing.

    FOUR BROWSER-TIMED DRAFTS OF THIS CELL FAILED TO SEE THE MUTATION, each for its own reason: the
    shipped limit of six needs seven looks, which outruns the budget; a limit of one makes the first
    quiet look terminal for both arms, so the reset never matters; a continuously bursting page
    never increments `stalled` at all, so removing the reset changes no value; and the shape that
    does work -- quiet, burst, quiet -- depends on where ~55 ms looks fall relative to a 350 ms
    burst, which moves run to run.

    The property is arithmetic: `stalled` counts CONSECUTIVE already-quiet looks, so a total of
    three with a burst in the middle must NOT stall at a limit of three, and the same three in a row
    must. A real page cannot pin that reliably; a scripted sequence pins it exactly. The browser
    cells above already cover the integration.
    """
    # A SMALL CAP TOO, because `_ScriptedPage.wait_for_timeout` does not sleep: with the real 2000 ms
    # the no-stall arm would spin the loop for two seconds of wall clock to reach its deadline.
    monkeypatch.setattr(
        flow_mod, "settings",
        dataclasses.replace(settings, settle_stall_looks=3, settle_cap_ms=200), raising=True)

    async def _never_binds(page, spec, *a, **kw):
        (kw.get("sink") or {})["saw_candidates"] = False
        return None

    monkeypatch.setattr(flow_mod, "resolve", _never_binds, raising=True)

    # The first verdict is the ENTRY settle and must be a real wait, or the `already-quiet` skip
    # above returns before the loop is entered. Then: two quiet looks, a real wait, two more --
    # three in total between any pair of waits, never three in a ROW.
    interrupted = _ScriptedSession("quiet", ["already-quiet", "already-quiet", "quiet"])
    tr = _tr()
    out = await flow_mod._retry_if_unpainted(
        interrupted, _ScriptedPage(), TARGET, tr, None, sink={"saw_candidates": False})
    assert out is None                       # nothing ever binds in this script, by construction
    assert "stalled" not in tr.meta["readiness_retry"], (
        f"a page that kept moving was declared stalled: {tr.meta['readiness_retry']!r} -- the "
        f"counter is not resetting when a settle actually waited")
    assert ":still-none:" in tr.meta["readiness_retry"], tr.meta["readiness_retry"]

    # And the control: three in a ROW must stall, or the guard is inert and drift_bench pays again.
    straight = _ScriptedSession("quiet", ["already-quiet"])
    tr2 = _tr()
    out2 = await flow_mod._retry_if_unpainted(
        straight, _ScriptedPage(), TARGET, tr2, None, sink={"saw_candidates": False})
    assert out2 is None
    assert ":stalled:" in tr2.meta["readiness_retry"], (
        f"three consecutive already-quiet looks did not stall: {tr2.meta['readiness_retry']!r}")


