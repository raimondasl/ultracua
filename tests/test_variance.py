"""Standing-benchmark gate logic (key-less): aggregation, run records, and regression comparison.

The real variance harness uses a live LLM and is manual/local, but its record + compare logic — the
part that turns "8/10 once" into a gate that separates a real regression from run-to-run noise — is
pure and tested here with synthetic numbers (no LLM, no browser).
"""

from __future__ import annotations

import pytest

from benchmarks.variance import aggregate, build_record, compare_records

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


def test_speedup_is_reported_but_not_gated() -> None:
    # speedup halves (machine-dependent micro-timing) but success + cost hold -> still PASS.
    base = _rec([1, 1, 1, 1, 1], cost=0.10, speedup=[100, 100, 100, 100, 100])
    cur = _rec([1, 1, 1, 1, 1], cost=0.10, speedup=[40, 40, 40, 40, 40])
    res = compare_records(base, cur)
    assert res["ok"] is True
    f = next(x for x in res["findings"] if x["metric"] == "speedup")
    assert f["gated"] is False and f["regressed"] is False
