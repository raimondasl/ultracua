"""Every guard B4's corpus and oracle cells rest on, violated on purpose and watched go RED.

WHY THIS FILE EXISTS. "A cell that cannot fail is not a test." `tests/test_corpus.py` and
`tests/test_oracles.py` are green; so is a vacuous cell. And this slice's own history says the risk
is real: the gate that arms every scored run was reading a superseded list for a whole release
(R4.88), the suite was green throughout, and the cell that would have caught it did not exist.

WHY IN-PROCESS RATHER THAN `tests/mutations/`. `scripts/prove_red.py` installs a mutant by putting a
copy of `src/` first on `PYTHONPATH`, and pytest puts the repo ROOT at `sys.path[0]`, so a mutation
of anything under `benchmarks/` is never the module the tests import and every one reports as a
SURVIVOR while the guard is fine (R4.77). The discipline survives without the tool: `tests/_arming.py`
mutates in-process and refuses to score a `TypeError` as a kill.

WHAT THIS FILE CANNOT ARM, said plainly rather than discovered later: a probe pointed at the wrong
TABLE. `arm_oracles` replaces `probe` with the falsified rows, so a narrowed probe arms perfectly.
Only two things see that — `test_the_odoo_read_probe_reads_every_surface_it_claims` (offline, in CI)
and `benchmarks/oracle_liveness.py` (a real container) — and the first of them IS armed below.
"""

from __future__ import annotations

import dataclasses

import pytest

from benchmarks import corpus as C
from benchmarks import oracles as O
from tests._arming import assert_red
import tests.test_corpus as TC
import tests.test_oracles as TOR


# ---------------------------------------------------------------------------------------------
# the harness: swap one corpus entry, leaving every other row intact
# ---------------------------------------------------------------------------------------------

def swap(monkeypatch, substrate: str, name: str, **changes) -> None:
    """Replace one `CorpusEntry`. `dataclasses.replace` re-runs `__post_init__`, so a mutation that
    the entry's own validation already refuses raises HERE rather than reaching a cell — which is
    the honest outcome: that guard is the construction check, not the cell under test."""
    rows = C.for_substrate(substrate)
    assert any(e.scenario.name == name for e in rows), f"no scenario {name!r} to mutate"
    monkeypatch.setitem(
        C.CORPORA, substrate,
        tuple(dataclasses.replace(e, **changes) if e.scenario.name == name else e for e in rows))


def swap_goal(monkeypatch, substrate: str, name: str, goal: str) -> None:
    rows = C.for_substrate(substrate)
    hit = [e for e in rows if e.scenario.name == name]
    assert hit, f"no scenario {name!r} to mutate"
    swap(monkeypatch, substrate, name,
         scenario=dataclasses.replace(hit[0].scenario, goal=goal))


# ---------------------------------------------------------------------------------------------
# R4.88 — the gate must arm the set a scored run will consult
# ---------------------------------------------------------------------------------------------

def test_the_gate_cell_notices_the_gate_arming_a_stale_subset(monkeypatch, capsys) -> None:
    """THE MUTATION THAT IS R4.88 ITSELF. `--arm-oracles` armed two oracles under names no scenario
    used while the corpus held seven, and printed "The oracle set is armed."."""
    monkeypatch.setattr(C, "oracles_for",
                        lambda name, sub: [e.oracle(sub) for e in C.for_substrate(name)[:2]])
    print(assert_red(TC.test_the_arming_gate_arms_exactly_the_corpus, capsys, monkeypatch))


def test_the_derivation_cell_notices_a_second_registry_appearing(monkeypatch) -> None:
    """The structural half: a convenience lookup re-added to `benchmarks.oracles` is a second list,
    and a reviewer would not read it as one."""
    monkeypatch.setattr(O, "REGISTRY", {"gitea": ()}, raising=False)
    print(assert_red(TOR.test_the_oracle_set_has_exactly_one_derivation))


def test_the_fake_coverage_cell_notices_a_substrate_without_a_fake(monkeypatch) -> None:
    monkeypatch.setitem(C.CORPORA, "thirdthing", ())
    print(assert_red(TOR.test_every_corpus_substrate_has_a_unit_test_fake))


# ---------------------------------------------------------------------------------------------
# the keyword declaration, BOTH ways
# ---------------------------------------------------------------------------------------------

def test_the_keyword_cell_notices_an_undeclared_read_goal_that_trips(monkeypatch) -> None:
    """The manufacturing direction: a read goal carrying a trigger word produces the `over_gated`
    the corpus exists to MEASURE, and the benchmark grades its own phrasing."""
    swap_goal(monkeypatch, "gitea", "gitea-search",
              "find the issue about marmalade and confirm its title")
    print(assert_red(TC.test_a_read_goal_trips_the_write_classifier_only_where_it_is_declared,
                     "gitea"))


