# The correctness plan — no new capability, only truth

**Goal.** Bring ultracua to a state with no known testing holes and no known correctness issues, for
users. Simplify and make more conservative where that closes a hole. Nothing lands without a test that
failed first; nothing merges that is not green; anything touching write safety gets a pre-merge
adversarial audit — the only instrument that has ever caught a wrong fix here (three for three).

**Grounding.** Survey at v0.75.0 (main; all 118 PRs merged; no open PRs): 11 open register items
(R3.2, R3.3, R3.5–R3.11, R3.12, R3.13), 6 test-machinery holes (H1–H6), 5 CLI/API truthfulness defects
beyond the register (CLI-1..5, MCP-1), 2 doc lies (DOC-1/2), ~16 stated residuals of which 9 are
UNPINNED. The full survey ships with Phase 0 as `docs/correctness-survey.md`; **every identifier in it
must end this plan either fixed, pinned, or carrying an argued disposition** (exit criterion 6).

**This plan was itself adversarially critiqued** (two judges, "sound-with-changes") and revised: the
ordering below reflects their confirmed gaps, several of which were this register's own documented
mistake-shapes recurring inside the plan draft.

---

## Phase 0 — Settle the working tree (one slice)

**Park the causal-attribution BRANCH; do not park the conservative half of R3.2.** The branch is
capability work with three unresolved audit criticals. But the critique is right that "park everything"
conflates two things: the register's own "cheapest correct option" — use the causal signal only to
REFUSE what cannot be proven — is conservative correctness, and it closes AB-1, the one remaining
SILENT wrong-gate residual. That becomes S6 below. The branch itself is parked.

Actions: save the branch diff as a patch artifact; record all 20 round-4 audit findings in the register
(the mandatory checklist for S6 and any future capability attempt); reset the tree to main; commit
`docs/correctness-survey.md` and this plan; fix DOC-1 (README misstates the register) and DOC-2
(README instructs bare `uv sync`, which breaks the environment) in the same docs commit.

## Phase 1 — Strengthen the net first (tests-only, two slices)

- **S1. Matrix: gate-on-the-WRITING-step (H1, H5).** The invariant matrix asserts only that *some*
  step is gated — which is exactly why it missed the round-4 clobber. Each cell's fixture knows which
  control writes; assert the gated step IS the writing step, and that the keyed replay request is the
  commit. Same fix in `test_multiwrite`. Print what each cell exercises before believing it. Also
  hoist the AB-1 pinning test here (it graduates from "pin" to "closed" when S6 lands).
- **S1b. drift_bench corpus extension.** The bench is named adjudicator for S9/D2/D3 yet its corpus
  has no fixtures for the shapes they change — "byte-identical baseline" would be trivially, meaninglessly
  satisfied. Add rows: shared-href rows, shared data-testid rows, hidden rows, positional
  `data-index` tokens, nested icon-only action lists, fuzzy-name decoys. Re-baseline deliberately,
  with the new rows' semantics stated in `baselines/README.md`.

## Phase 2 — Measured write-safety defects, ordered by argued user harm (one slice each)

Each: RED test verified against current main → fix in the MECHANISM → matrix dimension → siblings
checked → suite + drift_bench green → adversarial audit → PR.

Ordering argument: S2/S3 are fully silent double-submits; R3.13's re-fires at least print a refusal
each run, and its fix (quarantine) must not land before skip-visibility exists, so it comes after S7a.

