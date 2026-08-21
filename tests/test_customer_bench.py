"""The harness's own logic, driven without Docker, without an agent and without a key.

`run_scenario` takes `agent_call` as an argument rather than importing one, and that is not a
convenience — it is what makes the part B2 must get right testable at all. Reset, readiness,
error ATTRIBUTION and ledger accounting are the harness's job; a design that could only be exercised
with a live agent and a running ERP would leave exactly that untested, which is how a bench ends up
publishing numbers whose provenance nobody has checked.

The property under all of it: a harness failure must never become a scored result. "We could not
build the world" and "the agent could not do it" are different facts, and only one of them says
anything about the product.
"""

from __future__ import annotations

import pytest

from benchmarks import customer_bench as CB
from benchmarks import outcomes as O
from benchmarks import substrates as S
from ultracua import flows


class _Obs:
    def __init__(self, n):
        self.elements = list(range(n))


class _FakeSubstrate:
    """Records what the harness asked it to do, and can be told to fail at either step."""

    url = "http://substrate.invalid"

    def __init__(self, fail_reset=False, fail_ready=False):
        self.fail_reset, self.fail_ready = fail_reset, fail_ready
        self.calls = []

    def reset(self):
        self.calls.append("reset")
        if self.fail_reset:
            raise S.SubstrateError("reset failed: the template database is missing")

    def await_ready(self, **_kw):
        self.calls.append("await_ready")
        if self.fail_ready:
            raise S.SubstrateNotReady("gitea was not ready within 300s — R4.40")


@pytest.fixture()
def substrate(monkeypatch):
    fake = _FakeSubstrate()
    monkeypatch.setitem(CB.SUBSTRATES, "gitea", lambda: fake)
    return fake


SCEN = CB.Scenario(name="smoke-read", substrate="gitea", goal="read something")


def test_the_happy_path_resets_before_it_measures(substrate) -> None:
    """Order matters: a scenario run against an un-reset substrate measures the PREVIOUS scenario's
    leftovers and reports them as this one's result."""
    run = CB.run_scenario(SCEN, agent_call=lambda s, url: _Obs(40))
    assert substrate.calls == ["reset", "await_ready"], (
        f"expected reset THEN readiness; got {substrate.calls}")
    assert run.harness_error == ""
    assert run.wall_s >= 0
    assert run.llm_calls == 0, "nothing built a router, so nothing spent"


def test_a_failed_reset_abandons_rather_than_scoring(substrate, monkeypatch) -> None:
    """The whole point. A world we could not build is not a result about the agent."""
    monkeypatch.setattr(substrate, "fail_reset", True)
    reached = []
    run = CB.run_scenario(SCEN, agent_call=lambda s, url: reached.append(url))

    assert reached == [], "the agent was run against a substrate that failed to reset"
    assert "template database is missing" in run.harness_error
    assert run.wall_s == 0.0, "a scenario that never ran must not report a duration"


def test_a_substrate_that_never_becomes_ready_abandons_too(substrate, monkeypatch) -> None:
    monkeypatch.setattr(substrate, "fail_ready", True)
    reached = []
    run = CB.run_scenario(SCEN, agent_call=lambda s, url: reached.append(url))
    assert reached == []
    assert "R4.40" in run.harness_error


def test_a_skeleton_first_observation_is_recorded_as_a_harness_error(substrate) -> None:
    """R4.40 reaching the harness. The agent RAN — so this is the case where a scored zero is most
    tempting and most wrong: the page had not rendered, and grading that as a discovery failure
    converts the harness's impatience into a product defect."""
    run = CB.run_scenario(SCEN, agent_call=lambda s, url: _Obs(0))
    assert "ABANDONING rather than scoring" in run.harness_error
    assert run.agent_error == "", "a skeleton page is the HARNESS's fault, not the agent's"

    # ...and a rendered page does NOT trip it, or the guard would abandon every scenario.
    ok = CB.run_scenario(SCEN, agent_call=lambda s, url: _Obs(40))
    assert ok.harness_error == ""


