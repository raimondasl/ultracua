"""B3's record and the gate that reads it: what the bench publishes, and what it refuses to.

Browser-free, key-less, Docker-free.

THE THING THESE CELLS GUARD. A benchmark's characteristic failure is not a wrong number — it is a
CONFIDENT number over something nobody measured. `variance.aggregate([])` returns `mean: 0.0, n: 0`;
`UsageTotals.cost_usd()` returns None for spend it cannot price; `BoundaryLedger.observed` goes False
when a router was never watched. Each of those is an honest instrument reporting an unknown, and each
becomes a lie the moment an aggregator sums it into a headline. So most of what is asserted below is
a REFUSAL, and every refusal cell drives the real builder rather than the predicate underneath it.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from benchmarks import customer_bench as CB
from benchmarks import outcomes as O
from benchmarks import variance
from benchmarks.customer_bench import ScenarioRun
from ultracua import flows

TS = "2026-08-21T00:00:00+00:00"
PRICED = "claude-haiku-4-5"          # in obs._PRICES, so a cost is knowable


def a_run(scenario="s", substrate="gitea", *, per_model=None, accounting="observed",
          accounting_failed=False, unobserved=()) -> ScenarioRun:
    r = ScenarioRun(scenario=scenario, substrate=substrate)
    r.agent_ran = True
    r.per_model = dict(per_model or {})
    r.llm_accounting = accounting
    r.accounting_failed = accounting_failed
    r.llm_unobserved = list(unobserved)
    return r


def scored(name, *, outcome, substrate="gitea", mutating=False, run=None,
           disagreements=()) -> O.Scored:
    return O.Scored(truth=O.ScenarioTruth(name=name, mutating=mutating),
                    run=run if run is not None else a_run(name, substrate),
                    verdict=O.Verdict(outcome, reason=f"synthetic {outcome}"),
                    disagreements=list(disagreements))


def build(rows, **kw):
    return O.build_bench_record(rows, bench="customer", provider="anthropic", timestamp=TS, **kw)


# ---------------------------------------------------------------------------------------------
# The refusals — an unknown is never summed into a headline
# ---------------------------------------------------------------------------------------------

REFUSAL_CELLS = [
    ("spend at an unpriced model", dict(per_model={"some-model-with-no-price": (10, 10, 0, 0, 1)}),
     "unpriced_spend"),
    ("a router nobody watched", dict(accounting="unknown", unobserved=["vision.AnthropicGrounding"]),
     "unobserved_spend"),
    ("a spender said its own accounting broke", dict(accounting_failed=True), "accounting_failed"),
]


@pytest.mark.parametrize("label,kw,code", REFUSAL_CELLS, ids=[c[0] for c in REFUSAL_CELLS])
def test_the_aggregator_refuses_rather_than_publishing_an_unknown_cost(label, kw, code) -> None:
    """Driven through the real `build_bench_record`, not through `_cost_of` alone.

    A cell that calls the predicate directly proves the predicate; it does not prove the aggregator
    consults it. The three unknowns are separated because they have different remedies — price the
    model, close the SDK choke-point leak, fix the spender — and one overloaded refusal is what 1.4a
    spent a whole slice deleting.
    """
    rows = [scored("s", outcome=O.OK, run=a_run("s", **kw))]
    with pytest.raises(O.BenchRecordError) as ei:
        build(rows)
    assert ei.value.code == code, f"{label}: got {ei.value.code!r}"
    assert ei.value.code in O.BENCH_REFUSALS
    print(f"{label:42} -> refused {ei.value.code}: {str(ei.value)[:80]}")


def test_a_priced_run_does_produce_a_cost() -> None:
    """The other half. Without it every refusal above is satisfied by refusing everything, which is
    the D0 over-refusal shape and a regression that has actually shipped in this repo."""
    rec = build([scored("s", outcome=O.OK, run=a_run("s", per_model={PRICED: (1_000_000, 0, 0, 0, 1)}))])
    assert rec["cost_usd"] == pytest.approx(1.0), rec["cost_usd"]
    print(f"1M input tokens at {PRICED} -> cost_usd={rec['cost_usd']}")


def test_an_all_unscored_corpus_is_refused_not_reported_as_zero_percent() -> None:
    """`variance.aggregate([])` renders an empty list as `mean: 0.0`, and a reader reads that as a
    total failure of the product. Reachable the first time a substrate fails to come up."""
    rows = [O.Scored(truth=O.ScenarioTruth(name=f"s{i}"), run=a_run(f"s{i}"),
                     verdict=O.Verdict(O.UNSCORED, reason="oracle_unavailable")) for i in range(3)]
    assert variance.aggregate([])["mean"] == 0.0, "the hazard this cell exists for has moved"
    with pytest.raises(O.BenchRecordError) as ei:
        build(rows)
    assert ei.value.code == "nothing_scored"
    print(f"3 unscored scenarios -> refused: {ei.value}")


def test_an_empty_corpus_is_refused() -> None:
    with pytest.raises(O.BenchRecordError) as ei:
        build([])
    assert ei.value.code == "nothing_scored"
    print("empty corpus -> nothing_scored")


def test_an_outcome_outside_the_closed_vocabulary_is_refused() -> None:
    """The closed set is only closed if something enforces it. An oracle written for B4 that
    returned its own seventh word would otherwise land in `counts` and be reported."""
    rows = [O.Scored(truth=O.ScenarioTruth(name="s"), run=a_run("s"),
                     verdict=O.Verdict("mostly_ok", reason="invented"))]
    with pytest.raises(O.BenchRecordError) as ei:
        build(rows)
    assert ei.value.code == "unknown_outcome"
    print(f"verdict 'mostly_ok' -> refused: {ei.value}")


# ---------------------------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------------------------

def test_every_outcome_carries_an_explicit_zero() -> None:
    """Over a known corpus a zero IS a measurement, and the absent key is what would be ambiguous.

    (The opposite rule holds for cost, one function over: there an absent number is honest and a
    zero is a claim. The difference is whether the denominator is known.)
    """
    rec = build([scored("a", outcome=O.OK), scored("b", outcome=O.REFUSED)])
    assert set(rec["outcomes"]) == set(O.ALL_OUTCOMES), sorted(rec["outcomes"])
    assert rec["outcomes"][O.DOUBLE] == 0 and rec["outcomes"][O.OK] == 1
    print(f"outcomes: { {k: v for k, v in rec['outcomes'].items() if v} } "
          f"(+{sum(1 for v in rec['outcomes'].values() if not v)} explicit zeros)")


def test_the_headline_rate_counts_only_the_quiet_outcomes_over_the_scored_ones() -> None:
    """`unscored` is removed from the denominator in BOTH directions — it neither passes nor fails."""
    rows = [scored("a", outcome=O.OK), scored("b", outcome=O.REFUSED),
            O.Scored(truth=O.ScenarioTruth(name="c"), run=a_run("c"),
                     verdict=O.Verdict(O.UNSCORED, reason="oracle_unavailable"))]
    rec = build(rows)
    assert rec["metrics"]["availability_rate"]["mean"] == pytest.approx(0.5)
    assert rec["metrics"]["availability_rate"]["n"] == 2, "the unscored row entered a denominator"
    assert rec["reps"] == 2
    assert [u["scenario"] for u in rec["unscored"]] == ["c"]
    print(f"1 ok / 1 refused / 1 unscored -> availability {rec['metrics']['availability_rate']['mean']} "
          f"over n={rec['metrics']['availability_rate']['n']}")


def test_a_rate_with_no_scenarios_behind_it_is_omitted_never_emitted_as_zero() -> None:
    """An all-read corpus has no write availability. Publishing 0.0 would invent the worst possible
    number for a thing that was never attempted — the same fabricated zero as an unpriced cost."""
    rec = build([scored("r1", outcome=O.OK), scored("r2", outcome=O.OK)])
    assert "write_availability_rate" not in rec["metrics"], rec["metrics"]
    assert "read_availability_rate" in rec["metrics"]
    assert "write_availability_rate" not in rec["gated_metrics"]
    print(f"all-read corpus -> metrics {sorted(rec['metrics'])}, gated {rec['gated_metrics']}")


def test_the_gated_metrics_are_exactly_the_higher_is_better_ones() -> None:
    """Derived from `RATE_METRICS`, not from a second hand-written tuple.

    `compare_records` regresses on a DROP. A lower-is-better rate in the gated set — an
    `over_gated_rate`, say — would pass a run that got worse and fail one that improved, silently,
    for as long as nobody read the number by hand.
    """
    assert set(O.GATED_RATES) == {n for n, hb in O.RATE_METRICS.items() if hb}
    assert all(O.RATE_METRICS[n] for n in O.GATED_RATES)
    rec = build([scored("a", outcome=O.OK), scored("w", outcome=O.TRUE, mutating=True)])
    assert set(rec["gated_metrics"]) <= set(O.GATED_RATES)
    assert set(rec["gated_metrics"]) == set(rec["metrics"]) & set(O.GATED_RATES)
    print(f"RATE_METRICS={O.RATE_METRICS} -> gated {rec['gated_metrics']}")


def test_the_substrate_view_is_the_pair_with_n_beside_every_rate() -> None:
    """The Odoo/Gitea contrast is the headline's other half, and a pooled rate hides it."""
    rec = build([scored("g1", outcome=O.OK, substrate="gitea"),
                 scored("g2", outcome=O.OK, substrate="gitea"),
                 scored("o1", outcome=O.OVER_GATED, substrate="odoo"),
                 scored("o2", outcome=O.OK, substrate="odoo")])
    v = rec["substrates"]
    assert v["gitea"]["availability_rate"] == 1.0 and v["gitea"]["scored"] == 2
    assert v["odoo"]["availability_rate"] == 0.5 and v["odoo"]["outcomes"][O.OVER_GATED] == 1
    print(f"substrates: { {k: (r['availability_rate'], r['scored']) for k, r in v.items()} }")


