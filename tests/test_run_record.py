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
