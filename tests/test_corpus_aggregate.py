"""Folding N corpus passes into one record (B5).

BROWSER-FREE AND KEY-FREE — records are plain dicts, so every path runs in the fast tier, including
the refusals, which are the half a live series is least likely to exercise.
"""
from __future__ import annotations

import pytest

from benchmarks import outcomes as O
# IMPORTED AS A MODULE, NOT BY NAME. `from ... import fold` binds the original function object
# here, so patching `corpus_aggregate.fold` never reaches these cells — the arming harness
# reported all four mutations as survivors until this changed. That is CLAUDE.md's S14 lesson
# (`from .providers import build_router` never seeing a patch of `providers.build_router`),
# arrived at from the test side.
from benchmarks import corpus_aggregate as CA
from benchmarks.corpus_aggregate import AggregateError


def _rec(outcomes: dict, *, mean=None, cost=0.5, bench="customer-gitea") -> dict:
    """One pass's record, carrying only what `fold` reads."""
    if mean is None:
        scored = [o for o in outcomes.values() if o != O.UNSCORED]
        mean = (sum(1 for o in scored if o in O.QUIET_OUTCOMES) / len(scored)) if scored else 0.0
    return {
        "bench": bench,
        "cost_usd": cost,
        "metrics": {"availability_rate": {"mean": mean, "n": len(outcomes)}},
        "scenarios": {k: {"outcome": v, "substrate": "gitea", "code": ""}
                      for k, v in outcomes.items()},
    }


ALL_OK = {"a": O.OK, "b": O.OK, "c": O.OK}


def test_a_row_that_flips_is_named() -> None:
    """THE DELIVERABLE. `odoo-create-lead` learned in 6 steps in one run and captured 0 in another;
    a series that cannot say which rows did that is a series nobody can act on."""
    agg = CA.fold([_rec({**ALL_OK, "c": O.OK}), _rec({**ALL_OK, "c": O.NOT_AUTHORED}),
                _rec({**ALL_OK, "c": O.OK})])
    assert [u["scenario"] for u in agg["unstable"]] == ["c"]
    u = agg["unstable"][0]
    assert (u["passes"], u["of"]) == (2, 3)
    assert u["outcomes"] == sorted({O.OK, O.NOT_AUTHORED}), (
        "the OUTCOMES ride along: 'ok/over_gated' and 'ok/not_authored' are both 2-of-3 and want "
        "different fixes")


def test_a_row_that_never_changes_is_not_called_unstable() -> None:
    """Both directions — a row that fails every time is STABLE, and calling it unstable would send
    someone hunting a flake that is really a permanent product result."""
    agg = CA.fold([_rec({"a": O.OK, "b": O.NOT_AUTHORED})] * 3)
    assert agg["unstable"] == []
    assert agg["stability"]["b"]["passes"] == 0


def test_unscored_reps_leave_a_rows_denominator() -> None:
    """`unscored` is neither a pass nor a failure. Counting it either way invents a number: as a
    failure it manufactures instability, as a pass it hides it."""
    agg = CA.fold([_rec({"a": O.OK}), _rec({"a": O.UNSCORED}), _rec({"a": O.OK})])
    assert agg["stability"]["a"] == {"passes": 2, "scored_reps": 2, "reps": 3,
                                     "outcomes": sorted({O.OK, O.UNSCORED})}
    assert agg["unstable"] == [], "2 of 2 scored reps passed; the unscored one is not a flip"


def test_the_spread_is_a_real_across_run_estimate() -> None:
    """B3 had to hand `variance.aggregate` one value per SCENARIO, where the stdev of a 0/1 vector
    is a closed form of the mean. Here each value is one PASS, so `std` finally means what it says.
    """
    agg = CA.fold([_rec(ALL_OK, mean=1.0), _rec(ALL_OK, mean=0.5), _rec(ALL_OK, mean=0.75)])
    a = agg["availability_rate"]
    assert a["per_rep"] == [1.0, 0.5, 0.75]
    assert a["mean"] == pytest.approx(0.75)
    assert a["std"] > 0 and a["min"] == 0.5 and a["max"] == 1.0


def test_one_pass_is_refused() -> None:
    """The whole point of the module. A single pass has no spread, and publishing it here would
    attach `std: 0.0` to a number that has never been repeated — which reads as perfect stability.
    """
    with pytest.raises(AggregateError, match="at least 2 passes"):
        CA.fold([_rec(ALL_OK)])


def test_passes_over_different_scenario_SETS_are_refused() -> None:  # noqa: N802
    """A corpus edit mid-series is exactly the thing that would make a mean meaningless while every
    number in it still looked plausible. The refusal names the differing rows."""
    with pytest.raises(AggregateError, match="do not cover the same scenarios"):
        CA.fold([_rec(ALL_OK), _rec({**ALL_OK, "d": O.OK})])


def test_an_unpriceable_pass_makes_the_series_cost_unknown() -> None:
    """UNKNOWN IS ABSORBING (1.3). Summing the passes that happened to be priceable would understate
    a real bill silently, which is the `or 0.0` defect one module over."""
    agg = CA.fold([_rec(ALL_OK, cost=0.5), _rec(ALL_OK, cost=None)])
    assert agg["cost_usd"] is None


def test_a_series_with_no_availability_metric_is_refused() -> None:
    rec = _rec(ALL_OK)
    rec["metrics"] = {}
    with pytest.raises(AggregateError, match="nothing to fold"):
        CA.fold([rec, rec])


