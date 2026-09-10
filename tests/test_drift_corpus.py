"""drift-bench v2's mutation MODEL, tested without a browser (milliseconds).

The model is a claim about `locators.resolve()`'s decision surface. Testing it separately from the harness
is what makes it falsifiable: if these pass and the bench's `predicted_agreement` drops, the resolver moved,
not the model — and vice versa. Also guards the two ways the bench could quietly lie about itself: an
invisible oracle attribute becoming a locator hint, and fixture copy that trips the interstitial detector.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.drift_corpus import (
    ANCHORS,
    MUTATOR_VERSION,
    PRIMITIVES,
    PRIMITIVES_BY_NAME,
    SEMANTIC,
    TIER1,
    BindState,
    apply_composition,
    compose,
    corpus_hash,
    interstitial_offenders,
    intensity,
    killed_anchors,
    kills_on,
    predict,
)
from benchmarks.drift_fixtures import SCENARIOS, all_fixture_texts, mutation_js

FULL = {"testid", "role+name", "role+name~", "placeholder", "exact-text", "css", "anchor"}


# ============================ the predictor mirrors resolve() ============================

@pytest.mark.parametrize("anchor", TIER1)
def test_any_surviving_tier1_anchor_means_a_confident_bind(anchor: str) -> None:
    """`resolve()` returns on the FIRST unique confident match, so one surviving Tier-1 anchor is enough."""
    assert predict(BindState(alive={anchor})) == ("survived", anchor)


def test_tier1_order_is_the_resolvers_order() -> None:
    """When several confident anchors survive, the model must name the one the resolver would actually use —
    otherwise `bound_by` agreement is meaningless."""
    assert predict(BindState(alive=set(TIER1)))[1] == TIER1[0] == "testid"
    assert predict(BindState(alive=set(TIER1) - {"testid"}))[1] == "role+name"


def test_a_tier1_anchor_pointing_at_a_decoy_is_a_wrong_bind_not_a_survival() -> None:
    assert predict(BindState(decoy={"role+name"})) == ("wrong", "role+name")


def test_css_alone_binds_but_a_contradicting_substring_fails_loud() -> None:
    """The Tier-2 cross-check: css is structural so it is trusted alone, but if the substring guess uniquely
    contradicts it NEITHER is trustworthy and the safe answer is to fail loud."""
    assert predict(BindState(alive={"css"})) == ("survived", "css")
    assert predict(BindState(alive={"css"}, decoy={"substring"}))[0] == "drifted"


def test_a_lone_substring_is_never_trusted() -> None:
    """With the target's own text changed, a substring match may be a decoy and nothing corroborates it."""
    assert predict(BindState(alive={"substring"}))[0] == "drifted"
    assert predict(BindState(decoy={"substring"}))[0] == "drifted"


def test_the_neighbour_anchor_cannot_resurrect_a_dead_role_name() -> None:
    """Tier 3 re-queries `get_by_role(role, name, exact=True)` scoped to the landmark, so a dead accessible
    name is dead there too — the anchor only NARROWS an ambiguous one."""
    assert predict(BindState(alive={"anchor"}))[0] == "drifted"
    assert predict(BindState(alive={"anchor"}, ambiguous={"role+name"})) == ("survived", "anchor")


def test_nothing_left_is_a_loud_failure() -> None:
    assert predict(BindState()) == ("drifted", "none")


# ============================ intensity is derived, never chosen ============================

def test_k_is_the_size_of_the_destroyed_anchor_set() -> None:
    combo = ("testid_drop", "rename_full")
    killed = killed_anchors(FULL, combo)
    assert intensity(FULL, combo) == len(killed)
    assert "testid" in killed and "role+name" in killed
    assert "placeholder" not in killed          # untouched by either primitive


def test_a_primitive_only_counts_against_anchors_the_target_actually_has() -> None:
    """A roleless target has no role+name to lose, so a rename costs it less intensity than it costs a
    button. This is why `k` is fixture-relative and why the record ships the per-target anchor set."""
    span = {"exact-text", "css"}
    assert intensity(span, ("rename_full",)) == 1        # only exact-text existed
    assert intensity(FULL, ("rename_full",)) == 4        # exact-text + both role+name forms + anchor


