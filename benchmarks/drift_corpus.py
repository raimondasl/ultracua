"""drift-bench v2, part 1 of 4 — the MUTATION MODEL, as pure Python.

No browser, no engine import, no I/O. Everything here is a falsifiable model of `locators.resolve()`'s
decision surface, so it can be unit-tested in milliseconds (`tests/test_drift_corpus.py`) independently of
the harness that runs it. If the model and the browser disagree, that disagreement is the finding — the
bench reports it as a `predicted_agreement` mismatch rather than hiding it.

WHY A MODEL AT ALL. drift-bench v1 scored 12/12 on twelve hand-written cosmetic drifts. That number cannot
move and cannot teach: every drift binds at 0-LLM, so heal is never consulted (measured — see
`drift_sandbox._recovery_note`), and nothing says WHICH anchor carried each bind. v2 replaces "a list of
drifts someone thought of" with an axis:

    INTENSITY k = the number of the target's resolution ANCHORS that a mutation destroys.

That axis is principled *with respect to the resolver*: `resolve()` binds through an ordered set of
independent anchors, so the honest measure of a drift's severity is how many of them it removes. At low k a
confident anchor survives and replay binds 0-LLM. As k rises the confident anchors run out, then the
structural css, then the neighbour anchor — and at the top the target is still PRESENT and semantically
CORRECT while every locator for it is gone. That last band is the heal-eligible population v1 never had.

WHAT k IS NOT — and this is the honest limit that must travel with every number derived from it: k has NO
empirical prior. Nothing here claims real redesigns destroy k anchors with any particular frequency. The
curve answers "how many anchors must an adversary destroy before we break", i.e. a robustness score. It does
NOT answer "how likely is a real redesign to break us". That is why no area-under-curve scalar is gated: an
equal-weighted average over an axis with no prior would silently encode this sampler's weighting as a fact.

KILL SETS ARE CALIBRATED, NOT ASSERTED. Each primitive DECLARES which anchors it should destroy, but the
bench measures what it actually destroyed on the real fixture (run each primitive alone, read the
`bound_by` label `resolve()` now reports) and ships that table in the record. A primitive that fails to
break what it claims would otherwise make its rows accidentally trivial and inflate the survival rate
invisibly — the exact self-deception this file exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Optional

# Bump when a primitive's JS or the composition rules change, so a stale baseline fails loud instead of
# comparing rates computed over different mutations.
MUTATOR_VERSION = 1

# ---------------------------------------------------------------------------------------------------
# The anchors, in `resolve()`'s real order (locators.py:216-341). Names match the `bound_by` labels the
# resolver now emits, so the model and the measurement speak the same vocabulary.
#
#   Tier 1 (confident, identity-anchored — first UNIQUE match wins outright):
#     testid, role+name (exact), role+name~ (inexact/substring name), placeholder, exact-text, elem_id
#   Tier 2 (two independent guesses, cross-checked against each other):
#     css (the recorded path) and substring (the cached text, scoped to the element's own tag)
#   Tier 3 (last-resort tiebreaker, only NARROWS an ambiguous role+name — never overrides):
#     anchor (the neighbouring landmark)
# ---------------------------------------------------------------------------------------------------
TIER1 = ("testid", "role+name", "role+name~", "placeholder", "exact-text", "elem_id")
TIER2 = ("css", "substring")
TIER3 = ("anchor",)
ANCHORS = TIER1 + TIER2 + TIER3


@dataclass(frozen=True)
class Primitive:
    """One atomic, realistic page change, defined by the anchors it destroys.

    `kills` is the DECLARED set — a hypothesis. `drift_bench`'s calibration pass measures the real one on
    each fixture and the record ships both. `points_elsewhere` marks a primitive that destroys nothing but
    makes a surviving anchor resolve to a DIFFERENT element (a decoy), which is how the Tier-2 conflict —
    the only case where the safe answer is to fail loud rather than bind — gets manufactured on purpose.
    """

    name: str
    models: str                     # the real-world event this stands in for
    kills: frozenset               # declared anchor kills
    js: str                         # a JS expression: (t) => void, where `t` is the target element
    points_elsewhere: tuple = ()   # anchors that now resolve to a decoy instead of the target
    note: str = ""


def _p(name: str, models: str, kills: tuple, js: str, points_elsewhere: tuple = (), note: str = "") -> Primitive:
    for a in (*kills, *points_elsewhere):
        assert a in ANCHORS, f"{name}: unknown anchor {a!r}"
    return Primitive(name=name, models=models, kills=frozenset(kills), js=js,
                     points_elsewhere=points_elsewhere, note=note)


# `t` is the target element; `D` is a helper the fixture provides for finding a decoy sibling.
PRIMITIVES: tuple = (
    _p("testid_churn", "a prod build re-hashes its test ids", ("testid",),
       "t.setAttribute('data-testid', 'tid-' + Math.abs(0x9e37|0).toString(16))",
       note="deterministic: no RNG in the page, the value is a constant expression"),
    _p("testid_drop", "test ids stripped from the production bundle", ("testid",),
       "t.removeAttribute('data-testid')"),
    _p("hash_ids", "React useId / CSS-modules build hash rewrites the id",
       ("elem_id", "css"),
       "t.id = 'r_1a2b3c'; const s = t.closest('section'); if (s) s.id = 'r_9f8e7d';",
       note="kills TWO anchors with one change ON AN ID-BEARING TARGET: `cssPath` returns `#<id>` and stops "
            "(locators.py:80), so the recorded path IS the id — measured: `#go` for an element with id, "
            "`html > body > section > button:nth-of-type(2)` for one without. Corollary, and the reason "
            "calibration is not optional: on an id-bearing target `wrap`/`reparent`/`sibling_removed` kill "
            "NOTHING (the path is not positional), while on an id-less target they kill css and `hash_ids` "
            "only churns a non-existent anchor. The same primitive has a different kill set per fixture."),
    # The two renames are LABEL-aware, not textContent-aware, and that distinction is a measured
    # correction rather than a refinement: a `<select>`'s textContent IS its `<option>` list, so
    # `t.textContent='Zzz'` deleted every option and left a control that could not be operated at all.
    # The row then looked heal-eligible while actually being semantically broken — a mutation must change
    # how the target is FOUND, never what it IS. For a form control the accessible name comes from its
    # `<label>`/`aria-label`, so that is what a rename touches.
    # Both renames must touch `aria-label` WHENEVER it is present, not only on form controls: an
    # aria-label OVERRIDES text content in the accessible-name computation, so rewriting textContent on an
    # aria-labelled element changes nothing the resolver uses. Measured — `cart-row/rename_full+wrap`
    # predicted `drifted` and actually survived via role+name for exactly this reason.
    _p("rename_full", "an i18n pass or a copy change replaces the label",
       ("exact-text", "role+name", "role+name~", "substring", "anchor"),
       "if (t.hasAttribute('aria-label')) t.setAttribute('aria-label','Zzz');"
       "if (/^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName)) {"
       "  var l = t.id && document.querySelector('label[for=\"'+t.id+'\"]');"
       "  if (!l) l = t.closest('label');"
       "  if (l) l.textContent = 'Zzz';"
       "} else { t.textContent = 'Zzz'; }",
       note="also kills the neighbour anchor: it re-queries get_by_role(role, name, exact=True) inside the "
            "landmark, so a dead accessible name is dead there too (locators.py:275)"),
    _p("rename_augment", "a label gains a suffix: 'Proceed' -> 'Proceed now'",
       ("exact-text", "role+name"),
       "if (t.hasAttribute('aria-label')) "
       "  t.setAttribute('aria-label', t.getAttribute('aria-label').trim()+' now');"
       "if (/^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName)) {"
       "  var l2 = t.id && document.querySelector('label[for=\"'+t.id+'\"]');"
       "  if (!l2) l2 = t.closest('label');"
       "  if (l2) l2.textContent = l2.textContent.trim()+' now';"
       "} else { t.textContent = t.textContent.trim() + ' now'; }",
       note="role+name~ (exact=False) and the tag-scoped substring both still match"),
    _p("role_swap", "a component migration turns a semantic control into a generic element",
       ("role+name", "role+name~", "anchor"),
       "t.setAttribute('role', 'note');",
       note="`role=note` is honoured and is NOT in KNOWN_ROLES, so role+name and the neighbour anchor that "
            "scopes it both stop working. Deliberately NOT `role=presentation`: ARIA's presentational-role "
            "conflict resolution IGNORES presentation/none on a FOCUSABLE element, so on a link or button "
            "it is a no-op — measured, as a prediction mismatch where the row survived via role+name."),
    _p("aria_hide", "an a11y regression hides the control from the a11y tree",
       ("role+name", "role+name~", "anchor"),
       "t.setAttribute('aria-hidden', 'true');"),
    _p("strip_aria_label", "an i18n or a11y pass flattens per-item labels to one shared string",
       ("role+name", "role+name~", "exact-text", "substring"),
       "t.removeAttribute('aria-label');",
       note="On a list of otherwise-identical controls this does not merely change the name, it makes it "
            "AMBIGUOUS across every row. `resolve(unique=True)` then refuses every Tier-1 candidate "
            "(count != 1, never a blind `.first`) and the row's outcome rests entirely on the neighbour "
            "anchor narrowing to the right `<tr>`. Declared as killing role+name because the CACHED name no "
            "longer uniquely identifies the target; calibration records what actually happened."),
    _p("placeholder_rewrite", "placeholder copy rewritten", ("placeholder",),
       "if (t.hasAttribute('placeholder')) t.setAttribute('placeholder', 'zzz');"),
    _p("wrap", "an A/B-test or analytics wrapper div appears around the control", ("css",),
       "const w = document.createElement('div'); w.className='ab-wrap'; "
       "t.parentNode.insertBefore(w, t); w.appendChild(t);"),
    _p("reparent", "layout churn moves the control into a different container", ("css",),
       "const h = document.createElement('div'); h.className='moved'; "
       "t.closest('section,main,form,body').appendChild(h); h.appendChild(t);"),
    _p("sibling_removed", "a neighbouring control is removed, shifting nth-of-type", ("css",),
       "const p = t.parentElement; const sibs = Array.from(p.children)"
       ".filter(x => x !== t && x.tagName === t.tagName); if (sibs.length) sibs[0].remove();",
       note="v1 only ever INSERTS siblings; removal shifts the positional path the other way"),
    _p("heading_to_div", "the design system stops emitting semantic headings", ("anchor",),
       "const s = t.closest('section,main,form'); const h = s && s.querySelector('h1,h2,h3,legend,caption'); "
       "if (h) { const d = document.createElement('div'); d.textContent = h.textContent; "
       "h.replaceWith(d); }"),
    _p("decoy_substring", "an A/B variant adds a same-tag control sharing the cached label", (),
       "const d = t.cloneNode(true); d.textContent = t.textContent.trim() + ' anyway'; "
       "d.removeAttribute('id'); d.removeAttribute('data-testid'); "
       "d.removeAttribute('aria-label'); d.removeAttribute('aria-labelledby'); "
       "d.removeAttribute('placeholder'); d.setAttribute('data-oracle','decoy'); "
       "if (d.hasAttribute('href')) d.setAttribute('href','/wrong'); t.parentNode.appendChild(d);",
       points_elsewhere=("substring",),
       note="destroys nothing; makes the Tier-2 substring guess able to land on a DECOY, which is how the "
            "cross-check's fail-loud path gets exercised. The clone must be stripped of every IDENTITY "
            "attribute, not just id/testid: `cloneNode(true)` copies `aria-label`, and an aria-label "
            "OVERRIDES text content in the accessible name — so the decoy inherited the target's EXACT "
            "cached name and `get_by_role(name, exact=True)` bound it as a confident Tier-1 match. That "
            "produced this bench's first `silent_wrong`, and it was the FIXTURE's fault: the decoy was a "
            "perfect impostor rather than a substring look-alike, and any locator system would have bound "
            "the element carrying the recorded identity. A decoy must share a SUBSTRING, never an identity."),
)

PRIMITIVES_BY_NAME = {p.name: p for p in PRIMITIVES}

# Semantic primitives: the target genuinely goes away. The ONLY correct outcome is a loud failure — these
# are never part of a resilience rate, they are an invariant. Kept separate from the composition pool so a
# sampler can never accidentally mix "gone" into a "still present" row and call the fail-loud a miss.
SEMANTIC = (
    _p("target_removed", "the control is deleted in a redesign", tuple(ANCHORS), "t.remove();"),
    _p("target_replaced_crosstag", "the control is replaced by a different KIND of element carrying its "
       "exact label (the cross-tag exact-text trap)", tuple(ANCHORS),
       "const a = document.createElement('p'); a.textContent = t.textContent; "
       "a.setAttribute('data-oracle','decoy'); t.replaceWith(a);"),
)
SEMANTIC_BY_NAME = {p.name: p for p in SEMANTIC}


# ---------------------------------------------------------------------------------------------------
# The residual-anchor predictor: a faithful simulation of `resolve(unique=True)`.
# ---------------------------------------------------------------------------------------------------
@dataclass
class BindState:
    """What each anchor can still do, AFTER a composition is applied.

    Modelled per anchor rather than as a flat "alive" set because `resolve()`'s Tier-2 outcome depends not
    just on whether the two guesses resolve, but on WHETHER THEY AGREE — a distinction a kill-set alone
    cannot express, and the distinction that decides fail-loud vs bind.
    """

    alive: set = field(default_factory=set)      # anchors that still uniquely identify the TARGET
    decoy: set = field(default_factory=set)      # anchors that now uniquely identify a DIFFERENT element
    ambiguous: set = field(default_factory=set)  # anchors matching >1 element (Tier-1 -> skipped when unique=True)


def predict(state: BindState, *, pristine: frozenset = frozenset()) -> tuple[str, str]:
    """-> (expected_outcome, expected_bound_by), simulating `resolve(unique=True)` exactly.

    Mirrors `locators.resolve` step for step. Kept as a straight-line transcription rather than something
    cleverer so a reader can diff it against the resolver by eye.

    `pristine` is the target's ORIGINAL anchor set, needed only for the testid-contradiction branch: the
    rule keys off "a testid was recorded and is no longer alive", which the post-mutation `state` alone
    cannot express (a target that never had one looks identical to one whose token died).
    """
    # Tier 1 — first UNIQUE confident match wins. `unique=True` means an ambiguous candidate is skipped
    # entirely (never a blind `.first`), and a DECOY-pointing Tier-1 anchor is a wrong-bind, not a survival.
    for a in TIER1:
        if a in state.alive:
            return "survived", a
        if a in state.decoy:
            return "wrong", a

    # Tier 2 — the two guesses, cross-checked. css is structural so a unique css match is trusted UNLESS
    # the substring guess uniquely contradicts it; a LONE substring match is never trusted.
    css_target = "css" in state.alive
    css_decoy = "css" in state.decoy
    sub_target = "substring" in state.alive
    sub_decoy = "substring" in state.decoy

    # A recorded testid that is no longer alive is FALSIFIED by whatever the css path bound, so the
    # css-trust — which is a RENAME hypothesis, and a rename cannot change a developer token — is
    # withdrawn. Mirrors `locators._testid_contradicted`.
    identity_contradicted = "testid" in pristine and "testid" not in state.alive

    if css_target and not sub_decoy:
        if identity_contradicted:
            return "drifted", "none"
        return "survived", "css"                 # css unique, substring absent/agreeing
    if css_target and sub_decoy:
        return "drifted", "none"                 # they disagree -> neither trusted -> fail loud
    if css_decoy and not (sub_target or sub_decoy):
        if identity_contradicted:
            return "drifted", "none"
        # THE RESIDUAL HOLE: a TOKEN-LESS positional retarget. Nothing `describe()` records can tell this
        # apart from a legitimately renamed target, so it stays a silent wrong-bind — published, counted,
        # and pinned by its own corpus row rather than assumed closed.
        return "wrong", "css"
    if css_decoy and sub_target:
        return "drifted", "none"                 # disagree the other way -> fail loud
    if sub_target or sub_decoy:
        return "drifted", "none"                 # a lone substring is never trusted (unique=True)

    # Tier 3 — the neighbour anchor only NARROWS an ambiguous role+name; it cannot resurrect a dead one.
    if "anchor" in state.alive and ("role+name" in state.ambiguous or "role+name" in state.alive):
        return "survived", "anchor"
    return "drifted", "none"


def kills_on(primitive: str, pristine: set) -> frozenset:
    """A primitive's kill set ON THIS TARGET SHAPE.

    Two primitives are fixture-conditional, and the condition is a direct consequence of `cssPath`
    (locators.py:80): it returns `#<id>` and STOPS for any element bearing an id. Measured — `#go` for an
    id-bearing element, `html > body > section > button:nth-of-type(2)` for one without. Therefore:

      * an ID-BEARING target has no independent css anchor (its recorded path IS its id), so `hash_ids`
        kills both at once while a wrapper / reparent / sibling removal kills NOTHING — the path is not
        positional and does not move;
      * an ID-LESS target has a genuinely positional css that a wrapper / reparent / sibling removal DOES
        break, while `hash_ids` merely ADDS an id nobody recorded and kills nothing.

    Getting this wrong is not cosmetic: it mis-states `k` and therefore the whole intensity axis. It showed
    up as a prediction mismatch (`hash_ids+...` on the id-less ladder target predicted `drifted` and
    survived via css) before the rule was applied.
    """
    p = PRIMITIVES_BY_NAME.get(primitive) or SEMANTIC_BY_NAME[primitive]
    has_id = "elem_id" in pristine
    if primitive == "hash_ids":
        return frozenset(("elem_id", "css")) if has_id else frozenset()
    if primitive in ("wrap", "reparent", "sibling_removed"):
        return frozenset() if has_id else frozenset(("css",))
    return p.kills


def apply_composition(pristine: set, primitives: tuple) -> BindState:
    """Fold a composition of primitives over the target's pristine anchor set -> the predicted BindState."""
    state = BindState(alive=set(pristine))
    for name in primitives:
        p = PRIMITIVES_BY_NAME.get(name) or SEMANTIC_BY_NAME[name]
        k = kills_on(name, pristine)
        state.alive -= k
        state.decoy -= k
        for a in p.points_elsewhere:
            # The anchor still resolves, but now to a decoy. Only meaningful while it is alive: a primitive
            # that already destroyed it cannot make it point anywhere.
            if a in state.alive:
                state.alive.discard(a)
                state.decoy.add(a)
    return state


