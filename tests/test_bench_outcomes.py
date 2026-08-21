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

    `unscored` NOT being quiet is the clause worth an assertion of its own: an unscored scenario is
    removed from every denominator, and a nightly gate that treated it as a pass would let a
    substrate that never came up report as a clean run.
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
