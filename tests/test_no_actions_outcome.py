"""`no_actions_needed` — R4.101, half two.

A task whose answer is already on its landing page is completed with no actions, so no recipe is
cached and there is no speed-up to measure. Until 0.128.0 that published `not_authored` — "the
product was asked to author this flow and did not" — for a run in which the agent returned the
exactly correct answer. Measured on `gitea-search`: **14 points** off a seven-scenario headline.

WHAT THIS FILE IS MOSTLY ABOUT is the arithmetic, not the word. The first draft made the row LEAVE
the availability denominator, by analogy with `expect_refusal`. That is wrong and an existing guard
said so: excluding a row RAISES the mean, and an acknowledged exclusion makes the inflation
permanent — R4.96 exactly, and the same objection `outcomes.py` already records against routing such
a row to `unscored`. It is counted as a zero, and what changed is the LABEL and the enumeration.
"""
from __future__ import annotations

import pytest

from benchmarks import corpus, outcomes as O
from benchmarks.customer_bench import ScenarioRun
from benchmarks.scored_run import _Record

GITEA = {e.scenario.name: e for e in corpus.CORPORA["gitea"]}
READ, WRITE = "gitea-search", "gitea-comment"


def _classify(rec, name=READ, **oracle):
    return O.classify(GITEA[name].truth, rec,
                      O.Oracle(available=True, data_correct=oracle.pop("data_correct", None),
                               **oracle))


# --- what mints it, and what does not --------------------------------------------------------------

def test_a_task_completed_without_acting_is_named_for_what_happened() -> None:
    """The row as it was really measured before 0.127.0: correct answer, no actions, nothing cached."""
    v = _classify(_Record(True, "", None, authored=False, recipe_steps=0, actions_taken=0,
                          learn_found=True))
    assert v.outcome == O.NO_ACTIONS_NEEDED
    assert "without a single action" in v.reason


def test_a_learn_that_ACTED_and_cached_nothing_is_not_called_free(  # noqa: N802
) -> None:
    """R4.103 — the defect the first draft of this outcome shipped.

    `LearnResult.steps` is `list(cached.steps) if cached else []`, so `recipe_steps` reads **0
    whenever nothing was cached** — for a task that needed no work AND for a learn that acted five
    times and then failed verify-by-replay (`flow.py` sets `success = False` and does not cache,
    while `out["found"]` is set on the extraction path and stays True). Only the second is a product
    failure, and reading the recipe length as "did the agent act" republished it as "this task needed
    no work": a genuine authoring failure deleted from the product's account.
    """
    v = _classify(_Record(True, "", None, authored=False, recipe_steps=0, actions_taken=5,
                          learn_found=True))
    assert v.outcome == O.NOT_AUTHORED, (
        "a learn that ACTED and cached nothing is an authoring failure, not a free task")


def test_a_missing_action_count_does_not_mint_it_either() -> None:
    """Same rule as the step count: never inferred from absence. An arm that reports no action count
    cannot answer the question, so it falls through to the clause it always hit."""
    v = _classify(_Record(True, "", None, authored=False, recipe_steps=0, learn_found=True))
    assert v.outcome == O.NOT_AUTHORED


def test_zero_steps_alone_is_not_enough_and_the_corpus_holds_the_control() -> None:
    """A READ that recorded no action and found nothing is a discovery failure, not a free task.

    THE FIRST DRAFT OF THIS CELL USED `gitea-start-timer` AND PASSED FOR THE WRONG REASON — it is a
    WRITE, so the read-only guard blocked the mutation rather than the `learn_found` clause, and the
    arming harness reported the mutation as a survivor. The scenario has to be a read for this cell
    to be about what its name says. `gitea-start-timer` remains the real-world illustration: zero
    steps after 40 turns and $0.58, nothing found.
    """
    # `actions_taken=0` is supplied so `learn_found` is the ONLY thing standing between this row and
    # the outcome. Without it the new action-count conjunct blocks the clause too, and the mutation
    # that drops `learn_found` survives — which the arming harness duly reported.
    v = _classify(_Record(True, "", None, authored=False, recipe_steps=0, actions_taken=0,
                          learn_found=False))
    assert v.outcome == O.NOT_AUTHORED


def test_a_missing_step_count_is_not_read_as_zero() -> None:
    """THE ARM THIS PROTECTS IS A PURE-LLM BASELINE: it can answer correctly (`learn_found`) while
    recording no step count at all (`recipe_steps is None`, because it never learns). Read `None` as
    falsy and every one of its successes is republished as "this task needed no work" — inference
    from absence, the rule R4.96 set. Found by the arming harness, which showed the `== 0` mutation
    surviving because the sibling conjunct happened to cover the case the other cell used.
    """
    v = _classify(_Record(True, "", None, authored=False, actions_taken=0, learn_found=True))
    assert v.outcome == O.NOT_AUTHORED


def test_a_learn_that_recorded_steps_but_cached_nothing_is_still_not_authored() -> None:
    v = _classify(_Record(True, "", None, authored=False, recipe_steps=3, learn_found=False))
    assert v.outcome == O.NOT_AUTHORED


