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

# THE OTHER VOCABULARIES ON THE SAME FIELD, AND WHY THEY ARE NOT CLASSIFIED HERE.
#
# `flows.RESERVED_CODES` names twelve slugs that `ToolOutcome.code` and `SkippedFlow.code` mint on
# the MCP surface — `write_denied`, `already_done`, `unknown_tool` and the rest. They share a FIELD
# with this taxonomy, so a benchmark arm that drove the MCP server instead of `replay()` would put
# them into `agent_error_code`, and `family_of` would raise KeyError through the whole adjudication.
#
# They are NOT classified, and that is a decision rather than an omission. Every family assignment
# above carries an argument grounded in a raise site; inventing twelve more for a surface no bench
# path currently drives would be exactly the guess `CODE_FAMILY` exists to refuse — and a guessed
# family does not crash, it silently publishes a number.
#
# What makes the omission SAFE is not this comment. `customer_bench.run_scenario` sets
# `agent_error_code` from `flows.outcome_of` and from nothing else, and `outcome_of` returns either
# a `flows.REGISTRY` code or `raised`. Both halves are derived by
# `test_the_reserved_vocabulary_is_unreachable_from_the_bench` — so the day B4 wires the MCP surface,
# that cell fails and somebody classifies these twelve knowing what they mean.
#
# `replay_error` is here for a different reason and would never be classified: it is the abstract
# base's poison sentinel, and `FlowReplayError("x")` raises TypeError since 1.4a.
NOT_OBSERVABLE_CODES = frozenset(flows.RESERVED_CODES) - {CRASH_CODE}

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
    declares_write: Optional[bool] = None   # `spec.mutate is not None` -- the flow SAYS it writes

    @property
    def marked_as_a_write(self) -> bool:
        """Did anything mark this flow as a write? THE discriminator for `over_gated`.

        `present` is a READABILITY fact and was the wrong predicate to hang the headline on: a
        readable recipe with `mutating_steps=0` satisfied it, and the verdict then printed
        `mutating_steps: 0` as its own supporting evidence.

        The distinction is not academic. `NotApprovedError` fires on
        `(require_approved or declares_write) and not meta.approved` (`flows.py:3414`) -- so a
        caller that passes `require_approved=True` for a plain READ gets it, and nothing about the
        write machinery was involved. Four of the eight WRITE_GATE codes are approval-lifecycle
        gates of that kind. Deriving `over_gated` from the code alone lets a bench arm that simply
        forgot to `approve()` publish its own omission as the product's over-gating -- the bench
        manufacturing its own headline, which is the worst error available to it.

        `docs/realistic-benchmark-plan.md` specifies exactly this: *"derived from the recipe's
        `mutating` flags + `FlowMeta` + `FlowReplayError.code`, never guessed"*. The code is one of
        three inputs, not the whole derivation.
        """
        return bool(self.mutating_steps) or self.declares_write is True

    @classmethod
    def from_flow(cls, cached_flow, meta, spec=None) -> "GateEvidence":
        """Read the evidence off a cached recipe, its `FlowMeta` and the spec. All may be None."""
        approved = getattr(meta, "approved", None)
        declares = None if spec is None else (getattr(spec, "mutate", None) is not None)
        if cached_flow is None:
            return cls(present=False, approved=approved, declares_write=declares)
        steps = [s for s in getattr(cached_flow, "steps", ()) if getattr(s, "mutating", False)]
        marks: list = []
        for s in steps:
            for m in (getattr(s, "mutating_sources", None) or ()):
                if m not in marks:
                    marks.append(m)
        return cls(present=True, mutating_steps=len(steps), mutating_sources=tuple(marks),
                   approved=approved, declares_write=declares)


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


def _verdict(outcome: str, reason: str, ev: dict, **extra) -> Verdict:
    """The ONLY constructor the classifier uses, so a verdict's fields cannot disagree with its own
    evidence.

    Both were true before this existed, and only one of them was published. `ev` always carries the
    refusal `code` and `family`; the unscored path built its `Verdict` without them, so a scenario
    refused for `slot_unbound` reported `code=""` while its evidence said `slot_unbound`, and
    `record["families"]["harness"]` printed **0** over a run whose only refusal WAS a harness one.
    One fact in two places with nothing forcing them to agree -- R3.7's shape in a report.

    Taking both from `ev` here means no call site can pass a different one.
    """
    return Verdict(outcome=outcome, reason=reason, code=ev.get("code", ""),
                   family=ev.get("family", ""), evidence={**ev, **extra})


