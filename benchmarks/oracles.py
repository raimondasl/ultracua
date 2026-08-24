"""Server-side oracles: what the SUBSTRATE says happened, independent of what the agent claims.

The benchmark's whole credibility rests here. An agent can report success having changed nothing, or
having changed the wrong record; `landed` is the product's own evidence-bounded claim about its own
write and is explicitly NOT an input to a verdict (CLAUDE.md). So the adjudication comes from asking
the substrate directly — SQL for Odoo, the API for Gitea.

WHAT THIS MODULE IS BUILT AROUND, in the order the plan puts them:

1. **AN ORACLE THAT CANNOT FAIL IS THE MOST DANGEROUS OBJECT IN THIS REPO.** It approves everything
   and publishes a perfect score, and every instrument downstream — B3's outcome, B5's baseline, the
   honesty page — inherits the lie. So `arm_oracles()` exists before any oracle does, every oracle
   must declare how to falsify itself, and an oracle with no falsification is REFUSED rather than
   trusted. This is the plan's gate 1 and it is not optional.

2. **A COUNT IS NEVER AN ORACLE.** `Probe` carries a LINKAGE SET — the identities that answered the
   question — and `count` is a property of that set rather than a field anyone can set. "45 leads
   exist" is satisfied by the wrong 45; "the lead named X is owned by Y in stage Z" is not. The plan
   says this about writes; it is enforced for reads too, because a read oracle comparing counts has
   the identical hole.

3. **NO PREMISE, NO SCORE.** An oracle must establish that the world was in its pre-state before the
   agent ran. Without it, "the record was already there" scores as success, and a scenario whose
   data never existed scores as a clean miss. `Verdict.satisfied is None` is the honest answer and
   B3 already has `unscored` for it — which is deliberately absent from `QUIET_OUTCOMES`, so it
   cannot pass a gate.

4. **ORACLE SQL MAY NOT ASK THE CLOCK.** R4.86: the Odoo container's clock is pinned by libfaketime
   and its POSTGRES container's is not, so `now()` in an oracle answers a different question than the
   UI shows — measured, `overdue` = 14 against `now()` versus 3 against the epoch — and answers it
   differently again next month. `forbidden_clock_reads()` derives this over every registered oracle
   rather than trusting a rule written in prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Anything that makes a query's answer depend on WHEN it ran, matched case-insensitively.
# Split by FORM, not lumped, because the two halves carry different false-positive risk.
#
# CLOCK_CALLS are functions, flagged only when actually CALLED. `age` and `now` are also
# perfectly ordinary column names, and a scan that refused `SELECT age FROM people` would be an
# over-refusal inside a gate — the D0 shape that gets a check switched off wholesale. Measured:
# the first draft flagged exactly that. One-argument `age(x)` really is clock-dependent (it
# measures against today), so the CALL form is flagged and the bare column is not.
#
# CLOCK_KEYWORDS take no parentheses in SQL, so they are flagged bare — and no sane schema names
# a column `CURRENT_TIMESTAMP`.
CLOCK_CALLS = ("now", "age", "clock_timestamp", "statement_timestamp", "transaction_timestamp",
               "timeofday")
CLOCK_KEYWORDS = ("current_date", "current_time", "current_timestamp", "localtime",
                  "localtimestamp")
CLOCK_READS = CLOCK_CALLS + CLOCK_KEYWORDS
_CLOCK_RE = re.compile(
    r"\b(?:(?:" + "|".join(CLOCK_CALLS) + r")\s*\(|(?:" + "|".join(CLOCK_KEYWORDS) + r")\b)",
    re.I)


class OracleError(RuntimeError):
    """The oracle could not be trusted — refused rather than reported."""


@dataclass(frozen=True)
class Probe:
    """One question asked of a substrate, and the IDENTITIES that answered it.

    `rows` is the linkage set. It is a tuple of hashable identities — whatever uniquely names the
    thing the scenario is about (an issue number, a lead's `(name, stage, owner)`), never a bare
    count and never an opaque blob. `count` is derived so nobody can set one without the set.

    `query` is recorded so a verdict can be reviewed without re-running it, and so the clock scan has
    something to read.
    """

    rows: tuple
    query: str = ""
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.rows, tuple):
            raise OracleError(
                f"Probe.rows must be a tuple of identities, got {type(self.rows).__name__}. "
                f"A count is never an oracle: the linkage SET is what distinguishes 'the right "
                f"record changed' from 'some record changed'.")
        for r in self.rows:
            try:
                hash(r)
            except TypeError as exc:
                raise OracleError(
                    f"Probe identity {r!r} is not hashable, so it cannot take part in a set "
                    f"difference — which is the only thing this type is for.") from exc

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def identities(self) -> frozenset:
        return frozenset(self.rows)


@dataclass(frozen=True)
class Verdict:
    """The oracle's answer. `satisfied is None` means it could not adjudicate — never a quiet False.

    The distinction is load-bearing and B3 already consumes it: a False is a finding about the
    product, a None is a finding about the harness or the corpus, and collapsing them lets a broken
    premise read as a product failure.
    """

    oracle: str
    satisfied: Optional[bool]
    reason: str
    evidence: dict = field(default_factory=dict)
    #: The identities B3 consumes directly (`outcomes.Oracle.matched` / `.unmatched`). Carried as
    #: FIELDS rather than dug out of `evidence` strings, because `double` is decided by
    #: `len(matched) >= 2` and a verdict whose counts live in a display dict invites a re-derivation.
    matched: tuple = ()
    unmatched: tuple = ()

    @property
    def adjudicated(self) -> bool:
        return self.satisfied is not None


class Oracle:
    """One server-side question, asked twice: before the agent (premise) and after it (adjudication).

    Subclasses implement `_probe()`. They do NOT implement the premise/adjudication rules — those are
    here, once, so a new oracle cannot forget them. That is the shape this repo keeps re-learning:
    an invariant enforced per-subclass is an invariant with as many holes as there are subclasses.
    """

    #: Every query this oracle issues, so `forbidden_clock_reads` can read them without running it.
    QUERIES: tuple = ()
    #: Human name, used in refusals and in the arming report.
    name: str = ""
    #: Does this oracle adjudicate a WRITE? If so it must survive the duplicate-identity check below.
    MUTATING: bool = False

    def probe(self) -> Probe:  # pragma: no cover - overridden
        """Ask the substrate. MUST return the linkage set, not a count."""
        raise NotImplementedError

    # --- the rules, enforced once -----------------------------------------------------------

    def premise(self) -> Probe:
        """The world BEFORE the agent ran. Recorded, then handed back to `adjudicate`."""
        return self.probe()

    def expected_delta(self, before: Probe) -> frozenset:  # pragma: no cover - overridden
        """The identities that MUST appear. Empty means 'nothing may change'."""
        raise NotImplementedError

    def adjudicate(self, before: Probe, *, agent_ran: bool) -> Verdict:
        """Compare the world to `before` and say whether exactly the intended change landed."""
        if not agent_ran:
            return Verdict(self.name, None, "the agent never ran, so the substrate still carries "
                                            "the PREVIOUS scenario's world (B2 rule 3)")
        after = self.probe()
        want = frozenset(self.expected_delta(before))
        appeared = after.identities - before.identities
        vanished = before.identities - after.identities
        evidence = {"before": sorted(map(str, before.rows)), "after": sorted(map(str, after.rows)),
                    "appeared": sorted(map(str, appeared)), "vanished": sorted(map(str, vanished)),
                    "expected": sorted(map(str, want))}
        if appeared == want and not vanished:
            return Verdict(self.name, True, "exactly the intended identities appeared", evidence)
        if not appeared and not vanished:
            return Verdict(self.name, False, "nothing changed on the server", evidence)
        return Verdict(self.name, False,
                       f"the change does not match: appeared={sorted(map(str, appeared))} "
                       f"expected={sorted(map(str, want))} vanished={sorted(map(str, vanished))}",
                       evidence)

    # --- arming, which exists before any oracle does -----------------------------------------

    def duplicate_pair(self) -> tuple:  # pragma: no cover - overridden by write oracles
        """Two RAW records that are the same write submitted twice, differing only as the server
        made them differ (its own primary key).

        REQUIRED OF EVERY WRITE ORACLE, because a linkage set built from user-visible fields alone
        CANNOT SEE A DOUBLE-SUBMIT: two identical comments collapse to one identity, B3 reads
        `len(matched) == 1` and scores `true` for a write that fired twice. Measured live at
        0.123.0 — gitea held two comments with ids [3, 4] and the identity set had one member.

        A falsification cannot cover this on its own: the first attempt declared one labelled
        "DOUBLE-submitted" whose two rows differed by a trailing space, so it passed while the hole
        was wide open. This asks the oracle's own identity function the question directly.
        """
        raise NotImplementedError

    def identity_of(self, record) -> tuple:  # pragma: no cover - overridden by write oracles
        """The linkage identity of one raw record, as `probe()` would compute it."""
        raise NotImplementedError

    def falsifications(self) -> tuple:  # pragma: no cover - overridden
        """(label, before, after_rows) triples this oracle MUST reject.

        Declared, never derived: an oracle that generates its own counterexamples from its own
        logic agrees with itself by construction. These are hand-written statements of "here is a
        world where the task did NOT happen", and the point is that the oracle says so.
        """
        raise NotImplementedError


def forbidden_clock_reads(oracles) -> dict:
    """{oracle name: [offending query, …]} — R4.86's constraint, derived rather than trusted.

    The Odoo app container's clock is pinned and its Postgres container's is not, so a query asking
    the clock answers a different question than the UI shows, and a different one again next month.
    Measured at 0.121.0: `overdue` = 14 against `now()`, 3 against the pinned epoch.
    """
    out = {}
    for o in oracles:
        bad = [q for q in getattr(o, "QUERIES", ()) if _CLOCK_RE.search(q or "")]
        if bad:
            out[getattr(o, "name", type(o).__name__)] = bad
    return out


def arm_oracles(oracles) -> list:
    """Prove every oracle can say NO before any of them is allowed to say YES.

    THE GATE, and the reason this function was written before the first oracle. An oracle that
    cannot fail approves everything, publishes a perfect availability rate, and every instrument
    downstream inherits it. `--arm-oracles` runs this and a scored run refuses if it does not pass.

    Three ways to fail, and all three REFUSE rather than warn:
      * an oracle that declares no falsifications — untrusted by default, never trusted by silence;
      * a falsification the oracle ACCEPTS — the hole this whole function exists to find;
      * an oracle whose queries read the clock (R4.86).
    """
    report, problems = [], []
    clock = forbidden_clock_reads(oracles)
    for o in oracles:
        name = getattr(o, "name", type(o).__name__)
        if name in clock:
            problems.append(f"{name}: query reads the clock — {clock[name]}")
        try:
            cases = o.falsifications()
        except NotImplementedError:
            problems.append(f"{name}: declares no falsifications, so nothing has ever seen it say NO")
            continue
        if not cases:
            problems.append(f"{name}: declares an EMPTY falsification set, which is the same hole "
                            f"with a tidier spelling")
            continue
        if getattr(o, "MUTATING", False):
            try:
                a, b = o.duplicate_pair()
                ids = {o.identity_of(a), o.identity_of(b)}
            except NotImplementedError:
                problems.append(f"{name}: is a WRITE oracle and declares no duplicate_pair, so "
                                f"nothing has checked that it can see the same write landing twice")
                ids = None
            if ids is not None and len(ids) < 2:
                problems.append(
                    f"{name}: the SAME WRITE SUBMITTED TWICE collapses to one identity {ids} — "
                    f"`double` is decided by `len(matched) >= 2`, so a double-submit would be "
                    f"scored `true`. Put the record's own key in the linkage.")
        for label, before, after_rows in cases:
            probed = Probe(tuple(after_rows), label=f"falsified:{label}")
            verdict = _adjudicate_against(o, before, probed)
            if verdict.satisfied:
                problems.append(f"{name}/{label}: ACCEPTED a world where the task did not happen — "
                                f"{verdict.reason}")
            report.append((name, label, verdict.satisfied))
    if problems:
        raise OracleError(
            "REFUSING to score: the oracle set is not armed.\n  " + "\n  ".join(problems) +
            "\n    An oracle that cannot fail approves everything and publishes a perfect rate; "
            "every number downstream inherits that.")
    return report


def _adjudicate_against(oracle: Oracle, before: Probe, after: Probe) -> Verdict:
    """Run an oracle's adjudication against a SUPPLIED after-state instead of the live substrate.

    This is what makes arming cheap and deterministic: no substrate mutation, no ordering, no
    cleanup. It proves the ADJUDICATION rejects wrong evidence.

    It does NOT prove the probe reads the right thing — an oracle whose query is aimed at the wrong
    table is wrong in a way no falsified probe can see. That half needs a live substrate and is
    `tests/test_oracle_liveness.py`, kept separate for the same reason `test_door_liveness.py` is.
    """
    original = oracle.probe
    try:
        oracle.probe = lambda: after            # type: ignore[method-assign]
        return oracle.adjudicate(before, agent_ran=True)
    finally:
        oracle.probe = original                # type: ignore[method-assign]


def _match_on_tail(oracle, before: Probe, *, agent_ran: bool, thing: str) -> Verdict:
    """The adjudication every write oracle shares: intent matched on the TAIL, the server's key
    riding along so two identical landings stay two.

    THIS IS R4.87's RULE, AND IT LIVES ONCE. `GiteaCommentOracle` and `GiteaTimerOracle` each wrote
    it out, and the two copies had already drifted -- one built `unmatched` from a named `vanished`,
    the other inline -- before a third oracle existed to copy either. An invariant enforced
    per-subclass has as many holes as there are subclasses, which is the shape this repo keeps
    re-filing; the `Oracle` base already puts the premise/adjudication rules in one place for exactly
    this reason and the write half had escaped it.

    The asymmetry is the whole point. `expected_delta` CANNOT name the server's key (the corpus
    cannot predict an id), so intent is compared against `r[1:]`; the key stays in the identity, where
    it can only ever turn one landed write into TWO -- never two into one.
    """
    if not agent_ran:
        return Verdict(oracle.name, None, "the agent never ran, so the substrate still carries "
                                          "the PREVIOUS scenario's world (B2 rule 3)")
    after = oracle.probe()
    appeared = after.identities - before.identities
    vanished = before.identities - after.identities
    want = frozenset(oracle.expected_delta(before))
    matched = tuple(sorted(r for r in appeared if r[1:] in want))
    unmatched = tuple(sorted(r for r in appeared if r[1:] not in want)) + tuple(sorted(vanished))
    ev = {"matched": [str(r) for r in matched], "unmatched": [str(r) for r in unmatched],
          "expected": sorted(map(str, want))}
    if len(matched) == 1 and not unmatched:
        return Verdict(oracle.name, True, f"exactly one {thing} matching the intent landed", ev,
                       matched=matched, unmatched=unmatched)
    if not matched and not unmatched:
        return Verdict(oracle.name, False, "nothing changed on the server", ev,
                       matched=matched, unmatched=unmatched)
    if len(matched) >= 2:
        return Verdict(oracle.name, False,
                       f"the same intent landed {len(matched)} times -- a DOUBLE submit", ev,
                       matched=matched, unmatched=unmatched)
    return Verdict(oracle.name, False, f"the change does not match the intent: {ev}", ev,
                   matched=matched, unmatched=unmatched)


# ---------------------------------------------------------------------------------------------------
# CONCRETE ORACLES. Two, deliberately: one read and one write, on the substrate whose lifecycle is
# verified end to end. The 14-scenario corpus is PR 3 and slots into the machinery above; shipping
# fourteen oracles against an unproven framework is how a benchmark acquires fourteen silent holes.

class GiteaIssueOracle(Oracle):
    """Base for the Gitea oracles: the linkage set is `(number, title, state)` per issue.

    NOT a count, and not the title alone: `gitea-filter-state` turns on state and
    `gitea-open-issue` on identity, so an oracle that dropped either would be satisfied by the
    right issue in the wrong state.
    """

    QUERIES = ("GET /repos/{owner}/{repo}/issues?state=all&limit=100",)

    def __init__(self, substrate, name: str) -> None:
        self.substrate = substrate
        self.name = name

    def probe(self) -> Probe:
        token = self.substrate.token()
        issues = self.substrate._api(
            token, "GET", f"/repos/{self.substrate.user}/{self.substrate.repo}"
                          f"/issues?state=all&limit=100")
        rows = tuple(sorted((i["number"], i["title"], i["state"]) for i in issues))
        return Probe(rows, query=self.QUERIES[0], label=self.name)


class GiteaReadOracle(GiteaIssueOracle):
    """A read must leave the WATCHED SURFACES unchanged — and the watched set is stated, not implied.

    THE FIRST DRAFT SAID "a read must change NOTHING on the server" AND WATCHED ONLY THE ISSUE LIST.
    The liveness check caught it on its first run: a comment was posted, and this oracle still said
    True, because a comment does not alter any issue's `(number, title, state)`. The code was right
    and the claim was wider than the check — which is R4.86's shape exactly (a stated guarantee that
    is false), one module over, written by the same hand that had just fixed R4.86.

    So the surfaces are enumerated and the claim is exactly as wide as they are. `SURFACES` is what a
    reviewer checks; it covers what the Gitea corpus can actually mutate — issues, and the comments
    on them, which is where an accidental write would land given `gitea-comment` is the write
    scenario next door.

    WHAT THIS STILL CANNOT SAY, stated rather than left to be discovered: it is not a proof that
    NOTHING changed anywhere. Labels, stars, subscriptions and repo settings are unwatched, and a
    complete watch is not achievable against a real application. A read oracle is a bounded claim
    about named surfaces, and treating it as more than that is how a benchmark starts believing its
    own gaps.
    """

    #: Reviewed as a set, not one probe at a time: an unwatched surface is an unasserted claim.
    SURFACES = ("issues", "comments")
    QUERIES = ("GET /repos/{owner}/{repo}/issues?state=all&limit=100",
               "GET /repos/{owner}/{repo}/issues/{index}/comments")

    def probe(self) -> Probe:
        token = self.substrate.token()
        base = f"/repos/{self.substrate.user}/{self.substrate.repo}"
        issues = self.substrate._api(token, "GET", f"{base}/issues?state=all&limit=100")
        rows = [("issue", i["number"], i["title"], i["state"]) for i in issues]
        for i in issues:
            for c in self.substrate._api(token, "GET", f"{base}/issues/{i['number']}/comments"):
                rows.append(("comment", i["number"], c["user"]["login"], c["body"]))
        return Probe(tuple(sorted(rows)), query=" ; ".join(self.QUERIES), label=self.name)

    def expected_delta(self, before: Probe) -> frozenset:
        return frozenset()

    def falsifications(self) -> tuple:
        base = Probe((("issue", 1, "Alpha", "open"), ("issue", 2, "Beta", "closed")))
        return (
            ("an issue appeared", base, base.rows + (("issue", 3, "Gamma", "open"),)),
            ("an issue vanished", base, base.rows[:1]),
            ("a state flipped", base, (("issue", 1, "Alpha", "open"), ("issue", 2, "Beta", "open"))),
            # THE ONE THE FIRST DRAFT COULD NOT SEE. Watching only the issue list, this world is
            # indistinguishable from an untouched one.
            ("a COMMENT appeared during a read", base,
             base.rows + (("comment", 1, "bench", "posted by accident"),)),
        )


class GiteaCommentOracle(Oracle):
    """`gitea-comment`: exactly one comment, on the intended issue, with the intended body.

    The linkage is `(issue_number, author, body)`. A count would be satisfied by a comment on the
    WRONG issue, which is `incorrect_target` — the outcome B3 fails a run absolutely for.
    """

    QUERIES = ("GET /repos/{owner}/{repo}/issues/{index}/comments",)
    MUTATING = True

    def __init__(self, substrate, issue_number: int, expect_body: str,
                 name: str = "gitea-comment") -> None:
        self.substrate = substrate
        self.issue_number = issue_number
        self.expect_body = expect_body
        self.name = name

    def identity_of(self, record) -> tuple:
        """`(comment_id, issue, author, body)` — THE ID IS LOAD-BEARING AND IS WHY THIS METHOD EXISTS.

        Without it the identity is `(issue, author, body)`, and two identical comments collapse to
        one member of a set: B3 reads `len(matched) == 1` and scores a double-submit as `true`.
        Measured live — gitea held two comments, ids [3, 4], one identity. `double` is an inviolable
        and it was invisible.

        `expected_delta` therefore cannot name an id (the server assigns it), so the intended set is
        matched on the tail and the id only ever ADDS members. That is the right asymmetry: it can
        turn one landed write into two, never two into one.
        """
        return (record["id"], str(record["issue_url"].rsplit("/", 1)[-1]),
                record["user"]["login"], record["body"])

    def probe(self) -> Probe:
        token = self.substrate.token()
        comments = self.substrate._api(
            token, "GET", f"/repos/{self.substrate.user}/{self.substrate.repo}"
                          f"/issues/{self.issue_number}/comments")
        return Probe(tuple(sorted(self.identity_of(c) for c in comments)),
                     query=self.QUERIES[0], label=self.name)

    def expected_delta(self, before: Probe) -> frozenset:
        """Matched on the TAIL, because the server owns the id and the corpus cannot predict it."""
        return frozenset({(str(self.issue_number), self.substrate.user, self.expect_body)})

    def adjudicate(self, before: Probe, *, agent_ran: bool) -> Verdict:
        return _match_on_tail(self, before, agent_ran=agent_ran, thing="comment")

    def duplicate_pair(self) -> tuple:
        """The same comment, posted twice, exactly as the server returns it — same body, same author,
        DIFFERENT id. Modelled on the measured live shape (ids 3 and 4)."""
        url = f"http://x/{self.issue_number}"
        mk = lambda i: {"id": i, "issue_url": url, "user": {"login": self.substrate.user},
                        "body": self.expect_body}
        return (mk(3), mk(4))

    def falsifications(self) -> tuple:
        base = Probe(())
        me, body, n = self.substrate.user, self.expect_body, self.issue_number
        return (
            ("no comment at all", base, ()),
            ("a comment on the WRONG issue", base, ((9, str(n + 1), me, body),)),
            ("the wrong body", base, ((9, str(n), me, body + " (not this)"),)),
            # IDENTICAL CONTENT, different server ids — what a real double-submit looks like. The
            # first version of this row differed by a trailing space and therefore proved nothing.
            ("DOUBLE-submitted, identical content", base,
             ((3, str(n), me, body), (4, str(n), me, body))),
        )


# THE ORACLE SET LIVES IN `benchmarks/corpus.py`, AND THIS FILE DELIBERATELY HOLDS NO SECOND COPY.
#
# It used to. `REGISTRY`/`for_substrate` here listed two oracles under names no scenario used, while
# the corpus held seven — so `--arm-oracles`, the gate a scored run must pass, told the operator "the
# oracle set is armed" over 2 of 7, and six structural cells in `tests/test_oracles.py` that say
# "derived over the registry, so an oracle added tomorrow is covered" were covering neither the
# `GiteaTimerOracle` nor its SQL, which is the only query the R4.86 clock scan had to police (R4.88).
#
# The set cannot live here, and that is structural rather than stylistic: an oracle set is a fact
# about the CORPUS (which scenario is adjudicated by which oracle), and `corpus.py` imports this
# module. One derivation, in the layer that knows both halves — `corpus.oracles_for`.


class GiteaTimerOracle(Oracle):
    """`gitea-start-timer`: the tracked-time entry the timer creates, identified by its own id.

    IDENTITY COMES FROM `/issues/{n}/times`, NOT `/user/stopwatches`. The stopwatch listing carries
    no id at all — only `issue_index` and a timestamp — so two entries could not be told apart, which
    is R4.87 waiting to happen a second time. The times endpoint returns a real primary key.

    A DOUBLE IS SERVER-PREVENTED HERE, and that is recorded rather than relied on. Measured: a second
    `stopwatch/start` on the same issue returns HTTP 409, and a start on a DIFFERENT issue silently
    moves the stopwatch rather than adding one. So the product cannot double this write even if it
    tried. The oracle is still built to SEE a double — `duplicate_pair` proves its identity function
    distinguishes two — because the gate's question is whether the instrument can see it, not whether
    this particular substrate permits it. An oracle excused from the check because "it cannot happen
    here" is an oracle nobody has ever watched say no.
    """

    # The DATABASE, not the API. A running stopwatch has no id anywhere the API exposes, and
    # `/issues/{n}/times` stays EMPTY until the timer is STOPPED — measured, which is how the
    # first version of this oracle came to reject all four falsifications while seeing nothing
    # in the real world. Clock-free by construction, so the R4.86 scan passes it.
    QUERIES = ("SELECT id, issue_id, user_id FROM stopwatch",)
    MUTATING = True

    def __init__(self, substrate, issue_number: int, name: str = "gitea-start-timer",
                 user_id: int = 1) -> None:
        self.substrate = substrate
        self.issue_number = issue_number
        self.name = name
        #: The seeded admin is uid 1 — it is the first and only user `seed()` creates. Held as a
        #: number because the `stopwatch` table stores a foreign key, not a login.
        self.user_id = user_id

    def identity_of(self, record) -> tuple:
        return (record["id"], record["issue_id"], record["user_id"])

    def probe(self) -> Probe:
        rows = self.substrate.query(self.QUERIES[0])
        return Probe(tuple(sorted((int(a), int(b), int(c)) for a, b, c in rows)),
                     query=self.QUERIES[0], label=self.name)

    def expected_delta(self, before: Probe) -> frozenset:
        """Matched on the tail — the server owns the id, so intent names the issue and the user."""
        return frozenset({(self.issue_number, self.user_id)})

    def adjudicate(self, before: Probe, *, agent_ran: bool) -> Verdict:
        return _match_on_tail(self, before, agent_ran=agent_ran, thing="timer entry")

    def duplicate_pair(self) -> tuple:
        mk = lambda i: {"id": i, "issue_id": self.issue_number, "user_id": self.user_id}
        return (mk(1), mk(2))

    def falsifications(self) -> tuple:
        base, me, n = Probe(()), self.user_id, self.issue_number
        return (
            ("no timer started", base, ()),
            ("the timer started on the WRONG issue", base, ((7, n + 1, me),)),
            ("started by the wrong user", base, ((7, n, me + 99),)),
            ("DOUBLE-started, identical content", base, ((1, n, me), (2, n, me))),
        )


# ---------------------------------------------------------------------------------------------------
# ODOO. The first oracles here whose questions are real SQL, which is what makes the R4.86 clock scan
# more than a rule written in prose: every Gitea oracle but one asks the HTTP API, where a clock read
# is not even expressible.
#
# ONE DEFENCE THAT LOOKS LIKE FUSSINESS AND IS NOT: every text column is stripped of CR and LF inside
# the SQL. `Odoo.query` splits rows on newlines, so ONE record whose name contains a line break
# becomes TWO malformed identities -- a linkage set that silently GAINS a member is the same class of
# fault as one that silently loses a member (R4.87), and a lead name is free text an agent can write.

class OdooOracle(Oracle):
    """Base for the Odoo oracles: how to ask, and nothing else.

    Deliberately holds no `falsifications`, so `arm_oracles` refuses it rather than trusting it --
    the same reason `GiteaIssueOracle` is a base and not an oracle.
    """

    def __init__(self, substrate, name: str) -> None:
        self.substrate = substrate
        self.name = name

    def _rows(self, sql: str) -> tuple:
        return self.substrate.query(sql)


class OdooReadOracle(OdooOracle):
    """A read must leave the WATCHED SURFACES unchanged -- and the watched set is stated, not implied.

    THE CLAIM IS EXACTLY AS WIDE AS `SURFACES`, which is what `GiteaReadOracle` learned the hard way:
    its first draft claimed "a read must change NOTHING on the server" while watching only the issue
    list, so a comment posted during a read left it reporting True. Here the corpus can mutate two
    models -- `crm.lead` (both write scenarios) and `sale.order` (the record the keyword-tripping read
    opens) -- and both are watched.

    WHAT IT STILL CANNOT SAY, stated rather than left to be discovered: this is not a proof that
    nothing changed anywhere in an ERP. Odoo writes `res_users.login_date` on every login and
    `mail_message` / `bus_bus` as a matter of course, and a complete watch over a real application is
    not achievable. A read oracle is a bounded claim about named surfaces; treating it as more is how
    a benchmark starts believing its own gaps.
    """

    SURFACES = ("crm.lead", "sale.order")
    QUERIES = (
        "SELECT id, replace(replace(name, chr(13), ' '), chr(10), ' '), "
        "coalesce(stage_id, 0), active FROM crm_lead ORDER BY id",
        "SELECT id, replace(replace(name, chr(13), ' '), chr(10), ' '), "
        "state, coalesce(partner_id, 0) FROM sale_order ORDER BY id",
    )

    def probe(self) -> Probe:
        rows = [("lead",) + tuple(r) for r in self._rows(self.QUERIES[0])]
        rows += [("order",) + tuple(r) for r in self._rows(self.QUERIES[1])]
        return Probe(tuple(sorted(rows)), query=" ; ".join(self.QUERIES), label=self.name)

    def expected_delta(self, before: Probe) -> frozenset:
        return frozenset()

    def falsifications(self) -> tuple:
        base = Probe((("lead", "1", "Quote for 12 Tables", "1", "t"),
                      ("order", "18", "S00018", "sale", "9")))
        return (
            ("a lead appeared", base, base.rows + (("lead", "99", "Something new", "1", "t"),)),
            ("a lead vanished", base, base.rows[:1]),
            ("a lead moved stage", base,
             (("lead", "1", "Quote for 12 Tables", "4", "t"), base.rows[1])),
            # THE ONE A LEAD-ONLY PROBE COULD NOT SEE -- the analogue of the comment that defeated
            # `GiteaReadOracle`'s first draft. `odoo-open-record` opens a sale order, so an
            # accidental write lands HERE, nowhere near `crm_lead`.
            ("an ORDER changed state during a read", base,
             (base.rows[0], ("order", "18", "S00018", "cancel", "9"))),
        )


class OdooLeadOracle(OdooOracle):
    """`odoo-create-lead`: exactly one lead, with the intended name, of the intended type.

    THE ID IS IN THE IDENTITY AND THAT IS R4.87. Without it two identical leads collapse to one
    member of a set, B3 reads `len(matched) == 1`, and a write that fired twice scores `true` -- the
    inviolable invisible in the oracle built to catch it.

    THE WHOLE `crm_lead` TABLE IS PROBED, not only rows carrying the intended name. A probe narrowed
    to the expected name cannot see a record created with the WRONG one, which is `incorrect_target`
    -- also an inviolable, and the likeliest way a write goes wrong on a list of similar records.
    """

    QUERIES = ("SELECT id, replace(replace(name, chr(13), ' '), chr(10), ' '), type FROM crm_lead",)
    MUTATING = True

    def __init__(self, substrate, expect_name: str, name: str, expect_type: str = "lead") -> None:
        super().__init__(substrate, name)
        self.expect_name = expect_name
        self.expect_type = expect_type

    def identity_of(self, record) -> tuple:
        return (str(record["id"]), str(record["name"]), str(record["type"]))

    def probe(self) -> Probe:
        rows = tuple(sorted((str(a), str(b), str(c)) for a, b, c in self._rows(self.QUERIES[0])))
        return Probe(rows, query=self.QUERIES[0], label=self.name)

    def expected_delta(self, before: Probe) -> frozenset:
        return frozenset({(self.expect_name, self.expect_type)})

    def adjudicate(self, before: Probe, *, agent_ran: bool) -> Verdict:
        return _match_on_tail(self, before, agent_ran=agent_ran, thing="lead")

    def duplicate_pair(self) -> tuple:
        mk = lambda i: {"id": i, "name": self.expect_name, "type": self.expect_type}
        return (mk(101), mk(102))

    def falsifications(self) -> tuple:
        base, n, ty = Probe(()), self.expect_name, self.expect_type
        return (
            ("no lead at all", base, ()),
            ("a lead with the WRONG name", base, (("101", n + " (not this)", ty),)),
            ("the wrong record type", base, (("101", n, "opportunity" if ty == "lead" else "lead"),)),
            ("DOUBLE-submitted, identical content", base, (("101", n, ty), ("102", n, ty))),
        )


class OdooIdempotentReplayOracle(OdooLeadOracle):
    """`odoo-idempotent-replay`: the write is learned, then REPLAYED, and must land exactly once.

    WHAT THIS ORACLE CANNOT SEE, said here rather than discovered from a green number. From the
    server side, "the replay ran and the idempotency mechanism suppressed the second write" and
    "`_precheck_done` returned `already-done` before the browser did anything at all" are THE SAME
    WORLD: one lead, no second row. So it adjudicates the outcome and says nothing about the
    mechanism.

    The plan requires the difference. Gate 1 asks that `idempotent-replay` assert the mechanism RAN,
    "since `_precheck_done` returns `already-done` before any browser action and would otherwise pass
    inert" -- and that evidence is a REQUEST, not a record, so it belongs to the Idempotency-Key
    logging proxy rather than to any query. `INCOMPLETE_WITHOUT` carries the gap as a DECLARED limit
    instead of an absence nobody notices, and `tests/test_corpus.py` pins the set of incomplete
    oracles to exactly this one -- so the day the proxy lands, deleting the marker is forced by a red
    test, and a second incomplete oracle cannot appear quietly.
    """

    #: The evidence this oracle is missing. Absent on every complete oracle, and asserted both ways.
    INCOMPLETE_WITHOUT = "the Idempotency-Key logging proxy: whether the write mechanism RAN at all"