def test_the_keyword_cell_notices_a_declared_goal_that_no_longer_trips(monkeypatch) -> None:
    """The direction that rots quietly. Rephrasing a goal is the sort of tidy-up nobody re-checks,
    and it leaves the flag behind on a scenario that has stopped measuring what it claims."""
    swap_goal(monkeypatch, "odoo", "odoo-open-record",
              "open the record S00018 and report the customer on it")
    print(assert_red(TC.test_a_read_goal_trips_the_write_classifier_only_where_it_is_declared,
                     "odoo"))


def test_the_population_cell_notices_a_second_declared_row(monkeypatch) -> None:
    """Per-row agreement is not enough: two declared rows would pass the cell above while measuring
    word-driven over-gating on two of five reads, and the Odoo number would stop being about the
    transport without anything saying so."""
    swap_goal(monkeypatch, "odoo", "odoo-menu-nav",
              "open the CRM app and confirm how many pipeline stages are configured")
    swap(monkeypatch, "odoo", "odoo-menu-nav", keyword_read=True)
    print(assert_red(TC.test_exactly_one_read_goal_is_declared_to_trip_the_classifier))


# ---------------------------------------------------------------------------------------------
# the pairing, and where a scenario starts
# ---------------------------------------------------------------------------------------------

def test_the_pairing_cell_notices_a_scenario_losing_its_twin(monkeypatch) -> None:
    rows = C.for_substrate("odoo")
    hit = [e for e in rows if e.scenario.name == "odoo-search"][0]
    monkeypatch.setitem(C.CORPORA, "odoo", tuple(
        dataclasses.replace(e, scenario=dataclasses.replace(e.scenario, name="odoo-lookup"),
                            truth=dataclasses.replace(e.truth, name="odoo-lookup"))
        if e is hit else e for e in rows))
    print(assert_red(TC.test_the_two_halves_are_paired_scenario_for_scenario))


def test_the_start_url_cell_notices_a_read_starting_at_the_root(monkeypatch) -> None:
    """A blank start turns a read into a second navigation control, silently."""
    rows = C.for_substrate("odoo")
    swap(monkeypatch, "odoo", "odoo-search",
         scenario=dataclasses.replace(
             [e for e in rows if e.scenario.name == "odoo-search"][0].scenario, url_path="/"))
    print(assert_red(TC.test_every_scenario_declares_where_it_starts, "odoo"))


def test_the_corpus_size_cell_notices_a_dropped_scenario(monkeypatch) -> None:
    monkeypatch.setitem(C.CORPORA, "odoo", C.for_substrate("odoo")[:-1])
    print(assert_red(TC.test_every_entry_binds_a_scenario_a_truth_and_an_oracle, "odoo"))


# ---------------------------------------------------------------------------------------------
# the declared-incomplete oracle
# ---------------------------------------------------------------------------------------------

def test_the_incomplete_set_notices_a_second_marker_appearing(monkeypatch) -> None:
    """A declared-limit mechanism invites exactly this: a second oracle quietly excusing itself."""
    monkeypatch.setattr(O.OdooLeadOracle, "INCOMPLETE_WITHOUT", "something else", raising=False)
    print(assert_red(TC.test_the_incomplete_oracles_are_exactly_the_declared_one))


def test_the_incomplete_set_notices_the_marker_vanishing(monkeypatch) -> None:
    """And the other way: deleting it without the proxy existing would report a claim the oracle
    cannot make. When the proxy DOES land, this red test is the reminder to update the cell."""
    monkeypatch.delattr(O.OdooIdempotentReplayOracle, "INCOMPLETE_WITHOUT")
    print(assert_red(TC.test_the_incomplete_oracles_are_exactly_the_declared_one))


# ---------------------------------------------------------------------------------------------
# the expected answers, and their premises
# ---------------------------------------------------------------------------------------------

def test_the_tie_refusal_is_what_the_sort_cell_watches(monkeypatch) -> None:
    monkeypatch.setattr(C, "_top_opportunity",
                        lambda sub: max(n for n, _r, _s in C._opportunities(sub)))
    print(assert_red(TC.test_the_sort_answer_refuses_a_tie))