- **S2. R3.5 — undeclared write double-POSTs via the auth-refresh retry.** ✅ **DONE in 0.77.0**, via
  `_auth_retry_allowed` — one definition of when a drifted replay may be re-run, keyed off
  `is_write_flow` rather than the declaration. The enforcement test landed too, rebuilt as an AST scan
  with a positive control after the regex version was shown to be theatre.

  **THIS SLICE'S OWN PRESCRIPTION WAS WRONG, TWICE, AND THAT IS THE LESSON FOR EVERY SLICE BELOW.**

  *Wrong the first way:* "convert the fifth transcription to `is_write_flow`" is a defect — three sites
  downstream of that binding dereference `spec.mutate`, so widening it in place turns a refusal into an
  `AttributeError`. The patch reproducing the class it closes, one level down, sitting inside this plan's
  own text.

  *Wrong the second way, and worse:* the natural correction — push the siblings' flow-level refusal down
  into `_preflight_row` — was built, went green (105 targeted tests, a new 24-cell matrix dimension
  measured at 20/24 detection), and is **unshippable**. `step.mutating` over-counts: `classify_mutation`
  falls back to an unbounded substring match, so 8 of 10 sampled ordinary read navigations classify as
  mutating ("Payment history" → `pay`, "Show borders" → `order`). Refusing those flows breaks a large
  working population, and neither remedy the refusal names can rescue it — `flow record` re-derives the
  same verdict *and deletes the recipe*, declaring `mutate` demands a confirm signal a read cannot
  produce. That is the 0.74.0 over-refusal regression one population over.

  *Wrong a third way, caught by auditing the REWORK:* the retry-level fix, green again, still left
  `on_drift='relearn'` re-performing an undeclared write on an UNAPPROVED flow — and that path returns
  NORMALLY with a recorded success, so it is worse than the arm the finding is named for. This entry's
  own scope note had excluded relearn because it "is refused for any APPROVED flow", which covers the
  wrong half. Closed in the same slice. A second bug of the identical shape (declaration standing in for
  reality) was found INSIDE the new predicate: `is_multiwrite()` counts declared barriers, not the
  mutating steps that actually fire.

  **Three process consequences, which are the transferable part.** (a) Green was worthless three times in
  this one slice; only the adversarial passes caught it, each time. Do not treat a passing suite plus a
  measured matrix as evidence — measure what a refusal REFUSES, on the real classifier, before writing it
  up. (b) **Audit the rework, not just the original.** The second pass found a CRITICAL the first pass had
  walked past, in the same function, because the fix had changed under it. One adversarial pass per slice
  is not enough when the fix is redesigned mid-slice. (c) Every remaining slice's fix shape in this
  document was written in the same voice as S2's, by the same process, without that measurement. Treat
  each as a hypothesis, not an instruction. Full record in `docs/open-defects.md` under R3.5.
- **S3. R3.3 — a landed write re-fires on resume.** ✅ **DONE in 0.78.0.** Every failure return carries
  the evidence via a `_fail` closure, and `replay()` stamps every outgoing `FlowReplayError` at ONE point
  (its existing `except` handler) rather than at four raise sites.

  **This slice's prescription was WRONG, like S2's** — an earlier draft of this bullet claimed it "was
  right as written", which the paragraph below contradicts. It said "set `landed` at the single POINT
  where the confirm transition is observed", and that position was the first of five criticals: the
  transition is not observed there.

  The invariant layer went further than "matrix dimension". The behavioural property covers the failure
  kinds that exist today, but R3.3 was never about those — it was about the return ADDED below the
  evidence point afterwards. So the load-bearing test is an AST scan requiring every `return` in
  `_attempt_replay` to be the success tuple or a `_fail(...)` call. Measured: reinstate one raw tuple
  return that still carries `landed` correctly — behaviour unchanged, both behavioural tests green, only
  the AST guard fails. Prefer that shape wherever a fix's real risk is "the next person adds an exit".

  **But the plan's own wording ("set `landed` at the single POINT where the confirm transition is
  observed") contained the critical.** The transition is not observed at that point — `finalize` runs
  unconditionally, so it can have happened on a run that later failed a step, and the position sits below
  the `not report.success` guard. The second adversarial pass reproduced two payments for one request.
  R3.3 says "the exception's CLASS is the wrong proxy"; this plan answered "the POSITION is the right
  proxy"; both are proxies. **When a finding says a proxy is wrong, check whether your replacement is
  also a proxy** — read the evidence. That is now three slices running where the plan's prescribed fix
  shape was itself defective, so the standing caution above is not rhetorical.
- **S4. R3.8 — one transient error destroys the trust sidecar.** ✅ **DONE in 0.79.0.** The
  respecification held: load PROVENANCE, not a field check. `_load_meta_with_provenance` returns
  `(meta, "file"|"absent"|"unreadable")` and `_update_meta` refuses the read-modify-write on the third,
  raising `MetaUnreadableError`. A field check provably could not work — `release()`'s own mutation
  erases the marker it would test — and that is the second plan prescription to survive contact, against
  three that did not.

  Note the THREE states: folding `"absent"` into `"unreadable"` stops any new sidecar being created;
  folding `"unreadable"` into `"absent"` is the finding. The policy is a REQUIRED per-site keyword, so a
  new site cannot inherit one nobody considered.

  **The slice was NARROWED after three adversarial passes.** The core was right; every later defect was
  in the REMEDIATION of an audit finding — four of them, all cut and filed (R4.16-R4.19). When an audit
  says a caller lacks a guard, fix that caller, not the mechanism they share; and do not fix broadly
  under audit pressure, which is what turned one slice into three rounds of new defects.