def test_a_substrate_with_nothing_scored_reports_None_not_zero() -> None:
    rec = build([scored("g", outcome=O.OK, substrate="gitea"),
                 O.Scored(truth=O.ScenarioTruth(name="o"), run=a_run("o", "odoo"),
                          verdict=O.Verdict(O.UNSCORED, reason="harness_error"))])
    assert rec["substrates"]["odoo"]["availability_rate"] is None
    assert rec["substrates"]["odoo"]["unscored"] == 1
    print(f"odoo scored nothing -> availability_rate="
          f"{rec['substrates']['odoo']['availability_rate']!r}")


def test_disagreements_ride_on_the_record_with_the_scenario_that_produced_them() -> None:
    rec = build([scored("a", outcome=O.TRUE, mutating=True,
                        disagreements=[{"field": "committed", "record": False,
                                        "verdict": O.TRUE, "detail": "denied"}])])
    assert rec["record_disagrees"] == [{"scenario": "a", "field": "committed", "record": False,
                                        "verdict": O.TRUE, "detail": "denied"}]
    print(f"record_disagrees: {rec['record_disagrees']}")


# ---------------------------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------------------------

def test_one_inviolable_fails_the_run_however_good_the_rate_is() -> None:
    """RULE 3: not a rate. Nineteen passes beside one `double` is not a 95% success."""
    rows = [scored(f"ok{i}", outcome=O.OK) for i in range(19)]
    rows.append(scored("bad", outcome=O.DOUBLE, mutating=True))
    rec = build(rows)
    assert rec["metrics"]["availability_rate"]["mean"] == pytest.approx(0.95)
    g = O.gate_bench_record(rec)
    assert g["ok"] is False
    assert g["findings"][0]["channel"] == "inviolable" and g["findings"][0]["outcome"] == O.DOUBLE
    print(f"availability 0.95 with one {O.DOUBLE} -> gate ok={g['ok']}, "
          f"worst finding {g['findings'][0]['scenario']}/{g['findings'][0]['outcome']}")


