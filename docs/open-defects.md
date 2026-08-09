# Open defects — the standing register

**ROUND 1** (2026-07-31, at v0.63.0): 20 findings, all fixed in 0.64.0–0.69.0.
**ROUND 2** (2026-08-02, scoped to everything written SINCE): 10 findings, 2 critical — all fixed
(R1/R2 in 0.71.0, R3–R10 in 0.72.0). Most were holes in the round-1 fixes.
**ROUND 3** (2026-08-03, scoped to the 387 lines those fixes added): **11 findings, 1 critical, NONE
refuted** — see the bottom of this file. Two are REGRESSIONS introduced by the round-2 fixes. Defect
density in fix code is ~3x that of the code being fixed. A twelfth (R3.12) was added afterwards, found
by applying this file's own sibling rule while redesigning R3.2; it is recorded as NOT reproduced.
**ROUND 4** (2026-08-04, pre-merge on the causal-attribution attempt): 20 candidates, 2 defects
CONFIRMED BY EXECUTION and fixed on the branch, 3 left open — and the branch was **PARKED, not
shipped**. It was green (785 tests, drift_bench byte-identical) and still wrong: the THIRD consecutive
green-but-wrong change in this area. See the round-4 section below and `docs/parked/README.md`.

**THE PLAN.** `docs/correctness-plan.md` sequences every open item here — plus the test-machinery
holes, CLI/API truthfulness defects and unpinned residuals found by the survey in
`docs/correctness-survey.md` — into slices, worst user harm first. Work from it rather than picking
findings ad hoc; its ordering encodes dependencies the individual entries do not.

**Round 3's response was three REDESIGNS, not three patches** (R3.1, R3.2, R3.4 in 0.73.0): its own
finding was that patching each item is what produced round 3, so those three were changed in shape —
one implementation instead of two, exclusive intervals instead of arbitrated overlapping ones, and a
type that can say "unreadable" instead of one that could only say "absent".

**Open as of 0.80.0 — SEVEN.** Of round 3's eleven: R3.1 and R3.4 were redesigned in 0.73.0, R3.2's
redesign was measured to regress and REVERTED (so it is open), and R3.3, R3.5, R3.8 and R3.9 have since
been fixed by plan slices — leaving **R3.2, R3.6, R3.7, R3.10, R3.11**. R3.12 and R3.13 were recorded
later, during 0.74.0, and are also open. Keep this line honest: it is the first thing anyone reads, and
a stale count here is the DOC-1 defect the plan already fixed once for the README.

**What this is.** A six-lens adversarial audit of every implemented subsystem at v0.63.0, hunting for ways
to violate the three inviolables. 20 findings survived a refutation pass and all 20 were fixed in 0.64.0–0.69.0. **That is round 1, and it
is no longer the whole story** — the round-2 re-audit at the bottom of this file found 10 more, two of
them critical, mostly IN those fixes. Everything below is kept because re-deriving it costs ~28 agents and
~25 minutes, and it is far more valuable written down than re-discovered.

## 2026-08-01: all 14 open findings were independently reproduced

One verifier per finding, each instructed to REFUTE and to default to refuting, each required to run a real
probe rather than read code. **All 14 came back CONFIRMED with an executed probe** — none was misdiagnosed
the way A1 was. Two severities moved, and both moves matter:

* **C2 is worse than filed.** It was recorded as "authoring hygiene — no wrong data reaches a replay
  consumer". It does: the rejected re-learn's `read_pin` is bound onto the APPROVED flow, whose steps still
  walk to the OLD page, where that pin resolves to a *different* number. The approval gate structurally
  cannot catch it, because the steps never changed. Reproduced returning `7` where the truth was `42`.
* **C1 is milder** (low, not medium) — a reporting divergence, always loud.

Several verifiers also found sibling exposure the audit missed, now folded into the fix plans: A10 makes
`dry_run` **actually fire** a GET-commit at the real server while certifying itself clean, and silently
no-ops `run_batch(resume=...)`; A7 and A9 poison the *cache* as well as the run; A12's twin lives in
`history.py`, where losing the anchor is self-concealing.

