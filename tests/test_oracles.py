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

    #: Enough of a world for the corpus's own expected-answer functions to have one right answer:
    #: two states, a distinctive search token, and DISTINCT creation times so "oldest" is not a tie.
    DEFAULT = ({"number": 1, "title": "Alpha", "state": "open"},
               {"number": 2, "title": "Beta", "state": "closed"},
               {"number": 3, "title": "Marmalade parser rejects trailing commas", "state": "open"})

    def __init__(self, issues=None, comments=None):
        self.issues = [dict(i) for i in (issues if issues is not None else self.DEFAULT)]
        for i in self.issues:
            # DERIVED FROM THE NUMBER, so a cell that supplies its own issues does not have to know
            # this field exists — and `oldest` still means `#1`, as the real seed makes it mean.
            i.setdefault("created_at", f"2026-01-{15 + i['number']:02d}T09:00:00Z")
            # THE REAL API RETURNS A BODY, so the fake must too. It did not, and that was invisible
            # until the search term moved into one (R4.101): `_search_title` then matched over
            # `title + body` and every cell using this fake started computing a blank answer.
            i.setdefault("body", "")
        if issues is None:
            # THE SEARCH TOKEN LIVES IN A BODY, exactly where the real seed puts it, and is taken
            # from the corpus rather than retyped. A literal here would be a second place to edit,
            # and the failure mode is silent: the fake would still serve three plausible issues and
            # the corpus's search answer would simply become uncomputable. Imported lazily for the
            # reason `corpus_oracles` does it — this module is imported by `test_corpus`, which the
            # corpus does not import back, and the deferral keeps that one-way.
            from benchmarks import corpus as _c   # module binding: a direct import of
            # SEARCH_TERM would bind the VALUE, leaving `mutate_corpus_value` inert here.
            self.issues[1]["body"] = f"Only for {_c.SEARCH_TERM} sources."
        #: {issue number: [(login, body), …]}
        self.comments = dict(comments or {})

    def token(self):
        return "t" * 40

    def _api(self, token, method, path, payload=None):
        """Serves the QUERY STRING too, not just the route.

        `state=` and `q=` are what `gitea-filter-state` and `gitea-search` turn on, and a fake that
        ignored them would hand every caller the whole list — so the closed COUNT and the search hit
        would both be wrong, and the cells that compute them would be asserting the fake's shape
        rather than the corpus's logic. `_search_title` refuses a non-unique hit, so ignoring `q`
        does not even fail quietly; it fails for the wrong reason, which is worse to debug.
        """
        assert method == "GET", f"a unit-test fake serves reads only, got {method} {path}"
        if "/comments" in path:
            n = int(path.rsplit("/issues/", 1)[1].split("/")[0])
            return [{"issue_url": f"http://x/{n}", "user": {"login": u}, "body": b}
                    for u, b in self.comments.get(n, ())]
        route, _, qs = path.partition("?")
        query = dict(kv.split("=", 1) for kv in qs.split("&") if "=" in kv)
        if "/issues/" in route:                      # a single issue, by index
            n = int(route.rsplit("/issues/", 1)[1])
            hit = [i for i in self.issues if i["number"] == n]
            assert hit, f"the fake holds no issue {n}"
            return hit[0]
        out = list(self.issues)
        if query.get("state", "all") != "all":
            out = [i for i in out if i["state"] == query["state"]]
        if "q" in query:
            out = [i for i in out if query["q"].lower() in i["title"].lower()]
        return out


class _FakeOdoo:
    """A substrate stand-in that SERVES `query()`, so the real Odoo probes run against known rows.

    UNRECOGNISED SQL RAISES. A fake that answered `()` to a query it did not know would turn every
    probe whose SQL changed into an empty linkage set -- which compares EQUAL to another empty one,
    so the cell would go green having exercised nothing. That is this repo's "assume the stub is
    inert until measured" lesson, and the loud failure is what makes the fake worth having.
    """

    LEADS = (("1", "Quote for 12 Tables", "1", "t"),
             ("2", "Need 20 Desks", "3", "t"),
             ("3", "Quote for 150 carpets", "1", "t"))
    ORDERS = (("18", "S00018", "sale", "9"), ("19", "S00019", "sent", "4"))
    STAGES = (("1",), ("2",), ("3",), ("4",))
    #: (name, expected_revenue, stage) -- what the corpus's `_opportunities` asks for.
    OPPS = (("Quote for 12 Tables", "40000", "New"),
            ("Need 20 Desks", "60000", "Proposition"),
            ("Quote for 150 carpets", "40000", "New"))
    CUSTOMER = (("Gemini Furniture",),)

    def __init__(self, **over):
        for k, v in over.items():
            setattr(self, k.upper(), v)

    def query(self, sql: str) -> tuple:
        s = " ".join(sql.split()).lower()
        if "from crm_stage" in s and "join" not in s:
            return tuple(self.STAGES)
        if "join crm_stage" in s:
            return tuple(self.OPPS)
        if "from sale_order" in s and "res_partner" in s:
            return tuple(self.CUSTOMER)
        if "from sale_order" in s:
            return tuple(self.ORDERS)
        if "from crm_lead" in s and ", type from" in s:
            return tuple((i, n, "lead") for i, n, _st, _a in self.LEADS)
        if "from crm_lead" in s:
            return tuple(self.LEADS)
        raise AssertionError(
            f"_FakeOdoo does not recognise this query, so it would have answered nothing and the "
            f"cell would pass having exercised no probe:\n    {sql}")