def test_the_record_carries_cost_and_sites_from_the_ledger(substrate, monkeypatch) -> None:
    """Cost comes from where `Router.complete` meters it, never from a `RunRecord`.

    Driven by making the agent construct a router through a real derived binding, so the ledger sees
    a genuine crossing rather than one this test injected into it.
    """
    import ultracua.providers as providers
    from ultracua.obs import UsageTotals
    from ultracua.llm.base import Router

    spent = UsageTotals()
    spent.input_tokens, spent.output_tokens, spent.calls = 700, 70, 2
    spent.per_model["fast"] = (700, 70, 0, 0, 2)

    router = Router.__new__(Router)          # no config needed; only `.totals` is read
    router.totals = spent
    monkeypatch.setattr(providers, "build_router", lambda backend: router)

    def agent(scenario, url):
        providers.build_router("anthropic")   # the boundary crossing, through the real binding
        return _Obs(40)

    run = CB.run_scenario(SCEN, agent_call=agent)
    assert run.llm_calls == 2, "spend was not read from the router the run constructed"
    assert run.input_tokens == 700 and run.output_tokens == 70
    assert run.llm_sites == {"ultracua.providers.build_router": 1}, (
        f"the crossing was not attributed to its site; got {run.llm_sites}")
    assert run.per_model["fast"] == [700, 70, 0, 0, 2]


def test_the_record_states_no_verdict_even_now_that_b3_has_a_vocabulary(substrate) -> None:
    """B3 SHIPPED and this record still carries no outcome — that is the point, not an oversight.

    The original reason was sequencing: a provisional field would have needed reconciling against a
    vocabulary that did not exist yet. The reason it survives B3 is stronger. A verdict is an
    ADJUDICATION (harness facts + a server-side oracle + the corpus author's ground truth); this
    object is an OBSERVATION. `outcomes.Scored` holds the pair, so nothing that reads a raw scenario
    record can mistake one for the other — which is the same line B2 already draws between
    `harness_error` and `agent_error`, one level up.
    """
    run = CB.run_scenario(SCEN, agent_call=lambda s, url: _Obs(40))
    d = run.to_dict()
    for forbidden in ("outcome", "verdict", "passed", "score", "success"):
        assert forbidden not in d, (
            f"the B2 record carries {forbidden!r} — the outcome vocabulary is B3's, and a "
            f"provisional one here would have to be reconciled with it later")
    # NOT `== S.FAKETIME_EPOCH` — comparing the report's value to the constant it was copied from is
    # a tautology that would survive the field being deleted from the compose file entirely. What the
    # record must carry is a REAL pinned clock, and the compose file must default to the same one.
    epoch = d["substrate_world"]["faketime_epoch"]
    assert epoch.startswith("@") and len(epoch) > 10, f"{epoch!r} is not a pinned clock"
    assert epoch in S.COMPOSE.read_text(encoding="utf-8"), (
        "the recorded epoch is not the one the compose file defaults to, so a record would describe a "
        "clock the container never ran under")


def test_the_scenario_readiness_hook_is_polled_and_refuses_on_timeout() -> None:
    """Substrate-ready is not scenario-ready: "Gitea is serving" is not "the repo this scenario
    opens exists"."""
    calls = []
    late = CB.Scenario(name="x", substrate="gitea", goal="g",
                       ready=lambda: (calls.append(1), len(calls) >= 3)[1])
    CB._await_scenario_ready(late, timeout_s=5, poll_s=0.01)
    assert len(calls) == 3, "the hook must be POLLED, not asked once"

    never = CB.Scenario(name="y", substrate="gitea", goal="g", ready=lambda: False)
    with pytest.raises(S.SubstrateNotReady, match="readiness hook never passed"):
        CB._await_scenario_ready(never, timeout_s=0.05, poll_s=0.01)

    # No hook is not a failure — most scenarios need nothing beyond the substrate.
    CB._await_scenario_ready(CB.Scenario(name="z", substrate="gitea", goal="g"), timeout_s=0.01)


def test_the_smoke_set_is_two_scenarios_and_covers_both_directions() -> None:
    """B2 is scoped to two smoke scenarios. They exist to prove the LIFECYCLE, not to measure
    anything — B4 brings the real corpus. More here before then would produce numbers that look
    like results and are not."""
    assert len(CB.SMOKE) == 2, f"B2's scope is two smoke scenarios; found {len(CB.SMOKE)}"
    assert {s.mutating for s in CB.SMOKE} == {True, False}, (
        "the smoke set must exercise a read AND a write, or the write path's lifecycle is unproven")


def test_an_agent_that_raises_is_a_RECORDED_FACT_not_an_aborted_batch(substrate) -> None:
    """A failing agent is the NORMAL case a benchmark exists to record.

    Before the fix, `run_scenario` caught only `SubstrateError`/`SubstrateNotReady`, so anything else
    the agent raised propagated out of the function: `ledger.usage()` was never reached, the spend
    already incurred was discarded, and NO `ScenarioRun` was produced at all. One crashed scenario
    aborted the whole batch and left no artifact saying why — while having cost real money.
    """
    run = CB.run_scenario(SCEN, agent_call=lambda s, url: (_ for _ in ()).throw(
        ValueError("the selector did not resolve")))
    assert run.agent_error == "ValueError: the selector did not resolve"
    assert run.harness_error == "", (
        "an agent failure was attributed to the harness — the two facts are about different subjects "
        "and only one of them says anything about the product")
    assert run.wall_s >= 0