**Fixed in 0.65.0** (PR #106), each with a regression test confirmed to fail against the pre-fix code:
**C2**, **A12** (+ its `history.py` twin and the missing `fsync` on all three sidecars), **A14**, **C1**
(+ a `spec.slots and` short-circuit still living in `dry_run` — the A13 hole, one file over).

**Fixed in 0.66.0**, same standard: **A9 + A5** (one fix — the learn loop already had the wire
evidence and threw it away; it is now written back onto the step that caused it), **A10** (a
declared write that plans zero Idempotency-Keys is refused at the shared gate, which also stops
`dry_run` firing a GET-link commit for real), **A6** (the heal gets BOTH guards the sibling replan
path has), **A7** (a healed step types the caller's row, never the model's — and the suffix-replan
is refused when a row is bound), **A8** (the whole-flow confirm requires an absent->present
TRANSITION, plus an authoring-time refusal).

**Fixed in 0.67.0**, same standard: **B1** (a demoed password no longer reaches disk — blanked in
the page at BOTH capture sites, since neither `domainOf` nor `specOf` carries the input type into
Python; `record()` refuses an unbound credential field rather than caching a recipe that would
replay an empty one) and **B2** (password values are masked in `SNAPSHOT_JS` and a spec's resolved
`$secret_env` values are scrubbed from every Observation — element values AND page text — before one
can reach a provider).

**Fixed in 0.68.0**: **A3** — Tier 1's `role+name~` (a case-insensitive SUBSTRING match) is ordered
LAST in its tier, so it can no longer short-circuit placeholder / exact-text / element id and bind a
decoy whose name merely CONTAINS the cached one. Measured FREE on the drift corpus: the bound_by
histogram, the survival curve, silent_wrong, the mechanism rates and the predictor's agreement are
byte-identical to baselines/drift_v2.json, and the candidate still carries its 2 binds — deletion
would have cost a 0-LLM row. The residual (uncorroborated when it is the ONLY surviving Tier-1
candidate) is recorded in HEALING.md.

**Fixed in 0.69.0: A2, the last of round 1** (the register was briefly empty; round 2 refilled it).** The re-probe first CORRECTED the
finding: a flow whose only write is document-class was already refused (the `wire_write and not
gated` arm fires when nothing is gated), so the hole needed a SECOND, correctly-gated write to mask
it — the filed "save draft, then send" shape. The decisive control: the same masking shape over
`fetch` was always refused, and over a form POST was cached, so the bug was the TRANSPORT the
recorder could not see, not the shape. The recorder now markers document-class submissions in-page
(both disjoint paths: the `HTMLFormElement.prototype.submit` patch and a capture-phase `submit`
listener) and reconciles them against a `doc_urls` wire tally. An unattributable form POST now
REFUSES; an attributable one (a `<button type=button>` calling `form.submit()`) is now GATED with a
precondition and an Idempotency-Key.

**How to read it.** A *hole* here means a claim the code does not deliver, or an unguarded path that can
silently return wrong data / actuate the wrong element / fire or suppress a write. Deliberately excluded:
missing roadmap features, documented-and-enforced limits, and style. Severities are the audit's, corrected
where the refutation pass corrected them.

**How much to trust it.** Each finding was attacked by an independent verifier that defaulted to refuting;
one was killed outright and several severities were cut. 20-of-21 surviving was a suspiciously high rate at
the time — so on 2026-08-01 every remaining item was attacked a SECOND time by a fresh verifier required to
run a probe, including A2/A8/A9, whose original lens had reported that its own safety review did not
complete. All of them held. Treat this file as reproduced fact, not as leads.

**A caution learned the hard way.** The audit MISDIAGNOSED A1 — it blamed the row anchor's substring
matching, when in fact the anchor never runs in that scenario at all (deleting the row makes the control
unique, so Tier 1 binds the survivor outright). The *symptom* was real and serious; the *cause* was not what
was written. Reproduce before fixing.

## The structural finding, which matters more than any single item

Five of these — A1, A4, A6, A9, A13 — are cases where **the correct guard already exists elsewhere in the
codebase and was simply not applied to the sibling path**:

| finding | the guard exists in… | …but not in |
|---|---|---|
| A6 | the suffix-replan path (`block_mutations`) | the self-heal path |
| A9 | `recorder.py`'s form-method patch | `learn()` / `_author_steps` |
| A1, A3 | the hardened `heading` / `label` anchor sources | the `row` source |
| A12 | `load_history`, `cache.get` | `_load_meta` |
| A7 | the `flows.py` wrapper | the `run_cached` mechanism |

The recurring shape is **a guard living in a wrapper, or on one authoring path, instead of in the
mechanism**. Every safe entry point is safe; the raw one underneath is not. A targeted pass to push those
guards down would have prevented most of this list, and is the highest-leverage follow-up available.

## Status at a glance

Round 1 only — round 2's open findings are listed at the bottom of this file.

| | 0.64.0 | 0.65.0 | 0.66.0 | 0.67.0 | 0.68.0 | 0.69.0 | open |
|---|---|---|---|---|---|---|---|
| **critical** | A1 | — | — | — | — | A2 | **none** |
| **high** | A4, A11 | A12, A14, C2 | A5, A6, A7, A8, A9, A10 | — | A3 | — | **none** |
| **medium** | A13 | — | — | B1, B2 | — | — | **none** |
| **low** | — | C1 | — | — | — | — | **none** |

(Severities in this table are the *verified* ones where the 2026-08-01 pass corrected the audit: A14 and C2
up to high, B1 down to medium, C1 down to low.)

Fixed in 0.64.0 (PR #104), each with a regression test confirmed to fail against the pre-fix code:
**A1** (wrong-row write), **A4** (auth headers wiped by the Idempotency-Key), **A11** (empty pinned read
returned as an answer), **A13** (dropping the whole slot table skipped re-approval).

**VERIFIED BY HAND** — as of 2026-08-01, **every finding in this file**. A1, A3, A4, A11, A13 in the
original session; the other 14 in the independent reproduction pass described above, each with a probe that
was actually executed. Nothing here is now "a strong lead" only.

**Round 1 is fully closed.** All 20 are fixed, each with a regression test confirmed to fail against its
own pre-fix code. **Round 2 is not** — 10 findings, 2 critical, at the bottom of this file. Beyond those,
what remains is RESIDUALS, each recorded where the person who would hit it will look: the token-less positional-css retarget and the uncorroborated Tier-1
`role+name~` (HEALING.md); the vision tier shipping a legible screenshot of a secret typed into a plain
text field (`SlotSpec`); a top-frame document POST initiated by a third-party iframe's own form, which
now fails loud (recorder.py); and the H9 value-contract layer still being gated on `spec.mutate is None`,
so a write flow's readback value has no contract check — that last one was found while fixing A14, is NOT
part of this audit, and has not been probed.

---

*Everything below is the audit's own report, unedited apart from the ✅ markers on the fixed items and one
struck-through sentence in C2 that the reproduction pass proved wrong.*

---

# ultracua audit: decision-ready summary

## Headline

**Something is actually broken — not one thing, a pattern.** Six lenses, 20 findings survived adversarial verification: 2 critical, 11 high, 7 medium. Zero lenses came back clean.

The honest scoping: the core promise still holds on the narrow happy path — an approved, learned, slot-less read flow with no custom headers, no MCP, no heal provider, on a page without near-duplicate rows. Every confirmed break sits at an edge the docs explicitly claim is covered. That is the problem: **the failures cluster precisely where a written guarantee exists but is not enforced**, which is worse than an undocumented gap, because the docs are what people plan around.

What genuinely held up under attack, and is worth stating: **inviolable (1) — replay never calls an LLM — was never breached by any surviving finding.** No path was found where a 0-LLM replay silently reaches for a model, with one caveat below (wiped `read_pin`). The adversarial pass also did real work: one finding was killed outright (the MCP `job_id="mcp"` ledger claim), two severities were corrected downward, and sub-claims inside four surviving findings were refuted. The findings below are what survived being attacked, most with a probe that was actually run.

---

## Group A — can produce a WRONG RESULT or a WRONG WRITE (fix now)

Ranked by expected harm = probability × blast radius × silence, not by how interesting the bug is.

✅ **FIXED in 0.64.0** — **A1. A write can fire against the wrong row, and the run reports success.** *(critical, `locators.py:325`, `locators.py:108`)*
Tier-3's row anchor matches a *substring* of the recorded row text, and the anchor is truncated to 60 chars — so it is really a prefix. Delete the row you recorded, and a sibling row sharing that prefix (same customer, same address, same product name — routine in order/invoice tables) matches uniquely and binds outright. The mutation gate cannot catch it because per-row forms are structurally identical, so the fingerprint matches byte-for-byte. **Failure:** cancel/refund/pay fires against a different customer's record, an Idempotency-Key is minted for it, `_replay_step` returns `ok=True`. Reproduced twice end-to-end. `heading` and `label` were hardened against exactly this class; `row` — the only source guarding per-row writes — was left on the loose matcher.

✅ **FIXED in 0.69.0** — **A2. A demonstrated write is dropped from the recipe and never fires again, while replay says "confirmed."** *(critical, `recorder.py:516`, `recorder.py:249`, `flows.py:2639`)*
The recorder only reconciles fetch/XHR writes. A form POST (document-class) fired from a `<div>`, a bare `<a>`, or a `<button type=button>` calling `form.submit()` is captured as no step at all — or as a non-mutating one. **Failure:** a "save draft, then send" flow permanently loses the draft write on every replay and returns `{'status': 'confirmed'}`. The `<button type=button>` variant is worse and needs no exotic markup: the write is captured but flagged non-mutating, so on replay it re-fires **ungated, with no precondition check and no Idempotency-Key**. Reproduced with four variants plus a fetch/XHR control that correctly refused.

✅ **FIXED in 0.68.0** — **A3. Tier-1 `role+name~` is a substring match dressed as an identity anchor.** *(high, `locators.py:266`)*
It sits above placeholder, exact-text and element id, and returns outright — so the css cross-check built to stop exactly this never runs. **Failure:** the recorded target's label is renamed; three intact exact anchors still point at the correct element, and a substring match binds a *different* one ("Coupon code" instead of "Code"). As a `type` step it fills the wrong field. Universal surface — every flow, no writes required, 0 LLM, reports success. The whole corpus gives this candidate 2 binds out of 201, so demoting it is nearly free.

✅ **FIXED in 0.64.0** — **A4. The Idempotency-Key wipes the flow's auth headers — and they never come back.** *(high, `flow.py:882`, `flow.py:1009`)*
`set_extra_http_headers` replaces rather than merges. The write itself goes out with `X-Tenant`/`Authorization` stripped, and every request for the rest of the run loses them too. **Failure:** a payment POSTs against the server's default tenant instead of tenant 42, and the post-write confirm GET reads the wrong-context page. `replay()` returns `{"status": "confirmed"}`. Deterministic, no model involved, 100% reproducible for anyone using `spec.headers`. Reproduced against a real server. The one-line comment "clear the idempotency header" is itself wrong about what the call does.

✅ **FIXED in 0.66.0** — **A5. MCP advertises a POSTing flow as `readOnlyHint=True`.** *(high, `mcpserver/server.py:119-129`, `:338`)*
Write detection keys off `step.mutating`, which the engine's own comment (`flow.py:192-194`) says misses formless JS POSTs. The correct signal (`performed_write`) is computed at learn time and thrown away. **Failure:** a bland-named button whose handler POSTs is learned as a read, bulk-approved by `flow approve --all`, and served without `--expose-writes`. An untrusted outer agent fires an irreversible POST on every call — no write prefix, no confirm, no key, no mutex, no ledger. `record()` refuses the identical flow; only `learn()` caches it.

✅ **FIXED in 0.66.0** — **A6. Self-heal can bind a write target and persist it as non-mutating — double-submitting forever.** *(high, `flow.py:1012-1057`)*
`_maybe_heal` guards on the *recorded* flag only; the action the heal model returns is never classified. The sibling replan path guards this exact risk twice. **Failure:** an unapproved read flow (the configuration the docs recommend for unattended self-heal) re-grounds a drifted link onto a submit button. The POST fires, navigation makes `state_changed` trivially true so the heal is "good," the submit button is cached with `mutating=False`, and every subsequent 0-LLM replay re-fires it ungated and un-deduped. Full chain reproduced through the public `run_cached`.

✅ **FIXED in 0.66.0** — **A7. A healed `type` step types the model's value, not the caller's — and burns the row's idempotency key.** *(high, `flow.py:839` vs `:1031-1056`)*
`_maybe_heal` is never given `params`. **Failure:** `params={"qty": 500}`, the field drifts, the model types `3`. The POST body carries `qty=3` while the Idempotency-Key on the wire is the one minted for row 500 — so a later legitimate replay of row 500 mints the same key and is **deduped away by the backend**. A silently wrong write plus a silently suppressed one. Reachable only via the raw exported `run_cached`/`run_many`; every product wrapper is guarded today, but the guard lives in the wrapper, not the mechanism.

✅ **FIXED in 0.66.0** — **A8. The whole-flow confirm has no before/after baseline, so a write that never fires reads as "confirmed."** *(high, `flows.py:577-592`)*
The per-write barrier snapshots pre-state; the whole-flow one — the only barrier a single-write flow has — does a bare post-hoc check. **Failure:** a JS-only regression stops the POST, the DOM is unchanged so the mutation gate passes, and a persistent banner from a previous order satisfies the confirm. Under `run_batch(resume=...)` the un-landed row is written to the ledger as committed and permanently skipped. `ledger.py:11` claims "never a false skip of an un-landed write."

✅ **FIXED in 0.66.0** — **A9. A commit behind a `method=get` (or method-less) form is learned as non-mutating.** *(high, `safety.py:89-93`, `flow.py:290`)*
`<form onSubmit={...}>` with no method attribute is the ubiquitous React shape. **Failure:** learn caches the commit with no gate, no `precond_scope`, no Idempotency-Key; under form drift the write re-fires blind instead of failing loud. `flow inspect` shows a write flow with zero write steps. The recorder has this exact patch (`recorder.py:345`) and is test-pinned; `learn()` has no counterpart.

✅ **FIXED in 0.66.0** — **A10. An MCP write flow with no mutating step gets zero idempotency keys — so the retry-dedupe and the ledger both no-op.** *(high, `flows.py:1453-1459`)*
**Failure:** a GET-link commit, correctly declared and exposed as a WRITE, called twice: both fired, no key on either, no ledger file ever created. The human elicit still fires each time, so it is not fully silent — but the documented "machine correctness floor" is simply absent, and the only signal is an unexplained empty key list in the preview.

✅ **FIXED in 0.64.0** — **A11. A pinned string read on a blank element returns `""` as the answer, and all three value gates pass.** *(high, `pin.py:59-66`)*
`_parse` is strict for `int`/`float` and short-circuits for `str`. **Failure:** an SPA skeleton box is laid out but unpopulated, so `read_pin` returns `""`, `found=True`, `pinned=True`, and shape/contracts/magnitude all report clean. A scheduler records an empty shipment status as a clean 0-LLM success. A pin can never be created for an empty value, so `""` on replay is *always* drift. One-line fix; the list analogue of this hole was already closed.

✅ **FIXED in 0.65.0** — **A12. A corrupt or torn meta sidecar silently resets the whole trust state.** *(high, `flows.py:479-501`; also filed by the data lens)*
`except Exception: return FlowMeta()` drops quarantine, contracts, shape, `steps_hash` and `read_pin` in one step, with no log. `_save_meta` never `fsync`s (unlike the ledger, which does), and the meta is the hot file rewritten on every replay — so ultracua's own write path can produce the corruption. **Failure:** a flow quarantined for returning a wrong value returns that value again as a clean success on the unapproved read path; `health()` reports `never-run`; the next run overwrites the evidence permanently. This is also the one place inviolable (1) frays: the wiped `read_pin` puts the LLM extractor back on a replay that was pinned 0-LLM. The ROADMAP names this as a prerequisite for H9; it was implemented for the neighbouring branch only.

✅ **FIXED in 0.64.0** — **A13. Dropping the entire slot table skips re-approval.** *(medium, `flows.py:1366`)*
The gate reads `spec.slots and ...`; the contracts gate one line below carries the comment explaining why that short-circuit must not exist. **Failure:** an approved write flow's slot table is removed from the spec JSON; a scrubbed secret step then types the **empty string** into the credential field before the submit, and the write mints a different Idempotency-Key than the same logical write did while slotted. Removing one slot correctly refuses; removing all of them passes. One-word fix.

✅ **FIXED in 0.65.0** — **A14. A write flow's extraction discards `found`/`error`/`truncated`.** *(medium, `flows.py:582-591`)*
**Failure:** the confirmation panel renders lazily, the extraction fails, and `replay()` returns `{"status": "confirmed", "data": None}`. A caller logging that number records a null against a real order. Only on the `record()`-authored path, which is the path the GUIDE mandates for writes — the `learn()` path is accidentally saved by the shape gate.

---

## Group B — confidentiality, not wrongness (different axis, fix soon)

✅ **FIXED in 0.67.0** — **B1. Demo-typed passwords land in the flow cache on disk in plaintext, and `flow inspect` prints them.** *(high, `recorder.py:279/310/679`, `cache.py:236`, `cli.py:301`)*
The capture listener excludes only checkbox and radio; `input[type=password]` is stored with `el.value`. Reproduced: file mode `0o666`, plaintext password on disk, and `flow inspect` echoed it verbatim. `flow approve-all` reprints it fleet-wide, and replay re-types the frozen password every run. The recorder's own comment identifies this exact string as "a password / token / PII" and redacts it — but only from the LLM captioner, not from disk. A second variant: `record_demo` caches the un-scrubbed flow *before* the secret scrub runs, and when `writable_slots` is absent the corrective re-put never happens, leaving the plaintext permanently.

✅ **FIXED in 0.67.0** — **B2. The observation sent to the LLM carries every input's plaintext value, including passwords and resolved secret slots.** *(medium, `snapshot.py:117` → `providers/llm_agent.py:71`)*
Confirmed by probe: the rendered prompt contained `value="hunter2-BANK_TOKEN-SECRET"`. Off the 0-LLM path (needs a heal or suffix-replan while the secret is still on the page), but `SlotSpec` says "NEVER passed in params, logged, or serialized," and `Observation` is documented as "sanitized." It is not.

---

## Group C — loud failure or reporting completeness (schedule or accept)

✅ **FIXED in 0.65.0** — **C1. `dry_run` doesn't name the migration stale-approval gate it skips.** *(medium, `flows.py:1526`)* An artifact for a flow approved by a pre-0.60 version is byte-identical to one for a fully-blessed flow (`gates_skipped=[]`), while the real preflight refuses it. The dry run is strictly more permissive than the thing it previews. `health()` implements the correct predicate one file over; `dry_run` drops the `is None` disjunct.

✅ **FIXED in 0.65.0** — **C2. A failed re-learn reports success off the pre-existing flow and wipes its baselines.** *(medium, `flows.py:814`)* `cached = cache.get(key) is not None` instead of the correct `report.extra["cached"]` flag the engine already computes. Best-of-N short-circuits to one sample, no "nothing was cached" warning is printed, and `read_pin` is cleared even on approved flows. ~~No wrong data reaches a replay consumer, so this is authoring hygiene rather than a wrongness break.~~ **This last sentence was WRONG** (2026-08-01): the rejected attempt's `read_pin` is re-bound onto the APPROVED flow, which still walks its OLD steps to the OLD page — where that pin resolves to a different number. Reproduced returning `7` for a truth of `42`, with `approval_stale=False`, because the steps never changed and so the approval gate structurally cannot see it. Corrected to **high**.

---

## Lenses: none clean

All six — locators, writes, trust, surfaces, authoring, data — returned surviving findings. Counts: locators 4, writes 3, trust 3, surfaces 2, authoring 4, data 4.

What *is* clean and worth saying: inviolable (1) survived intact (modulo A12's `read_pin` wipe). The refutation pass killed one finding entirely and cut several sub-claims out of survivors, so this list is not inflated. Notably, the LLM-re-drive escalations claimed in two findings were correctly refuted — `on_drift="relearn"` really is refused for write flows, and that guard holds.

---

## Recommended next action

**Ship one "wrongness" release before anything else.** The cheap half of Group A is disproportionately valuable: **A3, A4, A11, A13, A10 are one-line or one-word fixes** — roughly half a day together — and they close a substring wrong-bind on every flow, a header clobber on every write, a wrong pinned read, an approval bypass, and a missing dedupe floor. Do these first.

Then, in order:

| Item | Fix | Size |
|---|---|---|
| A11 pin empty string | return `None` for empty `str` pin | 30 min |
| A13 slots gate | drop `spec.slots and` (2 sites) | 15 min |
| A4 headers | send the union; restore base in `finally` | 1 hr + 2 tests |
| A10 keyless MCP write | refuse loudly when a declared write plans 0 keys | 1–2 hr |
| A14 write extract | propagate `found`/`error`/`truncated` | 1 hr |
| A3 `role+name~` | demote to Tier 2; re-run the drift corpus | 0.5 day (bench dominates) |
| A1 row anchor | exact/identity match, not prefix; regression test | 0.5 day |
| A6 heal write-gate | reuse `classify_mutation` + `block_mutations` from the sibling path | 0.5 day |
| A7 heal + slots | refuse to heal a slotted step in the mechanism, not the wrapper | 1 hr |
| A9 GET-form learn | thread `write_flow` into `_author_steps`; reuse the recorder's patch | 0.5 day |
| A8 Phase-D confirm | snapshot pre-state; require the transition | 0.5 day |
| A12 meta durability | fail closed on unreadable; `fsync` in `_save_meta` | 2–3 hr |
| C1 dry_run gate | mirror all three preflight arms | 30 min |
| C2 re-learn flag | thread `report.extra["cached"]` into `LearnResult` | 2 hr |
| A5 MCP readOnly | persist `performed_write`; migrate existing caches | 1 day |
| B1 secrets at rest | password sentinel in capture + single `cache.put` in `record()` | 1 day |
| B2 prompt secrets | mask at `SNAPSHOT_JS`; thread `_secret_values` into replay | 0.5 day |
| A2 doc-class writes | capture-phase `submit` listener + reconcile document POSTs | 1–2 days |

Total: roughly 8–10 engineering days, of which the first half-day buys the largest risk reduction.

**One structural observation worth acting on beyond the individual fixes.** Five of these are cases where the correct guard *already exists elsewhere in the codebase* and was simply not applied to the sibling path: A6 vs the replan path, A9 vs the recorder, A1/A3 vs the hardened `heading`/`label` sources, A12 vs `load_history` and `cache.get`, A7 vs the wrapper guard. The recurring shape is **a guard placed in a wrapper or on one authoring path instead of in the mechanism**. Consider a targeted pass to push those guards down to the mechanism level; it would have prevented most of this list and will prevent the next one.

---

# Round 2 — the 2026-08-02 re-audit of everything written since v0.63.0

**Why a second audit.** The first one was a snapshot of v0.63.0. Fixing all 20 of its findings added
~1059 lines of source across 13 files — flows.py +409, flow.py +191, browser.py +120, locators.py
+116, recorder.py +98 — and none of it had ever been audited. Fix code written under adversarial
pressure is not automatically safer than the code it replaced.

**Method.** Six lenses over `git diff 344370b..HEAD -- src/` only, then EVERY finding handed to an
independent refuter told to kill it and required to reproduce it with its own probe. 14 were raised,
3 refuted outright, 11 survived — 10 distinct (`run_all` was found twice, by the trust and surfaces
lenses independently).

**The result is uncomfortable and worth stating plainly: most of these are holes in the FIXES, not
in the old code.** The pattern CLAUDE.md names — a guard applied to one path and not its sibling —
struck the very changes written to prevent it. The sharpest case: `is_write_flow` was extracted
specifically to stop the write predicate existing in triplicate, and left a FOURTH copy in
`run_all`, the unattended cron driver.

**A1 IS NOT FIXED.** The two criticals are both in the 0.64.0 row-identity guard. It asks whether a
token EXISTS somewhere in the DOM, never whether the element it binds belongs to the recorded row —
so the original wrong-row write comes straight back. HEALING.md currently claims these rows are
protected *because* they carry a token; that claim is false and must be corrected with the fix.

**R1 + R2 fixed in 0.71.0.** The row guard is now a CONTAINMENT check applied to the element
actually bound, at the single place every tier funnels through (`resolve` wraps `_resolve`, which
has five returns). `rowIdOf` now prefers an href/action over a `data-*`, and accepts a `data-*` only
when no sibling row carries the same attribute+value. Measured FREE on the drift corpus — survival
curve, bound_by histogram, mechanism rates, silent_wrong and predicted_agreement all byte-identical
to baselines/drift_v2.json, invariants ALL HOLD.

**A caution from building it.** The first version of this fix built a CSS selector from the
attribute value and mis-escaped the character class. The JS threw, `_bound_row_id` swallowed it and
returned "", which never matches — so every row anchor refused. The auditors' probes still showed
"refuses, no POST" and looked like a pass: THE FIX APPEARED TO WORK FOR THE WRONG REASON. Only the
drift bench caught it (8 rows of survival lost, `invariants: FAILED`). Both sides now compare
attributes directly, with no selector escaping anywhere. If you touch this guard, re-run the bench —
a probe that only exercises the refusal path cannot tell a working guard from a broken one.

**R3–R10 fixed in 0.72.0.** `run_all` routed through `is_write_flow` and now skips an UNDECLARED
write whatever `--include-writes` says (there is no confirm barrier for one, so it cannot be
verified). The heal's wire guard WAITS via `expect_request(write_settle_ms)` like both siblings it
mirrors. Write attribution became a pure `_write_owner` rule: more than one live candidate is
undecidable, so nobody is credited and the flow fails loud, exactly as the recorder's
`attributedSeq` already does. A new `landed` flag on the error taxonomy arms the ledger for the one
case the code POSITIVELY knows committed (`WriteReadbackError`) and only that one. A pre-true
confirm is now `WriteUnverifiedError` — non-retryable, NOT `landed`, and it says the commit
actuated. The extractor's page text is scrubbed, and `redact` now covers all five rendered fields
(`url`, `title`, `text`, element `name` and `value`) with the fingerprint computed BEFORE redaction
so it cannot manufacture drift. A transient meta read retries and never destroys a healthy sidecar.

**Residual on R5, stated rather than closed.** `record()`'s confirm probe still covers the ENTRY
page only; replay probes immediately before the first mutating step, which in a multi-page flow is a
different page. Probing the true pre-commit page at authoring time needs the recorder to evaluate
the confirm at each commit — a real slice, not a comment. The runtime backstop
(`WriteUnverifiedError`) is what actually holds, and it now says the commit actuated and that re-
running is not the remedy.

## Findings

### ✅ FIXED in 0.71.0 — R1. The row-identity gate checks that a token EXISTS somewhere in the DOM, not that the element it binds is in the recorded row — so a hidden row, or a surviving row whose own control is gone, still binds a different customer's Cancel

*critical, lens `locators`, inviolable #2, reproduced by an independent refuter*

**Where.** `src/ultracua/locators.py:427-450 (the `if spec.anchor_source == "row" and spec.anchor_id:` block, evaluated BEFORE Tier 1)`

**Mechanism.** The 0.64.0 guard is an independent existence query: `document.querySelectorAll(_LANDMARKS).some(c =>
identity(c) === want)`. It never relates the surviving row to the element resolution actually
returns, and it never requires that row to be visible. Tier 1 then runs unchanged and returns the
first count==1 match outright. Any drift that decouples "the token still exists in the DOM" from
"the control I bind belongs to the recorded row" reintroduces A1 verbatim: (B) the recorded row
survives but its own control is gone — the ubiquitous shape where an already-cancelled order renders
a `Cancelled` badge instead of a link; (D) the row is HIDDEN rather than removed (`display:none` /
`[hidden]`), the standard SPA client-side delete. In (D) the asymmetry is exact: `querySelectorAll`
sees the hidden row, while `get_by_role` excludes it from the a11y tree, so hiding the recorded row
is precisely what makes the SIBLING's control uniquely matchable. Downstream there is nothing left:
flow.py:947 resolves with `unique=True` and flow.py:952-954 compares `scope_fingerprint` of the
BOUND element, and per-row `<form>`s are structurally identical, so the fingerprint matches
byte-for-byte and the write is waved through with an Idempotency-Key minted for the recorded step.
The documented residual in HEALING.md:271-273 is only "when a row offers *no* such token" — every
probe below has a genuine, unique token (`id:order-3`), i.e. these are exactly the rows the doc says
are protected: "captures a row identity … and **fails loud when that row is gone**". Nothing in
tests/ or benchmarks/ references `anchor_id` (grep: no hits), and the corpus's only row fixture
(`benchmarks/drift_fixtures.py:_cart_rows`) happens to sit in the one shape where the gate works,
which is why drift_bench cannot see this.

**Failure.** A learned "cancel order #3" flow replays the day after ops cancelled #3 by hand (row now shows a
`Cancelled` badge) or after the UI filtered it out with display:none. resolve(unique=True) binds row
#7's Cancel button (`bound_by='role+name'`), the mutation gate's precond_scope matches
byte-for-byte, POST /cancel/7 fires under an Idempotency-Key minted for row #3, and `_replay_step`
returns ok=True. Globex's order is cancelled, Acme's is not, and the run reports success. Zero LLM
calls, zero signal.

### ✅ FIXED in 0.71.0 — R2. `rowIdOf` returns the first `data-*` attribute whatever it means, and PREFERS it over the identity-bearing href in the same row — a shared or positional data-* silently turns the row-identity gate into a no-op

*critical, lens `locators`, inviolable #2, reproduced by an independent refuter*

**Where.** `src/ultracua/locators.py:114-127 (`rowIdOf`, and the identical precedence re-implemented in the presence query at locators.py:430-444)`

**Mechanism.** The stated preference order is "an explicit id, any data-* value, or the first href/action inside
it", and the comment calls the result "A row's stable identity". But `for (const a of c.attributes)
if (a.name.indexOf('data-') === 0 && a.value) return ...` accepts the first data-* in attribute
order with no test that it distinguishes one record from another — and it returns BEFORE the
`querySelector('a[href], form[action]')` branch that would have produced a real identity. Two
universally common shapes defeat it: a design-system table stamping the same
`data-testid`/`data-component` on every row (the captured identity is then present on every
sibling), and a positional `data-index`/`data-row` that renumbers when a row is deleted (the
identity survives the record). In both cases `row_present` is trivially true, Tier 1 binds the
surviving sibling's control outright, and — per the probe — the mutation gate's scope fingerprint is
byte-identical. This is strictly worse than the token-less residual HEALING.md:271-273 admits:
there, no token means no claim of protection; here a token exists, the guard runs, reports
satisfied, and protects nothing. The A' control in the same probe run proves the identity WAS
available in the very same row (`href:/cancel/3`, which refuses correctly) and was passed over for
the shared `data-testid`.

**Failure.** An orders table rendered by any component library that tags rows `data-testid="order-row"` (or keys
them `data-index`). The recorded row #3 is deleted; the guard finds `data-testid:order-row` on row
#7, passes; role=link/button name="Cancel" is now unique; POST /cancel/7 fires with row #3's
Idempotency-Key; the flow returns confirmed. Identical harm to A1, on a page whose rows a maintainer
would inspect and correctly conclude do carry an identity token.

### ✅ FIXED in 0.72.0 — R3. The heal's new wire guard (A6 fix) has no settle window — a 25 ms deferred POST walks past it, and the write control is then cached as a read and re-fired ungated, un-keyed, on every 0-LLM replay

*high, lens `writes`, inviolable #3, reproduced by an independent refuter*

**Where.** `src/ultracua/flow.py:1173-1214 (GUARD 2 in `_maybe_heal`; the `wrote["hit"]` check at :1193 and the listener teardown at :1209)`

**Mechanism.** Guard 2 attaches a `request` listener, calls `session.act(action)`, and reads `wrote["hit"]` the
instant `act` returns (plus one `session.snapshot()` for a click), then removes the listener in the
`finally`. Both sibling guards it claims to mirror DO wait for the write to leave the browser:
`_author_steps`' watcher keeps its act window open through the verify snapshot PLUS a
`settings.write_window_ms` (2000 ms) grace tail — its own comment says "a write's POST can race the
post-act navigation and land just after the verify snapshot returns" — and `_replay_step` wraps
every mutating actuation in `page.expect_request(is_write_request, timeout=write_settle_ms)` (1000
ms). The heal got neither. Any click handler that defers its POST past the guard's zero-width window
(a debounce, an `await` before the fetch, an autosave tick) is invisible to it. `classify_mutation`
(Guard 1) cannot see a formless fetch-POST behind a bland name — that is exactly why Guard 2 exists
— so nothing catches it: `step.locator` is repointed at the write control, `mutating` stays False,
and the repaired recipe is written back by `_replay`'s `if dirty and success: cache.put(flow)`. This
is the same guard-on-one-path-not-its-sibling pattern CLAUDE.md names, one level inside the fix for
A6.

**Failure.** An UNAPPROVED read flow in auto/repair mode (the configuration the shipped A6 test itself calls "the
configuration the docs recommend for unattended self-heal"). The recorded link drifts; the heal
re-grounds onto a bland `Continue` button whose handler does
`setTimeout(()=>fetch('/save',{method:'POST'}),25)`. The heal is judged good, `step.locator` becomes
'Continue', `mutating=False` is persisted — and every subsequent PURE 0-LLM replay (no provider at
all) fires the POST with NO Idempotency-Key, no mutation gate, no precondition, and returns
`success=True`. `is_write_flow` still reports False, so MCP would advertise it `readOnlyHint=True`
and `run_batch` would treat it as a read batch. Inviolables #2 and #3 both. Exactly the A6 harm
chain that is marked FIXED in docs/open-defects.md, and not recorded as a residual anywhere.

### ✅ FIXED in 0.72.0 — R4. `_author_steps`' wire-write promotion credits the WRONG step: the real commit caches ungated and un-keyed while a benign neighbour gets the Idempotency-Key

*high, lens `writes`, inviolable #3, reproduced by an independent refuter*

**Where.** `src/ultracua/flow.py:357 (`cur["i"] = i`, never cleared) with the promotion loop at src/ultracua/flow.py:427-438`

**Mechanism.** `cur["i"]` is set just before each act and is only ever overwritten by the NEXT act.
`_in_act_window()`'s grace tail keeps attribution correct only while `cur["i"]` still equals i; the
moment step i+1 opens its window, any still-in-flight write from step i is added to `wrote_by_step`
as i+1. The promotion loop then flips `mutating` on step i+1 and leaves step i as recorded. Because
`any(s.mutating)` is now True, `flows._learn_once`'s new "a write fired on the wire but no step
could be attributed to it" refusal (flows.py:1285) is disarmed — it has the all-or-nothing shape the
recorder's own per-(method,url) COUNT reconciliation was rewritten to escape, and its comment even
names that masking class. Downstream, `_replay_step` sets the Idempotency-Key and runs the mutation
gate on the wrong step; the real commit replays with neither, and is heal/suffix-replan eligible
because `not step.mutating`. `_replay`'s A8 baseline (`if pre_write is not None and step.mutating`,
flow.py:762) is also probed before step i+1 — i.e. AFTER the real write already fired — which
contradicts its own stated invariant that "the baseline must be the state before ANY write".

**Failure.** A learn pass over a page where step 0's bland `Continue` button debounces its POST and step 1 is a
benign non-navigating click. The recipe caches with step 0 `mutating=False` and step 1
`mutating=True`. On replay the real commit fires with no Idempotency-Key and no precondition check
(so a retry or a resumed batch double-submits, and under form drift it fires blind instead of
failing loud), while the key that IS minted rides a request that writes nothing — and
`preflight_keys` / the RunLedger therefore key the row on a step that never commits. The exposure
window is (time from step i's act to step i+1's act), so it is wide with a fast/local provider or a
grounding model and narrow behind a slow LLM.

### ✅ FIXED in 0.72.0 — R5. A8's authoring-time confirm probe checks the ENTRY page while replay's baseline checks the PRE-FIRST-WRITE page — `record()` blesses a flow whose every replay fires the write and then reports it unconfirmed

*high, lens `writes`, inviolable #2, reproduced by an independent refuter*

**Where.** `src/ultracua/flows.py:2860-2880 (`_probe_confirm`, passed as `record_demo(prepare=...)`) vs src/ultracua/flow.py:756-764 (`pre_write` invoked before the first `step.mutating`)`

**Mechanism.** `record()`'s refusal probes `spec.mutate`'s confirm immediately after navigating to `start_url`,
before the demo. `_replay` probes the same condition immediately before the FIRST MUTATING STEP — in
any multi-page flow, a different page and a different URL. The authoring-time check therefore cannot
predict the runtime baseline it exists to predict; its own comment claims it fires "rather than
caching a recipe that can never confirm", and it does not deliver that. A confirm signal that is
absent at entry but already true on the page where the commit happens (a `confirm_url_contains`
covering the checkout section, a status/summary region on the review page, or a persistent artifact
of a PRIOR run that is visible pre-commit) passes `record()` and then fails `_make_finalize`'s `(not
pre) and condition_present(...)` on every single replay — after the POST has already left.

**Failure.** `FlowSpec(start_url='/', mutate=MutateSpec(confirm_url_contains='/review'))` over `/` -> `/review`
-> POST `/review/place`. `record()` caches it with no note. Every `replay()` then POSTs the order
and raises DriftError saying the confirm was already true. The error text tells the operator to
re-record and says nothing about the write having landed; `_record_run(ok=False)` marks the flow
failing. Under `run_batch(resume=...)` the row is never ledgered, so a resume re-fires it — defended
only by the frozen Idempotency-Key, i.e. only if the backend honours it. This is the exact inverse
of the false-skip A8 closed, and note the shipped control test
(test_a_write_that_does_fire_still_confirms) only passes because its fixture sets
`state['no_banner'] = True` to scrub the confirm artifact off the entry page on every later GET.

### ✅ FIXED in 0.72.0 — R6. `run_all` is the FOURTH transcription of the write predicate and was never converted to `is_write_flow` — the unattended "read flows only" fleet fires undeclared writes and reports green

*high, lens `trust`, inviolable #3, reproduced by an independent refuter*

**Where.** `src/ultracua/flows.py:2214 (`if spec.mutate is not None and not include_writes:`), against the new `is_write_flow` at src/ultracua/flows.py:1286 and the new `_author_steps` wire-write promotion at src/ultracua/flow.py:413-472`

**Mechanism.** The new `is_write_flow` docstring names exactly three former transcriptions it now replaces —
`mcpserver._is_write_flow`, `run_batch`, `cli._flow_approve_all` — and all three were converted.
`run_all` has a fourth, and it was not: it still gates only on `spec.mutate is not None`, so an
UNDECLARED write (spec.mutate=None, cached step `mutating=True`) is not skipped by
`include_writes=False`. This diff is also what CREATES that population at scale: `_author_steps`'s
new promotion loop writes the wire watcher's evidence back onto the step
(`steps[p].model_copy(update={"mutating": True, ...})`), so a formless JS fetch-POST / method-less
`<form onSubmit>` behind a bland button name now caches `mutating=True` on a flow learned as a read.
flow.py's own accepted-cost comment states the escape hatch is that such a flow "is refused from MCP
and `run_batch` until a human declares it" — it does not mention `run_all`, and `run_all` does not
refuse it. Downstream, `_preflight_row` computes `is_mutate = spec.mutate is not None` → False, so
there is no confirm-required gate and no UnkeyedWriteError; `_make_finalize` takes the `spec.extract
is None` branch and sets `out["found"]=True` unconditionally ("navigate-only flow: reaching the end
IS success"), so the run is reported ok whether or not the write landed. `FlowHealth` carries no
write indicator and `cli._flow_approve` (approve --name) prints no steps, so nothing on the approval
or status path warns either.

**Failure.** An operator learns a flow that reads an admin panel; the page's "Load report" button fires
`fetch('/report',{method:'POST'})`. The promotion caches the click as `mutating=True`; `spec.mutate`
is None. They `flow approve --name report` (no step listing, no write warning) and point cron at
`ultracua flow run-all` — documented as "read + approved by default", `--include-writes` NOT passed.
Every tick replays the flow and re-POSTs to /report, with no write-landed confirm, no
`--include-writes` consent, and the CLI printing `[OK] report`. A failed write is indistinguishable
from a landed one. `run_batch` refuses the identical spec+cache; MCP would refuse to expose it; only
the unattended cron surface runs it.

### ✅ FIXED in 0.72.0 — R7. The LLM extractor gets RAW page text — `redact` never reaches it, so a resolved secret is shipped to the model on an ORDINARY replay

*high, lens `secrets`, confidentiality (no inviolable), reproduced by an independent refuter*

**Where.** `src/ultracua/flows.py:749 (read branch) and src/ultracua/flows.py:711 (write readback) — `text = await session.page.inner_text("body")` -> `extract(router, …)``

**Mechanism.** The 0.67.0 fix routes secret scrubbing through `snapshot.capture(redact=…)` and claims (commit
58355b3, snapshot.py:253-258) that this is "the one place" because "every snapshot -> LLM path
(heal, suffix-replan, the learn loop) runs through here". `_make_finalize`'s extractor is a
page-text -> LLM path that does NOT run through it: it calls `session.page.inner_text("body")`
directly and hands up to 12000 chars to `extract()` -> `tool_extract()` ->
`router.complete(tier="strong")`. `session.redact` / `_secret_values(spec)` are never consulted.
This is the classic sibling gap and the guard exists 500 lines away in the same file:
`flows._capture_audit` (flows.py:1252-1254) passes `page_text=report.final_text` with
`redact=_secret_values(spec)` into `audit.capture`, which does `_redact(_sanitize(page_text),
redact)` (audit.py:234) — i.e. the identical page-text channel is scrubbed before it hits DISK but
not before it hits the MODEL. Unlike the heal/replan paths, this needs no drift: every replay of a
read flow without a resolved `read_pin`, and every write flow with an `extract` readback, makes this
call.

**Failure.** A flow declares `SlotSpec(secret=True, secret_env='ULTRACUA_API_TOKEN')` and `extract="the API key
shown on the page"` (or any read flow on a page that echoes a token/OTP/reset code as visible text —
an API-keys settings page, a "code 123456 accepted" banner, an error that reprints the submitted
value). `flows.replay(spec)` runs 0-LLM through the steps, then the finalize extractor sends the raw
body text — plaintext credential included — to the configured third-party provider. `obs.text` for
the same page reads `[REDACTED]`, so the operator has every reason to believe the redaction covered
it. Nothing logs, warns, or records that a secret left the machine.

### ✅ FIXED in 0.72.0 — R8. A write the code KNOWS landed is never recorded in the retry-dedupe ledger when `WriteReadbackError` fires — and the CLI then prints a resume command that re-fires it

*high, lens `surfaces`, inviolable #3, reproduced by an independent refuter*

**Where.** `src/ultracua/flows.py:1870-1877 (the `write_unreadable` branch in `replay`), consumed at src/ultracua/flows.py:2440 (`run_batch`'s `except FlowReplayError`, which skips the `ledger.record` at 2437-2439) and src/ultracua/mcpserver/server.py:277-279 (which skips `ledger.record` at 282-283); advice printed at src/ultracua/cli.py:845-848`

**Mechanism.** The new `WriteReadbackError` documents itself as "**Emphatically NOT retryable**: the side effect
has already happened, so re-running would double-submit (inviolable #3)... never to re-run."
`replay` handles it by `_record_run(cache, key, ok=True)` + raise — the fleet-health channel was
updated so a failure streak would not invite a retry, but the actual retry-dedupe rail on both write
surfaces, `RunLedger`, was not. Both surfaces record only on the success return path
(`data.get("status") in ("confirmed","already-done")`), and the raise bypasses it. So the one case
where the process is alive and positively knows the write COMMITTED is the one case the ledger is
not armed for — unlike the crash windows `ledger.py` explicitly excuses. On MCP that defeats the
rail's stated job verbatim: "a client timeout retry must not double-write". On the CLI it is worse
than passive: `_flow_run_batch` prints "to resume the rows that DIDN'T commit: ... --resume <job>"
for a row that DID commit, so the operator is instructed to re-fire it.

**Failure.** A 200-row `flow run-batch --commit` pays invoices. Row 7's payment posts and its confirm banner
shows, but the confirmation-number readback misses (a layout change moved the reference below the
extractor's cut). The row is marked FAIL, no ledger line is written, the batch stops, and the CLI
prints the resume command. The operator runs it; row 7's payment is submitted a second time. If the
payee's backend does not honor `Idempotency-Key` the invoice is paid twice; if it does, the
duplicate is suppressed but the whole batch's ledger is now permanently blind to row 7. On the MCP
surface the same state re-elicits a human confirm and re-fires on the outer agent's retry.

### ✅ FIXED in 0.72.0 — R9. `snapshot.capture(redact=…)` scrubs 2 of the 5 Observation fields the prompt renders — `Element.name`, `obs.url` and `obs.title` go to the model unredacted

*medium, lens `secrets`, confidentiality (no inviolable), reproduced by an independent refuter*

**Where.** `src/ultracua/snapshot.py:263-270 (the redact loop; `url`/`title` are assigned at :271-272, AFTER it)`

**Mechanism.** `capture()` replaces the redact terms in `e.value` and in the page `text` blob only.
`providers/llm_agent._render` (llm_agent.py:54-73) puts five page-derived fields in the user turn:
`URL:`, `TITLE:`, `PAGE TEXT:`, and per element `{e.name}` plus `value="…"`. Accessible NAME is
computed from an element's text content for links/buttons, so a page that renders a token inside a
clickable element (the standard "Copy sk-live-…" API-key UI, a `<td>` in a clickable row, an
`<option>` label) carries the plaintext in `e.name`; a token in a query string (magic link,
`?api_key=`, `?otp=`, a secret slot filled into a GET form) carries it in `obs.url`, which is
rendered in EVERY prompt. `types.py:42-46` was amended in this same diff to assert "SANITIZED is a
real guarantee, not a label" — the guarantee holds for two channels and silently fails for three.
The existing regression test picks a `<p>` and an `<input>` for its echo case, so the
interactable-element and URL siblings are untested.

**Failure.** A flow with a `$secret_env` slot drifts on step 3 and self-heals (`mode='auto'`, the exact path
`test_no_secret_reaches_the_rendered_prompt` covers), or is authored by `learn`. The snapshot masks
the value and scrubs the page text, then the very next lines of the same prompt read `URL:
https://app/settings?api_key=tok_live_51ABCDEF` and `[e0] button: Copy tok_live_51ABCDEF`. The
credential reaches the provider four times in one prompt while the audit trail records a clean,
redacted Observation.

### ✅ FIXED in 0.72.0 — R10. One TRANSIENT read error on a healthy meta sidecar destructively renames it and overwrites it with a quarantine — approval, contracts, shape, steps_hash and read_pin are permanently lost, and `release()`'s "re-arms under the SAME contracts" promise is then false

*low, lens `trust`, inviolable #2, reproduced by an independent refuter *(symptom real, stated cause corrected by the refuter)**

**Where.** `src/ultracua/flows.py:571-572 (`except Exception as exc: return _refuse_unreadable_meta(...)`) and src/ultracua/flows.py:533-551 (`_refuse_unreadable_meta` / `_preserve_corrupt`)`

**Mechanism.** `_load_meta` now treats ANY exception from `p.read_text()` as permanent corruption: it `os.replace`s
the file to `<key>.meta.json.corrupt.<ts>` and writes a poisoned
`FlowMeta(quarantine=meta_unreadable)` in its place. The catch is a bare `except Exception`, so a
transient IO failure on a perfectly valid file — on Windows the classic case is an AV/indexer
sharing violation (WinError 32) right after `os.replace`, or a network/removable-store hiccup — is
indistinguishable from a torn file. Before this change a transient read cost exactly one call and
the file stayed intact; now it is destroyed on the first occurrence. The sibling this diff wrote for
the OTHER sidecar got closer: `history.load_history` at least splits `FileNotFoundError` out from
`(OSError, ValueError)`; `_load_meta` splits nothing. The recovery path then breaks a second
promise: `release()`'s docstring says it "Re-arms under the SAME contracts — if the value is still
wrong the next run re-quarantines (no silent habituation)", but the poisoned meta has
`contracts=None` and `shape=None`, so after the documented `flow release` (+ `flow approve`, needed
because the poison also cleared `approved`) the flow runs with its H9 value contracts and shape gate
GONE and nothing reports their absence. `read_pin=None` likewise puts the LLM extractor back on a
replay that was pinned 0-LLM — the exact harm `_load_meta`'s own docstring cites as inviolable #1.

**Failure.** A scheduled `flow run-all` on Windows hits an AV sharing violation reading `<key>.meta.json` for an
approved, pinned, contract-guarded price-monitor flow. The healthy sidecar is renamed aside and
replaced with a `meta_unreadable` quarantine; every surface refuses. The operator follows the error
text (`flow release`), then `flow approve` to get past "not approved". The flow now runs green again
— but with `contracts=None`, `shape=None`, `read_pin=None`, and only `unapprove -> learn -> approve`
would ever re-seed them (a re-learn on an APPROVED flow deliberately preserves, i.e. keeps None). A
subsequent wrong value — the case the contracts exist to catch — is returned as a clean success, and
the pinned 0-LLM read is silently back on the LLM extractor.

## Refuted, and why — so nobody re-raises them

* **A failed or cancelled BrowserSession.close() skips browser.close(); with a driver_scope held, the Chromium is never reaped — unbounded, silent leak ac** (lens `concurrency`) — The skipped browser.close() is real, but the filed failure mode (unbounded, silent accumulation across a batch/fleet) does not occur — three guards kill it, and I measured all three through the REAL callers rather than a hand-written loop. GUARD 1 — src/ultracua/flows.py:2447-2450: run_batch's generic `except Exception` sets `stopped = True` UNCONDITIONALLY (not gated on on_row_error). A close() f

* **The two halves of commit 58355b3 disagree on what a credential IS: the recorder blanks `autocomplete=current-password / new-password / one-time-code`,** (lens `secrets`) — The predicate asymmetry is real but it is not a leak path; the guard that kills it is flows.py:2666 `_unbound_secret_steps` + `_refuse_unbound_secrets`, enforced at flows.py:3008 (write exit) and flows.py:3067 (read exit), feeding flows.py:1217 `_secret_values` -> `capture(redact=...)`. The finding's load-bearing premise is "a one-time code ... is never a $secret_env value, so _secret_values(spec)

* **Two of the four `run_cached` call sites never got `redact=` — latent today only because both happen to pass `provider=None`** (lens `secrets`) — The code fact is right (no `redact=` at flows.py:1766 and flows.py:3023) but the stated failure is structurally unreachable, and the reason is NOT `provider=None` — it is `mode="replay"`. GUARD 1 — flow.py:138: `heal_provider = provider if mode in ("auto", "repair") else None`. Both cited sites pass `mode="replay"`, so the finder's "one-line change" (adding a provider) is a NO-OP. Probe A ran exac

---

# Why a 757-test suite kept missing these — measured, 0.75.0

The obvious hypothesis was that the suite is weak. **It is not, and the measurement says so.**

**Mutation test.** Nine deliberate defects, each a class this project has actually shipped, each applied
in its own git worktree and run against the suite: the R3.2 refusal removed, the wire-vs-classifier
check dropped, `cache.get` failing open, the row-identity discrimination test removed, the
Idempotency-Key dropped on replay, the precondition gate disabled, `is_write_request` blinded, MCP
exposing unapproved flows, the promotion loop's silent drop. **All nine were caught**, most within a
targeted subset in under four minutes.

Two follow-ups also came back clean. Entry-point coverage is not the problem — 88 `run_cached` call
sites, 131 `replay`, 120 `record` across 25 files, so tests are not knocking on the wrong door. And an
AST scan that flagged three tests as "unable to fail" was WRONG: they call `_preflight_row` expecting it
not to raise, and mutating that function to always raise fails all three. The heuristic did not count
"a call that must not raise" as an assertion. Recorded because a wrong measurement asserted confidently
is the same failure mode this register keeps documenting, and it was made here.

## The actual answer

**Mutation testing can only probe guards that EXIST. Every defect this project has shipped was a guard
that was MISSING.** So a 9/9 mutation score is entirely consistent with the observed failure rate — the
two measure different things.

The suite is **regression-shaped**: one bespoke test per known defect, each asserting a specific
scenario. That makes it excellent at proving known bugs stay fixed, and structurally incapable of
failing for a bug nobody has thought of. The corroborating fact is stark: **~44 findings across three
adversarial audit rounds, and not one of them was discovered by the test suite.** Every one came from an
audit; the suite's role has been to hold the fix afterwards.

Four of the nine mutations were caught by exactly ONE test, three of those in the single file added with
the fix. So a guard is often only as covered as the one test written beside it — and if that test is
subtly wrong (one of them was: it passed against pre-fix source until corrected), the guard is
effectively uncovered.

## What was added, and the evidence it is worth having

`tests/test_write_safety_invariants.py` states inviolable #3 as a PROPERTY over a generated
cross-product (commit mechanism x position x classifier bait x classifier visibility, 24 cells), rather
than as 24 scenarios:

    If a POST reached the server while learning, then EITHER the flow was refused,
    OR the recipe has a gated step AND every POST on replay carries an Idempotency-Key.
    AND a commit the classifier can see for itself must remain learnable.

That second clause is not decoration. Without it the property is satisfied by refusing everything, which
is exactly the regression the first attempt at the R3.2 safety fix shipped.

**Value, measured against the real bug that attempt introduced** (refuse on `unattributed` alone, no
consistency check):

    existing write-safety files (54 tests)   ALL PASS
    the invariant matrix                     8 of 24 cells FAIL, naming the property and the shapes

The full suite did catch that bug — through `test_press_gate`, a login-flow file, seventeen minutes in,
as an opaque `assert learn.success`. The matrix catches it in under a minute with a message saying which
property broke and for which shapes.

## Two things this exercise proved about building the matrix itself

Both were caught by instrumenting the cells rather than trusting them, and both would have made the file
look thorough while testing nothing:

  1. The first draft gave the commit step the intent "place the order" — so `classify_mutation` gated
     every cell from the words alone and the classifier-blind case, where every real defect has lived,
     was never exercised.
  2. Varying only the intent was still not enough: the control's ACCESSIBLE NAME feeds the classifier
     too, so a button labelled "Place order" is gated however blandly the intent is worded.

**A matrix is only as good as the axis you forgot.** Print what each cell actually exercised before
believing it.

## How to use this file

When a defect is found in write safety, add a DIMENSION here rather than a new bespoke test. A failing
cell is a real defect: the flow either cached a write with nothing gated, replayed one un-keyed, or
refused a flow that must stay learnable.


# Round 4 — the 2026-08-04 pre-merge audit of the causal-attribution attempt (PARKED, not merged)

**Scope.** The uncommitted `feat/shared-causal-attribution` work (would-be 0.76.0): extracting the
recorder's `__ucturn` signal into `src/ultracua/attribution.py` and using it on the learn path to
ATTRIBUTE writes. Five lenses, 20 candidates, independent refuters per finding defaulting to REFUTE.

**The headline is the process fact, not any single finding.** The branch was GREEN — 785 tests,
`drift_bench` byte-identical, the flaky press-gate guard 10/10 clean — and the audit still found it
defective, twice over. **That is the third consecutive time a fully-green change in this exact area was
wrong**, after 0.73.0's drain (reverted) and 0.74.0's first refusal draft (over-refused, caught by the
suite only via an unrelated login-flow file). The work was PARKED rather than fixed-and-shipped; see
`docs/parked/README.md` and branch `feat/shared-causal-attribution`.

## Convergence, which is itself the signal

Five independent lenses landed on the same two lines. That concentration is why the parking decision
was easy: the defects were not incidental, they were the design.

    flow.py:462 (seq_to_step)         flagged 5x   3 critical, 2 high
    flow.py:542-543 (the REPLACE)     flagged 5x   3 critical, 2 high
    attribution.py:334 (no filter)    flagged 2x   1 critical, 1 high

## R4.1 — Telemetry beacons became "causal writes" and displaced the real gate — FIXED on the parked branch

*high/critical, confirmed 2/2 by execution.* `recordWire` marks every non-idempotent request regardless
of host, including `navigator.sendBeacon` (always a POST). `attribute()` counted them all. **Both
sibling consumers already filter** — `flow._watch_request` and the recorder's marker loop both call
`safety.is_write_request` — and the new one did not. The register's most-repeated shape, one more time.

Measured end to end, page with a GA beacon on step 0 and a genuine classifier-blind commit on step 1:

    0.75.0            steps [(0,'filter',False), (1,'continue',True)]   correct
    branch as written steps [(0,'filter',True),  (1,'continue',False)]  gate on the analytics click;
                                                                        real commit cached as a READ
    branch + filter   steps [(0,'filter',False), (1,'continue',True)]   correct

Strictly worse than the version it replaced. Fixed on the branch by filtering markers through
`is_write_request` inside `attribute()`, exactly as the siblings do.

## R4.2 — The causal set REPLACED the temporal set, deleting gates the temporal rule had earned — FIXED on the parked branch

*critical, flagged by five lenses.* `wrote_by_step = set(causal_steps)` discarded temporal
attributions whenever the page named ANY cause. Combined with R4.1 a beacon was enough to make
`causal_steps` non-empty and wipe the real gate. Fixed by `_merge_attribution` — UNION, never remove.
Over-gating costs an unused Idempotency-Key; under-gating costs a double-submit.

## R4.3 — Cross-origin seq collision silently rebinds a gate — OPEN (parked)

*critical, flagged by four lenses.* `seq` is allocated in `sessionStorage`, which is **per-origin**, but
`seq_to_step` is one flat dict resolved at the end of the run. After a cross-origin hop the counter
restarts at 1, and a later step's commit silently overwrites an earlier step's entry — the write is
then credited to the wrong step. Structural fix: resolve markers to steps AT DRAIN TIME per batch,
never accumulate a global map. **Not attempted.**

## R4.4 — A swallowed drain rebinds a step's commits to the NEXT step — OPEN (parked)

*high, flagged twice.* `attribution.drain` returns `[]` on any exception (a navigation destroying the
execution context is the ordinary case). Markers left in the buffer are read by a LATER drain and bound
to whichever step drained them. Same structural fix as R4.3. **Not attempted.**

## R4.5 — A page-initiated synthetic click launders a deferred write into a confident attribution — OPEN (parked)

*medium.* The learn listener registers a commit for any click, including one the PAGE dispatches. A
deferred write whose handler synthesises a click can therefore acquire a fresh, confidently-attributed
turn. **In scope for slice S6** — it applies to the refusal-oracle design too, and needs a RED test.

## R4.6 — The invariant matrix asserts only that SOME step is gated, never that the gate is on the step that WROTE — OPEN

*medium, and the reason the matrix did not catch R4.1/R4.2.* `tests/test_write_safety_invariants.py`
checks `gated = [i for i,s in enumerate(flow.steps) if s.mutating]; assert gated` — satisfied by a gate
on any step, including one that issues no request. **Closed by plan slice S1** (Phase 1), before any
slice relies on the matrix as a gate.

## Also recorded

- **R4.7** *(low)* `recordWire` bypasses `pushRec`, so wire markers have no in-memory fallback despite
  the documented contract — on an opaque origin the commit records and the write does not.
- **R4.8** *(high, process)* Nothing in the suite pinned the AUGMENT-NEVER-REFUSE rule; the only test
  that would have caught its removal did so by winning a race. Pinned on the branch afterwards.
- **R4.9** *(medium)* `test_run_cached_refuses_it_too_not_only_the_flows_wrapper` no longer exercises
  its stated property after the tests were relaxed for the new behaviour.

## What this round changes about how the next attempt is run

The conservative half of R3.2 continues as plan slice **S6**: the causal signal used ONLY as a refusal
oracle — "a wire write occurred whose cause the page cannot prove" → refuse loudly — with no
attribution and no seq→step map, which removes R4.3 and R4.4 **by construction** rather than by
patching them. R4.5 remains in scope. Any future attempt to ATTRIBUTE (rather than merely refuse) must
start from R4.3/R4.4 and from `docs/parked/README.md`, not from the diff.

### ✅ AB-1 FIXED in 0.89.0 — and the "refusal oracle" framing above was measured WRONG twice

**The finding, reproduced and sharper than filed.** AB-1 is the MIRROR of the ordering
`test_a_keyword_mutating_sibling_no_longer_disarms_the_refusal` already covered. There the bait is
first and the commit second, so `acting_at_write` and `gated` disagree and the flow is refused. Turn it
around — the commit first, deferring FORWARD onto a classifier-mutating neighbour — and the two agree,
on the step that never writes. The check is satisfied and the recipe caches the Idempotency-Key, the
precondition and the drift gate on the wrong row. **The guarded ordering had a test; its sibling did
not.** That is this register's own most-repeated pattern, one more time.

**And the verdict is not even stable.** With a plain `setTimeout(..., D)` the same page gives three
different answers, decided by a stopwatch:

| defer | gate on the WRONG step | correct | refused |
|---|---|---|---|
| 60 ms | **6/8** | 2/8 | 0/8 |
| 150 ms | **2/8** | 0/8 | 6/8 |

`_write_owner` decides ownership from `write_window_ms` grace tails — a clock-based boundary, exactly
what R4.26 had just proved unsound one module over. The 2/8 "correct" runs are not correct reasoning;
they are the stopwatch getting lucky.

**TWO measurements redirected the slice, and both killed a version of it before it shipped.**

*The plan's refusal oracle cannot ship.* "Refuse any write whose cause the page cannot prove", measured
against the real capture script on ordinary READS issued over POST:

| read pattern | cause | naive S6 |
|---|---|---|
| sync query in click handler | PROVABLE | ok |
| **await token, then query** | unprovable | **refuses** |
| **debounced search** | unprovable | **refuses** |
| **deferred pagination** | unprovable | **refuses** |
| microtask continuation | PROVABLE | ok |
| **rAF-batched query** | unprovable | **refuses** |

4 of 6, including the commonest SPA data-fetching shape. `is_write_request` is method-based and a
GraphQL read IS a POST, so a read's cause is exactly as unprovable as a deferred commit's. That is D0's
regression one surface over — and measuring it BEFORE writing the refusal is the only reason it is a
paragraph here rather than a revert.

*Over-gating unconditionally cannot ship either.* The suite caught it in one run:
`test_the_ordinary_fill_then_submit_flow_still_learns` went red, because the agreeing arm is ALSO where
the ordinary write flow lives (a benign step, then a correctly-classified commit). From timing, that
and AB-1 are **indistinguishable** — which is what the code's own comment had said all along: it needs
the causal signal, not a better reading of the clock.

**What ships.** The two are separated by whether the PAGE can prove the write's cause — the ordinary
commit leaves inside its own click's dispatch, a forward-deferred one leaves in a bare task. So the
recorder's marker script is installed on the learn path too (ONE implementation, shared not
transcribed) and read by `_wire_writes_are_provable`, which is used ONLY as evidence about whether a
gate PLACEMENT can be trusted. No attribution, no seq→step map — R4.3/R4.4 stay removed by
construction. When the placement cannot be confirmed, every candidate row is gated instead of trusting
the agreement. It fails CLOSED: no markers, a cross-origin hop, an exception — all read as "not
provable", so the caller gates MORE.

**PRICED, not waved through.** Such a recipe becomes multi-write, so it loses the auth-refresh retry and
every gated step loses self-heal and suffix-replan. Recovery features, not the flow. Refusing would
have cost the flow — and, per the table above, a large read population with it.

Both halves are mutation-checked, because green does not say which line made it green: disable the new
branch → only the AB-1 test fails; make it unconditional → only the ordinary-write-flow test fails.

**A THIRD version was rejected before merge — and the evidence for WHY is weaker than it first looked;
read the confound before citing these numbers.** Sharing the recorder's script by injecting ALL of it on
the learn path passed **867 tests green** and came back
`invariants: FAILED -> ['ambiguous_disambiguated']` (heal-eligible 26→27), bench wall 105.3s → 162.5s,
suite 20:57 → 23:20.

**But the host was under heavy CPU load and near-100% memory for that whole window**, which the operator
flagged afterwards. Under swapping a heal can miss its budget and record `drifted` instead of `healed` —
which is exactly what that invariant reads — so **neither the wall times nor the invariant failure can be
cleanly attributed to the change.** A controlled A/B on the same host minutes later put the injection at
13.6s against 15.0/15.7s WITHOUT it, i.e. no measurable cost at all. Treat every timing figure in this
entry as unusable and the invariant failure as UNATTRIBUTED. **CI is the adjudicator here**, per this
repo's own standing rule that a local green is the weaker evidence.

The mode is kept anyway, on first principles rather than on those numbers: `store()` computes `specOf`
and JSON round-trips a GROWING sessionStorage array on every click, change, keydown and scroll, and the
learn path needs exactly ONE bit from this script. Doing all of that for one bit is wrong whether or not
a loaded laptop can measure it. So `window.__ucAttribOnly` is a MODE on the one script, not a second copy
of the write entry-point patches: in that mode `store` does the turn bookkeeping and returns before
`nextSeq` for any non-commit action, so scroll/type never touch sessionStorage and no step rows
accumulate. One implementation with a documented mode, rather than the transcription R3.1 was filed for.
With it: `invariants: ALL HOLD`, recovery-eligible 44, ladder 27/44, writes `double=0 wrong_target=0`.

**The transferable lesson survives the confound, and it is about instruments, not milliseconds:** a
change that alters what runs IN THE PAGE cannot be adjudicated by the suite, which is shaped to notice
neither a resolver regression nor a cost. Run `drift_bench` — and on a host whose load you know.

**A SEPARATE DEFECT MEASURED IN PASSING, not yet fixed — the wire promotion marks ordinary reads as
writes.** Learning twelve ordinary read controls that query over POST, all twelve cached as write
flows. Seven were the keyword classifier; the other five — `Filter results`, `Export CSV`, `Next page`,
`Refresh data`, `View details`, none of which trips a keyword — were the WIRE PROMOTION, from its own
log: `step 0 'go to the next page' wrote on the wire — caching it as a WRITE`. D0's text calls this a
"stated read-POST residual"; it is now 5/5 measured. It is the mirror of the keyword classifier's false
positives and it deserves its own filing.


# Round 3 — the 2026-08-03 re-audit of everything written since v0.70.0

**Scope.** `git diff 9d7de9c..HEAD -- src/` — 387 insertions across 6 files (flows.py +187,
locators.py +154, flow.py +76, snapshot.py +32, mcpserver +7). ALL of it fix code for rounds 1 and
2. The baseline is v0.70.0 rather than v0.71.0 because round 2 audited only up to 0.70.0, so the
row-identity containment guard — the fix for a CRITICAL — had never been audited.

**The number that matters more than any single finding.** Round 2 found 10 defects in ~1059 lines of
fix code. Round 3 found **11 in 387**, and — unlike rounds 1 and 2 — **not one was refuted**. Defect
density in fix code is roughly 3x that of the code being fixed, and rising. The fix-audit-fix loop
is not converging on its own.

**Two findings are regressions, not merely gaps.** R3.1 (critical) is the 0.71.0 fix reintroducing
R2's exact mechanism through the branch that fix promoted to FIRST priority — and on a row carrying
both a shared link and a real per-record token it is strictly WORSE than pre-0.71.0, which captured
the real token. R3.2 turned R4's "credited the wrong step" into "credits no step at all" for
essentially every step after the first.

**What that says about the process.** Every one of these sits in code written to close a previous
finding, under adversarial pressure, reviewed, and shipped green. Three of them are the SAME shape
as the finding they were fixing, one level down: a per-branch test that was not copied to the branch
that outranks it (R3.1), a null-check the sibling callers have and the new one does not (R3.4), a
guard applied to `capture` and not its sibling `describe` (R3.6). Adding another patch to each is
what produced this round. Prefer changing the shape so the invariant is enforced ONCE.

## What was done about round 3: two REDESIGNS, and one attempt that was measured wrong and reverted (0.73.0)

Round 3's own conclusion was that patching each finding is what produced round 3. So the three taken
on here were changed in SHAPE, and each report states what the old primitive could not EXPRESS, not
just what it got wrong. **Two shipped. The third was built, adversarially audited before merge,
measured to have introduced a CRITICAL regression, and reverted** — its section below is the record
of what that ruled out. Nine findings remain open.

**R3.1 — one implementation, one rule.** `rowIdOf` existed TWICE, transcribed into `_SPECOF_JS`
(capture) and `_ROW_OF_JS` (bind); the guard compares a token computed by one against a token
computed by the other, so the two copies had to agree exactly, and round 3 found them already
diverging. It is now a single module-level `_ROWID_JS` included verbatim by both — divergence is no
longer expressible, and a test asserts the inclusion rather than hoping for it. The selection rule
changed from a priority order over two candidate kinds to DISCRIMINATION applied once across all of
them: id, every `href`/`action` in the row (not just the first), every `data-*`, and every
`input[type=hidden][name]` — a candidate is an identity only if no other row carries it. Two shapes
that previously fabricated an identity now resolve correctly: a shared decorative link no longer
hides the real per-row action behind it, and a hidden record field (`order=3`) is read at all, which
is how a great many server-rendered tables carry record identity and often the ONLY per-row token
they have.

**R3.2 — ATTEMPTED, MEASURED WRONG, REVERTED. Still open, and now with a whole family of fixes
ruled out.** A full redesign was built and then backed out before merge, because a pre-merge
adversarial pass on the fix itself reproduced a CRITICAL regression in it. Recording it because the
negative result is the useful part: it rules out every purely temporal design, so nobody has to try
this again.

*What was built.* The old rule arbitrated overlapping intervals — each closed step kept a
`write_window_ms` (2s) grace tail, later steps opened windows inside it, and `_write_owner` refused
whenever more than one was live. Measured over a 6-step learn, per writing step (`-1` = nobody):

    defer=  0ms   [0, -1, -1, -1, -1, -1]
    defer= 50ms   [0, -1, -1, -1, -1, -1]
    defer=150ms   [0, -1, -1, -1, -1, -1]
    defer=300ms   [-1, -1, -1, -1, -1, -1]

Only step 0, and only if the write landed almost immediately — R3.2 as reported. The replacement
made the intervals non-overlapping instead of arbitrating them: the loop DRAINED for
`write_attrib_ms` (250 ms) with the acting step's window still open, before opening the next.

*Why it was wrong, measured on the branch.* A drain can prove nothing was **dispatched** during the
horizon. It cannot prove nothing is **pending**. A commit on a debounce longer than the horizon left
the drain quiet, so the drain reported "exclusive", and the write then landed inside a LATER step's
open window and was credited to that step. Fixture: one button POSTing on a `setTimeout(d)`, then two
inert buttons; the real commit is always step 0:

    defer=  60ms   mutating_steps=[0]   OK
    defer= 300ms   mutating_steps=[]    loud refusal
    defer= 450ms   mutating_steps=[1]   *** WRONG STEP, silently ***
    defer= 600ms   mutating_steps=[]    loud refusal
    defer= 800ms   mutating_steps=[2]   *** WRONG STEP, silently ***

Note it is a RACE, not a function of the constant: whether the write lands in an inter-step gap
(loud) or inside the next step's window (silent, wrong) depends on timing, so the same page can go
either way run to run. `write_unattributed` was False and `any(s.mutating)` was True, so neither half
of `_learn_once`'s refusal fired. The gate, the precondition and the Idempotency-Key rode a step that
never writes while the real commit cached as a read — **strictly worse than R3.2 itself**, which at
least failed loud. Reverted for exactly that reason: loud-and-useless beats silent-and-wrong.

*What this rules out.* Every purely temporal rule, including the two obvious repairs. Shortening the
predecessor's tail to the drain horizon reproduces the bug (the tail ends before the deferred write
arrives). Lengthening it to `write_window_ms` restores R3.2's collapse — with 2s tails and steps
~300 ms apart, a plain synchronous write on step 3 has candidates {0,1,2,3} and is refused. There is
no constant that is both long enough to catch a deferred write and short enough not to swallow the
next step. **Attribution needs a causal signal, not a temporal one.**

*Where the causal signal already exists.* `recorder.py` has solved this exact problem since Phase I:
`__ucturn` counts commits in the current SYNCHRONOUS turn (reset on the next macrotask), and
`attributedSeq()` returns a commit only for `__ucturn === 1`, leaving every deferred write
unattributed so `record` fails loud. That is the sibling guard `learn()` has never had — the pattern
this register keeps naming. Two candidate designs for the next slice:

  1. **Adopt the recorder's rule on the learn path.** Deferred writes become unattributed and refuse,
     which matches `record()` exactly. Cheapest correct option. Cost: a debounced commit becomes
     unlearnable via `learn()` and must be recorded — which is already the documented answer.
  2. **Drain until the act's own scheduled callbacks have run** (count outstanding `setTimeout`s
     scheduled during the act window, bounded by the cap). This would attribute the debounced commit
     CORRECTLY rather than refusing it. But note `recorder.py:181-185` already considered
     scheduling-time capture and rejected patching `setTimeout` as "too invasive to do without
     altering page behaviour" — overruling that judgment needs its own argument and its own audit,
     not a footnote in someone else's PR.