def killed_anchors(pristine: set, primitives: tuple) -> list:
    """The anchors a composition destroys, out of those the target actually HAD. This is `k`'s definition,
    so it is derived from the composition — never chosen by hand."""
    st = apply_composition(pristine, primitives)
    return sorted(pristine - st.alive - st.decoy)


def intensity(pristine: set, primitives: tuple) -> int:
    return len(killed_anchors(pristine, primitives))


# ---------------------------------------------------------------------------------------------------
# The seeded sampler
# ---------------------------------------------------------------------------------------------------
def compose(pristine: set, seed: int, per_k: int = 3, k_max: int = 8,
            pool: Optional[tuple] = None, max_len: int = 5) -> list:
    """Sample `per_k` distinct compositions for each k in 1..k_max, deterministically from `seed`.

    Enumerates every combination up to `max_len` primitives, buckets by measured k, then samples inside
    each bucket. Enumeration (rather than rejection sampling) means a k with no achievable composition is
    reported as EMPTY instead of silently spinning or being quietly skipped — the bench then knows the
    ladder does not reach that rung on this fixture, which is itself a fact worth publishing.

    `k_max=8` because a target carrying every anchor has eight of them, and the heal-eligible band is
    exactly where ALL of them are destroyed while the element remains present and correct. A ladder
    stopping at 7 would leave one anchor alive on every rung and reproduce v1's starved condition.

    Sampling prefers the SHORTEST compositions achieving each k — fewer moving parts per unit of severity,
    so a surprising outcome is attributable to a specific primitive instead of a pile of them.
    """
    from itertools import combinations

    pool = pool or tuple(p.name for p in PRIMITIVES)
    buckets: dict = {}
    for n in range(1, max_len + 1):
        for combo in combinations(pool, n):
            k = intensity(pristine, combo)
            if 1 <= k <= k_max:
                buckets.setdefault(k, []).append(tuple(sorted(combo)))
    rng = random.Random(seed)
    out: list = []
    for k in range(1, k_max + 1):
        cand = sorted(set(buckets.get(k, ())))          # sorted -> the seed alone fixes the draw
        if not cand:
            continue
        # Fill from the shortest length upward, sampling WITHIN each length band. (Sampling the whole
        # sorted list would ignore the length preference entirely.)
        take: list = []
        for n in sorted({len(c) for c in cand}):
            band = [c for c in cand if len(c) == n]
            need = per_k - len(take)
            if need <= 0:
                break
            take.extend(band if len(band) <= need else rng.sample(band, need))
        out.extend(sorted(take))
    return out


