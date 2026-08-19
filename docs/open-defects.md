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
The round-4 series has since grown to R4.57 as later slices filed against it:
**39 open**, 15 fixed, 4 parked — indexed and token-checked in the R4 STATUS INDEX at the top of that
section. (This sentence used to wrap between `13 fixed,` and `4 parked`, which put it OUT of reach of
`_R4_CLAIM` in `tests/test_register_count.py` — so the file's most-read count was the one number the
guard could not see. Kept on one line deliberately; the test loops over every claim it can match.)

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

## Two strikes, then change the SENSOR CLASS — a standing gate on how a finding may be re-attempted

*A gate, not a lesson, and it governs this whole file rather than round 1. It exists because the stopping
rule that would otherwise apply — "keep going until the suite passes" — is measured not to work here.*

**The rule.** When two fix shapes for ONE finding have been built and measured wrong, the third attempt
must change what the decision is made FROM — inference → a human's verdict, or inference → a loud refusal
— rather than refine the inference. Moving *within* a class (a better constant, a better window, a better
in-page probe) is not a third attempt; it is the second one again.

**What counts.** A strike is a fix shape that was BUILT and measured wrong — reverted, parked, or
redesigned mid-slice. A hypothesis discarded at design time is free. The count is per FINDING, not per
release, and it does not reset when the person changes.

**Where the classes are.** Each row is a different thing to ask, not a better way of asking the same one:

| sensor class | what it is here | what it can never answer |
|---|---|---|
| the clock | `write_window_ms` grace tails, `setTimeout(…, 0)` turn boundaries | anything about a DEFERRED cause — R3.2, R4.26 |
| intent text | `MUTATING_KEYWORDS` | read vs write: 28% FP / 45% recall, unfixable by any matching rule |
| the page | confirm transitions, scope fingerprints, `_pre_confirm` | whether a request left, or what the server did with it — R3.3 |
| the wire | `is_write_request`, `expect_request` | what a request MEANS — a GraphQL read IS a POST |
| the platform | `window.event` dispatch-presence | the cause of a write issued from a bare task, by construction |
| a human | `approve`, `record`, `dry_run`, a write annotation | anything at REPLAY time — it answers only at authoring, and it costs friction |
| a loud refusal | every fail-loud boundary in the engine | nothing — it declines the question and pays the cost in availability |

**The evidence this is built on.** R3.2's first three attempts were three inferences answering one
under-determined question: a clock (0.73.0, reverted), a refusal keyed off that same clock's failure
(0.74.0, over-refused), and an in-page causal map (0.76.0, parked). Two of the three were fully GREEN —
754 tests with a clean `drift_bench`, then 785 with a byte-identical one. The third was not green, but the
only thing that caught it was an opaque `assert learn.success` in an unrelated login file, 17 minutes into
the suite. The fourth stopped claiming attribution at all, and shipped. Note what the suite did across
that span: **754 → 785 → 867 tests**, growing with every slice and never acquiring the ability to catch
this class. Those counts are evidence of SIZE, not of coverage, and quoting one as reassurance is the
habit this gate exists to interrupt. R3.3 ran the same course inside a
single predicate: six versions, five wrong, every one of them green, failing in ALTERNATING directions
(double-pay, then never-pay) — which is the tell that the question was under-determined rather than that
the answers were careless.

**The corollary that makes this operational.** Before building a third attempt, write down what would
falsify it and measure THAT first — on the real classifier and the real population, never on the fixture
the finding was filed with. Both of S6's dead versions died to that measurement in a day instead of to an
audit a release later, and D0 was rejected the same way after it had already gone green across 105 tests
and a 24-cell matrix scoring 20/24.

**Why it is written down rather than remembered.** The plan's prescribed fix shape has been wrong in four
of the seven slices that attempted one (S2 in three separate ways, S3, S5, S6) and survived contact in
three (S4, S7a, S7b). The failure mode is never a bad idea; it is a good-looking idea arriving in a session
that has not read the ones before it. See **D5** below for the worked application, and `D0` in
`docs/correctness-plan.md` for the other decision this gate has already produced.

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

## R4 STATUS INDEX — the machine-checked one. **39 open**, 15 fixed, 4 parked

*Round 3's count is derived from its headings and pinned by `tests/test_register_count.py`; round 4's
was not, and it is the larger series. It is now, but NOT by parsing prose: R4 findings are declared in
four different shapes (`## R4.N —` headings, `- **R4.N** *(sev)*` bullets, `**R4.N (filed, not fixed).**`
paragraphs, `* **R4.N —**` bullets) and several ids also appear as bare cross-references, so a format
parser would silently miscount — worse than none, by this file's own rule. The test instead does a TOKEN
scan: every `R4.N` appearing ANYWHERE in this file must have a row here, and the counts above must match
the rows. File a new round-4 finding in any style you like; the suite will demand you index it.*

**Three states, because two would lie.** `open` = live against `main`. `fixed` = closed in shipped code.
`parked` = the defect exists only in the unmerged `feat/shared-causal-attribution` branch, whose
`src/ultracua/attribution.py` is not on `main` at all — so it is neither live nor fixed, and it returns
if and only if that branch is ever resumed.

<!-- generated:r4-index — edit docs/register/state.json, then `python scripts/render_register.py --write` -->
| id | status | what |
|---|---|---|
| R4.1 | parked | telemetry beacons counted as causal writes (fixed ON the branch) |
| R4.2 | parked | the causal set REPLACED the temporal set (fixed ON the branch) |
| R4.3 | parked | cross-origin seq collision rebinds a gate — not attempted |
| R4.4 | parked | a swallowed drain rebinds commits to the next step — not attempted |
| R4.5 | open | a page-synthesised commit launders a deferred write; **HIGH**, inviolable #3; two fix attempts failed, D5 gate applies |
| R4.6 | fixed | the matrix asserted only that SOME step is gated — closed by S1 |
| R4.7 | open | `recordWire` bypasses `pushRec`, so wire markers have no in-memory fallback |
| R4.8 | open | nothing pinned the AUGMENT-NEVER-REFUSE rule (pinned only on the parked branch) |
| R4.9 | open | `test_run_cached_refuses_it_too…` no longer exercises its stated property |
| R4.10 | fixed | the H9 judge captured and judged write flows — closed in 0.82.0 |
| R4.11 | open | post-auth-refresh `write_unreadable` falls to the generic tail, recorded ok=False |
| R4.12 | open | `_learn` takes no `pre_write`, so learn-time write verification is a bare presence check |
| R4.13 | open | `release()`'s gating read has no provenance |
| R4.14 | fixed | `audit_flows`' candidate loop had no per-flow guard — closed in 0.80.0 (S7a) |
| R4.15 | fixed | `cli._flow_dispatch` lets `MetaUnreadableError` traceback on six verbs — closed in 0.95.0 (S10) |
| R4.16 | open | a refusal pairs the NEW recipe with the PREVIOUS read_pin/shape/steps_hash |
| R4.17 | fixed | a quarantine's value-free reason is replaced by a bare IO error — closed in 0.95.0 (S10) |
| R4.18 | fixed | `_save_meta`'s failure surfaces as a bare OSError every handler misses — closed in 0.95.0 (S10) |
| R4.19 | open | `_reset_learn_baselines` clears shape/contracts but not `read_pin` |
| R4.20 | fixed | seven durable renames, one retry — closed in 0.95.0 (S10) by one shared `fsio` helper |
| R4.21 | open | `record()`'s refusal stays non-terminal — deliberate, but each retry re-fires the write |
| R4.22 | open | Windows `ERR_NO_BUFFER_SPACE`, **9 occurrences**; socket churn refuted at 0.104.0; occurrence 8 (local) argues local ≠ CI, and occurrence 9 landed on a DOCS-ONLY PR with a failing-run capture |
| R4.23 | open | `test_flows_dry_run_holds_a_real_write_flow` failed once under load, undiagnosed |
| R4.24 | open | a localhost round-trip stalled past the 5 s budget; ships unmitigated, 2 occurrences |
| R4.25 | fixed | the load-dependent "cluster of three" was one defect + one bad test — assertion fixed in 0.88.0 |
| R4.26 | fixed | the recorder credited a DEFERRED write to the next click — closed in 0.88.0 |
| R4.27 | open | the wire promotion marks ordinary GraphQL-style READS as writes (12/12 measured) |
| R4.28 | open | `_write_owner` turns confident when a neighbour's grace tail expires — observation, harmful direction not reproduced |
| R4.29 | fixed | a deferred write ESCAPED the learn watcher (removed with no drain) — closed in 0.96.0 by draining the remaining act window |
| R4.30 | open | a commit deferred beyond `write_window_ms` is still unobserved — the residual R4.29 does not close; needs an over-refusal measurement first |
| R4.31 | fixed | an unrecognised `mode` fell through to LEARN — re-authored the flow and RE-FIRED its write; closed in 0.98.0 |
| R4.32 | fixed | every failed replay returned an EMPTY `note` while the cause sat in the traces; closed in 0.98.0 |
| R4.33 | fixed | the corpus row added to adjudicate R3.7 could not fail for it — closed in 0.101.0 by the `row-nested-icon` scenario |
| R4.34 | open | a shared `aria-label` on a nested per-row container turns the row guard OFF → silent wrong-row bind; **HIGH**, inviolable #3 |
| R4.35 | open | a heal on a ROW-GUARD refusal re-grounds to a byte-identical recipe and reports a repair that repairs nothing |
| R4.36 | open | a write leaving in a LATER dispatch was credited to the last commit — NARROWED in 0.105.0 (direct-`fetch` shape closed), see R4.38 |
| R4.37 | open | a control whose nested wrapper owns no identity captures no `anchor_id`, so the row guard silently does not run; **HIGH**, inviolable #3 |
| R4.38 | open | `ACTIVATION_CAUSED` matches an event's TYPE NAME with no provenance, so a write laundered through a synthetic `submit`/`reset` re-enters attribution; **HIGH**, inviolable #3 |
| R4.39 | open | the wire Idempotency-Key is whichever step was mid-act, so a deferred write's key moves with PAGE TIMING and a retry cannot dedupe; **HIGH**, inviolable #3 |
| R4.40 | open | `_learn` snapshots straight after navigate with no settle floor, so a client-rendered page is authored against its unrendered skeleton — quiet, and one variant PERSISTS a refusal |
| R4.41 | open | the SDK choke-point pin allowlists `vision.py`, which does NOT route through `build_client` — so the inference the test states does not hold and `no_llm` would not intercept a vision call |
| R4.42 | fixed | `flow release` returns "nothing to release" before `release()` runs, so the remedy `flow.py:207` names is dead through the CLI — and WIDENING the status check does not reach the cached-recipe shape (measured) — FIXED at 0.109.0 by DELETING the pre-check (reshape-plan 1.2); `release()` now returns a ReleaseResult and the CLI reports what it cleared |
| R4.43 | fixed | `_SDK_CTORS` does not match `genai.Client()`, so gemini's construction is invisible and the anti-vacuity floor is met by exactly 3 — which also makes R4.41's own remedy misdiagnose itself — FIXED at 0.4a (PR #179): `Client` added to `_SDK_CTORS`, anti-vacuity floor raised 3 -> 4 measured, `GenerativeModel` kept so the old API stays covered |
| R4.44 | open | B1: a raised attempt drops its own LLM spend, traces and minted keys, and leaves `ok`/`failure_code` stale from the previous attempt (F2 guarded the relearn leg only) |
| R4.45 | open | B1: `record.usage == {}` on the miss / escalate / precheck / pre-attempt-refusal / raise exits, against `RunRecord`'s own "always populated" docstring |
| R4.46 | open | B1: a usage-less later attempt flips a priced total to `None` with no reason flag — a missing key and an unpriceable one are treated alike |
| R4.47 | open | B1: the FAILED-path cell asserts one default and two truthy values, so an engine populating the record only on success keeps it green |
| R4.48 | open | B1: eleven wiring mutations of the record plumbing survive the entire suite — only two cells pass `record=` at all |
| R4.49 | open | B1: `record.failure_code` speaks the internal `kind` vocabulary, not `FlowReplayError.code`, and can name a different attempt than the exception |
| R4.50 | open | B1: `llm_calls` / `traces` / `healed_steps` / `total_ms` exclude the relearn while `usage` includes it, so two fields disagree about one run |
| R4.51 | open | B1: the "a 0-LLM replay says observed zero" claim has no end-to-end pin — an engine reporting UNKNOWN on every replay passes every cell |
| R4.52 | open | B1: `BatchRowResult.landed` is the two-state bool the same PR calls a trap, reading `False` on successful write rows and on crashed rows |
| R4.53 | open | B1: a key-less teacher (`ScriptedProvider`/`MockProvider`) is classified as an unobserved SPENDER, contradicting `flow.py:915-916`; `accounting_failed` is sticky across runs |
| R4.54 | open | the key scrub's completeness rests on a hand-written variable list (Bedrock/Vertex uncovered) — the shape S14 replaced with a derivation |
| R4.55 | open | the key scrub is in-process only: on win32 an empty value is an ABSENT one, so a child process would re-read `.env` while both pinning cells stay green |
| R4.56 | open | a fixture sub-resource silently fails to load and surfaces as a JS `ReferenceError`; the HTTP/1.0 socket-churn cause is REFUTED by measurement (8 vs 6 connections per load) and a sweep would risk 8 hangs — disposition is the shared fixture server |
| R4.57 | open | a SUCCESSFUL run reports the failed attempt's `failure_code` — `_attempt_replay`'s success exit sets `ok` but never clears it, and `_mark_ok` (which does) is only called on the precheck and relearn exits; found by the exit-set matrix on its first run |
| R4.58 | open | the R4.22 resource sampler measures the wrong quantity: a Windows shard failed TWICE on two DIFFERENT browser tests, both latency-shaped (a 5s Locator.wait_for timeout; a write confirm missing inside its budget), while the same run's PASSING Windows shard was the MORE loaded of the two on 4 of 7 sampled metrics — the first same-run healthy baseline, and it refutes resource exhaustion for this symptom class. Neither CPU nor disk I/O is sampled, which is what a latency failure is about |
<!-- /generated:r4-index -->


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

## R4.5 — A page-initiated synthetic click launders a deferred write into a confident attribution — STILL OPEN at 0.90.0. TWO fix attempts built and measured wrong; **D5's two-strikes gate now applies**

*Filed medium against the parked branch. **Re-filed HIGH at 0.90.0**, inviolable #3 (and #2 on the
`record()` path, where replay double-submits while reporting success): it applies to the
mechanism that SHIPPED in S6/AB-1, not to a branch nobody is on, and it is deterministic — no load, no
timing, 10 runs in 10. RED test: `test_a_page_synthesised_click_must_not_launder_a_deferred_write`
(`tests/test_unattributed_write.py`), strict-xfail until the fix lands.*

**As filed.** The learn listener registers a commit for any click, including one the PAGE dispatches, so
a deferred write whose handler synthesises a click can acquire a fresh, confidently-attributed turn.
That was written about the parked branch's ATTRIBUTION. It survives into the shipped design for a
different reason, which is why nothing caught it: S6 does not attribute — it asks whether the page can
PROVE a cause, and over-gates when it cannot. A manufactured cause answers that question with a yes.

**Mechanism, against `main`.** `attributedSeq()` is `__ucturn === 1 && inDispatch() ? __uclast : null`.
R4.26 hardened `inDispatch` against a page SHADOWING `window.event`, by reading the native getter
captured before page scripts run. Nothing anywhere checks `isTrusted`, so a page does not need to shadow
a dispatch — it can PERFORM one. A deferred task that dispatches a click on any element matching
`ACTIONABLE` and issues its write inside that dispatch gets `__ucturn` 0→1 from the capture listener and
a genuine `window.event`, so the marker is stamped with the SYNTHETIC commit's own seq.
`_wire_writes_are_provable` then returns True, `_author_steps` takes the "agree and provable" arm, the
placement is TRUSTED, and AB-1's over-gating never runs.

**Failure, measured.** Fixtures are `_DEFERRED_TWO_TASKS` (control) and `_DEFERRED_VIA_SYNTHETIC_CLICK`,
identical but for whether the write leaves inside a dispatch. Three arms, 10 reps each, every run POSTing
exactly once:

| arm | write leaves via | gated | over-gate log | outcome |
|---|---|---|---|---|
| shipped `_DEFERRED_ONTO_BAIT`, verbatim | bare task | `[0, 1]` | fired | safe, 10/10 |
| control: one MORE bare task | bare task | `[0, 1]` | fired | safe, 10/10 — the extra task is not the variable |
| **synthetic click** | inside its dispatch | **`[1]`** | **silent** | **commit UNGATED, 10/10** |

Read directly rather than inferred: the `__wirewrite` marker carries `seq=null` in the control and
`seq=3` in the treatment — and 3 is the synthetic click's own seq, a commit no step performed. So step 0,
the real commit, caches `mutating=False`: no drift gate, empty `precond_scope`, and heal- and
suffix-replan-eligible. Positive control: force `_wire_writes_are_provable` False and the same fixture
gates `[0, 1]`, so the RED test is wired to this decision point and will XPASS the moment it is closed.

**A first draft of this entry also claimed "no Idempotency-Key on the wire", and that is WITHDRAWN as
unmeasured.** The deferred POST leaves during step 1's window, and step 1 IS gated in the treatment, so
the key on the wire is not obviously absent. The claim was an inference about replay dressed as an
observation — the exact move R3.3 cost six predicate versions to unlearn — and the harm is fully carried
by the surviving, measured facts. Do not restore it without a captured request header.

**TWO SHAPES, not one.** `el.click()` launders identically to `dispatchEvent(new MouseEvent(...))` —
measured 3/3 at 0.90.0, and it is the shape the parked round-4 probe itself used. The RED test is
parametrized over both, so a fix that only sees one cannot XPASS its way to having the marker deleted.

### R4.5 IS WORSE ON THE `record()` PATH, AND THE FIRST FILING OF THIS SLICE MISSED IT

`attributedSeq` has TWO consumers, and this entry originally described only one. The learn path reads it
through `_wire_writes_are_provable`, where the worst outcome is a trusted placement. `record()` reads it
for per-write ATTRIBUTION and is **not** in attribution-only mode, so `store()` captures the page's
synthetic click as a REAL STEP with a real locator, and the laundered seq attributes the write to it.

Measured at 0.90.0 on `_R45_RECORD_PAGE` (a declared write flow, so the undeclared-write refusal cannot
mask the result — the first probe used an undeclared one, refused for that unrelated reason, and would
have "refuted" this had it been trusted):

    record cached=True     steps: [0] click Continue   [1] click Confirm address   [2] click Refresh
                           2 human clicks -> a 3-step recipe; step 2 is the page's own click
    replay: returns OK     POSTs during replay: 2      <- double submit, reported as success

So on this path R4.5 is not an over-gate at all. It (a) turns a refusal into a cached flow, (b) writes a
step **no human performed** into the recipe that `approve()` then blesses — defeating the approval digest
in the one way it exists to prevent — and (c) fires the commit TWICE on replay, under two different
Idempotency-Keys, so a backend cannot dedupe them. **Inviolable #3 broken outright, and #2 with it.**

The target's visibility is what decides which harm you see: with a `display:none` control replay fails
loud (`DriftError`, and its note states the write did commit); with a visible one replay succeeds and
double-submits. A reader who probes only the hidden variant will conclude this path is safe. It is not.

**This miss is the entry's own lesson turned on itself.** The paragraph below already named the
sibling-guard shape as what produced R4.5 — and the first draft of this re-filing then documented one
consumer of a two-consumer signal. The check is cheap and was skipped: enumerate every reader of the
signal before writing the scope line.

**One fixture note worth keeping, because it will look like a fudge otherwise.** The write must land TWO
bare tasks after the bait click, not one. `window` capture runs before `document` capture, so the page's
release timer is armed — and fires — before the capture script's own `setTimeout(reset, 0)`; at one task
out `__ucturn` is still 1 and a synthetic commit reads 2, refused for a reason unrelated to this finding.
One task further the reset has run and `__ucturn` is 0, the ordinary deferred-write state. Equal-delay
timers fire in arming order, so this is deterministic by construction — the same property the R4.26
harness leans on, and why neither test needs artificial load.

### Two attempts, both measured wrong. `isTrusted` IS NOT A USER-PRESENCE SIGNAL, and that kills the whole family

**Neither attempt shipped.** Both were built, both went green on gates that matter, and both were killed
by measurement — the first by the mandatory pre-merge audit, the second by the audit of the REWORK, which
is S4's rule (*"the second pass found a CRITICAL the first pass had walked past, because the fix had
changed under it"*) paying for itself again.

**ATTEMPT 1 — require the COMMIT's own `isTrusted`.** Passed the full suite (871), `drift_bench` (15) and
every write/recorder file, with a refused population measured as three `select_option` demos. That
measurement was taken against a sample that shared its blind spot: the suite holds no WAI-ARIA APG
widget, no design-system dropdown, no ref-forward from a non-actionable wrapper. Reproduced by hand on an
APG-compliant `role=button` — the pattern REQUIRES the widget to implement Enter itself, canonically via
`el.click()`:

    human presses ENTER    posts=1  cached=False   <- record REFUSES
    human CLICKS (control) posts=1  cached=True    gated=[0]

Same widget, same human, same write: **recordable by mouse, refused by keyboard.** The regression falls
on accessible widgets and the people who use them, and no remedy exists — the human already acted
directly. D0's shape, so it was not shipped.

