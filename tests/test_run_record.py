"""B1 — the run record. Every LLM call the engine causes is counted by ONE run-scoped
accounting object, and every outcome the engine already computes leaves on the record the
caller receives.

These are the properties the benchmark plan's R3/R4 rest on, and each one is written to fail
against the code that preceded it — a cost number nobody can falsify is worse than none, which
is exactly the state `extra["usage"]` was in: ABSENT on the 0-LLM path rather than zero, so
"this replay made no LLM call" and "no key was configured" read identically.
"""

from __future__ import annotations

import types

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


def test_an_unpriced_serving_model_is_not_repriced_at_the_fallback() -> None:
    """The fallback exists for a response that reported NO model. Applying it to a model that WAS
    reported but is simply not in the price table invents a number — and the pre-existing cells all
    used an unpriced-or-empty fallback, the one shape where the guard happens to fire.

    This is the defect the per-model change exists to prevent, one layer down: `_PRICES` is
    Anthropic-only, so every OpenAI/Gemini response id and every future Claude id lands here, and
    all three production call sites pass a fallback that IS priced (`settings.model`).

    Cannot pass vacuously: the fallback is priced, so a wrong implementation returns a definite
    number and this asserts it does not.
    """
    t = UsageTotals()
    t.add(_FakeUsage(1_000_000, 100_000), model="gpt-5-turbo")   # real id, absent from _PRICES
    d = t.as_dict("claude-opus-4-8")                             # a PRICED fallback
    assert d["cost_usd"] is None, (
        f"a reported-but-unpriced model was repriced at the fallback: {d['cost_usd']}")
    assert d["cost_unpriced_calls"] == 1


def test_the_empty_model_bucket_still_uses_the_fallback() -> None:
    """The control. Narrowing the fallback must not disable it for the case it was written for —
    a client that reports no model at all."""
    t = UsageTotals()
    t.add(_FakeUsage(1_000_000, 100_000), model="")
    d = t.as_dict("claude-opus-4-8")
    assert d["cost_usd"] == pytest.approx((1_000_000 * 5.0 + 100_000 * 25.0) / 1_000_000)
    assert "cost_unpriced_calls" not in d


# --- the record across MULTIPLE attempts --------------------------------------------------------
# `replay()` hands ONE record to up to three `_attempt_replay` calls and then a relearn. Each of
# those spends money. Reporting only the last is an understated bill, which is the failure class
# this whole slice exists to end — so it is pinned at the helper, where it is testable key-lessly.

def _rec():
    from ultracua.flows import RunRecord
    return RunRecord()


# ===================================================================================================
# THE SINK owns these properties since 1.5. They used to be unit tests of `_absorb_usage`, `_mark_ok`
# and `_forget_negative_write_evidence` — three helpers whose whole job was to UNDO what an earlier
# write site had claimed. The helpers are gone; the properties are not, so they are re-expressed
# against `_RecordSink`, which is now the only thing that can answer any of these questions.


class _SpendingRouter:
    """A router a `RouterWatch` can observe, so a cell can make a run actually spend."""

    def __init__(self) -> None:
        self.totals = UsageTotals()

    def spend(self, calls: int = 1, tokens_in: int = 10, model: str = "mock-1") -> None:
        for _ in range(calls):
            self.totals.add(types.SimpleNamespace(
                input_tokens=tokens_in, output_tokens=5, cache_read_tokens=0,
                cache_write_tokens=0), model)


def _sink(router=None, model: str = "mock-1"):
    from ultracua.flows import RunRecord, _RecordSink

    rec = RunRecord()
    sink = _RecordSink(rec)
    if router is not None:
        sink.arm(None, router, model)
    return rec, sink


def _facts(**kw):
    from ultracua.flows import _AttemptFacts

    return _AttemptFacts(**kw)


def test_usage_spans_every_attempt_rather_than_the_last_one() -> None:
    """M2. A run that auth-refreshed and retried spent BOTH attempts' money.

    The mechanism changed and the property did not. There is no per-attempt merge any more: ONE
    run-scoped watch spans the whole call, so "accumulation" stopped being something a site has to
    remember. Cannot pass vacuously — the two attempts spend different amounts, so an implementation
    that took only the last would report 2 calls, not 3.
    """
    router = _SpendingRouter()
    rec, sink = _sink(router)

    router.spend(calls=1, tokens_in=10)          # attempt 1
    sink.attempt(_facts(outcome="failed"))
    router.spend(calls=2, tokens_in=20)          # attempt 2, after the auth refresh
    sink.attempt(_facts(outcome="ok"))
    sink.finish(None)

    assert rec.usage["calls"] == 3
    assert rec.usage["input_tokens"] == 50


def test_an_attempt_that_saw_more_than_the_watch_makes_the_run_unpriceable() -> None:
    """Sticky-unknown, re-expressed. A partial sum presented as the total is the understated bill.

    Where the old code got this from merging each attempt's dict, the sink gets it from a CROSS-CHECK:
    the watch is the source, and an attempt reporting more than the whole-run watch saw means the
    watch is blind to some router — so the cost becomes unknown rather than a confident smaller
    number. Cannot pass vacuously: the watch alone would price this run at zero.
    """
    router = _SpendingRouter()
    rec, sink = _sink(router)

    router.spend(calls=1)
    sink.attempt(_facts(outcome="ok"))
    sink.cross_check_usage({"calls": 9, "cost_usd": 0.5})   # the engine saw far more than we did
    sink.finish(None)

    assert rec.usage["cost_usd"] is None, "a blind watch must not report a confident price"
    assert rec.usage["unobserved_llm_path"] is True
    assert rec.usage["watch_missed_a_router"] is True, "the record must say WHY it is unknown"


