# Open defects — the 2026-07-31 audit

**What this is.** A six-lens adversarial audit of every implemented subsystem at v0.63.0, hunting for ways
to violate the three inviolables. 20 findings survived a refutation pass; 4 were fixed in 0.64.0 and the
rest are open. This file exists so the findings are not lost: re-deriving them costs ~28 agents and ~25
minutes, and they are far more valuable written down than re-discovered.

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

**Fixed in 0.65.0** (PR pending), each with a regression test confirmed to fail against the pre-fix code:
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

| | 0.64.0 | 0.65.0 | 0.66.0 | 0.67.0 | 0.68.0 | open |
|---|---|---|---|---|---|---|
| **critical** | A1 | — | — | — | — | A2 |
| **high** | A4, A11 | A12, A14, C2 | A5, A6, A7, A8, A9, A10 | — | A3 | — |
| **medium** | A13 | — | — | B1, B2 | — | — |
| **low** | — | C1 | — | — | — | — |

(Severities in this table are the *verified* ones where the 2026-08-01 pass corrected the audit: A14 and C2
up to high, B1 down to medium, C1 down to low.)

Fixed in 0.64.0 (PR #104), each with a regression test confirmed to fail against the pre-fix code:
**A1** (wrong-row write), **A4** (auth headers wiped by the Idempotency-Key), **A11** (empty pinned read
returned as an answer), **A13** (dropping the whole slot table skipped re-approval).

**VERIFIED BY HAND** — as of 2026-08-01, **every finding in this file**. A1, A3, A4, A11, A13 in the
original session; the other 14 in the independent reproduction pass described above, each with a probe that
was actually executed. Nothing here is now "a strong lead" only.

**What remains: A2 alone.** It is the only finding needing genuinely new machinery — a capture-phase
`submit` listener plus document-class request reconciliation in the recorder, so a form POST fired from a
`<div>` or via `form.submit()` is captured at all. Its `<button type=button>` variant may be PARTLY
closed by 0.66.0's wire promotion, which now marks a step mutating on wire evidence the classifier
missed — **re-probe it before designing the fix** rather than assuming either way. The original probe
and its four variants are described in the A2 entry below.

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

**A2. A demonstrated write is dropped from the recipe and never fires again, while replay says "confirmed."** *(critical, `recorder.py:516`, `recorder.py:249`, `flows.py:2639`)*
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