**ATTEMPT 2 — require that a trusted USER ACTIVATION began the commit's turn** (`__uctrusted = own trust
OR a trusted click/change/keydown/submit opened this turn`). It admitted all three shapes attempt 1
refused, still refused both filed attacks, and was green. **It is forgeable.** Measured on a page with
ZERO user interaction, everything fired from a bare task, on markup the page creates at runtime:

    submit@made   TRUSTED     <- form.requestSubmit(), on a form built by the page
    change@cb     TRUSTED     <- checkbox.click()
    click@b       False

So a page opens its own activation window whenever it likes, and R4.5 returns verbatim — `record
cached=True`, a 3-step recipe for 2 human actions, replay firing 2 POSTs while returning normally.
**`isTrusted` does not mean "a user did this"; it means "the user agent fired this event", and a page can
make the user agent fire events.** That single sentence is the transferable result: it kills attempt 1,
attempt 2, and every variant of them. Narrowing the activation set does not help — dropping `submit`
leaves `checkbox.click()`, and the next reader would spend a slice discovering that.

**Ruled out with measurement, so it is not re-proposed:** `navigator.userActivation.isActive` is not a
usable discriminator here — it reads TRUE on a blank page with no interaction ever, and inside a bare task.

**TWO STRIKES — D5 NOW BINDS.** Both attempts are inferences over the same in-page bit. Per the gate at
the top of this file, a third attempt **must change the SENSOR CLASS** — inference → a human's verdict, or
inference → a loud refusal — and a third variant of "read a better bit in the page" is the second attempt
again, not a third. R4.5 therefore stays OPEN, guarded by the strict-xfail RED tests landed in 0.90.0
(`tests/test_unattributed_write.py`, both dispatch shapes plus the record path), and the next attempt is
gated on D5 rather than on someone having a better idea.

**Two instrument results worth keeping, both from work that was then thrown away.**

*`drift_bench` earns its mandate, in both directions.* A first implementation of attempt 2 registered 11
window capture listeners for the activation classes: bench wall **138 s → 265 s**, the 180 s budget blown,
and an absolute invariant broke with it. Nothing in the 871-test suite notices a cost like that. The
cheap form — setting the flag from the capture listeners the script already installs, at the TOP of each
so a trusted click on a non-actionable wrapper still counts — needed **zero new listeners** and was both
faster and more precise.

*A loaded host cannot adjudicate, and the A/B is how you know.* The full suite then returned `869 passed,
2 failed`, both `drift_bench`, on a 36:44 run against a ~22 min baseline. The obvious reading — the change
still costs something — is exactly backwards. Measured at that moment: **144–284 MB free of 16309 MB
(1–2%)**, top consumers Firefox / VS Code / Defender, zero leftover chromium or node. Back-to-back on that
same host:

| arm | recorder | result |
|---|---|---|
| A | **main's** — the fix ABSENT | **2 failed**, 184.3 s |
| B | the branch — the fix present | **15 passed**, 151.9 s |

`main` fails the same two tests under the same load. Without that A/B the honest-looking conclusion would
have been "my change broke the bench", and a slice would have been spent tuning a cost that does not
exist. Second occurrence of the mechanism R4.24 tracks.


**ON `isTrusted`, WHICH IS THE OBVIOUS LEVER — and a first draft of this paragraph argued against it on
three exhibits that do not hold.** The withdrawn version said the recorder's measured KEPT set
(`form.submit()`, `requestSubmit()`, the wrapping-`<label>` forwarded click) put those shapes at risk from
a trust filter. It does not: `form.submit()` fires no event at all, and for all three the COMMIT that
`attributedSeq` reads is the enclosing REAL click, which is trusted. A filter on the commit listener
cannot touch them. The direction of error was stated backwards too — dropping a commit makes
`attributedSeq` return null, which makes `record` REFUSE and learn OVER-GATE, i.e. over-refusal, not the
under-gate the draft warned of. Three independent lenses flagged that paragraph; it is corrected here
rather than quietly deleted, because a register that steers the next slice off a lever on false evidence
is worse than one that says nothing.

**Overtaken by the two attempts above, which measured it — and NOTE THERE IS NO FIX; an earlier draft of this line said "superseded by the fix above" while R4.5 was open, which is the kind of stale claim this file exists to prevent.** The instruction survived and was followed. The examples did not, and neither did this paragraph's guess about WHICH shapes a trust filter endangers: the untrusted-forwarded-click class is already refused on the commit count, and the real casualties were an APG keyboard activation and a design-system dropdown. Original text follows.

**What stands.** The instruction survives even though its examples did not: *measure what any candidate
filter refuses, on the real KEPT set, before writing it up* (D5's corollary). It killed both attempts —
attempt 1 on the population it refused, attempt 2 on the forgeability of the bit it reads. What is genuinely unpriced
is the untrusted-forwarded-click class — a wrapper element forwarding to a hidden control, and the
Playwright-dispatched `change` behind a `select` commit — and the cross-product in
`tests/test_write_safety_invariants.py` does **not** currently contain a single cell whose commit is
untrusted, so it cannot price the trade as it stands. New cells are needed first. And whatever the fix,
it must not refuse on unprovability, which the AB-1 entry above measured as taking 4 of 6 ordinary
read-over-POST patterns with it.

**Process note, recorded because the miss is more instructive than the bug.** The plan named this hazard
in scope for S6 and required a RED test; S6 shipped without one, and the register kept filing R4.5 as
"parked", which reads as belonging to a branch nobody is on. A hazard carried forward by DESCRIPTION into
a slice's scope, with no test to hold it, is a hazard that quietly leaves scope. That is the sibling-guard
shape one level up: the guard was specified, and never applied to the mechanism that shipped.

### The human-verdict prerequisite LANDED and R4.5 did not move. Measured at 0.93.0, and one of the two claims made for it was wrong

The plan sequenced S18 behind the write-provenance + annotation work, because D5 requires the third
attempt to change the SENSOR CLASS and "inference → a human's verdict" is that change. Both halves
shipped (0.92.0, 0.93.0). **A prerequisite landing is not a dependency being satisfied**, so both paths
were run rather than argued:

| path | what the human can do to the laundered recipe | |
|---|---|---|
| `learn` | step 0 caches `mutating=False, sources=None, scope='', fp=set`; `flow mark --write` → **ALLOWED**, step 0 becomes `mutating=True, sources=['human']` | the artifact IS repairable |
| `record` | 2 human clicks → a 3-step recipe; the phantom step 2 caches `mutating=True, sources=['wire']`; `--read` **REFUSED** (`wire`), `--write` allowed and pointless | the artifact is NOT repairable |

**The learn-path row corrects a claim this session made and should not have.** It was asserted that
promotion would be refused as a gateless write. It is not: `flows.mark_step`'s gate needs *either* a
`precond_scope` *or* a `precond_fingerprint`, and `_learn` always populates the whole-page fingerprint —
only `recorder._step_from_event` leaves both empty, which is what that refusal was written for. Reading
the guard's *intent* instead of running it produced a wrong answer in the safe-sounding direction, which
is the harder one to catch.

**The record-path row is the one that matters, and its bound is structural.** The harm there is a step
**no human performed** sitting in the recipe. `flow mark` has a two-word vocabulary — writes / does not
write — and neither word changes step MEMBERSHIP. There is no annotation that deletes the phantom, so
the primitive is not merely refusing here; it cannot express the repair. Widening it to delete steps is
not a small change either: step membership is what `approve()`'s digest is over, so a delete verb is an
approval-bypass surface, and this file's own rule is that a verb which can rewrite what was approved
needs its own argument, not an extra flag.

**And on the learn path, the repair is unreachable in practice** — not because the verb refuses, but
because nothing tells the operator to run it. The laundering arm is measured SILENT (no over-gate log,
`gated=[1]`, the flow reports success), so a human-verdict sensor that only fires when a human already
knows is not a sensor; it is a remedy for a diagnosis nobody has. **That is the transferable result:**
when D5 says change the sensor class to a human's verdict, the human needs a TRIGGER, and specifying the
verdict verb without specifying what surfaces the question builds half a sensor. S18's next attempt must
name the trigger first.

**Net: S18 is exactly where it was, and its stated prerequisite is now spent.** Recorded here rather
than in the plan alone so nobody re-derives it from the sequencing line, which was corrected in the same
change.

## ✅ FIXED by plan slice S1 — R4.6. The invariant matrix asserts only that SOME step is gated, never that the gate is on the step that WROTE

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
patching them. ~~R4.5 remains in scope.~~ **R4.5 was in S6's scope and S6 shipped without covering it**
— it is now reproduced 10/10 against shipped `main` and carries a RED test; see its entry above. Any
future attempt to ATTRIBUTE (rather than merely refuse) must start from R4.3/R4.4 and from
`docs/parked/README.md`, not from the diff.

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

### D5 — positive attribution of a deferred write is BLOCKED INDEFINITELY. Refuse-or-over-gate plus human adjudication IS the design, not a stopgap

*A decision, not a finding, recorded immediately below the entry it governs because this is what anyone
starting attempt 5 is reading. Same standing as **D0** in `docs/correctness-plan.md`: built, measured,
rejected, and BLOCKED rather than deferred. It is the first application of the two-strikes gate near the
top of this file.*

**What is blocked.** Any mechanism that answers *"which authored step CAUSED this wire write"* for a write
that did not leave inside its own commit's dispatch. **Not blocked, and untouched by this:** the
wire-vs-classifier consistency check (0.74.0), the provability evidence and gate-every-candidate posture
(S6/AB-1, 0.89.0), and the recorder's per-write attribution — which is sound BY CONSTRUCTION, because a
human's own dispatch stamps it, and is precisely why `record()` has never needed one of these attempts.

**Four passes at the problem, and what each one measured.** Three were attempts to ATTRIBUTE and all
three were defeated — which is the count `CLAUDE.md` carries. The fourth is in this table because it is
what a fifth would be measured against, not because it attributed anything: it deliberately does not.

| # | design | outcome |
|---|---|---|
| 1 | 0.73.0 — exclusive intervals via a 250 ms drain (`1e47f0f`) | green at 754 tests + clean `drift_bench`; **silently credited the WRONG step** at defer 450 ms and 800 ms. REVERTED |
| 2 | 0.74.0 — refuse whenever nothing could be attributed (`987ca58`) | over-refused: the ordinary fill-then-submit shape is unattributable by construction, so it failed every `tests/test_press_gate.py` login flow. Shipped instead as the CONSISTENCY check, which claims strictly less |
| 3 | 0.76.0 — shared in-page causal signal, flat seq→step map (`adc8266`) | green at 785 tests + byte-identical `drift_bench`; **PARKED** — the round-4 audit found the defects WERE the design (R4.3, R4.4, R4.5) |
| 4 | 0.89.0 — S6/AB-1: provability as evidence about a PLACEMENT | ships, and deliberately **does not attribute**. Where the page cannot prove the cause, every candidate row is gated |

**Why a fifth attempt of the same kind cannot work — three measured impossibilities, not a judgement:**

1. **No temporal design survives.** No constant is simultaneously long enough to catch a deferred write
   and short enough to exclude the next step: shortening the tail reproduces the bug, lengthening it
   restores the credit-nobody collapse. The ladder is the proof that it is a RACE and not a tuning
   problem — on one page, defer 60 ms → correct, 300 ms → loud, 450 ms → **wrong, silent**, 600 ms →
   loud, 800 ms → **wrong, silent**. R4.26 re-proved it one module over: a timer is a bet on the
   scheduler, not a boundary.
2. **The in-page causal signal is partial BY CONSTRUCTION, not by omission.** `attributedSeq` certifies a
   synchronous or microtask cause (`__ucturn === 1 && inDispatch()`) and returns `null` for every write
   that leaves in a bare task. That `null` is the CORRECT answer from that vantage; there is no version of
   the same sensor that returns a step instead, so "improve it" is not a design.
3. **Refusing on unprovability is not available either.** Measured against the real capture script, 4 of 6
   ordinary READ patterns issued over POST are exactly as unprovable as a deferred commit — an awaited
   round-trip, a debounced search, deferred pagination, an rAF-batched query — because `is_write_request`
   is method-based and a GraphQL read IS a POST. Refusing them is D0's regression one surface over. The
   same method-blindness files 12 of 12 ordinary read controls as write flows (R4.27).

**What the block costs, priced rather than waved through.** R3.2 stays open as a MANAGED residual, not a
closed one. A write deferred forward onto a classifier-mutating neighbour is now gated, but the recipe is
OVER-gated rather than correctly gated: it becomes multi-write, loses the auth-refresh retry, and every
gated step loses self-heal and suffix-replan. That is paid in RECOVERY features, never in silence — the
same trade this register has argued every time this has come up.

**What would unblock it: a genuinely NEW evidence source, measured BEFORE it is built.** All three
conditions, not any one:

* **A sensor class none of the four attempts used** (see the two-strikes gate). The candidate anyone will
  reach for is CDP `Network.requestWillBeSent.initiator` with async stack traces: out-of-band, so a page
  cannot shadow it the way it can shadow `window.event`, and able to see across the timer/await boundary
  the in-page signal cannot. Its hostile priors, stated here so nobody rediscovers them: an initiator
  stack names a script FRAME, not a recorded step, so a seq→step map re-enters and R4.3/R4.4 with it;
  framework schedulers (batching, event emitters) root the stack in a generic queue drain rather than in
  the click; and it says nothing whatever about read-POST vs write-POST, which is where most of the
  current cost actually falls.
* **It must WIDEN the provable set inside the shipped fail-closed shape** — union, never remove (R4.2's
  lesson) — keeping gate-every-candidate as the fallback. A design that REPLACES the current posture is
  the parked branch again.
* **It must be measured first against the artifacts that already exist**: the defer ladder
  (60/300/450/600/800 ms), the `_DEFERRED_ONTO_BAIT` fixture, the R4.26 deterministic harness, and the six
  read-over-POST patterns — plus new fixtures for its own blind spots (a framework-batched commit, a
  service-worker fetch). A spike that cannot beat those is not an attempt; it is a fifth wrong fix that
  has not been built yet.

**Order of attack, if this is ever revisited.** The HUMAN lever comes first: persist WHY a step carries
its `mutating` mark (keyword guess / form method / wire promotion / human verdict) and let a human confirm
or correct it at authoring time. It has certainty where a new inference has a measured-hostile prior, it
is the only sensor that can answer the semantic question at all, and the plan already names it as D0's
lever (ii) — which also says building it twice is how a fifth wrong fix arrives. A sensor spike after that
is optional; before it, it is attempt 5.

**Overturning this decision requires overturning this text**, which is the whole point of writing it down.

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

### ⚠️ STILL OPEN — R3.7. `_ROW_OF_JS` does not mirror `anchorOf`'s walk as its comment claims — `anchorOf` SKIPS a row-like container whose collapsed text is empty and keeps climbing, `_ROW_OF_JS` stops at it unconditionally — so a nested action list makes the guard refuse a correct bind on a page that has not drifted at all

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

**⚠️ STILL OPEN at 0.103.0. ATTEMPT 2 WAS ALSO BUILT, ALSO PASSED EVERY GATE, AND ALSO PRODUCED A
SILENT WRONG-RECORD BIND — a different route to the same failure class. Reverted. D5's two-strikes gate
now applies: a third attempt must change the SENSOR CLASS, not refine the inference.**

**Attempt 2 (0.103.0).** Decouple `anchor_id` from `anchor_source`: take the identity from the NEAREST
enclosing row-like container by a purely structural walk shared verbatim by capture and bind, whatever
the anchor TEXT did, and gate `resolve` on `anchor_id` alone. It closed R3.7 and R4.34 together, and on
every instrument available it looked better than main: containment property green with pristine binds
3 → 4, attempt 1's attack page refusing, `drift_bench` invariants all holding with survival UP at every
k (k1 20→22, k3 22→25), **zero rows regressed** across 185 rows × 2 arms, suite 1031 green.

**What it actually did.** `_nearestRow` stops at the INNERMOST row-like container. When that container is
an action wrapper owning no identity — no `form[action]`, no `a[href]`, no hidden input, no `data-*` —
`rowIdOf` returns null, `anchor_id` is captured as **None**, and the new gate reads "no identity" as "no
guard". The record key one landmark up on the `<tr>` is never consulted. Measured on an ordinary
client-rendered table (`<tr data-order-id="3"> > td > ul.actions > li > icon button`), recorded against
record 3, replayed after record 3 was cancelled:

| | capture | bind |
|---|---|---|
| main | `anchor_id='data-order-id:3'` | **REFUSED** |
| attempt 2 | `anchor_id=None` | **bound record 7**, `bound_by='role+name'`, no `row_mismatch` |

So it converted R3.7's LOUD false refusal into a SILENT wrong-record bind, for a sub-population of
exactly the shape R3.7 is about. The audit measured 6 newly-broken cells in a 30-cell cross-product;
an independent refuter reproduced it and also showed the mutation gate does not save it — a per-row
`<form method="post">` with no `action` attribute is invisible to `_rowCands` AND becomes the
fingerprint scope, so every row fingerprints identically, and a step not classified mutating has no gate
at all.

**WHY BOTH ATTEMPTS WERE BLIND, which is the transferable part.** Every shape in the suite and all 185
corpus rows put an identity INSIDE the nested wrapper — the matrix formats a `<form action=...>` into
the inner `<li>`, and `row-nested-icon` puts an `<a href=...>` there. The population where the wrapper
owns NOTHING and the key is one level up was in no instrument at all. It is now
`bare-nest/icon` in the containment matrix: GREEN on main, RED against attempt 2.

**What this rules out for attempt 3.** Not "nearest container" as such — the flaw is that
`anchor_id=None` is overloaded. It means both *"this row genuinely has no discriminating token"* (the
documented, accepted residual, where the guard must stay off or a large population is refused) and
*"I looked in the wrong place"*. `resolve` cannot tell them apart, and any rule that answers "which
container" without also answering "and is that answer trustworthy" will keep landing here. The plan's own
prescription — nearest row-like container **that can prove an identity**, plus `rowIdOf` no longer
borrowing from a row-like DESCENDANT — was written before attempt 2 and NOT what attempt 2 implemented;
it remains the best-argued candidate and is still unmeasured.

**Per D5 the next attempt must change the sensor class.** The two spent strikes were both inferences
from the same sensor: a string computed from one container, compared for equality. A third variant of
"pick a better container" is attempt 2 again. What would qualify: making the ABSENCE of an identity a
distinguishable state rather than a silent disarm (fail-closed with a measured over-refusal cost, or a
third value the caller must dispose of), or a containment test that does not go through an identity
string at all.


---

**ATTEMPT 1 (0.100.0), KEPT IN FULL — BUILT, PASSED EVERYTHING IT WAS ASKED TO PASS, AND TURNED A
CORRECT REFUSAL INTO A SILENT WRONG-ROW BIND. Reverted; the measurement was the deliverable.**

Attempt 1 was the fix this register and `docs/correctness-survey.md` both prescribe, and it is the
obvious one: extract `_ROWWALK_JS`, give the bind side the same non-empty-text condition capture uses, so
the two walks cannot disagree. It reproduced the finding first (6 of 18 parity cells diverged against
0.99.0; the end-to-end bind returned `None` with `bound_by='none'` on a pristine page), fixed all six,
passed 26 tests in the row-guard files, and passed `drift_bench` with every invariant holding, no
baseline regression and `writes double=0 suppressed=0 wrong_target=0`. The pre-merge audit found it
anyway — three independent lenses, each with its own executed probe, plus a fourth reproduction written
by hand afterwards.

**Why it is wrong, stated precisely, because it is not obvious.** Skipping a text-less row-like container
makes the bind walk climb OUT of the bound element's own row into an ANCESTOR that contains it. `rowIdOf`
then hands that ancestor an identity borrowed from a row nested inside it — deliberately, since its
`taken` set skips anything the container `contains`, so that a row's own link is not treated as evidence
the value is shared (locators.py:163, :168). When the recorded row is the FIRST identity-bearing row
inside that ancestor, the ancestor's identity string EQUALS the recorded one, and the guard compares
equal for two different records.

Measured end to end, on `resolve`'s own motivating drift (the recorded row survives, its control does
not): post-attempt `resolve` binds subscription 7's control for a step recorded against subscription 3,
`bound_by='role+name'`, `sink` carries no `row_mismatch`, nothing is logged, and at the wire the click
POSTs `/cancel/7`. Pre-attempt code refuses the identical input. The mutation gate cannot object: per-row
forms are structurally identical, so `scope_fingerprint` matched byte-for-byte (`69ecd918218fdef5` both
sides). That is inviolable #3, introduced by the fix for a fail-loud finding.

The audit also reported, and this one is recorded as its finding rather than as a measurement of ours —
it was not independently reproduced here, and the code it describes is reverted — that the attempt moved
the MIRROR direction too: a recorded row that renders icon-only at replay gets climbed past, so the guard
refuses the CORRECT record. If true it means the attempt was wrong in both directions at once, which is
worth knowing before attempt 2 rather than after.

**PARITY IS NECESSARY AND NOT SUFFICIENT — this is the transferable part.** "Capture and bind name the
same row" is satisfied by a bind walk that has stopped being a containment check at all, because two
different containers can produce the same identity string. The guard's real question is CONTAINMENT: is
the element I just bound inside the record that was recorded? The 18-cell parity matrix that attempt 1
passed is measuring a proxy, and the proxy broke away from the thing it proxies.

So the artifact this slice leaves is the CONTAINMENT property —
`tests/test_row_identity_binding.py::test_a_bind_outside_the_recorded_record_is_refused_on_every_nesting_shape`
— over seven nesting shapes, asserting that a bind is either refused or belongs to the recorded record,
with a floor on how many shapes must still bind on an untouched page so that refusing everything cannot
satisfy it. It is GREEN against main and RED against attempt 1, catching both wrong-record binds
(`grouped-rows/icon`, `group-hidden/icon` — the latter being the shared-endpoint-plus-hidden-record-key
shape `rowIdOf`'s own comment calls "very common"). **Whatever closes R3.7 has to keep it green**, and no
fix should be believed because the parity matrix went green.

**What the same measurement says about R3.7's own shape, which is wider than filed.** Across those seven
shapes, main falsely refuses on four — and the causes are not all this finding. `nested-actions/text` and
`grouped-rows/text` refuse because the row anchor is the control's own label, identical on every row, so
Tier 3 is ambiguous; that is a different residual and must not be counted as R3.7. The cell where the
cause is unambiguously this finding is `nested-actions/icon`, pinned as a strict xfail.

**Direction for attempt 2, not a design.** Two sub-problems have to be solved together, and either alone
reopens the other: (1) the id-bearing container must be chosen by the SAME rule on both sides — the
candidate worth measuring is "the nearest enclosing row-like container that can prove an identity",
which agrees by construction and never climbs past a row that could have objected; and (2) `rowIdOf`
must stop borrowing an identity from a row-like DESCENDANT, or a text-less recorded row inside a
borrowing ancestor rebuilds the same collision. Both change what a captured `anchor_id` MEANS for nested
shapes, so this is a resolver trade and `drift_bench` adjudicates it — with the corpus row fixed first
(R4.33), since today it cannot fail for this finding.

**The corpus row named for this finding cannot fail for it — filed as R4.33.** `row-nested-action` was
added by S1b to adjudicate R3.7 and measures a shape where the two walks agree, so the scenario's
numbers are byte-identical with and without attempt 1. Building the faithful version needs an icon-only
control AND a row identity the nested container does not share; measured in a scratch A/B, that shape
refuses **14/14 rows** (`bound_by={'none': 14}`, survival 0 at every k, 12 rows routed into heal) against
main, and recovers to 5 binds under attempt 1. It is not landed here: it needs a deliberate re-baseline
and a triage of the prediction model against an aria-label-only target.

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
* **R4.15 — ✅ FIXED in 0.95.0 (S10).** `cli._flow_dispatch` catches only `EmptyFlowStoreError`, so `MetaUnreadableError` reaches
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
* **R4.17 — ✅ FIXED in 0.95.0 (S10).** when the sidecar cannot be written, `_do_quarantine`'s H9 value-free reason is replaced by
  a bare IO error: the operator learns about a file problem instead of "this flow returned a wrong
  value", `_record_run` never captures the reason, and the typed `quarantined` code and
  `retryable=False` are lost. A catch was written for this and cut, because it caught only
  `MetaUnreadableError` while the save half raises a bare `OSError` — i.e. it did not deliver what it
  claimed. Fix both halves together.
* **R4.18 — ✅ FIXED in 0.95.0 (S10).** `_save_meta`'s failure surfaces as a bare `PermissionError`/`OSError`, not a
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

* **R4.20 — ✅ FIXED in 0.95.0 (S10).** `FlowCache.put`'s `os.replace` has NO retry, while S4 gave `_save_meta`'s exactly that,
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

* **R4.22 — OCCURRENCE 9 (2026-08-17, CI Windows shard 1/2), ON A DOCS-ONLY PR. The cleanest attribution
  the series has: no `src/` line changed, so no code cause is available.** `test_drift_bench.py::
  test_every_absolute_invariant_holds` failed with `Page.goto: net::ERR_NO_BUFFER_SPACE at
  http://127.0.0.1:55883/span` (1 failed, 563 passed, 526 deselected, 1 xfailed in 968 s). The other
  three arms passed. **Tenth distinct test in nine occurrences** — the spread across tests is now itself
  the strongest evidence that this is not a property of any test.

  The PR (#167) touched `docs/open-defects.md` and `docs/reshape-plan.md` and nothing else. Every prior
  occurrence landed on a run whose diff contained code, leaving "did the change do it?" formally open
  each time; this one closes that question for the series.

  **Sampler peaks at the failure (the instrument working as designed — a capture OF the failing run,
  which occurrence 8 explicitly could not provide):**

  | | occurrence 9 (CI, FAILING run) | occurrence 7 (CI) | developer host (healthy) |
  |---|---|---|---|
  | `time_wait` | max **414**, mean 194 | 25–359 | 50–388 |
  | `handles` | max **47,802** | 47k → 56k | 177k → 186k |
  | `processes` | max 140 | — | — |
  | `chrome_procs` | max **7** | — | — |
  | `nonpaged_pool_mb` | max **239** | 171 → 207 | 4573 → 4684 |
  | `paged_pool_mb` | max 368 | — | — |
  | `free_mb` | min **12,891** | 12,838 | 517 |

  **Both standing hypotheses stay excluded, now from a failing-run capture rather than a baseline.**
  414 TIME_WAIT against a 16,384-port ephemeral range is 2.5%; 12.9 GB free excludes memory pressure;
  nonpaged pool at 239 MB is nowhere near the developer host's 4.6 GB. So the CI series remains
  undiagnosed, and occurrence 8's reading — that the local failure (517 MB free) and the CI series are
  probably different phenomena — is unchanged and now better supported.

  **What is new and worth a hypothesis:** `chrome_procs` peaked at **7** on a shard that should be
  running one session at a time. Not pursued here, and stated as an observation rather than a lead —
  the register has spent two hypotheses on this finding already (drain aggravator, socket churn), both
  refuted, and a third guess is not what it needs. What it needs is a reproduction, and this occurrence
  does not provide one.

* **R4.22 — OCCURRENCE 8 (0.105.0), THE FIRST ON A DEVELOPER HOST, and it argues the local and CI
  failures are NOT the same phenomenon.** `test_write_safety_invariants.py::…[fetch-first-plain-loud]`
  failed a full local suite run with `Page.goto: net::ERR_NO_BUFFER_SPACE at http://127.0.0.1:50143/`.
  Ninth distinct test in eight occurrences. A re-run passed (1030 passed / 9 xfailed).

  **This is the local capture the entry has been asking for since occurrence 6** — "CI uploads its
  samples, a developer-host failure discards them". Two earlier failures in the same session were the
  same shape and were LOST, because the suite was piped through `tail`. The error text survives here only
  because that was changed; the instrument is a redirect, and it should stay one.

  **What the host looks like, measured on a healthy run of the same suite, same machine, 2 s sampling:**

  | | this developer host | CI (occurrence 7) |
  |---|---|---|
  | `free_mb` | **517 min**, 1330 start | 12,838 min |
  | `nonpaged_pool_mb` | **4573 → 4684** | 171 → 207 |
  | `handles` | 177k → 186k | 47k → 56k |
  | `time_wait` | 50–388 | 25–359 |

  The developer host runs at **25× the nonpaged pool and 3× the handles of the CI runner, and dips to
  517 MB free**. That is a machine near memory exhaustion, and `WSAENOBUFS` is exactly what Windows
  raises when a socket allocation cannot be satisfied. **So the local occurrence has an obvious candidate
  that CI's numbers exclude** — occurrence 7 failed with 12.9 GB free. Treating all eight occurrences as
  one phenomenon is the "two failures lumped together" mistake this register warns about; the honest
  reading is that the CI series remains undiagnosed and the local one is probably host pressure.

  **Stated as the limit it is:** the sampler covered the SUCCESSFUL re-run, not the failing run. This is a
  healthy-run BASELINE on the failing host — which is one of the two holes the sampler was built to close
  — and not a capture at the moment of failure. Do not quote it as one.

  **The socket-churn refutation holds on both**, and was independently reproduced on this very run by
  `scripts/count_fixture_connections.py`: 1890 connections over 1751 s = **1.08/s, 0.8%** of the
  ephemeral range, against 1.04/s measured at 0.104.0.

* **R4.22 — MEASURED at 0.104.0, and the leading hypothesis is REFUTED. Socket churn is not the cause.**
  Seven occurrences produced two standing hypotheses — ephemeral-port exhaustion and handle exhaustion —
  and a strong-looking lead: `benchmarks/drift_fixtures.py` sets `protocol_version = "HTTP/1.1"` with a
  comment saying HTTP/1.0 "burned its own socket" per request and that consecutive runs exhausted the
  ephemeral-port range with this exact error, while **28 of the suite's 32 fixture servers never got
  that fix**. That is this register's most-repeated shape (a guard applied to one path and not its
  siblings) with the remedy already written down. It is wrong, and the numbers say so.

  **The mechanism is real.** Counting TCP connections accepted against HTTP requests served:

  | protocol | connections | requests | requests per connection |
  |---|---|---|---|
  | HTTP/1.0 | 13 | 13 | **1.0** |
  | HTTP/1.1 | 1 | 25 | **25.0** |

  Every fixture request under HTTP/1.0 does burn its own ephemeral port.

  **The pressure is not.** A full suite run, instrumented end to end
  (`scripts/count_fixture_connections.py`, shipped so the next occurrence gets a number):

      1884 connections over 1806 s = 1.04/s
      steady state at Windows' 120 s TIME_WAIT delay ~= 125 sockets held
      = 0.8% of the 16384-port ephemeral range (49152-65535)

  **The model validates against occurrence 7's independent samples**, which is what makes this a
  refutation rather than a counter-story: predicted ~125 sockets held, and
  `scripts/sample_resources.ps1` measured TIME_WAIT at **130** on shard 2 and **186** (peak 359) on
  shard 1, on a different machine. The suite's socket behaviour is understood and it is two orders of
  magnitude away from exhaustion.

  **It does not explain the one correlate either.** Occurrence 7's only rising signal was the nonpaged
  pool (171.2 → 207.1 MB, +35.9 MB). TIME_WAIT entries cost on the order of a kilobyte, so ~125 held
  sockets account for **~0.13 MB — 0.4% of the observed rise**. Whatever is consuming that pool, it is
  not fixture sockets.

  **So the 28-file conversion is NOT being done**, and that is the finding. It would remove roughly half
  the suite's connections (measured on the bench: 695 → 381), cost nothing in correctness, and fix
  nothing — while touching 28 files. A textual screen (not a per-path verification, and it should not be
  quoted as one) flags 5 of them as containing no `Content-Length` at all, which under keep-alive makes a
  client wait for a body that never ends instead of closing — so the conversion is not even mechanical. A change justified by a plausible mechanism and a comment, with no
  measurement, is what this register keeps filing findings about; the fact that it would have been a
  textbook sibling-guard fix is precisely why it needed a number first.

  **A note on that comment, stated carefully.** The bench's own claim that HTTP/1.0 "exhausted the
  ephemeral-port range" does not reproduce at today's volumes: the HTTP/1.0 counterfactual runs at
  695 connections over 173 s = 4.0/s, ~482 held, **2.9%** of the range. That measures today's bench, not
  the historical one — the corpus has changed since — so the comment is annotated rather than
  contradicted. Keep the keep-alive: it halves churn and costs nothing. Do not cite it as R4.22's remedy.

  **What is now ruled out, so nobody re-spends it:** ephemeral-port exhaustion (0.8%), handle exhaustion
  (occurrence 7's peak was not at the failure), memory pressure (12.9 GB free), and fixture socket churn
  as the nonpaged-pool driver (0.4% of the rise). What remains is the pool itself, unexplained, and the
  standing observation that three occurrences in a row resist any change-linked explanation.

* **R4.22 — OCCURRENCE 7 (0.101.0, PR #152, run 31677764463, windows 1/2). THE FIRST ONE WITH RESOURCE
  SAMPLES COVERING THE MOMENT OF FAILURE**, which is what occurrence 6 said would turn this into
  evidence. It does — mostly by REFUTING things, which is worth more than another tally bump.

  `test_audit.py::test_audit_quarantine_refuses_future_runs_and_release_clears` failed on
  `Page.goto: net::ERR_NO_BUFFER_SPACE at http://127.0.0.1:50174/page1.html` at 07:41:32. Eighth distinct
  test in seven occurrences, still consistent with "whichever test happened to be running".

  **What the sampler measured on that runner** (`scripts/sample_resources.ps1`, ~7–9 s cadence, 119
  samples over 14 min; the failure sample is the last one at or before 07:41:32):

  | | at failure | run min | run max | verdict |
  |---|---|---|---|---|
  | `time_wait` | 186 | 25 | 359 | **refutes port exhaustion** — the ephemeral range is ~16k |
  | `free_mb` | 12,935 | 12,838 | 13,535 | **refutes memory pressure** |
  | `handles` | 51,680 | 47,508 | 55,644 | the peak was NOT at the failure |
  | `processes` | 151 | 140 | 163 | flat |
  | `nonpaged_pool_mb` | **207.0** | 171.2 | **207.1** | at its run maximum, risen 21% monotonically |
  | `paged_pool_mb` | 265.2 | 168.5 | 299.5 | rising steadily |

  **The one correlate is the nonpaged pool**, which is also the mechanism `ERR_NO_BUFFER_SPACE`
  classically indicates on Windows (a socket-buffer allocation failing, not an address running out). The
  two hypotheses this file has carried longest — TIME_WAIT/ephemeral-port exhaustion and handle
  exhaustion — are contradicted by the numbers at the moment it happened.

  **State the limit as loudly as the lead.** This is ONE sample from a ~7 s cadence, so a transient spike
  between samples is entirely possible and would not appear here; a monotone rise ending at the maximum
  is what you would also see if the pool simply grows with the run and the failure landed near the end.
  Correlation at n=1 is a direction to look, not a cause. What would settle it is a sample cadence fine
  enough to bracket the failure, or a nonpaged-pool watermark read at the moment `goto` raises.

  **AND THE AGGRAVATOR HYPOTHESIS WAS EXONERATED, for the second time in three occurrences.** Unlike
  occurrences 5 and 6, this one HAD a candidate: the slice adds 28 rows to `drift_bench`, and
  `tests/test_drift_bench.py` and the failing `tests/test_audit.py` are both on shard 1, so the added
  browser and socket churn lands on exactly the shard that failed. The re-run of the same commit passed
  all five jobs. So a change that plainly increases load on the failing shard did not reproduce it —
  which is the same shape as occurrence 5, where the R4.29 drain was the candidate and an A/B cleared it.

  Three occurrences in a row now resist a change-linked explanation, and the one measurement we have
  points at a host-level resource, not at anything this codebase does.

* **R4.36 (filed here, and it is NOT R4.22) — the same CI run's OTHER shard failed a write-safety
  refusal, with no resource signature at all.** Recorded as its own finding below; the two must not be
  lumped together because their evidence points in opposite directions. Shard 2's samples at its failure
  are calm (`time_wait` 130, `free_mb` 12,722, `nonpaged_pool_mb` 233.4 — mid-range on every axis), and
  its failure is an ASSERTION, not a socket error.

* **R4.22 — OCCURRENCE 6 (0.99.0), on a slice that cannot have caused it, which is the useful part.**
  `test_flows.py::test_mutate_flow_does_not_retry_write_after_auth_refresh` failed on
  `Page.goto: net::ERR_NO_BUFFER_SPACE`; it passes standalone. Seventh unrelated test in six
  occurrences, consistent with "whichever test happened to be running".

  **Why this one is worth a line rather than a tally bump.** The slice it landed in (S10b) changes the
  MCP server's tool LISTING and nothing else — no browser code, no page script, no session lifetime, no
  request path. Occurrence 5 arrived with a plausible aggravator attached (the R4.29 drain holding
  sessions open longer) and the A/B then exonerated it. Occurrence 6 arrives with no candidate at all.
  Two occurrences in a row that resist a change-linked explanation is the strongest evidence yet that
  this is environmental — a host-level burst, not something this codebase does.

  That still is not a diagnosis, and the instrument to get one already exists
  (`scripts/sample_resources.ps1`, with baselines in R4.24). What is missing is a capture at the moment
  of failure on a LOCAL run: CI uploads its samples, a developer-host failure discards them. Wiring the
  sampler into the local suite the way CI has it is the cheapest remaining step, and it is the one that
  would turn occurrence 7 into evidence.

* **R4.22 — OCCURRENCE 5 (0.96.0), and the first one a change could plausibly have aggravated.**
  `test_mark_provenance.py::test_every_freshly_marked_step_names_the_signal_that_marked_it` failed on
  `Page.goto: net::ERR_NO_BUFFER_SPACE`; the file passes standalone (10/10, 28 s). Same signature, same
  platform, same "only in the full suite" shape as the previous four.

  **What is different, and is flagged rather than asserted:** the R4.29 drain holds each learn's session
  open for up to `write_window_ms` (2 s) longer, and the suite went 23m43s / 25m05s -> 28m26s. More
  concurrent socket and handle lifetime across a 28-minute run is exactly the pressure this failure is
  suspected to be about, so this slice is a candidate contributor. It is NOT diagnosed as one: a single
  occurrence proves nothing, R4.22's own post-mortem ruled out the mechanism STATUS.md predicted, and
  this register's rule is that a fix built on a wrong diagnosis is worse than none.

  **A CORRECTION TO THIS ENTRY, MADE BEFORE ACTING ON IT.** As first written it said the evidence needed
  was "the instrument R4.22 already asks for — sampling handles/sockets DURING the run and on success as
  well as failure". That instrument **already exists**: `scripts/sample_resources.ps1`, built in 0.84.0,
  sampling on a timer throughout and wired into CI on success as well as failure — and it measures the
  one resource every hypothesis here had skipped, NON-PAGED POOL, which is what WSAENOBUFS actually
  exhausts. Writing "what would make it evidence" for a tool already in the repo is how a slice ends up
  rebuilding one; the register is meant to prevent that, so the miss is recorded rather than quietly
  fixed.

  It also has published passing-run baselines (R4.24): TIME_WAIT max 386 / 377, handles max 54262 /
  52861, non-paged pool 189.9 / 195.9 MB. So the honest gap is not an instrument — it is **one
  measurement**: does the drain move that profile?

  **MEASURED (0.97.0), AND THE ANSWER IS NO — THE DRAIN IS NOT AN AGGRAVATOR.** Full suite, same host,
  one variable: the drain's `await` patched out for the off arm (a low `write_window_ms` would have been
  a CONFOUND, not a control — it also changes which requests are attributable, so the arms would run
  different write-safety logic).

  | | drain ON | drain OFF | |
  |---|---|---|---|
  | time_wait max | **389** | 409 | lower with the drain |
  | time_wait mean | **170.4** | 239.2 | **29% lower** |
  | nonpaged_pool max MB | **585.4** | 603.9 | lower |
  | paged_pool max MB | **693.6** | 738.2 | lower |
  | handles max | 153324 | 153294 | unchanged — ambient |
  | processes max | 341 | 346 | unchanged — ambient |
  | chrome_procs max | 8 | 8 | unchanged |

  Every suite-attributable column moves the SAME way and it is the opposite of the hypothesis; the two
  ambient columns are unchanged, which is the internal control saying the host was comparable across the
  two runs. The mechanism is unsurprising once measured: the drain adds no sockets, it spreads the same
  churn over more wall-clock, so instantaneous concurrency falls. **Occurrence 5's "candidate
  contributor" note is withdrawn.**

  **DO NOT COMPARE THESE TO THE CI BASELINES ABOVE.** They were taken on a working desktop with ~341
  ambient processes against a runner's ~157, which by itself accounts for the 3x handles and non-paged
  pool. Only the same-host A/B is evidence here. Reading a local number against a CI baseline is the
  category error that produced the four discredited post-mortems in the first place.

  **n=1 per arm**, stated rather than dressed up. What makes it worth acting on is not the sample size
  but that four independent columns agree in direction while two controls stay flat — and that the
  claimed effect was an INCREASE, which the data contradicts rather than merely fails to support.

### S10 (0.95.0) closed R4.15, R4.17, R4.18, R4.20 and R3.11 as ONE invariant — and its pre-merge audit found a CRITICAL inside the fix

**The invariant:** *nothing crossing a boundary loses its type or its reason, and every durable rename
goes through one helper.* Five findings that read as five patches share one mechanism, and this file's
standing instruction is to change the shape rather than add a fifth per-branch guard.

**R4.20 was worse than filed — seven renames, one guarded.** The entry named `FlowCache.put`. Measured at
0.94.0: `flows._save_meta` (retried, S4), and with NO retry `cache.put`, `cache.remember_refusal`,
`audit`'s artifact, `history.save_history`, the refreshed `storage_state`, and both `_preserve_corrupt`
functions. New `src/ultracua/fsio.py` holds `durable_rename` + `durable_write_text`; all seven route
through it, and an AST scan makes a bare `os.replace` outside that module fail — with a positive control,
because this register already recorded one enforcement test that was regex-shaped theatre.

**R4.18 and R4.17 shared one root:** `_save_meta`'s bare `OSError`. It is now `MetaUnwritableError`, and
`_quarantine` catches it to re-raise with the H9 finding FIRST and the persistence failure second —
deliberately not promoted to `FlowQuarantineError`, which would assert to the audit judge that the flow
IS quarantined, the very thing that just failed to happen.

**R3.11 got a total wrapper, not another arm.** `_load_meta_with_provenance` now wraps `_read_meta` in a
catch-all. Another `except` clause would have been the same bet one level down: it enumerates again, and
the next unanticipated exception escapes again. The catch-all lands on `unreadable`, never `absent`
(that is R3.8) and never `corrupt` (that branch destroys the file).

**R4.15 catches the FAMILY.** Naming `MetaUnreadableError` would have fixed six verbs and left the next
typed error a traceback — the enumerate-the-loud-outcomes error one surface over.

#### THREE DEFECTS WERE FOUND IN THIS SLICE'S OWN FIX CODE, AND ONLY ONE OF THEM BY A TEST

*Density in fix code, again, exactly as this file predicts. The order they were found in is the useful part.*

1. **By the sibling check, before any test ran.** Typing `_save_meta`'s failure silently UN-CAUGHT it in
   `_refuse_unreadable_meta`, whose guard was `except OSError`. A genuinely CORRUPT sidecar would have
   been reported `unreadable` — telling the operator to RETRY a transient blip that never happened rather
   than to inspect the preserved copy. Both messages loud, exactly one true, and the fix swapped them.

2. **By an existing enforcement test.** The loader's provenance AST scan went red on the `_read_meta`
   split, as designed. It now follows both halves and allows exactly one delegating return, re-verified
   RED against the mutation the original caught. A second existing test had been pinning the DEFECT:
   `test_the_unapprove_verb...` asserted that a `FlowReplayError` propagates out of `_flow_main` — i.e.
   that the operator gets a traceback — and `unapprove` is one of the six verbs R4.15 names.

3. **CRITICAL, by the pre-merge adversarial audit — 26 findings filed, 24 refuted, 2 survived.**
   `_record_run` runs immediately before four deliberate raises in `replay()`. Once its failure became
   typed it propagated out of those positions, so a transient sharing violation — the ~1-run-in-6 blip
   this very slice cites — swapped the operator's verdict:

   | | class | retryable | ledger row |
   |---|---|---|---|
   | intended | `WriteUnverifiedError` "the commit actuated and cannot be verified" | False | — |
   | actual | `MetaUnwritableError` "nothing was corrupted … RETRY" | **True** | **none** |

   An MCP agent honouring `retryable` re-invokes, `ledger.is_committed` is False, the elicit fires and
   **the commit actuates a second time**. Inviolable #3, created by the fix for R4.18, and confirmed
   against a `main` worktree: pre-diff the same failure was a bare `PermissionError` that no
   `except FlowReplayError` caught, so it could never reach the retryable channel at all.

   **The guard already existed on the sibling half.** `_record_run` passes `on_unreadable="skip"` and its
   own comment argues at length that bookkeeping must not mask a real failure with an IO error. The SAVE
   half never got it. So the fix went into `_record_run` rather than into five call sites — a per-caller
   rule is what a sixth raise site forgets — and `MetaUnwritableError.retryable` became False, which the
   family's own convention already implied: of eleven typed classes the only ones that are retryable are
   raised strictly before anything can act, and the flag had been copied from the read twin without
   regard to position.

   **Both halves are pinned**, because "make the sidecar failure quiet" satisfies the safety property
   completely and undoes R4.18: bookkeeping stays quiet, every trust-changing write (`approve`,
   `unapprove`, `release`, `_quarantine`) stays loud, and a third test asserts `replay` still RECORDS on
   all eight paths — "never raises" is also satisfied by never being called.

   The audit's second survivor: `durable_write_text` leaked the temp when the WRITE half failed (ENOSPC,
   EIO, a quota hit) while the new message asserted it had been removed. The leak was pre-existing; the
   sentence denying it was new — a fail-loud message with a false clause, which is the `_META_CORRUPT` /
   `_META_UNREADABLE` defect verbatim. Fixed in `fsio`, not in the message, because seven writers share it.