def corpus_hash(rows: list) -> str:
    """A digest of WHAT was measured — `(scenario, step_index, primitive-tuple)` only.

    Deliberately does NOT hash the fixture HTML: a typo fix or a comment in a page must not invalidate a
    committed baseline. It DOES close v1's shrink hole — v1 never compared `cosmetic_total`, so deleting
    inconvenient drifts kept `resilience_0llm` at 1.0 and passed the gate.
    """
    basis = sorted((str(r["scenario"]), int(r.get("step_index", 0)), tuple(r["primitives"])) for r in rows)
    blob = json.dumps([[s, i, list(p)] for s, i, p in basis], separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------------
# Copy lint: no mutation may accidentally trip the interstitial detector
# ---------------------------------------------------------------------------------------------------
def interstitial_offenders(texts: list) -> list:
    """Any text that would make `safety.looks_like_interstitial` fire.

    `flow.py` short-circuits the ENTIRE recovery ladder to `mode="escalate"` when a page looks like a
    CAPTCHA wall. A mutation whose copy happened to contain "rate limit" would therefore produce a row
    that never reaches the resolver at all, scored as a drift for a completely unrelated reason. Linted at
    corpus-build time so it cannot happen by accident — the one row that escalates does so on purpose.
    """
    from ultracua.safety import INTERSTITIAL_SIGNALS

    bad = []
    for t in texts:
        low = (t or "").lower()
        hit = [s for s in INTERSTITIAL_SIGNALS if s in low]
        if hit:
            bad.append({"text": t, "signals": hit})
    return bad
