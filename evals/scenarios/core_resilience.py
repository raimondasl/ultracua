"""Core evals: shipped drift-resilience + fail-loud reads (key-less, local fixtures).

The invariant under test, from both directions:
- SURVIVE the drift that doesn't matter: cosmetic page churn (a new banner, reordered/renamed
  attributes) must not break a 0-LLM replay — the ranked locators re-resolve by identity.
- FAIL LOUD on the drift that does: an ambiguous target, a broken pinned read, a truncated
  extraction, a changed data shape — each must surface as a loud failure, never as silently
  wrong data (the "never silently wrong" inviolable).

All scenarios are key-less: local Fixture pages + scripted providers + MockClient-backed
extraction routers (the tests/test_flows.py convention) + real headless Chromium.
"""

from __future__ import annotations

from evals.core import Ctx, expect, fail, ok, scenario
from evals.fixtures import Fixture, page


class _ClickTheLink:
    """Scripted key-less 'agent': click the first link, then declare done (the tests/ convention)."""

    def __init__(self) -> None:
        self._clicked = False

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        if not self._clicked:
            for el in obs.elements:
                if el.role == "link":
                    self._clicked = True
                    return Action(action="click", intent="open the answer page", ref=el.ref), None
        return Action(action="done", intent="done"), None


def _spy_router(*datas):
    """A Router whose successive extraction calls return {found, data} — plus the MockClient itself,
    so a scenario can assert exactly how many LLM extraction calls were (not) made."""
    from ultracua.llm.base import Router, Tier
    from ultracua.llm.mock import MockClient

    mc = MockClient(actions=[{"found": True, "data": d} for d in datas], tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m")), mc


@scenario(
    id="core.resilience.cosmetic_drift",
    title="cosmetic drift (new banner + reordered attributes) survives a 0-LLM replay",
    group="core", tags=("drift", "replay"),
)
async def cosmetic_drift(ctx: Ctx):
    from ultracua.flow import run_cached

    checks = []
    fx = Fixture({
        # learn-time page: one link inside <main>
        "/": page('<main><h1>Daily</h1>'
                  '<a href="/answer" class="report-link" data-v="1">open the daily report</a></main>'),
        "/answer": page('<h1>Report</h1><p id="total">total: 42</p>'),
    })
    with fx.serve() as base:
        cache = ctx.cache()
        goal = "open the daily report page"
        learned = await run_cached(base + "/", goal, _ClickTheLink(), cache, mode="learn", headless=True)
        checks.append(expect(learned.success, "learn succeeds on the fixture", f"note={learned.note!r}"))
        # DRIFT the page cosmetically: an unrelated cookie banner appears, the link's attributes are
        # reordered and its class renamed, extra whitespace churn. The link's IDENTITY (role+name) is
        # unchanged, so the ranked locators must re-resolve it without an LLM.
        fx.pages["/"] = page(
            '<div class="banner" role="note">We updated our cookie policy — <b>dismiss</b></div>'
            '<main><h1>Daily</h1>\n'
            '  <a data-v="2" class="fresh-look report-link" href="/answer">open the daily report</a>\n'
            '</main>')
        replayed = await run_cached(base + "/", goal, None, cache, mode="replay", headless=True)
        # The core promise: cosmetic churn is invisible to a deterministic replay.
        checks.append(expect(replayed.success, "replay survives the cosmetic drift",
                             f"note={replayed.note!r}"))
        checks.append(expect(replayed.llm_calls == 0, "drifted replay still makes ZERO LLM calls",
                             f"llm_calls={replayed.llm_calls}"))
        checks.append(expect(replayed.mode == "replay", "pure replay — no heal/replan was needed",
                             f"mode={replayed.mode}"))
        checks.append(expect(not fx.writes, "a read flow sent no write to the server",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
    return checks


@scenario(
    id="core.resilience.ambiguity_fails_loud",
    title="a target whose text now matches TWO elements fails loud — never a silent first-match",
    group="core", tags=("drift", "fail-loud"),
)
async def ambiguity_fails_loud(ctx: Ctx):
    from ultracua.flow import run_cached

    checks = []
    fx = Fixture({
        "/": page('<a href="/answer">view the quarterly report</a>'),
        "/answer": page('<h1>Report</h1><p>total: 42</p>'),
        "/decoy": page('<h1>Wrong page</h1><p>stale draft: 0</p>'),
    })
    with fx.serve() as base:
        cache = ctx.cache()
        goal = "open the quarterly report page"
        learned = await run_cached(base + "/", goal, _ClickTheLink(), cache, mode="learn", headless=True)
        checks.append(expect(learned.success, "learn succeeds on the unambiguous fixture",
                             f"note={learned.note!r}"))
        # DRIFT into ambiguity: a decoy link with the IDENTICAL text appears FIRST in the DOM (so a
        # naive .first bind would click the wrong one). role+name, exact text, the tag-scoped
        # substring, and the positional css now all match count==2 — no unique signal remains.
        fx.pages["/"] = page(
            '<a href="/decoy">view the quarterly report</a>'
            '<a href="/answer">view the quarterly report</a>')
        seen = len(fx.gets)  # only count requests made by the replay below
        replayed = await run_cached(base + "/", goal, None, cache, mode="replay", headless=True)
        # The inviolable: an ambiguous bind must fail loud, never silently actuate a candidate.
        checks.append(expect(not replayed.success, "ambiguous replay is NOT reported as success"))
        checks.append(expect(replayed.llm_calls == 0, "the loud failure cost zero LLM calls",
                             f"llm_calls={replayed.llm_calls}"))
        # Neither candidate was clicked: had .first been silently taken, /decoy would have been fetched.
        touched = [p for p in fx.gets[seen:] if p in ("/decoy", "/answer")]
        checks.append(expect(not touched, "NEITHER candidate link was actuated (no wrong-element click)",
                             f"fetched during replay: {touched}"))
        # The failure is the resolve-ambiguity path (not some unrelated crash): the failing step's
        # trace carries the unresolved/ambiguous note.
        notes = [t.meta.get("note", "") for t in replayed.traces]
        checks.append(expect(any("ambiguous" in n for n in notes),
                             "failure is attributed to locator ambiguity (drift), not something else",
                             f"step notes={notes}"))
    return checks


@scenario(
    id="core.resilience.extract_truncation_flag",
    title="extraction over a >12k-char page reports truncated=True — a cut read is never silent (#78)",
    group="core", tags=("extraction", "fail-loud"),
)
async def extract_truncation_flag(ctx: Ctx):
    from ultracua.extract import extract
    from ultracua.llm.base import Router, Tier
    from ultracua.llm.mock import MockClient

    checks = []
    # The answer sits PAST the extractor's default 12k-char window, so the model can't see it: a
    # "not found" from this page is indeterminate, and only the truncated flag says so.
    big_page = ("lorem ipsum " * 1200) + "grand total: 4242"  # ~14.4k chars; sentinel past the cut

    # (1) the indeterminate not-found: the model (scripted) finds nothing on the visible portion.
    mc = MockClient(actions=[{"found": False, "data": None, "error": "not on the page"}],
                    tool_name="submit")
    ex = await extract(Router(fast=Tier(mc, "m"), strong=Tier(mc, "m")), "the grand total", big_page)
    checks.append(expect(ex.truncated is True, "a >12k-char page sets Extraction.truncated",
                         f"truncated={ex.truncated}"))
    checks.append(expect(not ex.found, "the scripted not-found passes through (flag rides alongside)"))
    # The cut happened BEFORE the LLM saw the page (not merely flagged after the fact): the sentinel
    # value past the window must be absent from the prompt the extractor actually sent.
    prompt = mc.last_request.messages[0].content[0].text if mc.last_request else ""
    checks.append(expect("4242" not in prompt, "the value past the cut was invisible to the model",
                         "sentinel leaked into the extractor prompt"))

    # (2) a page inside the window is a clean read — no false truncation alarms.
    router2, _ = _spy_router(42)
    short = await extract(router2, "the total", "total: 42")
    checks.append(expect(short.truncated is False, "a short page is NOT flagged as truncated",
                         f"truncated={short.truncated}"))

    # (3) a FOUND result on a truncated page keeps both the data and the marker — a possibly-short
    # list is returned but never as a clean read (the caller can flag incompleteness).
    router3, _ = _spy_router(["row1", "row2"])
    found = await extract(router3, "all the rows", big_page)
    checks.append(expect(found.found and found.data == ["row1", "row2"] and found.truncated is True,
                         "a found-but-truncated read carries data AND the truncated marker",
                         f"found={found.found} data={found.data!r} truncated={found.truncated}"))
    return checks


@scenario(
    id="core.resilience.pinned_zero_llm_read",
    title="a pinned read replays fresh data with ZERO extractor calls and fails loud when the pin breaks",
    group="core", tags=("pin", "read", "fail-loud"),
)
async def pinned_zero_llm_read(ctx: Ctx):
    from ultracua.cache import flow_key
    from ultracua.flows import FlowReplayError, FlowSpec, _load_meta, learn, replay

    checks = []
    fx = Fixture({
        "/": page('<a href="/v">see the value</a>'),
        "/v": page('<h1>Value</h1><p id="ans">42</p>'),  # a stable id -> pinnable scalar read
    })
    with fx.serve() as base:
        cache = ctx.cache()
        spec = FlowSpec(name="pinread", start_url=base + "/", goal="open the value page",
                        extract="the value number", pin_read=True, headless=True)
        router, _ = _spy_router(42)  # learn-time extraction is scripted (one MockClient call)
        res = await learn(spec, provider=_ClickTheLink(), router=router, cache=cache)
        checks.append(expect(res.cached and res.pinned, "learn pins the deterministic 0-LLM read",
                             f"cached={res.cached} pinned={res.pinned} note={res.note!r}"))
        # The pin is persisted in the trust sidecar (the building block replay reads back).
        meta = _load_meta(cache, flow_key(spec.goal, spec.start_url, spec.scope))
        checks.append(expect(meta.read_pin is not None, "FlowMeta.read_pin is persisted alongside the flow"))

        # The live value changes; replay must read TODAY's value via the pin, with no extractor call.
        fx.pages["/v"] = page('<h1>Value</h1><p id="ans">99</p>')
        router2, mc2 = _spy_router()  # deliberately scripted with NOTHING — any call would be visible
        try:
            data = await replay(spec, router=router2, cache=cache)
            checks.append(expect(data == 99, "replay returns the FRESH live value via the pin",
                                 f"data={data!r}"))
        except Exception as exc:  # noqa: BLE001 — a shipped pinned replay must not raise here
            checks.append(fail("replay returns the FRESH live value via the pin",
                               f"{type(exc).__name__}: {exc}"))
        checks.append(expect(mc2.calls == 0, "the pinned replay made ZERO extractor (LLM) calls",
                             f"extractor calls={mc2.calls}"))

        # The pinned element disappears: the read must fail LOUD (FlowReplayError), and must NOT
        # silently fall back to the LLM extractor — that could fabricate a value the pin can't vouch for.
        fx.pages["/v"] = page('<h1>Value</h1><p>no data today</p>')
        router3, mc3 = _spy_router()
        try:
            got = await replay(spec, router=router3, cache=cache)
            checks.append(fail("a broken pin fails loud (FlowReplayError)",
                               f"replay returned {got!r} instead of raising"))
        except FlowReplayError:
            checks.append(ok("a broken pin fails loud (FlowReplayError)"))
        except Exception as exc:  # noqa: BLE001 — wrong exception type is still a shipped-behavior bug
            checks.append(fail("a broken pin fails loud (FlowReplayError)",
                               f"raised {type(exc).__name__} instead: {exc}"))
        checks.append(expect(mc3.calls == 0, "a broken pin never silently falls back to LLM extraction",
                             f"extractor calls={mc3.calls}"))
    return checks


@scenario(
    id="core.resilience.shape_drift",
    title="a replay whose extracted data SHAPE differs from the learned shape is flagged as drift",
    group="core", tags=("shape", "drift", "fail-loud"),
)
async def shape_drift(ctx: Ctx):
    from ultracua.flows import FlowReplayError, FlowSpec, _shape_matches, _shape_of, health, learn, replay

    checks = []
    # The building blocks first (pure functions, no browser): the shape signature must flag a
    # STRUCTURE change while tolerating legitimate day-to-day variation.
    checks.append(expect(not _shape_matches(_shape_of(42), _shape_of(["a", "b"])),
                         "number -> list is a shape change (flagged)"))
    checks.append(expect(_shape_matches(_shape_of([1, 2]), _shape_of([5, 6, 7])),
                         "same-item-type arrays of different lengths match (counts vary, structure doesn't)"))
    checks.append(expect(not _shape_matches(_shape_of({"a": 1, "b": 2}), _shape_of({"a": 9, "c": 0})),
                         "an object whose KEYS changed is a shape change (flagged)"))

    # Now the shipped public path: replay(check_shape=True) over a real learned flow.
    fx = Fixture({
        "/": page('<a href="/answer">see the answer</a>'),
        "/answer": page('<h1>Answer</h1><p>The answer is 42.</p>'),
    })
    with fx.serve() as base:
        cache = ctx.cache()
        spec = FlowSpec(name="shape", start_url=base + "/", goal="open the answer page",
                        extract="the answer number", headless=True)
        router, _ = _spy_router(42)  # learned shape: number
        await learn(spec, provider=_ClickTheLink(), router=router, cache=cache)

        # Same structure, different value: replay must return the fresh value (lenient on values).
        router_ok, _ = _spy_router(99)
        try:
            data = await replay(spec, check_shape=True, router=router_ok, cache=cache)
            checks.append(expect(data == 99, "a same-shape value change replays fine (values may vary)",
                                 f"data={data!r}"))
        except Exception as exc:  # noqa: BLE001 — a shipped same-shape replay must not raise
            checks.append(fail("a same-shape value change replays fine (values may vary)",
                               f"{type(exc).__name__}: {exc}"))

        # Structure change (number -> list): replay must fail LOUD, never hand back drifted data.
        router_bad, _ = _spy_router(["a", "b"])
        try:
            got = await replay(spec, check_shape=True, router=router_bad, cache=cache)
            checks.append(fail("a shape change on replay raises FlowReplayError",
                               f"replay returned {got!r} instead of raising"))
        except FlowReplayError as exc:
            checks.append(expect("shape" in str(exc), "a shape change on replay raises FlowReplayError",
                                 f"error does not name the shape drift: {exc}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(fail("a shape change on replay raises FlowReplayError",
                               f"raised {type(exc).__name__} instead: {exc}"))
        # Partial credit on the trust loop: the failed run landed in the flow's health record.
        h = health(spec, cache=cache)
        checks.append(expect(h.status == "failing" and h.consecutive_failures == 1,
                             "the shape-drift failure is recorded in flow health",
                             f"status={h.status} consecutive_failures={h.consecutive_failures}"))
    return checks
