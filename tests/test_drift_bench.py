"""drift-bench v2 as a CI GATE — key-less, deterministic, one shared Chromium (~50 s).

This replaces `tests/test_drift_sandbox.py`. v1's guarantee is not dropped: all 17 of its drifts are ported
verbatim into v2 and this file asserts its 12/12 headline plus its per-row outcome vector ELEMENT-WISE
against the still-committed `baselines/drift.json`. So the historical gate is carried inside the new one
rather than replaced by a differently-shaped number.

Beyond that it adds what v1 structurally could not have:
  * a heal-eligible population (v1 had zero — every cosmetic drift bound at 0-LLM, measured), so the
    recovery mechanism is exercised at all;
  * a 0-LLM survival CURVE over mutation intensity instead of one saturated 100%;
  * `silent_wrong` judged from the actuated action sequence rather than the landed URL;
  * the write-safety invariants, both directions, against a server-side order counter.

The bench is deterministic — `OracleProvider` is page-reactive but has no sampling, no temperature and no
network, and the only RNG is a seeded `random.Random` at corpus-build time — so this can hold v1's
zero-tolerance posture on the safety invariants. The only stochastic numbers live in the paid
`--provider anthropic` arm, which is never wired into CI.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.drift_bench import KNOWN_WRONG_BINDS, MIN_HEAL_ELIGIBLE, measure

# The bench costs ~50 s, so it runs ONCE and every assertion reads the same record.
_REC: dict = {}


async def _record() -> dict:
    """Run the bench once and share the record across every assertion (it costs ~50 s)."""
    if not _REC:
        _REC["r"] = await measure()
    return _REC["r"]


# ============================ the invariants ============================

async def test_every_absolute_invariant_holds() -> None:
    rec = await _record()
    failed = sorted(k for k, v in rec["invariants"].items() if not v)
    assert not failed, f"drift-bench invariants failed: {failed}"


async def test_no_wrong_outcome_outside_the_published_allowlist() -> None:
    """Inviolable #2, in its strongest available form. Compared as an EXACT set against a named allowlist,
    never as a tolerance — a tolerance would launder an unknown number of holes, whereas a named row with a
    documented reason keeps the guarantee honest and makes closing it a visible act."""
    rec = await _record()
    assert rec["unexpected_wrong"] == [], (
        f"a wrong outcome appeared that is NOT in KNOWN_WRONG_BINDS: {rec['unexpected_wrong']}. "
        f"Triage it — do not add it to the allowlist without a documented reason.")
    known = {f"{s}/{n}" for s, n in KNOWN_WRONG_BINDS}
    assert set(rec["wrong_rows"]) <= known


async def test_zero_llm_replay_really_makes_no_llm_call() -> None:
    """Inviolable #1, measured rather than assumed, over every row in the corpus."""
    rec = await _record()
    assert all(r["llm_calls"] == 0 for r in rec["rows"] if r["arm"] == "replay")


async def test_a_write_is_never_healed_double_fired_or_silently_suppressed() -> None:
    """Inviolable #3, both directions, against a SERVER-SIDE counter that discriminates the real submit from
    the decoy submit in the same form."""
    rec = await _record()
    assert rec["write_double_submits"] == 0
    assert rec["write_suppressed"] == 0
    assert rec["write_at_wrong_target"] == 0
    assert rec["write_recovered"] == 0
    assert rec["write_recovery_refused"] == rec["write_drifted"], \
        "a drifted WRITE row was recovered — the four write refusals are not all holding"


# ============================ v1's gate, carried forward ============================