**The transferable part.** The adversarial audit is now six-for-six in this codebase, and this is the
second time it caught a write-safety critical that a full green suite, a clean `drift_bench` and the
author's own sibling check had all passed over. The refuters are also load-bearing rather than ceremony:
24 of 26 findings were refuted, several of them plausible-sounding and traced to pre-existing behaviour
the diff does not touch — an unrefuted finding list would have sent this slice chasing four phantoms.

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

## ✅ FIXED in 0.96.0 — R4.29. A write deferred past the loop ESCAPED the learn watcher entirely, and the flow cached as a clean READ. **HIGH, inviolables #2 and #3**

**THE FIX.** `_author_steps` now waits out whatever is LEFT of the act window before removing its request
listener. That is not a widening: the comment on `act_window` already promises that a request counts
"within `write_window_ms` after it closes", and honouring that promise requires the observer to outlive
the window. No new request becomes attributable; the observer simply stops leaving early.

Measured against the realistic shape — a commit chained N bare tasks out — the fix converts every escape
into a refusal: **18/18 across depths 2, 8 and 16**, where the same depths previously escaped or lost
their POST entirely.

**A CONSEQUENCE, stated because it looks like a downside and is the opposite.** Holding the page open
also means a commit scheduled for +900 ms now actually LEAVES, where the session sometimes closed first
and it never fired. Measured: the same cell reports "the commit never reached the server" without the
wait and a POST with it. That is not a new write being caused — it is the same page doing the same
thing, minus a race we were silently relying on. Closing fast enough to abort a deferred commit is not a
safety property; it is a coin flip whose other face is this finding.