def test_the_stage_clash_refusal_is_what_the_filter_cell_watches(monkeypatch) -> None:
    """Measured on the real seed: New 3, Qualified 5, Proposition 6, Won 3 — so on `Won` an agent
    that filtered the WRONG stage reports the accepted answer and the row scores green."""
    def no_clash_check(sub):
        by = {}
        for _n, _r, stage in C._opportunities(sub):
            by[stage] = by.get(stage, 0) + 1
        return str(by[C.ODOO_STAGE])
    monkeypatch.setattr(C, "_stage_count", no_clash_check)
    print(assert_red(TC.test_the_stage_count_refuses_when_another_stage_holds_the_same_number))


def test_the_money_cell_notices_an_answer_that_renders_with_separators(monkeypatch) -> None:
    """`odoo-search` originally asked for the expected REVENUE. Odoo renders it `$ 40,000.00`, and
    digit-boundary matching would not find `40000` in a correct answer — a false `wrong_data`, which
    is inviolable #2 fired at an agent that answered perfectly."""
    swap(monkeypatch, "odoo", "odoo-search",
         expected_answer=lambda sub: "40000")
    print(assert_red(TC.test_no_read_scenario_expects_a_rendered_number_as_its_answer))


def test_the_blank_answer_cell_notices_an_expected_answer_that_computes_to_nothing(monkeypatch) -> None:
    """A blank expected answer scores EVERY answer as correct — an oracle that cannot fail, one
    field over."""
    swap(monkeypatch, "gitea", "gitea-search", expected_answer=lambda sub: "   ")
    print(assert_red(TC.test_the_expected_answers_are_computable_against_a_served_substrate))


# ---------------------------------------------------------------------------------------------
# the oracles themselves
# ---------------------------------------------------------------------------------------------

def test_the_surface_cell_notices_an_odoo_probe_narrowed_to_one_model(monkeypatch) -> None:
    """THE SHAPE THAT SHIPPED ON THE GITEA SIDE. `GiteaReadOracle` watched only the issue list while
    claiming a read changes nothing, and a comment posted during a read left it reporting True. The
    arming gate cannot see this at all — `_adjudicate_against` supplies the probe's rows — so this
    cell and the liveness pass are the only two instruments that can."""
    def leads_only(self):
        return O.Probe(tuple(sorted(("lead",) + tuple(r) for r in self._rows(self.QUERIES[0]))),
                       query=self.QUERIES[0], label=self.name)
    monkeypatch.setattr(O.OdooReadOracle, "probe", leads_only)
    print(assert_red(TOR.test_the_odoo_read_probe_reads_every_surface_it_claims))


def test_the_clock_scan_notices_an_odoo_query_asking_the_time(monkeypatch) -> None:
    """R4.86, now with real SQL to police. The Odoo app container's clock is pinned and its Postgres
    container's is not, so `now()` answers a different question than the UI shows — and a different
    one again next month."""
    monkeypatch.setattr(O.OdooLeadOracle, "QUERIES",
                        ("SELECT id, name, type FROM crm_lead WHERE create_date < now()",))
    print(assert_red(TC.test_no_corpus_oracle_asks_the_clock, "odoo"))


def test_the_double_cell_notices_the_odoo_identity_losing_its_key(monkeypatch) -> None:
    """R4.87 on the Odoo side: without the id, two identical leads collapse to one member of a set,
    `len(matched)` reads 1, and a write that fired twice scores `true`."""
    monkeypatch.setattr(O.OdooLeadOracle, "identity_of",
                        lambda self, r: (str(r["name"]), str(r["type"])))
    print(assert_red(TOR.test_a_write_oracle_distinguishes_two_identical_landings))


def test_the_double_falsification_cell_notices_two_rows_that_are_not_the_same_write(
        monkeypatch) -> None:
    """The trap this project documents best: a row labelled DOUBLE whose two records differ in a
    user-visible field is two different writes, and it passes while the hole is wide open."""
    def sloppy(self):
        base, n, ty = O.Probe(()), self.expect_name, self.expect_type
        return (("no lead at all", base, ()),
                ("a lead with the WRONG name", base, (("101", n + " (not this)", ty),)),
                ("DOUBLE-submitted, identical content", base,
                 (("101", n, ty), ("102", n + " ", ty))))
    monkeypatch.setattr(O.OdooLeadOracle, "falsifications", sloppy)
    print(assert_red(TOR.test_the_double_falsification_uses_genuinely_identical_content))


def test_the_arming_cell_notices_an_odoo_read_oracle_that_accepts_a_changed_world(
        monkeypatch) -> None:
    """The gate's own reason for existing, aimed at the new oracles: an oracle that cannot fail
    approves everything and publishes a perfect availability rate."""
    monkeypatch.setattr(O.OdooReadOracle, "expected_delta",
                        lambda self, before: frozenset(
                            {("lead", "99", "Something new", "1", "t")}))
    print(assert_red(TC.test_the_whole_corpus_arms, "odoo"))
