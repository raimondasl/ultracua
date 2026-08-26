"""Every guard in B3's two test modules, mutated and watched go RED.

WHY THIS FILE EXISTS. "A cell that cannot fail is not a test", and this repo has shipped three of
them in one slice while their sibling assertions passed. `tests/test_bench_outcomes.py` and
`tests/test_bench_record.py` are 67 green cells; green is what a vacuous cell looks like too. So
each guard is paired here with a mutation that violates exactly the property it names, and the pair
FAILS unless the guard notices.

WHY IT IS IN-PROCESS RATHER THAN IN `tests/mutations/`. `scripts/prove_red.py` installs a mutant by
putting a copy of `src/` first on `PYTHONPATH`. `benchmarks/` is imported from the repo ROOT, which
pytest puts at `sys.path[0]` ahead of `PYTHONPATH` — so a mutant of `benchmarks/outcomes.py` is
never the module the tests import, and every mutation would be reported as a SURVIVOR while the
guard was in fact fine. That is a limit of the instrument, not a hole in the matrix (the same shape
as R4.75), and it is filed. The discipline is kept instead of the file: a mutation is expressed as a
find/replace over the function's OWN SOURCE, and a find-text that no longer matches is an ERROR
rather than a pass, because a stale mutation silently reports the suite as stronger than it is.
"""

from __future__ import annotations

import inspect
import textwrap

import pytest

from benchmarks import customer_bench as CB
from benchmarks import outcomes as O
from benchmarks import corpus as C
from benchmarks import scored_run as SR
from tests import _arming
from tests._arming import assert_red
import tests.test_bench_outcomes as TO
import tests.test_scored_run as TSR
import tests.test_bench_record as TR
import tests.test_login_discrimination as TLD
import tests.test_search_premise as TSP
import tests.test_no_actions_outcome as TNA


# ---------------------------------------------------------------------------------------------
# the harness
# ---------------------------------------------------------------------------------------------

# THE HARNESS ITSELF LIVES IN `tests/_arming.py`, because 1.3 needs it for `benchmarks/variance.py`
# and two copies of one harness is how a harness drifts — every lesson in that file was paid for by a
# false verdict here. These wrappers bind the module so the ~30 call sites below keep their shape.

def mutate_function(monkeypatch, name: str, find: str, repl: str) -> None:
    _arming.mutate_function(monkeypatch, O, name, find, repl)


def mutate_value(monkeypatch, name: str, value) -> None:
    _arming.mutate_value(monkeypatch, O, name, value)


def mutate_scored_run(monkeypatch, name: str, find: str, repl: str) -> None:
    """The same harness aimed at `benchmarks/scored_run.py` — the RUNNER, not the vocabulary."""
    _arming.mutate_function(monkeypatch, SR, name, find, repl)


def mutate_corpus_value(monkeypatch, name: str, value) -> None:
    """Rebind a constant in `benchmarks/corpus.py`.

    `SEARCH_TERM` is the only one so far, and mutating it is the honest test: the scenario's GOAL is
    an f-string evaluated at import, so rebinding the constant afterwards leaves the goal frozen —
    which is precisely how the two drift apart in real life.
    """
    _arming.mutate_value(monkeypatch, C, name, value)


# ---------------------------------------------------------------------------------------------
# RULE 4 — the partition
# ---------------------------------------------------------------------------------------------

def test_the_totality_pin_notices_a_missing_code(monkeypatch) -> None:
    mutate_value(monkeypatch, "CODE_FAMILY", {k: v for k, v in O.CODE_FAMILY.items() if k != "drift"})
    print(assert_red(TO.test_every_refusal_code_has_exactly_one_family))


def test_the_totality_pin_notices_a_code_that_no_longer_exists(monkeypatch) -> None:
    mutate_value(monkeypatch, "CODE_FAMILY", {**O.CODE_FAMILY, "a_deleted_code": O.PAGE})
    print(assert_red(TO.test_every_refusal_code_has_exactly_one_family))


def test_the_no_default_pin_notices_a_default(monkeypatch) -> None:
    """THE mutation this partition exists to refuse: `.get(code, PAGE)`."""
    mutate_function(monkeypatch, "family_of",
                    "    try:\n        return CODE_FAMILY[code]",
                    "    try:\n        return CODE_FAMILY.get(code, PAGE)")
    print(assert_red(TO.test_family_of_raises_rather_than_defaulting))


def test_the_quiet_allowlist_pin_notices_a_member_being_added(monkeypatch) -> None:
    """The TABLE half. `+{REFUSED}` rather than `+{UNSCORED}`, and the difference is the finding:
    adding `UNSCORED` changes no number anywhere (every consumer filters on `.scored` first), so the
    old mutation was killed only by the cell's own literal restatement. This one moves a rate."""
    mutate_value(monkeypatch, "QUIET_OUTCOMES", frozenset({O.OK, O.TRUE, O.REFUSED}))
    print(assert_red(TO.test_the_quiet_set_is_an_allowlist_and_unscored_is_not_in_it))


def test_the_availability_numerator_pin_notices_a_loud_outcome_being_promoted(monkeypatch) -> None:
    """The BEHAVIOURAL half — this is what `QUIET_OUTCOMES` is load-bearing for."""
    mutate_value(monkeypatch, "QUIET_OUTCOMES", frozenset({O.OK, O.TRUE, O.REFUSED}))
    print(assert_red(TO.test_a_loud_outcome_is_never_counted_as_available))


def test_the_assignment_table_notices_a_code_changing_family(monkeypatch) -> None:
    """Measured: `escalate` moved into HARNESS lifted availability 0.75 -> 1.00 with every cell in
    the slice green, because HARNESS deletes the scenario from the denominator."""
    mutate_value(monkeypatch, "CODE_FAMILY", {**O.CODE_FAMILY, "escalate": O.HARNESS})
    print(assert_red(TO.test_the_family_of_every_code_is_the_one_this_table_says))


def test_the_assignment_measurement_notices_the_deletion_it_describes(monkeypatch) -> None:
    """The cell that MEASURES why the table matters must go red when the measurement stops holding,
    or it is a comment with an assert in it."""
    mutate_value(monkeypatch, "CODE_FAMILY", {**O.CODE_FAMILY, "escalate": O.HARNESS,
                                              "auth_expired": O.HARNESS})
    print(assert_red(TO.test_moving_escalate_or_auth_expired_into_HARNESS_deletes_the_headline))


def test_the_cross_axis_pin_notices_a_post_actuation_code_in_the_write_gate(monkeypatch) -> None:
    """WRITE_GATE claims "refuses BEFORE firing". `write_readback` declares `landed=True`."""
    mutate_value(monkeypatch, "CODE_FAMILY", {**O.CODE_FAMILY, "write_readback": O.WRITE_GATE})
    print(assert_red(TO.test_the_two_cross_axis_properties_the_families_claim_are_DERIVED))