def test_it_does_not_write_a_baseline() -> None:
    """Promoting a record into `baselines/` stays a reviewed human act — the same property
    `corpus_run` carries, asserted the same way: every file this module opens is the caller's
    `--out`, read from the AST rather than grepped for a path."""
    import ast
    import inspect

    from benchmarks import corpus_aggregate

    tree = ast.parse(inspect.getsource(corpus_aggregate))
    targets = [ast.unparse(n.args[0]) if n.args else "<none>"
               for n in ast.walk(tree)
               if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "open"]
    assert targets == ["args.out"], (
        f"this module opens {targets}; the only permitted destination is the caller's --out")


def test_a_row_that_fails_two_different_ways_is_surfaced_separately() -> None:
    """FOUND BY THE FIRST REAL SERIES. `odoo-menu-nav` came back 0/3 having given `over_gated` in
    some passes and `refused` in others — it showed as stable because neither outcome is a pass,
    and a row failing two different ways is not the same as one failing the same way every time.

    Kept out of `unstable` deliberately: a RATE over such a row is reproducible, so a baseline is
    not endangered by it. What it endangers is the diagnosis.
    """
    agg = CA.fold([_rec({"a": O.OVER_GATED}), _rec({"a": O.REFUSED}), _rec({"a": O.OVER_GATED})])
    assert agg["unstable"] == [], "the pass/fail never moved, so the rate is not in doubt"
    assert [v["scenario"] for v in agg["varies"]] == ["a"]
    assert agg["varies"][0]["outcomes"] == sorted({O.OVER_GATED, O.REFUSED})


def test_a_row_that_fails_the_SAME_way_every_time_is_not_surfaced() -> None:  # noqa: N802
    """Both directions. `gitea-start-timer` is 0/3 `not_authored` in the real series — a stable,
    reproducible product limitation (R4.102), and flagging it would send someone hunting a flake
    that is not there."""
    agg = CA.fold([_rec({"a": O.NOT_AUTHORED})] * 3)
    assert agg["unstable"] == [] and agg["varies"] == []


# --- the baseline artifact ------------------------------------------------------------------------

def test_the_baseline_counts_every_observation_not_the_corpus_size() -> None:
    """`n` is what the gate's Wilson bound is computed from, so it must reflect the evidence bought.
    Three passes of seven scenarios is 21 observations; recording 7 would throw two thirds of the
    series away and re-create the problem the reps were paid for."""
    base = CA.as_baseline([_rec(ALL_OK)] * 3)
    assert base["metrics"]["availability_rate"]["n"] == 9
    assert base["reps"] == 3


def test_a_coin_flip_row_is_recorded_as_NOT_passing() -> None:  # noqa: N802
    """`_flip_findings` reports a row that was quiet in the baseline and is not now. Recording a
    1-of-3 row as quiet would make it report a flip on most future runs — and a channel that cries
    wolf gets ignored, which is the failure mode R3.9/CLI-1 is about."""
    base = CA.as_baseline([_rec({"a": O.OK}), _rec({"a": O.REFUSED}), _rec({"a": O.NOT_AUTHORED})])
    assert base["scenarios"]["a"]["outcome"] not in O.QUIET_OUTCOMES
    assert [u["scenario"] for u in base["unstable"]] == ["a"], "and it is named as untrustworthy"


def test_the_baseline_cost_is_the_MAX_observed_not_the_mean() -> None:  # noqa: N802
    """MEASURED, AND THE MEAN WAS WRONG. `_cost_findings` regresses at `baseline * 1.25`, and three
    identical Gitea passes cost $0.3502 / $0.8421 / $0.6167 — an **82% spread**. Against a mean
    baseline the gate failed rep 2, one of the passes the baseline was built from.
    """
    base = CA.as_baseline([_rec(ALL_OK, cost=0.35), _rec(ALL_OK, cost=0.84),
                           _rec(ALL_OK, cost=0.62)])
    assert base["cost_usd"] == 0.84
    assert base["cost_per_rep"] == [0.35, 0.84, 0.62], (
        "the mean stays recoverable — the expected cost and the alarm threshold are different "
        "questions and the artifact must not force one to stand in for the other")


def test_a_baseline_passes_the_very_passes_it_was_built_from() -> None:
    """THE LOAD-BEARING CELL. A baseline that fails its own evidence is not a baseline, and this is
    exactly what the mean-cost draft did. Both directions matter, so the next cell breaks it."""
    recs = [_rec(ALL_OK, cost=0.35), _rec({**ALL_OK, "c": O.REFUSED}, cost=0.84),
            _rec(ALL_OK, cost=0.62)]
    base = CA.as_baseline(recs)
    for i, r in enumerate(recs, 1):
        assert O.gate_bench_record(r, baseline=base)["ok"], f"rep {i} fails its own baseline"


def test_a_baseline_still_fails_a_run_that_really_regressed() -> None:
    """The other half — without it, everything above is satisfied by a baseline that passes anything.
    """
    recs = [_rec(ALL_OK, cost=0.5)] * 3
    base = CA.as_baseline(recs)
    collapsed = _rec({k: O.NOT_AUTHORED for k in ALL_OK}, mean=0.0, cost=0.5)
    assert O.gate_bench_record(collapsed, baseline=base)["ok"] is False
    blown = _rec(ALL_OK, cost=5.0)
    assert O.gate_bench_record(blown, baseline=base)["ok"] is False
