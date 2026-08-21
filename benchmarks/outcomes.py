"""The customer benchmark's OUTCOME VOCABULARY (reshape-plan 2.2 / benchmark-plan B3).

B2 records FACTS and states no verdict. This is the verdict, and it is the only place one is minted.

    from benchmarks.outcomes import classify, Oracle, build_bench_record

WHAT B3 OWNS. Two closed vocabularies -- `{ok, wrong_data, refused, over_gated}` for reads and
`{true, incorrect_target, double, suppressed, refused_correctly, refused_wrongly}` for writes -- plus
the record shape they are aggregated into and the gate that reads it. B4 supplies the per-scenario
oracles that ANSWER the questions asked here; B3 fixes the questions, so an oracle written later
cannot quietly invent a seventh write outcome.

FIVE RULES, EACH OF WHICH THIS REPO PAID FOR ONCE ALREADY.

  1. AN UNKNOWN IS EXPRESSIBLE, AND IT IS NOT A VERDICT. `unscored` is an extra state on both
     vocabularies and it says nothing about the product. B2 already draws this line between
     `harness_error` ("we could not build the world") and `agent_error` ("the agent raised"); B3
     extends it to the adjudication -- an oracle that could not run, and a refusal that was the
     BENCH's own misconfiguration, are not results. Scoring them would let the harness's setup bugs
     move the product's headline number.

  2. QUIET IS AN ALLOWLIST (R3.9/CLI-1). `QUIET_OUTCOMES` enumerates the outcomes a nightly gate may
     pass in silence -- `ok` and `true`, and nothing else. An outcome added tomorrow is LOUD because
     nobody put it here. The opposite shape (list the failures) is what let a third bucket satisfy
     neither of two cron channels and leave a flow dead with cron reporting green.

  3. THE THREE INVIOLABLES ARE NOT A RATE. `wrong_data`, `incorrect_target`, `double` and
     `suppressed` are the benchmark's spelling of "never silently return or act WRONG" and "never
     double-submit, never silently suppress". One occurrence fails the run -- it is not traded off
     against an availability percentage. It is not zero-tolerance-with-no-escape either, because a
     loud channel nobody can acknowledge gets `|| true`'d and takes everything dark with it: the
     escape is a PUBLISHED, committed allowlist keyed on (scenario, outcome) with a register id,
     which is how `drift_bench` already carries its own `silent_wrong` residue.

  4. NOTHING IS GUESSED FROM A MESSAGE. Every refusal bucket derives from `FlowReplayError.code` --
     1.4's vocabulary -- through `CODE_FAMILY`, which is a TOTAL partition of `flows.REGISTRY`.
     A code added tomorrow is not silently mis-bucketed: it is absent from the partition and
     `tests/test_bench_outcomes.py` fails naming it. That is the reason 1.4 had to land before B3,
     and it is why `over_gated` cannot be computed from a substring the way `MUTATING_KEYWORDS` does
     (measured 28% false positives -- the very defect this benchmark exists to put a number on).

  5. THE RECORD IS A CROSS-CHECK, NEVER AN INPUT. `RunRecord` is assembled by the code under test.
     `docs/reshape-plan.md` forbids sourcing cost or outcome from it, and `classify()` does not read
     it at all -- `cross_check()` is a SEPARATE pass that compares the verdict against what the
     product claimed and emits a `record_disagrees` bucket. A disagreement is a finding about the
     product, and it can never change the score.

WHAT B3 DELIBERATELY DOES NOT DO. It does not decide whether a write landed by asking the product
(`landed` is evidence-bounded, never truth -- see CLAUDE.md); the ORACLE decides, server-side, from
record identities. And it does not price anything: cost comes from `BoundaryLedger`, and a scenario
whose accounting was never observed makes the aggregator REFUSE rather than publish a zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ultracua import flows

# --------------------------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------------------------

# Reads. `over_gated` is carved OUT of `refused` rather than nested inside it: it is the benchmark's
# headline finding (a read refused by the write machinery), and a bucket you have to subtract to see
# is a bucket nobody reads.
OK = "ok"
WRONG_DATA = "wrong_data"
REFUSED = "refused"
OVER_GATED = "over_gated"
READ_OUTCOMES = (OK, WRONG_DATA, REFUSED, OVER_GATED)

# Writes.
TRUE = "true"
INCORRECT_TARGET = "incorrect_target"
DOUBLE = "double"
SUPPRESSED = "suppressed"
REFUSED_CORRECTLY = "refused_correctly"
REFUSED_WRONGLY = "refused_wrongly"
WRITE_OUTCOMES = (TRUE, INCORRECT_TARGET, DOUBLE, SUPPRESSED, REFUSED_CORRECTLY, REFUSED_WRONGLY)

# The extra state on BOTH, and the one that is not a verdict. Named once so a reader cannot mistake
# it for a bad result: an unscored scenario is removed from every denominator, in both directions.
UNSCORED = "unscored"

ALL_OUTCOMES = READ_OUTCOMES + WRITE_OUTCOMES + (UNSCORED,)

# RULE 2. The closed set a nightly gate may pass in silence. Everything else is reported; a member
# added here is a deliberate decision to stop looking at something.
QUIET_OUTCOMES = frozenset({OK, TRUE})

# RULE 3. The benchmark's spelling of the three inviolables. Membership is not a severity label -- it
# changes the ARITHMETIC: these are counted absolutely and never divided by anything.
INVIOLABLE_OUTCOMES = frozenset({WRONG_DATA, INCORRECT_TARGET, DOUBLE, SUPPRESSED})


# --------------------------------------------------------------------------------------------
# RULE 4 -- the code partition
# --------------------------------------------------------------------------------------------

WRITE_GATE = "write_gate"          # refused BY the write-safety machinery, pre-actuation
PAGE = "page"                      # the product tried and could not -- drift, shape change, escalation
FLOW_STATE = "flow_state"          # the flow's own lifecycle: quarantined, session expired
HARNESS = "harness"                # the BENCH's misconfiguration. Not a product result -- unscored.
POST_ACTUATION = "post_actuation"  # raised after the write may have fired; only the oracle can say

FAMILIES = (WRITE_GATE, PAGE, FLOW_STATE, HARNESS, POST_ACTUATION)

# A refusal's FAMILY, keyed on `FlowReplayError.code`. The partition is TOTAL over `flows.REGISTRY`
# -- `test_every_refusal_code_has_exactly_one_family` derives the left-hand side from the live
# registry, so a code minted tomorrow fails the suite rather than falling into a default.
#
# There is no default on purpose. A `.get(code, SOMETHING)` here is the defect class this benchmark
# measures: a bucket that absorbs what nobody classified, and then reports a confident number over it.
CODE_FAMILY = {
    # --- WRITE_GATE. Every one of these fires because something decided this was a write. On a READ
    # scenario that is `over_gated` by construction, which is the number the benchmark exists for.
    "approval_binding_stale": WRITE_GATE,
    "not_approved": WRITE_GATE,          # fires for a DECLARED WRITE -- flows.py says so explicitly
    "precheck_unsafe": WRITE_GATE,
    "relearn_refused": WRITE_GATE,
    "stale_approval": WRITE_GATE,
    "undeclared_write": WRITE_GATE,
    "unkeyed_write": WRITE_GATE,
    "write_unconfirmable": WRITE_GATE,   # can_follow_actuation=False: it refuses BEFORE firing

    # --- PAGE. The learned path no longer matches, or the engine gave up and asked for help.
    "drift": PAGE,
    "shape_drift": PAGE,
    "escalate": PAGE,

    # --- FLOW_STATE.
    "auth_expired": FLOW_STATE,
    "quarantined": FLOW_STATE,

    # --- HARNESS. The bench asked for something impossible, or failed to build the world. Scoring
    # these would let the harness's own bugs move the product's number, in whichever direction the
    # bug happened to point.
    "batch_bound_exceeded": HARNESS,
    "batch_unbounded": HARNESS,
    "invalid_batch_request": HARNESS,
    "invalid_params": HARNESS,
    "ledger_unusable": HARNESS,
    "login_env_unset": HARNESS,
    "login_unconfigured": HARNESS,
    "meta_unreadable": HARNESS,
    "meta_unwritable": HARNESS,
    "secret_env_unset": HARNESS,
    "slot_unbound": HARNESS,
    # BOTH OF THESE ARE ARGUED, because the obvious reading puts them elsewhere and would be wrong.
    #
    # `login_failed`: credentials were rejected. That is EITHER the bench seeding the wrong password
    # OR the product's login replay being broken, and from outside the two are the same string. The
    # benchmark cannot tell them apart, so it says so. Blaming the product for the bench's seed is
    # the direction that manufactures a headline, which is the worst error a benchmark can make.
    "login_failed": HARNESS,
    # `not_learned`: a normal intermediate state in the field (it is in `QUIET_SKIPS` for that
    # reason) -- but a bench arm that reached replay without a cached recipe did not learn first, and
    # that is a sequencing bug in the harness. Scoring it as `refused` would report the bench's own
    # missing setup step as the product declining to work.
    "not_learned": HARNESS,

    # --- POST_ACTUATION. The write may already have fired, so the CODE cannot answer what happened
    # and the oracle is the only thing that can. Never mapped straight onto a refusal bucket.
    "write_readback": POST_ACTUATION,     # landed=True -- it DID fire, the readback failed
    "write_unverified": POST_ACTUATION,   # it actuated and cannot be confirmed
}

# Codes that are NOT `FlowReplayError`s. `_RecordSink` writes "raised" for a non-typed crash, and an
# empty string is what a run that never refused carries. Both are named rather than defaulted.
CRASH_CODE = "raised"
NO_CODE = ""

# Families whose refusals are the BENCH's fault and are therefore removed from the score entirely.
UNSCORED_FAMILIES = frozenset({HARNESS})


def family_of(code: str) -> str:
    """The family of a refusal code. Raises on an unclassified code -- never returns a default.

    A `.get(code, "page")` here would silently bucket next month's code as an ordinary page failure
    and report a confident rate over it. The totality test makes this unreachable for anything in
    `flows.REGISTRY`; this raise is what catches a code arriving from somewhere else.
    """
    try:
        return CODE_FAMILY[code]
    except KeyError:
        raise KeyError(
            f"refusal code {code!r} has no family, so nothing here can say what bucket it belongs "
            f"in. Add it to CODE_FAMILY with an argument -- a default would put it in a bucket the "
            f"benchmark then reports a number over."
        ) from None


# --------------------------------------------------------------------------------------------
# The inputs
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Oracle:
    """What the SERVER says happened. B4 implements one per scenario; B3 fixes the contract.

    WRITES ARE ADJUDICATED BY LINKAGE, NOT BY A COUNT -- benchmark-plan's first B4 gate. `matched`
    and `unmatched` are the IDENTITIES of records the run created or changed, split by whether they
    are the record the scenario intended. A count cannot tell "created the right issue twice" from
    "created the right issue and also closed someone else's", and those are different harms with
    different names.

    `available` is separate from an empty result, and the distinction is why this is a dataclass
    rather than two lists. "The server says nothing changed" is a finding (`suppressed`); "we could
    not ask the server" is not a finding about anything, and the run is unscored.
    """

    available: bool = True
    unavailable_reason: str = ""

    # Reads. Tri-state on purpose: None means the oracle does not answer this question, which is not
    # the same as answering False.
    data_correct: Optional[bool] = None

    # Writes.
    matched: tuple = ()      # identities matching the scenario's intent
    unmatched: tuple = ()    # identities changed that do NOT match the intent


@dataclass(frozen=True)
class ScenarioTruth:
    """The corpus author's GROUND TRUTH about one scenario -- declared, never inferred from a run.

    `mutating` says what the task IS, and is what `over_gated` is measured against: a read the
    product gated as a write is the finding. Taking it from the recipe's own `mutating` marks
    instead would make the measurement circular -- the classifier would be agreeing with itself.

    `expect_refusal` is what separates `refused_correctly` from `refused_wrongly`. It is a property
    of the SCENARIO (this flow is deliberately unapproved / deliberately unkeyed), so a corpus author
    states it when they write the scenario and a run cannot talk the bench out of it.
    """

    name: str
    mutating: bool = False
    expect_refusal: bool = False


@dataclass(frozen=True)
class GateEvidence:
    """Why the product gated -- read off the recipe and the flow's own metadata, not inferred.

    REQUIRED FOR `over_gated`, and that requirement is a direction-of-error decision. Over-reporting
    `over_gated` makes the product look bad on precisely the axis this benchmark was built to
    indict, so the bench flattering its own thesis is the failure mode to design against. Without
    the recipe in hand, "the write gate fired on a read" is an inference from a code alone -- so the
    run is unscored rather than counted for the headline.

    `present=False` means the harness could not read the recipe at all.
    """

    present: bool = False
    mutating_steps: int = 0          # how many cached steps carry `mutating=True`
    mutating_sources: tuple = ()     # provenance marks for those steps (keyword? wire? human?)
    approved: Optional[bool] = None  # FlowMeta.approved

    @classmethod
    def from_flow(cls, cached_flow, meta) -> "GateEvidence":
        """Read the evidence off a cached recipe and its `FlowMeta`. Both may be None."""
        approved = getattr(meta, "approved", None)
        if cached_flow is None:
            return cls(present=False, approved=approved)
        steps = [s for s in getattr(cached_flow, "steps", ()) if getattr(s, "mutating", False)]
        marks: list = []
        for s in steps:
            for m in (getattr(s, "mutating_sources", None) or ()):
                if m not in marks:
                    marks.append(m)
        return cls(present=True, mutating_steps=len(steps), mutating_sources=tuple(marks),
                   approved=approved)


@dataclass(frozen=True)
class Verdict:
    """One scenario's outcome, plus everything needed to argue with it."""

    outcome: str
    reason: str = ""
    code: str = ""
    family: str = ""
    evidence: dict = field(default_factory=dict)

    @property
    def scored(self) -> bool:
        return self.outcome != UNSCORED

    @property
    def quiet(self) -> bool:
        return self.outcome in QUIET_OUTCOMES

    @property
    def inviolable(self) -> bool:
        return self.outcome in INVIOLABLE_OUTCOMES

    def to_dict(self) -> dict:
        return {"outcome": self.outcome, "reason": self.reason, "code": self.code,
                "family": self.family, "evidence": dict(self.evidence)}