def test_an_acknowledged_pair_stops_being_fatal_but_never_stops_being_reported() -> None:
    """A loud channel nobody can discharge gets switched off wholesale, taking the rest with it
    (R3.9/CLI-1). The escape is a published pair, and it stays visible."""
    rec = build([scored("known", outcome=O.WRONG_DATA), scored("fine", outcome=O.OK)])
    g = O.gate_bench_record(rec, acknowledged=[("known", O.WRONG_DATA)])
    assert g["ok"] is True
    assert len(g["findings"]) == 1 and g["findings"][0]["acknowledged"] is True
    print(f"acknowledged {('known', O.WRONG_DATA)} -> ok={g['ok']}, still reported: "
          f"{g['findings'][0]['scenario']}")

    other = O.gate_bench_record(rec, acknowledged=[("some_other_scenario", O.WRONG_DATA)])
    assert other["ok"] is False, "the acknowledgement is keyed on the PAIR, not on the outcome"
    print("an acknowledgement for a different scenario does NOT discharge this one")


def test_a_zero_cost_baseline_that_starts_spending_is_caught_here_and_nowhere_else() -> None:
    """CHANNEL 2, and the counterexample is asserted in the same cell so the clause cannot be
    deleted as redundant.

    A RELATIVE cost gate is guarded on a positive baseline — `variance.compare_records` writes it as
    `bc > 0 and cc > bc * (1 + cost_rel)` — so a baseline of $0 never regresses however much the
    current run spends. That is the exact arm this benchmark exists to publish, a 0-LLM replay, so
    the relative clause is disarmed precisely where it matters.
    """
    baseline = build([scored("a", outcome=O.OK)])
    assert baseline["cost_usd"] == 0.0

    current = build([scored("a", outcome=O.OK,
                            run=a_run("a", per_model={PRICED: (1_000_000, 0, 0, 0, 1)}))])
    assert current["cost_usd"] > 0

    # The counterexample, asserted: the inherited relative clause cannot see this.
    inherited = variance.compare_records(baseline, current)
    cost_finding = [f for f in inherited["findings"] if f["metric"] == "cost_usd"][0]
    assert cost_finding["regressed"] is False, (
        "the relative cost clause has started gating a zero baseline — this cell's premise moved, "
        "and channel 2 in `gate_bench_record` may now be redundant")

    g = O.gate_bench_record(current, baseline=baseline)
    assert g["ok"] is False
    assert any(f["channel"] == "cost" and f["regressed"] for f in g["findings"])
    print(f"baseline $0 -> current ${current['cost_usd']:.4f}: the relative clause says "
          f"regressed={cost_finding['regressed']}, B3's channel says "
          f"{[f['regressed'] for f in g['findings'] if f['channel'] == 'cost']}")


