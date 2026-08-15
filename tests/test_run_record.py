"""B1 — the run record. Every LLM call the engine causes is counted by ONE run-scoped
accounting object, and every outcome the engine already computes leaves on the record the
caller receives.

These are the properties the benchmark plan's R3/R4 rest on, and each one is written to fail
against the code that preceded it — a cost number nobody can falsify is worse than none, which
is exactly the state `extra["usage"]` was in: ABSENT on the 0-LLM path rather than zero, so
"this replay made no LLM call" and "no key was configured" read identically.
"""

from __future__ import annotations

import pytest

from ultracua.llm.base import Router, Tier
from ultracua.llm.mock import MockClient
from ultracua.llm.types import LLMRequest
from ultracua.obs import UsageTotals

# MockClient reports 10 in / 10 out per call (llm/mock.py:26). Prices are per 1M tokens
# (obs.py:_PRICES): haiku 1.0/5.0, opus 5.0/25.0.
_HAIKU, _OPUS = "claude-haiku-4-5", "claude-opus-4-6"
_PER_CALL_HAIKU = (10 * 1.0 + 10 * 5.0) / 1_000_000
_PER_CALL_OPUS = (10 * 5.0 + 10 * 25.0) / 1_000_000


def _req() -> LLMRequest:
    return LLMRequest(system="s", messages=[{"role": "user", "content": "hi"}])


def _router() -> Router:
    return Router(fast=Tier(MockClient(), _HAIKU), strong=Tier(MockClient(), _OPUS))


@pytest.mark.asyncio
async def test_cost_is_priced_per_response_model_not_at_one_call_site_model() -> None:
    """G4. A run that escalates spends at TWO prices; pricing it at one is wrong in whichever
    direction the caller happened to pass.

    Cannot pass vacuously: the two tiers are priced 5x apart, so a single-model computation
    cannot coincidentally equal the per-tier sum — asserted against both single-model answers.
    """
    r = _router()
    await r.complete(_req(), tier="fast")
    await r.complete(_req(), tier="strong")

    per_tier = _PER_CALL_HAIKU + _PER_CALL_OPUS
    assert r.totals.cost_usd() == pytest.approx(per_tier)

    # ...and it is NOT either of the answers the old single-model call produced.
    assert r.totals.cost_usd() != pytest.approx(2 * _PER_CALL_OPUS)
    assert r.totals.cost_usd() != pytest.approx(2 * _PER_CALL_HAIKU)


@pytest.mark.asyncio
async def test_router_totals_counts_api_calls_not_decides() -> None:
    """G2, and it PINS an existing property rather than fixing one — said plainly because a
    test that is green before and after proves nothing as a regression test.

    `Router.totals.calls` already increments per API call (llm/base.py:71), while
    `FlowReport.llm_calls` counts `decide()` invocations and one decide can make TWO API calls
    (the fast->strong escalation, providers/llm_agent.py:124). So G2's fix is "read the count
    from the Router", and this is the property that makes that sound. If it ever goes red, the
    run record's call count silently becomes a decide count again.

    Cannot pass vacuously: it asserts the count moves by exactly 2 across two tiers, so a
    counter stuck at 0 or incremented once fails.
    """
    r = _router()
    before = r.totals.calls
    await r.complete(_req(), tier="fast")
    await r.complete(_req(), tier="strong")
    assert r.totals.calls - before == 2


def test_as_dict_reports_zero_cost_rather_than_omitting_it() -> None:
    """G1, at the primitive. An absent `cost_usd` key and a zero one are different claims:
    absent means "nobody looked", zero means "nothing was spent". The 0-LLM path must be able
    to say the second.

    Cannot pass vacuously: asserts the KEY is present on a totals that spent nothing, which is
    precisely the case the old `if cost is not None` dropped.
    """
    d = UsageTotals().as_dict()
    assert d["calls"] == 0
    assert "cost_usd" in d, "a run that spent nothing must SAY zero, not omit the field"
    assert d["cost_usd"] == 0.0


