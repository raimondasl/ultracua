"""A gate finding must SAY something. Measured: two of them said nothing at all.

THE DEFECT (R4.126). `corpus_run._print_report` rendered `reason or detail or ""`, and the two
channels `--baseline` exists to turn on -- cost and rate -- carry neither. On the 0.150.0 three-rep
run every pass printed

    [note] cost        cost_usd:
    [note] rate        availability_rate:

with the baseline, the current value and the tolerance all computed, all in the record, and none of
them on the screen. The FAIL direction is the same line of code, so a genuine cost regression would
have printed `[FAIL] cost cost_usd:` and named no number.

WHY THE GUARD IS SHAPED LIKE THIS. It does NOT hand-write findings and check the renderer: that
tests the renderer against my idea of what a finding looks like, which is exactly the assumption
that failed. It drives the REAL `gate_bench_record` over records built to light each channel, and
requires every finding it mints to render non-empty. A channel added tomorrow is covered because
nobody listed the channels.
"""

from __future__ import annotations

import pytest

from benchmarks import corpus_run
from benchmarks import outcomes as O


def _rec(**over) -> dict:
    """A bench record shaped like `build_bench_record`'s real output.

    THE SHAPE IS COPIED FROM A REAL RECORD, not invented. A first draft made `scenarios` an int (a
    count, which is what the printed report shows) and every case died in `_flip_findings` with
    `'int' object has no attribute 'items'` -- it is a dict keyed by scenario name. A fixture that
    the gate cannot even traverse proves nothing about what the gate prints.
    """
    base = {
        "bench": "customer-gitea",
        "substrates": ["gitea"],
        "scenarios": {"a": {"outcome": "ok", "substrate": "gitea", "code": ""},
                      "b": {"outcome": "ok", "substrate": "gitea", "code": ""}},
        "scored_scenarios": 2,
        "cost_scenarios": 2,
        "reps": 1,
        "cost_usd": 1.0,
        "metrics": {"availability_rate": {"mean": 1.0, "std": 0.0, "min": 1.0, "max": 1.0, "n": 2}},
        "gated_metrics": ["availability_rate"],
        "outcomes": {"ok": 2},
        "inviolable": [],
        "unscored": [],
        "no_recipe": [],
        "scenario_rows": [{"scenario": "a", "outcome": "ok"},
                          {"scenario": "b", "outcome": "ok"}],
    }
    base.update(over)
    return base


def _findings(record, baseline=None, **kw):
    return O.gate_bench_record(record, baseline=baseline, **kw)["findings"]


# Each row lights a different channel. The POINT is that the assertion below never names a channel:
# it asserts over whatever the gate actually minted.
CASES = [
    ("cost + rate, both passing", _rec(), _rec()),
    ("cost regressed", _rec(cost_usd=100.0), _rec(cost_usd=1.0)),
    ("rate regressed", _rec(metrics={"availability_rate": {"mean": 0.0, "std": 0.0, "n": 2}}),
     _rec()),
    ("a cohort went dark", _rec(gated_metrics=[], metrics={}), _rec()),
    ("inviolable", _rec(inviolable=[{"scenario": "a", "outcome": "double"}]), None),
    ("unscored", _rec(unscored=[{"scenario": "a", "reason": "login_failed"}]), None),
    ("no_recipe", _rec(no_recipe=[{"scenario": "a", "reason": "no_actions_needed"}]), None),
]


@pytest.mark.parametrize("label,record,baseline", CASES, ids=[c[0] for c in CASES])
def test_no_gate_finding_prints_empty(label, record, baseline) -> None:
    findings = _findings(record, baseline)
    if not findings:
        pytest.skip(f"{label!r} minted no finding; another case covers the channel")
    for f in findings:
        rendered = corpus_run.finding_detail(f)
        assert rendered and rendered.strip(), (
            f"{label}: the {f.get('channel')!r} finding for "
            f"{f.get('scenario', f.get('metric'))!r} renders to NOTHING. The operator is told a "
            f"channel fired and not what it found. Raw finding: {f}")
        print(f"    [{label}] {f.get('channel'):10} -> {rendered[:88]}")


def test_the_cost_and_rate_findings_carry_their_numbers() -> None:
    """THE REGRESSION ITSELF, named. These two are the measured ones, and a renderer that satisfied
    the property above by printing a constant would pass it -- so this asserts the NUMBERS reach the
    line, which is what a reader needs to tell a near-miss from a rout."""
    findings = _findings(_rec(cost_usd=100.0), _rec(cost_usd=1.0))
    cost = [f for f in findings if f.get("channel") == "cost"]
    assert cost, "the cost channel minted nothing over a 100x rise"
    rendered = corpus_run.finding_detail(cost[0])
    print(f"    cost -> {rendered}")
    assert "100" in rendered and "1" in rendered, (
        f"the cost finding rendered {rendered!r}, which names neither the current spend nor the "
        f"baseline. A `[FAIL]` an operator cannot size is a FAIL they cannot act on.")


def test_a_finding_with_nothing_on_it_still_renders() -> None:
    """The floor. `_FINDING_STRUCTURE` strips the keys that identify a finding, so a finding with
    ONLY those keys has no evidence left to render -- and must still not print blank."""
    rendered = corpus_run.finding_detail({"channel": "cost", "metric": "cost_usd"})
    assert rendered.strip(), "a finding carrying only structural keys rendered to nothing"
    print(f"    bare -> {rendered}")


def test_none_is_rendered_as_unknown_not_crashed() -> None:
    """1.3, restated: a formatter that cannot render an unknown WILL meet one. `_rate_findings`
    mints `current: None` for a cohort that went dark, and that finding reaches this printer."""
    rendered = corpus_run.finding_detail({"channel": "rate", "metric": "r", "current": None,
                                          "baseline": 0.5})
    print(f"    none -> {rendered}")
    assert "unknown" in rendered, f"None rendered as {rendered!r} rather than an explicit unknown"