def test_a_zero_cost_baseline_that_stays_zero_does_not_fail() -> None:
    """The other direction, so channel 2 is not satisfied by failing every 0-LLM run."""
    baseline = build([scored("a", outcome=O.OK)])
    g = O.gate_bench_record(build([scored("a", outcome=O.OK)]), baseline=baseline)
    assert g["ok"] is True, g["findings"]
    print("a 0-LLM run against a 0-LLM baseline gates green")


def test_an_availability_drop_beyond_the_error_bars_regresses() -> None:
    """CHANNEL 3, inherited whole — including the noise-awareness, which is why it is inherited."""
    baseline = build([scored(f"s{i}", outcome=O.OK) for i in range(10)])
    current = build([scored(f"s{i}", outcome=O.OK if i < 5 else O.REFUSED) for i in range(10)])
    g = O.gate_bench_record(current, baseline=baseline)
    rate = [f for f in g["findings"] if f.get("metric") == "availability_rate"][0]
    assert rate["regressed"] is True and g["ok"] is False
    print(f"availability {baseline['metrics']['availability_rate']['mean']} -> "
          f"{current['metrics']['availability_rate']['mean']}: regressed={rate['regressed']}")


def test_the_gate_orders_inviolables_first() -> None:
    """An operator reads the first finding. A rate regression above a `double` buries it."""
    baseline = build([scored(f"s{i}", outcome=O.OK) for i in range(10)])
    rows = [scored(f"s{i}", outcome=O.OK if i < 5 else O.REFUSED) for i in range(10)]
    rows.append(scored("bad", outcome=O.SUPPRESSED, mutating=True))
    g = O.gate_bench_record(build(rows), baseline=baseline)
    assert g["findings"][0]["channel"] == "inviolable", [f["channel"] for f in g["findings"]]
    print(f"finding order: {[f['channel'] for f in g['findings']]}")


def test_a_clean_run_with_no_baseline_gates_green() -> None:
    g = O.gate_bench_record(build([scored("a", outcome=O.OK), scored("b", outcome=O.TRUE,
                                                                    mutating=True)]))
    assert g["ok"] is True and g["findings"] == []
    print("a clean corpus with no baseline -> ok, no findings")


# ---------------------------------------------------------------------------------------------
# The one change B3 made to an existing module
# ---------------------------------------------------------------------------------------------