Whichever is chosen, share ONE implementation with the recorder rather than transcribing it — that is
the whole lesson of R3.1, one file over.

**R3.2's SAFETY HALF is closed in 0.74.0, and it was worse than this register said.** The attribution
RULE is still wrong — that is the open part, above — but the belief that its wrongness was *fail-safe*
was itself wrong, and that has been fixed.

*What was actually true on 0.73.0.* The claim was: nothing gets attributed, so nothing is marked
`mutating`, so `_learn_once` refuses. It refuses only when `not any(s.mutating for s in cached.steps)`
— an INFERENCE — and `classify_mutation` marks a step mutating from its INTENT TEXT alone. Measured,
with the real commit on a later step:

    intent of the benign sibling = "look at the panel"   -> refused (loud, safe)
    intent of the benign sibling = "submit the search"   -> CACHED
        [(0, 'submit the search', True), (1, 'continue', False)]

The gate, the precondition and the Idempotency-Key attach to a step that never writes, and the real
commit caches as a READ — no gate, no precondition, no key, and heal- and replan-eligible because
`not step.mutating`. An inviolable-#3 violation, silent, reachable by nothing more exotic than an LLM
writing the word "submit" in a step's intent.

*The rule that replaced it is a CONSISTENCY check, not an attribution.* R3.2 is still open and this
does not close it. When a wire write cannot be attributed, the wire and the classifier must at least
AGREE about where it is: the step that was IN FLIGHT when the write fired must be one the recipe gates.
Agreement means the Idempotency-Key, the precondition and the drift gate sit on a row that really was
mid-flight when a write left the browser. Disagreement means they sit on the wrong row, and the flow is
refused. Two independent signals cross-checked is a weaker claim than attribution and a sound one.

