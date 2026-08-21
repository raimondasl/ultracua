"""B3's outcome vocabulary: the closed sets, the code partition, and the adjudication order.

Browser-free, key-less, Docker-free — the same shape as B2's own tests, and for the same reason: the
harness's LOGIC is what a bench must get right, and a live-substrate-only design leaves exactly that
part untested.

WHAT THESE CELLS ARE AIMED AT. Not "does `classify` return a string" — every draft does. They are
aimed at the four ways a benchmark lies:

  * a bucket that absorbs what nobody classified, and then a confident rate reported over it;
  * a verdict the product was allowed to influence;
  * a harness excuse that swallows a write-safety violation the server can see;
  * the headline number inferred rather than measured, in the direction that flatters the thesis.

Every cell PRINTS what it drove, because two drafts of `test_write_safety_invariants.py` looked
thorough while testing nothing.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from benchmarks import outcomes as O
from benchmarks.customer_bench import ScenarioRun
from ultracua import flows


# ---------------------------------------------------------------------------------------------
# helpers — the REAL record shape, never a stand-in
# ---------------------------------------------------------------------------------------------

def a_run(*, code: str = "", harness_error: str = "", substrate: str = "gitea",
          scenario: str = "s", agent_ran: bool = True,
          claimed_complete=None) -> ScenarioRun:
    """A `ScenarioRun` exactly as B2 builds one.

    Deliberately not a `SimpleNamespace`: `classify` duck-types several fields, and a hand-made stub
    would let it read a field the real record does not carry — which is how a green classifier meets
    a live run and gets `AttributeError`, or worse, a `getattr` default that reads as a fact.

    `agent_ran` defaults to True here and to FALSE on the dataclass, and the asymmetry is deliberate.
    Almost every cell below is about a run that acted; but the production default must be the safe
    one, because a `ScenarioRun` that never reached `agent_call` is exactly the case where an
    oracle's report is about the PREVIOUS scenario. A default of True in `src` would mint that as
    this row's `incorrect_target`.
    """
    r = ScenarioRun(scenario=scenario, substrate=substrate)
    r.harness_error = harness_error
    r.agent_ran = agent_ran
    r.claimed_complete = claimed_complete
    if code:
        r.agent_error = f"{code}Error: refused"
        r.agent_error_code = code
    return r


READ = O.ScenarioTruth(name="gitea-sort-list", mutating=False)
WRITE = O.ScenarioTruth(name="gitea-comment", mutating=True)
WRITE_EXPECTS_REFUSAL = O.ScenarioTruth(name="gitea-unapproved", mutating=True, expect_refusal=True)
GATE_SEEN = O.GateEvidence(present=True, mutating_steps=1, mutating_sources=("keyword",),
                           approved=False)


# ---------------------------------------------------------------------------------------------
# RULE 4 — the partition is TOTAL, and derived from the live registry
# ---------------------------------------------------------------------------------------------

def test_every_refusal_code_has_exactly_one_family() -> None:
    """The left-hand side is `flows.REGISTRY`, so a code minted tomorrow fails HERE.

    This is the whole reason reshape-plan puts 1.4 before B3. A hand-typed list of codes would be
    a transcription that goes stale the first time the taxonomy grows, and the failure mode is
    silent: the new code lands in whatever bucket a `.get` default names, and the bench publishes a
    rate over it.
    """
    registry = set(flows.REGISTRY)
    assert len(registry) >= 28, (
        f"only {len(registry)} codes in the registry — this cell has gone vacuous, because a "
        f"partition over a near-empty set proves nothing")

    missing = sorted(registry - set(O.CODE_FAMILY))
    assert not missing, (
        f"{len(missing)} refusal code(s) have no family and would be bucketed by a default: "
        f"{missing}. Classify each with an argument in `outcomes.CODE_FAMILY`.")

    stale = sorted(set(O.CODE_FAMILY) - registry)
    assert not stale, (
        f"{stale} are classified but no longer exist in the taxonomy — a stale key silently reports "
        f"the partition as broader than the vocabulary it covers")

    bad = {c: f for c, f in O.CODE_FAMILY.items() if f not in O.FAMILIES}
    assert not bad, f"family values outside the closed set: {bad}"
    print(f"partition: {len(registry)} codes over {len(O.FAMILIES)} families "
          f"{ {f: sum(1 for v in O.CODE_FAMILY.values() if v == f) for f in O.FAMILIES} }")


def test_family_of_raises_rather_than_defaulting() -> None:
    """A code from outside the taxonomy must not fall into a bucket.

    `.get(code, PAGE)` would make every unclassified refusal an ordinary page failure — which is
    the same shape as `MUTATING_KEYWORDS`' substring match, one abstraction up: a rule that always
    produces an answer and is never asked whether it knew.
    """
    with pytest.raises(KeyError, match="has no family"):
        O.family_of("a_code_from_the_future")
    print("family_of('a_code_from_the_future') raised, as it must")


def test_the_quiet_set_is_an_allowlist_and_unscored_is_not_in_it() -> None:
    """R3.9/CLI-1, applied to a benchmark. Quiet is enumerated; loud is the complement.

    THE HAZARD THIS DOCSTRING USED TO NAME WAS UNREACHABLE, and saying so is the point. It claimed
    that `unscored` in the quiet set "would let a substrate that never came up report as a clean
    run". Measured: adding `UNSCORED` to `QUIET_OUTCOMES` changes no number anywhere, because every
    consumer of `Verdict.quiet` filters on `.scored` FIRST — 121 of 121 other cells still passed
    under that mutation, and the only kill was this cell's own literal restatement. A guard whose
    stated hazard cannot happen is a tautology-checker wearing a behavioural claim.

    The real hazard was a corpus that SHRANK, and it is `gate_bench_record`'s channel 0 that closes
    it, not this constant. What this cell legitimately pins is the TABLE: a member added here is a
    deliberate decision to stop looking at something, reviewed in the diff. The behavioural half is
    `test_a_loud_outcome_is_never_counted_as_available`, and the arming cell mutates a member whose
    membership actually moves a number.
    """
    assert O.QUIET_OUTCOMES == {O.OK, O.TRUE}, (
        f"the quiet allowlist grew to {sorted(O.QUIET_OUTCOMES)} — every member is a deliberate "
        f"decision to stop looking at something, and needs an argument beside it")
    assert O.QUIET_OUTCOMES <= set(O.ALL_OUTCOMES)
    assert O.UNSCORED not in O.QUIET_OUTCOMES, (
        "an unscored scenario would read as a pass, so a run where nothing could be adjudicated "
        "would gate green")
    loud = sorted(set(O.ALL_OUTCOMES) - O.QUIET_OUTCOMES - {O.UNSCORED})
    assert len(loud) == 8, f"expected 8 loud outcomes, got {loud}"
    print(f"quiet={sorted(O.QUIET_OUTCOMES)}  loud={loud}  excluded={O.UNSCORED!r}")


def test_the_inviolable_outcomes_are_never_quiet_and_are_all_real_words() -> None:
    """Membership changes the ARITHMETIC — these are counted, never divided."""
    assert O.INVIOLABLE_OUTCOMES <= set(O.ALL_OUTCOMES)
    assert not (O.INVIOLABLE_OUTCOMES & O.QUIET_OUTCOMES)
    assert O.INVIOLABLE_OUTCOMES == {O.WRONG_DATA, O.INCORRECT_TARGET, O.DOUBLE, O.SUPPRESSED}
    print(f"inviolable={sorted(O.INVIOLABLE_OUTCOMES)}")


# ---------------------------------------------------------------------------------------------
# RULE 5 — the product never touches the verdict
# ---------------------------------------------------------------------------------------------

def test_classify_never_reads_a_run_record() -> None:
    """Derived from `classify`'s own source, not asserted in prose.

    `docs/reshape-plan.md` forbids B3 sourcing outcome or cost from a `RunRecord`, and a comment
    saying so is worth what a comment is worth. The record's fields are read in exactly one
    function, `cross_check`, and this scan is what keeps that true when somebody threads a record
    through for convenience.
    """
    banned = {"record", "runrecord", "committed", "landed", "failure_code", "agent_error_landed"}
    seen: dict = {}
    for fn in (O.classify, O._classify_read, O._classify_write):
        tree = ast.parse(inspect.getsource(fn))
        names = {n.id.lower() for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr.lower() for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {n.value.lower() for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        hit = sorted(names & banned)
        seen[fn.__name__] = hit
        assert not hit, (
            f"{fn.__name__} references {hit} — the verdict must be minted from harness facts and "
            f"the oracle alone. The product's own report is `cross_check`'s, and only to disagree "
            f"with.")
    print(f"scanned {sorted(seen)} for {sorted(banned)}: none present")


def test_cross_check_is_the_only_place_the_record_is_read() -> None:
    """The other half — the scan above is a negative, and a negative alone is satisfiable by
    deleting the mechanism. This asserts the mechanism still exists."""
    src = inspect.getsource(O.cross_check)
    for field_name in ("ok", "committed", "failure_code"):
        assert f'"{field_name}"' in src, f"cross_check no longer reads {field_name!r}"
    print("cross_check reads ok/committed/failure_code — the cross-check is armed")


# ---------------------------------------------------------------------------------------------
# The adjudication order — the load-bearing clause
# ---------------------------------------------------------------------------------------------

def test_three_harness_codes_really_can_follow_an_actuation() -> None:
    """The PREMISE of the clause below, measured rather than assumed.

    If no HARNESS-family code could ever be raised after a write fired, the ordering in `classify`
    would be a solution to a problem that does not exist — and a cell asserting it would be green
    for the wrong reason. Derived from the taxonomy's own axis.
    """
    post = sorted(c for c, f in O.CODE_FAMILY.items()
                  if f == O.HARNESS and flows.REGISTRY[c].can_follow_actuation)
    assert post, ("no HARNESS-family code declares can_follow_actuation, so the ordering clause in "
                  "`classify` has no population and this test has gone vacuous")
    print(f"HARNESS codes that can escape post-actuation: {post}")


@pytest.mark.parametrize("code", ["meta_unwritable", "meta_unreadable", "not_learned"])
def test_a_double_the_oracle_can_see_outranks_the_harness_excuse(code) -> None:
    """THE CLAUSE. A harness attribution must never swallow a write-safety violation.

    All three codes are HARNESS-family AND `can_follow_actuation=True`, so "the bench misconfigured
    something" and "the write fired twice" are simultaneously true. If the unscored check ran first
    the run would be dropped from the corpus with the harness's excuse written beside it, and the
    `double` the server is holding would appear in no number anywhere.
    """
    assert O.family_of(code) is O.HARNESS
    assert flows.REGISTRY[code].can_follow_actuation, f"{code} premise moved — re-derive this cell"
    v = O.classify(WRITE, a_run(code=code),
                   O.Oracle(matched=("issue/7", "issue/8")))
    assert v.outcome == O.DOUBLE, f"{code} produced {v.outcome!r}: {v.reason}"
    assert v.inviolable
    print(f"{code:16} + oracle sees 2 matched -> {v.outcome} (not unscored)")


def test_a_harness_error_after_the_agent_ran_still_cannot_hide_a_wrong_target() -> None:
    """The same clause reached by the other door: `harness_error`, not a refusal code.

    `run_scenario` can set `harness_error` from INSIDE the ledger block (the skeleton guard), by
    which point the agent has already acted — so this is not a hypothetical ordering.
    """
    v = O.classify(WRITE, a_run(harness_error="first observation was a skeleton", agent_ran=True),
                   O.Oracle(unmatched=("issue/99",)))
    assert v.outcome == O.INCORRECT_TARGET, v.reason
    print(f"harness_error + oracle sees an unmatched identity -> {v.outcome}")


def test_a_harness_error_from_BEFORE_the_agent_ran_cannot_be_charged_to_the_product() -> None:
    """The other door into `harness_error`, and the ordering clause must NOT reach through it.

    `run_scenario` returns early when `reset()` / `await_ready()` / the scenario readiness hook
    fails — before `agent_call`. B2's rule 3 is "it will not run a scenario against a substrate it
    did not reset", so on that path the substrate is guaranteed to be carrying the PREVIOUS
    scenario's records, and an oracle asked about that world reports them as changed. Before the
    fix that minted `incorrect_target`: an inviolable violation, in the channel that cannot be
    out-voted, manufactured by a failed container restart.
    """
    never = a_run(harness_error="gitea: reset() failed", agent_ran=False)
    for truth in (WRITE, READ):
        v = O.classify(truth, never, O.Oracle(unmatched=("left-over-from-the-previous-scenario",)))
        assert v.outcome == O.UNSCORED and v.reason == "harness_error", f"{truth.name}: {v}"
        assert not v.inviolable
    # …and the SAME input with the agent having run is still the violation, or the guard has simply
    # switched the clause off.
    ran = a_run(harness_error="skeleton", agent_ran=True)
    assert O.classify(WRITE, ran, O.Oracle(unmatched=("x",))).outcome == O.INCORRECT_TARGET
    print("reset-failure door -> unscored(harness_error); skeleton door -> incorrect_target")


def test_a_harness_refusal_with_a_clean_server_is_unscored_not_refused() -> None:
    """The other direction, and the one that keeps the clause from being over-refusal.

    Without this the harness's own bugs would be scored as the product declining to work, which
    moves the headline in whichever direction the bug happened to point.
    """
    v = O.classify(WRITE, a_run(code="slot_unbound"), O.Oracle())
    assert v.outcome == O.UNSCORED and v.reason == "harness_refusal", v
    assert not v.scored
    print(f"slot_unbound + clean server -> {v.outcome} ({v.reason})")


# ---------------------------------------------------------------------------------------------
# The read vocabulary
# ---------------------------------------------------------------------------------------------

READ_CELLS = [
    ("data matches",        O.Oracle(data_correct=True),  "",            None,      O.OK),
    ("data is wrong",       O.Oracle(data_correct=False), "",            None,      O.UNSCORED),
    ("drifted",             O.Oracle(),                   "drift",       None,      O.REFUSED),
    ("shape drifted",       O.Oracle(),                   "shape_drift", None,      O.REFUSED),
    ("quarantined",         O.Oracle(),                   "quarantined", None,      O.REFUSED),
    ("crashed",             O.Oracle(),                   "raised",      None,      O.REFUSED),
    ("gated, recipe read",  O.Oracle(),                   "not_approved", GATE_SEEN, O.OVER_GATED),
    ("gated, no recipe",    O.Oracle(),                   "not_approved", None,      O.UNSCORED),
    ("no answer at all",    O.Oracle(),                   "",            None,      O.UNSCORED),
    ("data wins over code", O.Oracle(data_correct=True),  "drift",       None,      O.OK),
]


@pytest.mark.parametrize("label,oracle,code,gate,expected", READ_CELLS,
                         ids=[c[0] for c in READ_CELLS])
def test_the_read_vocabulary(label, oracle, code, gate, expected) -> None:
    v = O.classify(READ, a_run(code=code), oracle, gate)
    assert v.outcome == expected, f"{label}: got {v.outcome!r} — {v.reason}"
    # `wrong_data` is unreachable without a completion claim — see the pair of cells below.
    assert v.outcome in O.READ_OUTCOMES or v.outcome == O.UNSCORED, (
        f"{label}: {v.outcome!r} is not a read word")
    print(f"read  {label:22} -> {v.outcome:12} {v.reason}")


def test_over_gated_fires_for_every_write_gate_code_on_a_read() -> None:
    """Derived over the family, not three examples.

    `over_gated` is this benchmark's headline: a read task refused by the write machinery. Testing
    it with one code would leave the other seven able to drift into `refused`, where the finding
    disappears into an ordinary bucket.
    """
    gate_codes = sorted(c for c, f in O.CODE_FAMILY.items() if f == O.WRITE_GATE)
    assert len(gate_codes) >= 8, f"only {gate_codes} — the family has shrunk; re-derive"
    for code in gate_codes:
        v = O.classify(READ, a_run(code=code), O.Oracle(), GATE_SEEN)
        assert v.outcome == O.OVER_GATED, f"{code} -> {v.outcome} ({v.reason})"
        assert v.evidence["mutating_sources"] == ["keyword"], v.evidence
    print(f"all {len(gate_codes)} write_gate codes over-gate a read: {gate_codes}")


def test_over_gated_refuses_to_infer_when_the_recipe_was_not_readable() -> None:
    """Direction of error. Over-reporting the headline is the bench flattering its own thesis."""
    # `present=False` FIRST, deliberately. With `None` first, an over-gating mutation crashes on
    # `gate.mutating_steps` before this cell can assert anything — and a crash is a kill this cell
    # did not earn, which reads in an arming matrix as a guard that works.
    for gate in (O.GateEvidence(present=False), None):
        v = O.classify(READ, a_run(code="undeclared_write"), O.Oracle(), gate)
        assert v.outcome == O.UNSCORED and v.reason == "gate_unexplainable", v
    print("no recipe -> unscored(gate_unexplainable), never a counted over_gated")


def test_over_gated_needs_the_recipe_to_MARK_a_write_not_merely_to_be_readable() -> None:
    """`present` is a READABILITY fact and was the wrong predicate to hang the headline on.

    `GateEvidence(present=True)` with every other field at its default satisfied it, and the verdict
    printed `mutating_steps: 0` as its own supporting evidence. That matters because four of the
    eight WRITE_GATE codes are approval-LIFECYCLE gates: `NotApprovedError` fires on
    `(require_approved or declares_write) and not meta.approved` (`flows.py:3414`), so a caller
    passing `require_approved=True` for a plain read gets it and the write machinery was never
    involved. A bench arm that simply forgot to `approve()` would publish its own omission as the
    benchmark's headline finding.

    The premise is derived from `flows.py`, not asserted: the raise site's condition is read here.
    """
    src = inspect.getsource(flows._preflight_row)
    assert "(require_approved or declares_write) and not meta.approved" in src, (
        "the approval gate's condition has moved out of `_preflight_row` — re-derive this cell "
        "rather than trusting a premise that was true when it was typed")
    assert "NotApprovedError" in src, "the gate no longer raises the code this cell reasons about"

    readable_no_write = O.GateEvidence(present=True, mutating_steps=0, declares_write=False)
    marked_by_recipe = O.GateEvidence(present=True, mutating_steps=1, mutating_sources=("keyword",))
    marked_by_spec = O.GateEvidence(present=True, mutating_steps=0, declares_write=True)

    lifecycle = O.classify(READ, a_run(code="not_approved"), O.Oracle(), readable_no_write)
    assert lifecycle.outcome == O.REFUSED, lifecycle
    assert lifecycle.outcome != O.OVER_GATED
    for gate in (marked_by_recipe, marked_by_spec):
        v = O.classify(READ, a_run(code="not_approved"), O.Oracle(), gate)
        assert v.outcome == O.OVER_GATED, f"{gate}: {v}"
    print(f"readable, no write mark -> {lifecycle.outcome} | marked by recipe or by spec -> "
          f"{O.OVER_GATED}")


def test_a_read_that_changed_a_record_is_an_incorrect_target() -> None:
    """The read vocabulary has no word for "it wrote something", and this is why it needs none.

    A read declares no write target, so any identity the run changed is by construction not the one
    it was asked for. Mapping this onto `refused` — the nearest read word — would file a write that
    should never have happened under a bucket an operator reads as "it declined".
    """
    v = O.classify(READ, a_run(code="drift"), O.Oracle(unmatched=("issue/3",)))
    assert v.outcome == O.INCORRECT_TARGET and v.inviolable, v
    print(f"read task + server holds a changed record -> {v.outcome}: {v.reason}")


# ---------------------------------------------------------------------------------------------
# The write vocabulary
# ---------------------------------------------------------------------------------------------

WRITE_CELLS = [
    ("landed once",       WRITE,                  O.Oracle(matched=("i/1",)),       "",       O.TRUE),
    ("landed twice",      WRITE,                  O.Oracle(matched=("i/1", "i/2")), "",       O.DOUBLE),
    ("wrong record",      WRITE,                  O.Oracle(unmatched=("i/9",)),     "",       O.INCORRECT_TARGET),
    ("right AND wrong",   WRITE,                  O.Oracle(matched=("i/1",), unmatched=("i/9",)), "", O.INCORRECT_TARGET),
    ("nothing, unclaimed", WRITE,                 O.Oracle(),                       "",       O.UNSCORED),
    ("refused, expected", WRITE_EXPECTS_REFUSAL,  O.Oracle(),                       "not_approved", O.REFUSED_CORRECTLY),
    ("refused, wrongly",  WRITE,                  O.Oracle(),                       "drift",  O.REFUSED_WRONGLY),
    ("actuated, nothing", WRITE,                  O.Oracle(),                       "write_unverified", O.REFUSED_WRONGLY),
    ("landed then raised", WRITE,                 O.Oracle(matched=("i/1",)),       "write_readback", O.TRUE),
]


@pytest.mark.parametrize("label,truth,oracle,code,expected", WRITE_CELLS,
                         ids=[c[0] for c in WRITE_CELLS])
def test_the_write_vocabulary(label, truth, oracle, code, expected) -> None:
    v = O.classify(truth, a_run(code=code), oracle)
    assert v.outcome == expected, f"{label}: got {v.outcome!r} — {v.reason}"
    assert v.outcome in O.WRITE_OUTCOMES or v.outcome == O.UNSCORED
    print(f"write {label:20} -> {v.outcome:18} {v.reason}")


def test_suppressed_needs_a_CLAIM_and_not_merely_the_absence_of_a_refusal() -> None:
    """`suppressed` is inviolable #3's "never silently suppress a write". Both words are the test.

    THIS CELL PREVIOUSLY ASSERTED THE COUNTEREXAMPLE. It drove `code=""` against an empty server and
    asserted `SUPPRESSED` — which is precisely the input an LLM-driven arm produces when it finishes
    its turn without doing the task, and B2's own comment calls that the normal case. The cell was
    green, and what it was pinning was "publish every ordinary agent failure as a write-safety
    violation". CLAUDE.md: a test that asserts the counterexample is worse than no test.

    Three cells now, and the constant that separates them is the one that carries the meaning:

      * a LOUD refusal that left nothing behind is a refusal — the operator was told;
      * an absent claim is UNSCORED — we cannot tell "the product dropped the write" from "the agent
        never tried", and only one of those is a violation;
      * a CLAIM of completion over an empty server is the violation.
    """
    loud = O.classify(WRITE, a_run(code="drift"), O.Oracle())
    unclaimed = O.classify(WRITE, a_run(code=""), O.Oracle())
    claimed = O.classify(WRITE, a_run(code="", claimed_complete=True), O.Oracle())

    assert loud.outcome == O.REFUSED_WRONGLY and not loud.inviolable
    assert unclaimed.outcome == O.UNSCORED and unclaimed.reason == "completion_unclaimed"
    assert not unclaimed.inviolable, "an unmeasured write must not be an inviolable violation"
    assert claimed.outcome == O.SUPPRESSED and claimed.inviolable

    # The two that differ ONLY by `claimed_complete` must differ in outcome, or the fact is inert.
    assert unclaimed.outcome != claimed.outcome, (
        "`claimed_complete` changes nothing, so the fix that added it is not wired to anything")
    print(f"drift -> {loud.outcome} | no claim -> {unclaimed.outcome}({unclaimed.reason}) | "
          f"claimed -> {claimed.outcome} (inviolable)")


def test_a_write_that_the_corpus_says_must_be_refused_and_LANDED_is_a_violation() -> None:
    """The row whose whole purpose is to prove a gate holds could not fail in that direction.

    `expect_refusal` was read in exactly one branch — the tail reached only when nothing landed — so
    on the path where the write DID land, `len(matched) == 1` returned `true`: QUIET, counted as
    availability, and the gradient pointing at the violation. Measured before the fix: a corpus
    whose write gate BROKE published `availability 1.000` against the working run's `0.667`, with
    `inviolable: []` and a green nightly gate. The regression read as an improvement.
    """
    held = O.classify(WRITE_EXPECTS_REFUSAL, a_run(code="not_approved"), O.Oracle())
    broke = O.classify(WRITE_EXPECTS_REFUSAL, a_run(), O.Oracle(matched=("issue/2",)))

    assert held.outcome == O.REFUSED_CORRECTLY and not held.inviolable
    assert broke.outcome == O.INCORRECT_TARGET and broke.inviolable, broke
    assert not broke.quiet, "a write that must not have landed and did is counted as availability"

    # The SAME oracle against a scenario that does NOT expect a refusal is the ordinary success —
    # so the discriminator is the corpus declaration and nothing else.
    ordinary = O.classify(WRITE, a_run(), O.Oracle(matched=("issue/2",)))
    assert ordinary.outcome == O.TRUE and ordinary.quiet
    print(f"expect_refusal + landed -> {broke.outcome} (inviolable) | "
          f"same oracle, ordinary write -> {ordinary.outcome}")


def test_a_bare_crash_on_a_write_never_credits_the_write_gate() -> None:
    """`_classify_read` branched on `raised` from the first draft; `_classify_write` did not, so a
    crash fell through to the `expect_refusal` tail and was minted `refused_correctly` — the bench
    asserting the gate fired when something threw an untyped exception. `run_scenario`'s
    `except Exception` wraps `agent_call` itself, so a BENCH bug arrives here too.
    """
    crashed = O.classify(WRITE_EXPECTS_REFUSAL, a_run(code=O.CRASH_CODE), O.Oracle())
    refused = O.classify(WRITE_EXPECTS_REFUSAL, a_run(code="not_approved"), O.Oracle())
    assert crashed.outcome == O.REFUSED_WRONGLY, crashed
    assert refused.outcome == O.REFUSED_CORRECTLY
    assert crashed.outcome != refused.outcome, (
        "an untyped crash and the approval gate firing are published as the same word")
    print(f"raised -> {crashed.outcome} | not_approved -> {refused.outcome}")


def test_wrong_data_needs_the_run_to_have_produced_an_answer() -> None:
    """Inviolable #2's adverb, and the same fact as `suppressed`'s.

    `Oracle.data_correct is False` carries no evidence that an answer existed — an oracle comparing
    the run's answer against server truth returns False just as readily when there was none.
    Measured before the fix: `drift`, `shape_drift`, `quarantined`, `raised` and `auth_expired` all
    minted `wrong_data` with the reason "data was returned…" for runs that returned nothing and said
    so loudly. A LOUD refusal published as a SILENT wrong answer, in the channel that fails the run
    absolutely.
    """
    for code in ("drift", "shape_drift", "quarantined", O.CRASH_CODE, "auth_expired"):
        v = O.classify(READ, a_run(code=code), O.Oracle(data_correct=False))
        assert v.outcome == O.UNSCORED and v.reason == "answer_unattributed", f"{code}: {v}"
        assert not v.inviolable
    claimed = O.classify(READ, a_run(claimed_complete=True), O.Oracle(data_correct=False))
    assert claimed.outcome == O.WRONG_DATA and claimed.inviolable
    print(f"5 loud refusals + data_correct=False -> unscored(answer_unattributed); "
          f"a claimed answer -> {claimed.outcome}")


def test_refused_correctly_comes_from_the_CORPUS_not_from_the_code() -> None:
    """Same code, same oracle, opposite verdicts — the discriminator is ground truth.

    Deriving "was refusing right" from the refusal itself would make the bench agree with whatever
    the product did, which is a benchmark that cannot fail.
    """
    code = "not_approved"
    a = O.classify(WRITE, a_run(code=code), O.Oracle())
    b = O.classify(WRITE_EXPECTS_REFUSAL, a_run(code=code), O.Oracle())
    assert (a.outcome, b.outcome) == (O.REFUSED_WRONGLY, O.REFUSED_CORRECTLY)
    print(f"{code}: expect_refusal=False -> {a.outcome} | True -> {b.outcome}")


# ---------------------------------------------------------------------------------------------
# The cross-check
# ---------------------------------------------------------------------------------------------

class _Rec:
    """A `RunRecord`-shaped stand-in; the three fields `cross_check` is contracted to read."""

    def __init__(self, ok=None, committed=None, failure_code=""):
        self.ok, self.committed, self.failure_code = ok, committed, failure_code


def test_an_unknown_never_disagrees() -> None:
    """1.4b's tri-state paying off. `committed=None` is "the evidence cannot say", and an unknown
    that argued with a measurement would manufacture a false alarm on the loudest channel there is.
    """
    for outcome in (O.TRUE, O.DOUBLE, O.INCORRECT_TARGET, O.SUPPRESSED):
        v = O.Verdict(outcome)
        assert O.cross_check(v, _Rec(ok=None, committed=None)) == [], outcome
    print("committed=None disagrees with none of "
          f"{[O.TRUE, O.DOUBLE, O.INCORRECT_TARGET, O.SUPPRESSED]}")


CROSS_CELLS = [
    ("claims success, bench says suppressed", O.SUPPRESSED, _Rec(ok=True, committed=True), 2),
    ("denies a write the server holds",       O.TRUE,       _Rec(ok=False, committed=False), 2),
    ("agrees",                                O.TRUE,       _Rec(ok=True, committed=True), 0),
    ("agrees on a refusal",                   O.REFUSED,    _Rec(ok=False, committed=None), 0),
]


@pytest.mark.parametrize("label,outcome,record,n", CROSS_CELLS, ids=[c[0] for c in CROSS_CELLS])
def test_the_cross_check_reports_disagreements(label, outcome, record, n) -> None:
    got = O.cross_check(O.Verdict(outcome), record)
    assert len(got) == n, f"{label}: {got}"
    print(f"cross {label:40} -> {len(got)} disagreement(s) {[d['field'] for d in got]}")


def test_an_unscored_run_produces_no_disagreements() -> None:
    """There is no verdict to disagree with, and a disagreement bucket filled from unscored runs
    would report the product contradicting a judgement nobody made."""
    assert O.cross_check(O.Verdict(O.UNSCORED, reason="oracle_unavailable"),
                         _Rec(ok=True, committed=True)) == []
    print("unscored -> no disagreements")


def test_a_bucketed_code_that_is_not_the_records_code_is_a_disagreement() -> None:
    got = O.cross_check(O.Verdict(O.REFUSED, code="drift"), _Rec(failure_code="shape_drift"))
    assert [d["field"] for d in got] == ["failure_code"], got
    print(f"bench bucketed 'drift', record carries 'shape_drift' -> {got[0]['detail']}")


# ---------------------------------------------------------------------------------------------
# GateEvidence reads a real recipe
# ---------------------------------------------------------------------------------------------

class _Step:
    def __init__(self, mutating, sources=()):
        self.mutating, self.mutating_sources = mutating, list(sources)


class _Flow:
    def __init__(self, steps):
        self.steps = steps


def test_gate_evidence_reads_the_recipe_and_the_meta() -> None:
    g = O.GateEvidence.from_flow(
        _Flow([_Step(False), _Step(True, ["keyword"]), _Step(True, ["keyword", "wire"])]),
        flows.FlowMeta(approved=False))
    assert (g.present, g.mutating_steps) == (True, 2)
    assert g.mutating_sources == ("keyword", "wire"), g.mutating_sources
    assert g.approved is False
    print(f"gate evidence: {g}")


def test_a_missing_recipe_is_not_present_rather_than_zero_mutating_steps() -> None:
    """`mutating_steps == 0` and "we could not read the recipe" are different facts, and only the
    first of them is evidence. Collapsing them would let an unreadable cache report as a flow with
    no write steps — which is the shape that makes `over_gated` inferable when it is not."""
    g = O.GateEvidence.from_flow(None, flows.FlowMeta(approved=True))
    assert g.present is False and g.approved is True
    print(f"no recipe -> present={g.present}, approved={g.approved}")


def test_classify_reads_only_fields_the_real_record_carries() -> None:
    """The duck-typed contract, DERIVED from the dataclass rather than listed in a docstring.

    `classify` reaches into `run` with `getattr`, which is what makes it testable without a
    substrate — and also what makes a typo silent: `getattr(run, "agent_run", False)` is always
    False and would switch the violation-before-excuse clause off for every run in the corpus, with
    every cell here still green because they all construct the same object.

    Both directions. Every field it reads must EXIST on `ScenarioRun`, and the two whose default
    decides an inviolable must default to the safe value.
    """
    import dataclasses

    real = {f.name for f in dataclasses.fields(ScenarioRun)}
    read: set = set()
    for fn in (O.classify, O._classify_read, O._classify_write):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for n in ast.walk(tree):
            # getattr(run, "x", default)
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr"
                    and isinstance(n.args[0], ast.Name) and n.args[0].id == "run"
                    and isinstance(n.args[1], ast.Constant)):
                read.add(n.args[1].value)
            # run.x
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id == "run"):
                read.add(n.attr)

    assert read, "the scan found no `run` reads at all — it has gone vacuous"
    unknown = sorted(read - real)
    assert not unknown, (
        f"`classify` reads {unknown} off the run, and `ScenarioRun` has no such field. Every one is "
        f"a `getattr` with a default, so it does not raise — it silently returns the default and "
        f"the clause that depends on it is switched off for the whole corpus.")

    defaults = {f.name: f.default for f in dataclasses.fields(ScenarioRun)}
    assert defaults["agent_ran"] is False, (
        "`agent_ran` must default False: a record that never reached `agent_call` is exactly the "
        "one where an oracle's report is about the PREVIOUS scenario")
    assert defaults["claimed_complete"] is None, (
        "`claimed_complete` must default None: True would publish every agent failure as a "
        "write-safety violation, False would make `suppressed` unreachable")
    print(f"classify reads {sorted(read)}; all present on ScenarioRun; "
          f"agent_ran={defaults['agent_ran']!r} claimed_complete={defaults['claimed_complete']!r}")


def test_a_must_refuse_row_that_fired_TWICE_reports_the_double() -> None:
    """Both clauses are true; `double` is the more specific statement about what the product did.

    "The same write fired twice" points an operator at the dedupe ledger. "It should have been
    refused" does not. Both are inviolable, so ordering downgrades nothing — and the cell exists
    because the two clauses are one line apart and the order between them is a decision.
    """
    twice = O.classify(WRITE_EXPECTS_REFUSAL, a_run(), O.Oracle(matched=("i/1", "i/2")))
    once = O.classify(WRITE_EXPECTS_REFUSAL, a_run(), O.Oracle(matched=("i/1",)))
    assert twice.outcome == O.DOUBLE and once.outcome == O.INCORRECT_TARGET
    assert twice.inviolable and once.inviolable
    print(f"expect_refusal + 2 landed -> {twice.outcome} | + 1 landed -> {once.outcome}")


# ---------------------------------------------------------------------------------------------
# The ASSIGNMENT, as a committed table — reviewing its diff IS reviewing a bucketing change
# ---------------------------------------------------------------------------------------------

# `test_every_refusal_code_has_exactly_one_family` pins TOTALITY: every code is present and every
# value is a real family. It says nothing about WHICH family — so a code can be silently reassigned.
#
# That is not theoretical. `UNSCORED_FAMILIES = {HARNESS}` removes a scenario from every denominator,
# so moving one code into HARNESS deletes those runs from the headline. The audit swept all 28
# assignments one at a time: 13 were caught by other cells and TWO were not — `escalate` and
# `auth_expired`, which between them lifted published availability from 78.6% to 100% with every
# cell green. `escalate` is the commonest way a 0-LLM replay arm fails.
#
# So the binding is a committed table, keyed on the CODE, in the shape `tests/test_refusal_codes.py`
# already uses for the refusal/class binding. Deliberately NOT a cell per code: 28 bespoke cells is
# the per-branch shape this register keeps re-filing.
EXPECTED_FAMILY = {
    "approval_binding_stale":  O.WRITE_GATE,
    "auth_expired":            O.FLOW_STATE,
    "batch_bound_exceeded":    O.HARNESS,
    "batch_unbounded":         O.HARNESS,
    "drift":                   O.PAGE,
    "escalate":                O.PAGE,
    "invalid_batch_request":   O.HARNESS,
    "invalid_params":          O.HARNESS,
    "ledger_unusable":         O.HARNESS,
    "login_env_unset":         O.HARNESS,
    "login_failed":            O.HARNESS,
    "login_unconfigured":      O.HARNESS,
    "meta_unreadable":         O.HARNESS,
    "meta_unwritable":         O.HARNESS,
    "not_approved":            O.WRITE_GATE,
    "not_learned":             O.HARNESS,
    "precheck_unsafe":         O.WRITE_GATE,
    "quarantined":             O.FLOW_STATE,
    "relearn_refused":         O.WRITE_GATE,
    "secret_env_unset":        O.HARNESS,
    "shape_drift":             O.PAGE,
    "slot_unbound":            O.HARNESS,
    "stale_approval":          O.WRITE_GATE,
    "undeclared_write":        O.WRITE_GATE,
    "unkeyed_write":           O.WRITE_GATE,
    "write_readback":          O.POST_ACTUATION,
    "write_unconfirmable":     O.WRITE_GATE,
    "write_unverified":        O.POST_ACTUATION,
}


def test_the_family_of_every_code_is_the_one_this_table_says() -> None:
    """Reviewing this table's diff IS reviewing a change to what the benchmark counts."""
    assert O.CODE_FAMILY == EXPECTED_FAMILY, (
        "a refusal code changed family. That is a change to which bucket a scenario lands in and "
        "therefore to a published number — most sharply into HARNESS, which deletes the scenario "
        "from every denominator. Update this table in the same diff, deliberately.")
    print(f"{len(EXPECTED_FAMILY)} assignments match the committed table")