def test_kill_sets_are_conditional_on_whether_the_target_bears_an_id() -> None:
    """A direct consequence of `cssPath` stopping at the first id-bearing ancestor (locators.py:80),
    measured on a real page: an id-bearing element's css IS `#id`, so it is not positional and a wrapper
    cannot move it — while `hash_ids` takes out both at once."""
    with_id = FULL | {"elem_id"}
    assert kills_on("hash_ids", with_id) == frozenset({"elem_id", "css"})
    assert kills_on("wrap", with_id) == frozenset()
    without_id = FULL
    assert kills_on("hash_ids", without_id) == frozenset()
    assert kills_on("wrap", without_id) == frozenset({"css"})


def test_a_decoy_primitive_destroys_nothing_but_redirects_an_anchor() -> None:
    st = apply_composition(FULL, ("decoy_substring",))
    assert killed_anchors(FULL, ("decoy_substring",)) == []
    assert "substring" not in st.alive


# ============================ the sampler ============================

def test_the_sampler_is_deterministic_and_seed_sensitive() -> None:
    assert compose(FULL, seed=7) == compose(FULL, seed=7)
    assert compose(FULL, seed=7) != compose(FULL, seed=8)


def test_the_sampler_reaches_total_anchor_destruction() -> None:
    """The heal-eligible band IS full destruction with the target still present. A ladder that stops one
    anchor short reproduces v1's starved condition, where heal is never consulted at all."""
    rows = compose(FULL, seed=7)
    top = [r for r in rows if intensity(FULL, r) == len(FULL)]
    assert top, f"no composition destroys all {len(FULL)} anchors — nothing would be heal-eligible"
    for r in top:
        assert predict(apply_composition(FULL, r))[0] == "drifted"


def test_every_k_bucket_is_populated_and_compositions_stay_short() -> None:
    rows = compose(FULL, seed=7)
    ks = {intensity(FULL, r) for r in rows}
    assert ks == set(range(1, len(FULL) + 1))
    assert sum(len(r) for r in rows) / len(rows) < 3.5      # shortest-first sampling


def test_semantic_primitives_are_kept_out_of_the_composition_pool() -> None:
    """A "target gone" row is an INVARIANT (must fail loud), never part of a resilience rate. Mixing it into
    a composition would score a correct fail-loud as a miss."""
    pool = {p.name for p in PRIMITIVES}
    assert {p.name for p in SEMANTIC}.isdisjoint(pool)
    assert all(p not in pool for p in ("target_removed", "target_replaced_crosstag"))


# ============================ the corpus digest ============================

def test_corpus_hash_ignores_order_but_not_content() -> None:
    a = [{"scenario": "s", "step_index": 0, "primitives": ["wrap"]},
         {"scenario": "s", "step_index": 0, "primitives": ["testid_drop"]}]
    assert corpus_hash(a) == corpus_hash(list(reversed(a)))
    assert corpus_hash(a) != corpus_hash(a[:1])                    # a SHRUNK corpus cannot pass unnoticed
    assert corpus_hash(a) != corpus_hash(
        [{"scenario": "s", "step_index": 1, "primitives": ["wrap"]}, a[1]])


def test_mutator_version_is_pinned() -> None:
    assert MUTATOR_VERSION == 1


# ============================ harness integrity ============================

def test_composed_mutations_are_syntactically_joined() -> None:
    """A plain concatenation produced invalid JS whenever a primitive lacked a trailing semicolon, which
    silently left the page PRISTINE — so every row scored `survived`. The bench also asserts at run time
    that each mutation actually applied; this pins the joiner itself."""
    js = mutation_js(("a=1", "b=2;", " c=3 "))
    assert js.count(";") == 3
    assert "1b" not in js and "2c" not in js


def test_the_oracle_attribute_is_invisible_to_the_resolver() -> None:
    """`data-oracle` is the bench's ground truth. If it ever became something `describe()` captures or
    `snapshot()` exposes, it would be a locator HINT and every number here would be inflated. `describe`
    reads only data-testid / id / placeholder / role / accessible-name / text / css path."""
    src = Path("src/ultracua")
    for name in ("locators.py", "snapshot.py"):
        assert "data-oracle" not in (src / name).read_text(encoding="utf-8"), \
            f"{name} now references data-oracle — the bench's oracle has become a locator hint"