def test_unknown_model_does_not_silently_report_zero_cost() -> None:
    """The other direction of the same field, and the reason zero cannot be the default for
    everything: tokens spent on a model with no price entry are UNKNOWN, not free. Reporting
    0.0 there would understate a real bill.

    Cannot pass vacuously: it spends real tokens on an unpriced model and demands the field
    distinguish that from the zero above.
    """
    t = UsageTotals()
    t.add(_FakeUsage(100, 100), model="some-unpriced-model")
    d = t.as_dict()
    assert d["calls"] == 1
    assert d.get("cost_usd") is None, "unpriced tokens are unknown cost, never 0.0"
    assert d.get("cost_unpriced_calls") == 1


class _FakeUsage:
    def __init__(self, i: int, o: int) -> None:
        self.input_tokens, self.output_tokens = i, o
        self.cache_read_tokens = self.cache_write_tokens = 0


# --- RouterWatch: the run-scoped view across every router one run can spend through -------------
# A run holds up to three (the agent's, the extraction-only one flows.py builds for a data replay,
# the audit judge's). Getting this wrong is silent in BOTH directions, which is why both are pinned.

@pytest.mark.asyncio
async def test_watch_does_not_double_count_a_router_reachable_twice() -> None:
    """The failure the design review flagged as silent: `flows._router` returns `provider.router`
    when the provider has one, so the agent router and the "extraction" router are frequently the
    SAME object. Counting it once per reference doubles every token while every existing test stays
    green, because nothing else asserts on the total.

    Cannot pass vacuously: it spends exactly one call and demands exactly one be counted, so a
    double-count reads 2 and a broken watch reads 0.
    """
    r = _router()
    prov = _Provider(r)
    w = UsageTotals.observe(prov, r, r)      # same totals reachable three ways
    await r.complete(_req(), tier="fast")
    d = w.delta()
    assert d.calls == 1, f"one API call must be counted once, got {d.calls}"
    assert d.cost_usd() == pytest.approx(_PER_CALL_HAIKU)


@pytest.mark.asyncio
async def test_watch_sums_across_distinct_routers() -> None:
    """The other direction: the extraction router is usually a DIFFERENT object on the replay path,
    and its spend is the whole reason this exists.

    Cannot pass vacuously: the two routers are on different tiers, so the expected cost is a sum
    neither router alone can produce.
    """
    agent, extraction = _router(), _router()
    w = UsageTotals.observe(_Provider(agent), extraction)
    await agent.complete(_req(), tier="fast")
    await extraction.complete(_req(), tier="strong")
    d = w.delta()
    assert d.calls == 2
    assert d.cost_usd() == pytest.approx(_PER_CALL_HAIKU + _PER_CALL_OPUS)


def test_an_unobserved_llm_path_reports_unknown_never_zero() -> None:
    """The hazard the explicit-zero change introduced, and the reason it could not ship alone.

    Before B1, a data replay reported NO usage key — an honest shrug. Emitting an unconditional
    zero instead would have been strictly worse: a confident wrong number over real extraction
    spend. So a run that cannot see one of its LLM-capable paths must say UNKNOWN.

    Cannot pass vacuously: it asserts the zero is withheld specifically when an owner exposes no
    totals, while the sibling test above asserts a fully-observed run DOES report zero.
    """
    w = UsageTotals.observe(_Spender())        # LLM-capable, exposes no UsageTotals
    assert w.observed is False
    d = w.as_dict("claude-opus-4-6")
    assert d["cost_usd"] is None, "an unobserved spending path must not be reported as free"
    assert d["unobserved_llm_path"] is True


def test_a_fully_observed_run_that_spent_nothing_does_claim_zero() -> None:
    """The control for the test above — without it, "always say unknown" would pass that one and
    destroy the 0-LLM claim this slice exists to make falsifiable."""
    w = UsageTotals.observe(_Provider(_router()))
    assert w.observed is True
    d = w.as_dict("claude-opus-4-6")
    assert d["cost_usd"] == 0.0 and "unobserved_llm_path" not in d


class _Provider:
    def __init__(self, router) -> None:
        self.router = router


class _Spender:
    """An LLM-capable owner with no `totals` — what the vision grounding object looks like today."""
