"""LLM-tier evals: the scenarios that inherently need a REAL model (--include-llm / --include-live).

The key-less suite proves the machinery; this module measures the paid half of the promise:
- a real LLM AUTHORS a flow on a local fixture that then replays at 0 LLM calls,
- real structured extraction honors an `extract_schema` and returns exactly the page's data,
- the recorder's opt-in intent captioner produces real (non-placeholder) step intents,
- best-of-N discovery (samples=2) on a decoy fixture still ends verified + 0-LLM replayable,
- (live, opt-in) the proven HN demo: learn once, then replay 0-LLM onto an item?id= page.

Every scenario gates itself on a configured provider key and reports `skip` when there is none,
so the module imports and runs cleanly key-less (requires="llm"/"live" is excluded from the
default run anyway — the gate is belt-and-suspenders for a keyed machine running --include-llm
with a broken config). Costs are declared as ceilings per the measured anchors in evals/README.md
(scripted learn ~= $0.09, full learn+replay ~= $0.27, one extraction/caption call ~= $0.02).
"""

from __future__ import annotations

import json

from evals.core import Ctx, expect, fail, scenario, skip
from evals.fixtures import Fixture, page


def _llm_ready() -> bool:
    """True iff the configured provider has a usable key (mirrors the `flow record` CLI's gate).
    Defensive: any import/attr surprise counts as "not configured" — a paid scenario must convert
    a missing/misconfigured key into `skip`, never crash and never attempt a doomed LLM call."""
    try:
        from ultracua.config import settings
        from ultracua.flows import _llm_configured

        return bool(_llm_configured(settings.provider))
    except Exception:  # noqa: BLE001 — no key info -> treat as not configured
        import os

        return bool(os.getenv("ANTHROPIC_API_KEY"))


def _no_key() -> list:
    return [skip("requires a real LLM — skipped",
                 "no provider API key configured (e.g. ANTHROPIC_API_KEY)")]


class _ClickThenDone:
    """Scripted navigation (0 LLM): click the first link, then declare done. Used where a scenario
    pays ONLY for the extraction/caption call — isolating that capability from authoring quality."""

    def __init__(self) -> None:
        self._clicked = False

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        if not self._clicked:
            for el in obs.elements:
                if el.role == "link":
                    self._clicked = True
                    return Action(action="click", intent="open the linked page", ref=el.ref), None
        return Action(action="done", intent="done"), None


# --- 1. real LLM learn on a 2-page fixture -> cached, then 0-LLM replay ------------------------
def _report_pages() -> Fixture:
    return Fixture({
        "/": page('<p>Welcome.</p><a href="/report">open the daily report</a>'),
        "/report": page('<h1>Daily report</h1><p id="total">total: 42</p>'),
    })


