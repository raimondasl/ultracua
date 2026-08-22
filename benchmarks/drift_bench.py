"""drift-bench v2, part 4 of 4 — the harness.

    uv run python -m benchmarks.drift_bench                                  # key-less, both arms
    uv run python -m benchmarks.drift_bench --json out.json --baseline baselines/drift_v2.json
    uv run python -m benchmarks.drift_bench --scenario anchor-button --seed 11

TWO NUMBERS, chosen so that neither can be gamed without the other noticing.

1. `silent_wrong` MUST BE 0. Across every row, the count of runs where the system actuated a different
   element, typed a different value, wrote at the wrong target, double-fired or silently suppressed a write,
   or reported success on an action sequence that was not the learned one. Judged from the GOLDEN TRAIL — the
   exact ordered (element-identity, value) sequence actually actuated — not from the landed URL. This is
   strictly stronger than v1's `wrong_binds`: v1 could only see a mis-bind that navigated somewhere wrong, so
   typing into a decoy field or choosing a wrong option scored as the SAFE `drifted` bucket.

2. THE RECOVERY LADDER GETS A RATE. v1's `--provider` arm could not produce one — and not because the
   number was small: 0 of its 12 cosmetic drifts ever consult heal (measured; see
   `drift_sandbox._recovery_note`), so there was no heal-eligible population at all. v2 engineers one, by
   composing mutations that destroy EVERY locator anchor while leaving the target present and semantically
   correct. Each recovery tier is then reported separately: `heal_attempted / heal_declined / heal_persisted
   / replan_attempted / replan_recovered`.

HOW THE RATE MUST BE READ, and this is not a caveat but the definition. The key-less arm answers with
`OracleProvider`, a perfect-vision model at accuracy 1.0 (part 3). So it measures the MECHANISM: does a heal
persist, is a no-effect heal rejected, does the splice preserve the working prefix, is the flow re-cached,
does the approval digest move. It is a CEILING, named `mechanism_*` everywhere in the record and never
`heal_rate`. `real recovery = mechanism ceiling x model accuracy`, and only the paid `--provider anthropic`
arm speaks to the second factor.

WHAT v1 CONTRIBUTES. All 17 of its drifts are ported and their outcomes are compared ELEMENT-WISE against
`baselines/drift.json`, with its 12/12 headline re-asserted as the `v1_parity` invariant. v1's guarantee is
not replaced by a differently-shaped number; it is carried inside v2 and gated exactly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

from ultracua.cache import FlowCache, flow_key, steps_hash
from ultracua.config import settings
from ultracua.flow import run_cached
from ultracua.providers import get_provider
from ultracua.providers.scripted import ScriptedProvider

from .drift_corpus import (
    MUTATOR_VERSION,
    PRIMITIVES,
    PRIMITIVES_BY_NAME,
    SEMANTIC_BY_NAME,
    apply_composition,
    compose,
    corpus_hash,
    interstitial_offenders,
    killed_anchors,
    predict,
)
from .drift_fixtures import (
    CENSUS_JS,
    FIXTURES_VERSION,
    GOAL_MARK,
    READ_ACTS_JS,
    SCENARIOS,
    SCENARIOS_BY_NAME,
    WRONG_MARK,
    FixtureServer,
    all_fixture_texts,
    mutation_js,
)
from .oracle_provider import OracleProvider, plan_for
# IMPORTED, not re-derived. The rule is one line long, which is exactly why it was written
# twice and immediately disagreed with itself — see `variance.sum_cost`.
from .variance import sum_cost

BENCH_VERSION = 1

# The bench does NOT override `settings.action_timeout_ms`, and that is a correction of a real mistake.
#
# An earlier version forced it to 1500 ms "so the rows fit a CI budget", assuming a drifted row waits out the
# full action timeout. Wrong twice over:
#
#   1. A drifted row fails at RESOLVE, which returns immediately — only a row that resolves and then fails to
#      ACTUATE pays the timeout, and those are rare. Measured: 48.7 s at 1500 ms vs 51.9 s at the 5000 ms
#      default, for byte-identical outcomes. The override bought ~3 s.
#   2. `browser.py` applies this value with `context.set_default_timeout(...)`, so it is the CONTEXT-WIDE
#      default for EVERY Playwright operation in the run, not just clicks. Lowering it tightened the whole
#      harness, which is why GitHub's Windows runner drifted 1-2 rows MORE per read scenario than the
#      development machine did: a slow-but-successful operation became a fail-loud row, so part of what the
#      "resilience" number measured was the speed of the machine measuring it.
#
# Running at the engine's own configured timeout removes that entire bias class and means the bench measures
# the engine as actually deployed. The value is still RECORDED and compared for equality by `--baseline`, so a
# rate measured under a different configuration is never silently compared against this one.

# `heal_eligible_nonempty`: the anti-rot invariant. If a resolver improvement re-saturates the ladder so
# that nothing drifts at 0-LLM any more, the bench must fail loud demanding harder rungs rather than
# quietly becoming decorative — which is precisely the state v1 was in.
MIN_HEAL_ELIGIBLE = 8

# ---------------------------------------------------------------------------------------------------
# v1's 17 drifts, ported VERBATIM as statement bodies (`t` = the target element).
# Same pages, same mutations, same `kind` labels. The only addition is `data-oracle="decoy"` on the decoy
# elements v1 created — v1 had no way to notice a bind that landed on one without navigating, which is the
# hole the golden trail closes.
# ---------------------------------------------------------------------------------------------------
V1_ANCHOR_DRIFTS = (
    ("none", "cosmetic", ""),
    ("banner-added", "cosmetic",
     "const d=document.createElement('div'); d.textContent='FLASH SALE';"
     "document.body.insertBefore(d, document.body.firstChild);"),
    ("section-id-removed", "cosmetic", "document.querySelector('#checkout').removeAttribute('id');"),
    ("target-classed", "cosmetic", "t.className='btn btn-primary pulse';"),
    ("target-wrapped", "cosmetic",
     "const s=document.createElement('span'); t.parentNode.insertBefore(s,t); s.appendChild(t);"),
    ("sibling-inserted", "cosmetic",
     "const b=document.createElement('a'); b.href='/back'; b.textContent='Back';"
     "b.setAttribute('data-oracle','decoy'); t.parentNode.insertBefore(b,t);"),
    ("section-reordered", "cosmetic", "document.body.appendChild(document.querySelector('#checkout'));"),
    ("heading-renamed", "cosmetic", "document.querySelector('#checkout h2').textContent='Payment';"),
    ("target-renamed", "cosmetic", "t.textContent='Proceed';"),
    ("ambiguous-twin", "ambiguous",
     "const a=document.createElement('a'); a.href='/wrong'; a.textContent='Continue';"
     "a.setAttribute('data-oracle','decoy'); document.querySelector('#cart').appendChild(a);"),
    ("target-removed", "semantic", "t.remove();"),
)

V1_SPAN_DRIFTS = (
    ("span-none", "cosmetic", ""),
    ("span-renamed", "cosmetic",
     "t.textContent='Next'; const p=document.createElement('p');"
     "p.textContent='Proceed only after reviewing your order.';"
     "document.querySelector('main').appendChild(p);"),
    ("span-reordered", "cosmetic",
     "const b=document.querySelector('.actions'); b.insertBefore(t, b.firstElementChild);"),
    ("span-augmented-reordered", "conflict",
     "t.textContent='Proceed now'; const b=document.querySelector('.actions');"
     "b.insertBefore(t, b.firstElementChild);"),
    ("span-sibling-decoy", "conflict",
     "t.textContent='Next'; const d=document.createElement('span'); d.className='lnk';"
     "d.setAttribute('onclick', \"location.href='/wrong'\");"
     "d.setAttribute('data-oracle','decoy'); d.textContent='Proceed anyway';"
     "document.querySelector('.actions').appendChild(d);"),
    ("span-removed-crosstag-twin", "semantic",
     "t.remove(); const a=document.createElement('a'); a.href='/wrong'; a.textContent='Proceed';"
     "a.setAttribute('data-oracle','decoy'); document.querySelector('main').appendChild(a);"),
)

# One deliberate escalate row. `flow.py` short-circuits the ENTIRE ladder to `mode="escalate"` when the page
# looks like a bot wall, and v1 buckets that as `drifted` — indistinguishable from a locator failure.
ESCALATE_ROW = ("interstitial", "escalate", "document.title='Checking your browser';")

# KNOWN, PUBLISHED wrong-binds. Compared as an EXACT set, never as a tolerance — the difference matters: a
# tolerance launders an unknown number of holes, whereas a named row with a reason keeps inviolable #2
# honest and makes closing it a visible act (delete the entry).
#
# THE POSITIONAL-CSS RETARGET: when a target whose only surviving anchor is a POSITIONAL css is removed and
# a same-tag sibling slides into its slot, the cached path re-matches the neighbour. Replay then actuates an
# element no human approved, and if it reaches a plausible page the run reports SUCCESS. (This is why the
# golden trail matters: v1 judged by landed URL and would have scored it a clean survival.)
#
# HALF OF IT IS NOW CLOSED (0.62.0). `locators._testid_contradicted` withdraws the Tier-2 css-trust when the
# bound element positively falsifies a recorded `data-testid`: that trust is a RENAME hypothesis, and a
# rename cannot change a developer token. `shop-4step/positional-css-retarget` therefore now fails loud
# (replay `drifted`; repair recovers by an alternate route) and its entry is gone from this table.
#
# THE OTHER HALF REMAINS OPEN, and it is the COMMON half: 8 of the corpus's 10 Tier-2 css binds are on
# targets that recorded no identity token at all, and for those nothing `describe()` captures separates "the
# target was renamed" from "a sibling slid into its slot" — both renames the resolver must keep recovering
# (`target-renamed`, `span-renamed`) present exactly as the retarget does. That is an information limit of
# the recorded field set, not an oversight.
#
# So the entry MOVED rather than being deleted: `anchor-link/positional-css-retarget-tokenless` is a curated
# row on a token-less target that reproduces the surviving hole. Deleting the old entry without adding it
# would have converted a MEASURED hole into an assumed-closed one — precisely the failure this bench exists
# to prevent.
#
# Ruled out with measurement, so they are not re-proposed: demoting or dropping the css candidate (turns the
# two `conflict` rows into silent wrong-binds — measured during v1); any N-of-M positive corroboration rule
# (both renames record zero corroborating tokens, so it refuses them — predicted to drop v1 parity to
# 10/12); text similarity (the renames change text exactly as the retarget does); and the neighbour anchor
# (it MATCHES on the retarget row, since the decoy is inserted into the same section).
KNOWN_WRONG_BINDS = (
    ("anchor-link", "positional-css-retarget-tokenless"),
)

# Recovered rows whose repair does NOT move the recipe digest, so `heal_invalidates_approval` cannot
# apply to them. Same discipline as `KNOWN_WRONG_BINDS`: named rows with a measured reason, never a
# loosened predicate. Deleting an entry is what closing the finding looks like.
#
# ALL THREE ARE R4.35, AND ALL THREE ARE ON THE SCENARIO ADDED TO EXPOSE R3.7 — which is how they were
# found. Measured: `heal_persisted=True`, `acts == golden == ['go','DONE']`, and `steps_hash_after ==
# steps_hash_before`. The equality is the evidence; the VALUE is deliberately not quoted here, because
# `steps_hash` folds `start_url` and the fixture server binds an ephemeral port, so the digest differs
# every run. (An earlier draft published one, which is a per-run constant masquerading as a fact.)
#
# The heal re-grounded to a spec BYTE-IDENTICAL to the one that had just been refused, because R3.7's
# refusal is not a property of the spec at all — it is a disagreement between two walks over a spec that
# is perfectly good. So there is nothing for the approval gate to re-gate: the heal did not pick
# something else, which is what the invariant exists to catch.
#
# Of this scenario's 12 heal-eligible rows, 11 persist a heal and 8 of those move the digest; one
# (`reparent`) declines. So the effect is three rows, not twelve — the rest re-ground to a genuinely
# different spec because their mutation changed the page.
KNOWN_NO_DIGEST_MOVE = (
    ("row-nested-icon", "sibling_removed"),
    ("row-nested-icon", "aria_hide"),
    ("row-nested-icon", "aria_hide+sibling_removed"),
)


# ---------------------------------------------------------------------------------------------------
# Row construction
# ---------------------------------------------------------------------------------------------------
def build_corpus(seed: int, per_k: int = 3, only: Optional[str] = None) -> list:
    """Every row to run, deterministically. A row is a dict; `js` is its composed mutation."""
    rows: list = []

    def add(scenario: str, name: str, kind: str, source: str, js: str,
            primitives: tuple = (), step_index: int = 0, expected: str = "", k: int = 0,
            killed: tuple = (), residual: tuple = ()) -> None:
        if only and scenario != only:
            return
        rows.append({"scenario": scenario, "row_id": f"{scenario}/{name}", "kind": kind, "source": source,
                     "primitives": list(primitives), "step_index": step_index, "js": js, "k": k,
                     "expected_0llm": expected, "killed_anchors": list(killed),
                     "residual_anchors": list(residual)})

    # -- v1 ports (the historical gate, carried forward) --
    for name, kind, js in V1_ANCHOR_DRIFTS:
        add("anchor-link", name, kind, "v1-port", js)
    for name, kind, js in V1_SPAN_DRIFTS:
        add("span-link", name, kind, "v1-port", js)
    add("anchor-link", ESCALATE_ROW[0], ESCALATE_ROW[1], "curated", ESCALATE_ROW[2])

    # -- calibration: every primitive ALONE on the full-anchor target, so its real kill set is measured
    #    rather than trusted. A primitive that fails to break what it claims would otherwise make its
    #    composed rows accidentally trivial and inflate the survival rate invisibly.
    for p in PRIMITIVES:
        add("anchor-button", f"calib:{p.name}", "calibration", "calibration",
            mutation_js((p.js,)), primitives=(p.name,))

    # -- the ladder + the generated compositions, per scenario --
    for sc in SCENARIOS:
        if only and sc["name"] != only:
            continue
        pristine = set(sc["pristine_anchors"])
        step_index = sc.get("target_step", 0)
        # CURATED rows first, and outside the v1-port skip: a v1 page keeps exactly its v1 drift list (so
        # `v1_parity` stays an element-wise comparison against `baselines/drift.json`), but it may still
        # carry a hand-written row of its own. `anchor-link` needs one — it is the corpus's token-less
        # positional-retarget case, and v1 has no drift of that shape. Curated rows carry `source="curated"`
        # so they can never enter the `v1_parity` block, which selects on `source == "v1-port"`.
        for cr in sc.get("curated_rows") or ():
            add(sc["name"], cr["name"], cr["kind"], "curated", mutation_js((cr["js"],)),
                primitives=(cr["name"],), step_index=step_index, expected=cr["expected"])
            if rows and rows[-1]["row_id"].endswith("/" + cr["name"]):
                rows[-1]["target_sel_override"] = cr["target_sel"]
        if sc["source"] == "v1-port":
            continue                      # v1 pages keep exactly their v1 drift list
        for combo in compose(pristine, seed=seed, per_k=per_k):
            k = len(killed_anchors(pristine, combo))
            st = apply_composition(pristine, combo)
            exp, _by = predict(st, pristine=frozenset(pristine))
            js = mutation_js(tuple((PRIMITIVES_BY_NAME[c]).js for c in combo))
            add(sc["name"], "+".join(combo), "cosmetic" if exp != "wrong" else "conflict", "generated", js,
                primitives=combo, step_index=step_index, expected=exp, k=k,
                killed=tuple(killed_anchors(pristine, combo)),
                residual=tuple(sorted(st.alive)))
        # -- semantic rows: the target genuinely goes away; a loud failure is the ONLY correct outcome --
        for sname, sp in SEMANTIC_BY_NAME.items():
            add(sc["name"], sname, "semantic", "curated", mutation_js((sp.js,)),
                primitives=(sname,), step_index=step_index, expected="drifted")
    return rows


# ---------------------------------------------------------------------------------------------------
# Outcome derivation from the golden trail
# ---------------------------------------------------------------------------------------------------
def classify(golden: list, acts: list, report, *, heal_persisted: bool, replan_attempted: bool) -> str:
    """-> survived | healed | replanned | drifted | escalated | wrong.

    The trail is the authority, but the standard it is held to DIFFERS BY TIER, and the boundary is the
    substantive judgement in this file:

      * A 0-LLM replay (tier 0) must reproduce the learned sequence EXACTLY. Nothing is supposed to have
        changed about what it does, so any divergence is a wrong outcome — this is the strict clause, and it
        is what catches a mis-bind that still lands on a plausible page.
      * A HEAL or a REPLAN is, by definition, allowed to actuate a DIFFERENT element: that is what recovery
        IS. Holding them to trail equality would score every correct heal of a REMOVED target as `wrong`
        (measured: `route-removed` healed onto a legitimate alternate route and was mislabelled). They are
        therefore judged on outcome — the goal was actually reached, and no decoy was touched.

    What guards a recovery that reaches the goal via an element no human approved? Not this bench's trail:
    the APPROVAL DIGEST. A persisted repair re-caches the flow, which moves `cache.steps_hash`, so an
    approved flow refuses on its next run with `stale_approval` (0.60.0). That is asserted here as
    `heal_invalidates_approval`, and it is the honest answer to "a heal picked something else".

    A `decoy`-marked element is never a legitimate target at any tier — the fixtures mark decoys precisely
    so that "reached the goal" can never launder "clicked the wrong thing on the way".
    """
    if report.mode == "escalate":
        return "escalated"
    if WRONG_MARK in acts or any(a == "decoy" or a.startswith("decoy=") for a in acts):
        return "wrong"
    recovered = heal_persisted or replan_attempted
    if report.success and recovered:
        if GOAL_MARK not in acts:
            return "wrong"                              # claimed a recovery that never reached the goal
        return "replanned" if replan_attempted else "healed"
    if report.success:
        return "survived" if acts == golden else "wrong"
    return "drifted" if acts == golden[:len(acts)] else "wrong"


def _tiers(report) -> dict:
    """Recovery-tier attribution from trace SPANS, not from `report.mode`/`healed_steps`.

    Neither of those can carry it: `mode` is last-writer-wins, and `healed_steps` counts heal ATTEMPTS (a
    row that healed and then still failed reports `healed_steps=1`). The spans are what actually happened.
    """
    heal_llm = heal_act = False
    heal_ok = False
    replan = False
    for t in report.traces:
        names = {s.name for s in t.spans}
        if t.meta.get("phase") == "replan":
            replan = True
        if "heal_llm" in names:
            heal_llm = True
        if "heal_act" in names:
            heal_act = True
            if t.meta.get("ok") is not False:
                heal_ok = True
    return {
        "heal_attempted": heal_llm,
        "heal_declined": heal_llm and not heal_act,
        "heal_persisted": heal_act and heal_ok,
        "replan_attempted": replan,
    }


def _bound_by(report) -> list:
    return [t.meta["bound_by"] for t in report.traces if "bound_by" in t.meta]


# A terminal page appends its own marker on load (see `drift_fixtures._ACTLOG_JS`), so a trail ending in one
# is complete by construction.
_TERMINAL = (GOAL_MARK, WRONG_MARK)


async def _settled_trail(page, cap_ms: int = 3000, quiet_ms: int = 25) -> list:
    """Read the act trail only after the page has finished reacting to the last actuation.

    WHY THIS EXISTS. Dropping v1's fixed `wait_for_url` from `finalize` — justified at the time as "the trail
    is read synchronously from sessionStorage, so no wait is needed" — removed a real synchronisation without
    replacing it. A click that navigates does so ASYNCHRONOUSLY and the destination page appends its marker on
    ITS OWN load, so an immediate read can catch the flow mid-navigation. The row then shows `success=True`
    with the goal marker absent, which scores `wrong`: a FABRICATED wrong-bind. It passed locally and failed
    on slower CI hardware — the worst possible form, because a benchmark whose headline invariant flakes is
    untrustworthy in both directions.

    WHY NOT JUST QUIESCENCE. Polling until two reads agree is unsound here, and my first fix had exactly that
    hole: while a navigation is in flight the trail is legitimately UNCHANGED, so two equal reads mean
    "nothing has happened yet", not "nothing more will happen". On a machine slower than the poll interval
    that reproduces the original bug a few milliseconds later.

    So the primary signal is Playwright's own: `wait_for_load_state("load")` resolves immediately when the
    document is already loaded and otherwise waits for the in-flight navigation to complete — and by the time
    `click()` has returned, a handler's `location.href` assignment has already initiated it. Quiescence is
    kept afterwards only as a cheap backstop for a second hop.

    Neither half biases the outcome: a trail already ending in a terminal marker returns immediately, and a
    genuinely drifted row is already loaded and static so it exits after one tick. A wait keyed on "the goal
    marker appeared" would have biased the measurement toward success by construction.
    """
    deadline = time.perf_counter() + cap_ms / 1000.0
    try:
        await page.wait_for_load_state("load", timeout=cap_ms)
    except Exception:  # noqa: BLE001 — already closed / navigating again; the loop below still bounds it
        pass
    prev = None
    while True:
        try:
            cur = await page.evaluate(READ_ACTS_JS)
        except Exception:  # noqa: BLE001 — mid-navigation the context can be torn down; retry within the cap
            cur = None
        if cur is not None:
            if cur and cur[-1] in _TERMINAL:
                return cur                      # a terminal page marked itself: nothing more can arrive
            # THE SOUND STOPPING RULE. `page.url` updates when a navigation COMMITS, so a terminal URL whose
            # marker has not yet been appended means the destination document is still initialising — the
            # trail is incomplete and quiescence must NOT be trusted. This is the case the first version of
            # this function got wrong: while a navigation is in flight the trail is legitimately UNCHANGED,
            # so two equal reads meant "nothing has happened yet", and it returned the mid-navigation trail.
            if not _url_is_terminal(page):
                if cur == prev:
                    return cur                  # not navigating anywhere terminal, and static -> settled
                prev = cur
        if time.perf_counter() > deadline:
            return prev if prev is not None else (cur or [])
        await asyncio.sleep(quiet_ms / 1000.0)


def _url_is_terminal(page) -> bool:
    """Is the page currently AT a terminal fixture page? Used to tell "still loading the destination" apart
    from "nothing more is coming"."""
    try:
        url = (page.url or "").split("?")[0]
    except Exception:  # noqa: BLE001
        return False
    return url.endswith("/done") or url.endswith("/wrong")


# ---------------------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------------------
async def measure(provider_name: str = "oracle", seed: int = 7, per_k: int = 3,
                  only: Optional[str] = None, arms: tuple = ("replay", "repair")) -> dict:
    """Run every row in every arm and return the record. PRINTS NOTHING, so a CI test can assert it."""
    t_start = time.perf_counter()
    lint = interstitial_offenders(all_fixture_texts())
    rows = build_corpus(seed, per_k=per_k, only=only)
    srv = FixtureServer().start()
    out: list = []
    try:
        from ultracua.browser import BrowserSession

        shared = await BrowserSession(headless=True).start()   # one Chromium for the whole run
        try:
            with TemporaryDirectory() as td:
                golden_root = Path(td) / "golden"
                learned: dict = {}
                # LEARN each scenario ONCE on a pristine page, asserting its golden trail — so a fixture
                # edit that changes what the flow does fails loud instead of silently re-baselining the
                # oracle every row is judged against.
                for sc in SCENARIOS:
                    if only and sc["name"] != only:
                        continue
                    srv.set_mutation("", "")
                    srv.reset_orders()
                    root = golden_root / sc["name"]
                    rep = await run_cached(
                        srv.base + sc["path"], sc["goal"], ScriptedProvider(list(sc["steps"])),
                        FlowCache(root=root), mode="learn", headless=True, browser=shared.browser,
                        finalize=lambda s: s.page.evaluate(READ_ACTS_JS),
                    )
                    acts = rep.extra.get("finalize")
                    if not rep.success or acts != sc["golden"]:
                        raise RuntimeError(
                            f"{sc['name']}: pristine learn failed or its act trail moved "
                            f"(got {acts}, expected {sc['golden']}) — fix the fixture, do not re-baseline")
                    learned[sc["name"]] = root

                for arm in arms:
                    for i, row in enumerate(rows):
                        sc = SCENARIOS_BY_NAME[row["scenario"]]
                        if sc["name"] not in learned:
                            continue
                        srv.reset_orders()
                        target_sel = row.get("target_sel_override") or sc["target_sel"]
                        srv.set_mutation(row["js"], target_sel)
                        # A successful heal/replan RE-CACHES the flow, so a shared cache would let row N's
                        # repair rewrite the locator row N+1 replays. Every row gets a throwaway copy of the
                        # pristine learn.
                        root = Path(td) / f"row-{arm}-{i}"
                        shutil.copytree(learned[sc["name"]], root)
                        cache = FlowCache(root=root)
                        key = flow_key(sc["goal"], srv.base + sc["path"])
                        before = steps_hash(cache.get(key))

                        prov = OracleProvider(plan_for(sc)) if provider_name == "oracle" \
                            else get_provider(provider_name)
                        box: dict = {}

                        async def _prep(session, _p=prov, _b=box, _sel=target_sel):
                            if hasattr(_p, "bind"):
                                _p.bind(session.page)
                            # Census on the MUTATED entry page: did the mutation actually run, and are the
                            # target's own handlers intact? Both are harness-integrity checks — a mutation
                            # that silently threw leaves the page pristine and the row scores `survived`.
                            _b["census"] = await session.page.evaluate(CENSUS_JS, _sel)

                        async def _fin(session):
                            return await _settled_trail(session.page)

                        t0 = time.perf_counter()
                        rep = await run_cached(
                            srv.base + sc["path"], sc["goal"],
                            prov if arm == "repair" else None, cache,
                            mode=arm, headless=True, browser=shared.browser,
                            prepare=_prep, finalize=_fin,
                        )
                        ms = (time.perf_counter() - t0) * 1000.0
                        acts = rep.extra.get("finalize") or []
                        tiers = _tiers(rep)
                        outcome = classify(sc["golden"], acts, rep,
                                           heal_persisted=tiers["heal_persisted"],
                                           replan_attempted=tiers["replan_attempted"])
                        cf = cache.get(key)
                        after = steps_hash(cf) if cf is not None else "MISS"
                        census = box.get("census") or {}
                        usage = (rep.extra or {}).get("usage") or {}
                        out.append({
                            **{k: row[k] for k in ("scenario", "row_id", "kind", "source", "primitives",
                                                   "step_index", "k", "expected_0llm", "killed_anchors",
                                                   "residual_anchors")},
                            "arm": arm, "outcome": outcome, "llm_calls": rep.llm_calls,
                            "report_mode": rep.mode, "success": rep.success,
                            **tiers,
                            "replan_recovered": tiers["replan_attempted"] and GOAL_MARK in acts,
                            "bound_by": _bound_by(rep),
                            "gate_bound_by": next((t.meta["gate_bound_by"] for t in rep.traces
                                                   if t.meta.get("gate_bound_by")), ""),
                            "acts": acts, "golden": sc["golden"],
                            "steps_hash_before": before, "steps_hash_after": after,
                            "recached": after != before,
                            "orders_delta": srv.orders if sc.get("write") else None,
                            "mut_applied": census.get("mut_applied"),
                            "mut_error": census.get("mut_error"),
                            "target_present": census.get("present"),
                            "handlers": census.get("on") or [],
                            "cost_usd": usage.get("cost_usd"),
                            "paid_calls": usage.get("calls"),
                            "wall_ms": round(ms, 1),
                        })
        finally:
            await shared.close()
    finally:
        srv.stop()
    return _score(out, provider_name=provider_name, seed=seed, per_k=per_k, lint=lint,
                  wall_ms=(time.perf_counter() - t_start) * 1000.0, only=only)


def _score(rows: list, *, provider_name: str, seed: int, per_k: int, lint: list,
           wall_ms: float, only: Optional[str]) -> dict:
    """Aggregate + the invariants. Pure over `rows`, so it is unit-testable without a browser."""
    replay = [r for r in rows if r["arm"] == "replay"]
    repair = [r for r in rows if r["arm"] == "repair"]
    gen = [r for r in replay if r["source"] == "generated"]

    # -- the curve: 0-LLM survival by intensity --
    by_k: dict = {}
    for r in gen:
        b = by_k.setdefault(str(r["k"]), {"n": 0, "survived": 0})
        b["n"] += 1
        b["survived"] += 1 if r["outcome"] == "survived" else 0
    for b in by_k.values():
        b["rate"] = round(b["survived"] / b["n"], 4) if b["n"] else 0.0
    k50 = next((int(k) for k in sorted(by_k, key=int) if by_k[k]["rate"] < 0.5), None)

    # -- the two recovery-eligible populations. They are DIFFERENT populations and conflating them would
    #    make both numbers meaningless:
    #      HEAL-eligible   — 0-LLM drifted and the target is STILL PRESENT and semantically correct. This is
    #                        the band a single-step re-grounding can legitimately fix, and it is the band v1
    #                        had none of.
    #      REPLAN-eligible — 0-LLM drifted and the target is GONE. A heal must decline here (there is nothing
    #                        to re-ground), so only a suffix-replan can recover, and only if the goal is
    #                        still reachable by another route at all.
    # WRITE rows are excluded from both, and the reason is a safety fact rather than bookkeeping: a mutating
    # step is NEVER healed and NEVER replanned (four independent refusals — `_maybe_heal`'s mutating check,
    # the replan gate, `block_mutations`, and the refusal to cache a write-firing repair). Counting them as
    # recovery failures conflates a deliberate refusal with a broken mechanism: it dragged the measured
    # ceiling from 12/12 to 12/24, i.e. it halved a number by adding rows that are *supposed* to fail. They
    # are reported separately as `write_recovery_refused`, where a high number is GOOD.
    drifted_ids = {r["row_id"]: r for r in replay
                   if r["outcome"] == "drifted" and not SCENARIOS_BY_NAME[r["scenario"]].get("write")}
    heal_ids = {i for i, r in drifted_ids.items() if r.get("target_present") is not False}
    replan_ids = {i for i, r in drifted_ids.items() if r.get("target_present") is False}
    write_drifted = [r for r in replay if r["outcome"] == "drifted"
                     and SCENARIOS_BY_NAME[r["scenario"]].get("write")]
    rep_heal = [r for r in repair if r["row_id"] in heal_ids]
    rep_replan = [r for r in repair if r["row_id"] in replan_ids]
    rep_elig = rep_heal + rep_replan
    eligible = heal_ids | replan_ids
    survivors = {r["row_id"] for r in replay if r["outcome"] == "survived"}
    rep_surv = [r for r in repair if r["row_id"] in survivors]

    def _n(rs: list, pred) -> int:
        return sum(1 for r in rs if pred(r))

    mech_recovered = _n(rep_elig, lambda r: r["outcome"] in ("healed", "replanned"))

    # -- the histogram: WHICH anchor carried each bind (a rate alone cannot show load migrating to css) --
    hist: dict = {}
    for r in replay:
        for b in r["bound_by"]:
            hist[b] = hist.get(b, 0) + 1

    # -- prediction agreement: the mechanism that catches "same rate, different mechanism" --
    # WRITE rows are excluded, and the reason is a finding rather than a convenience. The predictor is a
    # model of `resolve()` only; a write step ALSO passes the mutation gate, which re-fingerprints the
    # target's enclosing form and refuses on any change to it. Measured: every in-form mutation makes
    # `order-form` drift with `gate_bound_by="testid"` — the locator resolved perfectly and the GATE
    # refused. That is the write-safety posture working as designed (a write flow refuses under essentially
    # any in-form drift), but scoring it against a locator-only model would report 9 false "mismatches" and
    # bury the real ones. Write rows are gated by the four write-safety invariants instead.
    predicted = [r for r in gen if r["expected_0llm"] in ("survived", "drifted")
                 and not SCENARIOS_BY_NAME[r["scenario"]].get("write")]
    agree = [r for r in predicted
             if (r["outcome"] == "survived") == (r["expected_0llm"] == "survived")]
    mismatches = sorted(r["row_id"] for r in predicted if r not in agree)

    # -- v1 parity: its 17 rows, its 12 cosmetics, compared element-wise by the caller --
    v1 = [r for r in replay if r["source"] == "v1-port"]
    v1_cos = [r for r in v1 if r["kind"] == "cosmetic"]
    v1_rows = [{"scenario": r["scenario"], "drift": r["row_id"].split("/", 1)[1],
                "kind": r["kind"], "outcome": r["outcome"]} for r in v1]

    known = {f"{s}/{n}" for s, n in KNOWN_WRONG_BINDS}
    wrong = [r["row_id"] for r in rows if r["outcome"] == "wrong"]
    unexpected_wrong = sorted({w for w in wrong if w not in known})
    writes = [r for r in rows if r["orders_delta"] is not None]

    rec = {
        "bench": "drift_bench", "version": BENCH_VERSION, "fixtures_version": FIXTURES_VERSION,
        "mutator_version": MUTATOR_VERSION, "provider": provider_name, "seed": seed, "per_k": per_k,
        # Recorded, not overridden: the timeout the engine actually ran with, so a rate measured under a
        # different configuration is never silently compared against this baseline.
        "scenario_filter": only, "action_timeout_ms": settings.action_timeout_ms,
        "corpus_hash": corpus_hash([{"scenario": r["scenario"], "step_index": r["step_index"],
                                     "primitives": r["primitives"]} for r in replay]),
        "corpus_size": len(replay),

        # calibration: the MEASURED kill set of each primitive, run alone (declared vs actual)
        "calibration": {r["row_id"].split(":", 1)[1]: {"outcome": r["outcome"], "bound_by": r["bound_by"]}
                        for r in replay if r["source"] == "calibration"},

        "resilience_by_k": by_k, "k50": k50,
        "bound_by_hist": hist,
        "predicted_agreement": round(len(agree) / len(predicted), 4) if predicted else 0.0,
        "predicted_mismatches": mismatches,

        "heal_eligible": len(heal_ids),
        "replan_eligible": len(replan_ids),
        "recovery_eligible": len(eligible),
        "mechanism_heal_attempted": _n(rep_heal, lambda r: r["heal_attempted"]),
        "mechanism_heal_declined": _n(rep_heal, lambda r: r["heal_declined"]),
        "mechanism_heal_persisted": _n(rep_heal, lambda r: r["heal_persisted"]),
        "mechanism_heal_rate": round(_n(rep_heal, lambda r: r["outcome"] == "healed") / len(rep_heal), 4)
        if rep_heal else 0.0,
        "mechanism_replan_attempted": _n(rep_replan, lambda r: r["replan_attempted"]),
        "mechanism_replan_recovered": _n(rep_replan, lambda r: r["replan_recovered"]),
        "mechanism_replan_rate": round(
            _n(rep_replan, lambda r: r["outcome"] == "replanned") / len(rep_replan), 4)
        if rep_replan else 0.0,
        "mechanism_recovered": mech_recovered,
        "mechanism_ladder_rate": round(mech_recovered / len(rep_elig), 4) if rep_elig else 0.0,
        # `sum(x or 0.0) or None` was wrong in BOTH directions at once: an unknown row summed as
        # free, and a genuine total of exactly zero — which is what a key-less bench run produces —
        # came back as None because 0.0 is falsy. So the one number this bench can state with
        # certainty was published as "unknown". Absorbing sum, and zero means zero (1.3).
        "cost_usd": sum_cost(r["cost_usd"] for r in rows),

        "silent_wrong": len(wrong), "wrong_rows": sorted(set(wrong)),
        "known_wrong_binds": [list(k) for k in KNOWN_WRONG_BINDS],
        "unexpected_wrong": unexpected_wrong,
        # The published rate for the ONE accepted hole, so "silent_wrong is not zero" is a number rather
        # than a footnote. Denominator = distinct rows per arm.
        "known_wrong_rate": round(len(wrong) / max(1, len(rows)), 4),
        "escalated": _n(rows, lambda r: r["outcome"] == "escalated"),

        "v1_parity": {
            "cosmetic_total": len(v1_cos),
            "cosmetic_survived_0llm": _n(v1_cos, lambda r: r["outcome"] == "survived"),
            "resilience_0llm": round(_n(v1_cos, lambda r: r["outcome"] == "survived") / len(v1_cos), 4)
            if v1_cos else 0.0,
            "ambiguous_disambiguated": all(r["outcome"] in ("survived", "healed")
                                           for r in v1 if r["kind"] == "ambiguous"),
            "semantic_failed_loud": all(r["outcome"] == "drifted" for r in v1 if r["kind"] == "semantic"),
            "conflict_no_wrongbind": all(r["outcome"] != "wrong" for r in v1 if r["kind"] == "conflict"),
            "rows": v1_rows,
        },

        # A SAFETY number, not a failure rate: every drifted write row that the recovery arm refused to
        # heal or replan. High is good; anything less than 100% would be an inviolable-#3 breach.
        "write_recovery_refused": sum(
            1 for r in repair
            if r["row_id"] in {w["row_id"] for w in write_drifted}
            and r["outcome"] not in ("healed", "replanned")),
        "write_drifted": len(write_drifted),
        "write_rows": len(writes),
        "write_double_submits": _n(writes, lambda r: (r["orders_delta"] or 0) > 1),
        "write_suppressed": _n(writes, lambda r: r["success"] and (r["orders_delta"] or 0) == 0),
        "write_at_wrong_target": _n(writes, lambda r: "decoy" in r["acts"] or "draftdel" in r["acts"]),
        "write_recovered": _n(writes, lambda r: r["outcome"] in ("healed", "replanned")),

        "fixture_lint": lint,
        "wall_ms": round(wall_ms, 1),
        "rows": rows,
    }
    rec["invariants"] = _invariants(rec, rows, replay, repair, rep_surv, eligible)
    rec["unexpected_wrong"] = unexpected_wrong
    return rec


def _invariants(rec: dict, rows: list, replay: list, repair: list, rep_surv: list,
                eligible: set) -> dict:
    """Every hard guarantee, as name -> bool. All must hold or the bench exits 1, in BOTH arms — a
    wrong-bind is never a variance question. v1's four guarantees are the first four."""
    v1 = rec["v1_parity"]
    return {
        # --- v1's guarantees, carried forward (not replaced) ---
        # An EXACT set comparison, not `== 0` and not a tolerance: the one accepted hole is named in
        # `KNOWN_WRONG_BINDS` with its reason, and ANY other wrong row fails the bench.
        "no_unexpected_wrong": not rec["unexpected_wrong"],
        "semantic_failed_loud": all(r["outcome"] in ("drifted", "escalated") for r in rows
                                    if r["kind"] == "semantic"),
        "conflict_no_wrongbind": v1["conflict_no_wrongbind"],
        "ambiguous_disambiguated": v1["ambiguous_disambiguated"],
        "v1_parity_12_of_12": v1["cosmetic_total"] == 12 and v1["cosmetic_survived_0llm"] == 12,

        # --- the 0-LLM guarantee, measured rather than assumed (inviolable #1) ---
        "zero_llm_purity": all(r["llm_calls"] == 0 for r in replay),
        "repair_noop_on_survivors": all(r["llm_calls"] == 0 and not r["recached"] for r in rep_surv),
        "no_false_success": all(GOAL_MARK in r["acts"] for r in rows
                                if r["success"] and r["row_id"] not in
                                {f"{s}/{n}" for s, n in KNOWN_WRONG_BINDS}),

        # --- the ladder cannot silently rot ---
        "heal_eligible_nonempty": len([r for r in replay if r["outcome"] == "drifted" and r.get("target_present") is not False]) >= MIN_HEAL_ELIGIBLE,
        "provider_actually_fired": any(r["heal_attempted"] for r in repair),
        "heal_invalidates_approval": all(r["recached"] for r in repair
                                         if r["outcome"] in ("healed", "replanned")
                                         and r["row_id"] not in
                                         {f"{s}/{n}" for s, n in KNOWN_NO_DIGEST_MOVE}),

        # --- write safety, both directions (inviolable #3) ---
        "no_write_double_submit": rec["write_double_submits"] == 0,
        "no_write_suppressed": rec["write_suppressed"] == 0,
        "no_write_at_wrong_target": rec["write_at_wrong_target"] == 0,
        "write_never_recovered": rec["write_recovered"] == 0,

        # --- harness integrity: the bench must not be able to measure itself wrong ---
        "mutations_all_applied": all(r["mut_applied"] is not False and not r["mut_error"] for r in rows),
        "handlers_intact": all(r["handlers"] or not r.get("target_present") for r in rows
                               if r["scenario"] == "span-link"),
        "fixtures_lint_clean": not rec["fixture_lint"],
        "escalate_rows_classified": rec["escalated"] == sum(
            1 for r in rows if r["kind"] == "escalate"),
        "within_wall_budget": rec["wall_ms"] <= 180000,
    }


