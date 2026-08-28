# The correctness plan — no new capability, only truth

**Goal.** Bring ultracua to a state with no known testing holes and no known correctness issues, for
users. Simplify and make more conservative where that closes a hole. Nothing lands without a test that
failed first; nothing merges that is not green; anything touching write safety gets a pre-merge
adversarial audit — the only instrument that has ever caught a wrong fix here (five for five).

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

- **D6.** (from R4.105) **A wire-promoted step is gated on the WHOLE PAGE, not on its own form.**
  ⛔ **REFUTED 2026-08-27 BY ITS OWN MANDATED MEASUREMENT — DO NOT BUILD IT.** This entry
  required the action types to be measured BEFORE any `src/` change, on the grounds that a fix
  on a wrong diagnosis is worse than none. That measurement cost **$0.4634** across four Odoo
  learns and says the fix addresses nothing: 10 wire-promoted steps across the three scenarios that actually refused: **6 `navigate`** (no locator, no scope -- the precise gate is STRUCTURALLY unreachable, there is no element to scope) and **4 `click`** (scope AND locator -- the precise gate ALREADY, and it refused anyway).

  **BOTH BRANCHES REFUSE, FOR DIFFERENT REASONS.** `odoo-menu-nav` refused with `mutation gate:
  page drift` — the fallback. `odoo-sort-list` refused with `mutation gate: target
  missing/ambiguous` — the PRECISE branch's own first failure mode, i.e. the gate D6 wanted to
  route steps to was already judging them. Its failure is `resolve(..., unique=True)` unable to
  bind uniquely on a generated DOM: a LOCATOR problem, not a scope problem. And that
  `unique=True` is a deliberate write-safety choice (fail loud rather than bind a blind `.first`
  into the wrong form), correct for anything the system believes is a write.

  **SO THE GATE IS RIGHT AT EVERY BRANCH AND THE DEFECT IS UPSTREAM**, in R4.27's marking.
  Narrowing the gate would weaken write safety for steps the system believes are writes, which
  is the direction D0 was blocked for. Reproduce with `python -m benchmarks.gate_probe <cache>`;
  the instrument ships with the refutation because a "do not change `src/`" conclusion is worth
  what its reproducibility is worth.

  **THE MEASUREMENT CORRECTED ITSELF TWICE, which is why one run was not enough.** From the
  first scenario alone the conclusion was "the precise gate is structurally unreachable for the
  failing steps" — true of navigations and FALSE as a generalisation, since 40% of gated steps
  already reach it. The second produced no gated step at all. The plan budgeted ~$0.10 for ONE
  run; one run would have shipped a wrong `src/` change to the write rail.

  **AND A SIGNAL WORTH ITS OWN LOOK, NOT YET A FINDING.** R4.105 counted 7 of 12 refusals as the
  mutation gate and 4 as plain locator failures — but `odoo-sort-list`'s gate refusal IS a
  locator failure wearing a gate's message. So R4.27 may be partly masking a second,
  independent Odoo problem (locator ambiguity on generated markup), and fixing R4.27 alone might
  not unblock 2.4b. One scenario's message is a signal, not a measurement.

  ~~⏳ ELIGIBLE 2026-08-26 — the trigger fired and nothing has been built.~~ Phase 3 is taken "only
  when a number indicts it"; the number is **58% of Odoo's replay refusals** across three reps, every
  one carrying the message `mutation gate: page drift`, which is the whole-page FALLBACK branch. The
  precise branch exists and its own comment says the whole-page fingerprint "over-flags" churn
  (banners, badges) — Odoo pages churn constantly. `flow.py` already captures `scope_of[i]` for
  non-mutating **click/type** steps precisely so a later wire promotion can use the precise gate; it
  does NOT for `press`/`navigate`, which have no ref. **UNVERIFIED: which action types Odoo's failing
  steps actually are.** That costs ~$0.10 (one run keeping the recipe cache) and must be measured
  BEFORE any `src/` change — a fix on a wrong diagnosis is worse than none, and this register has the
  scars.

  **NOT A D0 RETRY, and not R4.27's disposition.** D0 is blocked for building a flow-level REFUSAL off
  an over-counting mark; this refuses nothing and demotes nothing. R4.27's disposition was attempted
  and correctly refused 12/12 by `flow mark`. This changes only WHICH drift test an already-marked
  step is judged by.

  **THE SAFETY ARGUMENT IS THE WHOLE SLICE**, and it cuts both ways: the precise gate is STRICTER
  about the target's own form and MORE PERMISSIVE about unrelated churn, so the honest risk is a
  scope fingerprint that is subtly wrong letting a write pass a check it should have failed. It wants
  the write-safety matrix extended by a dimension rather than a bespoke test beside the fix.

  **SEQUENCED AFTER 0.6 AND 2.4**, which is the plan's order and not a preference: Phase 3 follows
  Phase 2, 2.4 is open, and 2.4's nightly shares 0.6's workflow.

