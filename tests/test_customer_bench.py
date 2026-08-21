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
from benchmarks import substrates as S


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


def test_the_record_states_no_verdict_because_the_vocabulary_is_b3s(substrate) -> None:
    """A half-defined outcome in a recorded artifact is worse than none, because someone will read it.

    B3 owns `{ok, wrong_data, refused, over_gated}` and the six write outcomes. If B2 invented a
    provisional field, every record written before B3 would need reconciling against a vocabulary
    that did not exist when it was written — and the reconciliation would be a guess.
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