def test_the_safety_row_split_notices_refused_correctly_back_in_availability(monkeypatch) -> None:
    """It scored 0.0 on the headline for the product doing exactly the right thing."""
    mutate_function(monkeypatch, "build_bench_record",
                    "    task = lambda s: not s.truth.expect_refusal",
                    "    task = lambda s: True")
    print(assert_red(TR.test_a_safety_row_leaves_the_availability_rates_and_gets_its_own))


def test_the_gate_holds_rate_notices_a_landed_gate_row_scoring_as_a_hold(monkeypatch) -> None:
    mutate_function(monkeypatch, "_gate_holds_values",
                    "    return [1.0 if s.verdict.outcome == REFUSED_CORRECTLY else 0.0",
                    "    return [1.0")
    print(assert_red(TR.test_a_safety_row_leaves_the_availability_rates_and_gets_its_own))


def test_the_vanished_rate_pin_notices_the_union_becoming_the_current_set(monkeypatch) -> None:
    """A cohort that goes entirely unscored drops its rate from `gated_metrics`, and deriving the
    comparison set from the current record makes that indistinguishable from a corpus that never
    had the cohort."""
    mutate_function(monkeypatch, "_rate_findings",
                    '    for name in sorted(set(record.get("gated_metrics", ())) | '
                    'set(baseline.get("gated_metrics", ()))):',
                    '    for name in sorted(set(record.get("gated_metrics", ()))):')
    print(assert_red(TR.test_a_cohort_that_went_dark_is_a_regression_not_a_missing_metric))


def test_the_unscored_row_pin_notices_the_code_being_dropped(monkeypatch) -> None:
    """Publishing the refusal only as a message string is the sub-bucketing this slice exists to
    end, and the structured code was already computed."""
    mutate_function(monkeypatch, "build_bench_record",
                    '                        "reason": s.verdict.reason, "code": s.verdict.code,',
                    '                        "reason": s.verdict.reason, "code": "",')
    print(assert_red(TR.test_an_unscored_row_publishes_its_CODE_not_only_a_message))


# ---------------------------------------------------------------------------------------------
# The adjudication order — the load-bearing clause
# ---------------------------------------------------------------------------------------------

# A mutation's REPLACEMENT text can go stale on its own, and `mutate_function` cannot see that: it
# checks the find-text matches exactly once and says nothing about whether what replaces it still
# compiles against the current module. Measured — when `_unscored` gained an explicit `ev` parameter
# this block kept calling it with `**ev` and every cell using it died with `TypeError: _unscored()
# got an unexpected keyword argument 'code'`. `assert_red` refuses a TypeError as a kill for exactly
# that reason, so the layer below caught it; without that clause four mutations would have reported
# as armed while testing the harness's own breakage.
_HARNESS_FIRST = (
    '    if getattr(run, "harness_error", ""):\n'
    '        return _unscored("harness_error", ev, detail=run.harness_error)\n'
    '    if fam in UNSCORED_FAMILIES:\n'
    '        return _unscored("harness_refusal", ev, detail=getattr(run, "agent_error", ""))\n')


@pytest.mark.parametrize("code", ["meta_unwritable", "meta_unreadable", "not_learned"])
def test_the_ordering_pin_notices_the_excuse_moving_in_front(monkeypatch, code) -> None:
    """Move clause 2 ahead of clause 1 — the exact edit the docstring argues against.

    Under it, a `double` the server is holding is dropped from the corpus with the harness's excuse
    written beside it, and appears in no number anywhere.
    """
    mutate_function(monkeypatch, "classify",
                    "    # 1. A VIOLATION THE ORACLE CAN SEE OUTRANKS EVERY EXCUSE. See the docstring.",
                    _HARNESS_FIRST + "    # mutated: the excuse now runs first")
    print(assert_red(TO.test_a_double_the_oracle_can_see_outranks_the_harness_excuse, code))


def test_the_ordering_pin_notices_it_for_a_harness_error_too(monkeypatch) -> None:
    mutate_function(monkeypatch, "classify",
                    "    # 1. A VIOLATION THE ORACLE CAN SEE OUTRANKS EVERY EXCUSE. See the docstring.",
                    _HARNESS_FIRST + "    # mutated: the excuse now runs first")
    print(assert_red(TO.test_a_harness_error_after_the_agent_ran_still_cannot_hide_a_wrong_target))


def test_the_over_refusal_pin_notices_a_harness_refusal_being_scored(monkeypatch) -> None:
    """The other direction. Without this cell the clause above is satisfied by never unscoring
    anything, which puts the bench's own setup bugs into the product's number."""
    mutate_value(monkeypatch, "UNSCORED_FAMILIES", frozenset())
    print(assert_red(TO.test_a_harness_refusal_with_a_clean_server_is_unscored_not_refused))


def test_the_premise_cell_notices_an_empty_population(monkeypatch) -> None:
    """The cell that measures whether the ordering clause has a population at all must itself go red
    when it does not — otherwise it is a comment."""
    mutate_value(monkeypatch, "CODE_FAMILY",
                 {k: (O.PAGE if v == O.HARNESS else v) for k, v in O.CODE_FAMILY.items()})
    print(assert_red(TO.test_three_harness_codes_really_can_follow_an_actuation))


# ---------------------------------------------------------------------------------------------
# The read vocabulary
# ---------------------------------------------------------------------------------------------

def test_the_headline_pin_notices_over_gating_being_inferred(monkeypatch) -> None:
    """Remove the evidence requirement — the bench then flatters its own thesis for free."""
    mutate_function(monkeypatch, "_classify_read",
                    "        if gate is None or not gate.present:",
                    "        if False:")
    print(assert_red(TO.test_over_gated_refuses_to_infer_when_the_recipe_was_not_readable))


def test_the_headline_pin_notices_a_write_gate_code_falling_through_to_refused(monkeypatch) -> None:
    mutate_function(monkeypatch, "_classify_read",
                    "    if fam == WRITE_GATE:",
                    "    if fam == WRITE_GATE and code == 'not_approved':")
    print(assert_red(TO.test_over_gated_fires_for_every_write_gate_code_on_a_read))


def test_the_read_actuation_pin_notices_it_being_filed_as_a_refusal(monkeypatch) -> None:
    mutate_function(monkeypatch, "classify",
                    "        if oracle.unmatched:",
                    "        if oracle.unmatched and truth.mutating:")
    print(assert_red(TO.test_a_read_that_changed_a_record_is_an_incorrect_target))


def test_the_data_wins_cell_notices_the_code_being_consulted_first(monkeypatch) -> None:
    mutate_function(monkeypatch, "_classify_read",
                    "    if oracle.data_correct is True:",
                    "    if oracle.data_correct is True and not code:")
    print(assert_red(TO.test_the_read_vocabulary, *TO.READ_CELLS[-1]))


# ---------------------------------------------------------------------------------------------
# The write vocabulary
# ---------------------------------------------------------------------------------------------

def test_the_silence_pin_notices_a_loud_refusal_being_called_suppressed(monkeypatch) -> None:
    """Inviolable #3's adverb. Drop the code check and a LOUD refusal becomes a violation too."""
    mutate_function(monkeypatch, "_classify_write", "    if not code:", "    if True:")
    print(assert_red(TO.test_suppressed_needs_a_CLAIM_and_not_merely_the_absence_of_a_refusal))


