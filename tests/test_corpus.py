"""The v1 corpus: the properties that make a scenario measurable rather than merely present.

A corpus entry is three things that must agree — the task, the corpus author's DECLARATION about it,
and the oracle that questions the server afterwards. Every way they can disagree is silent, so they
are bound into one object and the disagreements are refused at construction.

FOURTEEN SCENARIOS ACROSS TWO SUBSTRATES, and every property here is parametrised over both. The
alternative — a Gitea cell and an Odoo cell per property — is the per-branch shape this register
keeps re-filing, and it is how the Odoo half would come to be covered one property less than the
Gitea half without anyone noticing.

Docker-free, and asserted so: each substrate is a fake that serves its own query surface.
"""

from __future__ import annotations

import pytest

from benchmarks import corpus as C
from benchmarks import customer_bench as CB
from benchmarks import oracles as O
from benchmarks import substrates as S
from benchmarks.customer_bench import Scenario
from benchmarks.outcomes import ScenarioTruth
from tests.test_oracles import FAKES, _Fake, corpus_oracles

SUBSTRATES = sorted(FAKES)


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch, request):
    def _refuse(*a, **k):
        raise AssertionError(f"{request.node.name} reached Docker; the corpus tests use a fake.")
    monkeypatch.setattr(S.subprocess, "run", _refuse)


# --- the three-way binding ------------------------------------------------------------------------

@pytest.mark.parametrize("substrate", SUBSTRATES)
def test_every_entry_binds_a_scenario_a_truth_and_an_oracle(substrate) -> None:
    entries = C.for_substrate(substrate)
    assert len(entries) == 7, f"the {substrate} corpus should have seven scenarios, has {len(entries)}"
    for e in entries:
        assert e.scenario.name == e.truth.name
        assert e.scenario.substrate == substrate, (
            f"{e.scenario.name} is filed under {substrate!r} and declares "
            f"{e.scenario.substrate!r} — the two lists would then disagree about which world it runs in")
        assert callable(e.oracle)
    names = [e.scenario.name for e in entries]
    assert len(set(names)) == len(names), f"duplicate scenario names: {names}"


def test_the_two_halves_are_paired_scenario_for_scenario() -> None:
    """The plan's whole design: the same read INTENT on both substrates, so a difference in the
    result is attributable to architecture rather than to task difficulty. A stem present on one side
    and missing on the other is a pair that silently became two unrelated measurements."""
    stems = {s: {e.scenario.name.split("-", 1)[1] for e in C.for_substrate(s)} for s in SUBSTRATES}
    gitea, odoo = stems["gitea"], stems["odoo"]
    # Four stems are shared verbatim; three differ by design and are declared here rather than
    # silently tolerated — benchmark-plan section 7 pairs them by INTENT, not by name.
    declared = {("filter-state", "filter-status"), ("open-issue", "open-record"),
                ("comment", "create-lead"), ("start-timer", "idempotent-replay")}
    unpaired_g = gitea - odoo - {a for a, _ in declared}
    unpaired_o = odoo - gitea - {b for _, b in declared}
    assert not unpaired_g and not unpaired_o, (
        f"unpaired scenarios — gitea-only {sorted(unpaired_g)}, odoo-only {sorted(unpaired_o)}")


def test_a_scenario_and_its_truth_may_not_disagree_about_being_a_write() -> None:
    """B3 measures `over_gated` against the DECLARATION precisely so the product's own classifier
    cannot agree with itself. A disagreement here moves that baseline silently."""
    with pytest.raises(O.OracleError, match="silently moves the baseline"):
        C.CorpusEntry(scenario=Scenario(name="x", substrate="gitea", goal="g", mutating=True),
                      truth=ScenarioTruth(name="x", mutating=False),
                      oracle=lambda s: None)


def test_names_that_disagree_are_refused() -> None:
    with pytest.raises(O.OracleError, match="names disagree"):
        C.CorpusEntry(scenario=Scenario(name="a", substrate="gitea", goal="g"),
                      truth=ScenarioTruth(name="b"), oracle=lambda s: None,
                      expected_answer=lambda s: "x")


def test_a_write_may_not_declare_an_expected_answer() -> None:
    """A write is adjudicated by what LANDED. Scoring it on what the agent said about it is the
    thing the server-side oracle exists to replace."""
    with pytest.raises(O.OracleError, match="adjudicated by what landed"):
        C.CorpusEntry(scenario=Scenario(name="x", substrate="gitea", goal="g", mutating=True),
                      truth=ScenarioTruth(name="x", mutating=True), oracle=lambda s: None,
                      expected_answer=lambda s: "x")