def _unscored(reason: str, **evidence) -> Verdict:
    return Verdict(outcome=UNSCORED, reason=reason, evidence=evidence)


# --------------------------------------------------------------------------------------------
# The classifier
# --------------------------------------------------------------------------------------------

def classify(truth: ScenarioTruth, run, oracle: Oracle,
             gate: "Optional[GateEvidence]" = None) -> Verdict:
    """Adjudicate one scenario run. The ONLY place an outcome is minted.

    `run` is duck-typed on the fields B2 records -- `harness_error`, `agent_error`,
    `agent_error_code` -- so this is testable without a substrate, an agent or a key.

    THE ORDER OF ADJUDICATION IS THE DESIGN, and one clause in it is load-bearing.

    A write-safety violation the oracle can SEE is decided FIRST, before any unscored reason. Three
    of the codes routed to `HARNESS` can be raised after an actuation (`meta_unwritable`,
    `meta_unreadable`, `not_learned` all declare `can_follow_actuation=True`), so "the bench
    misconfigured something" and "a write fired twice" are not mutually exclusive -- and if the
    unscored check ran first, a `double` the server is holding would be dropped on the floor with
    the harness's excuse written beside it. Inviolable #2 says never silently act wrong; suppressing
    a violation because of an attribution question is exactly that.

    Everything after that clause is ordinary, most-specific-first: our fault, then unanswerable,
    then the product's result.
    """
    code = getattr(run, "agent_error_code", "") or NO_CODE
    fam = family_of(code) if code and code != CRASH_CODE else ""
    ev = {"code": code, "family": fam, "scenario": truth.name,
          "mutating_task": truth.mutating, "expect_refusal": truth.expect_refusal}

    # 1. A VIOLATION THE ORACLE CAN SEE OUTRANKS EVERY EXCUSE. See the docstring.
    if oracle.available:
        if len(oracle.matched) >= 2:
            return Verdict(DOUBLE, f"the server holds {len(oracle.matched)} records matching one "
                                   f"intent: {list(oracle.matched)}", code, fam,
                           {**ev, "matched": list(oracle.matched),
                            "unmatched": list(oracle.unmatched)})
        if oracle.unmatched:
            # On a READ task this is the stronger statement, and it is exact rather than a stretch:
            # a read declares no write target, so ANY identity the run changed is by construction not
            # the one it was asked for. The read vocabulary has no word for "it wrote something", and
            # inventing one here would be a fifth read outcome smuggled past the closed set.
            why = ("a read task changed a record" if not truth.mutating
                   else "the run changed a record that is not the one it was asked for")
            return Verdict(INCORRECT_TARGET, f"{why}: {list(oracle.unmatched)}", code, fam,
                           {**ev, "matched": list(oracle.matched),
                            "unmatched": list(oracle.unmatched)})

    # 2. OUR FAULT -- most specific first. Neither of these says anything about the product.
    if getattr(run, "harness_error", ""):
        return _unscored("harness_error", detail=run.harness_error, **ev)
    if fam in UNSCORED_FAMILIES:
        return _unscored("harness_refusal", detail=getattr(run, "agent_error", ""), **ev)

    # 3. UNANSWERABLE. "The server says nothing changed" is a finding; "we could not ask" is not.
    if not oracle.available:
        return _unscored("oracle_unavailable", detail=oracle.unavailable_reason, **ev)

    decide = _classify_write if truth.mutating else _classify_read
    return decide(truth, run, oracle, gate, code, fam, ev)


