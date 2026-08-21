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
import linecache
import os
import tempfile
import textwrap

import pytest

from benchmarks import customer_bench as CB
from benchmarks import outcomes as O
import tests.test_bench_outcomes as TO
import tests.test_bench_record as TR


# ---------------------------------------------------------------------------------------------
# the harness
# ---------------------------------------------------------------------------------------------

def mutate_function(monkeypatch, name: str, find: str, repl: str) -> None:
    """Rebind `outcomes.<name>` to a source-level mutation of itself, for this test only.

    A stale or ambiguous `find` raises rather than silently applying nothing — `prove_red.py`'s own
    rule, and the reason it reports a non-matching mutation as an ERROR and not as a survivor.

    THE MUTANT'S SOURCE IS RETRIEVABLE, and that is not a nicety. Several guards in these modules
    are STRUCTURAL — they read `inspect.getsource` and scan the AST. Compiling a mutant under a
    `<mutant:name>` pseudo-filename makes `getsource` raise `OSError: could not get source code`,
    the guard dies before it reaches its scan, and this harness scores the crash as a kill. That
    happened THREE times while writing this file, twice in cells added an hour after the first was
    fixed by hand — which is what says the fix belongs here rather than in a cell.

    So the mutant is compiled under a `.py` filename registered in `linecache`, which is one of the
    two things `inspect.getsourcefile` accepts for a path that does not exist on disk. Nothing is
    written to disk, and `monkeypatch` removes the entry afterwards.
    """
    src = textwrap.dedent(inspect.getsource(getattr(O, name)))
    n = src.count(find)
    if n != 1:
        raise AssertionError(
            f"mutation for {name!r} matches its find-text {n} times, not once. The function has "
            f"moved and this mutation now proves nothing — re-express it against the current "
            f"source rather than deleting it.\nfind: {find!r}")
    mutated = src.replace(find, repl, 1)
    path = os.path.join(tempfile.gettempdir(), f"ultracua_mutant_{name}.py")
    monkeypatch.setitem(linecache.cache, path,
                        (len(mutated), None, mutated.splitlines(True), path))
    ns: dict = {}
    exec(compile(mutated, path, "exec"), O.__dict__, ns)
    mutant = ns[name]
    # Proven, not assumed: a structural guard is about to call this on the mutant, and if it raises
    # the guard never runs and its failure is credited to the mutation.
    assert inspect.getsource(mutant), f"the mutant of {name!r} has no retrievable source"
    monkeypatch.setattr(O, name, mutant)


def mutate_value(monkeypatch, name: str, value) -> None:
    assert hasattr(O, name), f"{name} no longer exists in outcomes.py"
    monkeypatch.setattr(O, name, value)


def assert_red(guard, *args) -> str:
    """Run one guard cell and require it to fail. Returns the message, for printing.

    `AssertionError` OR `KeyError`/`BenchRecordError` all count: a guard whose subject has been
    broken may notice by asserting or by the mutated code blowing up on the way. What does NOT count
    is passing, and what also does not count is `TypeError` from calling the guard wrongly — S14's
    third trap, where a bad kwarg raised and the harness read it as a legitimate refusal.

    `BaseException`, NOT `Exception`, and this cost five false verdicts on its first run. A guard
    written as `with pytest.raises(...)` fails by raising `_pytest.outcomes.Failed`, which descends
    from `BaseException` — so an `except Exception` here silently let the loudest kind of guard
    (the ones asserting a REFUSAL) escape as "the cell passed against a mutation". The instrument
    was reporting its own blind spot as a hole in the suite.
    """
    try:
        guard(*args)
    except (KeyboardInterrupt, SystemExit):
        raise
    except TypeError as exc:
        raise AssertionError(
            f"{guard.__name__} raised TypeError ({exc}) — that is this harness calling the cell "
            f"wrongly, not the cell noticing the mutation") from exc
    except BaseException as exc:  # noqa: BLE001 - any genuine complaint is a kill
        return f"{type(exc).__name__}: {str(exc).splitlines()[0][:110]}"
    raise AssertionError(
        f"{guard.__name__} PASSED against a mutation that violates the property it names. The cell "
        f"is not guarding what its name says it guards.")


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


def test_the_quiet_allowlist_pin_notices_unscored_becoming_a_pass(monkeypatch) -> None:
    mutate_value(monkeypatch, "QUIET_OUTCOMES", frozenset({O.OK, O.TRUE, O.UNSCORED}))
    print(assert_red(TO.test_the_quiet_set_is_an_allowlist_and_unscored_is_not_in_it))


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
    """Inviolable #3's adverb. Drop it and every empty-server write becomes a violation."""
    mutate_function(monkeypatch, "_classify_write", "    if not code:", "    if True:")
    print(assert_red(TO.test_suppressed_requires_the_SILENCE_not_merely_an_empty_server))


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
                    "        if cached_flow is None:\n"
                    "            return cls(present=False, approved=approved)",
                    "        if cached_flow is None:\n"
                    "            return cls(present=True, mutating_steps=0, approved=approved)")
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
    mutate_function(monkeypatch, "gate_bench_record",
                    "        if bc is not None and float(bc) == 0.0 and cc > 0.0:",
                    "        if False:")
    print(assert_red(TR.test_a_zero_cost_baseline_that_starts_spending_is_caught_here_and_nowhere_else))


def test_the_zero_cost_channel_notices_itself_becoming_unconditional(monkeypatch) -> None:
    """The other direction: a 0-LLM run against a 0-LLM baseline must still pass."""
    mutate_function(monkeypatch, "gate_bench_record",
                    "        if bc is not None and float(bc) == 0.0 and cc > 0.0:",
                    "        if bc is not None and float(bc) == 0.0:")
    print(assert_red(TR.test_a_zero_cost_baseline_that_stays_zero_does_not_fail))


def test_the_ordering_cell_notices_an_inviolable_being_buried(monkeypatch) -> None:
    mutate_function(monkeypatch, "gate_bench_record",
                    '    findings.sort(key=lambda f: (f["channel"] != "inviolable", not f.get("regressed")))',
                    '    findings.reverse()')
    print(assert_red(TR.test_the_gate_orders_inviolables_first))


def test_the_inherited_gate_cell_notices_the_default_changing(monkeypatch) -> None:
    """B3 added a keyword to `variance.compare_records`; a changed DEFAULT silently un-gates every
    existing baseline in the repo."""
    from benchmarks import variance
    monkeypatch.setattr(variance, "_GATED_RATES", ())
    print(assert_red(TR.test_compare_records_default_gating_is_unchanged_for_existing_callers))


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