async def test_v1_parity_is_exact_against_the_committed_v1_baseline() -> None:
    """v1's headline AND its per-row outcome vector, compared element-wise. This is how deleting
    `tests/test_drift_sandbox.py` costs nothing: the same 17 rows are still gated, here."""
    rec = await _record()
    v1 = rec["v1_parity"]
    assert v1["cosmetic_total"] == 12
    assert v1["cosmetic_survived_0llm"] == 12
    assert v1["resilience_0llm"] == 1.0
    assert v1["ambiguous_disambiguated"] is True
    assert v1["semantic_failed_loud"] is True
    assert v1["conflict_no_wrongbind"] is True

    base = json.loads(Path("baselines/drift.json").read_text(encoding="utf-8"))
    want = {(r["scenario"], r["drift"]): r["outcome"] for r in base["rows"]}
    got = {(r["scenario"], r["drift"]): r["outcome"] for r in v1["rows"]}
    assert got == want, f"v1 row outcomes drifted vs baselines/drift.json: {got} != {want}"


# ============================ what v1 could not measure ============================

async def test_the_ladder_has_a_nonempty_heal_eligible_population() -> None:
    """The anti-rot invariant, and the whole reason v2 exists. v1's recovery arm measured nothing because 0
    of its 12 cosmetic drifts ever consulted heal. If a resolver improvement re-saturates the curve so that
    nothing drifts at 0-LLM, this fails loud demanding harder rungs instead of the bench quietly becoming
    decorative again."""
    rec = await _record()
    assert rec["heal_eligible"] >= MIN_HEAL_ELIGIBLE, (
        f"only {rec['heal_eligible']} rows drift at 0-LLM with the target still present — the recovery "
        f"ladder is starved and its rates are not measuring anything. Add harder rungs.")
    assert rec["mechanism_heal_attempted"] >= 1, "the recovery provider never fired"


# Heal-eligible rows the mechanism is KNOWN to decline, each with the finding that explains it. Same
# discipline as the `silent_wrong` allowlist: a named, published exception — never a loosened threshold.
# A row leaving this list is a fix; a row joining it needs a finding first.
_KNOWN_HEAL_DECLINES = {
    # `reparent` moves the control into a fresh `<div>` appended to the nearest `section/main/form/body`
    # — for this fixture, out of the table altogether. The element is still present, so the row is
    # heal-eligible, but it is genuinely in NO row any more, so the containment guard has nothing to
    # confirm and refuses. That refusal is the guard working, and the cost of a guard that answers
    # "cannot confirm" with a refusal rather than a bind.
    #
    # THIS ENTRY USED TO BLAME R3.7, AND THAT ATTRIBUTION WAS WRONG (see R4.33). The divergence R3.7
    # names cannot occur on this fixture at all — the row's only identity is the href of the link inside
    # the nested container, so capture and bind produce the same string either way. The decline is
    # therefore unchanged by S11's attempted fix, which is what the bench measured: the scenario's
    # survival curve, `bound_by` histogram and ladder rate are byte-identical with and without it, so its
    # single decline is too. A comment naming a finding is not evidence the finding is what happens here.
    "row-nested-action/reparent",
}


async def test_the_recovery_mechanism_persists_its_repairs() -> None:
    """With a perfect-vision oracle the MECHANISM should recover every heal-eligible row: re-ground, verify
    the effect, persist the locator. Anything less is a mechanism defect rather than a model limitation."""
    rec = await _record()
    declined = {r["row_id"] for r in rec["rows"]
                if r.get("heal_attempted") and not r.get("heal_persisted")
                and r.get("target_present")}
    unexpected = declined - _KNOWN_HEAL_DECLINES
    assert not unexpected, (
        f"the heal mechanism declined {sorted(unexpected)} even though the target is still PRESENT and "
        f"it had ground-truth element identity — a mechanism defect, not a model limitation")
    assert rec["mechanism_heal_persisted"] + len(_KNOWN_HEAL_DECLINES) >= rec["heal_eligible"], (
        f"the heal mechanism recovered only {rec['mechanism_heal_persisted']}/{rec['heal_eligible']} "
        f"rows even with ground-truth element identity, beyond the published declines")