def test_spend_is_recorded_even_when_the_scenario_crashes(substrate, monkeypatch) -> None:
    """Cost incurred is cost incurred. `_record_cost` runs in the FINALLY for exactly this."""
    import ultracua.providers as providers
    from ultracua.llm.base import Router
    from ultracua.obs import UsageTotals

    spent = UsageTotals()
    spent.input_tokens, spent.calls = 900, 3
    router = Router.__new__(Router)
    router.totals = spent
    monkeypatch.setattr(providers, "build_router", lambda backend: router)

    def agent(scenario, url):
        providers.build_router("anthropic")            # money is spent...
        raise RuntimeError("...and then it falls over")

    run = CB.run_scenario(SCEN, agent_call=agent)
    assert "RuntimeError" in run.agent_error
    assert run.llm_calls == 3 and run.input_tokens == 900, (
        "a crashed scenario discarded its spend — the run cost real money and the record says zero")


def test_an_agent_that_shows_no_first_observation_is_refused_not_scored(substrate) -> None:
    """`if first is not None` used to skip R4.40's guard entirely.

    An `agent_call` returning None produced a clean, scored-looking record with the guard never
    invoked. An agent that cannot show its first observation has not proved the page rendered, and
    the safe direction is to treat that as unproven rather than fine.
    """
    run = CB.run_scenario(SCEN, agent_call=lambda s, url: None)
    assert "ABANDONING rather than scoring" in run.harness_error, (
        "a None first observation slipped past the skeleton guard and produced a clean record")


def test_the_record_distinguishes_a_seen_zero_from_an_unseen_one(substrate, monkeypatch) -> None:
    """`llm_calls: 0` and "could not see" must not share a representation.

    This is the confident-zero defect carried one layer out: the ledger now answers a tri-state, and
    the record has to keep it. `RouterWatch` calls the alternative "a CONFIDENT WRONG NUMBER".
    """
    clean = CB.run_scenario(SCEN, agent_call=lambda s, url: _Obs(40))
    assert clean.llm_calls == 0 and clean.llm_accounting == "observed"

    def agent(scenario, url):
        # A component that spends outside any router the ledger constructed — vision's shape.
        import benchmarks.customer_bench as _cb          # the live ledger is the one in the with-block
        _cb  # noqa: B018
        raise _Unobservable()

    class _Unobservable(Exception):
        pass

    # Drive the state directly rather than reaching into the with-block: the property under test is
    # that the RECORD carries it, and `_record_cost` is the one place that decides.
    from benchmarks.boundary_ledger import BoundaryLedger
    led = BoundaryLedger()
    led.mark_unobserved("vision drives the SDK directly (R4.41)")
    run = CB.ScenarioRun(scenario="x", substrate="gitea")
    CB._record_cost(run, led)
    assert run.llm_accounting == "unknown"
    assert run.llm_unobserved and "vision" in run.llm_unobserved[0]
    assert run.llm_calls == 0, "the count is still zero — what changed is that it is not a CLAIM"


# ---------------------------------------------------------------------------------------------
# B3 — the refusal arrives STRUCTURED, and the whole chain runs end to end
# ---------------------------------------------------------------------------------------------

def test_a_refusal_is_recorded_as_a_CODE_and_not_only_as_a_string(substrate) -> None:
    """`agent_error` is `f"{type(exc).__name__}: {exc}"`, and bucketing a benchmark on that is the
    "sub-labels from message labels" `reshape-plan.md` 2.2 forbids.

    The code comes through `flows.outcome_of` — 1.4b's seam — so the bench never asks `isinstance`
    and never mints a code of its own. This is the single dependency that made 1.4 a prerequisite
    for B3, so it is asserted against the real taxonomy rather than a stand-in.
    """
    def raises(scenario, url):
        raise flows.NotApprovedError("this flow is not approved")

    run = CB.run_scenario(SCEN, agent_call=raises)
    assert run.agent_error.startswith("NotApprovedError:")
    assert run.agent_error_code == "not_approved", run.agent_error_code
    assert run.agent_error_code in flows.REGISTRY
    assert run.agent_error_retryable is False
    print(f"refusal recorded as code={run.agent_error_code!r} "
          f"retryable={run.agent_error_retryable} beside {run.agent_error!r}")


