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


def test_every_anchor_name_is_known() -> None:
    for p in (*PRIMITIVES, *SEMANTIC):
        for a in (*p.kills, *p.points_elsewhere):
            assert a in ANCHORS
    assert set(PRIMITIVES_BY_NAME) == {p.name for p in PRIMITIVES}
