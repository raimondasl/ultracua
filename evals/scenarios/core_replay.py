"""Core evals: the shipped learn -> 0-LLM replay loop (key-less, local fixtures).

This module is also the STYLE EXEMPLAR for scenario authors:
- key-less: local Fixture pages + a tiny scripted Provider (no LLM, no network beyond localhost)
- every check is a CheckResult; nothing may raise out of the scenario function
- aspirational probes use `probe`/`expect(..., aspirational=True)` so a not-built-yet capability
  reports `missing` (the gap), never crashes and never counts as a regression `fail`
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


def _two_pages() -> Fixture:
    return Fixture({
        "/": page('<a href="/answer">open the daily report</a>'),
        "/answer": page('<h1>Report</h1><p id="total">total: 42</p>'),
    })


@scenario(
    id="core.replay.learn_then_zero_llm",
    title="learn once (scripted, $0) then replay deterministically with zero LLM calls",
    group="core", tags=("replay", "read"),
)
async def learn_then_zero_llm(ctx: Ctx):
    from ultracua.flow import run_cached

    checks = []
    fx = _two_pages()
    with fx.serve() as base:
        cache = ctx.cache()
        goal = "open the daily report page"
        learned = await run_cached(base + "/", goal, _ClickTheLink(), cache, mode="learn", headless=True)
        checks.append(expect(learned.success, "learn succeeds on the fixture", f"note={learned.note!r}"))
        replayed = await run_cached(base + "/", goal, None, cache, mode="replay", headless=True)
        checks.append(expect(replayed.success, "replay succeeds from cache", f"note={replayed.note!r}"))
        checks.append(expect(replayed.llm_calls == 0, "replay makes ZERO LLM calls",
                             f"llm_calls={replayed.llm_calls}"))
        checks.append(expect(replayed.mode == "replay", "report says mode=replay", f"mode={replayed.mode}"))
        checks.append(expect(not fx.writes, "a read flow sent no write to the server",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
    return checks


@scenario(
    id="core.replay.miss_without_cache",
    title="replay-only mode fails loud (miss) when nothing was learned — never improvises",
    group="core", tags=("replay", "fail-loud"),
)
async def miss_without_cache(ctx: Ctx):
    from ultracua.flow import run_cached

    fx = _two_pages()
    with fx.serve() as base:
        report = await run_cached(base + "/", "anything", None, ctx.cache(), mode="replay", headless=True)
    return [
        expect(not report.success, "no cached flow -> not success"),
        expect(report.mode == "miss", "reported as a miss, not a silent attempt", f"mode={report.mode}"),
    ]


@scenario(
    id="core.meta.forward_compat_trust",
    title="a meta file from a NEWER version (unknown fields) keeps approval + run history (#78)",
    group="core", tags=("trust", "meta"),
)
async def meta_forward_compat(ctx: Ctx):
    import json

    from ultracua.flows import _load_meta, _meta_path

    cache = ctx.cache()
    p = _meta_path(cache, "cafef00d")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"approved": True, "runs": 7, "successes": 6,
                             "field_from_the_future": {"x": 1}}), encoding="utf-8")
    meta = _load_meta(cache, "cafef00d")
    return [
        expect(meta.approved is True, "approval survives an unknown future field"),
        expect(meta.runs == 7 and meta.successes == 6, "run history survives"),
    ]