- **S7a. R3.9 + CLI-1 + R4.14 — skips are visible to cron.** ✅ **DONE in 0.80.0.** Hoisted ahead of S5
  deliberately: S5 creates a new skip class, and landing it while `run-all` exits 0 on an all-skipped
  fleet would widen the silently-dark surface. A fleet that ran nothing exits nonzero and fires the
  webhook.

  **This prescription survived contact — the third of six that has** — but it under-specified the slice
  in two ways worth carrying forward. (a) It named one skip class; the sibling check found a second, and
  it was the S4 shape one reader over (`run_all` decided whether to run a flow from a `_load_meta` that
  can synthesise `approved=False` from a sidecar it could not read). **A plan bullet naming a defect by
  its symptom will under-scope the mechanism.** (b) It said nothing about what must STAY quiet, and
  "alert on everything" passes every test the bullet implies while destroying the alert channel it is
  fixing — the D0 shape in a new place. Both directions are now pinned, including the `--allow-empty`
  collision that an existing test caught within minutes of the "nothing ran" rule landing.

  R4.14 was pulled in rather than left for later because it is the same invariant on the sibling verb
  AND its precondition-holder: **R4.10's fix adds a `cache.get` to the very loop R4.14 guards**, so
  landing R4.10 first would have widened the hole before fixing it. That makes R4.10 the natural next
  slice. The other sibling, CLI-4 (`flow canary` exits 0 on an all-not-learned fleet), is the identical
  shape and is deliberately LEFT in S7b rather than pulled in ad hoc — but it should get
  `fleet_verdict`'s treatment there, not a third hand-rolled condition.
- **S5. R3.13 — a refusal is non-terminal.** ✅ **DONE in 0.81.0.** Clearing requires a human act
  (`flow release`), and the "refuses once transiently, invoked again" cell landed as the
  direction-of-error pin: the memory never re-checks by driving a browser, so a transient refusal holds
  until a human clears it, and the test replaces the page with one that learns cleanly to prove the
  refusal holds anyway.

  **THIS PRESCRIPTION WAS WRONG TOO — the fourth of seven.** "Quarantine the key via
  `FlowMeta.quarantine`" cannot work: quarantine is enforced in `_preflight_row`, and R3.13's loop is
  `mode="auto"` → cache miss → LEARN, which never reaches it. The engine cannot read `FlowMeta` at all
  (circular import), so the memory had to move to `FlowCache` and be ON BY DEFAULT — an injected policy
  hook would have left `ultracua run` and the daemon re-firing forever, which is the
  wrapper-not-mechanism shape. **Reproducing first is what surfaced this**: the measurement covered both
  entry points, and the second one is the whole design constraint.

  Sequencing note for whoever picks up next: the register's per-finding "fix shape" paragraphs are
  hypotheses written before the code was read. Four of the seven attempted so far have been wrong in a
  way that only a reproduction or an adversarial pass caught.