*Getting the BALANCE right is the whole difficulty, and the first version of this fix failed the other
way.* It refused on "unattributed" alone. But fill-a-field-then-submit — the ORDINARY shape of a write
flow — has its commit on a later step, which R3.2 leaves unattributable, while `classify_mutation`
gates that step perfectly well. Measured: refusing on "unattributed" alone failed every
`test_press_gate` login flow, i.e. it would have made essentially every real write flow unlearnable.
Those flows are fine, and the consistency rule keeps them: wire and classifier agree.

    silent-wrong  : write fires while step 1 is in flight, recipe gates step 0  -> DISAGREE -> refuse
    ordinary flow : write fires while step 1 is in flight, recipe gates step 1  -> AGREE    -> cache

Structurally identical to `any(s.mutating)`, and separable only by which step was in flight — which is
the fact the old inference threw away.

*And the refusal moved into the MECHANISM.* It lived in `flows._learn_once`; `ultracua run` and the
daemon call `flow.run_cached` directly and never reach that function, so the guard covered one of three
callers. It is now in `_learn`, which all three go through. Guard-in-the-wrapper is this register's
most-repeated defect shape, and it was sitting in the guard for it.

Also closed while in there: the promotion loop's `p = pos_of.get(i); if p is None: continue`. A write
can be positively ATTRIBUTED to a step whose act then failed, so no step was appended to carry its gate
— and that branch dropped it silently. It is exactly as unattributable as one nobody claimed, and now
refuses.

*Residual, stated and NOT closed.* A write deferred out of its own step into a neighbour that happens
to be classifier-mutating passes the consistency check. That is R3.2's residual exactly; this slice
makes the failure safe where the two signals disagree, and claims nothing where they agree by accident.
It closes when attribution becomes causal.

*The cost, measured rather than asserted.* `safety.is_write_request` counts any non-idempotent,
non-telemetry request as a write, so a click-triggered GraphQL/JSON-RPC **read**-POST is over-counted —
a documented residual. Such a read flow is therefore not learnable, and an earlier draft of this
entry claimed that was "not new" because `flows.learn` already refused it. An audit of this very change
called that measurably false, and it was. What was actually measured on 0.73.0:

    GraphQL read flow, no step's intent trips the classifier   -> flows.learn already REFUSED
    GraphQL read flow, one step's intent says "submit the …"   -> flows.learn CACHED it, as
        [('submit the filter', mutating=True), ('load more products', mutating=False)]

So the second shape is newly refused. But note WHAT it used to do: it cached with the gate, the
precondition and the Idempotency-Key on a step that issues no request at all, while the POST rode the
ungated step beside it. That is the same silent-wrong this slice exists to close, wearing a read flow's
clothes. The honest statement is not "nothing changes" — it is that the flows which newly refuse are
exactly the flows that used to cache mis-gated.

Do NOT "fix" the GraphQL case by narrowing what counts as a write (a read-endpoint allowlist, a
response-shape probe, an inline-handler heuristic). A design panel judged that class and flagged it as
reintroducing precisely the silent-wrong being removed here: anything that decides a POST is benign
without proof will eventually decide a commit is benign. The residual closes when attribution becomes
causal, not when detection becomes cleverer.

**R3.4 — the fact became expressible in the type.** `FlowCache.get` returned `Optional[CachedFlow]`
and flattened every failure into `None`, which reads as "not learned" everywhere — and
`is_write_flow(spec, cache.get(key))` is False for `None`, so a file the OS declined to hand over
became a flow with no writes and the unattended fleet ran it. Measured against the pre-fix source:

    cache.get(unreadable) -> None
    is_write_flow(spec, that) -> False    <- the recipe's only step is mutating=True

Absent and unreadable are now different outcomes: transient errors retry (the Windows AV/indexer
sharing-violation case the sibling meta loader already handled), a persistent one raises
`CacheUnreadableError`, and CORRUPT deliberately still returns `None` — the bytes are there and are
not a flow, so every caller's miss path is correct. Raising moves the decision to the callers, which
is the point. FOUR of them needed to say something other than "propagate", and they are all the same
shape — a loop or fan-out over the fleet, where one unreadable file must fail its own flow without
taking down the rest, and must never fall through to "this flow has no writes": `health()` (a distinct
`unreadable` status), `run_all`, `canary_all`, the MCP `tools/list` loop, and `flow approve --all`.

**Two bugs in this guard were found by auditing the guard itself, and both are this register's own
pattern turned on the fix.** `health()`'s new `FlowHealth` construction was missing required fields and
no test had ever executed that branch. And `run_all` was guarded on ONE of its reads — the write gate.
Its second read lives inside `replay()`, `except FlowReplayError` does not catch a `RuntimeError`, and
`gather` has no `return_exceptions`, so the exact blast radius the guard was added to prevent survived
thirty lines further down the same function. The guard now sits at the FUNCTION boundary, covering
every read on the path including ones added later — enforce the invariant once, not per branch.

**Evidence.** 20 new tests across `tests/test_row_identity_redesign.py` and
`tests/test_cache_unreadable.py`. Verified against pre-redesign source: 8/11 fail, and the
`cache.get` contract is proven by a direct probe (its own tests cannot run pre-fix — the exception
class is new). `drift_bench` is byte-identical to `baselines/drift_v2.json` — `silent_wrong` 2,
within the published allowlist; `wrong_target=0`, `double=0`, `suppressed=0`; `invariants: ALL HOLD`.
The attribution redesign's 8 tests were removed with the revert; the fixture that CAUGHT the
regression is preserved verbatim in the R3.2 section above and should be the first test written
against whichever causal design replaces it.

**The process point, which is the reason this section reads the way it does.** The reverted redesign
was green: 754 tests, `drift_bench` clean, every one of its own regression tests verified to fail
against the pre-fix source. It was still critically wrong, and only an adversarial pass aimed at the
FIX — not at the code the fix was fixing — found it. That is now three rounds in a row where fix code
was the defect source, and the first time the check that caught it ran before the merge rather than
one release later.

## Findings

### ✅ FIXED in 0.73.0 (REDESIGNED) — R3.1. The row-identity uniqueness test lives only on the `data-*` branch — a shared `href`/`form[action]` is accepted as a row identity, so the containment guard passes and the wrong row's write fires (R2's mechanism, reintroduced through the branch R2's fix promoted to FIRST priority)

*critical, lens `rowguard`, inviolable #2, reproduced by an independent refuter*

**Where.** `src/ultracua/locators.py:134-135 (`rowIdOf`: the `c.id` and `c.querySelector('a[href], form[action]')` branches) and the mirrored src/ultracua/locators.py:308-309 in `_ROW_OF_JS`. The uniqueness scan (`shared === 0`) exists only on the `data-*` loop at locators.py:139-148 / 313-320.`

**Mechanism.** R2's fix reordered `rowIdOf` to id -> href/action -> data-*, and added a sibling-uniqueness test —
but ONLY to the `data-*` loop. The href/action branch (now precedence #2, ahead of the token that IS
tested) and the id branch return the first value they find with no test that it distinguishes one
record from another. Two ubiquitous table shapes therefore produce an identity that is
byte-identical on every row: (A) one endpoint plus a hidden record key, `<form method=post
action="/cancel"><input type=hidden name=order value=3>` — every row yields `href:/cancel`; (B) a
shared decorative/first link in each row (`<a href="/help/orders">?</a>`) which wins on DOM order
over the per-row `action="/cancel/3"` sitting in the same row. `resolve` then computes
`_bound_row_id(loc)` on the SIBLING's control, gets the same shared string, `got == spec.anchor_id`
succeeds, and the guard returns the locator with `row_mismatch=None`. This is exactly the argument
R2 made about `data-testid="order-row"` ("the guard runs, reports satisfied, and protects nothing"),
transplanted one branch over. It is also a REGRESSION versus pre-0.71.0 for shape C: a row carrying
BOTH a shared help link and a genuine per-record `data-order-id` used to capture the real token
(data-* was first); now the shared href outranks it. Nothing downstream can catch it — I measured
`scope_fingerprint` of the two rows' controls as byte-identical, so the mutation gate waves it
through. Not covered by any residual in docs/open-defects.md (those are the token-LESS row and the
POSITIONAL token; here a token exists and is not positional). The drift corpus cannot see it:
`benchmarks/drift_fixtures.py:_cart_rows` gives the learned row #3 a unique `href="/done"` while the
other eleven share `href="/wrong"`.

**Failure.** A learned "cancel order #3" flow on an orders table that posts to one endpoint with a hidden id (or
that has any shared link in each row) replays the day after ops cancelled #3 by hand. resolve binds
Globex's Cancel by `role+name`, the guard reports no mismatch, the mutation gate's precond_scope
matches byte-for-byte, and the click fires `POST /cancel` with `order=7` under the Idempotency-Key
minted for order 3. Acme's order is not cancelled, Globex's is, the run reports success, zero LLM
calls, zero signal. Identical under the `display:none` SPA-delete shape.

### ⚠️ STILL OPEN — a 0.73.0 redesign was attempted, measured to regress, and REVERTED (see "What was done about round 3") — R3.2. `_write_owner` credits NOBODY for essentially every step after the first: R4's fix turns "wrong step" into "no step", and the real commit caches ungated and un-keyed exactly as before

*high, lens `attribution`, inviolable #3, reproduced by an independent refuter*

**Where.** `src/ultracua/flow.py:190-207 (`_write_owner`), :277-280 (`_owner`), :412-416 (the `graces` append), :468-478 (the promotion loop). Disarmed refusal: src/ultracua/flows.py:1049-1055.`

**Mechanism.** `graces` accumulates one `(step_index, expires_at)` per CLOSED step, with `expires_at = close_time +
write_window_ms` (default 2000 ms). `_owner()` is evaluated at the moment the request event fires
and builds candidates = {every live grace tail} u {cur_i if the act window is open}; `_write_owner`
returns -1 unless there is EXACTLY ONE. But step i's act window opens only a snapshot + a `decide()`
after step i-1 closed — a few hundred ms with a scripted/local/fast provider — i.e. deep inside step
i-1's 2-second tail. So the candidate set at a genuine step-i write is {i-1, i}, len 2, and the
owner is -1: nothing is added to `wrote_by_step` and the promotion loop never runs. Attribution now
succeeds ONLY when the inter-step gap exceeds `write_window_ms`, which makes whether a commit gets a
mutation gate and an Idempotency-Key a race against LLM latency. Downstream, `flows._learn_once`'s
"a write fired but no step could be attributed" refusal is all-or-nothing (`performed_write and not
any(s.mutating)`), so it only catches the case where NO step is mutating; one classifier-mutating
sibling anywhere in the recipe ("confirm"/"submit"/"send" are in `MUTATING_KEYWORDS`, and a bland
client-only "Confirm address" button matches) disarms it — the exact masking shape the R4 register
entry itself names. `_write_owner`'s docstring claims "the caller still records that a write
happened, so the flow is refused rather than cached with a mis-attributed gate"; that claim does not
hold.

**Failure.** Page: an inert `<button>Confirm address</button>` (client-side only; `classify_mutation` says
mutating on the keyword) followed by `<button type='button'
onclick="fetch('/api/sync',{method:'POST'})">Continue</button>` — the real commit, formless, which
the classifier misses by design and which the wire promotion exists to catch. Learn it and the
recipe caches with the commit at `mutating=False`: no drift gate, `precond_scope` empty, no
Idempotency-Key on the wire, and the step is heal- and suffix-replan-eligible because `not
step.mutating`. Replayed 0-LLM against a DRIFTED page the commit fires anyway (success=True, no
refusal), and two replays are two undeduped POSTs — inviolable #2 and #3, the identical harm chain
R4 was filed for. With no mutating sibling the flow is refused via `flows.learn()` (fail-loud, but
the promotion is then simply dead for every step index >= 1), while `ultracua run` / the daemon call
`run_cached` directly and cache it with ZERO mutating steps, so `is_write_flow` returns False, MCP
advertises `readOnlyHint=True` and `run_batch` treats it as a read batch. Moving the SAME button to
step 0 promotes correctly — which is precisely why the existing suite (tests/test_write_signal.py)
does not see this: every one of its write fixtures puts the commit at step 0.

### ✅ FIXED in 0.78.0 — R3.3. The `landed` rail arms the ledger for `WriteReadbackError` but not for its sibling `ShapeDriftError` — the case where the code knows the write committed AND the readback succeeded is still unarmed, so the CLI's resume re-fires it

*high, lens `ledger`, inviolable #3, reproduced by an independent refuter*

**Where.** `src/ultracua/flows.py:1488 (the shape gate in `_attempt_replay`, reached only AFTER `out["found"]` and `out["extract_found"]`) + src/ultracua/flows.py:398 (`landed = True` set on `WriteReadbackError` only) + src/ultracua/flows.py:2564-2565 (`run_batch`'s `if getattr(exc, "landed", False)` arming) and src/ultracua/mcpserver/server.py:283-284 (the identical predicate); advice printed at src/ultracua/cli.py:847-849`

**Mechanism.** R8's fix added `FlowReplayError.landed` and set it True on exactly one class. But `_attempt_replay`
has TWO failure returns downstream of the write-landed evidence, not one. The `write_unreadable`
return (flows.py:1476-1485) is reached when `out["found"]` is True (the A8 confirm TRANSITION held)
and `extract_found` is False. The SHAPE gate at flows.py:1488 sits one line further down and is
reached when `out["found"]` is True AND `extract_found` is True — the confirm transitioned AND the
readback was read cleanly. That is strictly stronger evidence of commit than the case that was
armed, and the gate is not scoped to reads: the H9 contract/magnitude gates immediately below it are
explicitly fenced with `if check_shape and spec.mutate is None`, the shape gate above them is not.
`meta.shape` is likewise seeded for write flows: `_learn_once` at flows.py:1093-1097 does `if not
was_approved: meta.shape = _shape_of(data)` and only excludes `meta.contracts` for a write, so
`ultracua flow learn --extract ... --confirm-text ...` (cli.py:162-176) leaves an approved WRITE
flow with a non-None `meta.shape`. `ShapeDriftError` inherits `landed = False`, so neither
`run_batch`'s new `except FlowReplayError` arming nor the MCP one fires; `replay` also falls to the
generic tail, recording `_record_run(ok=False)` (the opposite of the deliberate `ok=True` on the
write_unreadable branch, whose stated reason is that a failure streak pushes the operator toward the
one action that must not be taken) and raising a message that says nothing about a write having
landed. `run_batch` then reports the row `failed`, `report.status == "failed"`, and cli.py:847
prints "to resume the rows that DIDN'T commit" with a `--resume` command that re-fires it. Because
shape drift is deterministic, every subsequent resume re-fires it again.