# ---------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------
_MARK = {"survived": "OK   ", "healed": "HEAL ", "replanned": "REPLN", "drifted": "DRIFT",
         "escalated": "ESCAL", "wrong": "WRONG"}


async def run(provider_name: str, json_path: Optional[str], baseline_path: Optional[str],
              seed: int, per_k: int, only: Optional[str], quiet: bool) -> int:
    rec = await measure(provider_name, seed=seed, per_k=per_k, only=only)
    if not quiet:
        for arm in ("replay", "repair"):
            print(f"\n=== arm={arm} ===")
            for r in rec["rows"]:
                if r["arm"] != arm:
                    continue
                extra = f"  llm={r['llm_calls']}" if r["llm_calls"] else ""
                by = ",".join(r["bound_by"]) or "-"
                print(f"  [{_MARK[r['outcome']]}] k={r['k']} {r['row_id']:<58} by={by}{extra}")

    print("\n" + "=" * 100)
    print(f"silent_wrong          = {rec['silent_wrong']} ({rec['known_wrong_rate']:.1%} of rows)"
          f"   known+published: {[f'{s}/{n}' for s, n in KNOWN_WRONG_BINDS]}")
    if rec["unexpected_wrong"]:
        print(f"  !! UNEXPECTED wrong-binds (not in the published allowlist): {rec['unexpected_wrong']}")
    print(f"v1 parity             = {rec['v1_parity']['cosmetic_survived_0llm']}/"
          f"{rec['v1_parity']['cosmetic_total']} cosmetic drifts survive 0-LLM")
    print(f"0-LLM survival curve  = " + "  ".join(
        f"k{k}:{rec['resilience_by_k'][k]['survived']}/{rec['resilience_by_k'][k]['n']}"
        for k in sorted(rec["resilience_by_k"], key=int)) + f"   (k50={rec['k50']})")
    print(f"recovery-eligible     = {rec['recovery_eligible']} rows   "
          f"(heal-eligible {rec['heal_eligible']} + replan-eligible {rec['replan_eligible']})"
          f"   [v1 had 0: every cosmetic drift bound at 0-LLM]")
    print(f"MECHANISM heal        = {rec['mechanism_heal_persisted']}/{rec['heal_eligible']} "
          f"({rec['mechanism_heal_rate']:.0%})  declined={rec['mechanism_heal_declined']}")
    print(f"MECHANISM replan      = {rec['mechanism_replan_recovered']}/{rec['replan_eligible']} "
          f"({rec['mechanism_replan_rate']:.0%})  "
          f"attempted={rec['mechanism_replan_attempted']}")
    print(f"MECHANISM ladder      = {rec['mechanism_recovered']}/{rec['recovery_eligible']} "
          f"({rec['mechanism_ladder_rate']:.0%})")
    print(f"                        ^ a perfect-vision oracle: an upper bound on the MECHANISM, "
          f"never a heal rate")
    print(f"bound_by              = {dict(sorted(rec['bound_by_hist'].items()))}")
    print(f"predicted_agreement   = {rec['predicted_agreement']:.0%}"
          f"{'   mismatches: ' + str(rec['predicted_mismatches']) if rec['predicted_mismatches'] else ''}")
    print(f"writes                = {rec['write_rows']} rows; double={rec['write_double_submits']} "
          f"suppressed={rec['write_suppressed']} wrong_target={rec['write_at_wrong_target']} "
          f"recovered={rec['write_recovered']}")
    print(f"                        recovery REFUSED on {rec['write_recovery_refused']}/"
          f"{rec['write_drifted']} drifted write rows (high is good — a write is never healed)")
    print(f"wall                  = {rec['wall_ms'] / 1000:.1f}s over {rec['corpus_size']} rows x 2 arms")

    failed = [k for k, v in rec["invariants"].items() if not v]
    print("\ninvariants: " + ("ALL HOLD" if not failed else f"FAILED -> {failed}"))

    if json_path:
        Path(json_path).write_text(json.dumps(rec, indent=2), encoding="utf-8")
        print(f"wrote {json_path}")

    if baseline_path:
        base = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
        for field in ("corpus_hash", "mutator_version", "fixtures_version", "action_timeout_ms"):
            if rec[field] != base.get(field):
                print(f"REGRESSION: {field} {rec[field]!r} != baseline {base.get(field)!r} — the corpus "
                      f"itself changed, so the rates are not comparable. Re-baseline deliberately.")
                failed.append(field)
        if rec["v1_parity"]["rows"] != base["v1_parity"]["rows"]:
            print("REGRESSION: v1 parity rows differ element-wise from the baseline")
            failed.append("v1_parity_rows")
        for k, b in base.get("resilience_by_k", {}).items():
            cur = rec["resilience_by_k"].get(k, {}).get("rate", 0.0)
            if cur + 1e-9 < b["rate"] - 0.10:
                print(f"REGRESSION: k={k} survival {cur:.0%} < baseline {b['rate']:.0%} - 10pts")
                failed.append(f"k{k}")
        if set(rec["predicted_mismatches"]) - set(base.get("predicted_mismatches", [])):
            print(f"NEW prediction mismatches: "
                  f"{sorted(set(rec['predicted_mismatches']) - set(base.get('predicted_mismatches', [])))}"
                  f" — resolve()'s behaviour moved away from the model. Triage before re-baselining.")
            failed.append("predicted_mismatches")
        cur_css = rec["bound_by_hist"].get("css", 0)
        base_css = base.get("bound_by_hist", {}).get("css", 0)
        if cur_css > base_css + max(3, 0.10 * max(1, rec["corpus_size"])):
            print(f"REGRESSION: binds carried by the brittle positional css rose {base_css} -> {cur_css}")
            failed.append("bound_by_css")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="benchmarks.drift_bench")
    ap.add_argument("--provider", default="oracle",
                    help="oracle = the KEY-LESS mechanism arm (default). anthropic/openai/gemini = the paid "
                         "arm, which measures model accuracy and is never wired into CI.")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--per-k", dest="per_k", type=int, default=3)
    ap.add_argument("--scenario", default=None, help="run one scenario only")
    ap.add_argument("--json", dest="json_path", default=None)
    ap.add_argument("--baseline", dest="baseline_path", default=None)
    ap.add_argument("--quiet", action="store_true", help="summary only, no per-row lines")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(run(a.provider, a.json_path, a.baseline_path, a.seed, a.per_k,
                                     a.scenario, a.quiet)))