- **D7.** (from R4.27) **Classify a POST by the protocol declaration in its BODY, not by its method.**
  📋 **SURVEYED 2026-08-27, NOT SCHEDULED — `docs/reads-over-post.md` is the full write-up.**
  ⚠️ **AND ITS GATING MEASUREMENT CAME BACK NO (R4.114, 0.141.0): this would not unblock Odoo.**
  ⚠️ **AND R4.115 (0.142.0) RE-ATTRIBUTES WHY.** R4.114 blamed the locator; measured, the
  locator binds uniquely on a RENDERED page and the replay is reading an unpainted one (Odoo
  serves 5 elements at `domcontentloaded`, 0 of 7 scenarios complete; Gitea 7 of 7). D7 stays
  a correct fix for the MARKING and is not the Odoo path — and neither is a locator fix.
  Flipping the wire marks off leaves the same step failing, with `bound_by: none` in both arms —
  the mutation gate was reporting a LOCATOR failure it reached first. D7 remains a correct fix
  for the MARKING (reads should not be filed as writes) and is no longer a candidate for the
  Odoo baseline. Anyone taking it should say which of those two things they are buying. Eleven
  approaches with an adversarial pass on each; six are dead, three of them killed by that pass rather
  than by prior history. What survives is a JSON-RPC read-method allowlist plus a route-EXACT read-route
  list, failing closed on everything else, packaged as a version-pinned framework profile.

  **WHY THE RECORDED REFUSALS DO NOT REACH IT.** The URL-denylist refusal reads "a GraphQL MUTATION
  travels the same URL"; an Odoo `create` cannot travel under the name `search_read`, because the method
  name IS the operation (`getattr(model, method)` dispatch). It is an allowlist of reads with unknowns
  staying loud — the shape this plan sanctions everywhere else — not an enumeration of exclusions. The
  request body is a sensor class D5 has NOT spent. It demotes a mark and refuses nothing, so D0's block
  does not reach it; D6's own entry states that boundary.

  **THE UNPRICED ASSUMPTION IS THE REASON THIS IS NOT SCHEDULED.** That fixing the marking improves Odoo
  availability at all is assumed by every approach and measured by none — R4.111's tail is the
  counter-signal, and the READ path also resolves `unique=True`, so demotion may only convert "gate
  refused" into "healed and still failed". Six free measurements settle it; **#1 (flip the cached marks,
  replay 0-LLM) decides whether the ranking holds at all** and must run before any `src/` change. That is
  D6's lesson applied to the fix direction before the fix.

  **CONDITIONS, if it is ever taken.** Measure the demoted population offline first (D0's standing
  order); trim the allowlist to methods actually observed; **price the verify-by-replay re-arm** —
  demotion re-enables a full second browser pass, so a wrongly-demoted write is double-fired AT LEARN;
  reach the heal wire-guards too, or Odoo recovery stays poisoned after demotion; demotion ADDS a
  provenance mark and never strips `MARK_WIRE`; no operator-extension door in any form (the
  human-verdict class is spent). One residual is NEW rather than parity and must be filed as such: a
  customized app overriding a read verb to write is gated today and would stop being.

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
  evidence — so a refusal can key off evidence. ✅ **The PERSISTENCE half shipped in 0.92.0** as
  `CachedStep.mutating_sources`; what remains blocked is ACTING on it, which is still D0.

  Two of the three constraints below were WRONG as written, and are corrected here rather than left to
  mislead the next reader. (a) "It must stay OUT of `_HASHED_STEP_FIELDS` … include it and every approved
  flow raises `StaleApprovalError`" — the CONCLUSION holds but the MECHANISM does not: `_canon` omits any
  field still at its declared default, which is exactly how `secret` was added to the hashed list with
  zero re-approvals. The field is `_UNHASHED` by the inclusion rule instead (it arms no guard, and the
  transition it describes — `mutating` itself flipping — is already hashed). (b) "It belongs INSIDE
  S6/AB-1, which needs the same primitive — building it twice is how a fifth wrong fix arrives" — S6
  shipped without it, and the primitive landed standalone; the warning against building it twice stands.
  The third constraint held exactly as written: It belongs INSIDE S6/AB-1, which needs
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
- **S10. API contract truth.** ✅ **DONE in 0.95.0**, scoped as ONE invariant rather than five patches —
  *nothing crossing a boundary loses its type or its reason, and every durable rename goes through one
  helper*. Closed R3.11 (`_load_meta`'s "never raises" was false — `RecursionError` and `Path.exists()`
  both escaped), plus the R4.15/17/18/20 cluster the register told this slice to sequence here.

  **The bullet under-scoped it, and measurement is what said so.** R4.20 was filed as one asymmetry
  (`FlowCache.put` vs `_save_meta`); there were **seven** durable renames and one retry. Reproducing
  before fixing is the only reason the fix went into a shared `fsio` helper instead of a second
  transcription of the retry loop.

  **AND THE PRE-MERGE AUDIT FOUND A CRITICAL IN THE FIX** — 26 findings, 24 refuted, 2 survived. Typing
  `_save_meta`'s failure (R4.18) let it propagate out of the four positions where `_record_run` runs
  immediately before a deliberate raise, so a sidecar blip replaced `WriteUnverifiedError`
  (retryable=False) with `MetaUnwritableError` ("RETRY", retryable=True) and no ledger row — an MCP agent
  honouring that re-fires the commit. Inviolable #3. The guard already existed on `_record_run`'s READ
  half (`on_unreadable="skip"`) and had never been applied to the save half: this plan's own
  most-repeated shape, inside the slice closing four instances of it.

  Two further defects were caught earlier and cheaper — one by the sibling check (typing the error
  un-caught it in `_refuse_unreadable_meta`, turning a CORRUPT verdict into an `unreadable` one with the
  opposite remedy), one by an existing enforcement test going red on the `_read_meta` split. A third
  existing test turned out to be pinning the R4.15 DEFECT: it asserted that a typed error tracebacks out
  of the CLI.

  **MCP-1 was deliberately NOT taken here** and moves to S10b. It is a protocol-surface design question
  — `tools/list` has nowhere to carry a drop count, so the answer is a new surface — and this plan does
  not add capability. Bundling it into a slice that already grew a critical is how the audit surface
  gets too large to aim.

