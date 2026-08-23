"""The oracle machinery, and the gate that has to exist before any oracle is trusted.

AN ORACLE THAT CANNOT FAIL IS THE MOST DANGEROUS OBJECT IN THIS REPO. It approves everything,
publishes a perfect availability rate, and B3's outcome, B5's baseline and the honesty page all
inherit it. Nothing downstream can notice, because every one of them takes the oracle's word.

So this file is mostly about the GATE rather than about the two oracles that exist today: the plan's
gate 1 says every oracle is demonstrated RED against a broken world before it is trusted, and the
machinery for that shipped before the corpus it will police.

DOCKER-FREE, and asserted rather than assumed — see `_docker_is_not_available_to_unit_tests`. The
arming gate being offline is not an implementation detail: it is what lets `--arm-oracles` gate
EVERY run instead of a nightly one.
"""

from __future__ import annotations

import pytest

from benchmarks import oracles as O
from benchmarks import substrates as S


@pytest.fixture(autouse=True)
def _docker_is_not_available_to_unit_tests(monkeypatch, request):
    """The third axis of "a local green is weaker evidence than CI" (CLAUDE.md), applied here too.

    This host has a Docker daemon and CI does not, so an oracle that quietly reached a live container
    would pass locally and fail both CI arms — measured on the previous slice, which is why the guard
    exists at all. Guarding `subprocess.run` rather than `_compose` for the reason recorded there: a
    guard on `_compose` also blocks the cell that tests `_compose`'s own error wrapping.
    """
    def _refuse(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args", [])
        raise AssertionError(
            f"{request.node.name} tried to run {' '.join(map(str, argv))[:80]!r} for real. "
            f"Oracle unit tests must not touch Docker — it is present here and ABSENT on CI.")

    monkeypatch.setattr(S.subprocess, "run", _refuse)


class _Fake:
    """A substrate stand-in that SERVES THE API, so the real `probe()` runs against known data.

    A fake that only supplies a token would let every cell stub `probe` out — and a stubbed probe
    cannot notice a probe aimed at the wrong thing. Measured: narrowing the read probe back to
    issues-only survived the whole file until this fake started answering `_api`, because one cell
    replaced `probe` with a lambda and the other only compared strings.
    """

    user, repo = "bench", "acme"

    def __init__(self, issues=None, comments=None):
        self.issues = list(issues if issues is not None else
                           [{"number": 1, "title": "Alpha", "state": "open"},
                            {"number": 2, "title": "Beta", "state": "closed"}])
        #: {issue number: [(login, body), …]}
        self.comments = dict(comments or {})

    def token(self):
        return "t" * 40

    def _api(self, token, method, path, payload=None):
        assert method == "GET", f"a unit-test fake serves reads only, got {method} {path}"
        if "/comments" in path:
            n = int(path.rsplit("/issues/", 1)[1].split("/")[0])
            return [{"issue_url": f"http://x/{n}", "user": {"login": u}, "body": b}
                    for u, b in self.comments.get(n, ())]
        return list(self.issues)


def _oracle(rows=()):
    o = O.GiteaReadOracle(_Fake(), "fake-read")
    o.probe = lambda: O.Probe(tuple(rows))       # type: ignore[method-assign]
    return o


# --- 1. a count is never an oracle ---------------------------------------------------------------

def test_a_probe_refuses_anything_that_is_not_a_linkage_set() -> None:
    """The type is the enforcement. "45 leads exist" is satisfied by the wrong 45; "the lead named X
    in stage Y" is not, and only the second can take part in a set difference."""
    O.Probe(())
    O.Probe((("issue", 1, "a", "open"),))
    with pytest.raises(O.OracleError, match="never an oracle"):
        O.Probe([("issue", 1)])                  # a list is not a linkage set
    with pytest.raises(O.OracleError, match="not hashable"):
        O.Probe(({"id": 1},),)                   # nor is a bag of dicts


def test_count_is_derived_and_cannot_be_supplied() -> None:
    p = O.Probe((("a",), ("b",)))
    assert p.count == 2 and p.identities == frozenset(((("a",)), (("b",))))
    with pytest.raises(TypeError):
        O.Probe((("a",),), count=1)              # type: ignore[call-arg]


# --- 2. the gate refuses in every direction it can be cheated ------------------------------------

class _Silent(O.Oracle):
    name = "silent"

    def probe(self):
        return O.Probe(())

    def expected_delta(self, before):
        return frozenset()


class _Empty(_Silent):
    name = "empty"

    def falsifications(self):
        return ()


class _AlwaysYes(_Silent):
    name = "always-yes"

    def falsifications(self):
        return (("anything at all", O.Probe(()), ()),)


@pytest.mark.parametrize("oracle,expect", [
    (_Silent(), "declares no falsifications"),
    (_Empty(), "EMPTY falsification set"),
    (_AlwaysYes(), "ACCEPTED a world where the task did not happen"),
])
def test_the_gate_refuses_an_oracle_that_has_never_been_seen_to_say_no(oracle, expect) -> None:
    """Three ways to be untrustworthy, and silence is the most likely one to ship.

    `_AlwaysYes` is the one that matters most: it looks like a working oracle, declares a
    falsification, and accepts it — which is precisely a benchmark cell that cannot fail.
    """
    with pytest.raises(O.OracleError) as exc:
        O.arm_oracles([oracle])
    assert expect in str(exc.value)
    assert "REFUSING to score" in str(exc.value)


def test_the_gate_passes_a_real_oracle_set_and_names_every_case() -> None:
    """The other direction, or "refuse everything" satisfies every cell above."""
    report = O.arm_oracles(O.for_substrate("gitea", _Fake()))
    assert report, "the gate returned an empty report, so it adjudicated nothing"
    assert all(satisfied is False for _, _, satisfied in report), report
    labels = {label for _, label, _ in report}
    assert any("DOUBLE" in x for x in labels), "no falsification covers the double-submit inviolable"
    assert "a comment on the WRONG issue" in labels, "none covers incorrect_target"


def test_arming_the_real_registry_touches_no_container() -> None:
    """THE PROPERTY THAT LETS THIS GATE EVERY RUN. Arming is falsified probes, not a mutated
    substrate — so it costs nothing, needs no Docker, and can run in CI where a nightly cannot.

    The autouse guard makes `subprocess.run` raise, so this cell fails loudly the day arming starts
    reaching a container rather than quietly becoming a nightly-only check.
    """
    O.arm_oracles(O.for_substrate("gitea", _Fake()))


# --- 3. the clock scan (R4.86), in both directions -----------------------------------------------

@pytest.mark.parametrize("query,flagged", [
    ("SELECT id FROM crm_lead WHERE create_date > %s", False),
    ("SELECT age FROM people", False),                       # a COLUMN named age, not the function
    ("SELECT partner_age, order_now_flag FROM t", False),
    ("SELECT id FROM t WHERE d < now()", True),
    ("SELECT id FROM t WHERE d < NOW ()", True),
    ("SELECT id FROM t WHERE d < CURRENT_DATE", True),
    ("SELECT id FROM t WHERE d < current_timestamp", True),
    ("SELECT age(d) FROM t", True),                          # one-arg age() IS clock-dependent
    ("SELECT localtime", True),
])
def test_the_clock_scan_flags_the_calls_and_not_the_columns(query, flagged) -> None:
    """R4.86's second-order finding, made checkable.

    The Odoo app container's clock is pinned and its Postgres container's is not, so a query asking
    the clock answers a different question than the UI shows — measured, overdue = 14 against `now()`
    versus 3 against the epoch — and a different one again next month.

    Both directions matter. Flagging a column called `age` would be an over-refusal inside a gate,
    which is the D0 shape that gets a check switched off wholesale; the first draft did exactly that
    and this row is why the pattern splits calls from keywords.
    """
    class _Q(_Silent):
        QUERIES = (query,)

    assert bool(O.forbidden_clock_reads([_Q()])) is flagged


def test_no_registered_oracle_asks_the_clock() -> None:
    """The scan pointed at the real set, which is the only place it decides anything."""
    assert O.forbidden_clock_reads(O.for_substrate("gitea", _Fake())) == {}


def test_the_gate_refuses_an_oracle_whose_query_reads_the_clock() -> None:
    """...and the scan is wired INTO the gate, not merely available beside it."""
    class _Clocky(_Silent):
        name = "clocky"
        QUERIES = ("SELECT id FROM t WHERE deadline < now()",)

        def falsifications(self):
            return (("x", O.Probe(()), (("a",),)),)

    with pytest.raises(O.OracleError, match="reads the clock"):
        O.arm_oracles([_Clocky()])


# --- 4. no premise, no score ---------------------------------------------------------------------

def test_an_agent_that_never_ran_is_unscored_and_never_false() -> None:
    """B2's rule 3: when reset or readiness fails, `run_scenario` returns before the agent, and the
    substrate still carries the PREVIOUS scenario's world. An oracle asked about that world would
    report the previous row's write as this row's change — which B3 mints as `incorrect_target`, an
    inviolable that fails a run absolutely.

    So the answer is None. A quiet False would be a finding about the product for a fact about the
    harness.
    """
    v = _oracle().adjudicate(O.Probe(()), agent_ran=False)
    assert v.satisfied is None and v.adjudicated is False
    assert "never ran" in v.reason


def test_nothing_changed_is_false_and_says_so_specifically() -> None:
    """Distinct from `None`, and distinct from a mismatch: three states, three reasons."""
    o = O.GiteaCommentOracle(_Fake(), 1, "hello")
    o.probe = lambda: O.Probe(())                # type: ignore[method-assign]
    v = o.adjudicate(O.Probe(()), agent_ran=True)
    assert v.satisfied is False and "nothing changed" in v.reason


# --- 5. the read oracle's claim is exactly as wide as what it watches ----------------------------

def test_the_read_oracle_watches_every_surface_it_names() -> None:
    """ITS FIRST DRAFT DID NOT, and the liveness check caught it on the first run: it claimed "a read
    must change NOTHING" while probing only the issue list, so a posted comment left it saying True.
    The code was right and the CLAIM was wider than the check — R4.86's shape one module over.

    So `SURFACES` is a stated set and the queries have to cover it.
    """
    assert O.GiteaReadOracle.SURFACES == ("issues", "comments")
    joined = " ".join(O.GiteaReadOracle.QUERIES).lower()
    for surface in O.GiteaReadOracle.SURFACES:
        assert surface in joined, f"{surface!r} is claimed as watched and no query reads it"


def test_the_read_oracle_rejects_a_comment_appearing_during_a_read() -> None:
    """The specific hole the first draft had, kept as a cell rather than as a memory."""
    o = _oracle()
    base = O.Probe((("issue", 1, "Alpha", "open"),))
    o.probe = lambda: O.Probe(base.rows + (("comment", 1, "bench", "oops"),))  # type: ignore
    assert o.adjudicate(base, agent_ran=True).satisfied is False


def test_the_read_probe_actually_reads_the_comments_it_claims_to_watch() -> None:
    """DRIVES THE REAL PROBE, and that is the whole point of this cell existing beside the one above.

    Its two neighbours both passed while the probe was narrowed to issues only — one stubs `probe`
    out, the other compares `SURFACES` against `QUERIES` as strings. Neither can see a probe aimed at
    the wrong thing, which is the failure mode the liveness check found in the first draft. Caught
    here by a registered mutation that survived until this cell was written.
    """
    sub = _Fake(comments={1: [("bench", "a real comment")]})
    rows = O.GiteaReadOracle(sub, "fake-read").probe().rows
    kinds = {r[0] for r in rows}
    assert kinds == {"issue", "comment"}, (
        f"the probe returned {kinds}; it claims to watch {O.GiteaReadOracle.SURFACES} and a surface "
        f"it does not read is a claim nothing checks")
    assert ("comment", 1, "bench", "a real comment") in rows


# --- 6. one registry, so armed and consulted cannot diverge --------------------------------------

def test_for_substrate_refuses_an_unknown_substrate_instead_of_returning_nothing() -> None:
    """An empty oracle list is the silent version of having no oracles at all: `arm_oracles([])`
    passes trivially and a scored run then adjudicates nothing while reporting success."""
    with pytest.raises(O.OracleError, match="no oracles registered"):
        O.for_substrate("nosuch", _Fake())


def test_every_registered_oracle_declares_a_falsification() -> None:
    """Derived over the registry, so an oracle added tomorrow is covered without editing this cell."""
    for name in O.REGISTRY:
        for o in O.for_substrate(name, _Fake()):
            cases = o.falsifications()
            assert cases, f"{o.name} declares no falsification"
            for label, before, after in cases:
                assert isinstance(before, O.Probe), f"{o.name}/{label}: before is not a Probe"
                assert isinstance(after, tuple), f"{o.name}/{label}: after is not a row tuple"


# --- 7. a write oracle must be able to SEE the same write landing twice --------------------------
#
# R4.87, measured live: `GiteaCommentOracle`'s identity was `(issue, author, body)`, so two identical
# comments collapsed to ONE member of a set. B3 decides `double` on `len(matched) >= 2`, so a write
# that fired twice scored `true` — the inviolable was invisible in the oracle that exists to catch it.
# Gitea held two comments with ids [3, 4] and the identity set had one member.
#
# A falsification could not have caught it: the one labelled "DOUBLE-submitted" declared two rows
# differing by a TRAILING SPACE, so it passed while the hole was open. This project's worst-documented
# trap is a cell that asserts the counterexample, and that is what it was.

def test_a_write_oracle_distinguishes_two_identical_landings() -> None:
    """The property, asked of the identity function directly rather than through a falsification."""
    for name in O.REGISTRY:
        for o in O.for_substrate(name, _Fake()):
            if not getattr(o, "MUTATING", False):
                continue
            a, b = o.duplicate_pair()
            assert o.identity_of(a) != o.identity_of(b), (
                f"{o.name}: the same write submitted twice yields ONE identity "
                f"{o.identity_of(a)} — `double` is `len(matched) >= 2`, so it would score `true`")


def test_the_gate_refuses_a_write_oracle_that_cannot_see_a_double() -> None:
    """...and the gate enforces it, rather than this cell being the only place it is checked."""
    class _Blind(_Silent):
        name, MUTATING = "blind-write", True

        def falsifications(self):
            return (("x", O.Probe(()), (("a",),)),)

        def identity_of(self, record):
            return (record["body"],)          # the id dropped — the R4.87 shape exactly

        def duplicate_pair(self):
            return ({"id": 3, "body": "same"}, {"id": 4, "body": "same"})

    with pytest.raises(O.OracleError, match="collapses to one identity"):
        O.arm_oracles([_Blind()])


def test_the_gate_refuses_a_write_oracle_that_declares_no_duplicate_pair() -> None:
    """Silence is not a pass here either — same rule as the falsifications themselves."""
    class _NoPair(_Silent):
        name, MUTATING = "no-pair", True

        def falsifications(self):
            return (("x", O.Probe(()), (("a",),)),)

    with pytest.raises(O.OracleError, match="declares no duplicate_pair"):
        O.arm_oracles([_NoPair()])


def test_a_read_oracle_is_not_required_to_declare_one() -> None:
    """The other direction: a read has no write target, so the requirement must not apply to it —
    or every read oracle would have to invent a meaningless pair to satisfy a gate."""
    read = [o for o in O.for_substrate("gitea", _Fake()) if not o.MUTATING]
    assert read, "no read oracle in the registry, so this cell proves nothing"
    O.arm_oracles(read)


def test_the_double_falsification_uses_genuinely_identical_content() -> None:
    """The cell that would have caught the original. A falsification whose two rows differ in any
    user-visible field is not a double-submit — it is two different writes."""
    for name in O.REGISTRY:
        for o in O.for_substrate(name, _Fake()):
            if not getattr(o, "MUTATING", False):
                continue
            doubles = [rows for label, _, rows in o.falsifications() if "DOUBLE" in label]
            assert doubles, f"{o.name} declares no double-submit falsification"
            for rows in doubles:
                assert len(rows) >= 2, f"{o.name}: a double needs two rows"
                tails = {r[1:] for r in rows}
                assert len(tails) == 1, (
                    f"{o.name}: the double falsification's rows differ in a user-visible field "
                    f"{tails} — that is two different writes, not the same one twice")
                assert len({r[0] for r in rows}) == len(rows), (
                    f"{o.name}: the rows share a server key, so they are one record")