def test_the_ground_truth_pin_notices_correctness_derived_from_the_code(monkeypatch) -> None:
    """Derive "was refusing right" from the refusal and the bench agrees with whatever happened."""
    mutate_function(monkeypatch, "_classify_write",
                    "    correct = truth.expect_refusal",
                    "    correct = fam == WRITE_GATE")
    print(assert_red(TO.test_refused_correctly_comes_from_the_CORPUS_not_from_the_code))


def test_the_double_cell_notices_a_count_of_two_being_scored_as_one(monkeypatch) -> None:
    mutate_function(monkeypatch, "classify",
                    "        if len(oracle.matched) >= 2:",
                    "        if len(oracle.matched) >= 3:")
    print(assert_red(TO.test_the_write_vocabulary, *TO.WRITE_CELLS[1]))


# ---------------------------------------------------------------------------------------------
# RULE 5 — the cross-check
# ---------------------------------------------------------------------------------------------

def test_the_unknown_pin_notices_None_being_made_to_disagree(monkeypatch) -> None:
    """1.4b's tri-state. An unknown that argues with a measurement is a false alarm on the loudest
    channel there is, and a false alarm is how a channel gets switched off."""
    mutate_function(monkeypatch, "cross_check",
                    "    if committed is False and verdict.outcome in (TRUE, DOUBLE, INCORRECT_TARGET):",
                    "    if committed is not True and verdict.outcome in (TRUE, DOUBLE, INCORRECT_TARGET):")
    print(assert_red(TO.test_an_unknown_never_disagrees))


def _classify_that_reads_the_record(truth, run, oracle, gate=None):
    """A `classify` that consults the product's own claim about its own write.

    A REAL function rather than an exec'd mutant, and this cell is where that mattered: the scan it
    faces reads `inspect.getsource`, an exec'd mutant has no retrievable source, and the first
    version of this cell scored an `OSError: could not get source code` as a kill. The scan was
    never reached. That is the arming harness reporting a guard as armed on the strength of its own
    failure to run — the same shape as a stale mutation, one instrument further out.
    """
    committed = getattr(run, "agent_error_landed", False)
    return O.Verdict(O.TRUE if committed else O.SUPPRESSED)


def test_the_isolation_scan_notices_the_record_reaching_classify(monkeypatch) -> None:
    """`reshape-plan.md`'s "do not source outcome from a RunRecord", made checkable."""
    monkeypatch.setattr(O, "classify", _classify_that_reads_the_record)
    print(assert_red(TO.test_classify_never_reads_a_run_record))


def _gutted_cross_check(verdict, record):
    """A `cross_check` that reads nothing off the record — the shape the second scan must catch.

    Defined here as a real function rather than exec'd, because the scan it faces reads
    `inspect.getsource`, and an exec'd mutant has no retrievable source: the scan would die with
    OSError and this harness would score that as a kill it did not earn.
    """
    return []


def test_the_isolation_scans_other_half_notices_cross_check_being_gutted(monkeypatch) -> None:
    """A negative alone is satisfied by DELETING the mechanism.

    `test_classify_never_reads_a_run_record` asserts the record is absent from `classify`. Delete
    `cross_check`'s reads too and that scan is still perfectly green over a benchmark that no longer
    cross-checks anything — which is why the pair exists.
    """
    monkeypatch.setattr(O, "cross_check", _gutted_cross_check)
    print(assert_red(TO.test_cross_check_is_the_only_place_the_record_is_read))


# ---------------------------------------------------------------------------------------------
# The gate evidence
# ---------------------------------------------------------------------------------------------

def test_the_missing_recipe_cell_notices_absence_being_reported_as_zero(monkeypatch) -> None:
    mutate_function(monkeypatch, "GateEvidence",
                    "            return cls(present=False, approved=approved, declares_write=declares)",
                    "            return cls(present=True, mutating_steps=0, approved=approved, "
                    "declares_write=declares)")
    print(assert_red(TO.test_a_missing_recipe_is_not_present_rather_than_zero_mutating_steps))


# ---------------------------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("label,kw,code", TR.REFUSAL_CELLS, ids=[c[0] for c in TR.REFUSAL_CELLS])
def test_the_cost_refusals_notice_a_zero_standing_in_for_an_unknown(monkeypatch, label, kw, code) -> None:
    """The single edit that turns all three honest refusals into a published number."""
    mutate_function(monkeypatch, "_cost_of",
                    '    name = getattr(run, "scenario", "?")',
                    '    return 0.0\n    name = getattr(run, "scenario", "?")')
    print(assert_red(TR.test_the_aggregator_refuses_rather_than_publishing_an_unknown_cost,
                     label, kw, code))


def test_the_priced_cell_notices_the_aggregator_refusing_everything(monkeypatch) -> None:
    """Without this, all three refusal cells are satisfied by refusing every run — the D0 shape."""
    mutate_function(monkeypatch, "_cost_of",
                    '    name = getattr(run, "scenario", "?")',
                    '    name = getattr(run, "scenario", "?")\n'
                    '    raise BenchRecordError("unpriced_spend", f"{name}: mutated")')
    print(assert_red(TR.test_a_priced_run_does_produce_a_cost))


def test_the_empty_corpus_cell_notices_the_live_guard_going_away(monkeypatch) -> None:
    mutate_function(monkeypatch, "build_bench_record", "    if not live:", "    if False:")
    print(assert_red(TR.test_an_all_unscored_corpus_is_refused_not_reported_as_zero_percent))


def test_the_closed_vocabulary_cell_notices_an_absorbing_bucket(monkeypatch) -> None:
    mutate_function(monkeypatch, "build_bench_record",
                    "        if s.verdict.outcome not in counts:",
                    "        if False:")
    print(assert_red(TR.test_an_outcome_outside_the_closed_vocabulary_is_refused))


def test_the_denominator_cell_notices_unscored_rows_entering_it(monkeypatch) -> None:
    mutate_function(monkeypatch, "_rate_values",
                    "            for s in scored if s.verdict.scored and predicate(s)]",
                    "            for s in scored if predicate(s)]")
    print(assert_red(TR.test_the_headline_rate_counts_only_the_quiet_outcomes_over_the_scored_ones))


def test_the_omitted_rate_cell_notices_an_empty_list_becoming_zero(monkeypatch) -> None:
    mutate_function(monkeypatch, "build_bench_record",
                    "    per_rep = {k: v for k, v in per_rep.items() if v}",
                    "    per_rep = dict(per_rep)")
    print(assert_red(TR.test_a_rate_with_no_scenarios_behind_it_is_omitted_never_emitted_as_zero))