- **S10b. MCP-1 — tool-list shrinkage is invisible to the client.** ✅ **DONE in 0.99.0.** The surface
  was decided first, as this bullet required: a diagnostic tool over an `instructions` line, because
  instructions are computed once at connect and go stale mid-session. The mechanism is the durable half —
  `_tool_for` returns `FlowTool | SkippedFlow`, so a drop cannot happen without naming itself, and
  `QUIET_SKIPS` is an allowlist so tomorrow's skip code is loud by default.

  **The acknowledgement question this bullet raised answered itself:** the report is PULL-based, so there
  is nothing to `|| true`. Nothing alerts; an agent or operator asks and gets an answer.

  **And the audit found the fix committing the defect one layer up** — branching on `health.cached` while
  discarding `health.status` put an R3.13 write refusal in the quiet bucket. The allowlist could not
  catch it: an existing status mapped onto an existing quiet code is invisible to a per-code guard. That
  is the limit of the enumerate-the-quiet-outcomes rule, and worth carrying forward.

- **S11. R3.7** — `_ROW_OF_JS` does not mirror `anchorOf`'s walk; false refusal + phantom-drift
  accusation + heal-LLM burn on nested action lists. ⚠️ **TWO ATTEMPTS SPENT, BOTH MEASURED WRONG,
  BOTH REVERTED. R3.7 stays OPEN and D5's two-strikes gate now applies.**

  Attempt 2 (0.103.0) decoupled `anchor_id` from `anchor_source` — identity from the NEAREST enclosing
  row-like container, structurally, gating on `anchor_id` alone — and closed R3.7 and R4.34 together. It
  passed everything, including a per-row check showing **zero regressions** and survival UP at every k.
  It also converted R3.7's loud false refusal into a **silent wrong-record bind** wherever the nested
  wrapper owns no identity: `rowIdOf` returns null, `anchor_id` is captured as None, and `resolve` reads
  "no identity" as "no guard" while the record key sits one landmark up on the `<tr>`.

  **What both attempts have in common is the reusable part.** Each died on a population no instrument
  contained — attempt 1 on nested rows sharing an identity string, attempt 2 on a wrapper owning none.
  30 test cells and 185 corpus rows were all built from the same mental image of a row, so a single
  blind spot defeated all of them at once. The audit's population cross-product is what found both.

  **What shipped instead:** `bare-nest/icon` in the containment matrix (green on main, RED against
  attempt 2 — the cell that caught it), and **R4.37**, a live silent wrong-row bind on main that the
  same population analysis exposed: a control with VISIBLE text in an identity-less wrapper captures no
  `anchor_id` at all. R4.34, R4.37 and R3.7 are three doors into one fault, and attempt 3 must dispose of
  all three — per D5 by changing the sensor class, since the overloaded `anchor_id=None` (meaning both
  "no token exists" and "I looked in the wrong place") is what both attempts foundered on.

  Attempt 1's record follows.

  The attempt was the fix this plan, the register and the survey all prescribe, and it is the obvious
  one: `_ROWWALK_JS` holding the landmark set, the normaliser, `rowIdOf` and the climb, with the bind
  side given capture's non-empty-text condition so the walks cannot disagree. It reproduced the finding
  first, fixed all 6 diverging cells, passed 26 tests in the row-guard files, and passed `drift_bench`
  with every invariant holding and no baseline regression.

  **It also turned a correct refusal into a silent wrong-row bind** — `POST /cancel/7` for a step
  recorded against `/cancel/3`, `bound_by='role+name'`, no `row_mismatch`, nothing logged, and a
  `scope_fingerprint` that matches byte-for-byte so the mutation gate cannot object. Inviolable #3,
  introduced by the fix for a fail-loud finding. Three independent audit lenses found it, each with its
  own probe; the mechanism is in R3.7's register entry.

  **The transferable finding is that PARITY IS NOT THE INVARIANT.** "Capture and bind name the same row"
  is satisfied by a bind walk that has stopped being a containment check, because two containers can
  produce the same identity string. So the 18-cell parity matrix — which this plan would have accepted
  as the property, and which the attempt passed — was measuring a proxy that broke away from the thing
  it proxies. What ships instead is the CONTAINMENT property over seven nesting shapes: a bind is
  refused or belongs to the recorded record, with a floor on how many shapes must still bind on an
  untouched page so refusing everything cannot satisfy it. GREEN on main, RED against the attempt,
  catching both wrong-record binds. Whatever closes R3.7 has to keep it green.

  **This is why the plan's own rule is "audit the fix, not just the code it fixes".** Every gate this
  plan specifies was green before the audit ran. The register's line that green is not evidence here now
  has a fifth data point, and the first one where the defect was in a fix for a finding with no
  inviolable at stake.

  **Two further findings came out of building the instrument.**

  *R4.33* — the corpus row S1b added to adjudicate R3.7 cannot fail for R3.7, in two independent ways
  (the control carries text, so the nested container is not text-less; and even icon-only, the row's
  only identity is the href inside that container, so both walks return the same string). Measured: the
  shipped fixture reads identically with and without the attempted fix, while a faithful one refuses
  14/14 rows against main. This bullet's own instruction — "adjudicated on the S1b-extended corpus" —
  was therefore unsatisfiable as written, and the adjudication was done in a scratch A/B instead. The
  corrected fixture is NOT landed: it needs a deliberate re-baseline plus a triage of the prediction
  model against an aria-label-only target (75% → 50% agreement on that scenario — measured in one arm
  with fixture and code changed together, so it sizes nothing; it says triage is needed), and bundling
  that into this slice would enlarge the audit surface for no safety gain.

  *R4.34* — the matrix's `GUARD OFF` branch turned up the same root cause pointing the other way, and
  it is HIGH: a shared `aria-label` on a nested per-row container makes capture anchor on it with
  `source='label'`, so the row guard never runs and a wrong-row bind goes through silently. R3.7 fails
  loud; this fails WRONG, which on a write flow is inviolable #3 under the recorded row's
  Idempotency-Key. Pinned as a strict xfail and given its own slice, because the remedy changes what
  `anchor_id` is captured FOR and re-arms a guard on a population that has never had it — a resolver
  trade, which means `drift_bench` adjudicates it.

  Worth carrying forward: **the property matrix found the sibling, not the fix.** Enumerating the
  shapes surfaced a cell whose disposition was "the guard is off here", and asking why that was true is
  what produced R4.34. A bespoke test for the nested-icon case would have gone green and found nothing.

  **S11 REMAINS OPEN and its next attempt is now sequenced, because the order matters.** R4.33 first —
  the corpus row has to be able to fail for the finding before the bench can adjudicate anything about
  it, and that is this plan's own "the net gets strengthened before it is relied on". ✅ **R4.33 is DONE
  in 0.101.0**: `row-nested-icon` is added as its own scenario, it binds nothing on any row including
  its pristine arm while R3.7 is open, and the survival curve's numerators are unchanged — so attempt 2
  now has **12 rows that must move** (the other two of its 14 carry `target_present=False` and must
  never bind). That the row CAN move was verified by simulating a fix, not assumed. It also found R4.35
  on its first run (a heal that re-grounds to a byte-identical recipe and reports a repair that changes
  nothing), which is the argument for fixing an instrument rather than working around it.

  **Two cautions for attempt 2, both of which this slice got wrong before an audit corrected them.**
  The scenario's 9 new `predicted_mismatches` do NOT share one cause — two are pre-existing `predict()`
  gaps visible on four other scenarios in the unchanged baseline — so triage them per row and do not
  read the COUNT as the signal. And `_maybe_heal` never calls `locators.resolve`, so why a replay
  refuses is never why a heal declines; the two allowlists in `tests/test_drift_bench.py` answer
  different questions and had confidently-worded causes attached to them twice, wrongly. Then attempt 2,
  which must solve two things together or reopen the other: the id-bearing container chosen by the SAME
  rule on both sides (candidate: the nearest enclosing row-like container that can prove an identity),
  AND `rowIdOf` no longer borrowing an identity from a row-like DESCENDANT. Both change what a captured
  `anchor_id` means for nested shapes, so it is a resolver trade, adjudicated on the bench, and it does
  NOT bundle with R4.34 even though the two are adjacent.