def _unscored(reason: str, ev: dict, detail: str = "") -> Verdict:
    return _verdict(UNSCORED, reason, ev, detail=detail)


# --------------------------------------------------------------------------------------------
# The classifier
# --------------------------------------------------------------------------------------------

def classify(truth: ScenarioTruth, run, oracle: Oracle,
             gate: "Optional[GateEvidence]" = None) -> Verdict:
    """Adjudicate one scenario run. The ONLY place an outcome is minted.

    `run` is duck-typed on the fields B2 records -- `harness_error`, `agent_error`,
    `agent_error_code`, `agent_ran` and `claimed_complete` -- so this is testable without a
    substrate, an agent or a key. The last two are read through `getattr` with a SAFE default
    (`False` / `None`), so a record that predates them is unscored rather than scored on a guess;
    `test_classify_reads_only_fields_the_real_record_carries` derives the list from the dataclass.

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
    #
    # `agent_ran` GUARDS IT, and that guard is the clause's own scope made explicit. The argument
    # below is about a run that ACTED and then hit an excuse. `run_scenario` has a second door into
    # `harness_error` where the agent never ran at all -- `reset()` / `await_ready()` / the scenario
    # readiness hook failing returns before `agent_call`. B2's rule 3 is "it will not run a scenario
    # against a substrate it did not reset", so on that path the substrate is GUARANTEED to be
    # carrying the previous scenario's records; an oracle asked about that world reports them as
    # `unmatched`, and without this guard the previous row's write is minted as this row's
    # `incorrect_target` -- an inviolable violation, in the one channel that cannot be out-voted,
    # manufactured entirely by a failed container restart.
    #
    # It is a recorded FACT, not `wall_s == 0.0`. A timer is not a boundary (R4.26).
    if oracle.available and getattr(run, "agent_ran", False):
        # `expect_refusal` means the corpus author declares the INTENDED matched set is EMPTY -- this
        # row exists to prove a gate holds. So any record it landed is by construction not one it was
        # asked for, and reading it HERE rather than at the tail is what makes the declaration
        # binding on the path where the write LANDED.
        #
        # Without this, `len(matched) == 1` returned `true`: QUIET, counted as availability, and a
        # write-gate REGRESSION published as an improvement (measured: 0.667 -> 1.000, gate green,
        # `inviolable: []`). The gradient pointed at the violation.
        # DOUBLE FIRST. On an `expect_refusal` row that fired twice BOTH clauses are true, and
        # `double` is the more specific statement about what the product did -- "the same write
        # fired twice" tells an operator to look at the dedupe ledger; "it should have been
        # refused" does not. Both are inviolable, so nothing is downgraded by the choice.
        if len(oracle.matched) >= 2:
            return _verdict(DOUBLE, f"the server holds {len(oracle.matched)} records matching "
                            f"one intent: {list(oracle.matched)}", ev,
                            matched=list(oracle.matched), unmatched=list(oracle.unmatched))
        if truth.expect_refusal and oracle.matched:
            return _verdict(INCORRECT_TARGET,
                            f"the scenario declares this write must be refused and "
                            f"{len(oracle.matched)} record(s) landed: {list(oracle.matched)}", ev,
                            matched=list(oracle.matched), unmatched=list(oracle.unmatched))
        if oracle.unmatched:
            # On a READ task this is the stronger statement, and it is exact rather than a stretch:
            # a read declares no write target, so ANY identity the run changed is by construction not
            # the one it was asked for. The read vocabulary has no word for "it wrote something", and
            # inventing one here would be a fifth read outcome smuggled past the closed set.
            why = ("a read task changed a record" if not truth.mutating
                   else "the run changed a record that is not the one it was asked for")
            return _verdict(INCORRECT_TARGET, f"{why}: {list(oracle.unmatched)}", ev,
                            matched=list(oracle.matched), unmatched=list(oracle.unmatched))

    # 2. OUR FAULT -- most specific first. Neither of these says anything about the product.
    if getattr(run, "harness_error", ""):
        return _unscored("harness_error", ev, detail=run.harness_error)
    if fam in UNSCORED_FAMILIES:
        return _unscored("harness_refusal", ev, detail=getattr(run, "agent_error", ""))

    # 3. UNANSWERABLE. "The server says nothing changed" is a finding; "we could not ask" is not.
    if not oracle.available:
        return _unscored("oracle_unavailable", ev, detail=oracle.unavailable_reason)

    decide = _classify_write if truth.mutating else _classify_read
    return decide(truth, run, oracle, gate, code, fam, ev)


def _classify_read(truth, run, oracle, gate, code, fam, ev) -> Verdict:
    """`{ok, wrong_data, refused, over_gated}`.

    `data_correct` is consulted BEFORE the refusal code because a run can both return data and carry
    a code (the engine healed past a failure). What the caller received is what the caller is harmed
    by, so the data answer wins where there is one.
    """
    if oracle.data_correct is True:
        return _verdict(OK, "the oracle matched the returned data", ev)
    if oracle.data_correct is False:
        # `wrong_data` is inviolable #2 -- a SILENTLY wrong answer. The adverb needs a fact, and
        # `Oracle.data_correct is False` does not carry one: an oracle comparing the run's answer
        # against server truth returns False just as readily when there was no answer to compare.
        # Measured: a read refused by `drift` / `shape_drift` / `quarantined` / `raised` was minted
        # as `wrong_data` with the reason "data was returned..." for a run that returned nothing and
        # said so loudly. That is a LOUD refusal published as a silent wrong answer, in the channel
        # that fails the run absolutely -- the false alarm that gets a channel switched off.
        if getattr(run, "claimed_complete", None) is not True:
            return _unscored("answer_unattributed", ev,
                             detail="the oracle says the data is wrong, but nothing records that "
                                    "this run produced an answer at all -- set "
                                    "`ScenarioRun.claimed_complete` in the arm, so a loud refusal "
                                    "cannot be published as a silent wrong answer")
        return _verdict(WRONG_DATA,
                        "the run produced an answer and it is not what the oracle holds", ev)

    # No data verdict. Either it refused, or the oracle did not answer -- and those are different.
    if not code:
        return _unscored("oracle_silent", ev,
                         detail="the run neither refused nor produced data the oracle adjudicated")
    if code == CRASH_CODE:
        return _verdict(REFUSED, "the run raised a non-typed exception", ev)

    if fam == WRITE_GATE:
        # THE HEADLINE, and the one place this module refuses to infer. See `GateEvidence`.
        if gate is None or not gate.present:
            return _unscored("gate_unexplainable", ev,
                             detail=f"{code!r} refused a read task, but the recipe was not readable, "
                                    f"so 'the write gate fired on a read' would be an inference "
                                    f"from a code alone")
        if not gate.marked_as_a_write:
            # The recipe was READABLE and says this flow writes NOTHING. Whatever refused it was not
            # the write machinery -- four of the eight WRITE_GATE codes are approval-lifecycle gates
            # that fire on `require_approved` alone. Counting it would let a bench arm that forgot
            # to `approve()` publish its own omission as the benchmark's headline finding.
            return _verdict(REFUSED,
                            f"a lifecycle gate refused a read task ({code}); the recipe marks no "
                            f"write, so the write machinery was not involved", ev,
                            mutating_steps=gate.mutating_steps, approved=gate.approved,
                            declares_write=gate.declares_write)
        return _verdict(OVER_GATED, f"a read task was refused by the write machinery ({code})",
                        ev, mutating_steps=gate.mutating_steps,
                        mutating_sources=list(gate.mutating_sources), approved=gate.approved)

    if fam == POST_ACTUATION:
        # Reached only when the oracle saw NO changed identity (clause 1 would have taken it
        # otherwise). The engine believed it was writing on a read task and nothing landed.
        return _verdict(REFUSED, f"a read task reached the write machinery and refused ({code})",
                        ev)

    return _verdict(REFUSED, f"the run refused ({code})", ev)


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
        return _verdict(TRUE, "the server holds exactly the intended record", ev)

    # Nothing landed.
    if code == CRASH_CODE:
        # `_classify_read` has had this branch since the first draft; the write side did not, so a
        # bare crash fell through to the `expect_refusal` tail and was minted `refused_correctly`
        # -- the bench asserting the write gate fired when the product (or the harness) threw an
        # untyped exception. CLAUDE.md names the trap exactly: never let "any exception" stand in
        # for "refused". It is never `refused_correctly`, whatever the corpus declares.
        return _verdict(REFUSED_WRONGLY,
                        "the write did not happen and the run raised a non-typed exception, which "
                        "is not the write gate firing", ev)
    if not code:
        # SILENTLY suppressed: the run CLAIMED the write was done and the server holds no record.
        # The word requires the silence -- a loud refusal that left nothing behind is a refusal,
        # below -- and the silence needs a FACT.
        #
        # `not code` is not that fact. An LLM-driven arm that finishes its turn without raising and
        # without doing the task arrives here with an empty code, and B2's own comment calls that
        # the normal case ("a failing agent is the NORMAL case a benchmark records"). Minting
        # `suppressed` from it publishes "the product silently suppressed N writes" over a number
        # that is really "the agent failed N tasks" -- an un-dischargeable inviolable whose only
        # escape permanently blinds that scenario to the REAL suppression.
        if getattr(run, "claimed_complete", None) is not True:
            return _unscored("completion_unclaimed", ev,
                             detail="the server holds no record and nothing says this run claimed "
                                    "the write was done -- set `ScenarioRun.claimed_complete` in "
                                    "the arm, or an ordinary task failure is published as a "
                                    "write-safety violation")
        return _verdict(SUPPRESSED,
                        "the run claimed the write was done and the server holds no record", ev)

    correct = truth.expect_refusal
    return _verdict(REFUSED_CORRECTLY if correct else REFUSED_WRONGLY,
                    f"the write was refused ({code}) and the scenario "
                    f"{'expects' if correct else 'does not expect'} a refusal", ev)


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
# `availability_rate` counts a scenario's DECLARED PURPOSE being achieved, which is not the same
# question as `Verdict.quiet` ("may a nightly gate pass this in silence") -- and conflating them
# scored the product 0.0 for doing exactly the right thing. An `expect_refusal` row is a SAFETY
# PROBE, not a task a customer wants done: `refused_correctly` is that row succeeding, and putting
# it in the availability denominator at 0.0 drags the headline down every time the write gate holds.
#
# So safety rows leave the availability rates entirely and get their own, and the two questions stop
# sharing a numerator. That is the same "two questions, one field" defect this slice keeps finding.
RATE_METRICS = {
    "availability_rate": True,          # (ok + true) / scored TASK rows -- the headline
    "read_availability_rate": True,
    "write_availability_rate": True,
    "gate_holds_rate": True,            # refused_correctly / scored SAFETY rows
}
GATED_RATES = tuple(sorted(n for n, higher_better in RATE_METRICS.items() if higher_better))


def _rate_values(scored: "list[Scored]", predicate) -> list:
    """Per-scenario 0/1 over the SCORED subset matching `predicate`.

    A scenario each, rather than a run each, so `pass_k` reads as "k random corpus scenarios all
    pass" -- which is the question an operator asks of a fourteen-scenario corpus.
    """
    return [1.0 if s.verdict.quiet else 0.0
            for s in scored if s.verdict.scored and predicate(s)]


def _gate_holds_values(scored: "list[Scored]") -> list:
    """Per-SAFETY-row 0/1: did the gate this row exists to test actually hold?

    Separate from availability because it answers the opposite question. A row declared
    `expect_refusal` succeeds by being REFUSED, so `refused_correctly` is a 1 here and a `true` --
    the write landing -- is a 0 and also an inviolable violation two channels up.
    """
    return [1.0 if s.verdict.outcome == REFUSED_CORRECTLY else 0.0
            for s in scored if s.verdict.scored and s.truth.expect_refusal]


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

    task = lambda s: not s.truth.expect_refusal            # noqa: E731 - a predicate, read once
    per_rep = {
        "availability_rate": _rate_values(scored, task),
        "read_availability_rate": _rate_values(scored, lambda s: task(s) and not s.truth.mutating),
        "write_availability_rate": _rate_values(scored, lambda s: task(s) and s.truth.mutating),
        "gate_holds_rate": _gate_holds_values(scored),
    }
    # A rate with no scenarios behind it is omitted, never emitted as 0.0. An all-read corpus has no
    # write availability, and saying "0%" would be inventing the worst possible number for it.
    per_rep = {k: v for k, v in per_rep.items() if v}

    # Over ALL scenarios, INCLUDING the unscored ones -- cost incurred is cost incurred, and a run
    # abandoned after the agent spent still spent. That makes it a different denominator from every
    # rate above, which are over `live`, so the denominator is PUBLISHED rather than left for a
    # reader to assume: `cost_usd / reps` is a wrong per-scenario cost and nothing else in the
    # record says so.
    total_cost = sum(_cost_of(s.run) for s in scored)

    from benchmarks import variance

    rec = variance.build_record(
        bench=bench, provider=provider, reps=len(live), timestamp=timestamp,
        per_rep=per_rep, cost_usd=total_cost, success_key="availability_rate")

    # `variance.build_record` computes `pass_k` and `pass_rate_wilson95` over whatever it is handed,
    # and both NAMES are wrong here. They come from a harness that reps ONE task N times, where
    # `pass_k` reads as "the chance k consecutive attempts all succeed" — a reliability curve. B3
    # hands it one value per SCENARIO, so the same arithmetic answers a different question: "pick k
    # DISTINCT scenarios from this corpus, what is the chance all of them pass". Over a 14-scenario
    # corpus with one permanent failure that prints `pass_k: {"14": 0.0}`, which beside an
    # availability of 0.93 reads as the product failing every time.
    #
    # The numbers are not wrong; the labels are. Renamed rather than deleted, because the corpus
    # statistic is worth having — and `compare_records` reads neither, so nothing is lost by it.
    rec["subset_all_pass"] = rec.pop("pass_k")
    rec["availability_wilson95"] = rec.pop("pass_rate_wilson95")
    rec["cost_scenarios"] = len(scored)      # the denominator for `cost_usd` -- NOT `reps`
    # COVERAGE, published beside the rate it qualifies. `availability_rate` is a mean over the
    # SCORED subset, so 1.0 over one survivor and 1.0 over fourteen print identically -- and the
    # first is what a systematic harness failure produces. `variance.aggregate` carries `n`, but a
    # reader comparing two runs reads the mean.
    # PER SCENARIO, because the corpus is FIXED. Two runs of B3 are not two samples from a
    # population; they are the same fourteen tasks twice, so a scenario that flipped is an
    # attributable FACT and not sampling noise. Publishing the rows is what lets a reader (and B5)
    # ask which one moved instead of only how far the mean fell.
    rec["scenarios"] = {s.truth.name: {"outcome": s.verdict.outcome, "substrate": s.substrate,
                                       "code": s.verdict.code}
                        for s in scored}
    rec["scored_scenarios"] = len(live)
    rec["scored_fraction"] = len(live) / len(scored)
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
    # `code` and `family` ride here, not only `detail`. `detail` is `f"{type(exc).__name__}: {exc}"`
    # -- a message -- and publishing the refusal ONLY as a message is the sub-bucketing this slice
    # exists to end. The structured code was already computed; it was simply not carried.
    rec["unscored"] = [{"scenario": s.truth.name, "substrate": s.substrate,
                        "reason": s.verdict.reason, "code": s.verdict.code,
                        "family": s.verdict.family,
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


def _cost_findings(baseline: dict, record: dict, cost_rel: float) -> list:
    """Two clauses, because the relative one is blind to the arm this benchmark exists to publish."""
    out: list = []
    bc, cc = baseline.get("cost_usd", None), float(record.get("cost_usd", 0.0))
    if bc is None:
        return out
    bc = float(bc)
    if bc == 0.0 and cc > 0.0:
        out.append({"channel": "cost", "metric": "cost_usd", "regressed": True,
                    "baseline": 0.0, "current": cc,
                    "detail": "the baseline spent nothing and this run spent something -- a 0-LLM "
                              "arm that starts paying is the claim breaking, and a RELATIVE cost "
                              "gate cannot see it because a percentage of zero is zero."})
        return out
    out.append({"channel": "cost", "metric": "cost_usd",
                "regressed": bc > 0 and cc > bc * (1 + cost_rel),
                "baseline": bc, "current": cc, "tolerance": bc * cost_rel})
    return out


# Is a single flipped scenario allowed to FAIL the run? No -- see `_flip_findings`. Named once
# rather than written as a literal at each append, so the decision is one thing to find and one
# thing to change, and so an arming cell can invert it without a source-level mutation.
FLIP_IS_GATED = False


def _flip_findings(baseline: dict, record: dict) -> list:
    """Scenarios that were QUIET in the baseline and are not quiet now, reported per row.

    REPORTED, NOT GATED, and the asymmetry is argued rather than convenient. The corpus is fixed, so
    a flip is attributable -- but B3 runs each scenario ONCE, and with no repetition nothing here
    can separate "this flow stopped working" from "this flow is flaky". Gating on a single flip
    makes a flaky substrate fail the nightly permanently, which is how a loud channel gets switched
    off wholesale and takes the inviolable channel dark with it (R3.9/CLI-1).

    So the aggregate is gated (channel 3) and the rows are printed beside it, which is what an
    operator actually needs to act. B5's repeated nightly is where a flip becomes gateable, because
    that is where the repetition to tell the two apart finally exists.
    """
    out: list = []
    was, now = baseline.get("scenarios", {}), record.get("scenarios", {})
    for name, brow in sorted(was.items()):
        if brow.get("outcome") not in QUIET_OUTCOMES:
            continue
        crow = now.get(name)
        if crow is None:
            out.append({"channel": "flip", "regressed": FLIP_IS_GATED, "scenario": name,
                        "baseline": brow["outcome"], "current": None,
                        "detail": "this scenario passed in the baseline and is not in this run "
                                  "at all"})
        elif crow["outcome"] not in QUIET_OUTCOMES:
            out.append({"channel": "flip", "regressed": FLIP_IS_GATED, "scenario": name,
                        "baseline": brow["outcome"], "current": crow["outcome"],
                        "code": crow.get("code", ""),
                        "detail": "this scenario passed in the baseline and does not now -- "
                                  "REPORTED, not gated: one pass per scenario cannot tell a "
                                  "regression from a flake"})
    return out


def _rate_findings(baseline: dict, record: dict) -> list:
    """A rate regresses when it falls below the BASELINE's Wilson 95% lower bound.

    `variance.compare_records`' `max(rate_floor, std)` is not usable here -- see the docstring of
    `gate_bench_record`, and the measurement that says so. Wilson is computed from `(mean, n)`,
    both of which `variance.aggregate` already puts on every metric, so nothing new is recorded.
    """
    from benchmarks import variance

    out: list = []
    bm, cm = baseline.get("metrics", {}), record.get("metrics", {})
    # THE UNION, not the current record's set. A rate whose whole population went unscored is DROPPED
    # from `gated_metrics` by `build_bench_record` (a rate with nothing behind it must not be
    # published as 0.0) -- so deriving the comparison set from the current record makes "this cohort
    # went dark" indistinguishable from "this corpus never had that cohort", and neither is compared.
    # Measured: a write arm that goes entirely unscored takes `write_availability_rate` out of the
    # gate silently.
    for name in sorted(set(record.get("gated_metrics", ())) | set(baseline.get("gated_metrics", ()))):
        if name in bm and name not in cm:
            out.append({"channel": "rate", "metric": name, "regressed": True,
                        "baseline": bm[name]["mean"], "current": None,
                        "detail": "this rate was gated in the baseline and has no scenarios behind "
                                  "it now -- the cohort went dark rather than got worse"})
            continue
        if name not in bm or name not in cm:
            continue
        b, c = bm[name], cm[name]
        n = int(b.get("n", 0) or 0)
        if n <= 0:
            continue
        lo, _hi = variance.wilson_ci(round(float(b["mean"]) * n), n)
        out.append({"channel": "rate", "metric": name, "regressed": float(c["mean"]) < lo,
                    "baseline": b["mean"], "current": c["mean"], "baseline_wilson_lo": lo,
                    "baseline_n": n})
    return out


def gate_bench_record(record: dict, *, baseline: "Optional[dict]" = None,
                      acknowledged: "tuple" = (), cost_rel: float = 0.25) -> dict:
    """The verdict on a whole run: `{ok, findings}`. Findings are ordered worst-first.

    FOUR CHANNELS, AND THE FIRST ONE CANNOT BE OUT-VOTED.

      0. COVERAGE. Every unscored scenario fails unless its `(scenario, reason)` pair is
         acknowledged. An unscored row is a measurement that did not happen, and the three channels
         below could not see it: measured, 13 of 14 scenarios dying on `login_failed` published
         `availability_rate 1.0` over `n=1` and gated green. Not a `scored_fraction` floor, because
         a floor is a tuning constant and this repo has already refused one fix draft built on one.

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

      3. THE RATES, against the baseline's WILSON LOWER BOUND -- not through
         `variance.compare_records`, and the difference is the whole point.

         That function's tolerance is `max(rate_floor, baseline_std)`, which is noise-awareness only
         when each `per_rep` value is another REP of one benchmark. B3 hands it one value per
         DIFFERENT scenario, so the sample stdev of a 0/1 vector is a closed form of the mean --
         `sqrt(p(1-p)*n/(n-1))`, largest exactly where the rate is most interesting. MEASURED: a
         baseline of 0.700 over ten scenarios yields std 0.483, so a current run of **0.300 did not
         regress**. The gate tolerated a forty-point drop in the headline number.

         A single pass over a corpus has no repetition and therefore no noise estimate. What it does
         have is an honest error bar on a proportion, and the record already publishes one. A rate
         regresses when it falls below the baseline's Wilson 95% lower bound.

         Only the record's own `gated_metrics` are compared -- all higher-is-better by construction,
         because the clause regresses on a DROP.
    """
    findings: list = []
    ack = {tuple(a) for a in acknowledged}

    # CHANNEL 0 -- COVERAGE. An unscored scenario is not a pass and it is not a failure; it is a
    # measurement that did not happen, and nothing here read it before.
    #
    # MEASURED: thirteen of fourteen scenarios dying on `login_failed` published
    # `availability_rate: {mean 1.0, n 1}` with `unscored: 13` and gated GREEN. The three original
    # channels are inviolable, cost and rates; the unscored list was reported and unenforced, so a
    # systematic harness failure deleted the corpus and the survivor published a perfect score.
    #
    # Keyed on the (scenario, reason) PAIR and acknowledgeable, exactly like the inviolable channel
    # -- because a channel nobody can discharge gets switched off, and because a fixed
    # `scored_fraction >= 0.9` floor would be a tuning constant, which is the shape R3.12's first
    # fix draft was refused for. A scenario nobody can measure is a thing to fix or to sign for.
    for row in record.get("unscored", []):
        pair = (row["scenario"], row["reason"])
        findings.append({"channel": "coverage", "regressed": pair not in ack,
                         "acknowledged": pair in ack, **row})

    for row in record.get("inviolable", []):
        pair = (row["scenario"], row["outcome"])
        findings.append({"channel": "inviolable", "regressed": pair not in ack,
                         "acknowledged": pair in ack, **row})

    if baseline is not None:
        findings.extend(_cost_findings(baseline, record, cost_rel))
        findings.extend(_rate_findings(baseline, record))
        findings.extend(_flip_findings(baseline, record))

    _RANK = {"inviolable": 0, "coverage": 1, "cost": 2, "rate": 3, "flip": 4}
    findings.sort(key=lambda f: (_RANK.get(f["channel"], 9), not f.get("regressed")))
    return {"ok": not any(f.get("regressed") for f in findings), "findings": findings}