def test_no_fixture_copy_trips_the_interstitial_detector() -> None:
    """`flow.py` short-circuits the whole recovery ladder to `mode="escalate"` on a page that looks like a
    bot wall. A fixture containing e.g. "rate limit" would produce rows that never reach the resolver, scored
    as drifts for an unrelated reason."""
    assert interstitial_offenders(all_fixture_texts()) == []


def test_a_handler_stripping_mutation_would_be_detected_not_laundered() -> None:
    """The act trail depends on the fixtures' own handlers. If a mutation stripped one the row would log
    less and land in the SAFE `drifted` bucket — silently. The census reports the target's `on*` attributes
    so that degradation is observable; this pins the fact that the fixtures HAVE such handlers to observe."""
    span = next(s for s in SCENARIOS if s["name"] == "span-link")
    from benchmarks.drift_fixtures import V1_SPAN_PAGE
    assert "onclick" in V1_SPAN_PAGE
    assert span["target_sel"] == '[data-oracle="go"]'


def test_every_scenario_declares_a_golden_trail_matching_its_steps() -> None:
    """The oracle plan is derived from `golden`, so a fixture edit that changes what the flow does must be
    caught. `plan_for` asserts the arity; this checks the invariant holds for every shipped scenario."""
    from benchmarks.oracle_provider import plan_for

    for sc in SCENARIOS:
        plan = plan_for(sc)
        acts = [s for s in sc["steps"] if s["action"] not in ("done", "give_up")]
        assert len(plan) == len(acts)
        assert sc["golden"][-1] == "DONE", f"{sc['name']}: the trail must end at the goal marker"


# ============================ the settled trail read (a CI-only flake, pinned) ============================
# The bench once read the act trail immediately in `finalize`. A click that navigates does so
# asynchronously and the destination page appends its marker on ITS load, so an immediate read could catch
# the flow mid-navigation: the row then showed `success=True` with the goal marker absent, which scores
# `wrong` — a FABRICATED wrong-bind. It passed locally and failed on GitHub's Windows runner. These pin the
# contract browser-free so the class cannot return silently.

class _FakePage:
    """Serves a scripted sequence of trail reads; `url` models where the browser has COMMITTED to."""

    def __init__(self, reads: list, url: str = "http://x/shop") -> None:
        self.reads = list(reads)
        self.url = url
        self.calls = 0
        self.waited = False

    async def wait_for_load_state(self, *_a, **_k) -> None:
        self.waited = True

    async def evaluate(self, *_a, **_k) -> list:
        self.calls += 1
        return self.reads[min(self.calls - 1, len(self.reads) - 1)]


async def test_a_trail_already_at_a_terminal_page_is_read_once() -> None:
    """The common success path must pay nothing: a terminal marker means nothing further can arrive."""
    from benchmarks.drift_bench import _settled_trail

    page = _FakePage([["go", "DONE"]], url="http://x/done")
    assert await _settled_trail(page) == ["go", "DONE"]
    assert page.calls == 1
    assert page.waited, "the load-state wait is the PRIMARY signal and must always be awaited"


async def test_a_committed_navigation_is_awaited_even_while_the_trail_looks_static() -> None:
    """THE ACTUAL CI FAILURE, and the hole the first fix still had. The browser has committed to /done but
    the destination has not yet appended its marker, so the trail is UNCHANGED across reads. Quiescence
    alone therefore concludes "settled" and returns a mid-navigation trail, which scores the row `wrong` —
    a fabricated wrong-bind. The URL is what disambiguates "nothing yet" from "nothing more"."""
    from benchmarks.drift_bench import _settled_trail

    page = _FakePage([["go"], ["go"], ["go", "DONE"]], url="http://x/done")
    assert await _settled_trail(page, quiet_ms=1) == ["go", "DONE"], \
        "returned a mid-navigation trail — this is the fabricated-wrong-bind bug"