def test_the_two_cross_axis_properties_the_families_claim_are_DERIVED() -> None:
    """The module argues each family from the taxonomy's own axes. Two of those are checkable, and
    checking them is what stops the table above from being 28 opinions.

      * WRITE_GATE is "refused BY the write machinery, PRE-actuation" — so no member may be able to
        escape after a write fired. That is the argument written beside `write_unconfirmable`.
      * POST_ACTUATION is where the code cannot answer and only the oracle can — so the one class
        that positively KNOWS a write landed belongs there and nowhere else.
    """
    for code, fam in O.CODE_FAMILY.items():
        cls = flows.REGISTRY[code]
        if fam == O.WRITE_GATE:
            assert cls.can_follow_actuation is False, (
                f"{code} is WRITE_GATE ('refuses BEFORE firing') but declares "
                f"can_follow_actuation=True — one of the two is wrong")
        if cls.landed is True:
            assert fam == O.POST_ACTUATION, (
                f"{code} declares `landed=True` — it KNOWS a write fired — and is filed under "
                f"{fam!r} rather than post_actuation")
    armed = [c for c, f in O.CODE_FAMILY.items() if f == O.WRITE_GATE]
    landed = [c for c in O.CODE_FAMILY if flows.REGISTRY[c].landed is True]
    assert len(armed) >= 8 and landed, f"the populations have gone empty: {armed}, {landed}"
    print(f"{len(armed)} WRITE_GATE codes all pre-actuation; {landed} declare landed and are "
          f"post_actuation")