def test_the_explicit_zero_cell_notices_sparse_counts(monkeypatch) -> None:
    """Mutate the EMISSION, not the initialisation.

    Emptying `counts` at the top makes the closed-vocabulary check fire instead, and the cell then
    goes red for a property it is not testing — a kill credited to the wrong guard. Dropping the
    zeros on the way out is the faithful expression of "the record became sparse".
    """
    mutate_function(monkeypatch, "build_bench_record",
                    '    rec["outcomes"] = counts',
                    '    rec["outcomes"] = {k: v for k, v in counts.items() if v}')
    print(assert_red(TR.test_every_outcome_carries_an_explicit_zero))


def test_the_direction_cell_notices_a_lower_is_better_rate_being_gated(monkeypatch) -> None:
    """A gated `over_gated_rate` would pass a run that got worse and fail one that improved."""
    mutate_value(monkeypatch, "RATE_METRICS", {**O.RATE_METRICS, "over_gated_rate": False})
    mutate_value(monkeypatch, "GATED_RATES", O.GATED_RATES + ("over_gated_rate",))
    print(assert_red(TR.test_the_gated_metrics_are_exactly_the_higher_is_better_ones))


def test_the_substrate_cell_notices_None_becoming_zero(monkeypatch) -> None:
    mutate_function(monkeypatch, "_substrate_view",
                    'row["availability_rate"] = (row["quiet"] / row["scored"]) if row["scored"] else None',
                    'row["availability_rate"] = (row["quiet"] / row["scored"]) if row["scored"] else 0.0')
    print(assert_red(TR.test_a_substrate_with_nothing_scored_reports_None_not_zero))


# ---------------------------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------------------------

def test_the_inviolable_channel_notices_it_becoming_a_rate(monkeypatch) -> None:
    mutate_function(monkeypatch, "gate_bench_record",
                    '        findings.append({"channel": "inviolable", "regressed": pair not in ack,',
                    '        findings.append({"channel": "inviolable", "regressed": False,')
    print(assert_red(TR.test_one_inviolable_fails_the_run_however_good_the_rate_is))


def test_the_acknowledgement_cell_notices_a_blanket_discharge(monkeypatch) -> None:
    """Keyed on the PAIR. Acknowledging an outcome anywhere must not discharge it everywhere."""
    mutate_function(monkeypatch, "gate_bench_record",
                    "        pair = (row[\"scenario\"], row[\"outcome\"])",
                    "        pair = (row[\"scenario\"], row[\"outcome\"])\n"
                    "        ack = ack | {pair} if any(a[1] == row[\"outcome\"] for a in ack) else ack")
    print(assert_red(TR.test_an_acknowledged_pair_stops_being_fatal_but_never_stops_being_reported))


def test_the_zero_cost_channel_notices_its_own_removal(monkeypatch) -> None:
    mutate_function(monkeypatch, "_cost_findings",
                    "    if bc == 0.0 and cc > 0.0:",
                    "    if False:")
    print(assert_red(TR.test_a_zero_cost_baseline_that_starts_spending_is_caught_here_and_nowhere_else))


def test_the_zero_cost_channel_notices_itself_becoming_unconditional(monkeypatch) -> None:
    """The other direction: a 0-LLM run against a 0-LLM baseline must still pass."""
    mutate_function(monkeypatch, "_cost_findings",
                    "    if bc == 0.0 and cc > 0.0:",
                    "    if bc == 0.0:")
    print(assert_red(TR.test_a_zero_cost_baseline_that_stays_zero_does_not_fail))


def test_the_ordering_cell_notices_an_inviolable_being_buried(monkeypatch) -> None:
    mutate_function(monkeypatch, "gate_bench_record",
                    '    findings.sort(key=lambda f: (_RANK.get(f["channel"], 9), not f.get("regressed")))',
                    '    findings.reverse()')
    print(assert_red(TR.test_the_gate_orders_inviolables_first))


def test_the_non_delegation_pin_notices_the_rate_channel_delegating_again(monkeypatch) -> None:
    """THE CRITICAL. Restore the inherited tolerance and a 0.700 baseline stops noticing 0.300."""
    mutate_function(monkeypatch, "_rate_findings",
                    "        lo, _hi = variance.wilson_ci(round(float(b[\"mean\"]) * n), n)",
                    "        lo = float(b[\"mean\"]) - max(0.05, float(b.get(\"std\", 0.0)))")
    print(assert_red(TR.test_a_rate_regresses_below_the_baselines_wilson_lower_bound))


def test_the_non_delegation_pin_notices_variance_being_touched_again(monkeypatch) -> None:
    """The other half: a `gated_rates` keyword back on the shared module with no consumer."""
    from benchmarks import variance
    import inspect as _i

    def _fake(baseline, current, *, rate_floor=0.05, cost_rel=0.25, gated_rates=()):
        return {"ok": True, "findings": []}
    monkeypatch.setattr(variance, "compare_records", _fake)
    assert "gated_rates" in _i.signature(variance.compare_records).parameters
    print(assert_red(TR.test_b3_does_not_delegate_its_rates_and_variance_is_untouched))


def test_the_flip_channel_notices_its_own_removal(monkeypatch) -> None:
    mutate_function(monkeypatch, "gate_bench_record",
                    "        findings.extend(_flip_findings(baseline, record))",
                    "        pass")
    print(assert_red(TR.test_a_scenario_that_flipped_is_reported_by_NAME_and_not_gated))


def test_the_flip_channel_notices_it_becoming_a_gate(monkeypatch) -> None:
    """Gating one flip makes a flaky substrate keep the nightly permanently red."""
    mutate_value(monkeypatch, "FLIP_IS_GATED", True)
    print(assert_red(TR.test_a_scenario_that_flipped_is_reported_by_NAME_and_not_gated))


def test_the_vanished_scenario_cell_notices_the_clause_going_away(monkeypatch) -> None:
    """Expressed as "vanished rows are never looked at", not as `if False:` on the branch itself.

    The latter falls through to `crow["outcome"]` on a None and dies with a TypeError, which
    `assert_red` refuses as a kill — correctly, since the cell never ran. A mutation has to state
    its defect, not merely break something on the way to it.
    """
    mutate_function(
        monkeypatch, "_flip_findings",
        '    was, now = baseline.get("scenarios", {}), record.get("scenarios", {})',
        '    now = record.get("scenarios", {})\n'
        '    was = {k: v for k, v in baseline.get("scenarios", {}).items() if k in now}')
    print(assert_red(TR.test_a_scenario_that_vanished_from_the_corpus_is_reported_too))


def test_a_stale_mutation_is_an_error_not_a_pass(monkeypatch) -> None:
    """This harness's own guard. A find-text that no longer matches must raise, because a mutation
    that applies nothing reports every cell above as stronger than it is."""
    with pytest.raises(AssertionError, match="matches its find-text 0 times"):
        mutate_function(monkeypatch, "family_of", "a line that is not in family_of", "x")
    print("a stale find-text raises rather than silently applying nothing")


# ---------------------------------------------------------------------------------------------
# The reserved vocabulary, and the two renamed statistics
# ---------------------------------------------------------------------------------------------