- **S12. R3.12** — ✅ **DONE in 0.106.0.** Reproduce-first was the whole value of the slice: the finding
  CONFIRMED 3/3, and the same reproduction **refuted the mitigation the entry used to rate itself
  medium**. R3.12 claimed the mislabelled row is "partly self-revealing — commit B's intent beside
  commit A's key", treating the window and the `Idempotency-Key` as independent sources. They are one
  source: the key is a context header live for whichever step is mid-act, so every field on the row
  agrees with every other and all of them are wrong.

  **The fix shape this bullet inherited was BLOCKED, and nobody had noticed.** The entry proposed
  bounding attribution to the drained window instead of a 2 s tail — a temporal design, which `D5`'s
  impossibility #1 rules out by measurement. D5 was written after R3.12 and the two were never
  reconciled. What shipped therefore does not attribute anything; it changes what the report may CLAIM
  (`step` / `ambiguous` / `ungated`, one deciding function, candidates listed). The grace tail survives
  in the rule but now governs how OFTEN "ambiguous" is said and never whether a WRONG step is named —
  the property D5 says a temporal *attribution* rule cannot have.

  **Two near-misses inside the fix, both the register's own shapes.** Reusing `step = -1` for
  "ambiguous" would have rebuilt the `anchor_id=None` overload that has cost R3.7 two attempts; and
  `steps_representative` filtered `-1` out, so an unattributable hold would have certified every step as
  representative — strictly worse than the mislabel. Both are pinned.

  **AND THE PRE-MERGE AUDIT EARNED ITS KEEP AGAIN — SIX FOR SIX.** Two drafts of the fix shipped into
  the audit's hands, both green on 1044 suite tests plus their own purpose-built matrix, and both were
  wrong. Draft 1 used the grace tail as the candidate horizon, so a CONSTANT decided whether a name was
  claimed — D5's impossibility #1, one module over, **with the counterexample sitting in the fix's own
  matrix asserted as correct**. Draft 2 treated a step that had written once as finished writing, which
  handed a control that pings analytics AND defers its real write straight back to R3.12's row, silently
  and at a 10x smaller deferral. The shipped rule has no constant in it: name a step only when it is the
  only one that has acted. The lesson is the one this project keeps paying for — *the instrument shared
  the fix's blind spot*, and here it did worse, containing the refutation as a green assertion.

  **It also exposed a worse sibling: `R4.39`,** live on the REPLAY path. The same "whichever step is
  mid-act" fact picks the Idempotency-Key that actually rides the wire, so one recipe replayed twice —
  with only the page's debounce changed — sent step 0's write under two different keys. A retry cannot
  dedupe. HIGH, inviolable #3, filed and pinned strict-xfail; NOT fixed here, because refusing to claim
  fixes a report and changes nothing about what the browser sends.