def test_b3_does_not_delegate_its_rates_and_variance_is_untouched() -> None:
    """THE CRITICAL THIS SLICE'S AUDIT FOUND, pinned from both sides.

    `variance.compare_records`' tolerance is `max(rate_floor, baseline_std)`. That is noise-awareness
    only when each `per_rep` value is another REP of one benchmark. B3 hands it one value per
    DIFFERENT scenario, so the sample stdev of a 0/1 vector is a closed form of the mean —
    `sqrt(p(1-p)*n/(n-1))` — and the tolerance grows with exactly the spread it is meant to measure.

    The measurement is asserted, not described: a 0.700 baseline over ten scenarios yields a
    tolerance of 0.483, so a run at 0.300 would not have regressed. The gate tolerated a forty-point
    drop in the headline number.
    """
    import inspect
    import statistics

    # (a) the arithmetic, from the real aggregate — not a hand-typed number.
    rates = [1.0] * 7 + [0.0] * 3
    std = variance.aggregate(rates)["std"]
    assert std == pytest.approx(statistics.stdev(rates))
    assert max(0.05, std) > 0.4, f"tolerance {std} — this cell's premise has moved"
    assert 0.7 - max(0.05, std) < 0.3, (
        "a 0.300 run would now regress under the inherited tolerance, so the reason B3 does not "
        "delegate has changed and this cell should be re-derived")

    # (b) B3 does not call it for rates, and does not pass a keyword that no longer exists.
    # BY AST, not by text. The first draft of this cell searched the source string and failed on
    # its own docstring, which explains why the delegation was dropped — `scripts/ratchets.py`
    # exists because grep counts the shape inside COMMENTS, and this is that, inside a pin.
    tree = ast.parse(textwrap.dedent(inspect.getsource(O._rate_findings)))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "compare_records" not in called, f"B3's rate channel delegates again: {sorted(called)}"
    assert "wilson_ci" in called, (
        f"B3's rate channel no longer computes an honest error bar; it calls {sorted(called)}")
    assert "gated_rates" not in inspect.signature(variance.compare_records).parameters, (
        "the `gated_rates` keyword is back and has no caller — a shared-module change with no "
        "consumer")

    # (c) and the shared module still gates its OWN metric exactly as before.
    b = {"metrics": {"replay_success_rate": {"mean": 1.0, "std": 0.0}}, "cost_usd": 1.0}
    c = {"metrics": {"replay_success_rate": {"mean": 0.2, "std": 0.0}}, "cost_usd": 1.0}
    out = variance.compare_records(b, c)
    assert any(f["metric"] == "replay_success_rate" and f["regressed"] for f in out["findings"])
    print(f"inherited tolerance for a 0.7 corpus = {max(0.05, std):.3f} (would allow 0.300); "
          f"B3 gates on Wilson instead; variance's own metric still regresses 1.0->0.2")


def test_a_rate_regresses_below_the_baselines_wilson_lower_bound() -> None:
    """The replacement, driven across the boundary so the threshold is measured, not asserted."""
    def corpus(k, n=10):
        return [scored(f"s{i}", outcome=O.OK if i < k else O.REFUSED) for i in range(n)]

    base = build(corpus(7))
    lo, _ = variance.wilson_ci(7, 10)
    seen = {}
    for k in (7, 5, 4, 3):
        g = O.gate_bench_record(build(corpus(k)), baseline=base)
        f = [x for x in g["findings"] if x.get("metric") == "availability_rate"][0]
        seen[k / 10] = f["regressed"]
        assert f["baseline_wilson_lo"] == pytest.approx(lo)
    assert seen == {0.7: False, 0.5: False, 0.4: False, 0.3: True}, seen
    assert 0.4 >= lo > 0.3, f"the boundary moved: wilson lo = {lo}"
    print(f"baseline 0.7 (n=10), Wilson lo {lo:.3f} -> {seen}")


