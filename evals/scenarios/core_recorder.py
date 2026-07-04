"""Core evals: the shipped Phase-I RECORDER (record-by-demonstration), key-less.

`record_demo(url, demo, goal=..., cache=..., headless=True)` drives a SCRIPTED "human" (an async
fn given the Playwright page) so capture stays deterministic and $0. What this module measures:
  - capture FIDELITY: click / type / select land as steps with resilient role+name locators, and
    the recorded flow replays 0-LLM (the recorder feeds the SAME replay engine as learn).
  - same-origin NAVIGATION survival: the sessionStorage exfiltration keeps steps on BOTH sides of
    a page boundary — including the navigating click itself (the race the old timeout-flush lost).
  - fail-LOUD refusals (the write-safety inviolables): a CROSS-origin hop and an UNDECLARED write
    both refuse to cache — never a silently-truncated flow, never an ungated write.
  - aspirational horizons: record-time narration (H12) and <select> option DOMAINS on the
    captured step (H3) — expected `missing` today, never a crash.
"""

from __future__ import annotations

from evals.core import Ctx, expect, fail, missing, ok, probe, scenario
from evals.fixtures import Fixture, page


@scenario(
    id="core.recorder.capture_fidelity_replay",
    title="a demo's click + type + select are captured with locators and replay 0-LLM",
    group="core", tags=("recorder", "replay"),
)
async def capture_fidelity_replay(ctx: Ctx):
    from ultracua.flow import run_cached
    from ultracua.recorder import record_demo

    checks = []
    fx = Fixture({
        "/": page('<a href="/form">open the order form</a>'),
        "/form": page('<input id="n" aria-label="notes">'
                      '<select id="f" aria-label="fruit"><option value="">--</option>'
                      '<option value="apple">Apple</option><option value="banana">Banana</option>'
                      '</select>'),
    })

    async def _demo(pg) -> None:
        await pg.get_by_role("link", name="open the order form").click()  # navigates to /form
        await pg.wait_for_selector("#n")
        await pg.fill("#n", "blue widgets")
        await pg.locator("#n").blur()          # `change` fires on blur -> the recorder's `type` step
        await pg.select_option("#f", "banana")

    with fx.serve() as base:
        cache = ctx.cache()
        goal = "order banana with a note"
        flow, wrote, crossed, _ = await record_demo(base + "/", _demo, goal=goal,
                                                    cache=cache, headless=True)
        # Capture fidelity: the three interaction KINDS land as three steps in demo order.
        checks.append(expect([s.action for s in flow.steps] == ["click", "type", "select"],
                             "click + type + select captured in demo order",
                             f"actions={[s.action for s in flow.steps]}"))
        # Locator quality: each step carries a role+name locator (resilient), not a CSS path.
        names = [s.locator.name for s in flow.steps if s.locator]
        checks.append(expect(names == ["open the order form", "notes", "fruit"],
                             "each step carries the element's accessible role+name locator",
                             f"names={names}"))
        sel = next((s for s in flow.steps if s.action == "select"), None)
        checks.append(expect(sel is not None and sel.text == "banana",
                             "the chosen option VALUE is captured on the select step",
                             f"text={getattr(sel, 'text', None)!r}"))
        checks.append(expect(wrote is False and crossed is False,
                             "a read demo reports no write and no origin crossing",
                             f"performed_write={wrote} crossed_origin={crossed}"))

        async def _finalize(session):
            return {"notes": await session.page.input_value("#n"),
                    "fruit": await session.page.input_value("#f")}

        # The recorder's whole point: the captured flow is an ordinary CachedFlow the existing
        # replay engine runs deterministically at ZERO LLM calls.
        report = await run_cached(base + "/", goal, None, cache, mode="replay",
                                  headless=True, finalize=_finalize)
        checks.append(expect(report.success and report.llm_calls == 0,
                             "the recorded flow replays 0-LLM",
                             f"success={report.success} llm_calls={report.llm_calls} "
                             f"note={report.note!r}"))
        fin = (report.extra or {}).get("finalize") or {}
        checks.append(expect(fin.get("notes") == "blue widgets" and fin.get("fruit") == "banana",
                             "replay re-performed the SAME edits (typed text + selected option)",
                             f"finalize={fin}"))
    return checks