def _classify_read(truth, run, oracle, gate, code, fam, ev) -> Verdict:
    """`{ok, wrong_data, refused, over_gated}`.

    `data_correct` is consulted BEFORE the refusal code because a run can both return data and carry
    a code (the engine healed past a failure). What the caller received is what the caller is harmed
    by, so the data answer wins where there is one.
    """
    if oracle.data_correct is True:
        return Verdict(OK, "the oracle matched the returned data", code, fam, ev)
    if oracle.data_correct is False:
        return Verdict(WRONG_DATA, "data was returned and it is not what the oracle holds",
                       code, fam, ev)

    # No data verdict. Either it refused, or the oracle did not answer -- and those are different.
    if not code:
        return _unscored("oracle_silent",
                         detail="the run neither refused nor produced data the oracle adjudicated",
                         **ev)
    if code == CRASH_CODE:
        return Verdict(REFUSED, "the run raised a non-typed exception", code, fam, ev)

    if fam == WRITE_GATE:
        # THE HEADLINE, and the one place this module refuses to infer. See `GateEvidence`.
        if gate is None or not gate.present:
            return _unscored("gate_unexplainable",
                             detail=f"{code!r} refused a read task, but the recipe was not readable, "
                                    f"so 'the write gate fired on a read' would be an inference from "
                                    f"a code alone",
                             **ev)
        return Verdict(OVER_GATED,
                       f"a read task was refused by the write machinery ({code})",
                       code, fam,
                       {**ev, "mutating_steps": gate.mutating_steps,
                        "mutating_sources": list(gate.mutating_sources),
                        "approved": gate.approved})

    if fam == POST_ACTUATION:
        # Reached only when the oracle saw NO changed identity (clause 1 would have taken it
        # otherwise). The engine believed it was writing on a read task and nothing landed.
        return Verdict(REFUSED, f"a read task reached the write machinery and refused ({code})",
                       code, fam, ev)

    return Verdict(REFUSED, f"the run refused ({code})", code, fam, ev)