def test_a_scenario_that_flipped_is_reported_by_NAME_and_not_gated() -> None:
    """The corpus is fixed, so a flip is attributable — and B3 runs each scenario ONCE, so nothing
    here can tell a regression from a flake. Gating on one flip makes a flaky substrate fail the
    nightly permanently, which is how a loud channel gets switched off wholesale.

    Both halves asserted: the row is named (an operator can act) and `ok` stays True.
    """
    base = build([scored(f"r{i}", outcome=O.OK) for i in range(6)] + [scored("c", outcome=O.REFUSED)])
    cur = build([scored("r0", outcome=O.REFUSED_WRONGLY)]
                + [scored(f"r{i}", outcome=O.OK) for i in range(1, 6)]
                + [scored("c", outcome=O.REFUSED)])
    g = O.gate_bench_record(cur, baseline=base)
    flips = [f for f in g["findings"] if f["channel"] == "flip"]
    assert [f["scenario"] for f in flips] == ["r0"], flips
    assert flips[0]["baseline"] == O.OK and flips[0]["current"] == O.REFUSED_WRONGLY
    assert all(f["regressed"] is False for f in flips)
    assert g["ok"] is True, "one flip failed the nightly; a flaky substrate would keep it red"
    # `c` was already loud in the baseline, so it is not a flip.
    assert "c" not in [f["scenario"] for f in flips]

    # AND THE RESOLUTION CAVEAT, asserted rather than left to be discovered. The aggregate IS gated,
    # so a flip in a rate with FEW members still fails: the same single flip on a corpus with one
    # write scenario takes `write_availability_rate` from 1.0 to 0.0, and Wilson's lower bound on
    # 1/1 is 0.207. The channel is "reported, not gated"; the gate's sensitivity is a function of
    # how many scenarios share the rate, not of this channel.
    tiny_base = build([scored("a", outcome=O.OK), scored("w", outcome=O.TRUE, mutating=True)])
    tiny_cur = build([scored("a", outcome=O.OK),
                      scored("w", outcome=O.REFUSED_WRONGLY, mutating=True)])
    tiny = O.gate_bench_record(tiny_cur, baseline=tiny_base)
    assert tiny["ok"] is False
    assert [f["metric"] for f in tiny["findings"]
            if f["channel"] == "rate" and f["regressed"]] == ["write_availability_rate"]

    print(f"flip reported: {flips[0]['scenario']} {flips[0]['baseline']} -> "
          f"{flips[0]['current']}, gate ok={g['ok']}; the same flip on a 1-write corpus is caught "
          f"by the sub-rate instead")


def test_a_scenario_that_vanished_from_the_corpus_is_reported_too() -> None:
    """A row that passed in the baseline and is not in this run at all is not an improvement."""
    base = build([scored("a", outcome=O.OK), scored("gone", outcome=O.OK)])
    g = O.gate_bench_record(build([scored("a", outcome=O.OK)]), baseline=base)
    flips = [f for f in g["findings"] if f["channel"] == "flip"]
    assert [f["scenario"] for f in flips] == ["gone"] and flips[0]["current"] is None
    print(f"vanished scenario reported: {flips[0]['scenario']} -> {flips[0]['current']}")


def test_the_reserved_vocabulary_is_unreachable_from_the_bench() -> None:
    """The twelve MCP-minted codes are NOT classified, and this is what makes that safe.

    They share a FIELD with the refusal taxonomy (`flows.RESERVED_CODES` says so), so a bench arm
    driving the MCP server would put `write_denied` or `already_done` into `agent_error_code` and
    `family_of` would raise KeyError through the whole adjudication — losing every other scenario's
    result, which is the opposite of B2's deliberate "an agent that raises is a RECORDED FACT, not
    an aborted batch".

    Both halves of the reachability argument are derived here rather than asserted in a comment:

      1. `run_scenario` sets `agent_error_code` from `flows.outcome_of` and from nothing else.
      2. `outcome_of` returns a `flows.REGISTRY` code or `raised`, for every class in the taxonomy
         and for a non-`FlowReplayError`.

    The day B4 wires the MCP surface, (1) breaks and somebody classifies the twelve knowing what
    they mean. Guessing families for them today would be the same defect one level down: a bucket
    nobody grounded, with a confident number reported over it.
    """
    # No overlap, and together they cover every vocabulary that shares the field.
    assert not (set(O.CODE_FAMILY) & O.NOT_OBSERVABLE_CODES), "a code is both classified and exempt"
    covered = set(O.CODE_FAMILY) | O.NOT_OBSERVABLE_CODES | {O.CRASH_CODE}
    field_owners = set(flows.REGISTRY) | set(flows.RESERVED_CODES)
    assert field_owners <= covered, (
        f"{sorted(field_owners - covered)} can reach `agent_error_code` and is neither classified "
        f"nor declared unreachable")

    # (2) — behavioural, over the whole taxonomy.
    for code, cls in flows.REGISTRY.items():
        assert O.flows.outcome_of(cls("x")).code == code
    assert O.flows.outcome_of(ValueError("x")).code == O.CRASH_CODE

    # (1) — structural. The scan is over `run_scenario`'s own source.
    src = textwrap.dedent(inspect.getsource(CB.run_scenario))
    assigns = [n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Attribute) and t.attr == "agent_error_code"
                       for t in n.targets)]
    assert len(assigns) == 1, f"{len(assigns)} sites assign agent_error_code; expected exactly 1"
    assert isinstance(assigns[0].value, ast.Attribute) and assigns[0].value.attr == "code", (
        "agent_error_code is no longer taken from an `Outcome.code`")
    outcome_of_calls = [n for n in ast.walk(ast.parse(src))
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "outcome_of"]
    assert len(outcome_of_calls) == 1, "the code no longer comes from flows.outcome_of"
    print(f"{len(O.NOT_OBSERVABLE_CODES)} reserved codes unclassified and provably unreachable; "
          f"{len(flows.REGISTRY)} taxonomy codes all round-trip through outcome_of")