def test_a_read_without_an_expected_answer_is_refused() -> None:
    """Otherwise `data_correct` is always None and the row can only ever be unscored or a safety
    finding — a read scenario that cannot produce a read outcome."""
    with pytest.raises(O.OracleError, match="declares no expected answer"):
        C.CorpusEntry(scenario=Scenario(name="x", substrate="gitea", goal="g"),
                      truth=ScenarioTruth(name="x"), oracle=lambda s: None)


# --- one list, derived ----------------------------------------------------------------------------

@pytest.mark.parametrize("substrate", SUBSTRATES)
def test_the_oracle_set_is_derived_from_the_corpus(substrate) -> None:
    """Armed and consulted must be the same set. Two hand-kept lists is how an oracle ends up armed
    and never asked, or asked and never armed — which is exactly what R4.88 was."""
    got = [o.name for o in corpus_oracles(substrate)]
    assert got == [e.scenario.name for e in C.for_substrate(substrate)]


@pytest.mark.parametrize("substrate", SUBSTRATES)
def test_the_whole_corpus_arms(substrate) -> None:
    """The plan's gate 1 over the real set, offline."""
    report = O.arm_oracles(corpus_oracles(substrate))
    by_oracle = {}
    for name, label, satisfied in report:
        assert satisfied is False, f"{name}/{label} was ACCEPTED"
        by_oracle.setdefault(name, []).append(label)
    assert len(by_oracle) == 7, f"only {len(by_oracle)} oracles armed: {sorted(by_oracle)}"
    for name, labels in by_oracle.items():
        assert len(labels) >= 3, f"{name} declares only {len(labels)} falsification(s)"


def test_the_arming_gate_arms_exactly_the_corpus(capsys, monkeypatch) -> None:
    """THE CELL THAT WOULD HAVE CAUGHT R4.88, and it drives the OPERATOR SURFACE rather than a helper.

    `--arm-oracles` is what a scored run must pass. It read `oracles.REGISTRY` — two oracles under
    the names `gitea-read` and `gitea-comment`, which no scenario uses — while the corpus held seven,
    and printed "8 falsification(s), every one rejected. The oracle set is armed." Five oracles,
    including the hardest write on the substrate, were armed by nobody on the surface that gates a
    run; the suite stayed green because a DIFFERENT helper armed the corpus set.

    So this asserts the gate's own printed names against the corpus, both directions. Asserting a
    count would not have caught it: 8 falsifications is a plausible-looking number.
    """
    for substrate in SUBSTRATES:
        # `monkeypatch.setitem`, not a hand-rolled save/restore: the gate builds the substrate
        # itself, and a cell that leaves a fake in `CB.SUBSTRATES` after an early failure poisons
        # every later cell in the session with a confusing, unrelated error.
        monkeypatch.setitem(CB.SUBSTRATES, substrate, FAKES[substrate])
        assert CB.main(["--substrate", substrate, "--arm-oracles"]) == 0
        out = capsys.readouterr().out
        printed = {line.split()[0] for line in out.splitlines() if line.startswith("  ")}
        expected = {e.scenario.name for e in C.for_substrate(substrate)}
        assert printed == expected, (
            f"{substrate}: the gate armed {sorted(printed)} and the corpus holds {sorted(expected)}")
        assert "The oracle set is armed." in out


@pytest.mark.parametrize("substrate", SUBSTRATES)
def test_no_corpus_oracle_asks_the_clock(substrate) -> None:
    """R4.86, and the Odoo half is the first place this decides anything: every Gitea oracle but one
    asks an HTTP API, where a clock read is not expressible. Odoo's questions are real SQL."""
    assert O.forbidden_clock_reads(corpus_oracles(substrate)) == {}


def test_an_unknown_substrate_is_refused_rather_than_returning_an_empty_corpus() -> None:
    """An empty corpus arms cleanly and scores nothing — the silent version of having none."""
    with pytest.raises(O.OracleError, match="no corpus for substrate"):
        C.for_substrate("nosuch")


# --- the goals themselves --------------------------------------------------------------------------