def _classify_write(truth, run, oracle, gate, code, fam, ev) -> Verdict:
    """`{true, incorrect_target, double, suppressed, refused_correctly, refused_wrongly}`.

    `double` and `incorrect_target` were already decided in `classify`'s first clause, so what
    reaches here is one matching record or none at all.
    """
    ev = {**ev, "matched": list(oracle.matched), "unmatched": list(oracle.unmatched)}

    if len(oracle.matched) == 1:
        # Scored on what the SERVER holds, even if the run also refused. That combination -- the
        # write landed and the caller was told it did not -- is real and dangerous (an operator
        # re-runs and doubles it), but it is a disagreement between the product's report and the
        # world, which is `cross_check`'s bucket. The outcome is what happened.
        return Verdict(TRUE, "the server holds exactly the intended record", code, fam, ev)

    # Nothing landed.
    if not code:
        # SILENTLY suppressed: the run reported success and the server holds nothing. The word
        # requires the silence -- a loud refusal that left nothing behind is a refusal, below.
        return Verdict(SUPPRESSED, "the run reported success and the server holds no record",
                       code, fam, ev)

    correct = truth.expect_refusal
    return Verdict(REFUSED_CORRECTLY if correct else REFUSED_WRONGLY,
                   f"the write was refused ({code}) and the scenario "
                   f"{'expects' if correct else 'does not expect'} a refusal", code, fam, ev)