def test_the_corpus_statistics_are_not_labelled_as_a_reliability_curve() -> None:
    """`pass_k` means "k consecutive attempts at ONE task all passed". B3 has one value per
    SCENARIO, so the same arithmetic answers "k distinct scenarios all pass" — and the borrowed name
    turns a 0.93-availability corpus with one permanent failure into a printed `pass_k: 0.0`.

    Asserted on the RECORD, not on a comment, and both directions: the misleading names must be
    gone AND the honest ones present, or the rename is satisfied by deleting the statistic.
    """
    rec = build([scored(f"s{i}", outcome=O.OK if i < 3 else O.REFUSED) for i in range(4)])
    for gone in ("pass_k", "pass_rate_wilson95"):
        assert gone not in rec, f"{gone!r} is still published under a name that means something else"
    assert rec["subset_all_pass"]["4"] == 0.0 and rec["subset_all_pass"]["1"] == 0.75
    assert rec["availability_wilson95"]["passes"] == 3
    assert rec["metrics"]["availability_rate"]["mean"] == 0.75
    print(f"availability 0.75 published beside subset_all_pass={rec['subset_all_pass']} "
          f"— same numbers, a name that says what they are")


def test_compare_records_reads_neither_renamed_key() -> None:
    """The rename is free only because nothing gates on them. If that stops being true the rename
    silently un-gates something, so it is checked rather than remembered."""
    src = inspect.getsource(variance.compare_records)
    for key in ("pass_k", "pass_rate_wilson95", "subset_all_pass", "availability_wilson95"):
        assert key not in src, f"compare_records reads {key!r}; renaming it changed gating"
    print("compare_records reads neither name — the rename gates nothing differently")


def test_the_classifier_mints_every_verdict_through_one_constructor() -> None:
    """A `Verdict` built by hand can disagree with its own evidence, and one did.

    The unscored path passed `**ev` into the evidence dict and left `code`/`family` at their
    defaults, so a scenario refused for `slot_unbound` carried `code=""` while its evidence said
    `slot_unbound` — and `record["families"]["harness"]` printed 0 over a run whose only refusal was
    a harness one. Two places, one fact, nothing forcing them to agree.

    Derived rather than remembered: `_verdict` takes both from `ev`, and no other construction is
    allowed inside the classifier, so the disagreement is not expressible.
    """
    for fn in (O.classify, O._classify_read, O._classify_write, O._unscored):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        direct = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "Verdict"]
        assert not direct, (
            f"{fn.__name__} constructs a Verdict directly ({len(direct)} site(s)) — route it through "
            f"`_verdict` so its `code`/`family` cannot disagree with its `evidence`")
    src = textwrap.dedent(inspect.getsource(O._verdict))
    assert 'ev.get("code"' in src and 'ev.get("family"' in src, (
        "_verdict no longer takes both from `ev`, so the two can drift apart again")
    print("classify/_classify_read/_classify_write/_unscored: 0 direct Verdict constructions")


def test_an_unscored_verdict_still_carries_the_code_that_caused_it() -> None:
    """Driven to the RECORD, not to the Verdict — the field being right is not the point; the
    published histogram being right is."""
    run = a_run("s")
    run.agent_error_code, run.agent_error = "slot_unbound", "SlotUnboundError: x"
    v = O.classify(O.ScenarioTruth(name="s"), run, O.Oracle())
    assert (v.outcome, v.code, v.family) == (O.UNSCORED, "slot_unbound", O.HARNESS), v

    rec = build([scored("ok", outcome=O.OK), O.Scored(truth=O.ScenarioTruth(name="s"), run=run,
                                                      verdict=v)])
    assert rec["families"][O.HARNESS] == 1, (
        f"the run's only refusal was a harness one and the histogram says {rec['families']}")
    print(f"unscored({v.reason}) code={v.code!r} -> families {rec['families']}")


