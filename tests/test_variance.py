"""Standing-benchmark gate logic (key-less): aggregation, run records, and regression comparison.

The real variance harness uses a live LLM and is manual/local, but its record + compare logic — the
part that turns "8/10 once" into a gate that separates a real regression from run-to-run noise — is
pure and tested here with synthetic numbers (no LLM, no browser).
"""

from __future__ import annotations

import pytest

from benchmarks.variance import (
    aggregate,
    build_record,
    compare_records,
    first_failure_index,
    hazard_curve,
    pass_hat_k,
    pass_k_curve,
    wilson_ci,
)

_TS = "2026-06-19T00:00:00+00:00"


def _rec(rates, cost, speedup=None):
    per_rep = {"replay_success_rate": rates}
    if speedup is not None:
        per_rep["speedup"] = speedup
    return build_record("demo", "anthropic", len(rates), _TS, per_rep, cost_usd=cost)


def test_aggregate_basic() -> None:
    a = aggregate([1.0, 2.0, 3.0])
    assert a["mean"] == 2.0 and a["min"] == 1.0 and a["max"] == 3.0 and a["n"] == 3 and a["std"] > 0
    empty = aggregate([])
    assert empty["n"] == 0 and empty["mean"] == 0.0 and empty["std"] == 0.0


def test_build_record_shape() -> None:
    rec = build_record("demo", "anthropic", 3, _TS,
                       {"replay_success_rate": [1.0, 1.0, 0.0], "speedup": [80.0, 90.0]}, cost_usd=0.12)
    assert rec["bench"] == "demo" and rec["reps"] == 3 and rec["timestamp"] == _TS
    assert rec["metrics"]["replay_success_rate"]["mean"] == pytest.approx(2 / 3)
    assert rec["metrics"]["speedup"]["mean"] == 85.0
    assert rec["cost_usd"] == 0.12


def test_compare_passes_within_error_bars() -> None:
    # baseline 0.8 mean with a wide spread; current 0.6 is within one stdev -> NOISE, not a regression.
    base = _rec([1, 1, 0, 1, 1], cost=0.10)   # mean 0.8, std ~0.45
    cur = _rec([1, 0, 1, 0, 1], cost=0.10)    # mean 0.6
    res = compare_records(base, cur)
    assert res["ok"] is True


def test_compare_flags_success_regression() -> None:
    # baseline is a rock-solid 1.0 (std 0); current collapses to 0.2 -> well beyond the floor tolerance.
    base = _rec([1, 1, 1, 1, 1], cost=0.10)
    cur = _rec([1, 0, 0, 0, 0], cost=0.10)
    res = compare_records(base, cur)
    assert res["ok"] is False
    f = next(x for x in res["findings"] if x["metric"] == "replay_success_rate")
    assert f["regressed"] is True and f["gated"] is True


def test_compare_flags_cost_regression() -> None:
    base = _rec([1, 1, 1, 1, 1], cost=0.10)
    cur = _rec([1, 1, 1, 1, 1], cost=0.20)    # +100% > 25% tolerance
    res = compare_records(base, cur)
    assert res["ok"] is False
    f = next(x for x in res["findings"] if x["metric"] == "cost_usd")
    assert f["regressed"] is True


def test_pass_hat_k_all_k_succeed() -> None:
    # 7 of 10 reps passed: pass^1 = 0.7, and pass^k falls off fast (the point of the metric).
    assert pass_hat_k(7, 10, 1) == pytest.approx(0.7)
    assert pass_hat_k(10, 10, 3) == 1.0            # all reps passed -> always pass^3
    assert pass_hat_k(2, 10, 3) == 0.0            # fewer successes than k -> impossible
    assert pass_hat_k(5, 10, 11) == 0.0           # k > n
    # pass^3 for a 7/10 agent is far below 0.7 — exactly the reliability the mean hides.
    assert pass_hat_k(7, 10, 3) < 0.35


def test_pass_k_curve_over_booleans() -> None:
    curve = pass_k_curve([True, True, True, False])  # 3 of 4
    assert curve["1"] == pytest.approx(0.75)
    assert curve["4"] == 0.0                        # can't draw 4 all-passing from 3 passes


def test_wilson_ci_bounds() -> None:
    lo, hi = wilson_ci(5, 10)
    assert 0.0 <= lo < 0.5 < hi <= 1.0             # interval brackets the point estimate
    assert wilson_ci(0, 0) == (0.0, 0.0)           # empty -> degenerate, no crash
    flo, fhi = wilson_ci(10, 10)
    assert fhi == pytest.approx(1.0) and flo < 1.0  # all-pass still has a lower bound < 1


def test_first_failure_index_and_hazard() -> None:
    assert first_failure_index([True, True, True]) is None     # all passed
    assert first_failure_index([True, False, True]) == 1       # first failing step
    haz = hazard_curve([None, 1, 1, 2, None])                  # two flows first-fail at step 1, one at 2
    assert haz == {"1": 2, "2": 1}


def test_build_record_includes_reliability_views() -> None:
    rec = build_record("demo", "anthropic", 4, _TS,
                       {"replay_success_rate": [1.0, 1.0, 0.0, 1.0]}, cost_usd=0.1,
                       first_fail=[None, None, 2, None])
    assert rec["pass_k"]["1"] == pytest.approx(0.75)
    assert rec["pass_rate_wilson95"]["passes"] == 3 and rec["pass_rate_wilson95"]["n"] == 4
    assert rec["hazard"] == {"2": 1}


def test_speedup_is_reported_but_not_gated() -> None:
    # speedup halves (machine-dependent micro-timing) but success + cost hold -> still PASS.
    base = _rec([1, 1, 1, 1, 1], cost=0.10, speedup=[100, 100, 100, 100, 100])
    cur = _rec([1, 1, 1, 1, 1], cost=0.10, speedup=[40, 40, 40, 40, 40])
    res = compare_records(base, cur)
    assert res["ok"] is True
    f = next(x for x in res["findings"] if x["metric"] == "speedup")
    assert f["gated"] is False and f["regressed"] is False