def test_an_arm_that_reports_neither_observation_is_unchanged() -> None:
    """BACKWARD COMPATIBILITY, asserted rather than assumed. A pure-LLM arm makes no claim about
    steps, so both fields stay None and the row falls through to the clause it always hit. Without
    this, adding the outcome would silently re-label every non-learning arm's failures."""
    v = _classify(_Record(True, "", None, authored=False))
    assert v.outcome == O.NOT_AUTHORED


def test_no_write_scenario_can_be_scored_no_actions_needed() -> None:
    """A write IS an action. A write flow that recorded none either did not happen (a discovery
    failure) or fired unattributably (a refusal) — never "the task needed no work". Asserted because
    the clause is in the shared `classify`, not in `_classify_read`, so only the guard keeps it read-
    only."""
    v = _classify(_Record(True, "", None, authored=False, recipe_steps=0, actions_taken=0,
                          learn_found=True), name=WRITE)
    assert v.outcome != O.NO_ACTIONS_NEEDED


def test_a_harness_fault_still_wins() -> None:
    """Ordering: a broken login means the learn never got a fair attempt, so the row says nothing
    about whether the task needed actions."""
    rec = _Record(True, "", None, authored=False, recipe_steps=0, actions_taken=0,
                  learn_found=True,
                  harness_error="SubstrateNotReady: the login could not be driven")
    assert _classify(rec).outcome == "unscored"


# --- the arithmetic, which is the part that was wrong first -----------------------------------------

def test_it_is_loud() -> None:
    assert O.NO_ACTIONS_NEEDED not in O.QUIET_OUTCOMES, (
        "a scenario measuring nothing must not let a nightly pass in silence")


def test_it_is_counted_as_a_zero_and_does_not_leave_the_denominator() -> None:
    """THE DRAFT THAT EXCLUDED IT WAS WRONG, and this is the cell that says why in numbers.

    Excluding raises the mean — here 1.0 instead of 0.5 — and an acknowledged exclusion would make
    that permanent. `expect_refusal` is not analogous: it is declared by the corpus before the run
    and gets `gate_holds_rate`, so nothing is deleted and nothing is discovered at run time.
    """
    rec = _record_for([("gitea-menu-nav", O.OK), (READ, O.NO_ACTIONS_NEEDED)])
    m = rec["metrics"]["availability_rate"]
    assert (m["mean"], m["n"]) == (0.5, 2), (
        f"the row must be counted as a zero over n=2, not excluded; got {m}")


def test_the_rows_are_enumerated_so_the_adjusted_rate_is_computable() -> None:
    """Counted as a zero AND listed, so a reader who wants availability among the tasks that have
    automation value can compute it without the record taking a second position on the headline."""
    rec = _record_for([("gitea-menu-nav", O.OK), (READ, O.NO_ACTIONS_NEEDED)])
    assert [r["scenario"] for r in rec["no_recipe"]] == [READ]
    assert rec["outcomes"][O.NO_ACTIONS_NEEDED] == 1


def test_the_gate_fails_on_it_and_a_human_can_sign_for_it() -> None:
    """Loud, and dischargeable — because an alert nobody can acknowledge gets switched off wholesale
    and takes the rest of the channel with it (R3.9/CLI-1). Safe to acknowledge here precisely
    because the row is counted: signing for it silences a known corpus wart and inflates nothing."""
    rec = _record_for([("gitea-menu-nav", O.OK), (READ, O.NO_ACTIONS_NEEDED)])
    row = rec["no_recipe"][0]
    assert O.gate_bench_record(rec)["ok"] is False
    signed = O.gate_bench_record(rec, acknowledged=((row["scenario"], row["reason"]),))
    assert signed["ok"] is True
    found = [f for f in signed["findings"] if f.get("scenario") == READ]
    assert found and found[0]["acknowledged"] is True and found[0]["channel"] == "coverage", (
        "an acknowledged row is still REPORTED, just not fatal")


# --- the helper ------------------------------------------------------------------------------------

def _record_for(rows) -> dict:
    """A bench record whose scenarios carry exactly the outcomes named."""
    scored = []
    for name, outcome in rows:
        e = GITEA[name]
        run = ScenarioRun(scenario=name, substrate="gitea", llm_accounting="observed")
        verdict = O.Verdict(outcome=outcome, reason=f"forced {outcome} for the arithmetic",
                            code="", family="", evidence={})
        # `substrate` is a PROPERTY derived from the run, not a field — so it comes from
        # `ScenarioRun.substrate` above and passing it here is a TypeError.
        scored.append(O.Scored(truth=e.truth, run=run, verdict=verdict))
    return O.build_bench_record(scored, bench="t", provider="p", timestamp="2026-08-25T00:00:00Z")


@pytest.mark.parametrize("field", ["recipe_steps", "learn_found"])
def test_the_observations_are_real_fields_on_the_shared_record(field: str) -> None:
    """`classify` reads them by name. A `getattr` default would switch the clause off for the whole
    corpus in silence — the defect `test_classify_reads_only_fields_the_real_record_carries` exists
    for, which caught exactly this while the slice was being written."""
    assert hasattr(ScenarioRun(scenario="s", substrate="gitea"), field)
    assert getattr(ScenarioRun(scenario="s", substrate="gitea"), field) is None