- **S6. ✅ DONE in 0.89.0 — but NOT as a refusal oracle; that framing was measured wrong.** AB-1 is
  closed by sharing the recorder's marker script with the learn path and using it as evidence about
  whether a gate PLACEMENT can be trusted — no attribution, no seq→step map, so R4.3/R4.4 stay removed
  by construction as this bullet intended.

  **Two versions died to measurement before the third shipped, and both deaths are the point.**
  (a) The refusal this bullet specified would refuse **4 of 6 ordinary READ patterns** issued over POST,
  including an awaited round-trip — D0's regression one surface over, because `is_write_request` is
  method-based and a GraphQL read is a POST. (b) Over-gating unconditionally broke the ordinary
  `filter → place order` flow in one suite run, because the arm AB-1 lives in is also where the ordinary
  write flow lives and the two are indistinguishable from timing. Full numbers in the register under
  AB-1; a separate defect found while pricing it is filed as **R4.27**.

  **The transferable part:** this bullet's prescribed fix shape was wrong in the same way S2's and
  S3's were, which is now four for four. Treat every remaining slice's fix shape here as a hypothesis.

  **AND THE SLICE MISSED A HAZARD ITS OWN BULLET NAMED.** The text below requires a RED test for
  page-synthetic clicks masking a deferred write; S6 shipped without one, and the hazard is real against
  the mechanism that shipped — reproduced 10/10 at 0.90.0 and now carrying a strict-xfail RED test
  (**R4.5**). The prescribed fix shape being a hypothesis is the lesson already recorded here; this adds
  a second one, about SCOPE rather than shape: **a hazard carried into a slice by description, with no
  test to hold it, quietly leaves that slice.** A named-but-untested hazard should be treated as out of
  scope unless the slice's test list names it too — the same reason the plan's dependencies are written
  as orderings rather than as intentions.

  Original bullet follows. The register's "cheapest correct option",
  scoped conservatively: share the recorder's `__ucturn`/`attributedSeq` machinery (ONE implementation
  — R3.1's lesson) and use it ONLY to detect "a wire write occurred whose cause the page cannot prove"
  → refuse loudly. **What it inherits is now worth more than when this was written:** after R4.26 the
  turn boundary is taken from the platform (`window.event`) instead of a `setTimeout(..., 0)`, so the
  primitive S6 shares is no longer a bet on the scheduler. Note the same criticism applies to the
  mechanism S6 would REPLACE — `flow.py`'s `write_window_ms` grace tails are a clock-based boundary of
  exactly the kind R4.26 showed to be unsound, which strengthens the case for sharing rather than
  tuning. Do not read that as licence to skip S6's own RED tests. No attribution, no augmentation, no seq→step map — which removes the parked
  branch's cross-origin-seq and drain-rebind hazards by construction. The remaining round-4 hazard
  that DOES apply (page-synthetic clicks masking a deferred write) is in scope and must have a RED
  test. First tests: the preserved defer-ladder fixture (60/300/450/600/800 ms) and the refuter
  fixtures from the parked branch. Cost stated: a debounced commit becomes unlearnable via `learn()`
  — already the documented answer (`flow record`)… and note `record()` refuses it too, so the
  capability-bounds doc (Phase 6) must say that plainly.

## Phase 3 — Conservative-narrowing decisions, made while exposure is understood (user calls)

Moved ahead of secrets/CLI per critique: these are silent wrong-TARGET writes shipping today at 0-LLM,
the harm class the register has rated critical every time it appeared. Each is reproduce-first and
adjudicated on the EXTENDED corpus from S1b. Deciding "no change" is acceptable; deciding late is not.