@scenario(
    id="llm.core.learn.fixture_learn_replay",
    title="a REAL LLM learns a 2-page fixture flow once; the replay is cached and 0-LLM",
    group="llm", requires="llm", est_llm_calls=6, est_cost_usd=0.30, tags=("learn", "replay", "read"),
)
async def llm_learn_fixture(ctx: Ctx):
    if not _llm_ready():
        return _no_key()
    from ultracua import flows
    from ultracua.flow import run_cached

    checks = []
    fx = _report_pages()
    with fx.serve() as base:
        cache = ctx.cache()
        spec = flows.FlowSpec(
            name="ev-llm-learn", start_url=base + "/",
            goal="open the daily report page and read the total",
            extract="the total number shown on the report page",
            extract_schema={"type": "object", "properties": {"total": {"type": "integer"}},
                            "required": ["total"]},
            max_steps=6, headless=True,
        )
        try:
            res = await flows.learn(spec, router=ctx.router(), cache=cache)
        except Exception as exc:  # noqa: BLE001 — a paid-path crash is a regression row, not an eval error
            return checks + [fail("LLM learn ran without crashing", f"{type(exc).__name__}: {exc}")]
        # learn() verify-by-replays the authored flow before caching — `cached` means the LLM's
        # navigation reproduced 0-LLM on a fresh session (the core promise of the paid learn).
        checks.append(expect(res.cached, "LLM-authored flow survived verify-by-replay and was cached",
                             f"note={res.note!r}"))
        checks.append(expect(res.found, "extraction found the answer during learn", f"note={res.note!r}"))
        checks.append(expect("42" in json.dumps(res.data), "learned data carries the page's total (42)",
                             f"data={res.data!r}"))
        if not res.cached:
            checks.append(skip("replay checks skipped", "learn did not cache a flow"))
            return checks
        # The $ bought a DETERMINISTIC asset: navigation must now replay at ZERO LLM calls.
        rep = await run_cached(spec.start_url, spec.goal, None, cache, mode="replay",
                               headless=True, scope=spec.scope)
        checks.append(expect(rep.success and rep.llm_calls == 0,
                             "cached navigation replays with ZERO LLM calls",
                             f"success={rep.success} llm_calls={rep.llm_calls} note={rep.note!r}"))
        checks.append(expect(not fx.writes, "a read flow sent no write to the server",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
    return checks


# --- 2. real structured extraction against an extract_schema -----------------------------------
def _inventory_pages() -> Fixture:
    return Fixture({
        "/": page('<a href="/inventory">open the inventory</a>'),
        "/inventory": page(
            "<h1>Inventory</h1>"
            "<table><tr><th>item</th><th>count</th></tr>"
            "<tr><td>widget</td><td>3</td></tr>"
            "<tr><td>wombat</td><td>7</td></tr>"
            "<tr><td>grommet</td><td>12</td></tr></table>"
        ),
    })


@scenario(
    id="llm.core.extract.table_schema",
    title="real structured extraction: FlowSpec.extract on a table returns data matching extract_schema",
    group="llm", requires="llm", est_llm_calls=2, est_cost_usd=0.05, tags=("extract", "read"),
)
async def llm_extract_table(ctx: Ctx):
    if not _llm_ready():
        return _no_key()
    from ultracua import flows

    checks = []
    fx = _inventory_pages()
    with fx.serve() as base:
        cache = ctx.cache()
        spec = flows.FlowSpec(
            name="ev-llm-extract", start_url=base + "/",
            goal="open the inventory and list every row of the table",
            extract="every row of the inventory table, as objects with the item name and its count",
            extract_schema={"type": "array", "items": {
                "type": "object",
                "properties": {"item": {"type": "string"}, "count": {"type": "integer"}},
                "required": ["item", "count"],
            }},
            max_steps=4, headless=True,
        )
        try:
            # Scripted provider (0-LLM navigation) + REAL router: the only paid call is the
            # extraction itself, so this measures extraction quality in isolation.
            res = await flows.learn(spec, provider=_ClickThenDone(), router=ctx.router(), cache=cache)
        except Exception as exc:  # noqa: BLE001
            return checks + [fail("LLM extraction ran without crashing", f"{type(exc).__name__}: {exc}")]
        checks.append(expect(res.found, "the extractor found the table data", f"note={res.note!r}"))
        data = res.data
        rows_ok = isinstance(data, list) and len(data) == 3
        checks.append(expect(rows_ok, "extraction returns one entry per table row (3)", f"data={data!r}"))
        if not rows_ok:
            checks.append(skip("schema/value checks skipped", "no 3-row list came back"))
            return checks
        keyed = all(isinstance(r, dict) and {"item", "count"} <= set(r) for r in data)
        checks.append(expect(keyed, "every row carries the extract_schema keys (item, count)",
                             f"data={data!r}"))
        try:  # exact values — "never silently wrong data" applies to the paid read too
            got = {str(r["item"]).strip().lower(): int(r["count"]) for r in data} if keyed else {}
        except Exception:  # noqa: BLE001 — a non-numeric count is just a value mismatch below
            got = {}
        checks.append(expect(got == {"widget": 3, "wombat": 7, "grommet": 12},
                             "extracted values match the table exactly", f"data={data!r}"))
    return checks


# --- 3. recorder intent caption (one off-replay-path LLM call) ---------------------------------
def _caption_pages() -> Fixture:
    return Fixture({
        "/": page('<a href="/reports">Open reports</a>'),
        "/reports": page('<h2>Reports</h2><a href="/done">Continue</a>'),
        "/done": page("<h1>done</h1>"),
    })


async def _caption_demo(pg) -> None:
    await pg.get_by_role("link", name="Open reports").click()
    await pg.get_by_role("link", name="Continue").click()


def _placeholder_intent(step) -> str:
    """Mirror recorder._step_from_event's placeholder ("<action> <accessible name>") so a captioned
    intent is detectable without importing recorder internals."""
    name = (step.locator.name or step.locator.tag) if step.locator else step.action
    return f"{step.action} {name}".strip()


@scenario(
    id="llm.core.caption.recorder_intents",
    title="record a demo with caption=flows.caption_for(): captured steps carry non-placeholder intents",
    group="llm", requires="llm", est_llm_calls=2, est_cost_usd=0.05, tags=("recorder", "caption"),
)
async def llm_caption_record(ctx: Ctx):
    if not _llm_ready():
        return _no_key()
    from ultracua import flows

    checks = []
    try:
        captioner = flows.caption_for()  # the `flow record` CLI's construction path
    except Exception as exc:  # noqa: BLE001
        return [fail("caption_for() built a captioner", f"{type(exc).__name__}: {exc}")]
    # With a key configured, caption_for() must return a callable (None is its key-less answer).
    checks.append(expect(captioner is not None, "caption_for() builds a captioner when a key is set"))
    if captioner is None:
        return checks
    fx = _caption_pages()
    with fx.serve() as base:
        cache = ctx.cache()
        spec = flows.FlowSpec(name="ev-llm-caption", start_url=base + "/",
                              goal="open the reports section and continue to the done page")
        try:
            rec = await flows.record(spec, demo=_caption_demo, headless=True, cache=cache,
                                     caption=captioner)
        except Exception as exc:  # noqa: BLE001
            return checks + [fail("record with caption ran without crashing",
                                  f"{type(exc).__name__}: {exc}")]
        # The caption call is OFF the replay path — the recorded read flow must still verify 0-LLM.
        checks.append(expect(rec.cached and rec.reproduced,
                             "the captioned demo still verified by 0-LLM replay and was cached",
                             f"note={rec.note!r}"))
        checks.append(expect(len(rec.steps) == 2 and all((s.intent or "").strip() for s in rec.steps),
                             "both captured steps carry an intent",
                             f"intents={[s.intent for s in rec.steps]}"))
        # The paid call's whole job: replace "click Open reports"-style placeholders with a concise
        # goal-grounded label. caption is best-effort by design, so demand at least ONE real caption.
        captioned = [s for s in rec.steps if (s.intent or "").strip() and s.intent != _placeholder_intent(s)]
        checks.append(expect(bool(captioned),
                             "at least one intent is LLM-captioned (differs from the placeholder)",
                             f"intents={[s.intent for s in rec.steps]}"))
    return checks


# --- 4. best-of-N discovery on a decoy fixture --------------------------------------------------
def _decoy_pages() -> Fixture:
    # A naive first attempt can fail here: the decoy "daily summary" link leads to a page WITHOUT
    # the total, so its extraction comes back not-found and the attempt is left unverified —
    # best-of-N (samples=2) must resample instead of caching the dud.
    return Fixture({
        "/": page('<p>Choose a page:</p>'
                  '<a href="/summary">open the daily summary</a> '
                  '<a href="/report">open the daily report</a>'),
        "/summary": page("<h1>Daily summary</h1><p>Numbers moved to the report page.</p>"),
        "/report": page('<h1>Daily report</h1><p id="total">total: 42</p>'),
    })


@scenario(
    id="llm.core.discovery.best_of_two",
    title="best-of-N discovery (samples=2) on a decoy-link fixture ends verified, cached, 0-LLM replayable",
    group="llm", requires="llm", est_llm_calls=12, est_cost_usd=0.60, tags=("discovery", "best-of-n"),
)
async def llm_best_of_two(ctx: Ctx):
    if not _llm_ready():
        return _no_key()
    import inspect

    from ultracua import flows
    from ultracua.flow import run_cached

    checks = []
    fx = _decoy_pages()
    with fx.serve() as base:
        cache = ctx.cache()
        spec = flows.FlowSpec(
            name="ev-llm-decoy", start_url=base + "/",
            goal="find the total for today and read it",
            extract="the total number",
            extract_schema={"type": "object", "properties": {"total": {"type": "integer"}},
                            "required": ["total"]},
            max_steps=6, headless=True,
        )
        try:
            # samples=2: keep the FIRST attempt the verify-by-replay + found oracle confirms
            # (flows.learn semantics) — the decoy page can waste attempt 1, never the cache.
            res = await flows.learn(spec, samples=2, cache=cache)
        except Exception as exc:  # noqa: BLE001
            return checks + [fail("best-of-N learn ran without crashing", f"{type(exc).__name__}: {exc}")]
        checks.append(expect(res.cached, "discovery cached a verified flow within 2 samples",
                             f"note={res.note!r}"))
        checks.append(expect(res.found, "only a VERIFIED attempt was kept (extraction found the data)",
                             f"note={res.note!r}"))
        checks.append(expect("42" in json.dumps(res.data), "the kept flow reads the REAL total (42), not the decoy",
                             f"data={res.data!r}"))
        if res.cached:
            rep = await run_cached(spec.start_url, spec.goal, None, cache, mode="replay",
                                   headless=True, scope=spec.scope)
            checks.append(expect(rep.success and rep.llm_calls == 0,
                                 "the discovered flow replays with ZERO LLM calls",
                                 f"success={rep.success} llm_calls={rep.llm_calls} note={rep.note!r}"))
        else:
            checks.append(skip("replay check skipped", "discovery cached nothing"))
    # ASPIRATIONAL: the engine has reflexion (run_cached(reflect=True) — a failed attempt teaches the
    # next one) but the product-level flows.learn does not expose it yet. Signature probe only — an
    # actual call would spend real $ the estimate doesn't cover.
    checks.append(expect("reflect" in inspect.signature(flows.learn).parameters,
                         "flows.learn exposes reflexion (reflect=) for failure-informed resampling",
                         "run_cached has reflect=; flows.learn does not surface it", aspirational=True))
    return checks


# --- 5. LIVE (opt-in): the proven HN demo flow ---------------------------------------------------
@scenario(
    id="llm.core.live.hn_top_story",
    title="LIVE: learn 'open the top story discussion' on news.ycombinator.com, replay 0-LLM onto item?id=",
    group="llm", requires="live", est_llm_calls=6, est_cost_usd=0.35, tags=("live", "replay", "read"),
    notes="politeness: read-only, ~2 page loads per pass (front page + item page); no verify-replay pass",
)
async def live_hn_top_story(ctx: Ctx):
    if not _llm_ready():
        return _no_key()  # a live learn still needs the LLM to author the flow
    from ultracua.config import settings
    from ultracua.flow import run_cached
    from ultracua.providers import get_provider

    checks = []
    cache = ctx.cache()
    url = "https://news.ycombinator.com"
    goal = "open the comments/discussion page of the current top story on the front page"
    seen: dict = {}

    async def _finalize(session):  # capture where the flow actually ended (the item?id= oracle)
        seen["url"] = session.page.url
        return {"solved": "item?id=" in session.page.url}

    try:
        # verify_replay stays off (default) for politeness — the explicit replay below IS the check,
        # so the site sees two passes total instead of three.
        learned = await run_cached(url, goal, get_provider(settings.provider), cache, mode="learn",
                                   max_steps=8, headless=True, finalize=_finalize)
    except Exception as exc:  # noqa: BLE001
        return checks + [fail("live learn ran without crashing", f"{type(exc).__name__}: {exc}")]
    checks.append(expect(learned.success, "the agent reaches the top story's discussion page",
                         f"note={learned.note!r}"))
    checks.append(expect("item?id=" in seen.get("url", ""), "learn ended on an item?id= page",
                         f"url={seen.get('url')!r}"))
    if not learned.success:
        checks.append(skip("replay checks skipped", "live learn failed"))
        return checks
    seen.clear()
    rep = await run_cached(url, goal, None, cache, mode="replay", headless=True, finalize=_finalize)
    checks.append(expect(rep.success and rep.llm_calls == 0,
                         "the live flow replays with ZERO LLM calls",
                         f"success={rep.success} llm_calls={rep.llm_calls} note={rep.note!r}"))
    checks.append(expect("item?id=" in seen.get("url", ""),
                         "replay lands on an item?id= discussion page", f"url={seen.get('url')!r}"))
    return checks