@scenario(
    id="core.recorder.navigation_survival",
    title="same-origin navigation drops NO step — pre-nav, the navigating click, and post-nav survive",
    group="core", tags=("recorder", "navigation"),
)
async def navigation_survival(ctx: Ctx):
    from ultracua.flow import run_cached
    from ultracua.recorder import record_demo

    checks = []
    fx = Fixture({
        "/one": page('<input type="checkbox" id="a"><label for="a">alpha</label>'
                     '<a href="/two">go to two</a>'),
        "/two": page('<input type="checkbox" id="b"><label for="b">beta</label>'),
    })

    async def _demo(pg) -> None:
        await pg.get_by_role("checkbox", name="alpha").click()  # BEFORE the navigation
        await pg.get_by_role("link", name="go to two").click()  # the NAVIGATING click itself
        await pg.get_by_role("checkbox", name="beta").wait_for()
        await pg.get_by_role("checkbox", name="beta").click()   # AFTER the navigation

    with fx.serve() as base:
        cache = ctx.cache()
        goal = "tick alpha, cross to two, tick beta"
        flow, _, crossed, _ = await record_demo(base + "/one", _demo, goal=goal,
                                                cache=cache, headless=True)
        # Same-origin multi-page is the recorder's target use case — it must NOT be flagged.
        checks.append(expect(crossed is False,
                             "a same-origin navigation is NOT flagged as an origin crossing"))
        # The exfiltration store survives the page teardown: the pre-nav step, the navigating
        # click (the event the old fixed-timeout flush could lose), AND the post-nav step.
        names = [s.locator.name for s in flow.steps if s.locator]
        checks.append(expect(names == ["alpha", "go to two", "beta"],
                             "all three clicks survive the page boundary (incl. the navigating one)",
                             f"names={names}"))

        async def _finalize(session):
            return {"url": session.page.url, "beta": await session.page.is_checked("#b")}

        report = await run_cached(base + "/one", goal, None, cache, mode="replay",
                                  headless=True, finalize=_finalize)
        checks.append(expect(report.success and report.llm_calls == 0,
                             "the cross-page flow replays 0-LLM", f"note={report.note!r}"))
        fin = (report.extra or {}).get("finalize") or {}
        checks.append(expect(str(fin.get("url", "")).endswith("/two") and fin.get("beta") is True,
                             "replay lands on page two and re-performs the post-nav action",
                             f"finalize={fin}"))
    return checks


@scenario(
    id="core.recorder.cross_origin_refusal",
    title="a demo that hops origins (second fixture, different port) fails LOUD — nothing cached",
    group="core", tags=("recorder", "fail-loud"),
)
async def cross_origin_refusal(ctx: Ctx):
    from ultracua.cache import FlowCache, flow_key
    from ultracua.flows import FlowSpec, record
    from ultracua.recorder import record_demo

    checks = []
    # Two Fixtures on two ports = two ORIGINS (origin = scheme://host:port). A cross-origin hop
    # orphans the prior origin's not-yet-drained events (sessionStorage is per-origin), so the
    # recording may be silently truncated — the inviolable is fail LOUD, never a truncated cache.
    fx_b = Fixture({"/land": page('<input type="checkbox" id="c"><label for="c">gamma</label>')})
    with fx_b.serve() as base_b:
        fx_a = Fixture({"/": page(f'<a href="{base_b}/land">external portal</a>')})
        with fx_a.serve() as base_a:

            async def _demo(pg) -> None:
                await pg.get_by_role("link", name="external portal").click()  # CROSS-origin hop
                await pg.wait_for_selector("#c")
                await pg.click("#c")  # a post-hop action on the second origin

            # Low-level truth: record_demo itself detects and flags the hop.
            raw_cache = FlowCache(root=ctx.tmp / "raw")
            _, _, crossed, _ = await record_demo(base_a + "/", _demo, goal="cross the portal",
                                                 cache=raw_cache, headless=True)
            checks.append(expect(crossed is True,
                                 "record_demo flags the hop (crossed_origin=True)"))

            # Policy layer: flows.record REFUSES the possibly-truncated flow — and says why.
            spec = FlowSpec(name="xorigin", start_url=base_a + "/", goal="cross the portal")
            cache = ctx.cache()
            res = await record(spec, demo=_demo, headless=True, cache=cache)
            checks.append(expect(res.cached is False,
                                 "flows.record refuses to cache the recording"))
            checks.append(expect("origin" in res.note.lower(),
                                 "the refusal note names the origin boundary (actionable, not silent)",
                                 f"note={res.note!r}"))
            # Never-silently-wrong: a refused recording must leave NO cached flow behind.
            key = flow_key(spec.goal, spec.start_url, spec.scope)
            checks.append(expect(cache.get(key) is None, "nothing is left in the cache",
                                 "a refused recording left a cached flow behind"))
    return checks