- **S13. Evals: port or delete (simplification).** Evals run nowhere, the runner cannot gate (H2), one
  grep-shaped check was red six releases (H3). Port load-bearing checks into pytest as behaviour;
  delete the rest and the runner. H6's ungated benchmarks get the same treatment: gate or delete.
- **S14. Properties for inviolables #1 and #2 (H4).** ✅ **DONE in 0.107.0 — but it had ALREADY PARTLY
  LANDED, and nothing said so.** `tests/test_inviolable_properties.py` was written in `0ec0387` (~0.98.0)
  and its docstring says "S14 / H4", yet this bullet was never marked, so the slice looked untouched.
  That is the DOC-1 staleness this plan fixed once for the README, inside the plan itself — and it is why
  the count guard in `tests/test_register_count.py` exists for the register but has no analogue here.

  **What had landed was the SHAPE without the BREADTH.** It swept ONE hand-made fixture through ONE door
  (`flow.run_cached`) and asserted #2 on TWO hand-picked failures. Both of this bullet's explicit asks —
  "across the drift corpus" and "every failure shape" — and H4's own ("every replay-mode entry point")
  were absent. A property that visits one path is a per-path assertion wearing a property's clothes, and
  the register's standing pattern is that defects here live on the SIBLING path.

  **Three things the breadth work found, none of which the green file could have told anyone:**

  1. **The instrument was inert where it mattered most — TWICE, and the second one only the audit
     caught.** `no_llm` claimed an LLM was "unreachable in BOTH directions", patching
     `ultracua.providers.build_router`; `flows.py:47` binds the name at import, so measured,
     `flows.build_router("anthropic")` ran the real code with the patch active. Deriving the MODULES
     from the live import graph fixed that and was still not enough: the factory NAMES stayed
     hand-listed and `llm.build_client` — which both others call — was missing. The audit armed it and
     a replay built **105 real Anthropic clients** while all 25 cells passed and the corpus cell printed
     "0 reached an LLM". Closed as a CLASS: an AST scan pins that SDK clients are constructed only in
     the leaf adapters, making `build_client` provably the choke point.
  2. **A reasonless failure report** at `flow.py:1019` — a bare `success=False` with no `note`, found by
     an AST scan over every construction site rather than by another hand-picked cell. Believed
     unreachable (`samples = max(1, samples)` guarantees the loop runs), so it was given a reason rather
     than deleted on a reachability argument.
  3. **THREE cells were green while exercising nothing**, one found here and two by the audit —
     auth-refresh refuses early without a `storage_state` (so `_form_login` never ran); `dry_run`
     aborted at pre-flight because the seeded step lacked `mutating=True` (so no browser opened, and its
     only assertion was `rep is not None`, which an aborted report satisfies); and a door called with a
     bad kwarg raised `TypeError`, which the harness accepted as a legitimate refusal. Each premise is now
     COUNTED, and `TypeError` fails the cell instead of passing as a refusal.
  4. **The corpus sweep's stated advantage over `drift_bench` did not exist.** It argued it beat the
     bench by handing the engine a RAISING provider where the bench passes `provider=None`. But
     `flow.py:171` is `heal_provider = provider if mode in ("auto", "repair") else None`, so in
     `mode="replay"` the engine gets None whatever the caller passes — reproduced directly
     (`replay -> ['NoneType']`, `repair -> ['_Exploding']`). The claim is restated to what the sweep
     actually proves, and the STRUCTURAL fact (the nulling holds for all 97 rows) is now asserted
     rather than assumed.

  **What ships:** the derived provider-binding patch (now including `build_client`) plus an anti-vacuity
  test on the STUB ITSELF and the choke-point scan above; a 7-door entry-point sweep — with the measured
  note that only four of them (`replay`, `run_batch`, `run_all`, `run_cached`) actually replay, so the
  list does not imply parity — plus a coverage guard that fails when a new public async verb in
  `flows.py` is neither swept nor exempted with a reason; the auth-refresh recovery path; 97
  drift-corpus rows in `mode=replay` (`per_k=1`, which drops only generated COSMETIC compositions and
  keeps every scenario and every semantic/conflict/escalate/residual-hole row — printed by the test,
  because a silent cap reads as full coverage); and #2 as two DERIVED properties (no failure report
  without a reason; every typed error a distinct machine-readable code).

  **The fix is verified the only way that counts: the violation was ARMED and the cells went red.** With
  a replay constructing a real client, 10 cells fail — including the corpus sweep, `dry_run` and
  `run_batch`. The audit measured **zero** failures for the same violation before.