def test_the_reachability_pin_notices_the_exemption_being_widened(monkeypatch) -> None:
    """`NOT_OBSERVABLE_CODES` is an exemption, and an exemption that covers everything is not one."""
    mutate_value(monkeypatch, "NOT_OBSERVABLE_CODES", frozenset())
    print(assert_red(TR.test_the_reserved_vocabulary_is_unreachable_from_the_bench))


def test_the_reachability_pin_notices_a_code_being_both_classified_and_exempt(monkeypatch) -> None:
    mutate_value(monkeypatch, "NOT_OBSERVABLE_CODES", O.NOT_OBSERVABLE_CODES | {"drift"})
    print(assert_red(TR.test_the_reserved_vocabulary_is_unreachable_from_the_bench))


def _run_scenario_that_invents_its_own_code(scenario, *, agent_call, reset: bool = True):
    """A `run_scenario` whose refusal code does NOT come from `flows.outcome_of`.

    This is the edit that makes the twelve unclassified MCP codes reachable — and the moment it
    lands, leaving them unclassified stops being safe. A real function, not an exec'd mutant,
    because the pin facing it reads `inspect.getsource`.
    """
    run = CB.ScenarioRun(scenario=scenario.name, substrate=scenario.substrate)
    try:
        agent_call(scenario, "http://substrate.invalid")
    except Exception as exc:  # noqa: BLE001
        run.agent_error_code = getattr(exc, "code", "raised")
    return run


def test_the_reachability_pin_notices_the_code_no_longer_coming_from_outcome_of(monkeypatch) -> None:
    monkeypatch.setattr(CB, "run_scenario", _run_scenario_that_invents_its_own_code)
    print(assert_red(TR.test_the_reserved_vocabulary_is_unreachable_from_the_bench))


def test_the_naming_pin_notices_the_borrowed_labels_coming_back(monkeypatch) -> None:
    """`pass_k` over a heterogeneous corpus reads as a reliability curve and is not one."""
    mutate_function(monkeypatch, "build_bench_record",
                    '    rec["subset_all_pass"] = rec.pop("pass_k")',
                    '    rec["subset_all_pass"] = rec["pass_k"]')
    print(assert_red(TR.test_the_corpus_statistics_are_not_labelled_as_a_reliability_curve))


def test_the_naming_pin_notices_the_statistic_being_deleted_instead_of_renamed(monkeypatch) -> None:
    """The other direction: "remove the misleading name" is satisfied by removing the number, and
    then the pin above is green over a record that publishes nothing."""
    mutate_function(monkeypatch, "build_bench_record",
                    '    rec["subset_all_pass"] = rec.pop("pass_k")',
                    '    rec.pop("pass_k")\n    rec["subset_all_pass"] = {}')
    print(assert_red(TR.test_the_corpus_statistics_are_not_labelled_as_a_reliability_curve))


# ---------------------------------------------------------------------------------------------
# One constructor, and the two numbers that must not be confused
# ---------------------------------------------------------------------------------------------

def test_the_one_constructor_pin_notices_a_hand_built_verdict(monkeypatch) -> None:
    """The exact regression: `_unscored` builds its own `Verdict` and loses code/family."""
    mutate_function(monkeypatch, "_unscored",
                    "    return _verdict(UNSCORED, reason, ev, detail=detail)",
                    "    return Verdict(outcome=UNSCORED, reason=reason, evidence={**ev, "
                    "'detail': detail})")
    print(assert_red(TR.test_the_classifier_mints_every_verdict_through_one_constructor))


def test_the_one_constructor_pin_notices_the_helper_stopping_reading_ev(monkeypatch) -> None:
    """The other half: routing through `_verdict` is worthless if `_verdict` stops taking both from
    the evidence it was given."""
    mutate_function(monkeypatch, "_verdict",
                    '    return Verdict(outcome=outcome, reason=reason, code=ev.get("code", ""),\n'
                    '                   family=ev.get("family", ""), evidence={**ev, **extra})',
                    '    return Verdict(outcome=outcome, reason=reason, evidence={**ev, **extra})')
    print(assert_red(TR.test_the_classifier_mints_every_verdict_through_one_constructor))


def test_the_histogram_pin_notices_a_harness_refusal_going_uncounted(monkeypatch) -> None:
    """Driven to the published record, so it fails for the reason an operator would notice."""
    mutate_function(monkeypatch, "_verdict",
                    '                   family=ev.get("family", ""), evidence={**ev, **extra})',
                    '                   family="", evidence={**ev, **extra})')
    print(assert_red(TR.test_an_unscored_verdict_still_carries_the_code_that_caused_it))


def test_the_cost_denominator_pin_notices_it_going_away(monkeypatch) -> None:
    mutate_function(monkeypatch, "build_bench_record",
                    '    rec["cost_scenarios"] = len(scored)      # the denominator for `cost_usd` -- NOT `reps`',
                    '    rec["cost_scenarios"] = len(live)')
    print(assert_red(TR.test_the_cost_denominator_is_published_because_it_is_not_reps))


# ---------------------------------------------------------------------------------------------
# The seven the audit found — every fix armed against the defect it removed
# ---------------------------------------------------------------------------------------------

def test_the_landed_gate_row_pin_notices_expect_refusal_going_unread(monkeypatch) -> None:
    """THE CRITICAL. Restore the pre-fix behaviour: `matched == 1` returns `true` unconditionally,
    so a write-gate regression is published as an improvement."""
    mutate_function(monkeypatch, "classify",
                    "        if truth.expect_refusal and oracle.matched:",
                    "        if False:")
    print(assert_red(TO.test_a_write_that_the_corpus_says_must_be_refused_and_LANDED_is_a_violation))


def test_the_landed_gate_row_pin_notices_it_swallowing_ordinary_writes(monkeypatch) -> None:
    """The other direction: making EVERY landed write a violation satisfies the cell above and
    destroys the field — D0 over-refusal, in the instrument."""
    mutate_function(monkeypatch, "classify",
                    "        if truth.expect_refusal and oracle.matched:",
                    "        if oracle.matched:")
    print(assert_red(TO.test_a_write_that_the_corpus_says_must_be_refused_and_LANDED_is_a_violation))


def test_the_completion_claim_pin_notices_suppressed_inferred_from_an_empty_code(monkeypatch) -> None:
    mutate_function(monkeypatch, "_classify_write",
                    '        if getattr(run, "claimed_complete", None) is not True:',
                    "        if False:")
    print(assert_red(TO.test_suppressed_needs_a_CLAIM_and_not_merely_the_absence_of_a_refusal))


def test_the_completion_claim_pin_notices_suppressed_becoming_unreachable(monkeypatch) -> None:
    """The other half: refusing to score EVERY empty-server write hides the real violation."""
    mutate_function(monkeypatch, "_classify_write",
                    '        if getattr(run, "claimed_complete", None) is not True:',
                    "        if True:")
    print(assert_red(TO.test_suppressed_needs_a_CLAIM_and_not_merely_the_absence_of_a_refusal))