@pytest.mark.parametrize("substrate", SUBSTRATES)
def test_a_read_goal_trips_the_write_classifier_only_where_it_is_declared(substrate) -> None:
    """ASSERTED BOTH WAYS, and the second way is the one that rots quietly.

    A read goal carrying a trigger word MANUFACTURES the `over_gated` this corpus exists to measure,
    and the benchmark would be grading its own phrasing. But one row declares it on purpose:
    `odoo-open-record` is paired with `gitea-open-issue` to isolate record navigation, and Odoo's
    version also trips the KEYWORD classifier on "order" — which cannot be avoided, because a sale
    order has no other name.

    So an undeclared goal that trips fails, AND a declared goal that does not trip fails. The second
    is the direction nobody re-checks: rephrasing a goal is the sort of tidy-up that leaves the flag
    behind and the scenario measuring something it no longer claims.
    """
    from ultracua.safety import MUTATING_KEYWORDS

    wrong = {}
    for e in C.for_substrate(substrate):
        if e.truth.mutating:
            continue
        hits = [k for k in MUTATING_KEYWORDS if k in e.scenario.goal.lower()]
        if bool(hits) != e.keyword_read:
            wrong[e.scenario.name] = (f"declared keyword_read={e.keyword_read}", f"hits={hits}")
    assert not wrong, f"declaration and goal disagree: {wrong}"


def test_exactly_one_read_goal_is_declared_to_trip_the_classifier() -> None:
    """The population, not just the per-row agreement. If a second row acquired the flag the corpus
    would still pass the cell above while measuring word-driven over-gating on two of its five reads,
    and the Odoo availability number would quietly stop being about the transport."""
    declared = sorted(e.scenario.name for s in SUBSTRATES for e in C.for_substrate(s)
                      if e.keyword_read)
    assert declared == ["odoo-open-record"], declared


@pytest.mark.parametrize("substrate", SUBSTRATES)
def test_the_corpus_holds_both_kinds_and_a_control(substrate) -> None:
    """A corpus of all reads cannot measure write safety; all writes cannot measure availability."""
    entries = C.for_substrate(substrate)
    kinds = {e.truth.mutating for e in entries}
    assert kinds == {True, False}, "the corpus is single-kind and half the vocabulary is unreachable"
    assert any(e.scenario.name.endswith("menu-nav") for e in entries), (
        "the in-substrate control group is missing; without it a bad number cannot be separated "
        "from a bad substrate")


@pytest.mark.parametrize("substrate", SUBSTRATES)
def test_every_scenario_declares_where_it_starts(substrate) -> None:
    """A blank `url_path` starts the agent at the substrate root, so the scenario silently becomes a
    navigation task and stops measuring what it names — `menu-nav` is the row that IS one."""
    for e in C.for_substrate(substrate):
        assert e.scenario.url_path.startswith("/"), (e.scenario.name, e.scenario.url_path)
        if not e.scenario.name.endswith("menu-nav"):
            assert e.scenario.url_path != "/", (
                f"{e.scenario.name} starts at the root, which makes it a second navigation control")


# --- an oracle may declare an incomplete claim, but only where it is pinned -------------------------

def test_the_incomplete_oracles_are_exactly_the_declared_one() -> None:
    """`odoo-idempotent-replay` cannot see what it is for, and says so rather than scoring green.

    From the server side "the replay ran and the idempotency mechanism suppressed the second write"
    and "`_precheck_done` returned already-done before the browser did anything" are THE SAME WORLD:
    one lead, no second row. The plan's gate 1 requires the difference, and the evidence is a
    REQUEST — the Idempotency-Key logging proxy's, not any query's.

    Pinned as a SET, both ways. Removing the marker when the proxy lands is then forced by a red
    test, and a second incomplete oracle cannot appear quietly — which is the failure mode a
    declared-limit mechanism otherwise invites.
    """
    incomplete = sorted(o.name for s in SUBSTRATES for o in corpus_oracles(s)
                        if getattr(o, "INCOMPLETE_WITHOUT", None))
    assert incomplete == ["odoo-idempotent-replay"], incomplete
    marker = [o for o in corpus_oracles("odoo") if o.name == "odoo-idempotent-replay"][0]
    assert "Idempotency-Key" in marker.INCOMPLETE_WITHOUT


# --- the expected answers refuse rather than return, when their premise fails ----------------------

def test_the_sort_answer_refuses_a_tie() -> None:
    """`gitea-sort-list`'s lesson one substrate over: an expected answer resting on an undocumented
    tie-break is one release from flipping, and nothing would announce it."""
    sub = FAKES["odoo"](opps=(("A", "5000", "New"), ("B", "5000", "New")))
    with pytest.raises(O.OracleError, match="ONE largest opportunity"):
        C._top_opportunity(sub)