- **D0.** (from S2) Undeclared-write flows refuse everywhere — confirm scope. ⛔ **DECIDED, 0.77.0: DO
  NOT, and it is BLOCKED, not merely deferred.** It was implemented and reverted. A flow-level refusal
  keyed off `is_write_flow` refuses a large population of ordinary READS, because `step.mutating`
  over-counts from two independent sources: `classify_mutation`'s unbounded substring fallback (8 of 10
  sampled read navigations classify mutating) and the wire promotion's stated read-POST residual (every
  GraphQL-backed SPA read). Neither remedy a refusal can name works for that population — `flow record`
  re-derives the verdict and deletes the recipe; declaring `mutate` demands a write-completion signal a
  read cannot produce, and is a one-way door.

  **Unblocking condition — CORRECTED after measurement (the first version of this bullet was wrong).**
  It named "(i) word-boundary matching in `safety.MUTATING_KEYWORDS`, currently `borders` matches
  `order`" as the first step. **Measured, that is a write-safety REGRESSION, and the whole (i) line is a
  dead end:**

  | matching rule | read false-positives fixed | genuine writes that LOSE their gate |
  |---|---|---|
  | substring (today) | — | 0 |
  | word boundary | 17/20 | **16/21** — `Reorder`, `Resend`, `Unpublish`, `Ordering`, `Submitting form` |
  | affix + inflection aware | **3/20** | 0 |

  Word boundaries un-gate ordinary inflections and affixes of real commits. The safe variant preserves
  every write and removes almost nothing, because the surviving false positives are DEVERBAL NOUNS —
  `payment`, `sender`, `subscriber`, `publisher`, `confirmation`, `transfers`, `bookings` — which are
  morphologically identical to the inflected verb. `Transfers` (a list) versus `Transferring funds` (a
  commit) is not separable by any string rule. **Do not spend a slice on the keyword matcher.**

  So the only real lever is (ii): persist WHY a step was marked — keyword guess vs form method vs wire
  evidence — so a refusal can key off evidence. Three constraints on it. It must stay OUT of
  `_HASHED_STEP_FIELDS` (that list is an allowlist, so a new field is approval-safe only if excluded;
  include it and every approved flow raises `StaleApprovalError`). It belongs INSIDE S6/AB-1, which needs
  the same primitive (no longer blocked — S17 landed in 0.88.0) — building it twice is how a fifth wrong fix arrives. And it
  is not retroactive: `mutating` is persisted and never recomputed, so D0 stays blocked for every
  already-cached flow until it is re-learned.

  **Net: D0 is blocked indefinitely, not pending a small fix.** The retry-and-relearn guard from S2 is
  the answer for as long as the write signal is a guess — treat it as the design, not a stopgap. (ii) overlaps **S6/AB-1** (the
  causal signal as a refusal oracle), whose S17 blocker cleared in 0.88.0.

  **What the deferral actually costs that population, stated precisely — an earlier draft of this bullet
  said "one auth-refresh retry and nothing else", which the same commit falsified.** They lose (a) the
  auth-refresh retry, and (b) `on_drift='relearn'` entirely, since relearn re-performs the write and its
  gate keys off the same over-counting signal. (b) is an up-front refusal of the whole RUN, not just of
  the healing. It is bounded — no fleet surface is affected (`run_all` already skips these flows on the
  identical predicate before `replay()` is reached; `run_batch`, `preflight_keys`, `dry_run` and MCP all
  pass `on_drift="raise"`), approved flows were already refused relearn, and the named remedy
  (`flow learn`/`flow record` by hand, which does not funnel through `_preflight_row`) genuinely works —
  which is exactly what distinguishes it from the rejected flow-level refusal, whose remedies did not.
  **Do not re-attempt D0 without redoing the refused-population measurement first** — it went green,
  with a measured 20/24 matrix, and was still wrong.