**Failure.** A 200-row `flow run-batch --commit` pays invoices from a write flow authored with `flow learn
--extract 'the payment reference' --confirm-text 'Payment sent'`. Row 7's payment POSTs, the confirm
banner transitions, and the reference IS read back — but the vendor now renders it as a bare number
where the learned run gave a string. `_attempt_replay` returns kind="shape",
`ShapeDriftError.landed` is False, no ledger line is written for row 7, the batch stops, health
records a failure, and the CLI prints the resume command. The operator resumes; row 7's payment is
submitted a second time. If the payee honors `Idempotency-Key` the duplicate is suppressed but the
ledger is permanently blind to row 7 and every further resume re-fires it; if it does not (or the
key window has expired), the invoice is paid twice. On the MCP write surface the same state leaves
`ledger.record` unreached, so an outer agent's retry re-elicits and re-fires (not separately probed
— the predicate at server.py:283 is byte-identical and `ShapeDriftError.landed` is False).

**FIXED in 0.78.0** (plan slice S3), regression tests confirmed RED against the pre-fix source — **after
SIX adversarial passes and SIX versions of the predicate, five of which were wrong.** Passes 2–5 each
found a critical that the previous pass had approved, because the fix had changed underneath it; pass 6
was the first clean one. Every wrong version passed the full suite and `drift_bench`. If you are about
to change this predicate, read the five failure modes below first — they are not hypothetical, each was
reproduced with a live browser against a local fixture.

**The fix is positional, not typological** — which is the whole point, and the plan was right to insist
on it. "Also arm `ShapeDriftError`" is the shape that CREATED this finding: R8 armed one class, and the
failure return that grew one line below it was never armed. Arming a second class only resets the clock
for the third.

So `landed` is a CONJUNCTION of two independently-sourced facts, and each is computed where it is known:
`_make_finalize` publishes `out["write_landed"] = confirmed and "_pre_confirm" in out` (the A8 baseline
ran, and the confirm then transitioned), and _attempt_replay ANDs that with ll_writes_ok — EVERY step the
cached recipe marks `mutating` produced a trace that ran and SUCCEEDED, read from `report.traces`.
The quantifier is `all` over the RECIPE: `any` and counting-against-traces each shipped a critical. Every failure return then leaves
through a nested `_fail(reason, kind)` closure carrying it. `replay()` ORs the
value across its three attempt sites and stamps every outgoing `FlowReplayError` at ONE place: its
existing `except FlowReplayError` handler. Four raise sites, one arming point.

The MCP write surface needed no code change — it reads `getattr(exc, "landed", False)`, so instance
stamping reaches it — but both consumers' comments described the old per-class rule and now state that
`landed` is positional and must not be narrowed back to a class check.

**The layer that matters is the structural one, and it was measured rather than assumed.** Three landed:
the E2E (the payment fires, `ShapeDriftError` carries `landed=True`), a property over every failure kind
downstream of the evidence point, and an AST scan requiring every `return` in `_attempt_replay` to be
either the success tuple or a `_fail(...)` call. Mutation on the third: reinstate one raw tuple return
that STILL carries `landed` correctly — behaviour unchanged, both behavioural tests stay green, only the
AST guard fails. That is exactly the case worth catching, because R3.3 was never about the returns that
existed when R8 was written; it was about the one added below them afterwards.

**THE FIRST VERSION OF THIS FIX WAS CRITICALLY WRONG, and the reason generalises.** It set `landed` at a
POSITION — just past the `not out.get("found")` check — which sits BELOW the `not report.success` guard.
But `finalize` runs UNCONDITIONALLY (`flow.py` calls it outside the step loop), so the confirm's
transition can be observed on a run whose `report.success` is False because a LATER step drifted. A
trailing "Print receipt" / "Back to list" / "Continue" is the canonical shape of a write flow, so that
population is LARGER than the shape gate this finding was filed for. Reproduced end to end by the second
adversarial pass: **two payments for one operator request** — no ledger row, and the slice's own new
retry stop, keyed off the wrong `landed`, let the auth-refresh path re-run the flow from `start_url`.

**AND THE SECOND VERSION WAS WRONG TOO, in the OPPOSITE and worse direction.** Reading the evidence as
`out["found"] and not out["confirm_pre_true"]` looked like the fix — but `_pre_confirm` is written by a
hook `flow.py` calls only when the step loop REACHES a mutating step, so its ABSENCE means "the run
never got to the write", and `bool(out.get("_pre_confirm"))` cannot tell that apart from "measured, and
clean". A run that fired ZERO writes, failing before the commit while a stale banner from a previous
order satisfied the confirm, therefore armed. Reproduced by the third adversarial pass: 0 POSTs,
`landed=True`. `run_batch` would write a ledger row, every `--resume` would report "already committed —
not re-fired", and **the invoice would never be paid, silently and permanently** — the direction
`ledger.py` explicitly forbids, and a REGRESSION, since unarmed the resume re-runs the row and pays it.

That is this project's own absent-vs-unreadable trap (R3.1, R3.4) for the third time: a two-state
boolean answering a three-state question, with the third state read as the safe one. The arming now
requires POSITIVE proof the baseline ran (`"_pre_confirm" in out`), and is computed where all three
facts are known rather than re-derived at a distance.

**AND THE THIRD VERSION WAS WRONG TOO, same direction, one entry further in.** "Positive proof the
baseline ran" is not proof the WRITE ran: `pre_write` is called BEFORE `_replay_step` attempts the
action, so merely REACHING a mutating step creates the key — the click, the mutation gate and the POST
all happen after. And the two probes are asymmetric by construction: the baseline is a single
instantaneous check while the finalize confirm POLLS for seconds. So on a run whose write step itself
drifted (a renamed commit control, a mutation-gate refusal — and mutating steps are never healed),
anything matching the confirm that painted inside that window read as a transition. Reproduced by the
fourth adversarial pass and independently here: **0 POSTs, `landed=True`**, with the operator told "the
write DID commit". `flow.py` already had the missing guard ONE LINE OVER — the per-step commit barrier
gates its identical transition check on `ok` — so this is the register's own predictor, a guard on a
sibling path never applied to the mechanism.

**AND THE FOURTH VERSION WAS WRONG TOO — the quantifier.** Adding "a mutating step ran and succeeded"
used `any()`. Two criticals, both reproduced with zero writes on the wire. (a) A recipe with a SECOND
mutating step arms off that sibling while the real commit fails — and the exploit needs nothing exotic,
because `classify_mutation` matches `pay` inside "Payment history", a false positive this repo PINS BY
NAME in `tests/test_write_classification.py` and measures at ~28% of ordinary read controls. (b) On a
genuine multi-write, write #1 landing armed the whole ROW while write #2 never fired, so a resume
suppressed it permanently — inviolable #3's second clause, and a verbatim contradiction of `ledger.py`'s
"a multi-write row that died mid-flow is not recorded and re-fires all its writes on resume".

The claim that licensed that version — "parity with the success path" — was FALSE, and it was false
because it was checked shallowly. The success path reaches `ledger.record` only via `report.success`,
and the step loop breaks on the first failure, so EVERY mutating step ran and succeeded there. `any()`
required one. The arming now counts against the CACHED RECIPE: every step the recipe marks mutating must
have produced a trace that ran and succeeded. (Counting against the traces instead would make `all([])`
vacuously true and re-open the never-reached-the-write hole from two versions earlier.)

**Five fixes, four wrong, and the through-line is one sentence.** R3.3 is "the exception's CLASS is the
wrong proxy"; the plan answered "the POSITION is the right proxy"; then "the collapsed boolean is the
evidence"; then "the baseline's presence is the evidence"; then "some mutating step succeeded". All five
were proxies, and each correction shipped a defect in the OPPOSITE direction to the one before. **When a
finding says a proxy is wrong, check whether your replacement is also a proxy** — when a boolean stands
in for an observation, ask what its False means when the observation never happened, and when you reach
for a quantifier over a collection, ask which collection and why `any` rather than `all`.

**The residual, stated rather than hidden.** A click that SUCCEEDS but fires no request, with a
late-painting confirm, still arms. That is A8's documented residual, and it is now genuinely the same
residual the SUCCESS path carries — same `confirmed`, same all-steps-ok requirement. Parity with the
success path is the claim, and this time it was verified against `report.success`'s own semantics rather
than assumed. Nothing stronger is reachable without threading the wire signal from `_replay_step`, which
is S6/AB-1 territory.

**Two further changes the adversarial passes forced, both now part of this fix.** (1) A SECOND
double-submit: `replay()` hard-stops `write_unverified` and `write_unreadable` before `retry_ok` is
computed — "both would re-fire a committed write" — and `shape` had no such stop, falling through to a
precheck that can return False while the commit HAS landed. The evidence now lives inside
`_auth_retry_allowed` as a required `landed` argument, checked before the write arms and after the
auth-path precondition. (2) DISCLOSURE: arming fixes the machine loop, but the human one still read
"nothing happened", because under R8 arming and disclosure were coincident by accident (the one armed
class's message already said "the write WAS confirmed"). The failure `reason` now states the commit
before `_record_run`, so it reaches `health.last_error`, the CLI row line, `BatchRowResult.error` and
the MCP message from one place — and it deliberately does NOT promise a ledger row, because `replay()`
owns no ledger and three of its callers have none.

**R4.12 (filed, not fixed).** `learn()` passes `pre_write=_make_pre_write(spec, out)` to `run_cached`,
but `run_cached` forwards `pre_write` only to `_replay` — `_learn` has no such parameter, so the
argument goes nowhere. On the LEARN path a declared write's whole-flow confirm is therefore still a bare
presence check, and a stale banner lets an un-landed write cache as a verified flow. That is A8's own
hole on the sibling path, and it means the `_pre_confirm` channel S3 now treats as evidence is populated
on one of two code paths. Not introduced here; found by the sibling check while closing the false-arm
regression. It does NOT affect the arming (which requires the key's presence, absent on the learn path
→ never armed), but it does weaken learn-time write verification. Sequence with S4.

**R4.11 (filed, not fixed).** After an auth-refresh retry, `kind = kind2` and the early `write_unreadable`
/ `write_unverified` raises are not re-checked, so a POST-REFRESH `write_unreadable` falls through to the
generic tail and is recorded `ok=False` — contradicting `WriteReadbackError`'s own docstring, which
states the run is recorded a SUCCESS precisely so a failure streak cannot invite the retry that must not
happen. Pre-dates this slice; found while verifying the `ok=False` comment. Sequence with S4.

### ✅ FIXED in 0.73.0 (REDESIGNED) — R3.4. run_all's NEW write predicate fails OPEN: `FlowCache.get` swallows every read error into `None`, and `is_write_flow(spec, None)` is False — one transient read blip on the cached-flow file and the unattended fleet fires the undeclared write and prints [OK]

*high, lens `surfaces`, inviolable #3, reproduced by an independent refuter*

**Where.** `src/ultracua/flows.py:2317 `if is_write_flow(spec, cache.get(key)):` (new in this diff) reading through src/ultracua/cache.py:235-238 (`except Exception: return None # corrupt entry -> miss`); same shape at src/ultracua/mcpserver/server.py:132`

**Mechanism.** The R6 fix routes run_all's skip decision through `is_write_flow(spec, cache.get(key))`.
`FlowCache.get` converts EVERY failure — including a transient OSError — into `return None`, and
`is_write_flow(spec, None)` returns False because `cached_flow is not None and any(...)`
short-circuits. So "the cache file could not be read right now" is indistinguishable from "this is
not a write". run_all then falls through to `_load_meta` (which the SAME commit gave a 3-attempt
retry loop, flows.py:594-620, explicitly for `WinError 32` AV/indexer sharing violations on this
very directory) and on to `replay()`, whose own `cache.get` re-reads the file successfully and fires
the commit. The guard added to close R6 is therefore a coin flip on exactly the fault class this
commit documents as observed and real. The two sibling callers that consolidation converted DO
handle it — `run_batch` (flows.py:2428 `if cached_flow is None: raise`) and `flow approve --all`
(cli.py:354 `if cached is None: skipped('not learned yet')`) both check `cached is None` BEFORE the
predicate — while run_all's new copy and `mcpserver._is_write_flow` do not. This is the CLAUDE.md
pattern striking the fix again, one level down: not a missing transcription, a missing null-check on
two of the five.

**Failure.** A flow learned as a read whose button POSTs (`spec.mutate=None`, cached step `mutating=True`) sits
in an approved cron fleet. On the tick where an AV scan/indexer holds `<key>.json` for the few ms of
the predicate's read, run_all does not skip: it replays, the commit POSTs unverified (no confirm
barrier, `_preflight_row` derives `is_mutate` from `spec.mutate`), `_make_finalize` takes the
navigate-only branch and sets `found=True` unconditionally, and the CLI prints `[OK]`. Identical
mechanism on MCP: `list_flow_tools` reads the cache twice (health(), then `_is_write_flow`); a blip
on the second read advertises the undeclared write to an untrusted outer agent with `is_write=False`
— no `[WRITE — …irreversible…]` prefix, readOnlyHint true, and `call_flow_tool` takes the READ path:
no single-flight lock, no ledger, no human elicit.

### ✅ FIXED in 0.77.0 — R3.5. The consolidation left a FIFTH surface: `replay()`/`_preflight_row` still key every write rail off `spec.mutate` alone, so an UNDECLARED write takes the auth-refresh retry that a DECLARED write is explicitly forbidden from taking — measured, the commit POSTs twice

*high, lens `surfaces`, inviolable #3, reproduced by an independent refuter*

**Where.** `src/ultracua/flows.py:1620 (`_preflight_row`: `is_mutate = spec.mutate is not None`), src/ultracua/flows.py:1901 (`replay`: same), src/ultracua/flows.py:1979-1981 (`retry_ok = auth_refresh and spec.login is not None and (not is_mutate or …)`); reachable via `ultracua flow replay --name X` (cli.py:247) and the `flows.replay()` API`

**Mechanism.** 0.72.0 converted four surfaces to `is_write_flow` — run_all, run_batch, mcpserver, `flow approve
--all`. The surface all four funnel THROUGH was not converted. `replay()` and the shared
`_preflight_row` still compute `is_mutate = spec.mutate is not None`, so for an undeclared write
(`spec.mutate=None`, cached step `mutating=True`) `not is_mutate` is True and `retry_ok` becomes
True. The whole flow is then replayed from `start_url` after `refresh_auth`, re-actuating the
commit. The declared path refuses this in as many words — "a first attempt may have committed the
write before failing its confirm check, and a blind retry would double-submit" — and the R6 fix is
precisely the argument that the declaration is the thing you must not trust. The only remaining
defense is the Idempotency-Key, which I measured to be byte-identical across both firings; but for
an undeclared write the commit is a plain form/fetch POST to an endpoint that never asked for that
header, and the declared path deliberately does NOT lean on it. (Two sub-arms died to upstream
guards and are NOT part of this finding: `on_drift='relearn'` is refused for any APPROVED flow
regardless of declaration, and run_all/run_batch/MCP now all refuse the flow outright — so the
reachable surfaces are `flow replay` and the library API.)

**Failure.** An operator has a flow learned as a read whose commit POSTs, with `spec.login` configured (the
ordinary shape for anything behind a session) and `auth_refresh` on by default. The post-commit page
drifts — the commit's response page loses a control the recipe clicks next. Attempt 1 actuates the
commit and then fails a later step; `retry_ok` is True; auth is refreshed; attempt 2 replays from
the start and actuates the commit AGAIN. The operator gets `DriftError: … after auth refresh: replay
failed (page drift?): replay` — a message that never mentions that the commit fired, let alone
twice. The declared-write control on the identical fixture, identical cached recipe, identical
drift, fires once and says why.

**FIXED in 0.77.0** (plan slice S2), regression test confirmed RED against the pre-fix source —
verbatim: `the commit fired 2 times (keys=['uca-8e4d01d65220b115dd25cc01',
'uca-8e4d01d65220b115dd25cc01'], auth refreshes=1)`.

The fix: `_auth_retry_allowed(spec, cached_flow, auth_refresh=…)`, one definition of when a drifted
replay may be re-run from `start_url`, keyed off `is_write_flow` (wire OR declaration). A read retries
freely; a DECLARED single write with a whole-flow precheck retries (the precheck detects that the commit
already landed); everything else — including the undeclared write — does not, and now says so in the
operator-facing reason, which the pre-fix code never did because the branch could not be reached.

**Plus the arm this entry originally scoped OUT, which turned out to be the worse half.** The exclusion
above reads: "`on_drift='relearn'` is refused for any APPROVED flow regardless of declaration". True, and
it covers the wrong population — the flows that can run undeclared are the UNAPPROVED ones. An
unapproved undeclared write satisfied neither the declared-write arm (no `spec.mutate`) nor the
approved-flow arm, so it re-authored: `replay()` re-ran the flow from `start_url`, re-firing the commit
with the same byte-identical key, and then **returned normally with a recorded SUCCESS**, because the
`{"status": "confirmed"}` envelope also keys off the declaration. A green run, a green health record, and
a commit that fired more than once — inviolables #2 and #3 in one call, and strictly worse than the
retry arm this finding is named for, since the retry at least raised. Reachable from `ultracua flow
replay --name X --on-drift relearn`, which the CLI help actively routes operators toward. The relearn
refusal now asks `is_write_flow` instead of the declaration. That costs the misclassified-read population
only the opt-in self-healing mode, and only while `--on-drift relearn` is asked for; a plain replay is
untouched. Found by the second adversarial pass, on the reworked fix — the sibling `if`/`elif` pair
twenty lines apart, in the function the first pass had already been through.

**And one more declaration-standing-in-for-reality bug, inside the new predicate itself.**
`MutateSpec.is_multiwrite()` is `len(step_confirms) > 1` — a question about the DECLARATION — while
`record()` explicitly permits a declared write with two mutating steps and no `step_confirms` at all.
Such a flow read as a single write, so a whole-flow precheck earned it the retry; the precheck probes
only the LAST write's marker, so after write #1 lands the re-run re-fires it. Verbatim the harm the
multiwrite exclusion exists to prevent. `_auth_retry_allowed` now counts the mutating steps in the
recipe as well. Note the shape: R3.5's own conflation, one field over, reproduced inside the fix for
R3.5 — which is this register's structural finding operating on itself.

**Two rejected fixes are the substance of this entry.**

**(1) What the survey and the plan both prescribed is a defect.** "Convert the funnel surface to
`is_write_flow(spec, cached)` — the exact conversion 0.72.0 did on the four surfaces that funnel THROUGH
it." Done literally, that breaks the write path: THREE sites downstream of that binding dereference
`spec.mutate` (`has_confirm()`, `has_precheck()`, `is_multiwrite()`), so a widened predicate turns a
refusal into an `AttributeError`. The patch reproducing the class it closes, one level down — this
project's signature failure, sitting inside its own remediation plan.

**(2) The flow-level refusal was built, went green, and is WRONG.** The natural reading of the sibling
guards (`run_batch` at flows.py:2556 and `run_all` at flows.py:2437 both refuse an undeclared write
outright; MCP never advertises one) is "push that refusal down into `_preflight_row`, the gate all four
funnel through". That was implemented, the full targeted suite passed (105 tests), and a new
24-cell matrix dimension mutation-measured at 20/24 detection. **All of that was true and the change was
still unshippable**, which is the fourth consecutive time green has been worthless here.

An adversarial pass measured what it refused. `is_write_flow` trusts `step.mutating`, and that flag
OVER-counts badly: `safety.classify_mutation` falls back to an unbounded substring match over
`intent + accessible name`, with no word boundaries. Verified directly —

| step | classified | because |
|---|---|---|
| click "Payment history" | mutating | `pay` |
| click "Show borders" | mutating | `order` |
| click "the Sent folder" | mutating | `send` |
| click "Deleted items" | mutating | `delete` |
| click "Subscribers" | mutating | `subscribe` |

8 of 10 sampled ordinary read navigations classified True. Such flows CACHE (the wire-vs-classifier
consistency check only fires when there is wire evidence to reconcile) and they replay fine today. A
flow-level refusal breaks every one of them on `flows.replay()`, `ultracua flow replay` and `dry_run` —
and **neither remedy such a refusal can name works for that population**: `flow record` re-derives the
identical verdict through the same classifier and then refuses, and its refusal path DELETES the cached
recipe, so following the printed advice destroys the working flow; and declaring `mutate` demands a
write-completion
confirm signal a read cannot produce, disables the H9 read-side contract/magnitude rails
(`_attempt_replay` runs them only when `spec.mutate is None`), changes `replay()`'s return type, drops
the flow out of the default cron fleet, and has no `unset-mutate` verb to undo. The second false-positive
source is the wire promotion's own stated residual: a click-triggered GraphQL/RPC read-POST, i.e. every
GraphQL-backed SPA read.

That is the 0.74.0 over-refusal regression one population over — the one this file already records as
having actually shipped. **The guard therefore sits on the RETRY, not on the flow.** Declining one
auth-refresh retry costs a misclassified read nothing but a loud failure on an expired session; it costs
a genuine undeclared write its double-submit. Refusing the flow is decision D0, and it is now BLOCKED on
telling the two populations apart — see `docs/correctness-plan.md`.

**Test-side, three things the same passes corrected.** The enforcement test was a regex over three
literal variable names — theatre, and blind to `writes = spec.mutate is not None`, which the fix's own
rename had just made the house style. It now walks the AST, matches the PREDICATE
(`is`/`is not`/`==`/`!=`/`not …`/`bool(…)`, **including buried inside a larger boolean, which is the form
R3.5 actually had**), allows it only under names that mean the declaration, asserts it scanned a non-zero
number of files, and carries a positive control sharing its matcher — which caught `not spec.mutate is
None` slipping past the first version on its first run. Its LIMIT is now stated in the test rather than
overclaimed in the docs: it catches BINDINGS, not every inline use, because the raw predicate is
legitimate in ~20 places where the question really is the declaration, and an allowlist longer than the
rule is not a rule. It narrows the next transcription's blast radius; it does not make the predicate
inexpressible, and nothing should say it does. The matrix dimension asserts the retry property rather
than a refusal. And the missing liveness cell — a misclassified READ must still pre-flight clean — is now
a test, with its own premise asserted so it fails loudly rather than vacuously the day the classifier is
tightened.

**A sibling gap found by this slice's sibling check, filed not fixed.** `run_audit` (flows.py:2320) skips
write flows on `spec.mutate is not None` alone, so an undeclared write is captured and judged in
contradiction of its own "never captured, never judged" invariant. Fixing it needs a
`CacheUnreadableError` guard around the `cache.get` (an R3.4 shape), so it is a slice, not a one-liner.
**R4.10**, sequenced in `docs/correctness-plan.md`. Note this is NOT neutralized by the fix that shipped:
the flow-level refusal would have made it unreachable, and the retry-level guard does not.