def test_a_bare_crash_is_recorded_as_raised_rather_than_as_an_empty_code(substrate) -> None:
    """An empty code and "it crashed" are different facts. `outcome_of` names the second one, so the
    bench never has to decide what a missing code means."""
    def boom(scenario, url):
        raise ZeroDivisionError("division by zero")

    run = CB.run_scenario(SCEN, agent_call=boom)
    assert run.agent_error_code == O.CRASH_CODE == "raised"
    assert run.agent_error_code not in flows.REGISTRY, (
        "'raised' must stay OUTSIDE the taxonomy — it is `_RecordSink`'s non-typed fallback, and a "
        "class claiming it would put two meanings under one slug")
    print(f"a ZeroDivisionError is recorded as code={run.agent_error_code!r}")


def test_the_product_s_own_landed_claim_is_recorded_and_never_reaches_the_verdict(substrate) -> None:
    """`exc.landed` is the ledger's arming token — the product's claim about its own write.

    It rides on the record so `cross_check` can notice it disagreeing with the server, and that is
    ALL it does. Driven with `WriteReadbackError`, the one class in the taxonomy that declares
    `landed = True`, so the field is non-default and the assertion is not a tautology.
    """
    assert flows.WriteReadbackError.landed is True, "premise moved — pick another armed class"

    def raises(scenario, url):
        raise flows.WriteReadbackError("the readback did not match")

    run = CB.run_scenario(SCEN, agent_call=raises)
    assert run.agent_error_landed is True

    # The SERVER says nothing landed. The verdict follows the server, not the claim.
    truth = O.ScenarioTruth(name=SCEN.name, mutating=True)
    v = O.classify(truth, run, O.Oracle())
    assert v.outcome == O.REFUSED_WRONGLY, v
    assert "landed" not in v.evidence, v.evidence
    print(f"exc.landed=True recorded; verdict from the server = {v.outcome}")


def test_the_whole_chain_runs_from_a_scenario_to_a_gated_record(substrate) -> None:
    """B2's harness -> B3's verdict -> B3's record -> B3's gate, through the REAL functions.

    Every cell in `test_bench_outcomes.py` constructs its `ScenarioRun` by hand. That pins the
    classifier and NOT the plumbing, and 1.4b's worst finding was exactly a golden that built its
    subject by hand and therefore could not see the change it existed for. This one starts where a
    real run starts.
    """
    def ok(scenario, url):
        return _Obs(40)

    def refuses(scenario, url):
        raise flows.UndeclaredWriteError("the wire saw a write this flow does not declare")

    good = CB.run_scenario(SCEN, agent_call=ok)
    bad = CB.run_scenario(CB.Scenario(name="odoo-open-record", substrate="gitea", goal="read"),
                          agent_call=refuses)

    gate = O.GateEvidence(present=True, mutating_steps=1, mutating_sources=("keyword",),
                          approved=False)
    rows = [
        O.adjudicate(O.ScenarioTruth(name=good.scenario), good, O.Oracle(data_correct=True)),
        O.adjudicate(O.ScenarioTruth(name=bad.scenario), bad, O.Oracle(), gate),
    ]
    assert [r.verdict.outcome for r in rows] == [O.OK, O.OVER_GATED], \
        [r.verdict.outcome for r in rows]

    rec = O.build_bench_record(rows, bench="customer", provider="anthropic",
                               timestamp="2026-08-21T00:00:00+00:00")
    assert rec["outcomes"][O.OVER_GATED] == 1
    assert rec["metrics"]["availability_rate"]["mean"] == 0.5
    assert rec["cost_usd"] == 0.0, "no LLM was reached, and the ledger could see that"
    assert O.gate_bench_record(rec)["ok"] is True, "over_gating is loud, but it is not inviolable"
    print(f"chain: {[r.verdict.outcome for r in rows]} -> availability "
          f"{rec['metrics']['availability_rate']['mean']}, cost ${rec['cost_usd']}, "
          f"gate ok={O.gate_bench_record(rec)['ok']}")


def test_adjudicate_mints_the_verdict_BEFORE_it_looks_at_the_record(substrate) -> None:
    """The two passes stay separate even when called together.

    A record that contradicts the world must not be able to change the outcome — only to appear in
    `record_disagrees`. Driven with a record claiming success over a run the server refutes.
    """
    class _Rec:
        ok, committed, failure_code = True, True, ""

    run = CB.run_scenario(SCEN, agent_call=lambda s, url: _Obs(40))
    s = O.adjudicate(O.ScenarioTruth(name=run.scenario, mutating=True), run, O.Oracle(),
                     record=_Rec())
    assert s.verdict.outcome == O.SUPPRESSED, s.verdict
    assert len(s.disagreements) == 2, s.disagreements
    print(f"record says ok/committed; server holds nothing -> verdict {s.verdict.outcome}, "
          f"{len(s.disagreements)} disagreement(s) {[d['field'] for d in s.disagreements]}")