# --------------------------------------------------------------------------------------------
# RULE 5 -- the cross-check
# --------------------------------------------------------------------------------------------

def cross_check(verdict: Verdict, record) -> list:
    """Compare the verdict against what the PRODUCT claimed. Never changes the score.

    `record` is a `RunRecord` (duck-typed on `ok`, `committed`, `failure_code`). It is read HERE and
    nowhere else, which is what makes `reshape-plan.md`'s "do not let B3 source outcome from
    `RunRecord`" checkable rather than aspirational -- `test_classify_never_reads_a_run_record`
    derives that from `classify`'s own source.

    A `None` NEVER disagrees. That is 1.4b's tri-state paying off: `committed=None` means "the
    evidence available cannot say", and an unknown that argued with a measurement would turn the
    honest answer into a false alarm -- which is how a loud channel gets silenced.
    """
    if record is None or not verdict.scored:
        return []
    out: list = []
    ok = getattr(record, "ok", None)
    committed = getattr(record, "committed", None)
    failure_code = getattr(record, "failure_code", "") or ""

    if ok is True and verdict.outcome not in QUIET_OUTCOMES:
        out.append({"field": "ok", "record": True, "verdict": verdict.outcome,
                    "detail": "the product reported success for a run the bench did not score as ok"})
    if ok is False and verdict.outcome in QUIET_OUTCOMES:
        out.append({"field": "ok", "record": False, "verdict": verdict.outcome,
                    "detail": "the product reported failure for a run the bench scored as ok"})
    if committed is True and verdict.outcome == SUPPRESSED:
        out.append({"field": "committed", "record": True, "verdict": verdict.outcome,
                    "detail": "the product says a write committed and the server holds no record"})
    if committed is False and verdict.outcome in (TRUE, DOUBLE, INCORRECT_TARGET):
        out.append({"field": "committed", "record": False, "verdict": verdict.outcome,
                    "detail": "the product denies a write the server is holding"})
    if verdict.code and failure_code and failure_code != verdict.code:
        out.append({"field": "failure_code", "record": failure_code, "verdict": verdict.code,
                    "detail": "the code the bench bucketed on is not the one the record carries"})
    return out


