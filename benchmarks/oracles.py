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

# `now()`, `current_date`, … — anything that makes a query's answer depend on when it ran. Matched
# case-insensitively and on word boundaries, so a column legitimately CALLED `create_date` is not a
# hit while `CURRENT_DATE` is.
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
        """Identity carries the id; intent does not. So the comparison is on the tail, and the id
        rides along so two otherwise-identical landings stay two."""
        if not agent_ran:
            return Verdict(self.name, None, "the agent never ran, so the substrate still carries "
                                            "the PREVIOUS scenario's world (B2 rule 3)")
        after = self.probe()
        appeared = after.identities - before.identities
        vanished = before.identities - after.identities
        want = frozenset(self.expected_delta(before))
        matched = tuple(sorted(r for r in appeared if r[1:] in want))
        unmatched = tuple(sorted((r for r in appeared if r[1:] not in want))) + tuple(sorted(vanished))
        evidence = {"matched": [str(r) for r in matched], "unmatched": [str(r) for r in unmatched],
                    "expected": sorted(map(str, want))}
        if len(matched) == 1 and not unmatched:
            return Verdict(self.name, True, "exactly one record matching the intent landed", evidence,
                           matched=matched, unmatched=unmatched)
        if not matched and not unmatched:
            return Verdict(self.name, False, "nothing changed on the server", evidence,
                           matched=matched, unmatched=unmatched)
        if len(matched) >= 2:
            return Verdict(self.name, False,
                           f"the same intent landed {len(matched)} times — a DOUBLE submit",
                           evidence, matched=matched, unmatched=unmatched)
        return Verdict(self.name, False, f"the change does not match the intent: {evidence}",
                       evidence, matched=matched, unmatched=unmatched)

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


def for_substrate(name: str, substrate) -> list:
    """Every oracle that applies to `name`, DERIVED from the registry rather than typed at a call site.

    One place, because `--arm-oracles` and a scored run must see the SAME set: an oracle armed but not
    consulted, or consulted but not armed, is the hole this module exists to close, and two hand-typed
    lists is how that happens.
    """
    if name not in REGISTRY:
        raise OracleError(f"no oracles registered for substrate {name!r}; known: {sorted(REGISTRY)}")
    return [build(substrate) for build in REGISTRY[name]]


#: substrate -> factories. B4's PR 3 adds the 14-scenario corpus's oracles here; the machinery above
#: does not change for them, which is the point of shipping it first.
REGISTRY = {
    "gitea": (
        lambda s: GiteaReadOracle(s, "gitea-read"),
        lambda s: GiteaCommentOracle(s, 1, "looks right to me"),
    ),
}