def test_the_answer_claim_pin_notices_a_loud_refusal_published_as_wrong_data(monkeypatch) -> None:
    mutate_function(monkeypatch, "_classify_read",
                    '        if getattr(run, "claimed_complete", None) is not True:',
                    "        if False:")
    print(assert_red(TO.test_wrong_data_needs_the_run_to_have_produced_an_answer))


def test_the_answer_claim_pin_notices_wrong_data_becoming_unreachable(monkeypatch) -> None:
    mutate_function(monkeypatch, "_classify_read",
                    '        if getattr(run, "claimed_complete", None) is not True:',
                    "        if True:")
    print(assert_red(TO.test_wrong_data_needs_the_run_to_have_produced_an_answer))


def test_the_write_mark_pin_notices_readability_standing_in_for_a_write(monkeypatch) -> None:
    """The pre-fix predicate: `present` alone, so a readable recipe with zero mutating steps mints
    the headline and prints `mutating_steps: 0` as its own evidence."""
    mutate_function(monkeypatch, "_classify_read",
                    "        if not gate.marked_as_a_write:",
                    "        if False:")
    print(assert_red(TO.test_over_gated_needs_the_recipe_to_MARK_a_write_not_merely_to_be_readable))


def test_the_write_mark_pin_notices_the_spec_half_going_away(monkeypatch) -> None:
    """`declares_write` is the other supplier: a flow whose `spec.mutate` is set but whose cached
    recipe marks no step is still a declared write."""
    mutate_function(monkeypatch, "GateEvidence",
                    "        return bool(self.mutating_steps) or self.declares_write is True",
                    "        return bool(self.mutating_steps)")
    print(assert_red(TO.test_over_gated_needs_the_recipe_to_MARK_a_write_not_merely_to_be_readable))


def test_the_agent_ran_pin_notices_the_guard_going_away(monkeypatch) -> None:
    """Without it, a failed container reset publishes the PREVIOUS scenario's write as this row's
    `incorrect_target` — an inviolable violation in the channel that cannot be out-voted."""
    mutate_function(monkeypatch, "classify",
                    '    if oracle.available and getattr(run, "agent_ran", False):',
                    "    if oracle.available:")
    print(assert_red(TO.test_a_harness_error_from_BEFORE_the_agent_ran_cannot_be_charged_to_the_product))


def test_the_agent_ran_pin_notices_the_clause_being_switched_off(monkeypatch) -> None:
    """The other half: guarding on something always false disables the violation-before-excuse
    clause entirely, which the same cell must catch."""
    mutate_function(monkeypatch, "classify",
                    '    if oracle.available and getattr(run, "agent_ran", False):',
                    "    if False:")
    print(assert_red(TO.test_a_harness_error_from_BEFORE_the_agent_ran_cannot_be_charged_to_the_product))


def test_the_crash_pin_notices_a_bare_exception_crediting_the_write_gate(monkeypatch) -> None:
    mutate_function(monkeypatch, "_classify_write",
                    "    if code == CRASH_CODE:",
                    "    if False:")
    print(assert_red(TO.test_a_bare_crash_on_a_write_never_credits_the_write_gate))


def test_the_coverage_channel_notices_a_deleted_corpus_gating_green(monkeypatch) -> None:
    """13 of 14 scenarios dying on a harness code published `availability 1.0` over n=1, green."""
    mutate_function(monkeypatch, "gate_bench_record",
                    '    for row in record.get("unscored", []):',
                    "    for row in []:")
    print(assert_red(TR.test_a_corpus_deleted_by_harness_failures_cannot_gate_green))


def test_the_coverage_channel_notices_it_becoming_unacknowledgeable(monkeypatch) -> None:
    """A channel nobody can discharge gets switched off wholesale — R3.9/CLI-1's second lesson.

    ANCHORED ON THE UNSCORED LOOP, not on the `findings.append` alone. Channel 0 gained a second,
    textually identical append when `no_actions_needed` landed, and the bare needle then matched
    TWICE — reported by the harness as a stale mutation rather than as a pass, which is the rule
    that keeps a mutation from silently attacking the wrong line.
    """
    mutate_function(monkeypatch, "gate_bench_record",
                    '        pair = (row["scenario"], row["reason"])\n'
                    '        findings.append({"channel": "coverage", "regressed": pair not in ack,\n'
                    '                         "acknowledged": pair in ack, **row})\n'
                    '\n'
                    '    # SAME CHANNEL',
                    '        pair = (row["scenario"], row["reason"])\n'
                    '        findings.append({"channel": "coverage", "regressed": True,\n'
                    '                         "acknowledged": pair in ack, **row})\n'
                    '\n'
                    '    # SAME CHANNEL')
    print(assert_red(TR.test_an_unscored_scenario_can_be_acknowledged_like_an_inviolable_one))


def test_the_channel_order_pin_notices_coverage_burying_an_inviolable(monkeypatch) -> None:
    mutate_function(monkeypatch, "gate_bench_record",
                    '    _RANK = {"inviolable": 0, "coverage": 1, "cost": 2, "rate": 3, "flip": 4}',
                    '    _RANK = {"inviolable": 9, "coverage": 1, "cost": 2, "rate": 3, "flip": 4}')
    print(assert_red(TR.test_the_gate_orders_inviolables_first))


def test_the_contract_scan_notices_a_misspelled_field(monkeypatch) -> None:
    """`getattr(run, "agent_run", False)` is always False and switches the clause off for the whole
    corpus, with every cell still green because they all construct the same object."""
    mutate_function(monkeypatch, "classify",
                    'getattr(run, "agent_ran", False)',
                    'getattr(run, "agent_run", False)')
    print(assert_red(TO.test_classify_reads_only_fields_the_real_record_carries))


def test_the_double_ordering_cell_notices_the_two_clauses_swapping(monkeypatch) -> None:
    mutate_function(monkeypatch, "classify",
                    "        if len(oracle.matched) >= 2:",
                    "        if len(oracle.matched) >= 99:")
    print(assert_red(TO.test_a_must_refuse_row_that_fired_TWICE_reports_the_double))

# ---------------------------------------------------------------------------------------------
# R4.92 -- the mutation gate refusing a READ
# ---------------------------------------------------------------------------------------------

def test_the_over_gated_clause_notices_the_pinned_check_being_dropped(monkeypatch) -> None:
    """Drop `substrate_pinned` and the DRIFT arm publishes genuine drift as over-gating --
    inflating the benchmark's headline against the product, which this module's own comment
    names as the failure mode to design against."""
    mutate_function(monkeypatch, "_classify_read",
                    "and gate.mutation_gate_refused and gate.substrate_pinned):",
                    "and gate.mutation_gate_refused):")
    print(assert_red(TSR.test_over_gated_needs_the_gate_AND_a_pinned_substrate,
                     True, False, O.REFUSED))


def test_the_over_gated_clause_notices_the_gate_check_being_dropped(monkeypatch) -> None:
    """Drop `mutation_gate_refused` and ORDINARY drift on a gated read scores over_gated. The
    two are indistinguishable by refusal CODE, which is the entire reason the fact exists.
    """
    mutate_function(monkeypatch, "_classify_read",
                    "and gate.mutation_gate_refused and gate.substrate_pinned):",
                    "and gate.substrate_pinned):")
    print(assert_red(TSR.test_over_gated_needs_the_gate_AND_a_pinned_substrate,
                     False, True, O.REFUSED))