async def test_a_genuinely_static_trail_returns_without_burning_the_cap() -> None:
    """A fail-loud row never navigated, so its URL is not terminal and its trail is static — it must settle
    immediately. Otherwise every drifted row pays for the success path's synchronisation, which is exactly
    what made v1's fixed `wait_for_url` the wrong shape (it cost ~12 s of v1's 27 s)."""
    from benchmarks.drift_bench import _settled_trail

    page = _FakePage([["qty=3"]], url="http://x/shop")
    assert await _settled_trail(page, cap_ms=5000, quiet_ms=1) == ["qty=3"]
    assert page.calls <= 3, f"polled {page.calls} times for a static trail"


async def test_a_wrong_page_landing_is_also_terminal() -> None:
    """A decoy's destination marks itself too, so a wrong-bind is detected without waiting on the cap."""
    from benchmarks.drift_bench import _settled_trail

    page = _FakePage([["decoy"], ["decoy", "WRONG-PAGE"]], url="http://x/wrong")
    assert await _settled_trail(page, quiet_ms=1) == ["decoy", "WRONG-PAGE"]


async def test_the_bench_does_not_override_the_engines_action_timeout() -> None:
    """An earlier version forced 1500 ms, which turns a slow-but-successful bind into a fail-loud row — so
    the measured "resilience" partly measured the machine, and GitHub's Windows runner drifted 1-2 extra
    rows per scenario. The bench must run at whatever timeout the engine is configured with."""
    import benchmarks.drift_bench as db
    from ultracua.config import settings

    assert not hasattr(db, "BENCH_ACTION_TIMEOUT_MS"), \
        "the bench reintroduced a hardcoded action timeout — that biases the rate by machine speed"
    src = Path("benchmarks/drift_bench.py").read_text(encoding="utf-8")
    assert "object.__setattr__(settings" not in src, "the bench is mutating frozen settings again"
    assert settings.action_timeout_ms > 0


def test_every_anchor_name_is_known() -> None:
    for p in (*PRIMITIVES, *SEMANTIC):
        for a in (*p.kills, *p.points_elsewhere):
            assert a in ANCHORS
    assert set(PRIMITIVES_BY_NAME) == {p.name for p in PRIMITIVES}


def test_every_published_wrong_bind_names_a_row_that_exists_and_vice_versa() -> None:
    """`KNOWN_WRONG_BINDS` is the allowlist that keeps inviolable #2 honest, and it is hand-typed at one
    end and hand-typed at the other. Both directions rot, differently:

      * an entry naming a row that no longer exists is a SILENCER with nothing to silence — and worse,
        it stays green while the hole it documents may have been closed, so nobody deletes it;
      * a curated row declaring `expected: "wrong"` and NOT listed would fail `no_unexpected_wrong` on a
        187-row browser run, minutes into a shard, where this fails offline in milliseconds.

    Derived from the two structures rather than restated, so adding a curated row or an allowlist entry
    cannot leave the pair inconsistent. Scoped to CURATED rows: a generated composition's outcome is a
    measurement, not a declaration, so it has no `expected: "wrong"` to compare against.
    """
    from benchmarks import drift_bench as db

    curated = {(sc["name"], cr["name"]): cr
               for sc in db.SCENARIOS for cr in (sc.get("curated_rows") or ())}
    listed = set(db.KNOWN_WRONG_BINDS)

    # Every allowlist entry must name a row that exists. `anchor-link`'s entry is the historical one and
    # is a curated row too, so the whole set is checkable without an exception.
    orphans = sorted(f"{s}/{n}" for s, n in listed if (s, n) not in curated)
    assert not orphans, (
        f"KNOWN_WRONG_BINDS names rows that no longer exist: {orphans}. An allowlist entry with no row "
        f"silences nothing and hides whether its hole is still open — delete it, or restore the row."
    )

    # And every curated row that DECLARES a wrong bind must be listed, or the bench goes red on a
    # browser run instead of here.
    declared = sorted(f"{s}/{n}" for (s, n), cr in curated.items()
                      if cr.get("expected") == "wrong" and (s, n) not in listed)
    assert not declared, (
        f"these curated rows declare `expected: 'wrong'` and are not in KNOWN_WRONG_BINDS: {declared}. "
        f"A published hole is honest; an unpublished one fails `no_unexpected_wrong` minutes into a "
        f"browser shard."
    )

    # ANTI-VACUITY. Both assertions above are satisfied by an empty corpus, and the slice that added
    # D2's and D3's rows is exactly when this cell must have something to check.
    #
    # THE FLOOR CAME DOWN 3 -> 2 AT 0.178.0, AND THE DIRECTION IS THE WHOLE POINT. D2's entry
    # (`fuzzy-decoy/fuzzy-decoy-wins`) was DELETED because the hole was CLOSED (R4.156) -- the row
    # still exists and now declares `expected: "drifted"`, so the pair-wise assertions above still
    # adjudicate it, in the other direction. A floor that refused to move would have made closing a
    # decision fail this cell, which is the opposite of what it is for; a floor that moved without
    # the row being re-declared would be a silenced hole. Both are checked above, so this number only
    # has to stop the SET going empty. It may drop to 1 when D3 closes and no lower: the token-less
    # positional retarget is R4.35's published residual with four remedies already refused by
    # measurement, so nothing is expected to remove it.
    assert len(listed) >= 2, (
        f"only {len(listed)} published wrong binds; the corpus should carry at least the token-less "
        f"positional retarget plus D3's residual (R4.150). D2's was closed at 0.178.0 (R4.156)."
    )