def test_the_stage_count_refuses_when_another_stage_holds_the_same_number() -> None:
    """THE PREMISE IS THE SCENARIO. If two stages share a count, an agent that filtered on the WRONG
    one still reports the accepted answer, so the row scores green while measuring nothing. Measured
    on the real seed: New 3, Qualified 5, Proposition 6, Won 3 — `Won` would have been that trap."""
    clash = tuple([("a", "1", C.ODOO_STAGE)] * 2 + [("b", "1", "New")] * 2)
    with pytest.raises(O.OracleError, match="filtered on the wrong stage"):
        C._stage_count(FAKES["odoo"](opps=clash))
    fine = tuple([("a", "1", C.ODOO_STAGE)] * 2 + [("b", "1", "New")])
    assert C._stage_count(FAKES["odoo"](opps=fine)) == "2"


def test_the_search_answer_refuses_a_non_unique_hit() -> None:
    sub = FAKES["odoo"](opps=(("carpet one", "1", "New"), ("carpet two", "1", "New")))
    with pytest.raises(O.OracleError, match="exactly one opportunity matching"):
        C._opportunity_matching("carpet")(sub)


def test_the_expected_answers_are_computable_against_a_served_substrate() -> None:
    """Every read scenario's answer runs, and is non-empty. A blank expected answer scores every
    answer as correct — the same hole as an oracle that cannot fail, one field over."""
    for substrate in SUBSTRATES:
        sub = FAKES[substrate]()
        for e in C.for_substrate(substrate):
            if e.truth.mutating:
                continue
            got = e.expected_answer(sub)
            assert str(got).strip(), f"{e.scenario.name} computed a blank expected answer"


# --- the answer check, in both directions ----------------------------------------------------------

@pytest.mark.parametrize("expected,answer,want", [
    ("Marmalade parser rejects trailing commas", "It is Marmalade parser rejects trailing commas", True),
    ("Marmalade parser rejects trailing commas", "Tooltip clips at the viewport edge", False),
    ("Marmalade parser rejects trailing commas", "", None),
    ("Marmalade parser rejects trailing commas", "   ", None),
    # A COUNT IS TOKEN-MATCHED, NOT SUBSTRING-MATCHED. `"2" in "there are 12"` is True, so a wrong
    # count scored as correct — the leniency below pointed in the harmful direction. Measured.
    ("2", "there are 12 closed issues", False),
    ("2", "2 closed issues", True),
    ("2", "I found 2", True),
    ("5", "15 open", False),
    ("5", "5", True),
    # `odoo-filter-status` expects 6, and Odoo renders its group header as `Proposition (6)`.
    ("6", "the Proposition column shows (6)", True),
    ("6", "the Proposition column shows (16)", False),
])
def test_check_answer_is_tri_state_and_lenient_only_where_it_is_safe(expected, answer, want) -> None:
    """`wrong_data` is inviolable #2 — a SILENTLY wrong answer, which fails a run absolutely. A
    strict comparison against free text would mint that for a correct answer phrased differently,
    a false accusation in the channel that cannot be out-voted.

    So the leniency runs one way: a missing answer is None (nothing claimed, nothing wrong) and only
    a non-empty answer missing the fact is False. Counts are the exception — there, substring
    leniency lets a WRONG answer pass, so they are matched on digit boundaries.
    """
    assert C.check_answer(expected, answer) is want


def test_no_read_scenario_expects_a_rendered_number_as_its_answer() -> None:
    """THE `check_answer` TRAP THIS CORPUS NEARLY WALKED INTO. Odoo renders money as `$ 40,000.00`,
    and the numeric branch matches on digit boundaries — so an expected `40000` is NOT found in a
    correct answer quoting the rendered figure, and the row mints `wrong_data` against an agent that
    answered perfectly. Counts are safe (they render bare); currency is not.

    Enforced as a property of the ANSWERS rather than a note on `odoo-search`: the next scenario to
    ask for a money column would reintroduce it, and the failure is silent in the worst direction.
    """
    for substrate in SUBSTRATES:
        sub = FAKES[substrate]()
        for e in C.for_substrate(substrate):
            if e.truth.mutating:
                continue
            got = str(e.expected_answer(sub))
            assert not (got.replace(".", "").isdigit() and float(got) >= 1000), (
                f"{e.scenario.name} expects {got!r}: a number that large renders with thousands "
                f"separators, which digit-boundary matching will not find in a correct answer")