> **✅ R4.10 FIXED in 0.82.0 — and the filing understated it by half.** Reproduced first, all three parts
> live at 0.81.0. The judge half was as filed: an undeclared write was judged, one real LLM call, an
> advisory finding recorded against a write flow. But **the CAPTURE had no write gate in any form** —
> `audit.capture`'s own docstring says "the CALLER gates opt-in / write-flow / deterministic-gates-passed"
> and the caller checked opt-in and the deterministic gates and never the write, so a write flow's
> post-commit page (up to 3000 chars, plus the extracted data) was written to `<cache>/audit/<key>/`
> while `flow audit` printed "never captured" for that same flow. A mechanism that documents a gate as
> its caller's responsibility, and a caller that does not implement it: this register's signature shape,
> stated in its own source. Third: `flow audit --set-mode enforce` did not refuse a declared write flow,
> and printed "a corroborated finding can now QUARANTINE this flow" about a flow the layer excludes.
>
> **The gate went INTO `_capture_audit`, not onto its call site.** It is the only function that reaches
> `audit.capture`, so a future caller inherits the rule instead of rediscovering it — the difference
> between fixing today's caller and closing the hole. The judge's gate became `is_write_flow` as filed,
> and is now the second line rather than the only one: no artifact means nothing to judge, whatever a
> later gate asks. The arming refusal is third, and deliberately NOT load-bearing — an undeclared write
> has no declaration to test at arming time, and a read can become one on a re-learn.
>
> Five tests, three verified RED. The fifth is the must-remain-usable clause — a READ flow is still
> captured on a drift signal and still judged — without which every other assertion is satisfied by
> disabling the H9 layer, which is the 0.74.0 over-refusal shape again.
>
> **MIGRATION, for anyone who had `spec.audit` set on a write flow before 0.82.0.** The fix stops new
> captures; it does not remove artifacts already on disk, and because the write gate now returns before
> `audit.capture` (which is what runs `prune`), those files no longer age out by TTL. They are bounded —
> `prune` ran when each was written, so the store is capped at `AUDIT_KEEP` per flow — and
> **`flow audit --purge` clears them**, which is why nothing here deletes anything: a destructive
> remediation inside a fully-swallowed best-effort path is the exact shape that produced S4's four cut
> fixes.

### ✅ FIXED in 0.86.0 (S9) — R3.6. The redaction covers the Observation but not `LocatorSpec` — `describe()` writes the same page-derived secret to the flow cache in plaintext, on both learn and heal

**Reproduced first, and the reproduction's own failure is the best statement of the finding.** The first
fixture drove a scripted click on the button's real accessible name and nothing happened: `_learn_once`
passes `redact=_secret_values(spec)`, so `snapshot.capture` had ALREADY rewritten that name to
`Copy [REDACTED]` before any provider saw it. The agent literally cannot name the element by its secret.
**That is what makes the disk channel so easy to miss — every observable signal says the redaction
worked.** With the provider matching what the agent actually sees, the leak was exact:

    the resolved secret was written to the flow cache in plaintext, where it persists.
    Offending fields: ['name', 'text', 'anchor']

**A THIRD PATH TO DISK existed and `describe()` was only one of them.** `recorder._step_from_event`
builds `LocatorSpec(**raw)` itself from the same shared in-page `specOf`, so scrubbing `describe()`
alone would have left `flow record` writing the same plaintext to the same file. One shared
`redact_spec_fields` now serves both, threaded from `flows.record` — the sibling-gap shape, caught by
checking for it rather than by a test.

**Every string field, not the leaking three.** A scrub is a no-op unless the term is present, so
structural selectors are untouched in the ordinary case; partial coverage is exactly how R9 shipped
scrubbing 2 of 5 Observation fields and had to be reopened.

**What the adversarial pass found in the fix, which no test above would have caught.** `anchor_id` is
compared by EQUALITY against the row identity recomputed from the LIVE page (`got == spec.anchor_id`).
The page still holds the real secret, so a stored `href:/reveal?api_key=[REDACTED]` can NEVER match —
the row guard would refuse the bind on every replay, permanently, for a reason no message explains. A
redacted identity is a FABRICATED one, and this module's own rule already covers it: return null, because
a fabricated identity "makes the guard claim protection it does not provide". `anchor`/`anchor_source` go
with it.

The alternative that would keep the guard working — scrub both sides at compare time — was REJECTED:
it makes two DIFFERENT secrets compare equal, trading a loud refusal for a possible silent wrong-row
bind. Worse on this register's ranking, and the reasoning is recorded so it is not re-attempted.

**The cost, stated rather than hidden.** A control whose only identity IS the secret becomes unbindable
once scrubbed, and a row whose identity carries one loses the row guard. Both fail LOUD; neither is
silent. `drift_bench` invariants ALL HOLD with the binding-tier distribution unchanged, and the
must-remain-bindable direction is pinned (the recipe still resolves at 0-LLM where any other tier
survives).

*high, lens `secrets`, confidentiality (no inviolable), reproduced by an independent refuter*

**Where.** `src/ultracua/locators.py:173-188 (`_SPECOF_JS` -> `specOf`) and :202-207 (`describe`); persisted at src/ultracua/flow.py:347 + :601 (learn) and :1198 + :1280 -> :916 (heal, `cache.put(flow)`); written unscrubbed by src/ultracua/cache.py:249 (`FlowCache.put`); re-echoed by src/ultracua/cli.py:294-296 (`_step_line`, feeding `flow inspect` and `flow approve --all`)`

**Mechanism.** The 0.72.0 diff widened `snapshot.capture(redact=...)` to all five prompt-rendered fields,
justifying element-`name` scrubbing with "an accessible name carries the plaintext on the standard
'Copy sk-live-…' button" and `url` scrubbing with "a token in a query string — a magic link,
`?api_key=`, `?otp=`". Both of those exact strings are ALSO captured, verbatim and unredacted, by
`locators.specOf` into `LocatorSpec.name`, `LocatorSpec.anchor` (60 chars of the enclosing row's
collapsed innerText) and `LocatorSpec.anchor_id` (`'href:' + the row's first href/action`, query
string included). `describe()` reads the LIVE page and never consults `session.redact` /
`_secret_values(spec)`. The learn loop (flow.py:347) and the heal (flow.py:1198, `step.locator =
spec` at :1280) both push that spec into the `CachedFlow`, which `_learn`/`_replay` then `cache.put`
to `<flow_home>/flows/<key>.json`. `FlowCache.put` does no chmod (unlike `audit.capture`, which
chmods its dir 0o700), so the file lands at the default umask. This is the project's own recurring
shape — a guard applied to the mechanism (`capture`) and never to its sibling (`describe`) — and it
violates the stated rule that secrets are "never serialized, logged or written to disk".
docs/open-defects.md records R7 (extractor page text) and R9 (name/url/title to the MODEL) as fixed;
the DISK sibling is recorded nowhere, and is not in the residual list.

**Failure.** An operator runs an approved read flow over an API-keys / magic-link / invite table where the page
echoes the token (the exact page shape the 0.72.0 fix cites as its own motivation). The flow drifts
on one step and self-heals, or is (re)learned. The heal prompt is correctly `[REDACTED]` — so every
observable signal says the redaction worked — while the same run writes `"name": "Copy
sk-live-DEADBEEF0123456789"`, `"anchor": "Production key sk-live-… open Copy"` and `"anchor_id":
"href:/reveal?api_key=sk-live-…"` into the flow cache JSON, where it persists indefinitely. `flow
inspect` then prints it to the terminal, and `flow approve --all` reprints it fleet-wide — the
renderer whose own comment says it "must never be the thing that echoes a secret". Nothing logs or
warns.

### R3.7. `_ROW_OF_JS` does not mirror `anchorOf`'s walk as its comment claims — `anchorOf` SKIPS a row-like container whose collapsed text is empty and keeps climbing, `_ROW_OF_JS` stops at it unconditionally — so a nested action list makes the guard refuse a correct bind on a page that has not drifted at all

*medium, lens `rowguard`, confidentiality (no inviolable), reproduced by an independent refuter*

**Where.** `src/ultracua/locators.py:296-303 (`_ROW_OF_JS`'s climb, `if (/^(li|tr)$/...) { row = c; break; }`) versus src/ultracua/locators.py:164-167 (`anchorOf`, which only returns at a row-like container `if (t)` — a non-empty collapsed text). The claim being broken is the comment at locators.py:293-298: "MIRROR `anchorOf`'s walk exactly".`

**Mechanism.** `anchorOf` requires a row-like container to have non-empty `innerText || textContent` before it will
anchor on it; if the text is empty it keeps climbing (up to 4 landmark hops) and anchors on an OUTER
row. `_ROW_OF_JS` has no text condition and breaks on the first row-like container it meets. The two
therefore disagree on WHICH row whenever a text-less row-like container sits between the target and
the real row — the ordinary "row > nested actions <ul> > <li> > icon-only control" pattern, where
the inner `<li>`'s collapsed text is empty because the control is an icon with only an `aria-label`.
Capture records the OUTER row's identity (`id:order-3`); the guard measures the INNER `<li>` and
gets a different string (`href:/cancel/3`, or `''` when the inner li offers nothing), so `got !=
spec.anchor_id` and `resolve` returns None. This is authoring-silent: the learn loop actuates
through `[data-ultracua-ref=...]` (flow.py:350/364/378/388/1204) and never calls `resolve`, so
`learn()`/`record()` succeed and the flow is cached; only replay binds through `resolve`
(flow.py:1082). Fails loud rather than wrong, so no inviolable is breached — but the flow is
permanently unusable and, with a heal provider configured, every replay of that step is routed into
`_maybe_heal`, i.e. into an LLM call.

**Failure.** A flow learned against any list/table whose row actions live in a nested `<ul class="actions">` with
icon-only controls records fine and caches fine. Every subsequent replay refuses at the bind with
`bound_by='none'`, `row_mismatch="'id:order-3' -> href:/cancel/3"` — on the pristine, unmodified
page. The failure is discovered only in production, and the sink message accuses the page of drift
that did not happen.

### ✅ FIXED in 0.79.0 — R3.8. The meta transient-retry fix does not hold on the path that actually writes: `_update_meta` re-saves the poisoned meta OVER the healthy sidecar, and unlike the code it replaced it leaves NO `.corrupt.*` backup — so one transient WinError 32 now destroys approval, contracts, shape, steps_hash and read_pin irrecoverably, while the log asserts "leaving the file untouched"

*medium, lens `trust`, inviolable #2, reproduced by an independent refuter*

**Where.** `src/ultracua/flows.py:616-623 (the OSError fall-through returning `_poisoned_meta()` without persisting) in combination with src/ultracua/flows.py:527-535 (`_update_meta` = `_load_meta` -> mutate -> `_save_meta`), reached from `_record_run` (flows.py:683 — every replay), `release` (1205), `approve` (1164), `_quarantine` (1213), `unapprove` (1177), the relearn pin-clear (2023).`