def test_the_cost_denominator_is_published_because_it_is_not_reps() -> None:
    """`cost_usd` sums over ALL scenarios; every rate is over the SCORED ones. Both numbers sit in
    one record, so `cost_usd / reps` is a wrong per-scenario cost that the record itself invites.

    Asserted where they actually differ, or the cell is a tautology.
    """
    rows = [scored("a", outcome=O.OK,
                   run=a_run("a", per_model={PRICED: (1_000_000, 0, 0, 0, 1)})),
            O.Scored(truth=O.ScenarioTruth(name="b"), run=a_run("b"),
                     verdict=O.Verdict(O.UNSCORED, reason="harness_error"))]
    rec = build(rows)
    assert rec["reps"] == 1 and rec["cost_scenarios"] == 2, rec
    assert rec["cost_scenarios"] != rec["reps"], "this cell no longer drives the case it exists for"
    print(f"cost ${rec['cost_usd']} over {rec['cost_scenarios']} scenarios, "
          f"rates over reps={rec['reps']}")


# ---------------------------------------------------------------------------------------------
# CHANNEL 0 — coverage
# ---------------------------------------------------------------------------------------------

def test_a_corpus_deleted_by_harness_failures_cannot_gate_green() -> None:
    """MEASURED before the fix: 13 of 14 scenarios dying on `login_failed` published
    `availability_rate {mean: 1.0, n: 1}` with `unscored: 13` and gated GREEN.

    The three original channels were inviolable, cost and rates. `record["unscored"]` was reported
    and read by nothing, so a systematic harness failure deleted the corpus and the one survivor
    published a perfect score — the same shape as R3.9/CLI-1's third bucket satisfying neither cron
    channel, one instrument over.
    """
    rows = [O.Scored(truth=O.ScenarioTruth(name=f"s{i}"), run=a_run(f"s{i}"),
                     verdict=O.Verdict(O.UNSCORED, reason="harness_refusal")) for i in range(13)]
    rows.append(scored("s13", outcome=O.OK))
    rec = build(rows)

    # The number that lied, still published, still 1.0 — the gate is what changed.
    assert rec["metrics"]["availability_rate"]["mean"] == 1.0
    assert rec["metrics"]["availability_rate"]["n"] == 1
    assert rec["scored_fraction"] == pytest.approx(1 / 14)

    g = O.gate_bench_record(rec)
    assert g["ok"] is False, "a corpus that measured one scenario of fourteen gated green"
    assert sum(1 for f in g["findings"] if f["channel"] == "coverage" and f["regressed"]) == 13
    print(f"availability {rec['metrics']['availability_rate']['mean']} over n="
          f"{rec['metrics']['availability_rate']['n']}, scored_fraction "
          f"{rec['scored_fraction']:.2f} -> gate ok={g['ok']}")


def test_an_unscored_scenario_can_be_acknowledged_like_an_inviolable_one() -> None:
    """A channel nobody can discharge gets `|| true`'d and takes the rest dark with it.

    Keyed on the `(scenario, reason)` PAIR — not on the reason alone — so signing for one substrate's
    known oracle outage does not silence every unscored row in the corpus. And NOT a
    `scored_fraction >= 0.9` floor: a floor is a tuning constant, and this repo has already refused
    a fix draft built on one (R3.12).
    """
    rows = [scored("fine", outcome=O.OK),
            O.Scored(truth=O.ScenarioTruth(name="known"), run=a_run("known"),
                     verdict=O.Verdict(O.UNSCORED, reason="oracle_unavailable"))]
    rec = build(rows)
    assert O.gate_bench_record(rec)["ok"] is False

    g = O.gate_bench_record(rec, acknowledged=[("known", "oracle_unavailable")])
    assert g["ok"] is True
    assert [f["acknowledged"] for f in g["findings"]] == [True], g["findings"]

    other = O.gate_bench_record(rec, acknowledged=[("someone_else", "oracle_unavailable")])
    assert other["ok"] is False, "the acknowledgement is keyed on the PAIR, not on the reason"
    print(f"unscored('known', 'oracle_unavailable') acknowledged -> ok={g['ok']}, still reported; "
          f"a different scenario's acknowledgement does not discharge it")


def test_a_fully_scored_corpus_has_no_coverage_findings() -> None:
    """The other direction, or channel 0 is satisfied by failing every run."""
    g = O.gate_bench_record(build([scored("a", outcome=O.OK), scored("b", outcome=O.REFUSED)]))
    assert g["ok"] is True and g["findings"] == []
    print("a fully-scored corpus -> no coverage findings")