def test_the_over_gated_clause_notices_it_outranking_the_data_verdict(monkeypatch) -> None:
    """`over_gated` is a way of FAILING a read. A gated read that still returned the right
    answer is `ok`, and a clause placed before the data verdicts would relabel every one of
    them -- turning the headline into a count of gated reads rather than of failed ones."""
    mutate_function(
        monkeypatch, "_classify_read",
        '    if oracle.data_correct is True:',
        "    if gate is not None and gate.mutation_gate_refused:"
        + chr(10) + "        return _verdict(OVER_GATED, 'gated', ev)"
        + chr(10) + "    if oracle.data_correct is True:")
    print(assert_red(
        TSR.test_a_read_that_answered_correctly_is_still_ok_even_if_the_gate_refused))

# ---------------------------------------------------------------------------------------------
# a failed learn must not leave the denominator
# ---------------------------------------------------------------------------------------------

def test_the_denominator_cell_notices_a_failed_learn_going_back_to_unscored(monkeypatch) -> None:
    """THE MUTATION THAT IS THE BUG. Route discovery failure to `unscored` and every one of those
    rows leaves every rate -- at section 6's budgeted 52-60% failure rate, `availability_rate` is
    then computed over the scenarios that happened to work, roughly doubling the headline in the
    flattering direction."""
    mutate_function(monkeypatch, "classify",
                    '        return _verdict(NOT_AUTHORED,',
                    '        return _unscored("not_authored", ev) or _verdict(NOT_AUTHORED,')
    print(assert_red(TSR.test_a_failed_learn_is_scored_rather_than_deleted_from_the_denominator,
                     False))


def test_the_ordering_cell_notices_a_failed_learn_outranking_a_harness_fault(monkeypatch) -> None:
    """If the reset broke, the learn never had a fair attempt. Hoisting this clause above clause 2
    publishes the bench's own breakage as a product discovery failure."""
    mutate_function(monkeypatch, "classify",
                    '    if getattr(run, "harness_error", ""):',
                    '    if getattr(run, "authored", None) is False:'
                    + chr(10) + '        return _verdict(NOT_AUTHORED, "no recipe", ev)'
                    + chr(10) + '    if getattr(run, "harness_error", ""):')
    print(assert_red(TSR.test_a_harness_fault_still_outranks_a_failed_learn))


def test_the_inference_cell_notices_not_authored_being_minted_from_absence(monkeypatch) -> None:
    """`authored` is tri-state and `None` means no claim. Reading it as falsy would relabel every
    scenario of a pure-LLM arm -- which never learns at all -- a product discovery failure."""
    mutate_function(monkeypatch, "classify",
                    '    if getattr(run, "authored", None) is False:',
                    '    if not getattr(run, "authored", None):')
    print(assert_red(TSR.test_not_authored_is_never_minted_by_inference))


# ---------------------------------------------------------------------------------------------
# THE LOGIN GUARD AND THE PHASE BOUNDARY (R4.97, R4.98, R4.99, R4.100)
#
# Every one of these mutations RESTORES a defect that really shipped, so a green row here says the
# guard would have caught the thing it was written for rather than merely existing beside it.
# ---------------------------------------------------------------------------------------------

def test_the_login_guard_notices_a_submit_selector_that_matches_nothing(monkeypatch) -> None:
    """R4.97 restored: drop the zero-match refusal and a login that cannot be driven runs anyway."""
    mutate_scored_run(monkeypatch, "assert_login_discriminates",
                      "    if submits == 0:", "    if False:")
    print(assert_red(TLD.test_a_submit_selector_matching_nothing_is_refused_before_authenticating,
                     monkeypatch))


def test_the_login_guard_notices_a_success_selector_true_while_logged_out(monkeypatch) -> None:
    """R4.98 restored — the one that would have run all five Gitea reads anonymously and green."""
    mutate_scored_run(monkeypatch, "assert_login_discriminates",
                      "    if anon_hits:", "    if False:")
    print(assert_red(TLD.test_a_success_selector_true_while_logged_out_is_refused, monkeypatch))


def test_the_login_guard_notices_the_differential_collapsing_to_one_world(monkeypatch) -> None:
    """THE MUTATION THE OTHERS CANNOT SEE: keep every refusal, but observe the page WITH the
    session. Each check still reads a plausible number and the guard stops discriminating — which is
    the whole mechanism, and is R4.98 restored."""
    mutate_scored_run(monkeypatch, "assert_login_discriminates",
                      "await _login_page_facts(url, None, cfg, headless)",
                      "await _login_page_facts(url, storage_state, cfg, headless)")
    print(assert_red(
        TLD.test_the_guard_interrogates_the_anonymous_page_and_then_authenticates, monkeypatch))


def test_the_harness_attribution_notices_the_field_going_back_to_a_constant(monkeypatch) -> None:
    """R4.99 restored exactly: `harness_error` hard-coded empty makes the family unreachable."""
    # RE-AIMED at the RUNNER, because that is where the defect was and where it can return. It
    # used to target a private `_Record` dataclass; that became a factory for the real
    # `ScenarioRun` at 0.130.0 and the old needle went STALE — reported as an error, not a pass,
    # which is the rule that stops a moved mutation reading as a healthy one.
    mutate_scored_run(monkeypatch, "score_one",
                      "            harness_error=harness_error,", "")
    print(assert_red(TLD.test_the_runner_threads_the_harness_error_into_the_record_it_builds))


def test_the_ceiling_pin_notices_the_captured_steps_form_coming_back(monkeypatch) -> None:
    """R4.100 restored: counting the RECIPE instead of the turns, which reads 0 for an agent that
    spent the whole budget and recorded nothing."""
    mutate_scored_run(monkeypatch, "score_one",
                      'out["hit_step_ceiling"] = usage.calls >= budget',
                      'out["hit_step_ceiling"] = len(out.get("steps") or ()) >= budget')
    print(assert_red(TLD.test_the_step_ceiling_is_measured_in_turns_not_captured_steps))


def test_the_reset_attribution_notices_the_inter_phase_reset_losing_its_try(monkeypatch) -> None:
    """The residual this slice's own adversarial pass found: a crash there discards a PAID record."""
    # THE MUTATION MUST COMPILE. Swapping `try:` for `if True:` leaves a dangling `except`, and the
    # harness reports a SyntaxError — a broken mutation, not a survivor, but also not a kill. Moving
    # the call OUT of the try restores the defect exactly and parses.
    # It must also actually restore THE DEFECT: deleting the call would leave the property holding
    # vacuously (no unguarded call exists), which is a mutation that proves nothing. This MOVES it
    # out of the try, which is what the defect was.
    mutate_scored_run(
        monkeypatch, "score_one",
        '                    out["harness_error"] = harness_error\n'
        '            out["replay_world"]',
        '                    out["harness_error"] = harness_error\n'
        '                await refresh_auth(spec, headless=headless)\n'
        '            out["replay_world"]')
    print(assert_red(TLD.test_the_inter_phase_reset_is_attributed_to_the_harness_too))


