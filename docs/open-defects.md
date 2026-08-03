# Open defects — the standing register

**ROUND 1** (2026-07-31, at v0.63.0): 20 findings, all fixed in 0.64.0–0.69.0.
**ROUND 2** (2026-08-02, scoped to everything written SINCE): 10 findings, 2 critical — **all fixed**
(R1/R2 in 0.71.0, R3–R10 in 0.72.0). Most were holes in the round-1 fixes.

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