async def test_a_persisted_repair_always_invalidates_the_approval() -> None:
    """The tie-in to the 0.60.0 steps-hash gate: a self-healed recipe must never keep running under a stale
    approval bit. Every re-cached row's digest must move, so an approved flow refuses on its next run."""
    rec = await _record()
    recovered = [r for r in rec["rows"] if r["outcome"] in ("healed", "replanned")]
    assert recovered, "no recovery happened, so this guarantee was not exercised"
    for r in recovered:
        assert r["recached"], f"{r['row_id']} recovered without re-caching"
        assert r["steps_hash_after"] != r["steps_hash_before"], \
            f"{r['row_id']} was repaired but its approval digest did not move"


async def test_repair_mode_is_a_noop_on_rows_that_already_survive() -> None:
    """A repair arm that quietly re-authored working rows would inflate every ladder number."""
    rec = await _record()
    survivors = {r["row_id"] for r in rec["rows"] if r["arm"] == "replay" and r["outcome"] == "survived"}
    for r in rec["rows"]:
        if r["arm"] == "repair" and r["row_id"] in survivors:
            assert r["llm_calls"] == 0 and not r["recached"], f"repair touched a survivor: {r['row_id']}"


async def test_the_survival_curve_actually_declines() -> None:
    """`k` is only a meaningful axis if it orders difficulty. A flat curve would mean the intensity model
    does not track what actually breaks the resolver."""
    rec = await _record()
    by_k = rec["resilience_by_k"]
    assert len(by_k) >= 5, "the ladder does not span enough intensities to be a curve"
    lo = by_k[min(by_k, key=int)]["rate"]
    hi = by_k[max(by_k, key=int)]["rate"]
    assert hi < lo, f"survival does not fall with intensity ({lo} -> {hi}) — k is not ordering difficulty"


async def test_the_bench_records_which_anchor_carried_each_bind() -> None:
    """A resilience rate alone cannot distinguish "still 100%, carried by role+name" from "still 100%, now
    carried by the brittle positional css". The histogram is what makes that regression visible."""
    rec = await _record()
    hist = rec["bound_by_hist"]
    assert hist, "no bound_by labels were recorded — the resolve() sink is not wired"
    assert {"testid", "role+name"} & set(hist)


# ============================ harness integrity ============================

async def test_every_mutation_actually_ran() -> None:
    """A mutation that throws leaves the page pristine, the flow replays perfectly, and the row scores
    `survived` — silently inflating the rate with rows that were never mutated. It happened (composed
    primitives concatenating into a syntax error), so it is asserted rather than assumed."""
    rec = await _record()
    bad = [r["row_id"] for r in rec["rows"] if r["mut_applied"] is False or r["mut_error"]]
    assert not bad, f"mutations failed to apply on {bad}"


async def test_the_baseline_is_current() -> None:
    """The committed baseline must describe THIS corpus, or its rates are being compared across different
    populations. Exact compare on the corpus digest + the versions."""
    rec = await _record()
    base = json.loads(Path("baselines/drift_v2.json").read_text(encoding="utf-8"))
    for field in ("corpus_hash", "corpus_size", "mutator_version", "fixtures_version",
                  "action_timeout_ms"):
        assert rec[field] == base[field], (
            f"{field} differs from baselines/drift_v2.json ({rec[field]!r} vs {base[field]!r}) — "
            f"re-record the baseline deliberately if the corpus really changed")


async def test_no_new_prediction_mismatches_vs_the_baseline() -> None:
    """`predicted_agreement` compares the resolver against a frozen MODEL of its decision surface. A new
    mismatch means the resolver's behaviour moved — which is exactly the "same rate, different mechanism"
    regression a rate cannot show. Gated as a subset, so known modelling gaps do not block."""
    rec = await _record()
    base = json.loads(Path("baselines/drift_v2.json").read_text(encoding="utf-8"))
    new = sorted(set(rec["predicted_mismatches"]) - set(base["predicted_mismatches"]))
    assert not new, f"resolve() diverged from the corpus model on new rows: {new}"


async def test_the_run_fits_its_wall_budget() -> None:
    rec = await _record()
    assert rec["wall_ms"] <= 180000, f"drift-bench took {rec['wall_ms'] / 1000:.0f}s"