- **S15. Pin every remaining unpinned residual** (AB-2..5, 7, 9, 11, 13 — minus any closed by Phase 3
  decisions). Each pin DEMONSTRATES current behaviour and names the residual, so any silent change in
  either direction fails a test. AB-10 (possibly-dead belt-and-braces branch): coverage probe; delete
  if dead.
- **S16. ✅ DONE in 0.136.0 (reshape-plan 0.6) — the nine known mutants are re-runnable for the
  first time since 0.75.0, and the registry LIST is derived rather than typed.**
  `tests/mutations/known_nine.py` + `scripts/mutation_sweep.py` +
  `.github/workflows/mutation-sweep.yml` (weekly, plus `workflow_dispatch`). **9 killed, 0
  survived** — and it is a re-measurement rather than a re-run, because SIX of the nine sites
  had moved (the R3.2 refusal migrated out of `flows.py` into `flow._learn`, so it now covers
  the two callers that never reach the `flows.py` surface) and were re-expressed against the
  property rather than the 2026-06 line number.

  **What it does NOT re-measure**, stated because the 0.75.0 record is quoted in three
  documents: "four of the nine were caught by exactly ONE test". Each mutant here is scored by
  ONE killer FILE, chosen for it, so this proves each is still killed and says nothing about by
  how many cells. The generic-operator half is sized, refused and filed as **R4.108** — 2848
  mutants, 70.4 h serial against the fast tier, and a narrow killer measured to manufacture
  false survivors at ~38% on the best-case file, all six of which the fast tier kills.
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

  **AND THE SAME FAMILY PRODUCED R4.36, NARROWED (not closed) in 0.105.0 — R4.26's residual.**
  R4.26 proved a timer is not a turn boundary and added `inDispatch()`; it left the boundary a timer and
  made the new signal a CONJUNCT, which closes only the case where the write leaves in a BARE task. A
  write leaving inside a LATER, UNRELATED dispatch while the overdue reset is still pending satisfies
  both conjuncts. Fixed by asking WHICH dispatch — the commit's own event, plus the platform-closed set
  of activation-caused events (`submit`/`reset`/`formdata`) — so native submit, `requestSubmit()` and
  Enter-submit stay attributable while `input`/`change` do not.

  **It did NOT close the finding, and the slice's own audit is what established that.** The whitelist
  matches on the event's TYPE NAME with no provenance check, so the same field shape with
  `form.requestSubmit()` in place of `fetch` — the mainstream autosave idiom — is still credited to the
  benign click, and a BARE TASK that synthesises a `submit`/`reset` re-enters attribution entirely
  (**R4.38**). The narrowing is real and strictly safe (`NEW ⊆ OLD`), but "fixed" was the wrong verdict
  and a closed finding stops being looked at.

  Two things carry forward. The deterministic-harness lesson held a third time: the field shape is a
  scheduling race, and it was reproduced 4/4 by making the reset overdue on purpose rather than by
  waiting. And **the matrix could not have caught it, because the defect was encoded in the
  instrument**: its per-cell premise asserted `inDispatch is (timing not in _MUST_REFUSE)` — "a refused
  write is one that left outside a dispatch" — which is the exact belief the finding walks through.
  Shape and verdict are now declared independently, so a cell can exist for every combination.

  Original bullet follows. Found while
  landing S1: it fails intermittently in the full suite and never in isolation (5/5 isolated, 3/3 whole
  file both with and without the S1 diff, 1 failure inside the 781-test run). It guards the DEFERRED
  -write refusal — the exact property **S6** uses as its oracle — so **S6 must not be built on it until
  it is deterministic**. Reproduce under artificial load first; do not silence with reruns, and do not
  weaken the production bound (the register's earlier de-flake work set that rule).

- **S18. R4.5 — a page-synthesised commit launders a deferred write. OPEN, HIGH, inviolable #3, and
  ⛔ BLOCKED BY `D5` until a new SENSOR CLASS is measured.** It owned no slice until now, which is how it
  spent a round filed as "parked" while being live against shipped code — the same drift the R4 STATUS
  INDEX was added to stop.

  **State.** Reproduced 10/10 against 0.89.0 on the learn path and, worse, on `record()`, where it caches
  a step no human performed and replay double-submits while reporting success. Guarded by strict-xfail
  RED tests in 0.90.0 (both dispatch shapes + the record path), which the anti-wallpaper guard in
  `tests/test_register_count.py` now ties to this finding's OPEN status.

  **Two attempts are spent, so D5's two-strikes gate applies to the next one.** Attempt 1 (require the
  commit's own `isTrusted`) over-refused an APG `role=button` activated by ENTER — recordable by mouse,
  refused by keyboard — which is D0's shape landing on accessible widgets with no remedy. Attempt 2
  (require a trusted activation to have begun the turn) is forgeable: `form.requestSubmit()` and
  `checkbox.click()` both yield TRUSTED events from a bare task, on markup the page creates at runtime.
  **`isTrusted` is not a user-presence signal — it means the user agent fired the event, and a page can
  make the user agent fire events.** That one sentence rules out the whole family, so a third variant of
  "read a better bit in the page" is attempt 2 again, not a third attempt.

  **What the next attempt must do**, per D5: change the sensor class — inference → a human's verdict, or
  inference → a loud refusal — and measure it against the existing artifacts BEFORE building it (the
  shape set in the register's fix section, both attack shapes, and the APG / framework-forward /
  design-system-dropdown population that killed attempt 1). Ruled out already, so nobody re-spends them:
  `navigator.userActivation` (reads true on a blank page with no interaction ever) and driver-side
  tagging (works for a scripted `demo=`, but `flow record`'s real surface is a headed human whose input
  is trusted and indistinguishable from `Input.dispatchMouseEvent`).

  **Sequencing — WRITTEN, THEN MEASURED, AND IT DID NOT HOLD.** The original line read: *"the
  human-verdict sensor is the write-provenance + annotation work — the same primitive D0's lever (ii)
  and R4.27's disposition need — so S18 should follow it rather than duplicate it."* Both halves shipped
  (0.92.0 provenance, 0.93.0 `flow mark`). Running the three populations against 0.93.0 instead of
  reasoning from the vocabulary:

  * **R4.27 — not disposed of. 12 of 12 demotions REFUSED**, because all twelve carry `wire` and the verb
    declines to overrule a POST that was watched leaving the browser. Correct refusal, closed nothing.
  * **R4.5 / `record` — not repairable.** The harm is a phantom step; `flow mark` cannot change step
    membership, so the repair is not expressible, not merely refused.
  * **R4.5 / `learn` — repairable, and unreachable.** Promotion is allowed (an earlier claim that it
    would be refused as a gateless write was wrong — `_learn` always sets `precond_fingerprint`), but
    the laundering arm is silent, so nothing tells the operator to run it.

  **So the prerequisite is spent and S18 has not moved.** The lesson is a gate on the next attempt, not
  just bookkeeping: *a human-verdict sensor is a verdict verb PLUS a trigger that puts the question in
  front of a human.* Specifying only the verb builds half a sensor, which is how a landed prerequisite
  can leave the finding exactly where it was. S18's next attempt names the trigger first. Numbers and
  the per-path table are in R4.5's and R4.27's entries in `docs/open-defects.md`; the R4.27 half is
  pinned end-to-end by `tests/test_annotation_disposition.py`, in both directions.

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