**Mechanism.** The new OSError branch is correct in isolation: after 3 attempts it returns `_poisoned_meta()` and
deliberately does NOT touch the file, logging "leaving the file untouched (it may be perfectly
healthy)". But `_load_meta` is not only a reader. `_update_meta` calls it as the LOAD half of a
read-modify-write and then unconditionally `_save_meta`s whatever it got back — there is no check
for `quarantine == meta_unreadable` between them. So on the hot write path the poisoned in-memory
meta is serialised straight over the healthy file. Two things make this strictly worse than the code
it replaced: (1) the old branch went through `_refuse_unreadable_meta`, whose `_preserve_corrupt`
renamed the original to `<key>.meta.json.corrupt.<ts>` FIRST — the new path skips that, so
`os.replace` in `_save_meta` overwrites the only copy; (2) the read inside `_update_meta` is the
read most exposed to the very window the fix names, since `_save_meta`'s own `os.replace` on the
previous run is what opens the AV/indexer sharing window. The operator-facing quarantine reason is
still `_META_UNREADABLE` (flows.py:538-542), which instructs "Inspect the preserved `.corrupt.*`
copy, then re-learn and re-approve" — a copy that on this path is never created. The end state is
exactly the harm chain docs/open-defects.md R10 describes and marks FIXED, and the round-2 summary
claim at docs/open-defects.md:306 ("A transient meta read retries and never destroys a healthy
sidecar") is false as measured. No residual covers it.

**Failure.** A scheduled `flow run-all` replays an approved, pinned, contract-guarded read flow. The replay
itself succeeds; `_record_run` then hits a >150ms sharing violation reading `<key>.meta.json`. The
healthy sidecar is overwritten in place with `approved=false, contracts=null, shape=null,
read_pin=null, steps_hash=null, quarantine=meta_unreadable`, with no backup on disk. The operator
follows the quarantine text, finds no `.corrupt.*` file, and does `flow release` + `flow approve` —
the flow goes green again with its H9 value gate and shape gate gone and the LLM extractor back on a
replay that was pinned 0-LLM (inviolable #1); a subsequent wrong value returns as a clean success
(inviolable #2). The `release()` variant is worse still: if the transient lands on the read INSIDE
release's `_update_meta`, the mutate sets `quarantine = None` on the poisoned meta and the saved
file is completely blank — the H9 quarantine a human was told to investigate is silently forgotten
AND the contracts that would re-quarantine are gone, with no quarantine left to warn anyone.

**FIXED in 0.79.0** (plan slice S4), both variants confirmed RED against the pre-fix source — the
`release` one leaves the sidecar completely blank, `{"approved": false, "shape": null, ...,
"quarantine": null}`, exactly as described above.

**The fix is PROVENANCE, and the plan was right to insist it is not a field check.** The obvious patch —
"skip the save if `meta.quarantine` is `meta_unreadable`" — fails on the worst variant, because
`release()`'s own mutation SETS `quarantine = None`. By save time the marker a content check would test
has been erased by the very mutation being applied. So the question the writer must ask is not *what
does this meta say* but *where did it come from*, which is known for certain at the point `_load_meta`
picks its branch and cannot be erased downstream.

`_load_meta_with_provenance` returns `(meta, "file" | "absent" | "unreadable" | "corrupt")`; `_load_meta`
is a thin wrapper over it. Only `"unreadable"` refuses a write — the corrupt path has already preserved
the original aside and replaced it, so there is nothing left to protect. The fourth state exists so the
READER can give opposite advice on the two ("retry, nothing was lost" vs "inspect the preserved copy"). `_update_meta` refuses the whole read-modify-write on `"unreadable"`, raising
`MetaUnreadableError` (retryable — a sharing violation clears). THREE states, not two, and both
collapses are bugs: folding `"absent"` into `"unreadable"` stops any new flow's sidecar from ever being
created, and folding `"unreadable"` into `"absent"` is R3.8 itself.

**The policy is REQUIRED per call site** — `on_unreadable` is a keyword with no default, so a new site
cannot inherit one nobody considered (omitting it is a TypeError, which caught five existing call sites
immediately). `"skip"` at exactly three bookkeeping sites (`_record_run`, `AdvisorySink.quarantine`,
`_capture_audit`); `"raise"` everywhere else. The default direction is
chosen so that a forgotten site fails visibly rather than silently: silently not persisting an approval,
a quarantine or a release leaves the operator believing a trust decision took effect when it did not.

**THE SLICE WAS NARROWED AFTER THREE ADVERSARIAL PASSES, and the reason matters more than the fix.**
The core above closed R3.8 and passed its first audit. Everything that went wrong afterwards was in the
REMEDIATION of an audit finding, never in the core — three rounds, and each round's fix was the next
round's defect, twice at the same shape: *applied to a shared mechanism instead of the caller that
lacked the guard*, or *converted one failure into a worse one*.

* Making `_quarantine` skip (to stop a raise replacing the H9 reason and aborting `flow audit`) silently
  discarded the audit JUDGE's finding — which, unlike the deterministic contract check, is not
  re-derivable, and whose evidence artifact is dropped immediately after. `flow audit` then printed
  `[QUARANTINED]` for a flow that stayed approved.
* Adding `cache.delete(key)` on the torn-commit paths put a recipe-destroying call on two
  WRITE-AUTHORING paths, whose own guidance said "re-run learn" — for a write flow, an instruction to
  re-fire a commit that already landed.
* Adding a per-flow guard to `audit_flows` converted a loud abort into `exit 0, nothing to report`,
  because it incremented no counter the exit-code contract reads.
* Adding a `"corrupt"` arm to `_update_meta`'s refusal protected nothing (that path has already been
  preserved aside and replaced) and told the operator "retrying will NOT help" when retrying succeeds.

**All four were cut.** What ships is the core plus the `_save_meta` retry, and the caller-level
ergonomics are FILED rather than fixed under audit pressure. The transferable rule, which is now this
register's third instance: **a guard that converts one failure into another must be audited for what the
NEW failure does to every caller** — and when an audit says a caller lacks a guard, fix that caller, not
the mechanism they share.

**Two structural guards**, following S3's lesson that behavioural tests cannot fail for an exit added
tomorrow: an AST scan requiring every return in `_load_meta_with_provenance` to declare a known
provenance (and asserting all three are still produced), and an AST scan pinning the `best_effort`
opt-outs to an allowlist. The second earned its place immediately — it flagged that
`AdvisorySink.quarantine` (a counter) and `QuarantineSink.quarantine` (which makes every future run
refuse) share a method name, so a bare-name allowlist would have authorised silent failure on the one
whose entire job is to be loud. The allowlist is now qualified by class.

**FILED, NOT FIXED — three residuals from the same adversarial pass, all pre-existing in shape:**

* **R4.13** — `release()`'s GATING read (deciding whether there is a quarantine to clear) has no
  provenance, only its `_update_meta` does. On a flake that recovers between the two reads, the gate sees
  the poisoned meta, logs "releasing quarantine (was: the trust sidecar is UNREADABLE…)", and the
  mutation then clears the REAL on-disk quarantine — a wrong-value quarantine a human was told to
  investigate, released while the log names something else. The fix's own thesis ("a read that could not
  read must not authorize a write") applies here and was only applied to the second read.
* **R4.14 — ✅ FIXED in 0.80.0 (S7a)** — `audit_flows`' candidate loop has no per-flow guard at all:
  `_quarantine`, `audit.judge` (an LLM call) and `audit.drop` can each abort the fleet run, discarding the
  findings of every flow already judged. `run_all` has exactly this guard. A first attempt at adding it
  shipped `exit 0, nothing to report` — whatever lands must increment `unjudged` and print skips
  unconditionally.

  **How the second attempt avoided the first one's failure.** The guard is at the per-flow BOUNDARY in
  both halves of the loop (the gather does four disk reads and was guarded on the first alone; the judge
  half does a network call, two sink writes and a delete) — but the part that mattered was the REPORTING.
  Failures go to a new `AuditRun.errors` list, not to `skipped`: `skipped` is the by-design bucket ("a
  write flow is never judged") which the CLI hides behind `--verbose`, so routing failures there would
  have printed a clean-looking summary for a fleet that was never examined — the guard wearing the
  clothes of the failure it prevents. `errors` prints unconditionally as `[NOT AUDITED]`, and every path
  that appends to it also increments `unjudged`, so `exit_code` needs no new clause of its own. Two
  conditions for one fact is how this fleet's exit code and its webhook drifted apart to begin with.
* **R4.15** — `cli._flow_dispatch` catches only `EmptyFlowStoreError`, so `MetaUnreadableError` reaches
  the user as a Python traceback on six verbs (`approve`, `unapprove`, `release`, `learn`, `record`,
  `audit`), burying its remedy text. `flow replay` and `run-batch` render it cleanly. Also: `Path.exists()`
  can itself raise on some IO errors, a FOURTH outcome the three-state provenance model does not name and
  `_load_meta`'s docstring explicitly denies ("It never raises").

**R4.16** — a refusal in `_learn_once`'s baseline write leaves the NEW recipe paired with the PREVIOUS
  `read_pin`/`shape`/`steps_hash`. Approved flows are caught by the steps-hash gate; an UNAPPROVED READ
  flow is not, and the old pin is fed to the new recipe's page. The obvious instrument (delete the
  recipe) was TRIED IN THIS SLICE AND CUT — `learn()` performs the write during discovery on a declared
  write flow, so "discard and re-run learn" prescribes re-firing a landed commit. Needs the pin
  invalidated by the steps digest.
* **R4.17** — when the sidecar cannot be written, `_do_quarantine`'s H9 value-free reason is replaced by
  a bare IO error: the operator learns about a file problem instead of "this flow returned a wrong
  value", `_record_run` never captures the reason, and the typed `quarantined` code and
  `retryable=False` are lost. A catch was written for this and cut, because it caught only
  `MetaUnreadableError` while the save half raises a bare `OSError` — i.e. it did not deliver what it
  claimed. Fix both halves together.
* **R4.18** — `_save_meta`'s failure surfaces as a bare `PermissionError`/`OSError`, not a
  `FlowReplayError`, so every `except FlowReplayError` on the replay, batch and MCP paths misses it. A
  write that COMMITTED can therefore surface as `PermissionError` from `_record_run` instead of
  `{"status": "confirmed"}`, and the MCP ledger row is never written for it. This is the read/write
  sibling asymmetry one level up from the one this slice fixed.
* **R4.19** — `_reset_learn_baselines` clears `shape` and `contracts` but not `read_pin`, so a
  learn-then-`record` READ flow replays 0-LLM against the previous recipe's pin with both the shape gate
  and the value gate now `None`. Success path, not an error path.

Sequence all of these with S7a, which is the next slice touching these surfaces. *(S7a shipped R4.14;
the rest remain. R4.10's precondition is now satisfied — see the plan.)*

**Two more, filed by S5's sibling check (0.81.0) rather than folded into it:**

* **R4.20** — `FlowCache.put`'s `os.replace` has NO retry, while S4 gave `_save_meta`'s exactly that,
  three attempts with a backoff, after measuring it fail roughly 1 run in 6 under full-suite load. Same
  directory, same platform, same Windows AV/indexer sharing violation, same consequence class: the rename
  is the operation that opens the window on the next reader. This is the sibling-asymmetry shape the
  register names as its most-repeated, one file over from where it was last fixed — and the fix is to
  share ONE durable-rename helper, not to transcribe the retry loop a second time.
* **R4.21** — `record()`'s refusal for the same unattributable-write class stays NON-TERMINAL after S5,
  deliberately: `flow record` is the remedy the learn refusal names, and making its own refusal terminal
  removes the operator's last path (the D0 shape). The residual is real — repeated `flow record` on such
  a page re-fires the write each time — but the harm profile differs from R3.13's: this is a human
  running a command and reading a refusal each time, not an unattended `mode="auto"` loop firing
  invisibly. Revisit only with a remedy that does not depend on `record()` itself.

* **R4.22 — the Windows `ERR_NO_BUFFER_SPACE` recurred (2nd occurrence), and the post-mortem RULED OUT
  what STATUS.md predicted it would implicate.** Recorded here rather than left in a CI comment, which is
  what cost "a day of hypothesis-guessing" the first time.

  Second occurrence on PR #127 (run 31151484849), Windows only; ubuntu passed the same commit, and the
  re-run of the identical commit passed — so it is INTERMITTENT, not deterministic. It failed in
  `test_a_human_clear_makes_the_flow_learnable_again` with `Page.goto: net::ERR_NO_BUFFER_SPACE`, i.e. a
  socket-layer resource error in whichever test happened to be running, exactly as the first occurrence
  was characterised.

  | post-mortem, measured at failure | value |
  |---|---|
  | dynamic port range | 16384 (49152–65535) |
  | TIME_WAIT total / ephemeral | **133 / 133 — 0.8% of the range** |
  | Chromium still running | **0** |
  | process count / total handles | 131 / 45285 |
  | free memory | **13372 MB of 16379** |

  **Ports are not the constraint** (confirming the earlier local measurement) and **nothing leaks**.
  Critically, `STATUS.md` said the browser-pool lever should be pulled "if the post-mortem implicates
  handles or memory" — **it implicates neither**. 13 GB free is not memory pressure, and 45k handles
  across 131 processes has no baseline to be judged against, which is itself the gap.

  **Two limits of the instrument, both worth fixing before the next occurrence.** It runs AFTER the suite
  exits, so every number is post-teardown: `chromium_still_running=0` proves teardown works, and nothing
  here can see the PEAK that actually caused the failure. And there is no healthy-run baseline, so
  "45285 handles" cannot be called high or normal. Sampling during the run, and on success as well as
  failure, is what would turn a third occurrence into evidence instead of another inference.

  **What this does NOT justify — and what was done instead.** A browser pool, on this evidence, for this
  reason: the precondition `STATUS.md` had written down was not met, and this register's own rule is that
  a fix built on a wrong diagnosis is worse than none. **The pool was NOT built** (0.83.0), and the
  deferral note held under its first real test, which is the first time one of them has stopped work
  rather than merely described it.

  What shipped answers a DIFFERENT, measured problem: Windows CI at **21m53s against a 25-minute job
  timeout** on a suite that grows every slice — a deterministic failure approaching, unlike this
  intermittent one. CI now shards the suite across two runners per OS. That also halves per-runner
  Chromium churn, which is the standing suspect here — **stated as a side effect, not as this finding's
  fix.** If `ERR_NO_BUFFER_SPACE` recurs on a sharded runner, that is informative rather than surprising,
  and R4.22 stays open either way: nothing here diagnosed it.

  **THIRD OCCURRENCE (0.84.0, PR #129, run 31212477882) — ON A SHARDED RUNNER, WHICH WEAKENS THE ONLY
  REMAINING HYPOTHESIS.** `tests/test_contracts.py::test_wrong_value_quarantines_and_refuses_future_runs`
  this time — a third unrelated test, consistent with "whichever test happened to be running". Windows
  1/2 only; ubuntu 1/2, ubuntu 2/2 and windows 2/2 all passed the same commit.

  | | occ 2 (unsharded, 21m53s) | occ 3 (SHARDED, 11m02s) |
  |---|---|---|
  | TIME_WAIT / ephemeral | 133 | **215** |
  | process count / handles | 131 / 45285 | 137 / 47262 |
  | chromium still running | 0 | 0 |
  | free memory | 13372 MB | 13410 MB |

  **What this ruled out further.** The standing suspect was cumulative Chromium launch/teardown churn
  (~650 per run). Shard 1 runs 433 of 840 tests, i.e. roughly HALF the churn, in HALF the wall-clock —
  and it failed anyway, with numbers in the same band. So the trigger does not look cumulative, which is
  what a handle/non-paged-pool leak would predict. A burst — many sockets created in a short window —
  fit the evidence better, and would also explain why it lands on an arbitrary test.

  Ports remained ruled out at 215/16384 (1.3%), teardown remained clean, memory remained abundant.

  **OCCURRENCE 4, immediately after 3, on the OTHER shard** (windows 2/2, run 31213427292,
  `test_write_safety_invariants.py::test_a_learned_write_is_never_cached_ungated_or_replayed_unkeyed`):
  TIME_WAIT 206, handles 47097, chromium 0, free 13352 MB. Four occurrences, four different tests, both
  shards, two consecutive PR runs — the frequency is up, not down.

  **What occurrences 3 and 4 together establish.** Sharding halved the cumulative churn per runner
  (~650 launches → ~335 and ~312) and left the RATE essentially unchanged (~30/min in all three
  measured configurations, since the wall-clock halved too). The failure persisted in both halves. A
  cumulative handle/non-paged-pool leak predicts that halving the total helps; it did not. **So the
  trigger tracks rate or burst, not cumulative total** — the first mechanism-level discrimination in
  four occurrences, and it is what the sampler below is built to confirm or kill.

  **THE INSTRUMENT SLICE (0.85.0, `ci/resource-sampler`).** Rather than a fifth hypothesis, the two
  filed gaps got closed: `scripts/sample_resources.ps1` samples every ~5 s THROUGHOUT the run, and the
  summary step runs on `always()` so a PASSING run produces the baseline that four failure-only
  post-mortems could never provide. It records **non-paged pool**, which is the resource WSAENOBUFS is
  actually about and which no occurrence has ever measured — every hypothesis so far has been about
  something else. The CSV uploads as an artifact so two runs can be diffed directly.

  It reports numbers and no verdict, deliberately. A diagnostic that renders its own opinion is how four
  occurrences each got explained away.

  **FIRST BASELINE (0.84.0, run 31222830257, both windows shards PASSING) — and it invalidates the four
  post-mortems above rather than confirming them:**

  | | pass 1/2 | pass 2/2 | at "failure" (occ 2 / 3 / 4) |
  |---|---|---|---|
  | TIME_WAIT max | **386** | 377 | 133 / 215 / 206 |
  | handles max | **54262** | 52861 | 45285 / 47262 / 47097 |
  | non-paged pool max | 189.9 MB | 195.9 MB | never measured |
  | chrome procs max | 5 | 8 | 0 (post-teardown) |

  **A healthy run peaks HIGHER than anything the failing runs reported.** That is not a paradox, it is
  the gap: the post-mortem samples after the suite exits, so its numbers describe teardown, not the
  event. Every "ports / handles / memory are ruled out" statement in the entries above was therefore
  drawn from the wrong moment. **Treat them as void, not as evidence** — ports genuinely do look fine
  (386 of 16384 = 2.4% at peak, now measured properly), but that conclusion now rests on the baseline,
  not on the post-mortems that claimed it first.

  Non-paged pool sits at ~190 MB and barely moves across a whole run. If a failing run shows the same,
  the pool hypothesis is dead and the answer is somewhere none of the five hypotheses have looked.

  *(The sampler's own first draft formatted with `N1`, which inserts a thousands separator and split
  `6,434.5` across two CSV columns — caught by running it once before trusting it. An instrument that
  lies is worse than none, and this one was built to settle an argument.)*

  The pool's own ceiling was measured before it was declined, so the next person does not re-derive it:
  **356.7 ms per session with its own browser vs 84.7 ms sharing one — 272 ms, ~2.9 min over ~650
  sessions** — and it cannot reach the suite at all without moving every test onto a session-scoped
  event loop, since a Playwright `Browser` is bound to the loop that created it.

* **R4.23 — `test_flows_dry_run_holds_a_real_write_flow` failed once under load and I could not diagnose
  it.** Observed while validating the shard split (0.83.0): shard 1 failed it with a Playwright error,
  and the same shard re-run passed 433/433, having passed in isolation (3.6s), within its file (12/12)
  and within shard 1's dry-run subset (11/11). The full suite had passed 836 minutes earlier. So it is
  INTERMITTENT and **not** a shard-ordering dependency — which was the specific risk sharding introduces
  and the reason it was checked.

  **What makes it worth a finding rather than a shrug**: the property it guards is that a DRY RUN HOLDS A
  REAL WRITE — i.e. that a previewed write never reaches the server. A flaky gate on that is the same
  problem as H7/S17 (`test_record_write_deferred_write_outside_its_turn_is_refused`, flaky under
  full-suite load only), and for the same reason: a guard that fails at random cannot be trusted to mean
  anything when it fails for real.

  **The instrument failed me and that is fixable.** The error text was truncated by `| tail` to `"pla..."`
  so the actual Playwright error is unknown. Next occurrence: capture the whole run (tee to a file, not
  `tail`) with `--tb=long`, before theorising. Do NOT re-run until green and move on — that is how a
  suite normalises its own flakes, and this project's de-flake rule forbids it.

  Sequence with **S17**: same family, same load-dependence, and S17 already owns "reproduce under
  artificial load first, do not silence with reruns, do not weaken the production bound".

* **✅ FIXED in 0.88.0 — R4.26 — CRITICAL. Under load the recorder credits a DEFERRED write to the NEXT
  click, caching the real commit UNGATED and UN-KEYED.** Measured, not read: this is R3.2's harm class
  on the `record` path.

  **THE MECHANISM, NOW MEASURED — and the inference below was right about the what, wrong about the
  when.** The filing said "do not fix from that inference alone; instrument the turn counter first".
  Doing so paid twice. First, instrumenting the turn counter SUPPRESSED the defect: 0 reproductions in
  150 loaded runs with an in-page probe, against 1-in-40 without one. A Heisenbug, so the trace was
  never going to arrive by waiting. Second, the deterministic harness built instead REFUTED the obvious
  story. The turn boundary is closed by a `setTimeout(..., 0)`, and the guess was that an overdue timer
  simply sorts earlier and preempts it. It does not — not on its own:

  | how the LATER commit arrives | which runs first | outcome |
  |---|---|---|
  | a TIMER task (a synthetic `.click()`) | the freshly-armed reset | unattributed, correct, 3/3 |
  | an INPUT task (a real dispatched click) | the OVERDUE write timer | **misattributed, 3/3** |

  Blink prioritises input, so the later commit's `store` runs, arms its reset, and returns — and the
  overdue write timer then runs INSIDE that commit's still-open turn, before the reset it was armed
  after. The agent's clicks are input tasks. That is the whole defect, and it is why load is required
  in the field (the renderer must be starved past the write's due time) while nothing about the logic
  is machine-speed dependent — the contradiction that made this look impossible for three releases.

  **The fix: take the turn boundary from the page instead of from the clock.** A timer is not a
  boundary, it is a bet on the scheduler. `window.event` is the spec's "an event dispatch is in
  progress" signal — set for the duration of a dispatch and restored when it ends — so it is still set
  through the dispatch's microtask continuations, which ARE the commit's turn, and gone in a timer or
  network continuation, which are not. `attributedSeq()` now requires it in addition to
  `__ucturn === 1`, read through the native getter captured before any page script runs.

  Chosen by measurement across every write shape the recorder supports, because the tighter rules fail:

  | rule | sync | microtask | submit button | `form.submit()` | requestSubmit | Enter | setTimeout | await |
  |---|---|---|---|---|---|---|---|---|
  | same Event identity | ✓ | ✓ | **✗** | ✓ | **✗** | **✗** | ✓ | ✓ |
  | commit's `eventPhase` | ✓ | ✓ | **✗** | ✓ | ✓ | **✗** | ✓ | ✓ |
  | **inside a dispatch** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

  Identity and `eventPhase` both refuse the ordinary form write — a native submit button dispatches a
  `submit` event, so the current event is no longer the click and the click's phase has returned to 0.
  Either would have shipped the D0 over-refusal shape wearing an attribution hat. Only the PRESENCE of
  a dispatch separates the two groups; the nested-synthetic-commit case stays caught by `__ucturn > 1`.

  **RESIDUAL, stated rather than implied.** A write issued from a NON-commit event dispatch — a `load`,
  `message` or `visibilitychange` handler — that runs inside another commit's still-open turn is still
  attributable to that commit. It needs the same starved-reset window R4.26 needed, so this is a
  narrowing (any task → an event-dispatch task), not a closure. It was NOT closed by requiring the
  current event to be the commit's own, because that is the identity rule in the table above and it
  costs every form write; and a denylist of lifecycle event types is the "enumerate the loud outcomes"
  error this file already records. Pinned by the `timer`/`microtask` cells' premise assertions, which
  fail loudly if a cell stops producing the shape it is named for.

  **The test is a DIMENSION, not a bespoke case** — `tests/test_write_safety_invariants.py` now runs the
  property over the `record` entry point too, crossed on WHEN the write leaves the browser (sync /
  microtask / timer / timer-armed-by-an-earlier-commit). That file previously covered only
  `run_cached(mode="learn")`, whose attribution is a different mechanism entirely, so the recorder's
  had no property-level coverage at all — which is the structural reason this survived. Both directions
  are load-bearing: sync and microtask MUST stay learnable, or the fix is satisfied by refusing
  everything.

  The R4.26 cell is deterministic — 8/8 RED against pre-fix source, reproducing the field signature
  `steps=[('click Commit', False), ('click Next', True)]` exactly. It gets there WITHOUT racing: the
  field ordering is 1-in-40 under load and only 2-in-10 even when the page starves its own renderer
  (Playwright's CDP traffic interleaves at the block boundary), so the cell instead reproduces the same
  decision-point state via two documented orderings — a `window` capture listener runs before the
  recorder's `document` one, and equal-delay timers fire in arming order.

  **Each half of the fix was mutation-checked, because "the cells are green" is not evidence of which
  line made them green:**

  | mutation | cells RED |
  |---|---|
  | drop `&& inDispatch()` (revert the fix) | `timer_armed_by_an_earlier_commit`, `timer_with_window_event_shadowed` |
  | keep the fix, read `window.event` NAIVELY | `timer_with_window_event_shadowed` only |

  So the dispatch check is what closes R4.26, the native-getter capture is independently load-bearing
  rather than defensive decoration, and the two liveness cells are not passing merely because
  everything is refused. **Verified hostile**: `window.event` is a *configurable* accessor, a page can
  redefine it to return a truthy fake, and only the getter captured at init still reads the truth.

  **The change is monotone in the safe direction** — `attributedSeq` can now only return `null` where
  it previously returned a seq — so it cannot introduce a new silent-wrong. The entire risk of this fix
  is capability loss, which is what the liveness cells and the 88 existing recorder/write tests
  measure. That asymmetry is why this one is not the fourth green-but-wrong change in this area.

      steps=[('click Commit', False), ('click Next', True)]      # 1 run in ~40 under CPU saturation

  `click Commit` arms the write and is cached `mutating=False`. `click Next` writes nothing and carries
  the gate. On replay Commit fires the commit with **no mutation gate, no precondition and no
  Idempotency-Key**, while the barrier sits on a step that never writes. Inviolable #3.

  **Rates**, all under `os.cpu_count()` busy-spin workers: 1/8 and 1/20 at the pytest level, 1/40 in a
  direct harness. Zero in 12 unloaded runs and 5/5 in isolation historically — which is precisely why it
  has looked like a flaky test for three releases.

  **What is measured vs inferred, kept apart on purpose.** MEASURED: the cached gate lands on the benign
  click while the committing step is ungated. INFERRED (consistent with the evidence, not proven): the
  120 ms timer fires inside the NEXT click's synchronous turn, so `__ucturn === 1` and `attributedSeq()`
  returns `__uclast` = Next. The in-page logic is turn-based and therefore machine-speed independent IN
  PRINCIPLE — which is what made this look impossible — but the turn BOUNDARY is closed by a
  `setTimeout(..., 0)`, and a starved reset plus a delayed timer is a scheduling question, not a logic
  one. **Do not fix from that inference alone; instrument the turn counter first.**

  **THIS IS WHY S17 MUST NOT BE "DE-FLAKED".** `test_record_write_deferred_write_outside_its_turn_is_refused`
  is not flaky — it is faithfully reporting an intermittent PRODUCT defect. Every tempting remedy the S17
  bullet already forbids (rerun to green, widen `write_attrib_ms`, loosen the assertion to "refused OR
  cached") would have SUPPRESSED a live write-safety hole, and the last of those would have done it
  silently. S6 is blocked on S17 because its oracle must be deterministic; the oracle is honest and the
  MECHANISM is not.

* **R4.27 — OPEN. The wire promotion marks ordinary GraphQL-style READS as writes, and it is the
  mirror of the keyword classifier's false positives.** Measured while pricing AB-1's fix (0.89.0), not
  read: twelve ordinary read controls that query over `POST /graphql` were learned, and **all twelve
  cached as write flows**. Seven are the keyword classifier doing what `MUTATING_KEYWORDS` always does.
  The other five — `Filter results`, `Export CSV`, `Next page`, `Refresh data`, `View details`, not one
  of which contains a keyword — were promoted by the WIRE, from its own log line:

      learn: step 0 'go to the next page' wrote on the wire — caching it as a WRITE (the classifier said otherwise)

  `is_write_request` is method-based (`POST/PUT/PATCH/DELETE`, minus telemetry hosts) and a GraphQL read
  is a POST, so the promotion cannot tell a query from a commit. The cost is not theoretical: such a
  flow becomes `is_write_flow`, so it is approval-gated, refused from MCP and `run_batch` until a human
  declares it, and denied heal/replan and the auth-refresh retry.

  **This is already acknowledged, in the wrong register.** The promotion's own comment states the
  residual and calls it "the fail-loud direction and the right trade"; D0's entry calls it "the wire
  promotion's stated read-POST residual (every GraphQL-backed SPA read)". Neither carries a number, and
  5/5 on non-keyword controls is a different claim from "a residual". Filed so the disposition is made
  deliberately rather than inherited.

  **Do not reach for a URL heuristic.** Excluding `/graphql` is a denylist — the "enumerate the loud
  outcomes" error this file already records — and a GraphQL MUTATION travels the same URL, so it would
  un-gate real writes to spare reads. This is the same no-oracle shape as `MUTATING_KEYWORDS` and
  `landed`: at the moment of the decision nothing in the system knows. Any fix must come from evidence
  the page can produce, and the direction of error must stay conservative.

* **R4.25 — CORRECTED. It is not a class of three; it is one real defect, one over-specified test, and
  one non-reproducer.** The original entry unified three load-dependent failures on surface similarity.
  Reproduced under saturation, they are not the same thing:

  | test | 8 iters under load | what it actually is |
  |---|---|---|
  | `..._deferred_write_outside_its_turn_is_refused` | 1/8 | **R4.26** — a real product defect |
  | `..._load_armed_write_with_single_commit_is_refused` | 3/8 | a TEST defect (see below) |
  | `test_flows_dry_run_holds_a_real_write_flow` (R4.23) | **0/8** | did not reproduce; different mechanism |

  The load-armed failure is **not a safety hole**: `res.cached is False` held every time — the flow WAS
  refused, via a different and equally correct path (under load the load-armed POST lands before the
  demo, so A8's confirm-baseline probe sees `LOAD-SAVED` already on the entry page and refuses there).
  The test pins one refusal by asserting `"single" in res.note`, so it fails when a second legitimate
  refusal wins the race. Fix the ASSERTION — refused, not cached, not re-fired — not the guard.

  **✅ The assertion was fixed in 0.88.0**, alongside R4.26 because it is the same cluster and the same
  lesson: an intermittent red is how a real defect stays invisible. It now asserts the property —
  refused, nothing cached, the write never re-fired, and a non-empty reason — instead of which of two
  correct refusals won. It still fails if the load-armed write is attributed to the benign click, which
  is the thing it exists to catch. Note the 3-in-8 rate was NOT reproduced afterwards (0 in 25 loaded
  runs), but under a lighter harness — four tests per pytest process rather than the full file — so
  that is not evidence the race is gone, only that it was not provoked.

  **The lesson is about the filing, not the code.** Three observations were unified into one finding on
  the strength of their shape, and reproduction dissolved two thirds of it. This register's own rule —
  reproduce before fixing — applies to CLASSIFYING a defect as much as to repairing one.
  Observed on the S7b run (0.87.0):

      FAILED tests/test_record.py::test_record_write_load_armed_write_with_single_commit_is_refused
      (assert res.cached is False and "single" in res.note)

  It passes in isolation in 1.9 s. The failure is at the ASSERTION, not a timeout, so under full-suite
  load the refusal took a DIFFERENT PATH — the load-armed POST landed at a different moment and
  attribution came out otherwise. Not caused by the slice that surfaced it: S7b's diff is CLI exit codes
  plus the `sweep_verdict` extraction, and `record()` reaches neither.

  **The cluster is what matters, not the third instance.** Every load-dependent test found so far guards
  a WRITE REFUSAL:

  | | guards |
  |---|---|
  | H7 / **S17** `test_record_write_deferred_write_outside_its_turn_is_refused` | a deferred write outside its turn is refused |
  | **R4.23** `test_flows_dry_run_holds_a_real_write_flow` | a dry run HOLDS a real write |
  | **R4.25** `test_record_write_load_armed_write_with_single_commit_is_refused` | a load-armed write is not attributed to a benign click |

  Three independent tests, one shape: they all pin a refusal whose input is WHEN a request arrives
  relative to a step. That is not three flaky tests, it is one property being verified unreliably — and
  it is the property inviolable #3 rests on. A guard that fails at random cannot be trusted to mean
  anything when it fails for real, and, worse, cannot be trusted to be MEANINGFUL when it passes.

  **This widens S17.** Its scope was one test; it should be "the timing-dependent write-refusal guards",
  reproduced under artificial load together, because a fix for one is likely a fix for all three — and
  because S6 is blocked on S17 for exactly the reason that its oracle must be deterministic.

* **R4.24 — a LOCALHOST round-trip stalled past the 5 s action budget on the Windows runner. This is NOT
  R4.22, and the sampler is what proves it.** (0.84.0, PR #129 run 31230301214, windows 1/2.)

      FAILED tests/test_contracts.py::test_write_flow_is_not_contract_checked
        playwright TimeoutError: Locator.wait_for: Timeout 5000ms exceeded.
        Call log: - waiting for get_by_text("Order placed") to be visible

  The fixture is a REAL form POST (`<form method='post' action='/order'>`) to a local
  `ThreadingHTTPServer` that returns the confirm page. Not a `fetch().then()` reveal race — there is no
  JS in the path — so the reveal-synchronously rule (CLAUDE.md) does not apply and would not have helped.
  A localhost POST + navigation should complete in milliseconds; it exceeded five seconds.

  **Why it is filed separately from R4.22, which is the whole point of having built the instrument.**
  Same platform, same suite, adjacent tests — the tempting move is to call it one flake. The sampler
  says otherwise: at this failure the resource profile is indistinguishable from a PASSING run.

  | | this failure | baseline 1/2 (pass) | baseline 2/2 (pass) |
  |---|---|---|---|
  | TIME_WAIT max | 393 | 386 | 377 |
  | handles max | 53561 | 54262 | 52861 |
  | non-paged pool max | **193.2 MB** | 189.9 | 195.9 |
  | processes / chrome max | 158 / 8 | 157 / 5 | 154 / 8 |

  So this one is not pool, handle or port pressure. Lumping it into R4.22 would have buried the one
  measurement that distinguishes them, which is exactly how the previous four occurrences each got
  explained away.

  **What the baselines also did to R4.22's own history — read this before citing those numbers.** Every
  post-mortem figure (TIME_WAIT 133/215/206, handles 45-47k) was captured AFTER the suite exited, and a
  healthy run PEAKS HIGHER during the run (TIME_WAIT 386, handles 54k). **The four "we ruled out ports /
  handles / memory" conclusions were drawn from the wrong moment and never had standing to rule anything
  in or out.** They are not evidence against those hypotheses; they are evidence of nothing. R4.22 stays
  open with its measurements downgraded accordingly.

  **THE OBVIOUS MITIGATION WAS BUILT, THEN NOT SHIPPED — and what stopped it is worth more than it was.**
  Setting `ULTRACUA_ACTION_TIMEOUT_MS=15000` in CI looked free: the knob already existed, the shipped
  default stays 5000, no assertion weakens. It was implemented, and the full suite then failed
  `test_drift_bench.py::test_the_baseline_is_current`.

  That is not a flake, it is the bench's provenance guard doing its job. `baselines/drift_v2.json` PINS
  `action_timeout_ms` so a rate measured under one configuration is never compared against another, and
  the bench deliberately inherits the ambient setting — its own note records that forcing a value was a
  past mistake which made "part of what the resilience number measured the speed of the machine measuring
  it". **An ambient CI override reintroduces exactly that bias**, and the guard refused it.

  The workaround (pin the timeout in the test harness) needs `object.__setattr__` on a FROZEN dataclass —
  defeating a deliberate immutability decision to work around a mitigation for a SINGLE observation. On
  one occurrence that trade is not worth it, and this register's own rule is that a fix on thin evidence
  is worse than none. **So R4.24 ships unmitigated**, with the sampler (0.84.0) in place to characterise
  the next occurrence.

  What DID ship from the attempt: `tests/test_production_timeouts.py`, an AST guard that the shipped
  timeout defaults have not been raised — verified RED against exactly the mistake a future reader will
  be tempted by (raise the default instead of the env var) — plus a guard that the env knob itself
  survives, since it is load-bearing precisely when unused.

  **The open question, for whoever picks this up.** Two failure shapes on the same platform — fail-fast
  (`WSAENOBUFS`) and fail-slow (a 5 s stall) — are both consistent with Windows socket-establishment
  pressure on localhost, which nothing has yet measured. The sampler covers pools, handles, TIME_WAIT and
  processes; it does NOT cover connection-establishment latency or the fixture servers' accept backlog.
  That is the next instrument, if a sixth occurrence justifies one.

### ✅ FIXED in 0.80.0 (the VISIBILITY half; the escape-hatch half is argued below and stays open) — R3.9. The new unconditional skip has no escape hatch and is invisible to the cron contract the surface documents — `run-all` exits 0 and the alert webhook stays silent for a fleet that ran nothing

**What shipped (S7a), and the shape it took.** The finding names two defects and they have different
answers, so they are recorded separately rather than closed together:

* **Visibility — FIXED.** `fleet_verdict(results, *, allow_empty=False)` is now the single definition of
  what cron is told, and BOTH channels consume it. They previously carried a copy of the condition each
  (`SystemExit(1 if failed else 0)`, `if failed and args.alert_webhook`), which is the mechanism of the
  finding: a third bucket satisfying neither was invisible in both. Quiet is an **allowlist**
  (`{"ok", "skipped"}`), not a list of loud statuses — that inversion is what makes a `FleetRun` status
  added tomorrow loud by default, and enumerating the loud ones is exactly how `skipped` came to feed
  nothing. Exit 1 = something loud; **exit 2 = nothing ran**, on the `EmptyFlowStoreError` precedent
  whose own comment already covers this case ("never 0, which is what made a wrong-cwd cron job look
  healthy forever"); 0 = work happened.
* **The escape hatch — STILL OPEN, and deliberately not invented here.** There is none to offer: for the
  population this hits (a monitoring READ whose fetch-POST the wire promotion marks `mutating`),
  declaring `spec.mutate` demands a confirm a read cannot satisfy and `flow record` re-derives the same
  verdict. A flag to run them would be consent to fire unverifiable writes on a schedule, which the skip
  exists to refuse. The real lever is D0's (ii) — persist WHY a step was marked mutating — which is
  blocked on S6/S17. **Until then, visibility IS the whole of the remedy**, and the refusal text now says
  so instead of naming remedies that do not work for this population.

**Two skip classes were reclassified, not one.** The undeclared write became `failed` ("refused a run"),
matching what `_one`'s unreadable-recipe guard thirty lines up already did for the same reason. The
second was found by the sibling check and is the S4 shape one reader over: `run_all` decided whether to
run a flow at all from `_load_meta`, which after S4 can SYNTHESISE `approved=False` from a sidecar it
could not read — byte-identical to a human's `flow unapprove`. So one AV sharing violation dropped a
flow from the tick under a reason naming a human act that never happened. It reads provenance now. The
corrupt branch needed a third case: it PERSISTS a quarantine, so from the second tick on the sidecar
reads back cleanly and the flow would go quiet forever, one tick after it went loud.

**What the fix had to be stopped from breaking, and this is the load-bearing half.** "Alert on
everything" satisfies every test above and is a regression: an operator with one declared write flow in
the store would go red nightly for a standing configuration choice, and that alert reaches `|| true`
within a week — the channel dies for everyone. A chosen skip beside real work stays quiet, pinned.
The same trap one level in: **an existing test caught that `--allow-empty` was being overridden** by the
"nothing ran" rule. That flag is documented consent for a fleet home holding zero flows; it is NOT
consent for a store that resolved flows and ran none of them. One word apart, now pinned both ways.

**Three existing tests had to change, and one of them was passing for the wrong reason.**
`test_run_all_classifies_ok_failed_skipped`'s write-flow case was never learned or approved, so it
reached the write branch only because the write gate happened to run before the approval check — it
would have reported "write flow" with the mutate spec removed. The reorder exposed it; the fixture now
learns and approves that flow, so the assertion means what it says. The other two are the round-2
undeclared-write pins, whose invariant (NOT RUN) is unchanged and which now also assert the run is loud.

**The sibling that was NOT fixed here, named so S7b cannot miss it.** `_flow_canary` has the identical
shape — `stale = status in ("stale", "error")`, so an all-`not-learned` fleet exits 0 (CLI-4). It is
already sequenced in S7b and is left there rather than pulled in ad hoc, but it is now a
KNOWN-IDENTICAL shape rather than an independent finding: whatever S7b does there should be
`fleet_verdict`'s treatment, not a third hand-rolled condition.

*Original finding follows.*

*low, lens `surfaces`, inviolable #2, reproduced by an independent refuter *(symptom real, stated cause corrected by the refuter)**

**Where.** `src/ultracua/flows.py:2317-2329 (the new UNDECLARED-write skip) against src/ultracua/cli.py:750 (`if failed and args.alert_webhook:`) and src/ultracua/cli.py:752 (`raise SystemExit(1 if failed else 0) # cron alerts on a non-zero exit`)`

**Mechanism.** `FleetRun(status="skipped")` is neither `ok` nor `failed`, so it feeds neither of the two channels
`run_all`'s own docstring points cron at ("alert on a non-zero exit (any flow failed)") — exit code
and `--alert-webhook`. Before this diff a flow could only become `skipped` by a human act
(unapproving it, or declaring `spec.mutate`). This diff creates the first skip class a flow can
enter with NO human act: `_author_steps`'s wire promotion marks any non-telemetry POST fired in an
act window as `mutating=True`, and flow.py:465-471 accepts as a stated cost that "a click-triggered
non-telemetry read-POST (a GraphQL/RPC query)" now does this — which on a modern SPA is the ordinary
shape of a READ. Where that cost used to be "a wasted re-sample", it is now permanent removal from
the scheduled fleet, and the escape hatch flow.py names ("declare it, never expose it silently")
does not exist for a read: declaring `spec.mutate` makes `_preflight_row` demand a confirm the read
has nothing to satisfy. `--include-writes` correctly refuses. So a re-learn of a monitoring flow can
silently retire it from cron, and cron keeps reporting green.

**Failure.** An operator re-learns a dashboard read flow after a site redesign; its "Load" button now goes
through `fetch('/graphql',{method:'POST'})`. The promotion caches `mutating=True`. From the next
tick on, `flow run-all` prints `[SKIP]`, writes `"status": "skipped"` to its `--json` record, exits
0, and posts nothing to `--alert-webhook`. Nothing has been read since the redesign; the monitoring
fleet is dark and every automated signal says healthy. In the limit the whole fleet reclassifies and
`flow run-all` reports `0 ok, 0 failed, N skipped` with exit code 0.

### ✅ FIXED in 0.84.0 (S8) — R3.10. The new redaction has no minimum-term-length floor, unlike the `audit._redact` sibling it is explicitly modeled on — a short or common-substring term shreds the extractor's input and the observation

*low, lens `secrets`, confidentiality (no inviolable), reproduced by an independent refuter *(symptom real, stated cause corrected by the refuter)**

**THE SEVERITY LINE ABOVE IS WRONG AND IS LEFT VISIBLE RATHER THAN EDITED.** "Confidentiality (no
inviolable)" does not describe this finding's own Failure paragraph, which ends in the extractor returning
a wrong number read out of a mangled span — **inviolable #2, silently WRONG**. The `found=False` half
fails loud and is survivable; the other half is not. Third slice running where the register understated
its own finding (see S5 and R4.10), which is now a pattern worth naming: **a filing's SCOPE and SEVERITY
are provisional, not just its prescribed fix.**

**Reproduced end-to-end before fixing**, through a real flow with a secret slot resolving to `bo`, by
recording what the extractor actually received:

    PAGE TEXT: Support In[REDACTED]x: 12 unread. Re[REDACTED]ot at 3pm. Open tickets: 47

`Inbox` and `Reboot`, shredded by coincidence, in the real prompt.

**The fix is one definition, not a third copy.** `snapshot.REDACT_MIN_LEN` + `redact_terms` +
`apply_redactions`, with all three channels routed through them — including `audit._redact`, whose
behaviour is byte-identical (it always had the floor; this is the definition moving, not a policy change
on that path). A fourth channel added tomorrow inherits the floor instead of re-deriving it, which is the
whole shape of the finding: `_redacted_body_text`'s docstring CITED the audit sibling by name and did not
carry the guard it cited.

**What the floor does NOT close, pinned as a test that demonstrates the surviving damage** rather than
described in prose: a term at or above the floor is still an unconditional substring replace, so `1234`
still mangles `Open tickets: 12345` and `smith` still mangles `Blacksmith Ltd`. Nothing string-based
separates a secret appearing AS a secret from the same characters appearing legitimately — the
`MUTATING_KEYWORDS` problem one surface over, with the same conclusion: the rule is a guess, so keep its
blast radius small rather than pretending it is precise.

**What the floor COSTS, also pinned, because it is a decision and not a free win.** A secret of 1-3
characters is no longer scrubbed on the channel that reaches the MODEL. Paid because `audit._redact` has
always made that trade for the identical terms on the disk channel, because a 1-3 character secret is not
meaningfully one, and because the damage it does when scrubbed is severe, silent and measured. A 4-digit
PIN is at the floor and still redacts. **If this trade is ever judged wrong, the fix is NOT to drop the
floor** — that reinstates R3.10 — **it is to stop putting the login USERNAME in the term list**, which is
where the short non-secrets come from.

`SlotSpec`'s "ENFORCED at four points" docstring was updated in the same commit: it promised the value is
scrubbed from every Observation, which the floor makes false for short secrets. A fix that silently
falsifies a promise elsewhere in the codebase is a doc lie, and this register has fixed four of those.

**Where.** `src/ultracua/flows.py:718-738 (`_redacted_body_text`, new in this diff) and src/ultracua/snapshot.py:278-295 (the five-field redact loop, new in this diff), versus the sibling they cite by name: src/ultracua/audit.py:194-201 (`_redact`, `if val and len(str(val)) >= 4`). Term list built by src/ultracua/flows.py:1291-1303 (`_secret_values`), which includes `LoginSpec.username_env`'s value.`

**Mechanism.** `_redacted_body_text`'s docstring says the guard "already existed 500 lines away in this same file:
`_capture_audit` scrubs the IDENTICAL page-text channel with the SAME `_secret_values(spec)`". It
does — but `audit._redact` deliberately refuses any term shorter than 4 characters, and neither the
new extractor scrub nor the new five-field `capture` scrub carries that floor. Independently of
length, the scrub is an unconditional `str.replace`, so any term that is a legitimate substring of
page content is excised from `text`, `url`, `title` and every element `name` before the strong-tier
extractor and the agent see them. `_secret_values` puts the LOGIN USERNAME in that list (default env
`ULTRACUA_USERNAME`), and usernames are short and commonly substrings of ordinary copy. To the
extractor, `[REDACTED]` is indistinguishable from a genuinely absent value.

**Failure.** A read flow with a `LoginSpec` whose username/secret is short or a common substring (a numeric
customer id `1234`, a handle like `bo`, a PIN). Every replay that reaches the extractor — i.e. every
read flow without a resolved `read_pin`, and every write flow with a readback — hands the strong
tier a page text with those spans excised: `Open tickets: 12345` becomes `Open tickets:
[REDACTED]2345`. The extractor then returns a mangled value, or `found=False` on data that is
plainly on the page. The `found=False` branch fails loud; the other returns a wrong number from a
mangled span. I could not prove the wrong-answer half key-lessly (it is LLM-mediated), so I claim
only the input corruption, which is deterministic.

### R3.11. Narrowing `except Exception` to `(ValueError, UnicodeDecodeError, OSError)` broke `_load_meta`'s explicit "It never raises" contract — a deeply nested meta raises RecursionError straight out of `health()`, tracebacking `flow status` and the MCP `tools/list` loop on one bad flow

*low, lens `trust`, confidentiality (no inviolable), reproduced by an independent refuter*

**Where.** `src/ultracua/flows.py:608-615 — the `except (ValueError, UnicodeDecodeError) as exc:` / `except OSError as exc:` pair that replaced 9d7de9c's single `except Exception as exc: # noqa: BLE001 — corrupt/torn/unreadable`.`

**Mechanism.** `json.loads` raises `RecursionError` (a plain `Exception`, not a `ValueError`) on nesting past the
interpreter limit. The old bare `except Exception` swallowed it and routed to
`_refuse_unreadable_meta`, satisfying the docstring's stated guarantee at flows.py:587: "It never
raises: `health()` and the MCP tools/list loop must not traceback on one bad flow." The new tuple
does not include it, so it propagates. `health()` (flows.py:1409) has no try/except of its own — the
docstring at flows.py:1412-1413 leans entirely on `_load_meta` never raising ("one corrupt flow must
not take down the fleet view"). This is fail-loud rather than silent-wrong, and the input is a
locally written file, so realistic probability is low; it is a regression the diff introduced, not a
pre-existing gap.

**Failure.** A meta sidecar corrupted into deeply nested brackets (a truncate-then-patch, a bad hand-edit, a
filesystem transposition) makes `flow status` and the MCP `tools/list` enumeration abort with an
unhandled RecursionError instead of showing that one flow as quarantined — the entire fleet view is
lost to one bad file, which is the specific outcome both docstrings promise cannot happen.


**Nothing was refuted this round.** In rounds 1 and 2 the refutation pass killed findings outright
and cut several severities. Here every one of the 11 held, each reproduced by a second agent with
its own probe. Read that as a statement about the code under audit, not about the refuters.

### R3.12. `DryRunArbiter`'s act window has the SAME overlapping-tail shape R3.2 just removed — on a multi-write flow a deferred write is labelled with the NEXT commit's step and intent in the report a human approves from

*medium, lens `writes`, NOTICED while redesigning R3.2 — **not reproduced end to end**, see below*

**Where.** `src/ultracua/dryrun.py:249-263` (`open_window` / `close_window` / `_state`), called from
`src/ultracua/flow.py:1092` and `:1216-1217`.

**Mechanism (from the code, not from a run).** `open_window` does
`self._window.update({"open": True, ..., "step": step, "intent": intent, "key": key})` — one slot,
overwritten by each mutating step. `close_window` sets `until = now + grace_ms` with
`grace_ms=settings.write_window_ms` (2000 ms). Replay steps are tens to hundreds of ms apart, so on a
multi-write flow commit B's `open_window` lands well inside commit A's 2s tail and takes the slot. A
write deferred from A and arriving then is recorded as `HeldWrite(step=B, intent=B.intent)`. This is
the identical single-slot overlap that R4 fixed in `_author_steps` and that R3.2's redesign has now
removed there by draining instead of arbitrating — the sibling that was not updated either time.

**Why it is ranked medium and not high.** `dry_run` HOLDS every write and releases no non-idempotent
method, so nothing mis-fires: the damage is confined to the report. It is also partly
self-revealing — `HeldWrite.idempotency_key` is read from the actual request headers while
`step`/`intent` come from the window, so a mis-labelled row shows commit B's intent beside commit A's
key. It is still the report a human approves a write flow from.

**Not reproduced, and what would.** This is a code-reading finding; the register's own rule is that a
fix built on a wrong diagnosis is worse than none, and two earlier items in this file were
misdiagnosed exactly this way. To reproduce: a two-commit flow whose first commit POSTs on a
~100 ms `setTimeout`, dry-run it, and check whether the held write's `step`/`intent` name the first
commit or the second. If the arbiter's existing `drain(write_settle_ms)` already covers the gap in
practice, the finding is refuted and should be recorded as such.

**Fix shape, if confirmed.** The same one, and it is cheap here: `close_window` already runs after a
`drain`, so the exclusivity mechanism exists — it just needs to bound attribution to the drained
window rather than to a 2s tail that outlives it. Do not add a second slot; that is the patch R4
tried.

### ✅ FIXED in 0.81.0 — R3.13. A refusal is NON-TERMINAL: nothing is remembered, so every `mode="auto"` invocation re-authors the flow and re-fires the write un-keyed

**Re-measured against main @ 0.80.0 before a line of fix was written**, because this entry's own history
is a draft that was measured on a fixture which does not refuse and reached the opposite conclusion. Six
releases and five plan slices had changed nothing:

    flows._learn_once          run1 POSTs=1  run2 POSTs=2  run3 POSTs=3   0 keyed
    run_cached(mode="auto")    run1 POSTs=1  run2 POSTs=2  run3 POSTs=3   0 keyed

    after (0.81.0), both paths:  run1 POSTs=1  run2 POSTs=0  run3 POSTs=0

Run 1's write is not preventable and the fix does not claim to prevent it: the commit fires during
discovery, before anything can know it was unattributable, which is HOW we find out. Runs 2..N are.

**The fix shape this entry proposed — `FlowMeta.quarantine` — could not work, and the reason is the
interesting part.** `quarantine` is enforced in `_preflight_row`, which serves `replay`, `run_batch`,
`preflight_keys` and `run_all`. R3.13's loop is `mode="auto"` → cache miss → LEARN, and neither `learn()`
nor `_learn_once` consults it. The engine cannot: `flow.py` does not import `flows.py`, and `flows.py`
imports `flow.py`, so reaching back is circular. Quarantining alone would have left the measured loop
exactly as it was.

The alternative — inject the memory as a caller-supplied policy, the way `finalize` and `pre_write` are
injected — reads idiomatic and is a trap: `ultracua run` and the daemon call `run_cached` DIRECTLY, so an
opt-in memory protects the `flow` verbs and leaves those two re-firing forever. That is the
wrapper-not-mechanism shape, in the one function whose own comment names it, written when this very
refusal was moved into the engine for that reason. **The regression test calls `run_cached` bare, exactly
as those two callers do, so it fails against any opt-in design** — the design constraint is pinned, not
just the behaviour.

So the memory lives on `FlowCache`, which the engine already holds, ON BY DEFAULT. It is deliberately a
narrower concept than `quarantine` ("may this be RE-AUTHORED" vs "may this RUN"), and `flows.release()`
clears both so the operator sees one story.

**Three judgement calls, each pinned:**
* `FlowCache.refusal` FAILS CLOSED — an unreadable marker returns a refusal, not `None`. R3.8's
  provenance lesson pointed the other way, because here the cost of a wrong "not refused" is re-firing a
  write;
* the memory never re-checks by driving the browser, so a TRANSIENT refusal (a deferred commit landing
  outside the attribution window) holds until a human clears it. Same asymmetry as `landed`: a stale
  refusal costs one command from someone told exactly what to run; a forgotten one costs a duplicate
  un-keyed POST that nothing catches. The test replaces the page with one that learns cleanly and
  requires the refusal to hold anyway;
* `health()` reports `refused`, not `not-learned`, with the reason attached — a refused flow has no run
  history, so `last_error` would otherwise be empty. S7a's rule one surface over.

**The adversarial pass found one defect in the fix, as it has in every slice:** `release()` cleared the
refusal ABOVE an `_update_meta(..., on_unreadable="raise")` that can fail, so on an unreadable sidecar the
operator got a raise, the refusal gone, and the quarantine still on disk — i.e. a flow that could now be
re-authored (re-firing the write) while the thing a human was told to investigate remained. Each clear
now happens only where nothing after it can fail, and the pin was verified RED against the bad ordering.

*Original finding follows.*

*medium, lens `over-refusal`, confirmed 2/2 by the audit and then MEASURED against the shipped code —
RECORDED, NOT FIXED in 0.74.0. Not a regression: measured identical before and after.*

**Where.** `src/ultracua/flow.py` (`_learn`'s refusal), `src/ultracua/flows.py:_learn_once`.

**Mechanism.** A refused flow caches nothing and records nothing, so the next `mode="auto"` run finds no
recipe, learns again, drives the browser again, and the page fires the same write again. Best-of-N
inside one call is already guarded (`_learn_n` breaks on `performed_write`); this is strictly an
ACROSS-INVOCATION hazard.

**Measured, three `mode="auto"` invocations of the same refusing flow** (the R3.2 disagreement shape: a
benign step whose intent trips the keyword classifier, real commit on the next step):

    0.74.0   run1 learn  success=False unattributed=True  cached=False   POSTs=1
             run2 learn  success=False unattributed=True  cached=False   POSTs=2
             run3 learn  success=False unattributed=True  cached=False   POSTs=3
             TOTAL 3 POSTs, 0 carrying an Idempotency-Key

    0.73.0   run1 learn  success=True                     cached=True    POSTs=1
             run2 replay success=True                     cached=True    POSTs=2
             run3 replay success=True                     cached=True    POSTs=3
             TOTAL 3 POSTs, 0 carrying an Idempotency-Key

**Identical write counts. The difference is only what the tool SAYS while doing it.** 0.73.0 cached the
flow with the gate and the Idempotency-Key on the step that issues no request, so every replay fired the
real commit un-keyed anyway — and reported success. 0.74.0 fires the same number of un-keyed writes and
reports the refusal. So this slice does not worsen the hazard, and does not fix it either; it makes it
visible, which is a precondition for fixing it.

**Note the earlier draft of this entry was wrong**, and how. It was written from the audit's
reproduction, which used a `<form method=post>` submit — a shape that does NOT refuse under the shipped
consistency rule (the classifier gates the submit step and the wire agrees), so it demonstrated nothing
about the shipped code. Re-measuring on a shape that actually refuses is what produced the numbers
above, and is what changed the conclusion from "widened by 0.74.0" to "unchanged by it". Reproduce
against the code you are shipping, not against the draft the finding was written for.

**Fix shape.** A persistent refusal marker, and the machinery exists: `FlowMeta.quarantine`, already
used by `_poisoned_meta()` for an unreadable sidecar. Quarantining the key on refusal makes the second
invocation refuse WITHOUT driving a browser. That is a real behaviour change — quarantine feeds
`health()`, the MCP tool list, `run_all`'s skip logic and the CLI — so it wants its own slice and its
own audit. Check the siblings while there: `record()` refuses the same class with the same
non-terminal property.