#: substrate -> its unit-test stand-in. Asserted against the corpus below, so a substrate added
#: without a fake fails HERE rather than silently dropping out of every property in this file.
FAKES = {"gitea": _Fake, "odoo": _FakeOdoo}


def corpus_oracles(name: str) -> list:
    """The oracle set a scored run would consult, built against a fake substrate.

    DERIVED FROM THE CORPUS AND NOWHERE ELSE (R4.88). Until 0.124.0 this file walked an `O.REGISTRY`
    that `benchmarks/corpus.py` had superseded: two oracles under names no scenario used, while the
    corpus held seven. Every cell below said "derived over the registry, so an oracle added tomorrow
    is covered" and none of them covered the five that had been added -- including the only oracle
    with real SQL, which is the only thing the clock scan can police.
    """
    from benchmarks import corpus
    return corpus.oracles_for(name, FAKES[name]())


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
    report = O.arm_oracles(corpus_oracles("gitea"))
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
    for name in FAKES:
        O.arm_oracles(corpus_oracles(name))


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
    for name in FAKES:
        assert O.forbidden_clock_reads(corpus_oracles(name)) == {}, name


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


def test_the_odoo_read_probe_reads_every_surface_it_claims() -> None:
    """The Odoo half of the surface cell above, and it is the ONLY offline guard on probe breadth.

    `arm_oracles` structurally cannot see this: `_adjudicate_against` REPLACES `probe` with the
    falsified rows, so an oracle whose probe watches half of what it claims arms perfectly. The
    liveness pass catches it against a real container, and this catches it in CI — the two are not
    redundant, they are the only two instruments that exist for it.

    A narrowed probe is not hypothetical. `GiteaReadOracle` shipped watching only the issue list
    while claiming "a read must change NOTHING on the server", and a comment posted during a read
    left it reporting True.
    """
    sub = _FakeOdoo()
    rows = O.OdooReadOracle(sub, "fake-odoo-read").probe().rows
    kinds = {r[0] for r in rows}
    assert kinds == {"lead", "order"}, (
        f"the probe returned {kinds}; it claims to watch {O.OdooReadOracle.SURFACES} and a surface "
        f"it does not read is a claim nothing checks")
    assert ("order", "18", "S00018", "sale", "9") in rows


def test_every_odoo_query_is_answerable_by_the_fake() -> None:
    """The fake RAISES on an unrecognised query, so this drives each declared query through it.

    Without it a probe's SQL could change, the fake would raise inside a cell nobody runs, and the
    surface cell above would keep passing on stale rows — a fake is only worth what its coverage is.
    """
    sub = _FakeOdoo()
    for oracle in corpus_oracles("odoo"):
        assert oracle.probe().rows, f"{oracle.name}: probed the fake and got an EMPTY linkage set"


# --- 6. one registry, so armed and consulted cannot diverge --------------------------------------

def test_the_oracle_set_has_exactly_one_derivation() -> None:
    """`benchmarks.oracles` must hold NO substrate registry of its own (R4.88).

    It held one, and `--arm-oracles` -- the gate a scored run must pass -- read it: two oracles under
    names no scenario used, against a corpus of seven. The operator was told "the oracle set is
    armed" over 2 of 7. Deleting the second list is the fix; this cell is what stops it coming back,
    because a reviewer adding a convenience lookup here would not think of it as a second list.

    The set cannot live in that module anyway: it is a fact about the CORPUS, and `corpus.py` imports
    `oracles.py`, so only the layer that knows both halves can derive it.
    """
    assert not hasattr(O, "REGISTRY") and not hasattr(O, "for_substrate"), (
        "`benchmarks.oracles` has grown a second oracle registry. The set is derived by "
        "`corpus.oracles_for`, once; two derivations is how the gate came to arm 2 of 7 (R4.88).")


def test_every_corpus_substrate_has_a_unit_test_fake() -> None:
    """Derived, so a third substrate cannot join the corpus and silently skip every cell here."""
    from benchmarks import corpus

    assert sorted(FAKES) == sorted(corpus.CORPORA), (
        f"substrates without a fake: {sorted(set(corpus.CORPORA) - set(FAKES))}")


def test_every_registered_oracle_declares_a_falsification() -> None:
    """Derived over the registry, so an oracle added tomorrow is covered without editing this cell."""
    for name in FAKES:
        for o in corpus_oracles(name):
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
    for name in FAKES:
        for o in corpus_oracles(name):
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
    read = [o for name in FAKES for o in corpus_oracles(name) if not o.MUTATING]
    assert read, "no read oracle in the registry, so this cell proves nothing"
    O.arm_oracles(read)


def test_the_double_falsification_uses_genuinely_identical_content() -> None:
    """The cell that would have caught the original. A falsification whose two rows differ in any
    user-visible field is not a double-submit — it is two different writes."""
    for name in FAKES:
        for o in corpus_oracles(name):
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