# --------------------------------------------------------------------------------------------
# The record, and the gate that reads it
# --------------------------------------------------------------------------------------------

class BenchRecordError(RuntimeError):
    """The bench refuses to publish a number it cannot stand behind.

    Carries a `code` from a closed set, for the same reason 1.4 gave the refusal taxonomy one: a
    caller that cannot tell "some spend was unpriced" from "no scenario was scored" cannot act on
    either, and a single overloaded string is what twenty-four refusals shared before 1.4a.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


BENCH_REFUSALS = frozenset({"unpriced_spend", "unobserved_spend", "accounting_failed",
                            "nothing_scored", "unknown_outcome"})


@dataclass(frozen=True)
class Scored:
    """One adjudicated scenario: the ground truth, the facts, the verdict, and the cross-check."""

    truth: ScenarioTruth
    run: object
    verdict: Verdict
    disagreements: list = field(default_factory=list)

    @property
    def substrate(self) -> str:
        return getattr(self.run, "substrate", "") or ""


def adjudicate(truth: ScenarioTruth, run, oracle: Oracle,
               gate: "Optional[GateEvidence]" = None, record=None) -> Scored:
    """`classify` + `cross_check` in the order that keeps them separate.

    The verdict is minted from harness facts and the oracle ALONE; only then is the product's own
    record consulted, and only to disagree with. Calling them in one place is what stops a future
    caller from passing the record into `classify` for convenience.
    """
    verdict = classify(truth, run, oracle, gate)
    return Scored(truth=truth, run=run, verdict=verdict,
                  disagreements=cross_check(verdict, record))


def _usage_of(run):
    """Rebuild a `UsageTotals` from what B2 recorded, so cost is priced by `obs.py` and not here.

    A second pricing table in the benchmark is a second thing to keep in step with the first, and
    the one that would drift is the one nobody runs against a real bill.
    """
    from ultracua.obs import UsageTotals

    t = UsageTotals(
        input_tokens=int(getattr(run, "input_tokens", 0) or 0),
        output_tokens=int(getattr(run, "output_tokens", 0) or 0),
        calls=int(getattr(run, "llm_calls", 0) or 0),
        accounting_failed=bool(getattr(run, "accounting_failed", False)),
    )
    t.per_model = {m: tuple(v) for m, v in (getattr(run, "per_model", None) or {}).items()}
    return t


def _cost_of(run) -> float:
    """This scenario's spend in dollars, or a REFUSAL. Never a zero standing in for an unknown.

    Three separable unknowns, named separately because they have different remedies: some spend
    could not be priced (add the model to `obs._PRICES`), some router was never watched (the SDK
    choke-point leaks -- `vision.py`, `llm/gemini.py`), and a spender declared its own accounting
    broken. `reshape-plan.md` forbids claiming "zero recorded = zero spent" while any of these hold.
    """
    name = getattr(run, "scenario", "?")
    if getattr(run, "accounting_failed", False):
        raise BenchRecordError("accounting_failed",
                               f"{name}: a spender declared its own accounting broken, so this "
                               f"run's cost is unknown. Publishing it as a number would understate "
                               f"a real bill silently.")
    if getattr(run, "llm_accounting", "observed") != "observed":
        raise BenchRecordError("unobserved_spend",
                               f"{name}: the boundary ledger could not see every router this run "
                               f"could have spent through ({list(getattr(run, 'llm_unobserved', []))}). "
                               f"An unobserved zero is a confident wrong number, not a saving.")
    cost = _usage_of(run).cost_usd()
    if cost is None:
        raise BenchRecordError("unpriced_spend",
                               f"{name}: spend was recorded against a model with no price entry "
                               f"({sorted(getattr(run, 'per_model', None) or {})}), so the bill is "
                               f"unknown. Add the model to `ultracua.obs._PRICES`.")
    return cost


# The metrics this record publishes as RATES, with the direction stated. Only higher-is-better rates
# may be gated: `compare_records` regresses on a DROP, so gating a lower-is-better rate (an
# `over_gated_rate`, say) would pass a run that got worse and fail one that improved. The direction
# is declared here once and `tests/test_bench_record.py` derives the gated set from it rather than
# from a second hand-written tuple.
RATE_METRICS = {
    "availability_rate": True,     # (ok + true) / scored -- the headline
    "read_availability_rate": True,
    "write_availability_rate": True,
}
GATED_RATES = tuple(sorted(n for n, higher_better in RATE_METRICS.items() if higher_better))


def _rate_values(scored: "list[Scored]", predicate) -> list:
    """Per-scenario 0/1 over the SCORED subset matching `predicate`.

    A scenario each, rather than a run each, so `pass_k` reads as "k random corpus scenarios all
    pass" -- which is the question an operator asks of a fourteen-scenario corpus.
    """
    return [1.0 if s.verdict.quiet else 0.0
            for s in scored if s.verdict.scored and predicate(s)]


def build_bench_record(scored: "list[Scored]", *, bench: str, provider: str, timestamp: str) -> dict:
    """The customer benchmark's record, in `variance.py`'s shape so its comparison logic applies.

    REFUSES rather than publishes in two cases, and both are the same rule wearing different hats:

      * any scenario whose spend cannot be priced or was never observed (`_cost_of`);
      * a corpus in which NOTHING was scored. `variance.aggregate([])` returns `mean: 0.0, n: 0`,
        so an all-unscored run would publish a headline of "0% availability" that no scenario
        supports. That is the fabricated zero this whole module is arranged against, and it is
        reachable the first time a substrate fails to come up.
    """
    if not scored:
        raise BenchRecordError("nothing_scored", "no scenarios were adjudicated at all")

    counts = {name: 0 for name in ALL_OUTCOMES}      # EXPLICIT zeros: the corpus is the denominator,
    for s in scored:                                 # so "never happened" is a measurement here.
        if s.verdict.outcome not in counts:
            raise BenchRecordError("unknown_outcome",
                                   f"{s.truth.name}: verdict {s.verdict.outcome!r} is outside the "
                                   f"closed vocabulary {ALL_OUTCOMES}")
        counts[s.verdict.outcome] += 1

    live = [s for s in scored if s.verdict.scored]
    if not live:
        raise BenchRecordError(
            "nothing_scored",
            f"all {len(scored)} scenarios were unscored "
            f"({sorted({s.verdict.reason for s in scored})}), so every rate below would be a mean "
            f"over an empty list -- which `variance.aggregate` renders as 0.0 and a reader reads as "
            f"a total failure of the product.")

    per_rep = {
        "availability_rate": _rate_values(scored, lambda s: True),
        "read_availability_rate": _rate_values(scored, lambda s: not s.truth.mutating),
        "write_availability_rate": _rate_values(scored, lambda s: s.truth.mutating),
    }
    # A rate with no scenarios behind it is omitted, never emitted as 0.0. An all-read corpus has no
    # write availability, and saying "0%" would be inventing the worst possible number for it.
    per_rep = {k: v for k, v in per_rep.items() if v}

    total_cost = sum(_cost_of(s.run) for s in scored)

    from benchmarks import variance

    rec = variance.build_record(
        bench=bench, provider=provider, reps=len(live), timestamp=timestamp,
        per_rep=per_rep, cost_usd=total_cost, success_key="availability_rate")

    rec["vocabulary_version"] = "b3.1"
    rec["outcomes"] = counts
    rec["gated_metrics"] = [n for n in GATED_RATES if n in per_rep]
    rec["families"] = _family_counts(scored)
    rec["substrates"] = _substrate_view(scored)
    # RULE 3: absolute, never a rate. Listed in full rather than counted, because the gate needs the
    # (scenario, outcome) pair to check an acknowledgement and a count cannot be acknowledged.
    rec["inviolable"] = [{"scenario": s.truth.name, "substrate": s.substrate,
                          "outcome": s.verdict.outcome, "reason": s.verdict.reason}
                         for s in scored if s.verdict.inviolable]
    rec["record_disagrees"] = [{"scenario": s.truth.name, **d}
                               for s in scored for d in s.disagreements]
    rec["unscored"] = [{"scenario": s.truth.name, "substrate": s.substrate,
                        "reason": s.verdict.reason,
                        "detail": s.verdict.evidence.get("detail", "")}
                       for s in scored if not s.verdict.scored]
    return rec


def _family_counts(scored: "list[Scored]") -> dict:
    out = {f: 0 for f in FAMILIES}
    for s in scored:
        if s.verdict.family:
            out[s.verdict.family] += 1
    return out


def _substrate_view(scored: "list[Scored]") -> dict:
    """The Odoo/Gitea contrast, which is the headline's other half.

    Per substrate rather than pooled, because a pooled rate hides the exact comparison the corpus
    was paired to expose -- and `n` rides beside every rate so a two-scenario substrate cannot be
    read as if it were fourteen.
    """
    out: dict = {}
    for s in scored:
        row = out.setdefault(s.substrate, {"scored": 0, "unscored": 0, "quiet": 0,
                                           "outcomes": {}})
        row["outcomes"][s.verdict.outcome] = row["outcomes"].get(s.verdict.outcome, 0) + 1
        if not s.verdict.scored:
            row["unscored"] += 1
            continue
        row["scored"] += 1
        row["quiet"] += 1 if s.verdict.quiet else 0
    for row in out.values():
        row["availability_rate"] = (row["quiet"] / row["scored"]) if row["scored"] else None
    return out


def gate_bench_record(record: dict, *, baseline: "Optional[dict]" = None,
                      acknowledged: "tuple" = ()) -> dict:
    """The verdict on a whole run: `{ok, findings}`. Findings are ordered worst-first.

    THREE CHANNELS, AND THE FIRST ONE CANNOT BE OUT-VOTED.

      1. INVIOLABLE. Every `(scenario, outcome)` pair in `record["inviolable"]` fails unless it
         appears in `acknowledged` -- a published, committed allowlist, the shape `drift_bench`
         uses for its own `silent_wrong` residue. Acknowledgement exists because an alert nobody
         can discharge gets switched off wholesale, taking the rest of the channel with it
         (R3.9/CLI-1's second lesson). An acknowledged pair is still REPORTED, just not fatal.

      2. THE 0-LLM CLAIM. A baseline of `cost_usd == 0.0` is the replay arm's whole thesis, and
         `variance.compare_records` cannot gate it: its cost clause is `bc > 0 and ...`, so a zero
         baseline never regresses however much the current run spends. Measured against the arm this
         benchmark exists to publish, that is the gate being disarmed exactly where it matters, so
         B3 adds an absolute clause rather than relying on the relative one.

      3. THE RATES, through `variance.compare_records`, which supplies the noise-awareness (a drop
         inside the baseline's own error bars is not a regression). Only the record's own
         `gated_metrics` are compared -- all higher-is-better by construction.
    """
    findings: list = []
    ack = {tuple(a) for a in acknowledged}

    for row in record.get("inviolable", []):
        pair = (row["scenario"], row["outcome"])
        findings.append({"channel": "inviolable", "regressed": pair not in ack,
                         "acknowledged": pair in ack, **row})

    if baseline is not None:
        from benchmarks import variance

        bc = baseline.get("cost_usd", None)
        cc = float(record.get("cost_usd", 0.0))
        if bc is not None and float(bc) == 0.0 and cc > 0.0:
            findings.append({
                "channel": "cost", "metric": "cost_usd", "regressed": True,
                "baseline": 0.0, "current": cc,
                "detail": "the baseline spent nothing and this run spent something -- a 0-LLM arm "
                          "that starts paying is the claim breaking, and the relative cost gate "
                          "cannot see it because it is guarded on a positive baseline."})

        cmp = variance.compare_records(baseline, record,
                                       gated_rates=tuple(record.get("gated_metrics", ())))
        for f in cmp["findings"]:
            findings.append({"channel": "rate", **f})

    findings.sort(key=lambda f: (f["channel"] != "inviolable", not f.get("regressed")))
    return {"ok": not any(f.get("regressed") for f in findings), "findings": findings}