**THE COST — AND THE FIRST MEASUREMENT OF IT WAS WRONG, WHICH THE PRE-MERGE AUDIT CAUGHT.** A serial
run put the full drain at 175.5 s against `drift_bench`'s 180 s budget, and the cost table built from it
was non-monotone on its face: 0.4 s of added sleep reading FASTER (106.6 s) than no sleep at all
(115–127 s). No additive wait can do that. It was host variance read as signal — this file's own rule
("timing under unknown load is not evidence") broken by the slice quoting it, and it was quoted in three
places and used to pick a safety bound.

A controlled A/B, arms alternated to cancel drift, is monotone and reproduces to ±0.4 s:

| drain | bench wall |
|---|---|
| none | 101.2 s |
| 800 ms | 111.2 / 111.1 s |
| **full window (2 s)** | **125.5 / 125.9 s** |

**AND THE SUITE COST WAS ALSO MIS-STATED — 0.97.0, corrected by A/B.** #145 reported "23m43s -> 28m26s"
for the suite, which was again a comparison of two runs made at different times rather than a controlled
one. Same host, one variable (the drain's `await` patched out):

    drain ON  27m31s          drain OFF  19m03s          => ~8.5 min, about 45% overhead

That is materially larger than the ~4-5 min the slice claimed, and it is the THIRD time in one session a
cross-run comparison was published as a measurement here. The number is banked deliberately, not
excused: the drain closes a HIGH write-safety hole and no cheaper variant has been shown correct. The
lead worth pursuing is that the code WAITS before removing the listener when it could instead remove it
LATER — `_watch_request` already discards anything outside the window, so natural post-loop work
(finalize, verify-replay, teardown) could cover much of it at zero added wall-clock, with a bounded wait
only for the remainder. Not attempted here: it is a patch on a patch in a write path, which this file
calls its most defect-dense move, and it needs its own RED test and audit.

So the complete fix costs ~25 s and leaves ~55 s of budget headroom. **An 800 ms cap was drafted on the
bad number and is NOT what shipped**: it bought ~14 s in exchange for an uncovered 800 ms–2 s band, and
introduced a second setting (`write_drain_ms`) alongside `write_window_ms` — two knobs for one concept,
free to drift, which is the SHAPE OF THIS VERY BUG. One setting now governs attribution and observation
together, so "attributable for 2 s, observed for 0" is inexpressible rather than merely fixed.

**A NUMBER IN THE FIRST FILING OF THIS ENTRY WAS WRONG AND IS WITHDRAWN.** It said "measured 5/6 on
Windows" from a `setTimeout`-chain reproduction. Re-run on a quieter host the same chain measured **0/8**,
and at 32 tasks the commit never left at all because the page was torn down first. Two contradictory
rates from the same fixture is this register's own signal that a rare bug is a HARNESS problem, not a
patience problem (R4.26). The race was then REMOVED rather than re-measured: the commit waits on a
response the test server holds, released from inside `flows._make_finalize`'s callable, which
`run_cached` awaits after `_author_steps` has returned and before the session closes. Order fixed by the
call graph, not by the host — **10/10 against pre-fix source**. Quote that construction, never the 5/6.

**THE PRE-MERGE AUDIT'S REAL CATCH WAS THE METHOD, NOT THE CODE — 22 filed, 3 survived, and all three
were the same mistake.** The shipped mechanism came through clean. What did not was the MEASUREMENT
around it: a bad bench number, the 800 ms cap chosen because of it, and a register entry describing the
variant that number justified rather than the code beside it — two artifacts in one commit giving the
next person opposite instructions about which knob to turn.

That is worth more than the fix. Every previous audit here caught a defect in fix CODE; this one caught
a defect in the EVIDENCE, and the evidence was what chose the safety bound. The tell was available for
free and nobody looked: **a cost table where adding a sleep made the run faster is refuted on its face**,
before any re-measurement. Serial timing runs on a developer host are not an A/B; alternate the arms,
repeat, and check the series is monotone in the thing you varied — or say plainly that the number is not
evidence, which this file already instructs and which the slice quoting that instruction ignored.

**Original filing follows.**

## R4.29 (as filed) — A write deferred far enough ESCAPES the learn watcher entirely

**Found by following a CI flake instead of silencing it.** `test_a_page_synthesised_click_must_not_launder_a_deferred_write` kept losing its premise on ubuntu 2/2 while windows passed. Two diagnoses were
built and both were wrong — a premise pin (0.93.0) and a `write_window_ms` pin (0.94.0) — so the third
attempt changed the SENSOR rather than the inference: the premise message was made to report
`mutating_sources` instead of guessing a cause. The next CI run answered it in one line:

    OBSERVED: the write was attributed to NO step — it fired outside every act window / grace tail,
    or after the watcher was removed. steps=[('continue', False, None),
                                             ('confirm the address', True, ['keyword'])]

**Mechanism.** `_author_steps` breaks out of its loop and immediately runs
`page.remove_listener("request", _watch_request)` (`flow.py`), with **no drain and no settle**. A request
the page has not dispatched YET is invisible from that instant. `write_window_ms` cannot help: it bounds
the attribution of requests that were OBSERVED, not the lifetime of the observer. So the grace-tail work
(R3.2's `graces` list, AB-1) is all downstream of a listener that may already be gone.

**What that costs, measured.** Fixture: `Continue` arms, a benign bait named **`Show details`** — no
`MUTATING_KEYWORDS` term anywhere, deliberately, so the classifier cannot accidentally cover the hole —
schedules the commit N bare tasks out. Learn, then check the cached recipe:

| deferral | POST left | cached | `wire` mark | gated |
|---|---|---|---|---|
| 2 tasks | yes | **refused** (`"a write fired on the wire during discovery but no step could…"`) | no | — |
| 4 tasks | yes | **refused** | no | — |
| **8 tasks** | **yes** | **CACHED** | **no** | **`[]`** |
| timer 50 / 200 / 800 ms | no (browser torn down first) | cached | no | `[]` — harmless, nothing committed |

**5 of 6 repetitions** of the 8-task cell cached a flow with `gated=[]` while the server recorded the
POST. The sixth was refused, which is the boundary moving under ordinary timing — the same boundary the
ubuntu runner crosses at 2 tasks because it is slower relative to the page's task queue.

**Why this is worse than a missed gate.** Three guards fail together, and they fail QUIETLY:
* `wrote["hit"]` is never set, so `_learn_once`'s "a write fired but nothing could be attributed"
  refusal — the guard that exists exactly for this — never arms;
* no wire promotion runs, so the commit's step is never marked `mutating`;
* the flow therefore caches as an ordinary READ: no drift gate, no `precond_scope`, no Idempotency-Key,
  and heal- and suffix-replan-eligible.
Replay then re-fires that commit on every run, ungated and un-keyed. Inviolable #3, and #2 with it —
nothing anywhere is loud.

**This is NOT R4.5.** R4.5 is a page manufacturing a PROVABLE cause so the placement is trusted. This is
the write never being seen at all. They share a fixture family, which is how one masked the other for
three CI rounds.

**Do not "fix" it by widening `write_window_ms`.** That was attempt two, measured: sweeping the injected
inter-step gap with the tail at 30 s, the AB-1 arm survives 0/1/3/6 s gaps — the tail is simply not the
variable. The fix has to be about the OBSERVER's lifetime: a bounded drain before the listener comes off,
with the same "retrying must not become swallowing" rule the durable-write helper follows. Anything that
waits must be bounded and must fail LOUD on expiry, or it becomes a stall on every read flow.

**Sequencing.** The fix touches `flow.py`'s write path, so it takes the full gate: RED test verified
against current main, fix in the mechanism, a dimension in `tests/test_write_safety_invariants.py`,
siblings checked (`recorder.py` has its own instrumentation lifetime — check it), suite + `drift_bench`,
and a pre-merge adversarial audit. It also needs a DETERMINISTIC harness rather than the 5/6 race above:
build the mechanism on demand (R4.26), do not fish for it.

**THE SIBLING CHECK CAME BACK 2-FOR-3, AND THAT IS WHY THIS WAS MISSED FOR SO LONG.** Three places
attach an observer and tear it down. Two already wait:
* `recorder.py` — `wait_for_timeout(max(settle_ms, 150))`, then awaits its pending drain tasks, then a
  final `_drain()` before close. The recorder got this right first, which is this register's own
  most-repeated shape pointing the usual direction.
* `_maybe_heal` — wraps the actuation in `expect_request(..., timeout=write_settle_ms)`, added by R3
  for precisely this reason ("Reading `wrote['hit']` the instant `act` returns is a zero-width window").
* `_author_steps` — **had no wait at all.** Its own comment claimed the grace tail covered it, and the
  grace tail governs ATTRIBUTION, not OBSERVATION. Two siblings with a guard, one without: the pattern
  the top of this file says predicts the next bug, found by following a CI flake instead of silencing it.

## R4.30 — OPEN. A commit deferred beyond `write_window_ms` is still unobserved

The residual R4.29's fix does not close, pinned rather than described: `tests/test_watcher_drain.py::
test_a_commit_deferred_BEYOND_the_window_is_still_lost`, strict-xfail, measured 10/10 at 0.96.0.

The drain restores the observer's PROMISED lifetime; it does not extend it. A commit dispatched more
than `write_window_ms` after the last act — a long debounce, a slow awaited round-trip — is still
invisible, and the harm is identical to R4.29's: the flow caches as a clean read and replay re-fires the
commit ungated and un-keyed.

**Why it is not simply fixed by watching until the session closes.** That changes what counts as a
CAUSED write. Today a request outside every act window is background noise by definition, and that
definition is what stops an ordinary page's heartbeat or non-vendor analytics POST from refusing every
flow it appears in. Removing it points straight at the population R4.27 already measures — 12 of 12
GraphQL-style reads filed as writes — and D0 is blocked indefinitely for exactly that reason. So the
prerequisite is a MEASUREMENT of what watching-until-close would refuse, on a real read population,
BEFORE any code. Same gate as D0's lever (ii), same reason.

Direction of error, for whoever picks this up: a missed late write is a silent ungated replay; an
over-eager one breaks a large working read population. Neither is free, which is why this is filed
rather than fixed in the slice that found it.

### MEASURED at 0.97.0 — the proposed closure would refuse reads WITHOUT catching commits. Do not build it.

The gate was "measure the refusal population first". Done, by OBSERVATION rather than a prototype: the
fixture server timestamps every request, and a wrapper on `flows._make_finalize` marks the moment
`_author_steps` returns — i.e. the moment the observer is dropped today. Anything non-idempotent logged
after that marker is what "watch until close" would NEWLY count. No decision logic was altered.

**First result: the exposure band is much narrower than this entry implied.** Sweeping the deferral:

| deferral after the triggering click | commit fires? | seen today? |
|---|---|---|
| ≤ 2000 ms (inside `write_window_ms`) | yes | **yes** — the R4.29 drain covers it |
| 2500 ms | **no — the page is already torn down** | n/a |
| ≥ 3000 ms | no | n/a |

A commit deferred past its own step's window mostly **never happens**: the session closes first, so there
is no write to miss. The band is `[last act + write_window_ms, session close]`, and close follows fast.

**And the case that sounds like R4.30 usually is not.** A commit deferred past its OWN step's window but
with later steps still to run is observed inside a LATER step's window — already counted today. That is
AB-1's territory (the gate lands on the wrong row), not this one. R4.30 is only the tail: a commit
deferred past the LAST acting step's window, where teardown then wins the race.

**Second result, and it settles the disposition.** Across eight ordinary read shapes — heartbeats at
500 ms and 2 s, a 3 s autosave, first-party analytics, a 1 s GraphQL poll, a `pagehide` beacon, late
telemetry, and a quiet control — **1 of 8 would be newly counted as writing** (first-party analytics
landing at 2.5 s), while **0 of 2 deferred-commit shapes would be newly caught**, because neither commit
fires before teardown.

So the trade is not "a little over-refusal for a real safety gain". In the configuration measured it is
**pure cost**: it refuses a read population and catches nothing. That is D0's outcome again, arrived at
by measurement instead of by shipping it — and it is why this stays open as a bounded residual rather
than becoming a fix.

**What would change the answer, stated so the next person does not re-run what is already done.** All of
the above is with a scripted provider, where steps are milliseconds apart. A real learn spends seconds
per step on the LLM, so the session lives longer and the band widens — a commit deferred 3 s past the
LAST step might then fire before close. Measuring THAT needs a paid provider arm or an artificially
slowed one, and it is the only version of this question still open. The read-population cost would widen
with it, in the same proportion and for the same reason.

**Withdrawn from the entry above:** the framing that this is mainly about "a long debounce, a slow
awaited round-trip". Both were tested and neither survives teardown.

## ✅ FIXED in 0.98.0 — R4.31. An unrecognised `mode` fell through to LEARN, re-authoring the flow and RE-FIRING its write. **HIGH, all three inviolables**

**Found by writing S14's property for inviolable #1** — the first real input it was given. That is the
plan's own thesis paying out: this suite is regression-shaped, ~60 findings across four audit rounds and
not one discovered by it, because a point assertion on a happy path cannot fail for a path nobody
imagined.

`flow.run_cached` dispatched on `mode in ("auto", "replay", "repair")` and then
`mode in ("replay", "repair")`. **An unrecognised string matched neither and fell past both, onto the
LEARN path.** Measured with a provider present, which is the daemon's ordinary state:

| mode | report.mode | llm_calls | POSTs |
|---|---|---|---|
| `"replay"` | replay | 0 | 0 |
| **`"bogus"`** | **learn** | **2** | **1 — the write fired again** |
| **`"REPLAY"`** | **learn** | **2** | **1 — a CASE TYPO re-placed the order** |

An LLM call the caller never asked for (#1), something other than what was requested and no reason given
(#2), and a repeated commit (#3) — from one line of control flow. `daemon/server.py` passes
`params.get("mode", "auto")` straight from JSON-RPC **without validating it**, so the bad value can
arrive off the wire.

**The fix REFUSES rather than degrades.** "Unknown -> treat as replay" would be this file's own worst
habit: guessing what the caller meant is exactly why the fall-through read as harmless for so long. A
caller who mistypes a mode has a bug and the fastest thing to do is tell them. `_MODES` is now declared
ONCE beside the dispatch that reads it — the signature comment said `"auto" | "learn" | "replay"` while
the body also accepted `"repair"`, so the documentation and the code had already drifted apart, which is
how `repair` came to be an undocumented public mode nobody could discover.

## ✅ FIXED in 0.98.0 — R4.32. Every failed replay returned an EMPTY `note` while the cause sat in the traces

Inviolable #2's other half, and the second thing S14's properties turned up. `_replay` built its
`FlowReport` with no `note`, so a caller checking the documented reason field on a failure got `""`
while the real cause — e.g. `"locator unresolved or ambiguous (drift)"` — sat in
`traces[-1].meta["note"]`, which no caller walks.

**Severity, stated honestly: this is loud-but-unexplained, not silently wrong.** The caller does see
`success=False`. It is filed because the engine is the surface `ultracua run`, the JSON-RPC daemon and
every `run_cached` library caller read directly, and "a failure carries its reason" is a contract this
project states rather than an aspiration.

The note is sourced from the last trace that recorded one, so the message stays the mechanism's own
words rather than something invented at the boundary, and it is set ONLY on failure so the success path
is byte-identical and no caller starts seeing a note where it never had one.

* **R4.28 — OPEN, and filed as an OBSERVATION rather than a defect: `_write_owner` becomes CONFIDENT
  because a neighbour's grace tail EXPIRED, not because evidence arrived.** Found while diagnosing an
  ubuntu-only CI failure of `test_the_control_a_write_deferred_two_tasks_is_still_over_gated` (PR #143,
  and once before as a bare `gated=[1]` on PR #142). The test half is fixed in 0.94.0; this is the half
  that is about the product.

  `_write_owner` credits a step when it is the ONLY live candidate, and credits nobody when there are
  two — "undecidable from timing", which is R3.2 and is the honest answer. But candidacy expires: a
  step leaves `live_tails` when its `write_window_ms` grace tail runs out. So the SAME deferred write,
  on the SAME fixture, is undecidable or confidently attributed depending only on how long the gap
  between two steps happened to be. Measured on Windows by shrinking the tail rather than waiting for a
  slow host — the same experiment from the other end, and deterministic:

  | `write_window_ms` | AB-1 arm reached | gated |
  |---|---|---|
  | 30000 / 2000 (default) / 200 | yes | `[0, 1]` |
  | 50 / 10 | **no** | **`[1]`** |

  **What is NOT claimed.** In this fixture the surviving candidate is the step that really did cause the
  write, so the tighter gate is correct and the outcome is SAFE — arguably better than the over-gate.
  The harmful direction — a write caused by an EARLIER step whose tail expired, leaving a later
  innocent step as the unique "owner" and confidently gated alone, with AB-1's blanket never firing —
  is **not reproduced**, and this entry does not assert it. It is filed because the mechanism plainly
  permits it and because this register's rule is to write down the measurement, not the inference.

  **Why it is worth a row anyway.** It is the same shape as `landed` and the keyword classifier: a
  question with no oracle answered by a proxy, where the proxy's confidence is an artefact of the
  instrument. R3.2 says attribution from timing is impossible; the uniqueness test quietly re-introduces
  it whenever the clock thins the candidate set to one. Any future work here must not "fix" this by
  widening the tail in production — that trades one arbitrary threshold for another and would make every
  ordinary two-step flow multi-write, which is the cost AB-1's own comment already prices.

  **Reproduce it before acting on it**, with the harness above (`scratchpad/ab1_premise.py` shape: drive
  `_learn_once` against the fixture at several `write_window_ms` values and read `arm_reached`/`gated`).
  A fix built on the un-reproduced direction would be a fix built on a wrong diagnosis.

  **RE-MEASURED at 0.93.0, and the human-verdict verb does NOT dispose of it — 12 of 12 refused.** The
  plan sequenced R4.27's disposition behind the annotation work (`flow mark`, 0.93.0) on the argument
  that a human can separate this population from real commits where no automated rule can. Landing that
  primitive is not the same as satisfying the dependency, so the twelve controls were re-run against
  shipped code rather than reasoned about:

  | | of 12 |
  |---|---|
  | cached as WRITE flows (R4.27 still live) | 12 |
  | step carries `wire` | 12 |
  | `flow mark --read` **REFUSED** | 12 |
  | demotion allowed | **0** |

  The 7/5 split is as filed: seven record `['keyword','wire']`, five (`Filter results`, `Export CSV`,
  `Next page`, `Refresh data`, `View details`) record `['wire']` alone. The wire promotion stamps
  `MARK_WIRE` on BOTH of its branches — the already-marked one too, deliberately, so the field tracks the
  strongest signal rather than the control's name — so a keyword false positive that also queries over
  POST ends up **less** demotable than one that does not. `wire ∉ _DEMOTABLE_MARKS`, so `flows.mark_step`
  refuses every one of the twelve, naming the evidence.

  That refusal is CORRECT, and it is why this closes nothing: the verb declines to overrule a POST that
  was watched leaving the browser, and a GraphQL query is a POST that was watched leaving the browser.
  The verb cannot separate this population for the same reason the wire cannot. **R4.27 needs a sensor
  class that can, and the human-verdict one is now spent on it** — which is a D5-shaped result arrived at
  by measurement instead of by two more attempts. Pinned end-to-end by
  `tests/test_annotation_disposition.py`, in both directions: it fails loudly if a GraphQL read stops
  being filed as a write, so closing R4.27 cannot leave this entry stale.

  **A correction that fell out of the same run.** `test_a_human_may_demote_a_mark_that_was_only_ever_a
  _keyword_guess` described its fixture as "R4.27's population". It is not — R4.27's population all
  carries `wire`. The test is sound and pins a real permitted case; only its claim about which
  population it represents was wrong, and it is fixed.

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

  **OCCURRENCE 2 (0.90.0, local windows, incidental to the R4.5 slice and recorded because a single
  observation was the stated reason not to act).** Same shape, DIFFERENT test — so the "one flaky test"
  reading is now unavailable:

      FAILED tests/test_multiwrite.py::test_multiwrite_barrier_rejects_a_nonunique_confirm
        playwright TimeoutError: Locator.wait_for: Timeout 5000ms exceeded.
        Call log: - waiting for get_by_text("Saved") to be visible

  Full suite, 1 failed / 867 passed / 1 xfailed in 23:47. The same file re-run in isolation: 8 passed in
  31.9 s. The failing wait is a `record()` DEMO step — a localhost round-trip again, and again nothing in
  the JS-reveal-race class. Two occurrences, two different tests, two different confirm strings, one
  signature: a localhost round-trip exceeding a 5 s action budget.

  **What this occurrence does NOT establish, stated because the temptation is to over-read it.** The host
  load was not measured, so the wall-clock (23:47 against a nominal ~21 min) is not evidence — this
  register's own rule about timing numbers under unknown load applies to its own filings first. The
  sampler that exists to characterise the next occurrence runs in CI, not locally, so occurrence 2 came
  with no resource profile at all. It moves the count from one to two and removes the single-observation
  argument; it does not diagnose anything, and it is not grounds for raising a timeout.

  One negative result worth keeping, since it is the hypothesis a reader reaches for first: the clean
  re-run of the same tree was SLOWER — 868 passed in **26:56** against the failing run's 23:47 — and did
  not fail. So wall-clock does not track the failure, and "the host was busy" is not supported by the one
  cheap observation available. Whatever the mechanism is, it is not simply "slower run, more likely".

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

### ✅ FIXED in 0.95.0 (S10) — R3.11. Narrowing `except Exception` to `(ValueError, UnicodeDecodeError, OSError)` broke `_load_meta`'s explicit "It never raises" contract — a deeply nested meta raises RecursionError straight out of `health()`, tracebacking `flow status` and the MCP `tools/list` loop on one bad flow

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

### ✅ FIXED in 0.106.0 — R3.12. `DryRunArbiter`'s act window has the SAME overlapping-tail shape R3.2 just removed — on a multi-write flow a deferred write is labelled with the NEXT commit's step and intent in the report a human approves from

*medium **as filed**; the mitigation that kept it there was measured FALSE, see below. Lens `writes`,
NOTICED while redesigning R3.2, **reproduced end to end 3/3 at 0.105.0** before a line of fix was written*

**REPRODUCED, and the entry's own hedge was the thing that needed correcting.** A two-commit flow —
step 0 "Send invite" POSTing on a `setTimeout` past `write_settle_ms`, step 1 "Publish order" — dry-run
at 0.105.0, 3/3 identical:

    HELD POST /invite  -> labelled step=1 intent='publish the order'  in_window=True late=True

`/invite` is step 0's write; no other control requests it. The report a human approves from says the
publish step sends it.

**THE STATED MITIGATION DOES NOT EXIST — it was reasoned, not measured, and it is wrong.** This entry
claimed the row is "partly self-revealing … commit B's intent beside commit A's key", treating the
window and the `Idempotency-Key` as two independent sources. They are one source. The key is a
*context* header, live for whichever step is mid-act, so a write issued during step 1's act carries step
1's key:

    per-step keys   step 0 uca-7e04e8cb…   step 1 uca-7ac0e3c1…
    the held row    step=1, intent='publish the order', key=uca-7ac0e3c1…   <- step 1's, not step 0's

Every field agrees with every other field and all of them are wrong. Nothing in the report betrays it.

**Why the naive fix is BLOCKED, and what shipped instead.** This entry proposed bounding attribution to
the drained window rather than a 2 s tail. That is a temporal design, and **D5's impossibility #1 rules
it out by measurement**: no constant is simultaneously long enough to catch a deferred write and short
enough to exclude the next step. D5 was recorded after this entry and nobody had reconciled the two.

So the fix does not attribute anything. It changes what the report is ALLOWED TO CLAIM. `HeldWrite`
gains an `attribution` axis with three states — `step` / `ambiguous` / `ungated` — and one function,
`DryRunArbiter._attribute`, decides it.

**THE RULE HAS NO CONSTANT IN IT, and getting there took two wrong drafts that the pre-merge audit
caught.** The sound candidate set for an arriving write is *every step that has already acted*: a step
whose window has not opened cannot have caused it, and nothing observable can narrow the set further —
that narrowing IS D5's blocked question. So a step is NAMED only when it is the only one that has acted;
otherwise the row names none and lists the candidates.

Both discarded drafts tried to narrow the set, and each failed in one of this register's standing
shapes. **Both passed the full suite (1044 tests), the fix's own end-to-end tests, and a purpose-built
matrix** — the sixth consecutive time that has been true in this area:

| draft | narrowing | how it died |
|---|---|---|
| 1 | the grace tail is the candidate horizon | a CONSTANT then decided whether a name was claimed: the same causal situation reported `ambiguous` at 2000 ms and confidently named the wrong step at 0 ms. D5's impossibility #1, re-derived one module over — **and the counterexample was a cell in the fix's own matrix**, asserted as correct behaviour |
| 2 | a step that has written once is `settled` | "we saw a write" is not "we saw all its writes". A control firing an analytics ping AND deferring its real write settled itself, and the deferred write was then confidently named with the NEXT step — R3.12's exact row, **silently** (no warning fired), reproduced 3/3 at deferrals of 150/300/600 ms |

Draft 2's exposure was *wider* than the defect it was fixing: the prompt write resolves
`expect_request` immediately, so the step's window closes in milliseconds and the deferral needed to
escape drops from 1500 ms to 150 ms.

**Two details that were nearly defects in the fix itself:**

* **`step = -1` would have been the `anchor_id=None` fault again.** It already means "ungated"; making
  it also mean "ambiguous" rebuilds the exact overloaded sentinel that has now cost R3.7 two attempts —
  two different facts, one value, and the consumer cannot tell them apart. Hence a separate axis.
* **`steps_representative` reads `held[].step` and filtered out `-1`.** Left alone, an unattributable
  hold would have made a run that HELD a write report every step as representative — strictly more
  dangerous than the mislabel being fixed. It now reads `earliest_step` and clamps an unknown to 0, so
  not knowing always makes the artifact MORE conservative.

**THE PRICE, measured rather than argued, and it is the design rather than a tuning artifact.** A
multi-write flow's holds are never named. Measured on an ordinary 3-mutating-step recipe where only the
last step writes, and writes PROMPTLY — the easiest possible attribution:

    0.105.0   step 2 'confirm the order'                 steps_representative 2
    0.106.0   ambiguous, candidates=[0, 1, 2]            steps_representative 0

Draft 2 existed to buy that precision back and could not do it soundly. The report still lists every
held request in order with its method, URL, body and key; what is lost is the per-step NAME, and
recovering it needs the causal signal D5 blocks. Pinned as a matrix cell
(`a mutating step that never writes keeps every later hold ambiguous`) so a future change is deliberate.

**Residual, stated.** A write caused by a NON-mutating step's control never has a candidate of its own —
such a step opens no window — so in a single-mutating-step flow it is named with that step. The audit
confirmed this at the arbiter level and REFUTED it end-to-end: the learn path promotes a step to
mutating from wire evidence (`flow.py:627-635`), so a control that writes during learn always gets a
window. It survives only for a page that writes at replay and did not at learn. Note also that with one
candidate "ambiguous" is not expressible — naming it is the only answer available.

**And the sibling this reproduction exposed is worse than the finding: `R4.39`.** The same "whichever
step is mid-act" fact that refuted the mitigation is live on the REPLAY path, where it picks the
Idempotency-Key that actually rides the wire.

*Original entry follows.*

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


## ✅ FIXED in 0.101.0 — R4.33. The drift-corpus row added to adjudicate R3.7 cannot fail for R3.7, in two independent ways

*low (an instrument defect, not a product one), lens `bench`, no inviolable, measured in S11*

**Where.** `benchmarks/drift_fixtures.py` — `_nested_action_rows()` and the `row-nested-action` SCENARIO
entry. Added by plan slice S1b, whose stated job was to give the bench fixtures for the shapes it is
named as adjudicator for; the plan then scoped S11 as "adjudicated on the S1b-extended corpus".

**Mechanism.** The row is a nested action list, `tr > td > ul.actions > li > a`, and its comment claims
"the inner `li` has empty collapsed text apart from the control itself". That sentence contradicts
itself: the control's own text IS the `li`'s collapsed text. The link renders "Details", so `anchorOf`
anchors on the inner `<li>` exactly as the bind walk does and the two never disagree.

Fixing only that is not enough, and this is the part worth carrying: with an icon-only control the row's
sole identity is the `href` of the link INSIDE the nested `<li>`, so `rowIdOf` returns the same string
for the `<li>` and the `<tr>` and the walks still agree. A faithful fixture needs BOTH an icon-only
control AND an identity the outer row has that the inner container does not — an `id` on the `<tr>`,
which is R3.7's own description (`id:order-3` at capture against `href:/cancel/3` at bind).

**Measured, in a scratch A/B rather than asserted.** Shipped fixture: identical numbers under pre-S11
and post-S11 `locators.py` (survival `k1:2/3 k2:2/3 k3:3/3 k4:1/3`, `bound_by={'css':5,'none':6,
'role+name':1,'role+name~':2}` both arms). Corrected fixture, pre-S11 code: `bound_by={'none': 14}` —
every row refuses, survival 0 at every k, 12 rows routed into heal. Post-S11: 5 binds, survival
`k1:1/3 k2:1/3 k3:2/3 k4:1/3`. The first A/B is what makes this a finding; the second is what a working
corpus row would have shown all along.

**Not fixed in S11, deliberately** (and R3.7 itself did not land either, so the corpus row is now the
FIRST thing its next attempt needs). Landing the corrected fixture bumps `FIXTURES_VERSION`, requires a
deliberate re-baseline, and moves `predicted_agreement` for that scenario from 75% to 50% (3 mismatches
to 6). **State that number with its caveat:** it was measured in ONE arm, with the corrected fixture AND
attempt 1 both in place, so it does not separate the fixture's effect from the code's — it is evidence
that triage is NEEDED, not a measurement of how much. The mechanism is clear enough to expect it: the
prediction model's kill sets are calibrated on a target carrying BOTH visible text and an aria-label,
and an icon-only target's name dies to `strip_aria_label` outright. Those mismatches would enter the
baseline permanently and dilute the signal for every other row, so the shape wants triage in its own
slice, not a bundled re-baseline. What S11 does instead is delete the false claim: the fixture
now says what it actually measures and cites this finding.

**A third site inherited the same wrong belief.** `tests/test_drift_bench.py`'s `_KNOWN_HEAL_DECLINES`
allowlist carried `row-nested-action/reparent` with a comment attributing the decline to R3.7 and
calling it a false refusal. It is neither: `reparent` moves the control out of the table entirely, so
the element really is in no row and the containment guard correctly has nothing to confirm. Measured —
the decline is unchanged by S11's fix (`declined=1` before and after), which is what an R3.7-caused
decline could not be. The comment is corrected in S11; the entry stays, because the decline is real.

**Failure.** Someone closes a row-guard finding, runs `drift_bench`, sees the scenario named for it
unchanged, and reads that as evidence the fix worked — or worse, as evidence a regression did not
happen. It is the bench equivalent of a regression test that passes against the old code, which this
register already treats as proving nothing. The allowlist variant is worse, because an entry that
names a now-CLOSED finding as its reason reads as a leftover somebody forgot to remove.

**FIXED in 0.101.0.** `row-nested-icon` is added as a SEPARATE scenario rather than an edit to its
sibling: `row-nested-action` keeps measuring a labelled nested action list — a legitimate shape whose
numbers stay comparable — and the new one carries the icon-only control AND the `id` on the `<tr>` that
the divergence needs. `FIXTURES_VERSION` 1 → 2, deliberate re-baseline written up in
`baselines/README.md` with what moved and why.

**The instrument now shows R3.7's damage for the first time:** the scenario binds nothing on any row,
including its pristine arm, and the survival curve's numerators are unchanged (20/24 → 20/27 at k1) —
nothing that used to bind stopped binding, 14 rows that bind nothing were added. **12 of those 14 should
move when R3.7 closes; the other two (`target_removed`, `target_replaced_crosstag`) carry
`target_present=False` and must NEVER bind** — an earlier draft of this entry said "a block of 14",
which would have made a correct fix look incomplete. The audit also verified the row can move at all, by
simulating a fix: `bound_by` goes `{'none': 14}` → `{'css': 4, 'none': 9, 'role+name': 1}` and survival
`0/3` → `k1 1/3, k3 2/3`. A corpus row that could not move would have been as useless as the one it
replaces.

**And it found something on its first run, which is the argument for fixing an instrument rather than
working around it.** Three of its heal-eligible rows re-ground to a byte-identical recipe, so the heal
reports a persisted repair that repairs nothing — filed as **R4.35**. No other corpus row has ever done
that, because every other drift changes the page enough that the re-grounded spec differs.

**The cost side, corrected after the audit refuted the first version of it.** There are 9 new
`predicted_mismatches`, all on this scenario — but they DO NOT have one cause, and the claim that they
would "disappear with R3.7" was wrong. Checked against the two committed baselines:
`decoy_substring+strip_aria_label` already mismatches on four other scenarios and `reparent` on three,
i.e. they are pre-existing `predict()` gaps that appear wherever those primitives appear and have
nothing to do with the row guard. Under a simulated R3.7 fix the audit measured the list going 9 → 6,
with a tenth appearing. **So per-row triage IS required when R3.7 closes, and the COUNT is not the
signal** — the opposite of what this entry first said.

## R4.35 — OPEN. A heal on a row the ROW GUARD refused re-grounds to a byte-identical recipe, reports a persisted repair, and repairs nothing

*low, lens `bench`/`heal`, no inviolable (loud, and the run itself succeeds), measured in R4.33*

**Where.** The recovery ladder's persist step, observed through `benchmarks/drift_bench.py`'s
`recached` (`steps_hash(after) != steps_hash(before)`) against `heal_persisted`.

**Mechanism.** Every drift in this corpus until now was a page change: the target is renamed, hidden,
reparented, its role swapped. Re-grounding after one of those produces a spec that genuinely differs, so
the re-cache moves the digest, the approval re-gates, and the repair survives to the next run. R3.7's
refusal is not like that. The spec is perfectly good; what fails is a disagreement between two walks
over it. So the heal re-grounds, gets the SAME spec back, re-caches it, and reports success — and the
next replay refuses identically.

**Measured** on `row-nested-icon` at 0.101.0, three rows (`sibling_removed`, `aria_hide`,
`aria_hide+sibling_removed`): `heal_persisted=True`, `acts == golden == ['go','DONE']`, and
`steps_hash_after == steps_hash_before`. The EQUALITY is the evidence and the value is deliberately not
quoted — `steps_hash` folds `start_url`, the fixture server binds an ephemeral port, so the digest
differs every run; a first draft published one as if it were a fact.

Of the scenario's 12 heal-eligible rows, 11 persist a heal, 8 of those move the digest, and one
(`reparent`) declines — so this is a property of the refusal's CAUSE and not of the scenario. (A first
draft said "the other nine move the digest"; it is eight.)

**A larger population is claimed and NOT verified here.** The audit measured that 7 of the persisted
heals leave the next 0-LLM replay still refusing — i.e. by the criterion this entry actually cares about
("the repair does not survive"), the population is 7 and not 3, and 4 of those 7 DO move the digest. That
would mean the digest is the wrong proxy for the harm. It is recorded rather than adopted because it was
not reproduced independently, and it needs a replay-after-heal experiment the bench does not currently
run. **Do not quote the 7 until someone measures it.** What IS pinned is the 3, because those are the
rows the `heal_invalidates_approval` invariant has to be told about.

**What is NOT claimed.** This is not a trust defect and the `heal_invalidates_approval` invariant is not
violated in spirit: the heal did not pick a different element — it picked the same one — so there is
nothing for the approval gate to re-gate. The invariant's predicate (`all recovered rows moved the
digest`) simply cannot express "the repair was a no-op", which is why the three rows are named in
`KNOWN_NO_DIGEST_MOVE` rather than the predicate being loosened. `tests/test_drift_bench.py` pins that
set by EQUALITY in both directions, so a row joining it needs a finding and a row leaving it — which is
what closing R3.7 does — forces the entry to be deleted.

**UPDATE 0.103.0 — NO LONGER OBSERVABLE, AND THAT IS NOT THE SAME AS FIXED.** Closing R3.7 removed the
refusals these heals were repairing: all three rows now survive at 0-LLM and never reach the heal, so
the corpus contains no instance of the phenomenon and `KNOWN_NO_DIGEST_MOVE` was deleted (its equality
pin is what forced the deletion rather than leaving an over-broad allowlist behind — that is the
direction such lists usually rot in). The MECHANISM is untouched: a heal that re-grounds to a
byte-identical recipe still reports a persisted repair that changes nothing. It is now a finding with no
reproducer, which is exactly the state that becomes wallpaper, so it stays OPEN and says so.

**The consequence worth stating:** `MECHANISM heal` counted these three as recoveries, so the published
heal rate is optimistic by exactly the population whose refusal cause is not in the spec. Whether the
ladder should DECLINE a re-ground that changes nothing is a real question and is not settled here —
declining it would make the row honest but would also mean a heal that reaches the goal reports failure.

## R4.34 — OPEN, HIGH, inviolable #3. A shared `aria-label` on a nested per-row container turns the row guard OFF, and a wrong-row bind then goes through silently

*high, lens `rowguard`, inviolable #3, reproduced against 0.99.0 and 0.100.0*

**Where.** `src/ultracua/locators.py` — `_climb`'s aria-label branch (it is checked BEFORE row-ness at
every hop) in combination with `resolve`'s gate `if loc is None or not (spec.anchor_source == "row" and
spec.anchor_id)`.

**Mechanism.** The capture walk returns at the FIRST enclosing landmark that can anchor, checking
aria-label, then heading, then row-ness. A nested per-row action container carrying a design-system
label — `<li aria-label="Row actions">`, `"Item actions"`, `"More options"` — is a landmark with an
aria-label, so capture returns `source='label'` at the INNER container and never reaches the row.
`anchor_id` is therefore never captured, and the row guard, which is scoped to `anchor_source == "row"`,
does not run at all. The exemption itself is deliberate and documented in `_resolve` — a renamed SECTION
heading is cosmetic drift the resolver is required to survive — but nothing considered that the same
exemption reaches ROWS through a nested container. This register's most-repeated shape: a guard that
exists on one path and was never applied to a sibling that reaches the same mechanism.

**This is strictly worse than R3.7 and is its root cause pointed the other way.** R3.7 fails LOUD — the
two walks disagree and a correct bind is refused. Here the walk never reaches the row, so there is
nothing to disagree with and the bind is simply unguarded.

**Failure.** Two pending orders in an ordinary table, each row's actions in
`<ul class="actions"><li aria-label="Row actions">`. Learn against order 3. Order 3 is later cancelled
and renders a `Cancelled` badge instead of its button — R1's own shape. Replay binds order 7's Cancel
via Tier 1 `role+name`, uniquely and outright, with no `row_mismatch` and nothing logged. Measured:
`POST /cancel/7` where `/cancel/3` was recorded. The mutation gate cannot object, because per-row forms
are structurally identical and `scope_fingerprint` matches byte-for-byte — so on a write flow this fires
another customer's commit under the recorded row's Idempotency-Key.

**A FIX WAS BUILT IN 0.103.0 AND REVERTED** — it closed this and R3.7 together and produced a silent
wrong-record bind by a third route. See R3.7's entry for the measurement and for what D5 now requires of
attempt 3. **R4.37 is the third door into this same fault** and any next attempt must dispose of all
three, because a fix for one that ignores the others has now failed twice.

The original filing follows. The candidate remedy — capture `anchor_id` from the enclosing row whatever the anchor
source, and scope the guard on `anchor_id` alone — changes what capture MEANS and re-arms a guard on a
population that has never had it, which is exactly the class of resolver trade `drift_bench` exists to
adjudicate. It is not a comment-sized change and does not belong bundled into the slice that found it.


## ⚠️ NARROWED in 0.105.0, STILL OPEN — R4.36, HIGH, inviolable #3. Under CI load, `record()` CACHED an autosave write flow that the unattributed-write rule must refuse

*high, lens `write-safety`, inviolable #3, observed on CI and then reproduced 4/4 deterministically*

**⚠️ NARROWED in 0.105.0 and STILL OPEN. The direct-`fetch` shape is closed; the mainstream
`requestSubmit()` idiom is not — see R4.38, filed by this slice's own audit. What follows
described the change as a fix; it is a strict narrowing, and the difference matters because a
closed finding stops being looked at.**

**It is R4.26's residual rather than a new mechanism.** R4.26 established that a
timer is not a turn boundary and added `inDispatch()` — but it left the boundary a timer and made the new
signal a CONJUNCT. `inDispatch()` answers "is SOME dispatch in progress", which closes the case where a
deferred write leaves in a BARE task and nothing else. When the `setTimeout(..., 0)` reset is overdue and
a LATER, UNRELATED dispatch is running, both conjuncts are true and the write is credited to a commit
that did not cause it.

**Reproduced deterministically — no artificial load and no retries**, which is what the entry's own next
step asked for. A benign `Details` click whose handler busy-waits (so its reset is overdue), one real
keystroke queued during that block into an autosaving field. Chromium serves input tasks ahead of overdue
timers — the same ordering R4.26 measured — so the write leaves inside the `input` dispatch with the
click's turn still open. **4/4 against 0.104.0.**

**And it answers the severity question this entry left open.** The cached recipe's ONLY step is the benign
click, carrying `mutating=True`, a `precond_scope`, and `mutating_sources=['wire']`: not "cached ungated"
but **gated onto the wrong step**, with the demonstrated write absent from the recipe entirely. That is
R3.2's harm class. The matrix cell shows the same shape from the other side —
`steps=[('click Commit', False), ('click Next', True)]`, the real commit ungated and the benign click
gated.

**ONE KEYSTROKE IS LOAD-BEARING and cost the first three attempts at this harness.** With three
characters the second and third writes land after the reset, go unattributed, and `record` refuses for a
DIFFERENT write — the flow is correctly refused, the misattribution is still there, and a test built that
way passes while proving nothing. The shipped test pins `saves == 1` as a premise for exactly this reason.

**THE FIX: ask WHICH dispatch, not WHETHER.** The turn belongs to the commit's own event, captured at
commit time (`__ucev`). A write is in that turn if the current dispatch IS that event — which holds
through its microtask continuations, since `window.event` is restored only when the dispatch ends — or if
the current dispatch is one the activation itself CAUSED. That whitelist is closed by the platform rather
than by taste: `submit`, `reset`, `formdata`. So a native submit button, `requestSubmit()` and
Enter-submit stay attributable, and `form.submit()` fires no event at all so it remains the commit's own
dispatch.

`input` and `change` are deliberately excluded. They are caused by the user editing a field, not by the
activation — that is R4.36 — and `change` additionally fires on BLUR, arbitrarily far from any commit.
**The cost is real and accepted:** a page that writes from a `change` handler after a checkbox click is
now REFUSED rather than attributed. Fail-loud, in `record()`, where a human reads the refusal — the
direction this register requires when a cause cannot be proven.

**The instrument had the defect encoded in it, which is why the matrix could not have caught this.**
`tests/test_write_safety_invariants.py` asserted its per-cell premise as
`inDispatch is (timing not in _MUST_REFUSE)` — "a refused write is one that left OUTSIDE a dispatch" —
and that equivalence IS the belief R4.36 walks through. Every REFUSED cell it had defers into a bare
task. Shape and verdict are now declared independently (`_IN_DISPATCH` beside `_MUST_REFUSE`), so a cell
can exist for every combination of the two, and the naive-`window.event` premise is stated as the
invariant it actually is — the naive read agrees with the native one except where the page fakes it —
rather than derived from the verdict.

Both regression tests verified RED against 0.104.0: the field-shaped one in
`tests/test_turn_boundary.py`, and the decision-point cell
`dispatch_of_a_later_noncommit_event` in the matrix. `drift_bench` invariants all hold with
`writes double=0 suppressed=0 wrong_target=0` — required here because this changes what runs IN the page.

**THE RESIDUAL SENTENCE THAT WAS HERE WAS FALSE, and the audit caught it.** It said "a write deferred
into a bare task still cannot be attributed at all, by design". A bare task that calls
`form.requestSubmit()` — or merely `div.dispatchEvent(new Event('reset'))` — puts the write inside a
dispatch the whitelist accepts by NAME, and attribution resumes. Under the narrowest reading ("no
dispatch is running at the instant of the write") the sentence survives; under the reading an engineer
would rely on ("deferring into a bare task cannot recover attribution") it is false. Measured: the
entire difference between refused and attributed is one string, `uc-fake` vs `reset`. See R4.38.

**What DID move:** a write leaving inside a non-whitelisted later dispatch — the direct-`fetch` autosave
— is now refused, 4/4.


The original filing follows.

**Where.** `tests/test_record.py::test_record_write_flow_type_autosave_is_refused` (the assertion
`res.cached is False and "single" in res.note`), guarding the refusal in `record()`'s write-attribution
path.

**What happened.** PR #152, run 31677764463, windows-latest 2/2, 07:42:59:

    FAILED tests/test_record.py::test_record_write_flow_type_autosave_is_refused
      - AssertionError: assert (True is False)

`res.cached` was **True**. The fixture's autosave POST fires in a commitless turn — a `type` is not a
commit — so the write is UNATTRIBUTED and the flow must be refused, uncached. Instead it cached. The
test's own comment states the harm the refusal exists to prevent: *"never gating the benign Details
click or caching the write ungated"*.

**Why this is filed HIGH rather than as a flake.** A cached write flow is a flow an unattended
`mode="auto"` loop will replay. If the write was cached ungated, or gated onto the wrong step, replay
re-fires a commit — R3.2's harm class, and exactly what R4.26 was about on this same `record` path. The
direction of the miss matters: an over-refusal is loud and recoverable, a MISSED refusal is silent and
arms a replay.

**What is NOT claimed.** The cached flow's contents were not inspected — the CI log carries the
assertion, not the recipe — so it is not established whether the write was cached ungated, gated onto
the benign step, or gated correctly with the note merely absent. That distinction decides the severity
and it is the first thing a repro must answer.

**Separated from R4.22 deliberately.** The same run's other shard failed with
`net::ERR_NO_BUFFER_SPACE`, and it would be easy to file both as "CI was unhappy". The evidence points
the other way: the resource samples for THIS shard are mid-range on every axis at the moment of failure
(`time_wait` 130, `handles` 49,309, `free_mb` 12,722, `nonpaged_pool_mb` 233.4 against a run max of
233.8), and the failure is an assertion rather than a socket error. Whatever this is, it is not the
resource exhaustion R4.22 describes.

**Load-dependent, and the family is known.** 10/10 passes standalone at ~1.6 s each on the developer
host. The register has been here before: R4.25/R4.26 (0.87.0–0.88.0) found that a load-dependent
write-refusal failure was NOT flakiness but the recorder crediting a deferred write to the next click,
and the fix was measured at 0 failures in 25 loaded runs of the four load-dependent write-refusal tests.
**This test is not one of those four.** So either the same class survives on a path the S17 work did not
cover, or there is a second mechanism.

**The rule this file already wrote for exactly this situation** (S17's bullet in the plan): do not
de-flake it, do not add reruns, do not weaken the production bound. Reproduce under artificial load
first — and note R4.26's harder lesson, that instrumenting the mechanism SUPPRESSED it (0 in 150 loaded
runs with an in-page probe), so the answer was a deterministic harness built on demand rather than a
heavier instrument and more patience.

**Next step, in order.** (1) Reproduce under artificial load using the harness S17 built. (2) If it
reproduces, capture the CACHED FLOW, not just the boolean — whether the write step is gated, and onto
which step, is the whole severity question. (3) Only then decide whether this is R4.26's mechanism on an
uncovered path.
## R4.37 — OPEN, HIGH, inviolable #3. A control whose nested wrapper owns no identity captures no `anchor_id`, so the row guard silently does not run — and the record key is one landmark up

*high, lens `rowguard`, inviolable #3, reproduced against 0.101.0/main*

**Where.** `src/ultracua/locators.py` — `anchorOf`'s row branch takes `rowIdOf(c)` from the container
that supplied the anchor TEXT, combined with `resolve`'s gate `spec.anchor_source == "row" and
spec.anchor_id`.

**Mechanism.** The anchor walk stops at the first row-like container with non-empty collapsed text. For a
control with VISIBLE TEXT inside a nested action wrapper (`tr > td > ul.actions > li > button`), that
container is the inner `<li>` — whose text is the control's own label. If the wrapper owns no identity
(no `form[action]`, no `a[href]`, no hidden input, no `data-*`), `rowIdOf` honestly returns null, so
`anchor_id` is None and the guard never runs. The `<tr>`'s `data-order-id` — the actual record key, one
landmark up — is never consulted.

**Measured against main**, two rows, record 3 recorded, replayed after record 3 was cancelled (the shape
`resolve`'s own docstring names):

    MAIN  icon-only  src='row'  anchor_id='data-order-id:3'  -> REFUSED
    MAIN  text-ctrl  src='row'  anchor_id=None               -> bound record 7   by='role+name'

The two halves differ ONLY in whether the control has visible text. The icon-only variant is safe by
accident: its wrapper collapses to the empty string, so the anchor walk climbs past it to the `<tr>` and
picks up the real key. That accident is also R3.7 — the same climb that saves this case is the one that
makes capture and bind disagree elsewhere.

**Relationship to R3.7 and R4.34.** All three are the same underlying fault: `anchor_id` is decided by
where the anchor TEXT came from, which has nothing to do with containment. R4.34 is the aria-label door
into it, R4.37 is the visible-text door, and R3.7 is what the climb that avoids R4.37 costs elsewhere.
**A fix for one that does not consider the other two has now failed twice** — see R3.7's entry.

**Found by** the pre-merge audit of R3.7's attempt 2, as a by-product of the population analysis that
killed it. It is NOT introduced by that attempt: it reproduces on main, and the attempt's own failure
was the icon-only twin of this shape.

**Pinned** by `test_a_control_in_an_identityless_wrapper_is_still_guarded_by_its_row` (strict xfail) and,
in the safe direction, by `bare-nest/icon` in the containment matrix — which is green on main and was
the cell that caught attempt 2.

**Why the corpus never saw it, and this is the reusable lesson.** Every row shape in the suite and all
185 `drift_bench` rows put an identity INSIDE the nested wrapper: the matrix formats a
`<form action=...>` into the inner `<li>`, `row-nested-action` and `row-nested-icon` put an
`<a href=...>` there. A wrapper that owns nothing was in no instrument. Two fix attempts, a 30-cell test
matrix and a 185-row corpus were all blind to the same gap because they were built from the same mental
image of what a row looks like.


## R4.38 — OPEN, HIGH, inviolable #3. `ACTIVATION_CAUSED` matches an event's TYPE NAME with no provenance check, so a write laundered through a synthetic `submit`/`reset`/`formdata` dispatch is credited to the last commit

*high, lens `write-safety`, inviolable #3, reproduced 4/4 against 0.105.0 and identically against 0.104.0*

**Where.** `src/ultracua/recorder.py` — `attributedSeq`'s second arm,
`ACTIVATION_CAUSED[cur.type]`, where `ACTIVATION_CAUSED = { submit: 1, reset: 1, formdata: 1 }`.

**Mechanism.** R4.36's fix replaced "is SOME dispatch running" with "is it the commit's dispatch, or one
the activation CAUSED". The second half is answered by a dict lookup on the event's type NAME. It asks
nothing about the event's target, its provenance, or whether the commit's activation had any part in
creating it — so ANY dispatch named `submit`, `reset` or `formdata` re-opens attribution while
`__ucturn === 1`. The turn is still closed by `setTimeout(..., 0)`, so R4.36's overdue-reset window is
unchanged.

**Two consequences, both measured.**

1. **R4.36's own field shape survives for the mainstream idiom.** Take the harness in
   `tests/test_turn_boundary.py` and change one thing — the autosaving field calls
   `form.requestSubmit()` instead of `fetch` — and the write lands inside a `submit` dispatch the
   whitelist accepts. That is the Turbo/Rails/Stimulus auto-submit pattern, not a contrivance.
   Measured 4/4: `cached=True`, the recipe's ONLY step the benign `Details` click carrying
   `mutating=True`, a `precond_scope` and `mutating_sources=['wire']`, with the demonstrated write
   absent from the recipe entirely.

       shape=fetch   saves=1 cached=False   refused (R4.36's fix working)
       shape=submit  saves=1 cached=True    step 0 'click Details' mutating=True sources=['wire']

2. **A BARE TASK can re-enter attribution**, which is the state R4.26's `inDispatch()` exists to refuse.
   A `setTimeout(() => form.requestSubmit(), N)` debounce — or literally
   `div.dispatchEvent(new Event('reset'))` on a plain `<div>` with no form and no activation anywhere —
   puts the write inside an accepted dispatch. The audit's A/B isolated it to the string: same page,
   same bare task, same `dispatchEvent`, same write, and only the event's NAME differs —
   `submit`/`reset`/`formdata` cache, `uc-fake` refuses.

**NOT A REGRESSION, and this is load-bearing for how to treat it.** The 0.105.0 predicate is strictly
narrower than 0.104.0's: `OLD = __ucturn===1 && cur`, `NEW = __ucturn===1 && cur && __ucev && (cur ===
__ucev || WL[cur.type])`, so `NEW ⊆ OLD` and no misattribution the fix *creates* is possible. Both
probes cache identically on 0.104.0. R4.36's narrowing is a real improvement that does not reach this
shape.

**Why the instrument missed it, again.** `tests/test_turn_boundary.py` and all six record-path matrix
cells stay green throughout — every one of them writes with `fetch`. One idiom was not in the shape set,
which is the third time in this register's recent history that a fix and its instrument shared a blind
spot because the same person built both from the same mental image.

**What a fix has to answer, and why it is not a one-line whitelist edit.** Deleting the whitelist
re-breaks native submit buttons, `requestSubmit()` and Enter-submit — all legitimately activation-caused,
all currently attributable, and all shapes `tests/test_write_safety_invariants.py` covers. The real
question is PROVENANCE: was this `submit` dispatched *within* the commit's own dispatch? That is the same
"which task / which dispatch nesting" question the turn boundary has failed to answer twice
(R4.26, R4.36), so the next attempt should be treated as the third strike on that mechanism and read D5
first.

**Pinned** by `test_a_write_laundered_through_requestsubmit_is_not_credited_either` (strict xfail).

## R4.39 — OPEN, HIGH, inviolable #3. The Idempotency-Key that rides the wire is whichever step was mid-act when the request was ISSUED, so a deferred write's key moves with PAGE TIMING and a retry cannot dedupe

*high, lens `write-safety`, inviolable #3, measured at 0.106.0. Found by REPRODUCING R3.12 — it is the
same "whichever step is mid-act" fact, one path over, and it is what refuted R3.12's stated mitigation.*

**Where.** `src/ultracua/flow.py:1325` (`set_transient_headers({"Idempotency-Key": key})` in the mutation
gate) and `:1457` (`set_transient_headers({})` in the `finally`), against the contract stated in
`src/ultracua/flows.py:_plan_idempotency_keys`.

**The contract, quoted.** `_plan_idempotency_keys` documents the preview as "computed with the SAME four
inputs as `flow._replay_step` (scope, idx, intent, slot_values) so a dry-run preview is **byte-identical
to the wire key**". All four inputs are RECIPE-side. The key is `sha256(scope|step_index|intent)`, and
`safety.idempotency_key`'s own docstring calls it "run-INVARIANT so a retry of the SAME write dedupes".

**Mechanism.** The key is not attached to a request; it is attached to the browser CONTEXT for the
duration of one step's act and removed in that step's `finally`. So the key a request carries is decided
by WHEN it leaves, not by what caused it. A write the page defers past `write_settle_ms` (1000 ms) leaves
after its own step restored the base headers — under the next step's key, or under none.

**Measured: ONE cached recipe, TWO replays, and only the SERVER's debounce changed between them.**
Nothing recipe-side moved — same cache, same steps, same scope, same intents.

    predicted   step 0 'send the invite'   uca-7e04e8cb7cd4b95bc0ddcb51
                step 1 'publish the order' uca-7ac0e3c18b6bd47af1c4af6e

    run 1  page debounce   50 ms   POST /invite  key=uca-7e04e8cb…   == step 0's key
    run 2  page debounce 1500 ms   POST /invite  key=uca-7ac0e3c1…   == step 1's key

Step 0's write carried DIFFERENT keys across two runs of one recipe. A retry mints a different key from
the attempt it is retrying, so the backend dedupe this key exists for cannot fire — **a double-submit,
and the direction of error the register rates worst** (a missed arm re-fires under the same key; this is
the shape where it does not).

**Three consequences, separated because they need different remedies.**

1. **Retry dedupe is void for a deferred write.** Measured above. Needs no page change to be wrong —
   only for the debounce to sit either side of `write_settle_ms` between the run and its retry. R4.26
   measured renderer starvation moving exactly these timings by hundreds of ms; that this can be
   *induced by load alone* is a CONSEQUENCE, not something reproduced here — the measurement above
   changes the page, not the host.
2. **`preflight_keys` is not the preview it says it is.** The MCP write surface uses it to check the
   dedupe ledger BEFORE actuating. An operator pre-registering the predicted key registers one the write
   will not carry.
3. **Two distinct writes can share one key** (step N's own write and step N-1's deferred write, both
   issued during N's act), which is the *suppressed*-write direction. Structurally reachable; **not
   reproduced** — every construction tried either lost the race or ended the run first, and it is filed
   as unproven rather than counted.

**NOT the same finding as R3.12, and the distinction is the point.** R3.12 is what the dry-run REPORT
claims; it is closed by refusing to claim. R4.39 is what the browser SENDS on a real replay, where
refusing to claim changes nothing — the request still leaves with some key. This is why R3.12's fix is
not extended to cover it.

**What a fix must not be.** "Leave the key on the context for longer" re-keys a genuinely later write
with an earlier step's key, which is the same defect pointing the other way. "Key every request in the
run identically" collapses distinct writes onto one key — direction 3, deliberately. The key has to
travel with the REQUEST rather than with the clock, which is a different mechanism from the one that
exists; and note that deciding which key a deferred request should carry is D5's blocked question
wearing a header. A fix that only ever REFUSES (fail loud when a write leaves outside its own step's
act, rather than key it wrong) is available and is not blocked — cost unmeasured.

**Pinned** by `tests/test_dryrun_attribution.py::test_the_wire_key_is_a_function_of_the_recipe_not_of_page_timing`
(strict xfail).

---

## R4.40 — `_learn` authors against an unrendered page, and says nothing

**Severity: MEDIUM** (availability + diagnosability; one variant persists state). Not an inviolable
violation — the default direction is fail-safe, nothing is cached and nothing wrong is actuated. Filed
because the failure is **quiet**, because it is guaranteed on client-rendered apps rather than rare, and
because one measured variant converts a rendering race into a **persistent refusal a human must clear**.

**Found** while running Gate-0 of `docs/realistic-benchmark-plan.md` against a real Odoo 17 instance —
i.e. by pointing the engine at an ordinary SPA, not by reading code.

### The mechanism

`run_cached` navigates and then snapshots with nothing in between: the decide loop's first act is
`obs = await session.snapshot()` (`flow.py:402`), and the only hook that could wait is the caller's
optional `prepare` (`flow.py:812`). There is **no floor on what counts as an observation worth authoring
from.** On a page whose content is client-rendered, the first snapshot is the skeleton.

Measured on Odoo's Sales-Orders list: the first observation held **5 elements**; once the list renders it
holds **80** (of ~163 candidate interactables — the cap is a separate matter). With the target absent,
the run proceeds:

1. the provider cannot find the target and returns `ref=None`;
2. `session.act` runs `page.click('[data-ultracua-ref="None"]')` (`browser.py:263`), which matches
   nothing and times out, so `ok=False` (`flow.py:504`);
3. steps are appended only `if ok:` (`flow.py:523`), so **no `CachedStep` is recorded**;
4. `if success and steps:` (`flow.py:880`) never opens, `cached_here` stays False (`flow.py:848`), and
   `extra["cached"]` is False (`flow.py:904`).

### Why it is filed as quiet rather than merely unlucky

The learn **reported `success=True` with nothing cached**, and the later replay said only
`no cached flow for key` — which reads as *"this flow was never learned"* rather than *"this flow was
learned against a page that had not rendered"*. An operator following that message investigates the
wrong thing. Note also which key `extra` did **not** contain: `"verify"`. It is set on both branches
inside the `success and steps` gate, so its absence is the only available evidence that the gate never
opened — a diagnostic that exists by accident and is documented nowhere.

### The variant that persists

One bare run instead tripped `write_unattributed=True` — Odoo's background RPC traffic with no
successful step to attribute it to — taking the refusal at `flow.py:863`, which sets `success=False`
**and calls `cache.remember_refusal(...)`**. That marker is terminal until a human runs `flow release`.
So a transient rendering race can leave sticky state behind, not just an un-cached flow. Measured 1 of 1
in the bare configuration and **0 of 4** once the page was allowed to render, which is consistent with
it being a race rather than deterministic — the rate is not otherwise characterised.

### Confirmed by removing the cause

A `prepare` hook waiting for the list before the loop:

| arm | elements | target | `cached` | replay |
|---|---:|---|---|---|
| as found | 5 | `ref=None` | False | `mode=miss` |
| + wait for render | 80 | `ref=e29` | **True** | `mode=replay`, `success=True`, `llm_calls=0` |

Four reps with the hook: cached 4/4, replay 4/4 at 0 LLM calls, `write_unattributed` 0/4.

### The direction that is NOT measured, and matters more than the one that is

Every measurement above used a `ScriptedProvider`, which returns `ref=None` when its target is missing —
so it fails closed **by construction**. A real provider does not have that property: handed a 5-element
skeleton it will author against *whatever is present* (a spinner, the nav chrome), and the resulting
recipe could plausibly survive verify-by-replay, because the replay races the same way the learn did.
That path would cache a **wrong recipe** rather than none, and is the reason this is filed as a defect in
`_learn` rather than as a note in the benchmark plan. **It is unproven** — no LLM arm has been run — and
it should be the first thing any fix attempt reproduces, because if it is real the severity is higher
than MEDIUM and the fail-safe framing above is wrong.

### What a fix must not be

* **"Wait for `networkidle`."** Measured on this same substrate: Odoo holds a long-poll open, so
  `networkidle` never fires. The engine already waits on it in four places (`flow.py:1135`,
  `flows.py:1008`, `flows.py:1072`, `flows.py:1144`), all inside `try/except`, so each burns its full
  timeout (8–10 s) and proceeds. Adding a fifth would add dead wall-clock to every learn and fix nothing.
* **"Sleep after navigate."** A timer is not a boundary — R4.26's lesson, one subsystem over. It trades a
  guaranteed failure for a load-dependent one and makes the remaining failures rarer and therefore
  harder to catch, which this register rates as worse.
* **"Tell callers to pass `prepare`."** The hook already exists and works; the defect is that the default
  path has no floor and no diagnostic. A fix that only documents the hook leaves the quiet failure and
  the persistable refusal exactly where they are.

The available shapes that are not blocked: refuse to author when the first observation is implausibly
small **and say so** (a floor is a guess, but a LOUD guess, and this register prefers that direction);
or settle on a page-derived signal rather than a clock. Cost unmeasured for both.

**Not pinned by a test.** No regression test is added with this entry — the reproducer needs a
client-rendered fixture the suite does not have, and the un-measured LLM direction above would change
what the test should assert. Filing ahead of the fix deliberately.

---

## R4.41 — the SDK choke-point pin allowlists a module that is not on the choke point

**Severity: MEDIUM** (test-integrity; no inviolable violated *today*). Filed because the guard states an
inference its own allowlist breaks, and it is the same shape as the defect the guard was written to
close.

**Found** while auditing cost instrumentation for the benchmark plan — i.e. by asking "where can an LLM
call originate", not by reading the test.

### What the pin claims, and where it stops being true

`tests/test_inviolable_properties.py::test_llm_client_construction_has_a_single_choke_point` walks the
AST of everything under `src/ultracua`, forbids direct construction of `AsyncAnthropic` / `AsyncOpenAI` /
`OpenAI` / `GenerativeModel`, and exempts `_SDK_ALLOWED`. Its docstring states the reasoning plainly:

> "no module outside the LLM leaf adapters may construct an SDK client directly, so all construction
> funnels through `llm.build_client`, which the fixture does patch."

That inference is sound for the three entries it was written for — `llm/anthropic.py`, `llm/openai.py`,
`llm/gemini.py` are precisely the leaves `build_client` **dispatches to**, so exempting them does not
widen what is reachable without going through it.

`src/ultracua/vision.py` is the fourth entry (`tests/test_inviolable_properties.py:150`) and is **not**
such a leaf. It constructs its own client at `vision.py:66-68`:

```python
from anthropic import AsyncAnthropic
...
self._client = AsyncAnthropic()
```

and never calls `build_client`. So the allowlist does not merely exempt a module from the scan — it
**removes the only evidence for the claim that `build_client` is the choke point**, for that module. A
`no_llm`-style fixture that patches `build_client` (and `flows.build_router` / `providers.build_router`,
the other two names the file checks) would **not** intercept a vision call.

### Why it is filed, and why it is not filed as HIGH

Reachability today is narrow. `_vision_decide` is called from the decide loop
(`flow.py:408-409`, when `not obs.elements and grounding is not None`) and from the heal path — all of
which are learn/recovery, i.e. paths where an LLM call is expected and inviolable #1 ("replay never
calls an LLM") is not in force. A plain replay does not reach it. **No violation is claimed, and none
was observed.**

What is wrong is the *guarantee*. This file's own history is the argument: S14 added the AST scan
precisely because a hand-written `_FACTORIES` list had let a replay construct **105 real Anthropic
clients** with every cell green. The scan was the fix that "closes the class instead of lengthening a
list" — and an allowlist entry for a module that bypasses the choke point lengthens a list again,
inside the guard that replaced it. A future slice that gives `grounding` a path into a replay-side
rung would find the guard already silently not covering it.

### Two dispositions, and the cheap one is not the right one

* **Cheap and wrong:** delete `vision.py` from `_SDK_ALLOWED`. The scan goes red and the only way to
  green it is to route vision through `build_client` — which may be correct (see below) but is a
  behaviour change smuggled in as a test edit.
* **Right:** decide whether vision *should* be a `build_client` consumer. It probably should — it is a
  second Anthropic client with its own configuration, and routing it through the factory would also
  close the cost blind spot that surfaced beside this (vision spend never reaches `router.totals`, so a
  vision-tier run reports `cost_usd` 0). If it should not, then the allowlist needs a comment saying
  *why* this entry does not undermine the docstring — and the docstring needs to stop claiming what it
  currently claims.

Either way the invariant to restore is the one the test already names: **every SDK client construction
is reachable from one patchable point**, and the allowlist may only contain modules for which that is
true.

**Not pinned by a new test.** The existing test passes and will keep passing; the defect is in what it
proves, not in whether it runs. A regression test belongs with whichever disposition is chosen.


## R4.42 — `flow release` cannot clear a learn-time refusal, so R3.13's remedy is dead through the operator's only surface

**Severity: MEDIUM** (availability + fail-loud integrity; no inviolable violated — the refusal holds,
which is the safe direction). Filed because the engine prints an instruction that the CLI then refuses
to carry out, and because the obvious one-word fix does **not** work — measured, not reasoned.

**Found** while writing `docs/reshape-plan.md`, by following the operator's path from the refusal
message rather than from the code that emits it.

### The loop

`flow.py:204-211` refuses to re-author a flow whose learn was refused, and tells the operator:

> "Clear it with `flow release` once the cause is addressed"

`flows.release()` (`flows.py:1646-1690`) does exactly that: R3.13's fix made it THE human act, and it
clears both holds — the `FlowMeta` quarantine and the engine's refusal memory (`_clear_refusal`, which
calls `FlowCache.forget_refusal`). The API is correct.

`cli._flow_release` (`cli.py:646-655`) never reaches it:

```python
h = health(spec)
rebaseline = getattr(args, "rebaseline", False)
if h.status != "quarantined" and not rebaseline:
    print(f"{spec.name!r} is not quarantined (status: {h.status}) — nothing to release")
    return
release(spec, rebaseline=rebaseline)     # cli.py:655 — unreachable for a refusal
```

A refusal is not a quarantine, so the guard returns first. The operator is told to run a verb that
reports "nothing to release" and leaves the hold in place. `flow record` still works (the message says
so), so the flow is not stuck — but the named remedy does nothing, and nothing says so.

### Reproduced, both shapes, and the second one is the reason this entry exists

Driven through `cli._flow_release` via `argparse.Namespace`, against a temp `ULTRACUA_HOME`:

| shape | `health.status` | CLI printed | refusal after CLI | after `release()` |
|---|---|---|---|---|
| A — learn-refused, **not** cached | `refused` | "is not quarantined (status: refused) — nothing to release" | **still held** | cleared |
| B — refused **with** a cached recipe | `never-run` | "is not quarantined (status: never-run) — nothing to release" | **still held** | cleared |

Shape B is the one that matters. `health()` reports `refused` only when the flow is **not** cached
(`flows.py:2068`, `if not cached and refused is not None`), so a refusal recorded against a flow that
already has a recipe — the `mode="auto"` fall-through and `on_drift="relearn"` shapes, where
`remember_refusal` does not delete the recipe — reports something else entirely.

### The mitigation was reproduced too, and it fails

The obvious fix is to widen the pre-check to `h.status not in ("quarantined", "refused")`. Measured
against the table above: it reaches shape A and **does not reach shape B**, whose status is
`never-run`. A status-based pre-check cannot cover a hold that `health()` does not always name.

*(An earlier draft of this entry predicted shape B would report `failing` or `healthy`. It reports
`never-run`. The conclusion held; the predicted value did not — which is the argument for reproducing
the mitigation rather than reasoning about it, R3.12's lesson one surface over.)*

### Fix shape — a hypothesis, per this file's standing caution

**Delete the pre-check** rather than widen it, and let `release()` — which already knows about both
holds and about neither being present — decide and report what it cleared. That makes the CLI a
renderer of the API's answer instead of a second, partial copy of the API's knowledge, which is the
wrapper-not-mechanism shape this register keeps paying for (A7, R4.10, R3.13's own design constraint).

The `rebaseline` arm must keep working when nothing is held: `release()` already handles that
(`flows.py:1674-1677`, the `meta.quarantine is None` branch resets history when asked).

**RED test required for BOTH shapes**, driven through argparse rather than by calling `release()`
directly — a test that calls the API cannot fail for this defect, which is precisely why the suite
does not.

---

### FIXED at 0.109.0 — reshape-plan step 1.2

The pre-check at `cli.py:652` is **deleted**, not widened, and `release()` now returns a
`ReleaseResult` naming what it actually cleared (`quarantine` / `refusal` / `baseline`). The CLI reports
that instead of inferring it from a status.

**Widening was refused with a third reason, found while writing the RED cells.** The finding already
recorded that `health()` reports `"refused"` only for an UNCACHED flow, so a refusal beside a cached
recipe escapes a widened check. Reproducing it turned up one more: the status ladder tests `not cached`
**before** `meta.quarantine is not None`, so a genuinely quarantined flow whose recipe has been deleted
reads `"not-learned"` and the ORIGINAL check misses it too. Three states, one pre-check, and no
membership test over statuses reaches all three — which is what makes deletion the fix rather than a
better predicate.

**Pinned** by `tests/test_flow_release.py`, eight cells, six of them verified RED against 0.108.0 before
the fix. Two are green both sides on purpose: the quarantine path through the CLI (the behaviour that
already worked, and the thing deleting a guard is most likely to lose) and the partial-release ordering
— `release()`'s own comment says clearing the refusal above a meta write that can raise leaves the
refusal gone while the quarantine stays, so that cell drives the raising path and requires the refusal
to have SURVIVED.

The quiet direction is pinned as hard as the loud one: a release that clears nothing must SAY so.
Without that cell the fix could have been "always print released", which passes every other cell here.

## R4.43 — the SDK choke-point scan does not match `genai.Client()`, so its anti-vacuity floor is met by exactly three constructions and gemini's is invisible

**Severity: MEDIUM** (test-integrity; no inviolable violated today). Distinct from **R4.41** and worse
in one specific way: R4.41 is an allowlist entry that voids the scan's *inference* for one module;
this is a constructor the scan cannot *see at all*, anywhere in `src/`.

**Found** while verifying R4.41's numbers for `docs/reshape-plan.md` — i.e. by counting what the scan
actually matches rather than by reading what it says it matches.

### What the scan misses

`tests/test_inviolable_properties.py:148` enumerates the constructors:

```python
_SDK_CTORS = ("AsyncAnthropic", "AsyncOpenAI", "OpenAI", "GenerativeModel")
```

`src/ultracua/llm/gemini.py:99-101` constructs its client as:

```python
from google import genai
...
self._client = genai.Client()     # reads GEMINI_API_KEY / GOOGLE_API_KEY
```

`Client` is not in the tuple. `GenerativeModel` is the *old* `google-generativeai` entry point; the
adapter uses the current `google-genai` one. So the scan's forbidden-name list does not cover the SDK
this repo actually ships against for that backend.

### Measured, by running the scan's own logic over the tree

| allowed leaf | constructions the scan counts | client-ish calls actually present |
|---|---|---|
| `llm/anthropic.py` | `AsyncAnthropic` @108 | — |
| `llm/openai.py` | `AsyncOpenAI` @114 | — |
| `llm/gemini.py` | **none** | `Client` @101 |
| `vision.py` | `AsyncAnthropic` @75 | — |

`found_in_leaves = 3`, against `assert found_in_leaves >= 3`. Two consequences:

1. **A `genai.Client()` anywhere in `src/` passes the scan.** Verified against a synthetic module: the
   offender list comes back empty. The stated invariant — "every SDK client construction is reachable
   from one patchable point" — is not enforced for that constructor, and a future backend or a helper
   that builds a genai client outside the leaves would be invisible.
2. **R4.41's own "cheap and wrong" disposition now fails for the wrong reason.** Deleting `vision.py`
   from `_SDK_ALLOWED` drops `found_in_leaves` to 2, so the **anti-vacuity assert fires first** and the
   run reports *"the scan is broken"* rather than *"vision.py constructs a client outside the leaves"*.
   The two findings interact: R4.43 makes R4.41's remedy misdiagnose itself.

### Fix shape — a hypothesis

Adding `"Client"` to `_SDK_CTORS` is the instance fix, and it is nearly free — but `Client` is a
generic name and would also match unrelated `X.Client()` calls, so it wants the import context or a
qualified match (`genai.Client`). Whatever is chosen, the anti-vacuity floor must be **derived** rather
than a constant: today's `>= 3` is a hand-typed number that happens to equal the count of constructions
the list can see, so it cannot notice a fourth backend arriving unmatched. Deriving it (one construction
per allowed leaf that declares itself a client owner) is the shape that closes the class — the same move
S14 made when it replaced `_FACTORIES` with this scan, one level up.

**Not pinned by a new test**, for R4.41's reason: the existing test passes and will keep passing. The
pin belongs with the fix.


# The B1 family (R4.44–R4.53) — ten findings in PR #165's own new code

**Filed 2026-08-17 against 0.108.0.** Found by a four-lens adversarial pass over PR #165 (B1, "the run
record") **after it merged**, run while writing `docs/reshape-plan.md`. Thirty-five raw candidates; the
top ten by severity went to independent skeptics briefed to REFUTE them, and **all ten came back
CONFIRMED**, most with the severity corrected downward. **None violates an inviolable.** Every `file:line`
below was re-verified against `main` on the day of filing.

**They are filed as a family because they are one defect wearing ten hats.** `RunRecord` is written at
**10 sites in two functions** — `flows.py:2184-2185, 2197-2199, 2224-2233, 2387, 2843, 2921, 2924,
2980-2981, 2987-2988, 2990` — with three helper mutators (`_absorb_usage` :2097,
`_forget_negative_write_evidence` :2123, `_mark_ok` :2141) each covering a different subset of fields.
Accounting exists at three layers with different lifetimes: a watch inside `flow.py` per `run_cached`
(lost on a raise, because `FlowReport` is a return value), `_absorb_usage` per `replay()`, and an ad-hoc
third watch for the relearn. **Eight of the ten are literally "a record site that was not written."**

**Two facts that make the shape, not the instances, the thing to fix.** Exactly **two** tests in the
whole suite pass `record=` to `replay()` (`tests/test_flows.py:1026`, `:1059`), so eight of those ten
write sites have never been reached by a test. And B1's own in-slice fix F2 wrapped **one** of the four
run-the-engine call sites in `try/except` and left the other three — the register's "guard on a sibling
path" predictor firing inside the PR that cites it.

**Do not fix these one at a time.** Ten patches would be ten more copies of the shape that produced them.
`docs/reshape-plan.md` step 1.5 disposes of them with ONE deletion-heavy change (a single-exit
`_RecordSink` whose `finish()` is total by construction, with `flow.py` untouched), gated behind step 0.3
so the ten exist as strict-xfail cells in a browser-free exit-set matrix BEFORE the sink is written.
R4.52 goes with step 1.4 and R4.53 with step 1.3.

**One consequence reaches past the engine.** Every existing consumer un-makes B1's central guarantee:
`benchmarks/variance.py:47`, `benchmarks/drift_bench.py:651` and `evals/run.py:240` all do
`.get("cost_usd") or 0.0`, collapsing "unknown" back to zero one hop past the record.

| id | what | where |
|---|---|---|
| R4.44 | a raised attempt drops its own LLM spend, traces and minted keys, and leaves `ok`/`failure_code` stale from the previous attempt | `flows.py:2191-2233` |
| R4.45 | `record.usage == {}` on the miss / escalate / precheck / pre-attempt-refusal / raise exits | `flow.py:191-192`, `:1096-1099` |
| R4.46 | a usage-less later attempt flips a priced total to `None` with no reason flag | `flows.py:2097-2120` |
| R4.47 | the FAILED-path cell asserts one default and two truthy values, so population-only-at-success stays green | `tests/test_flows.py:1039-1065` |
| R4.48 | eleven wiring mutations of the record plumbing survive the entire suite | `flows.py:2843/2921/2924/2980/2987/2990` |
| R4.49 | `record.failure_code` speaks a different vocabulary from `FlowReplayError.code` and can name a different attempt | `flows.py:2182-2186` vs `:441` |
| R4.50 | `llm_calls` / `traces` / `healed_steps` / `total_ms` exclude the relearn while `usage` includes it | `flows.py:2971-2988` |
| R4.51 | the headline claim has no end-to-end pin: an engine reporting UNKNOWN on every replay passes every test | `flow.py:1085`, `:1240` |
| R4.52 | `BatchRowResult.landed` is a two-state bool reading `False` on successful write rows and on crashed rows | `flows.py:3562` |
| R4.53 | a key-less teacher is classified as an unobserved SPENDER, and `accounting_failed` is sticky across runs | `obs.py:235-241` |


### FIXED at 0.4a (PR #179) — reshape-plan 0.5's one line, taken early

`Client` joins `_SDK_CTORS`; the anti-vacuity floor rises 3 -> **4**, which is the MEASURED number of
SDK constructions in the allowed leaves (anthropic, openai, gemini, vision) rather than a round one.
`GenerativeModel` stays in the tuple, dead, so a return to the old `google-generativeai` API is still
covered. Armed by constructing `genai.Client()` in `cache.py` — a non-leaf module — and watching the
scan go red.

## R4.44 — a raised attempt drops its own spend, and leaves the previous attempt's verdict standing

**MEDIUM.** `_attempt_replay` stamps `record.attempts += 1; record.mode = "raised"` **before** the engine
runs (`flows.py:2191-2199`, deliberately — M4's fix, so the worst case is "raised, unknown" rather than a
confident denial). But the population block that records usage, traces, `llm_calls` and the minted keys
sits at `:2224-2233`, **after** `run_cached` returns. An exception between them exits above it, so that
attempt's spend and evidence are lost, and `ok` / `failure_code` keep whatever the *previous* attempt
left.

B1's F2 fixed exactly this shape for the relearn leg (`flows.py:2971-2988`, wrapping `learn()` in
`try/except` to absorb the watch before re-raising) and left the three `_attempt_replay` legs unguarded.
**Disposition:** step 1.5 — the wrapper owns the watch and `finish(exc)`, so the raise path stops being
a special case.

## R4.45 — `usage` is absent, not zero, on five exits

**MEDIUM.** `RunRecord`'s docstring says `usage` "is always populated and always carries `cost_usd`"
(`flows.py:206-207`). It is `{}` after the miss exit (`flow.py:191-192` returns a `FlowReport` with no
`extra`), the escalate exit (`:1096-1099`), both idempotency-precheck returns, a pre-attempt refusal, and
any raise. **This is the exact shape B1 was written to remove** — the absent-vs-zero ambiguity that made a
0-LLM replay's cost unfalsifiable — surviving on the paths its own tests did not visit.

## R4.46 — one usage-less attempt makes the whole run unpriceable, silently

**MEDIUM.** `_absorb_usage` (`flows.py:2097-2120`) is sticky-`None` on `cost_usd` by design: one
unpriceable attempt makes the run unpriceable. Correct. But it treats a **missing** key the same as an
unpriceable one, so an attempt that returned a usage-less report (R4.45) poisons a correctly-priced total
to `None` **with no flag saying why** — indistinguishable from a genuine unpriced model.

## R4.47 — the FAILED-path cell cannot fail for what it claims to test

**MEDIUM, test-integrity.** `test_run_record_is_populated_on_a_FAILED_replay`
(`tests/test_flows.py:1039-1065`) asserts `rec.ok is False`, `rec.mode` truthy and `rec.failure_code`
truthy. `ok` defaults to `False` (`flows.py:211`), and the M4 pre-stamp sets `mode="raised"` before the
engine runs — so an engine that populated the record **only on success** would keep this cell green. A
patch (the M4 pre-stamp) landed on top of an existing test and silently removed its discriminating power
without turning it red: the patch-on-patch shape, applied to a test.

## R4.48 — eleven wiring mutations survive the whole suite

**MEDIUM, test-integrity.** Measured on a scratch copy of `src/` on `PYTHONPATH`: eleven separate
mutations of the record plumbing — disarming `_mark_ok` at each of its three call sites, the relearn
absorb, the `auth_refreshed` stamp, the `record.mode` assignments — are invisible to all 1092 tests. The
helpers are individually well covered; the WIRING is not, because only two cells pass `record=` at all.
**Disposition:** step 0.3's matrix kills all eleven before the sink is written, and step 0.6's weekly
sweep keeps them dead.

## R4.49 — two vocabularies for one taxonomy, and they can name different attempts

**LOW.** `_fail` writes the internal `kind` string into `record.failure_code` (`flows.py:2182-2186`) —
`"miss"`, `"drift"`, `"shape"`, `"escalate"`, `"quarantine"`, `"write_unverified"`, `"write_unreadable"`
— while the exception the caller catches carries `_classify_replay_failure(kind).code` (`:441`), a
different vocabulary. They can also describe **different attempts**: `replay()` discards attempt 3's
`_kind3` (`:2961`), so the record may hold attempt 1's kind beside attempt 3's exception.

## R4.50 — the relearn's calls are counted in dollars but not in calls

**LOW.** The relearn absorbs `usage` (`flows.py:2971-2988`) but not `llm_calls`, `traces`,
`healed_steps` or `total_ms`, which are only accumulated in `_attempt_replay`'s population block. A run
that drifted, replanned and relearned can report `llm_calls == 0` beside a `usage` showing dozens of
calls — two fields from two sources disagreeing about one run.

## R4.51 — the headline claim has no end-to-end pin

**LOW, test-integrity.** B1's stated purpose is that a 0-LLM replay SAYS it spent zero rather than
staying silent. Nothing asserts it end to end: an engine that reported `unobserved_llm_path` on **every**
replay would pass every cell in the suite. The property is asserted at the primitive (`RouterWatch`) and
never through `replay()`.

## R4.52 — the two-state boolean the same PR calls a trap, one surface down

**LOW.** B1 made `RunRecord.landed` three-state and wrote a paragraph explaining why ("a two-state
boolean answering a three-state question is the trap this register records shipping three times"), then
added `landed: bool = False` to `BatchRowResult` (`flows.py:3562`) in the same diff and populates it only
on the `FlowReplayError` branch. A row whose replay returned `{"status": "confirmed"}` carries
`landed=False`; a row that crashed after the POST carries `landed=False, code=""`; a resumed row carries
`landed=False`. One field, three meanings — and on the ok row it contradicts `data["status"]` on the same
object. **Disposition:** step 1.4, with its value on an ok-write row stated as a golden cell first.

## R4.53 — a teacher that CANNOT spend is reported as a spender nobody watched

**LOW.** `RouterWatch.__init__` treats any owner without a `UsageTotals` as an unobserved spender
(`obs.py:235-241`), so `MockProvider`, every `ScriptedProvider` and every oracle teacher — the population
the entire key-less suite and `drift_bench` are built from — report `cost_usd: None` +
`unobserved_llm_path: True`. `flow.py:915-916` states the opposite in a comment: "a key-less learn
(scripted teacher, no router) must SAY it spent nothing rather than stay silent about it." Measured:
`UsageTotals.observe(ScriptedProvider([]))` reports cost UNKNOWN. Separately, `accounting_failed` rides
on the `UsageTotals` object rather than the run, so one failed vision accounting poisons every later run
that observes the same router.

**Disposition:** step 1.3, and the fix must use the `totals = UsageTotals()` variant — **never** a new
attribute probe on the owner, because that is commit `00888b4`, which tripped inviolable #1's
`_Exploding` tripwire during B1 itself.


## R4.54 — the key scrub's completeness rests on a hand-written list, which is the shape it was built to escape

**Severity: LOW** (test-integrity; no live exposure measured). Filed because the guard is a list, and
this register's most-repeated finding is that a list is only as good as its worst entry.

**Found** by the adversarial audit of the fast tier (PR #170), while checking claim 6 ("provider keys are
scrubbed for every run").

`tests/_tiers.PROVIDER_KEY_VARS` enumerates the variables to blank. It now covers
`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` —
`ANTHROPIC_AUTH_TOKEN` was added by that audit, because the Anthropic SDK resolves auth from it too, so
leaving it set defeats the very `Could not resolve authentication method` signal that made S8/0.84.0
visible on CI in the first place.

**What is still open is the CLASS.** Bedrock (`AWS_*`) and Vertex (`GOOGLE_APPLICATION_CREDENTIALS`,
`ANTHROPIC_VERTEX_PROJECT_ID`) are not covered, and neither is whatever the next backend reads. The
scrub's completeness is therefore a claim about a list nobody re-derives.

**Fix shape — a hypothesis, and it is the move this repo has already made once.** S14 replaced a
hand-written `_FACTORIES` list with an AST scan proving `build_client` IS the choke point. The analogous
move here is to derive the variable set from what the leaf adapters actually read (`llm/anthropic.py`,
`llm/openai.py`, `llm/gemini.py`, `vision.py`) rather than to keep extending the literal — which also
makes a new backend's variable a compile-time fact rather than something the next audit notices.

**Not pinned.** A pinning test would assert the derived set equals the literal, which is the fix rather
than a pin. Recorded here as a bound, per exit criterion 2, and it should be closed by the derivation
rather than by lengthening the list again.

---

## R4.55 — the key scrub is in-process only, and on win32 an empty value is an ABSENT one

**Severity: LOW** (no live exposure: the only subprocess in the suite reaches no provider). Filed because
the mechanism inverts across a process boundary on the primary development platform, and both cells that
pin it stay green while it does.

**Found** by the same audit, measured rather than reasoned.

`tests/_tiers.scrub_provider_keys` does `os.environ[var] = ""`. In-process that is exactly right, and it
is what makes CLAUDE.md's `ANTHROPIC_API_KEY= uv run pytest` recipe sufficient: `load_dotenv()` does not
override a variable that is already SET, so an empty value is enough.

**Across a process boundary on Windows it is not.** Assigning `""` to an environment variable removes it
from the real process environment block, so a CHILD process sees the variable as **absent**, not empty —
and `ultracua.config`'s own `load_dotenv()` in that child would then populate it from `.env`. The parent's
`os.environ` mapping still reports `''`, so `test_no_provider_key_is_visible_to_the_suite` and
`test_importing_ultracua_config_does_not_repopulate_a_scrubbed_key` both stay green either way.

**Exposure today is nil, and that is the reason this is LOW rather than closed.** The only `subprocess`
call in the whole suite is a `python -c` lock probe in `tests/test_health_lock.py` that reaches no
provider. `scripts/derive_test_tiers.py` and `scripts/check_shard_coverage.py` shell out to pytest, but
they are tools, not collected tests, and the child re-runs the scrub on its own `pytest_configure`.

**Why it is filed anyway.** A future test that shells out — a CLI end-to-end cell, an MCP server driven
as a process, a benchmark invoked from a test — inherits the S8 hazard silently, on the platform this
project develops on, with the existing pins green. That is the shape this register exists to catch
before it costs a release.

**Fix shape — a hypothesis.** Either pass a scrubbed environment explicitly to every child
(`env=` on the subprocess call, which is a per-caller discipline and therefore the wrapper-not-mechanism
shape), or set a sentinel the SDKs treat as present-but-invalid so the variable survives the boundary,
or assert at the boundary that no child inherits a provider key. Measure which before building: the
register's rule is that a fix shape written before the code is read has been wrong 4 of 7 times here.

**Pinned by demonstration, not yet by a test.** The behaviour was reproduced during the audit (a parent
setting `""` and spawning a child that observes the variable absent); a cell asserting it would be
cheap and belongs with whichever fix is chosen.


## R4.56 — a fixture sub-resource silently fails to load, and the obvious cause is REFUTED by measurement

**Severity: LOW** (suite reliability; no product code involved, no inviolable at stake). Filed for the
occurrence and, more usefully, for the two measurements that rule out the fix everyone would reach for.

**The occurrence.** `tests/test_recorder_ceiling.py::test_recorder_cracks_the_garbled_label_ceiling_0llm`
failed one full local run (1102 passed, 10 xfailed, 1 failed) with:

```
playwright._impl._errors.Error: Page.evaluate: ReferenceError: d3 is not defined
    at genProblem (http://127.0.0.1:51364/miniwob/click-option.html:37:13)
    at core.startEpisodeReal (http://127.0.0.1:51364/core/core.js:88:3)
```

It passes in isolation. `d3.v3.min.js` is a **local** 151 KB asset served by the suite's own
`ThreadingHTTPServer` — no network is involved — so one of the page's script tags silently did not load
and the failure surfaced as a JavaScript `ReferenceError` **inside the page** rather than as a connection
error. That is why it reads as a broken test rather than as a resource problem, and it is the reason this
entry exists even though the cause is unknown.

**The obvious hypothesis, and its refutation.** `benchmarks/miniwob_env.py`'s `_QuietHandler` does not set
`protocol_version`, so it serves **HTTP/1.0** and closes the connection after every response, while that
page pulls five sub-resources. `benchmarks/drift_fixtures.py` already carries the HTTP/1.1 fix with a
comment about HTTP/1.0 "burning its own socket" per request, and this register records that **28 of the
suite's 32 fixture servers never got it** — so "socket churn dropped the request" looks like a
one-line fix waiting to happen.

**Measured on the real fixture, five page loads each:**

| protocol | TCP connections | per page load |
|---|---:|---:|
| HTTP/1.0 (today) | 40 | **8.0** |
| HTTP/1.1 | 30 | **6.0** |

**A 25% reduction, not a collapse** — Chromium opens up to six parallel connections per host regardless,
so keep-alive saves two. The protocol version is therefore not the lever the hypothesis needs, which is
consistent with this register's own refutation of socket churn as R4.22's cause at 0.104.0 (1.08
connections/s, 0.8% of the ephemeral range). **The cause of the dropped sub-resource is NOT established.**

**And the sweep would be worse than the disease.** HTTP/1.1 requires a framed body: a handler that writes
a body with neither `Content-Length` nor chunked encoding leaves an HTTP/1.1 client waiting for a close
that never comes — a hang, not a failure. Measured across the suite:

| fixture handlers | count |
|---|---:|
| files defining one | 38 |
| already frame their bodies | 30 |
| **write a body with NO `Content-Length`** | **8** |

The eight are `drift_sandbox.py`, `write_flow_bench.py`, `test_canary.py`, `test_flows.py`,
`test_multiwrite.py`, `test_record_caption.py`, `test_recorder_fidelity.py`, `test_select_locator.py`.
Flipping `protocol_version` across the suite would trade a 25% socket reduction for eight hang
candidates.

**Disposition — the shared fixture server (`docs/reshape-plan.md` step 0.7), NOT a sweep and NOT
tolerance.**

* **Not a sweep.** Refused on the measurement above: it buys little and risks eight hangs. A 22-site
  edit justified by one unreproduced failure is the shape this register exists to prevent.
* **Not tolerance.** Retries or waits would suppress a signal whose cause is unknown, which is how R4.26
  spent three releases mislabelled as a flake. There is also nothing to "handle": Chromium speaks
  HTTP/1.0 correctly, and the protocol version is not causing a protocol problem.
* **The class closes at the seam.** One `serve()` helper owns the protocol version, the `Content-Length`
  framing and the synchronous-reveal discipline, so every future fixture is correct by construction and
  the 38 hand-rolled handlers migrate as they are touched rather than in one risky sweep. This occurrence
  is evidence FOR that step; it is not an argument for editing 22 files today.

**Not pinned, deliberately.** A pinning test would have to reproduce an intermittent sub-resource drop,
which is the "fish for the mechanism" approach R4.26 showed to be a waste — that finding reproduced 1 run
in 40 under load and **zero in 150** once instrumented. If this recurs, the thing to build is a
deterministic harness that drops one sub-resource on purpose, not a longer wait.


## R4.57 — a SUCCESSFUL run reports the failed attempt's failure_code

**Severity: LOW** (report truthfulness; no inviolable at stake, no write mis-handled). Filed because a
caller that branches on `record.failure_code` sees a failure on a run that returned data, and because of
where it was found.

**Found by the exit-set matrix on its first run** — the instrument built in the same slice, driving
`replay()` through the auth-refresh retry, a path that had no browser-free cell and therefore no cell at
all. B1's own audits looked at this surface twice.

### The mechanism

`_mark_ok` (`flows.py:2141-2151`) exists precisely to clear a previous attempt's verdict, and its
docstring says so:

> "A successful return must clear the failure a PREVIOUS attempt recorded … Only the success exit inside
> `_attempt_replay` cleared them, and those two paths never reach it."

It is called on the two idempotency-precheck exits and the relearn success. But the success exit *inside*
`_attempt_replay` (`flows.py:2384-2387`) sets only:

```python
record.ok, record.landed, record.committed = True, landed, committed
```

— and never clears `failure_code`. So the post-auth-refresh success, which DOES go through that exit,
returns data with `ok=True` and `failure_code="drift"` from the attempt that failed. Measured with the
fake engine (attempt 1 drifts, re-login, attempt 2 succeeds):

```
returned data; rec.ok=True  rec.attempts=2  rec.auth_refreshed=True  rec.failure_code='drift'
```

**The same shape as B1's M3, on the one success exit M3 did not cover.** M3 found `_mark_ok` missing from
three success returns and added it to those three; the fourth success return — the one that already sets
`ok` and so looked handled — sets an incomplete subset. A guard applied to the paths that were broken and
not to the sibling that was merely incomplete.

### Why it is LOW and not lower

Nothing acts on `failure_code` today inside `src/` — it is a field on the record a caller receives, and
`run_batch`/MCP read `exc.code` from the exception instead. The harm is a benchmark or an operator
dashboard bucketing a successful run as a drift, which is exactly what `docs/realistic-benchmark-plan.md`
B3 intends to do with those codes (`refused` sub-buckets derived from real codes, never message labels).
So it is cheap now and wrong later.

### Fix shape — a hypothesis, and it should not be a fourth call site

Adding `_mark_ok(record)` to `_attempt_replay`'s success exit would work and would be the fourth
transcription of "a success clears the previous verdict". `docs/reshape-plan.md` step 1.5 replaces all ten
record write sites with ONE sink whose `finish()` derives the record from the attempt list, at which point
"a successful run carries no failure code" is a property of the sink rather than something four call
sites must each remember. **Disposition: step 1.5**, with the strict-xfail cell in
`tests/test_replay_exit_matrix.py` flipping when it lands.

**Pinned** by `test_R4_57_a_successful_retry_clears_the_failed_attempts_code`, strict-xfail against
shipped behaviour.

---

## R4.58 — the resource sampler measures the wrong quantity, and the first healthy baseline says so

**Severity: MEDIUM** (diagnostic capability, not product behaviour — but it is the instrument every
future Windows CI failure will be diagnosed with, and it has now been shown blind to the symptom class
it faced). Observed on PR #177's CI, 2026-08-18, run `32187328059`.

**What happened.** The Windows shard-2 job failed, was re-run with `--failed`, and failed again — on a
**different test each time**:

| attempt | test | symptom |
|---|---|---|
| 1 | `tests/test_writable_slots.py::test_writable_slots_end_to_end` | `DriftError: write not confirmed (no completion signal on the page)` |
| 2 | `tests/test_run_batch.py::test_dry_run_key_preview_matches_wire_key` | `TimeoutError: Locator.wait_for: Timeout 5000ms exceeded` |

Two different tests, same runner, both **latency-shaped**: something the page was expected to do inside a
budget did not happen inside that budget. This is R4.22's "a resource error in whichever test happened to
be running" shape with a **different symptom** — R4.22 is specifically `ERR_NO_BUFFER_SPACE`, and neither
of these is.

**What was ruled out first, so nobody re-derives it.**

* *Not this slice.* PR #177 changes zero `src/` lines and nothing page-side.
* *Not a fixture async-reveal race.* `tests/test_writable_slots.py`'s fixture has no `fetch(...)`, no
  `setTimeout` and no JS at all — a real form POST, a 303, and a static `/done` page carrying the confirm
  text. The synchronous-reveal discipline was already satisfied.
* *Not shard re-balancing.* `test_writable_slots.py` sat entirely in group 2 **before** this branch too
  (checked by collecting at `c3186db` in a worktree). Shard 2 gained 9 tests and got **48 s FASTER**
  (1003 s → 955 s), so it did not become the heavier half.
* *Not local.* Both tests pass here, and the whole file passes in 27.8 s.

**The measurement that matters, and it is the one R4.22 has been waiting for.** The sampler runs on both
Windows jobs and its summary step runs on success too, so this run finally provides a healthy baseline
taken *in the same run, on the same OS, minutes apart*:

| metric | shard 2 — **FAILED** | shard 1 — **passed** |
|---|---|---|
| `time_wait` max / mean | 382 / 142.3 | 336 / **171.3** |
| `handles` max | 53 423 | **53 736** |
| `processes` max | 153 | **158** |
| `chrome_procs` max | 8 | **16** |
| `nonpaged_pool_mb` max | 234.0 | 231.4 |
| `paged_pool_mb` max | 350.6 | 344.2 |
| `free_mb` min | 12 739 | 12 740 |

Every metric is within ~2 %, free memory is identical to within 1 MB of 16 379, and on **four of seven**
the *passing* job was the more loaded one — twice as many concurrent Chromium processes, more handles,
more processes, a higher mean TIME_WAIT. **Resource exhaustion does not distinguish the failing run from
the passing one.** That is a refutation, not an absence of evidence: the instrument was built precisely
to answer this question and it has now answered it in the negative for this symptom class.

**The gap.** `scripts/sample_resources.ps1` samples sockets, handles, processes, pool memory and free
memory. It samples **no CPU utilisation and no disk queue**. A five-second locator timeout and a confirm
that misses its budget are *latency* failures, and on a 2-vCPU hosted runner latency is about CPU
contention and I/O — neither of which appears in any column above. The instrument is measuring the
resource the *previous* symptom was about (R4.22's `WSAENOBUFS`) and is blind to the one this symptom is
about. CLAUDE.md's own line — *"Every hypothesis so far has been about something else"* — now applies to
the instrument that was added to end that.

**Disposition.** Add CPU and disk-queue sampling before the next occurrence is diagnosed; the columns are
one line each in the existing sampler and cost nothing at 5-second intervals. Do **not** re-derive the
socket / handle / memory hypotheses for a timeout-shaped failure — this measurement rules them out. And
do not reach for the browser pool as a reaction: R4.22 already records the occurrence that did not
justify it, and pooling reduces launch churn, which is exactly the quantity shown here not to
discriminate.

**What this does not say.** It does not identify the cause. One same-run A/B is two data points, taken
under GitHub-hosted-runner conditions nobody controls, and "the failing job was not more loaded" is
consistent with several causes (CPU steal, a slow disk, an unlucky neighbour) that the current sampler
cannot separate. It also does not say the two failing tests are sound — only that nothing about them or
about the slice explains a failure that lands on a different test each attempt. The next occurrence is
worth waiting for **with a better instrument**, which is the whole disposition.