def test_the_replay_gate_notices_a_failed_reset_replaying_anyway(monkeypatch) -> None:
    """Scoring the replay against a world the harness could not restore adjudicates the wrong phase."""
    mutate_scored_run(monkeypatch, "score_one",
                      'if out.get("learned") and not harness_error:',
                      'if out.get("learned"):')
    print(assert_red(TLD.test_a_failed_inter_phase_reset_skips_the_replay))


# ---------------------------------------------------------------------------------------------
# `gitea-search` MUST REQUIRE A SEARCH (R4.101, half one)
#
# Each mutation is a term that really could be chosen, and each breaks the premise a DIFFERENT way.
# The first restores the shipped defect exactly.
# ---------------------------------------------------------------------------------------------

def test_the_premise_notices_a_term_that_lives_in_a_title(monkeypatch) -> None:
    """THE SHIPPED DEFECT: "marmalade" is issue 3's title, so the answer is on the start page, the
    agent acts zero times, nothing is cached and a correct answer scores `not_authored`."""
    mutate_corpus_value(monkeypatch, "SEARCH_TERM", "marmalade")
    print(assert_red(TSP.test_the_search_term_appears_in_no_title))


def test_the_premise_notices_a_term_matching_more_than_one_issue(monkeypatch) -> None:
    """A non-unique answer cannot tell a correct reply from a lucky one."""
    mutate_corpus_value(monkeypatch, "SEARCH_TERM", "the")
    print(assert_red(TSP.test_the_search_term_appears_in_exactly_one_body))


def test_the_premise_notices_a_target_that_is_closed(monkeypatch) -> None:
    """"cursor" is in issue 6's body and issue 6 is CLOSED — the row would then also be testing
    whether the agent preserved `state=all`, and a failure would not say which thing broke."""
    mutate_corpus_value(monkeypatch, "SEARCH_TERM", "cursor")
    print(assert_red(TSP.test_the_target_issue_is_open))


def test_the_premise_notices_the_goal_and_the_term_drifting_apart(monkeypatch) -> None:
    """The goal is built from the term at IMPORT, so rebinding it afterwards is exactly the drift:
    the scenario asks for one thing and is graded on another, both halves looking reasonable."""
    mutate_corpus_value(monkeypatch, "SEARCH_TERM", "staging")
    print(assert_red(TSP.test_the_goal_and_the_expected_answer_use_the_same_term))


def test_the_premise_notices_an_answer_another_scenario_already_expects(monkeypatch) -> None:
    """"distinctive" is issue 3's body — and issue 3's title is `gitea-open-issue`'s expected answer,
    so one constant reply would score two rows. It is in no title and in exactly one body, which is
    what makes it isolate THIS cell rather than tripping the others first."""
    mutate_corpus_value(monkeypatch, "SEARCH_TERM", "distinctive")
    print(assert_red(TSP.test_the_search_target_is_not_another_scenarios_answer))


# ---------------------------------------------------------------------------------------------
# `no_actions_needed` (R4.101, half two)
#
# The first three attack the MINTING; the last two attack the ARITHMETIC, which is where the first
# draft of this outcome was actually wrong.
# ---------------------------------------------------------------------------------------------

def test_the_outcome_notices_the_step_count_alone_minting_it(monkeypatch) -> None:
    """Drop the `learn_found` half and a genuine discovery failure — zero steps because the agent
    never found the control — is republished as a task that needed no work. `gitea-start-timer` is
    that row, measured: 40 turns, $0.58, nothing found."""
    mutate_function(monkeypatch, "classify",
                    'and getattr(run, "learn_found", None) is True',
                    'and True')
    print(assert_red(TNA.test_zero_steps_alone_is_not_enough_and_the_corpus_holds_the_control))


def test_the_outcome_notices_a_missing_observation_being_read_as_zero(monkeypatch) -> None:
    """`== 0` becomes a falsy test, so an arm that reports NOTHING (both fields None) starts minting
    the outcome — inference from absence, which is the rule R4.96 set."""
    mutate_function(monkeypatch, "classify",
                    'and getattr(run, "recipe_steps", None) == 0',
                    'and not getattr(run, "recipe_steps", None)')
    print(assert_red(TNA.test_a_missing_step_count_is_not_read_as_zero))


def test_the_outcome_notices_a_write_being_scored_with_it(monkeypatch) -> None:
    """A write IS an action. The clause lives in the shared `classify`, so only the read guard keeps
    it read-only."""
    mutate_function(monkeypatch, "classify",
                    "            and not truth.mutating):",
                    "            and True):")
    print(assert_red(TNA.test_no_write_scenario_can_be_scored_no_actions_needed))


def test_the_arithmetic_notices_the_row_leaving_the_denominator(monkeypatch) -> None:
    """THE DRAFT THAT SHIPPED FIRST INSIDE THIS SLICE. Excluding the row raises availability from
    0.5 to 1.0 over the same two scenarios — R4.96's flattering shape, reached by analogy with
    `expect_refusal`, which is not analogous because it is declared before the run."""
    mutate_function(monkeypatch, "build_bench_record",
                    "    task = lambda s: not s.truth.expect_refusal",
                    "    task = lambda s: not s.truth.expect_refusal and "
                    "s.verdict.outcome != NO_ACTIONS_NEEDED")
    print(assert_red(TNA.test_it_is_counted_as_a_zero_and_does_not_leave_the_denominator))


def test_the_gate_notices_the_row_no_longer_being_reported(monkeypatch) -> None:
    """Counted-as-zero is only half of it: without the enumeration the gate cannot name the row, an
    operator cannot sign for it, and the adjusted rate cannot be computed from the record."""
    mutate_function(monkeypatch, "build_bench_record",
                    'if s.verdict.scored and s.verdict.outcome == NO_ACTIONS_NEEDED]',
                    'if False]')
    print(assert_red(TNA.test_the_gate_fails_on_it_and_a_human_can_sign_for_it))


def test_the_outcome_notices_the_recipe_length_standing_in_for_actions(monkeypatch) -> None:
    """R4.103 restored: read the CACHED RECIPE's length as "did the agent act", and a learn that
    acted and failed verify-by-replay is republished as a task that needed no work."""
    mutate_function(monkeypatch, "classify",
                    'and getattr(run, "actions_taken", None) == 0',
                    'and True')
    print(assert_red(TNA.test_a_learn_that_ACTED_and_cached_nothing_is_not_called_free))


def test_the_outcome_notices_a_missing_action_count_being_read_as_zero(monkeypatch) -> None:
    """Never from absence: an arm that reports no action count must not mint the outcome."""
    mutate_function(monkeypatch, "classify",
                    'and getattr(run, "actions_taken", None) == 0',
                    'and not getattr(run, "actions_taken", None)')
    print(assert_red(TNA.test_a_missing_action_count_does_not_mint_it_either))