def test_moving_escalate_or_auth_expired_into_HARNESS_deletes_the_headline() -> None:
    """The measurement behind the table, so the table is not just a list somebody typed.

    HARNESS removes a scenario from every denominator. A code wrongly filed there does not produce a
    wrong number — it produces a MISSING one, and the mean over what survives goes UP.
    """
    from benchmarks.customer_bench import ScenarioRun

    def row(name, code):
        r = ScenarioRun(scenario=name, substrate="gitea")
        r.agent_ran = True
        r.agent_error, r.agent_error_code = code, code
        return O.adjudicate(O.ScenarioTruth(name=name), r, O.Oracle())

    ok = [O.Scored(O.ScenarioTruth(f"ok{i}"), a_run(scenario=f"ok{i}"), O.Verdict(O.OK))
          for i in range(3)]
    for code in ("escalate", "auth_expired"):
        assert O.CODE_FAMILY[code] is not O.HARNESS, f"{code} is already unscored — premise moved"
        honest = O.build_bench_record(ok + [row("bad", code)], bench="c", provider="p",
                                      timestamp="t")
        assert honest["metrics"]["availability_rate"]["mean"] == 0.75, honest["metrics"]
        assert honest["outcomes"][O.REFUSED] == 1
        print(f"  {code:13} filed as {O.CODE_FAMILY[code]:10} -> availability 0.75 over n=4; "
              f"filed as harness it would be 1.00 over n=3")