@scenario(
    id="core.recorder.undeclared_write_refusal",
    title="a demo that submits a form WITHOUT mutate declared is detected and refused (never cached)",
    group="core", tags=("recorder", "writes", "fail-loud"),
)
async def undeclared_write_refusal(ctx: Ctx):
    from ultracua.cache import flow_key
    from ultracua.flows import FlowSpec, record

    checks = []
    fx = Fixture({
        "/": page('<form action="/submit" method="post">'
                  '<input id="q" name="q" aria-label="qty"><button>Send</button></form>'),
        "/done": page("<h1>done</h1>"),
    }, post_redirect="/done")

    async def _demo(pg) -> None:
        await pg.fill("#q", "3")
        await pg.get_by_role("button", name="Send").click()  # a POST fires on the wire
        await pg.wait_for_url("**/done")

    with fx.serve() as base:
        cache = ctx.cache()
        # NO spec.mutate: the demo writes, but the caller never declared a confirm check.
        spec = FlowSpec(name="unsend", start_url=base + "/", goal="send the order")
        res = await record(spec, demo=_demo, headless=True, cache=cache)
        # The oracle: the demo's write REALLY reached the server exactly once — so the detection
        # checks below are measuring a genuine wire write, not a no-op demo.
        checks.append(expect(len(fx.writes) == 1 and fx.writes[0].method == "POST",
                             "the demo's write reached the fixture server exactly once",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
        checks.append(expect(res.performed_write is True,
                             "record reports performed_write (the wire write was detected)"))
        checks.append(expect(res.is_write is True, "the flow is classified as a WRITE"))
        # Write safety: an undeclared write has no action-completion check, so caching it would
        # risk a blind (double-submit-capable) replay — record must refuse.
        checks.append(expect(res.cached is False,
                             "an UNDECLARED write is refused — a write needs a confirm check",
                             f"note={res.note!r}"))
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        checks.append(expect(cache.get(key) is None, "nothing is left in the cache",
                             "a refused write recording left a cached flow behind"))
    return checks


@scenario(
    id="core.recorder.aspirational_horizons",
    title="horizon probes: record-time narration (H12) and <select> option DOMAINS (H3)",
    group="core", aspirational=True, tags=("recorder", "horizon"),
)
async def aspirational_horizons(ctx: Ctx):
    from ultracua.recorder import record_demo

    checks = []
    fx = Fixture({"/": page('<select id="f" aria-label="fruit">'
                            '<option value="apple">Apple</option>'
                            '<option value="banana">Banana</option></select>')})

    async def _demo(pg) -> None:
        await pg.select_option("#f", "banana")

    with fx.serve() as base:
        cache = ctx.cache()
        # H12 — record-time NARRATION: does record_demo accept narrate=True (capture the human's
        # spoken/typed commentary alongside the steps)? An unexpected-kwarg TypeError = not built.
        status, out = await probe(record_demo, base + "/", _demo, goal="narrated pick",
                                  cache=cache, headless=True, narrate=True)
        if status == "ok":
            checks.append(ok("record_demo accepts narrate=True (H12)"))
        elif status == "missing":
            checks.append(missing("record_demo narrate=True kwarg (H12)",
                                  f"{type(out).__name__}: not built yet"))
        else:  # the kwarg EXISTS but the recording blew up -> a shipped-capability failure
            checks.append(fail("record_demo narrate=True probe errored", f"{out}"))

        # H3 — option DOMAINS: does the captured `select` step carry the field's LEGAL options
        # (so a later parameterized replay can validate a substituted value against the domain)?
        # First a shipped-behavior prerequisite (measures real current capability, PASSES today):
        flow, _, _, _ = await record_demo(base + "/", _demo, goal="pick banana",
                                          cache=cache, headless=True)
        sel = next((s for s in flow.steps if s.action == "select"), None)
        checks.append(expect(sel is not None, "a select step is captured (the probe's prerequisite)",
                             f"actions={[s.action for s in flow.steps]}"))
        domain = None
        for field in ("options", "option_values", "domain", "choices"):  # plausible future spellings
            domain = getattr(sel, field, None) if sel is not None else None
            if domain:
                break
        checks.append(expect(bool(domain) and "apple" in str(domain),
                             "the captured select step carries the legal options list (H3)",
                             "CachedStep has no options/domain field — the recorder does not yet "
                             "capture a select's legal values", aspirational=True))
    return checks