def test_unobserved_is_sticky_across_attempts() -> None:
    """One blind attempt makes the run's total untrustworthy, so the flag must survive a later
    fully-observed attempt rather than being overwritten by it."""
    router = _SpendingRouter()
    rec, sink = _sink(router)

    sink.cross_check_usage({"calls": 1, "cost_usd": None, "unobserved_llm_path": True})
    router.spend(calls=1)
    sink.cross_check_usage({"calls": 1, "cost_usd": 0.01})
    sink.attempt(_facts(outcome="ok"))
    sink.finish(None)

    assert rec.usage["unobserved_llm_path"] is True
    assert rec.usage["cost_usd"] is None


def test_landed_and_committed_default_to_unknown_not_to_false() -> None:
    """M4. The engine can raise before the question is answerable — a `finalize` extraction whose
    provider 500s does so AFTER the commit has POSTed. A two-state field defaulting to False then
    asserts "nothing committed" over a write that may have landed.

    Cannot pass vacuously: it asserts `is None`, which a `bool = False` default cannot satisfy.
    """
    r = _rec()
    assert r.landed is None and r.committed is None, (
        "unknown must be representable — a two-state boolean answering a three-state question is "
        "the trap this register records shipping three separate times")


def test_a_successful_run_never_carries_a_failed_attempts_verdict() -> None:
    """M3, and R4.57 one level down. `replay()` can return data from the relearn or the precheck after
    an earlier attempt failed, and only `_attempt_replay`'s success exit cleared the verdict — which
    neither of those paths reaches, so a caller saw `ok=False` on a call that returned data.

    `_mark_ok` was the patch for it: a helper that UNDID an earlier write. The sink removes the need
    by never writing early — `ok` and `failure_code` are decided once, at the single exit, from
    whether `replay()` raised. Cannot pass vacuously: a failed attempt is appended first.
    """
    rec, sink = _sink()
    sink.attempt(_facts(outcome="failed", mode="replay"))
    sink.attempt(_facts(outcome="relearn", mode="relearn"))
    sink.finish(None)

    assert rec.ok is True and rec.failure_code == ""


def test_the_failed_run_names_itself_with_the_exceptions_code() -> None:
    """R4.49's other half: ONE vocabulary. The record used to carry the engine's internal `kind` while
    the caller read `FlowReplayError.code`, and the two could describe different attempts."""
    from ultracua.flows import DriftError

    rec, sink = _sink()
    sink.attempt(_facts(outcome="failed", mode="replay"))
    sink.finish(DriftError("drifted"))

    assert rec.ok is False
    assert rec.failure_code == DriftError("x").code

    rec2, sink2 = _sink()
    sink2.attempt(_facts(outcome="raised", mode="raised"))
    sink2.finish(RuntimeError("boom"))
    assert rec2.failure_code == "raised", (
        "an untyped crash must still name itself, and must not borrow a refusal's vocabulary")


def test_accounting_failure_DURING_the_run_is_reported() -> None:
    """The flag is set while the run is happening, so a watch that reads it at CONSTRUCTION can
    never see it. The first placement probed the owner and tripped an inviolable test; the second
    moved the storage onto UsageTotals and kept the timing bug — the guard was inert both times,
    and the commit message claimed it had a caller when it did not.

    Cannot pass vacuously: the flag is set AFTER observe() returns, which is the only ordering
    that occurs in production (vision sets it inside decide()).
    """
    t = UsageTotals()
    prov = _Provider(Router(fast=Tier(MockClient(), _HAIKU)))
    spender = _Grounder(t)
    w = UsageTotals.observe(prov, spender)
    assert w.observed is True                      # nothing has gone wrong yet

    t.accounting_failures += 1                     # ...now it does, mid-run

    assert w.observed is False, "a failure during the run must reach the report"
    d = w.as_dict("claude-opus-4-6")
    assert d["cost_usd"] is None and d["unobserved_llm_path"] is True


class _Grounder:
    """Shaped like AnthropicGrounding: owns a UsageTotals, has no router."""

    def __init__(self, totals) -> None:
        self.totals = totals


def test_a_previous_attempts_False_never_survives_as_a_confident_denial() -> None:
    """F3/F4, one defect in two places, now answered by one rule.

    A `False` means "THAT attempt did not confirm a write", never "no write happened in this run".
    Two paths let one survive into a claim it cannot support: a post-auth-refresh precheck that
    succeeds — whose own comment says the first attempt's write may have landed — and a later attempt
    that raises, where the honest answer is unknown. `_forget_negative_write_evidence` was the patch
    for it: a helper that downgraded a `False` some other site had already written.

    The sink writes nothing early, so there is nothing to downgrade. `_evidence` decides once, over
    every fact, and unknown beats False by construction.

    Cannot pass vacuously: each case sets a False explicitly and asserts None.
    """
    for unknown in ("precheck", "raised", "relearn_raised"):
        rec, sink = _sink()
        sink.attempt(_facts(outcome="failed", landed=False, committed=False))
        sink.attempt(_facts(outcome=unknown))
        sink.finish(None)
        assert (rec.landed, rec.committed) == (None, None), (
            f"a {unknown} outcome left the record denying a write it has no evidence about")

    # ...and a True is never downgraded: evidenced spend stays evidenced.
    rec, sink = _sink()
    sink.attempt(_facts(outcome="failed", landed=True, committed=True))
    sink.attempt(_facts(outcome="raised"))
    sink.finish(None)
    assert (rec.landed, rec.committed) == (True, True), (
        "a write once evidenced as landed cannot be un-landed by a later attempt failing earlier")