- **R4.10.** ✅ **DONE in 0.82.0.** (found by S2's sibling check) `run_audit` skipped write flows on
  `spec.mutate is not None` alone, so an undeclared write was captured and judged in contradiction of its
  own "never captured, never judged" invariant.

  **The dependency this entry recorded was the real one, and it paid off**: the fix needs `cache.get`
  inside the candidate loop, which raises `CacheUnreadableError`, and before S7a's per-flow guard
  (R4.14) an escape there discarded every flow already judged. This is the plan's "a hole-widener never
  lands before its hole-fix" rule working as designed — the one ordering constraint in this document
  that has actually bound.

  **Reproducing first doubled the slice, as it did for S5.** The judge half was as filed. The CAPTURE
  half — that a write flow's post-commit page was being written to disk at all, against a guarantee
  printed in the skip message — was not in the filing and is the more serious of the two. The gate went
  into `_capture_audit` rather than onto its call site, so the judge's gate is now a second line rather
  than the only one.

  Two entries in a row where the register's own text understated the finding. Treat a filing's SCOPE as
  provisional too, not just its prescribed fix.
- **D2.** Refuse sole-candidate fuzzy (`role+name~`) binds for MUTATING steps (AB-2).
- **D3.** Reject purely positional row-identity tokens (`data-index`, `id="row-N"`) (AB-3).
- **D4.** Learn-path WebSocket parity with the recorder (AB-8): a sent WS frame during learn is a
  write-suspect; an undeclared one refuses. The register's named sibling-gap shape.
- **D1.** Vision tier screenshots secrets (AB-6): refuse secret slots under the vision tier
  (recommended) or document-and-pin only.

## Phase 4 — Secrets at rest (ordered fix-then-thread)

- **S8. R3.10 first — the redaction floor.** ✅ **DONE in 0.84.0.** The scrub had no minimum-term-length
  floor; a short term shreds text. Matched the sibling `audit._redact` floor — and put it in ONE place
  (`snapshot.REDACT_MIN_LEN` / `redact_terms` / `apply_redactions`) that all three channels now route
  through, since "three copies of one rule, one of which drifted" WAS the finding.

  Ordered BEFORE S9 because S9 threads this primitive into a new channel — threading a known-defective
  scrub into the strings replay BINDS ON would corrupt cached anchors (the critique caught the draft
  scheduling the spread before the repair). **That ordering was right and is now the second dependency in
  this document to have actually bound**, after R4.14 → R4.10.

  Two things the bullet did not say and the work had to decide. The floor **does not close the class** —
  `1234` still mangles `12345` — so the residual is pinned as a demonstration, not prose. And the floor
  **costs** confidentiality on short secrets, which is a trade to be argued rather than a side effect; the
  argument, and what to do instead if it is ever judged wrong (drop the USERNAME from the term list, never
  the floor), are in the register.
- **S9. R3.6 — `describe()` writes page-derived secrets to disk.** ✅ **DONE in 0.86.0.** Threaded the
  S8 scrub (floor included) into `describe()` AND into `recorder._step_from_event`, which builds its own
  `LocatorSpec` from the same in-page `specOf` — one shared `redact_spec_fields`, because fixing only
  `describe()` would have left `flow record` leaking to the same file.

  **The S8-before-S9 ordering was right and is the third dependency in this document to actually bind**
  (after R4.14 → R4.10 and S8 → S9 itself): the scrub S9 threads is the one S8 repaired, floor and all.

  Two things the bullet did not anticipate, both found by auditing the fix rather than by a test. A
  redacted `anchor_id` can never match the live page it is compared against by equality, so it is
  DROPPED rather than stored — a fabricated identity is the failure this module already documents. And
  the "scrub both sides at compare time" alternative is rejected in writing, because it makes two
  different secrets compare equal and trades a loud refusal for a silent wrong-row bind.

  Cache-dir permissions (the bullet's third clause) are NOT done and are not folded in: `FlowCache.put`
  still writes at the default umask while `audit.capture` chmods 0o700. Left as its own item rather than
  widened into this slice.

## Phase 5 — Fail-loud means the exit code too

- **S7b. Remaining CLI truth (CLI-2..5).** ✅ **DONE in 0.87.0.** One rule, as the bullet said: work
  that did not succeed exits nonzero with the reason printed.

  **The prescription held, and CLI-4 is why the S7a cross-reference was worth writing down.** S7a
  recorded CLI-4 as a KNOWN-IDENTICAL shape to CLI-1 rather than leaving it to be rediscovered, so this
  slice extracted `sweep_verdict` — one rule (loud outside a quiet ALLOWLIST; exit 2 when nothing was
  actually checked; else quiet) parameterised by each surface's vocabulary — instead of hand-rolling a
  third condition. `fleet_verdict` is now a thin wrapper over it and S7a's 12 tests pass unchanged.

  That framing also produced the right nuance rather than a binary: `not-learned` is QUIET on its own (a
  saved-but-not-yet-learned flow is an ordinary intermediate state, and going red for it nightly is how
  an alert earns its `|| true`), while an all-`not-learned` fleet is loud via the second clause.

  **CLI-3's real defect was the MESSAGE, not the exit code** — "the agent took no clean steps" is false
  for the refusal population, and `res.note` already held the true reason and its remedy.
- **S10. API contract truth.** `_load_meta`'s "never raises" broken by `RecursionError` (R3.11);
  MCP tool-list shrinkage visible only in logs (MCP-1) — surface count/reason in the listing.

## Phase 6 — Remaining register items + systemic holes

- **S11. R3.7** — `_ROW_OF_JS` does not mirror `anchorOf`'s walk; false refusal + phantom-drift
  accusation + heal-LLM burn on nested action lists. Adjudicated on the S1b-extended corpus.
- **S12. R3.12** — reproduce first (register's own rule; it is code-reading-only today), then fix or
  refute the dryrun act-window mislabel.
- **S13. Evals: port or delete (simplification).** Evals run nowhere, the runner cannot gate (H2), one
  grep-shaped check was red six releases (H3). Port load-bearing checks into pytest as behaviour;
  delete the rest and the runner. H6's ungated benchmarks get the same treatment: gate or delete.
- **S14. Properties for inviolables #1 and #2 (H4).** #1: replay across the drift corpus with a
  provider that raises on ANY call. #2: for every failure shape in the API map, a typed error or
  nonempty note — never a bare False/log-only.
- **S15. Pin every remaining unpinned residual** (AB-2..5, 7, 9, 11, 13 — minus any closed by Phase 3
  decisions). Each pin DEMONSTRATES current behaviour and names the residual, so any silent change in
  either direction fails a test. AB-10 (possibly-dead belt-and-braces branch): coverage probe; delete
  if dead.
- **S16. Scheduled mutation sweep.** The nine known mutants become a script + a weekly CI schedule.
- **S17. ✅ DONE in 0.88.0 — the test was not flaky; the recorder was wrong, and it is now fixed.**
  Rescoped by measurement at 0.87.0: under artificial load the deferred-write refusal failed ~1 run in
  20 and the cached result put the gate on the BENIGN click while the committing step cached ungated
  and un-keyed — **R4.26**, inviolable #3, R3.2's harm class on the `record` path.

  So "de-flake" was the wrong verb, and every remedy this bullet forbade would have SUPPRESSED a live
  write-safety hole. The attribution was fixed instead and the test stopped failing as a consequence,
  which was the only acceptable way for it to stop failing: **0 failures in 25 loaded runs** of the
  four load-dependent write-refusal tests, against 1-in-8 before.

  Two things worth carrying forward. **Instrumenting the mechanism suppressed it** — 0 in 150 loaded
  runs with an in-page probe — so the trace was never going to arrive by waiting for it; a
  deterministic harness had to be built, and it then REFUTED the inferred cause (see R4.26 for the
  measured one). And **the recorder's attribution had no property-level coverage at all**:
  `tests/test_write_safety_invariants.py` covered only the `learn` path, whose attribution is a
  different mechanism. That structural gap is the reason a misattribution survived three releases, and
  closing it is what the fix's test does.

  **S6 is UNBLOCKED.** Its oracle was honest all along; the mechanism under it was not, and now is.

  Original bullet follows. Found while
  landing S1: it fails intermittently in the full suite and never in isolation (5/5 isolated, 3/3 whole
  file both with and without the S1 diff, 1 failure inside the 781-test run). It guards the DEFERRED
  -write refusal — the exact property **S6** uses as its oracle — so **S6 must not be built on it until
  it is deterministic**. Reproduce under artificial load first; do not silence with reruns, and do not
  weaken the production bound (the register's earlier de-flake work set that rule).

## Phase 7 — Documentation truth

Capability-bounds section for users: what `learn()` AND `record()` refuse and why (deferred commits,
classifier-blind later-step commits on opaque origins, undeclared writes, WS-writing flows if D4) —
so refusals read as designed behaviour. STATUS/README counts squared with reality.

## Exit criteria

1. Register: zero open findings. A finding may be reclassified as a capability bound ONLY if the
   user-visible behaviour is a loud refusal whose message states the bound — behaviour that can be
   silently wrong is never eligible (critique-hardened).
2. Every stated residual has a pinning test; grep for "residual" finds no comment without one.
3. CI runs the suite + drift_bench (extended corpus) on both OSes + ported eval behaviours + a
   scheduled mutation sweep. Nothing verifiable exists outside CI.
4. CLI/API: no failure path exits 0; no refusal loses its reason.
5. A final full-tree adversarial audit reports zero confirmed findings — or is re-run after fixes
   until it does.
6. Every identifier in `docs/correctness-survey.md` is fixed, pinned, or carries an argued
   disposition in the register.

## Standing rules (gates, not habits)

- RED first, verified to fail against pre-change source. Green only: full key-less suite +
  drift_bench (deliberate re-baselines argued in writing).
- Audit the fix: anything touching flow.py / flows.py / locators.py / recorder.py write paths gets a
  pre-merge adversarial audit.
- **Two strikes, then change the SENSOR CLASS.** After two fix shapes for one finding have been built
  and measured wrong, the third must change what the decision is made FROM — inference → a human's
  verdict, or inference → a loud refusal — not refine the inference. Green cannot tell attempt N from
  attempt N+1 here, so "until the suite passes" is not a stopping rule. Full gate, the class table, and
  its worked application (**D5**, attribution blocked) are in `docs/open-defects.md`.
- Matrix over bespoke: write-safety fixes add a dimension, and the cell prints what it exercised.
- Guards go in the mechanism, never the wrapper; predicates become inexpressible raw (enforcement
  tests), not merely consolidated.
- Flake gate: any timing-adjacent test touched runs 10× clean before merge.
- One slice per PR; the user merges.