def test_the_committed_baseline_publishes_the_holes_the_tree_still_has() -> None:
    """THE THIRD LEG (R4.159). The cell above joins `KNOWN_WRONG_BINDS` to the curated ROWS, both ways.
    Nothing joined it to the committed RECORD — and that is the end that went stale.

    `baselines/drift_v2.json` was last written at 0.174.0. D2 landed at 0.178.0, deleted
    `fuzzy-decoy/fuzzy-decoy-wins` from the constant and took `silent_wrong` 6 -> 4; the record kept
    saying 6, and listed a hole the resolver no longer has, for two releases. **`baselines/README.md`
    described the world correctly the whole time** — so prose and artifact disagreed with the ARTIFACT
    wrong, which is the inverse of this register's usual rot (R4.113) and reads far worse: a reader who
    distrusts the prose and opens the data gets the older answer.

    WHY THE CURRENCY GUARD COULD NOT SEE IT, because it is not a hole in that cell.
    `test_the_baseline_is_current` compares `corpus_hash`, `corpus_size`, `mutator_version`,
    `fixtures_version` and `action_timeout_ms` — WHICH POPULATION the record describes. D2 changed the
    RESOLVER and not the corpus, so all five were byte-identical and the cell was green and right about
    what it asserts. A baseline has two halves and only one was checked.

    SO THIS CHECKS THE OTHER HALF AT THE ONE PLACE IT IS CHEAP. Not the rates — re-deriving those needs
    the 187-row browser run, which is where they already live. The allowlist is the field a slice EDITS
    when it closes a hole, so it is the field that dates the record, and comparing it costs a file read.
    Asserted as a SET both ways: an entry in the record and not the tree means the record is stale (the
    measured case), and the reverse means a hole was published without re-recording the evidence for it.
    """
    import json

    from benchmarks import drift_bench as db

    rec = json.loads(Path("baselines/drift_v2.json").read_text(encoding="utf-8"))
    recorded = {(s, n) for s, n in rec["known_wrong_binds"]}
    live = set(db.KNOWN_WRONG_BINDS)

    assert recorded == live, (
        f"baselines/drift_v2.json publishes {sorted('/'.join(x) for x in recorded)} and the tree's "
        f"KNOWN_WRONG_BINDS holds {sorted('/'.join(x) for x in live)}. The record is describing a "
        f"different resolver from the one in this tree, so every rate in it — `silent_wrong`, the "
        f"heal denominator, `known_wrong_rate` — is about a world that no longer exists. Re-record it "
        f"deliberately (`python -m benchmarks.drift_bench --json baselines/drift_v2.json`) and say in "
        f"`baselines/README.md` what moved; do NOT edit the JSON by hand to make this pass."
    )

    # ANTI-VACUITY, for the same reason the cell above carries one: two empty sets are equal, and an
    # empty allowlist is exactly the state a mis-edit produces.
    assert live, "KNOWN_WRONG_BINDS is empty, so the comparison above asserted nothing"