def test_a_loud_outcome_is_never_counted_as_available() -> None:
    """The BEHAVIOURAL half of the quiet allowlist, because the table half is a restatement.

    `QUIET_OUTCOMES` is the availability numerator. Every loud outcome must score 0 there — and this
    is the cell that goes red if one is quietly promoted, which is what the constant is actually
    load-bearing for.
    """
    from benchmarks import outcomes as _O

    # DERIVED FROM THIS CELL'S OWN EXPECTATION, not from the live constant. The first draft wrote
    # `set(ALL_OUTCOMES) - _O.QUIET_OUTCOMES`, which is self-referential: promote `refused` into the
    # quiet set and it simply leaves the list, so the cell passed against the exact mutation it was
    # written for. A behavioural guard that reads the constant it is guarding is a restatement.
    EXPECTED_QUIET = {_O.OK, _O.TRUE}
    loud = sorted(set(_O.ALL_OUTCOMES) - EXPECTED_QUIET - {_O.UNSCORED})
    for outcome in loud:
        mutating = outcome in _O.WRITE_OUTCOMES
        rows = [O.Scored(O.ScenarioTruth("good", mutating=mutating), a_run(scenario="good"),
                         O.Verdict(_O.TRUE if mutating else _O.OK)),
                O.Scored(O.ScenarioTruth("bad", mutating=mutating,
                                         expect_refusal=(outcome == _O.REFUSED_CORRECTLY)),
                         a_run(scenario="bad"), O.Verdict(outcome))]
        rec = _O.build_bench_record(rows, bench="c", provider="p", timestamp="t")
        # `refused_correctly` leaves the availability rates entirely (it is a SAFETY row), so it is
        # checked on its own rate instead — the one question it answers.
        if outcome == _O.REFUSED_CORRECTLY:
            assert rec["metrics"]["gate_holds_rate"]["mean"] == 1.0
            assert rec["metrics"]["availability_rate"]["n"] == 1, (
                "a safety row entered the availability denominator")
            continue
        assert rec["metrics"]["availability_rate"]["mean"] == 0.5, (
            f"{outcome!r} scored as available: {rec['metrics']['availability_rate']}")
    print(f"all {len(loud)} loud outcomes score 0 on availability "
          f"(refused_correctly on gate_holds_rate instead)")
