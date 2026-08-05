# The correctness survey — the inventory the plan is built on

Produced 2026-08-04 at v0.75.0 (main; all 118 PRs merged; no open PRs) by five parallel
readers over the register, the PR history, the CI/eval machinery, the accepted residuals in
`src/`, and the user-facing surface. Every claim was verified by reading or running and cites
file:line or a PR number.

**This file is a checklist with teeth.** `docs/correctness-plan.md` exit criterion 6 says every
identifier below must end that plan either FIXED, PINNED by a test, or carrying an argued
disposition in `docs/open-defects.md`. Nothing here may be closed by silence.


---

## PR timeline #100–#118 reconstructed from `gh pr list` + git log; all 118 PRs are MERGED, zero OPEN PRs

_PR timeline #100–#118 reconstructed from `gh pr list` + git log; all 118 PRs are MERGED, zero OPEN PRs. Branch feat/shared-causal-attribution has ZERO commits ahead of main — all its work is uncommitted (modified: pyproject.toml, src/ultracua/flow.py, src/ultracua/recorder.py, tests/test_unattributed_write.py, uv.lock; new: src/ultracua/attribution.py + 7 new test files incl. 5 refuter/repro tests) — it is the next attempt at R3.2 causal attribution. The dominant systemic pattern, verified in docs/open-defects.md: three adversarial audit rounds found ~44 defects (~20 round 1, 10 round 2, 11+1 round 3), NOT ONE discovered by the test suite (docs/open-defects.md:648-651); fix code has been the defect source three rounds running, with round-3 defect density in fix code ~3x the code it replaced (line 711); the only successful revert (attribution redesign inside 0.73.0/PR #116) was fully green — 754 tests, drift_bench clean, regression tests verified failing pre-fix — and still critically wrong, caught only by an adversarial pass aimed at the fix itself (lines 922-925)._


### `timeline-100-118` (info)

**What.** PR timeline #100+: #100 drift-bench v2 (0.61.0, recovery-ladder metric); #101 close half positional-css retarget, measure the open half (0.62.0); #102 escape page-controlled id in Tier-1 selector (0.62.1); #103 H5 dry-run replay, hold every write (0.63.0); #104 fix 4 silent-wrong ways (0.64.0, round-1 fixes begin); #105 create docs/open-defects.md register; #106 fix 4 trust-state lies (0.65.0); #107 write SIGNAL detect/persist/refuse (0.66.0); #108 secrets never to disk/model (0.67.0); #109 Tier-1 fuzzy anchor no longer outranks exact (0.68.0); #110 de-flake write-settle test (0.68.1); #111 CI Windows-flake measurement; #112 recorder couldn't see form POST, dropped a write (0.69.0, closed A2, last of round 1); #113 perf: shared Playwright driver (0.70.0); #114 row guard checked EXISTS not BOUND — A1 never actually closed (0.71.0, closed round-2 R1/R2); #115 round 2's remaining eight R3–R10, mostly holes in round-1's own fixes (0.72.0); #116 round-3 response: R3.1+R3.4 redesigned, R3.2 attribution attempt reverted (0.73.0); #117 unattributed wire write must refuse, in the mechanism (0.74.0, R3.2 safety half); #118 write-safety inviolable as property test (0.75.0). main = merge c7b4119 of #118.


**Where.** gh pr list (all MERGED)


**Evidence.** gh pr list --state all --json output; git log --oneline


### `prs-later-found-defective` (high)

**What.** PRs whose fixes were later found defective: round-1 fix PRs #104–#112 (0.64.0–0.69.0) — round 2 found 10 more findings 'mostly holes in round 1's own fixes' (PR #115 title, register line 5); PR #114 (0.71.0 row-identity fix) — round 3's CRITICAL R3.1 is that very fix reintroducing R2's mechanism through the branch R2's fix promoted to first priority (lines 715, 931); PR #115 (0.72.0) — R3.2 regression: R4's fix turned 'credited the wrong step' into 'credits no step at all' (lines 717-718, 964). Round 3 audited exactly the 387 lines rounds 1–2 added (baseline v0.70.0, line 706-708), found 11, refuted NONE.


**Where.** docs/open-defects.md:5-18,66,706-726,931


**Evidence.** register lines cited; PR #114/#115/#116 titles corroborate


### `reverts-and-pullbacks` (high)

**What.** Reverts/pullbacks in project history: (1) R3.2 attribution redesign — built, green on 754 tests + drift_bench, every regression test verified to fail pre-fix, then an adversarial pass ON THE FIX reproduced a CRITICAL regression (real commit cached as a read = silent-wrong, 'strictly worse than R3.2 itself which at least failed loud') and it was reverted before merge; its 8 tests removed, but the fixture that caught the regression is preserved verbatim in the register's R3.2 section and 'should be the first test written' for the next attempt (lines 784-785, 918-920). (2) PR #52: reflexion retry (added in #51) measured net-harmful, removed. (3) PR #53: 0-LLM DOM list extractor reviewed unsafe, pulled back. (4) PR #7: stuck-termination guard from #6 lost in stacked merge, restored. The register also rules out every purely temporal attribution design (window-based collapse analysis, lines 764-789).


**Where.** docs/open-defects.md:728-813,918-925; PR #116, #52, #53, #7


**Evidence.** register lines cited; PR titles #7, #52, #53


### `systemic-lesson-fix-code` (critical)

**What.** Systemic lessons: (a) all ~44 defects across 3 rounds came from adversarial audits, none from the suite — the suite is regression-shaped and cannot fail for a missing guard; (b) fix-code defect density ~3x the replaced code; three round-3 findings are the same shape as the finding they were fixing, one level down (a rowIdOf duplicate that outranks it R3.1, a null-check siblings have and the new caller lacks R3.4, a guard on capture but not sibling describe R3.6); (c) the mandated response shape is 'enforce the invariant ONCE' redesigns, not per-branch patches — patching each item is what produced round 3; (d) audit the fix before the PR, since green tests + clean bench + failing-pre-fix regression tests did not catch a CRITICAL regression; (e) loud-and-useless beats silent-and-wrong was the explicit revert criterion.


**Where.** docs/open-defects.md:648-651,711-726,922-925


**Evidence.** docs/open-defects.md lines cited


### `r32-current-state` (high)

**What.** R3.2 status: safety half closed in 0.74.0 (PR #117) — an unattributed wire write now refuses in the mechanism, and it 'was worse than this register said'; but the rule that replaced attribution is a CONSISTENCY check, not an attribution — a multi-step write flow with its commit on a later step remains unattributable while classify_mutation passes, so R3.2's residual is open (lines 831-862). The uncommitted branch adds src/ultracua/attribution.py, modifies flow.py + recorder.py, and carries 5 adversarial refuter/repro tests (test_zz_refute_seqcollide[.py/_e2e.py], test_zz_refuter_wrongate.py, test_zz_repro_synthclick.py, test_zz_repro_synthetic.py, test_zzz_refute_replace_gate.py) — consistent with the register's demand to attack the fix pre-merge and with CLAUDE.md pointing at the causal signal recorder.py has had since Phase I.


**Where.** docs/open-defects.md:813-862,964; uncommitted work on feat/shared-causal-attribution


**Evidence.** git status --short; register lines cited


### `open-prs-and-branches` (info)

**What.** OPEN PRs: none (gh pr list --state open returns empty). Branches: feat/shared-causal-attribution has 0 commits ahead of main (work is entirely uncommitted). git branch --no-merged main lists 4 others (feat/write-safety-classification, fix/provider-live-path, fix/stuck-termination, phase-1-flow-cache) but each tip commit's message matches a June-era PR already merged under a different SHA (#45's grounding-hygiene, #42's variance benchmark, #6's stuck-termination, #2's MiniWoB) — stale local artifacts of rebased/stacked merges, not live work. Remote branches beyond main: fix/live-llm-path, fix/round3-redesign, fix/stuck-termination, fix/unattributed-write-refusal, phase-1-*, phase-2/3/4-*, restore/stuck-termination, test/write-safety-invariants — all correspond to merged PRs.


**Where.** gh pr list --state open; git branch -a; git log main..HEAD


**Evidence.** command outputs in session; git log main..branch tips


### `open-defect-count` (medium)

**What.** Nine round-3 findings remain OPEN per CLAUDE.md (of 11 found + R3.12 recorded as NOT reproduced): R3.1 and R3.4 closed by redesign in 0.73.0, R3.2 half-closed (safety in 0.74.0, attribution open). Verified-open examples in register: R3.2 attribution residual (line 964); R3.3 — landed rail arms ledger for WriteReadbackError but not sibling ShapeDriftError, so CLI resume re-fires a committed write (line 1002 — a write-safety inviolable-3 exposure). R3.4's pre-fix failure mode (fleet fires undeclared write and prints [OK]) is documented at line 1040 as fixed.


**Where.** CLAUDE.md; docs/open-defects.md:5-11,1002,1040


**Evidence.** register lines cited; CLAUDE.md 'Nine remain open'


---

## docs/open-defects.md (1351 lines, read end to end) at v0.75.0: Rounds 1-2 (30 findings) are all FIXED

_docs/open-defects.md (1351 lines, read end to end) at v0.75.0: Rounds 1-2 (30 findings) are all FIXED. Round 3 filed 11 findings (none refuted) plus R3.12 (added later, NOT reproduced) and R3.13 (measured, recorded NOT fixed). Of the original 11: R3.1 and R3.4 were FIXED via redesign in 0.73.0; R3.2's SAFETY half was closed in 0.74.0 (consistency check in _learn) but its attribution rule is STILL OPEN with every purely-temporal design measured wrong and ruled out. Precise open count: 11 items — 9 audit findings (R3.2, R3.3, R3.5, R3.6, R3.7, R3.8, R3.9, R3.10, R3.11) + R3.12 + R3.13. Severity spread: 4 high (R3.2, R3.3, R3.5, R3.6), 4 medium (R3.7, R3.8, R3.12, R3.13), 3 low (R3.9, R3.10, R3.11). All reproduced by an independent refuter with an executed probe EXCEPT R3.12 (code-reading only, register's own rule demands reproduction before fixing) and R3.13 (measured directly against shipped 0.73.0 and 0.74.0). The register also carries named residuals not filed as findings (R5 entry-page confirm probe, R3.2 deferred-into-mutating-neighbour, GraphQL read-POST over-count, H9 contracts gated on spec.mutate is None, token-less row, uncorroborated Tier-1 role+name~). Process conclusions PROCESS-1..7 below are the register's own, quoted from open-defects.md:703-927 and 624-694._


### `R3.2` (high)

**What.** Write attribution: _write_owner credits NOBODY for essentially every step after the first (candidate set {i-1,i} inside the 2s grace tail -> owner -1), so a formless commit on step>=1 caches ungated/un-keyed; a 0.73.0 redesign (drain-based exclusive intervals) was built, measured to credit the WRONG step silently (defer=450ms -> mutating_steps=[1]), and REVERTED pre-merge. The SAFETY half was closed in 0.74.0 by a wire-vs-classifier CONSISTENCY check moved into _learn (the mechanism, covering run_cached/daemon), but attribution itself is still wrong.


**Where.** src/ultracua/flow.py:190-207 (_write_owner), :277-280, :412-416, :468-478; consistency rule now in _learn; register: docs/open-defects.md:749-886, 964-1000


**Evidence.** Reproduced by independent refuter; revert fixture measured on branch (defer table at open-defects.md:775-779); safety-half measurement at :821-829. Residual stated at :861-864: a write deferred into a classifier-mutating neighbour still passes the consistency check.


**Disposition / fix shape.** Register's fix shape (:794-811): attribution needs a CAUSAL signal, not temporal — every purely temporal rule is ruled out by measurement. recorder.py has the signal since Phase I (__ucturn sync-turn commit counter + attributedSeq returning a commit only for __ucturn===1). Option 1 (cheapest, correct): adopt the recorder's rule on the learn path — deferred writes become unattributed and refuse, matching record(); debounced commits must be record()ed, already the documented answer. Option 2: drain until the act's own scheduled setTimeouts have run (attributes debounced commits correctly) but recorder.py:181-185 already rejected patching setTimeout as too invasive — overruling needs its own argument and audit. Either way share ONE implementation with the recorder (the R3.1 lesson). First test: the preserved revert fixture at open-defects.md:772-779.


### `R3.3` (high)

**What.** The landed rail arms the ledger for WriteReadbackError only; ShapeDriftError — reached with strictly STRONGER evidence of commit (confirm transitioned AND readback read cleanly) — inherits landed=False, so no ledger line is written, _record_run(ok=False) fires, and the CLI prints a --resume command that re-fires the committed write on every resume (deterministic, since shape drift is deterministic).


**Where.** src/ultracua/flows.py:1488 (shape gate in _attempt_replay), :398 (landed=True on WriteReadbackError only), :2564-2565 (run_batch arming), mcpserver/server.py:283-284, cli.py:847-849; register: open-defects.md:1002-1038


**Evidence.** Reproduced by independent refuter (MCP arm not separately probed — predicate byte-identical). Note the shape gate is NOT fenced with 'spec.mutate is None' unlike the H9 contract/magnitude gates directly below it, and _learn_once seeds meta.shape for write flows (flows.py:1093-1097).


**Disposition / fix shape.** Register gives no explicit one-liner but the mechanism implies: arm landed (and the ok=True _record_run treatment) on the shape-drift return for write flows, or fence the shape gate off writes as the contract gates already are — and per CLAUDE.md, add a dimension to test_write_safety_invariants.py, check the MCP sibling.


### `R3.5` (high)

**What.** The is_write_flow consolidation left a FIFTH surface: replay()/_preflight_row still compute is_mutate = spec.mutate is not None, so an UNDECLARED write (spec.mutate=None, cached step mutating=True) gets retry_ok=True on auth_refresh and the whole flow replays from start_url, re-actuating the commit — measured, the commit POSTs twice with byte-identical Idempotency-Keys, on an endpoint that never asked for that header. Declared writes refuse this retry in as many words.


**Where.** src/ultracua/flows.py:1620 (_preflight_row), :1901 (replay), :1979-1981 (retry_ok); reachable via 'ultracua flow replay --name X' (cli.py:247) and the flows.replay() API; register: open-defects.md:1071-1099


**Evidence.** Reproduced by independent refuter, double-POST measured. Two sub-arms explicitly excluded: on_drift='relearn' refused for approved flows; run_all/run_batch/MCP now refuse the flow outright — reachable surfaces are flow replay and the library API only.


**Disposition / fix shape.** Convert the funnel surface (replay/_preflight_row) to is_write_flow(spec, cached) — the exact conversion 0.72.0 did on the four surfaces that funnel THROUGH it. Guard-in-the-mechanism, not per-caller; siblings already converted show the shape.


### `R3.6` (high)

**What.** Redaction covers the Observation (capture) but not LocatorSpec (describe): specOf captures accessible name, 60-char row anchor text, and anchor_id ('href:'+first href incl. query string) verbatim from the live page — the exact 'Copy sk-live-...' / '?api_key=' strings the 0.72.0 fix cites — and learn AND heal persist them plaintext into the flow cache JSON (no chmod), which flow inspect and flow approve --all then reprint. Violates the stated rule that secrets are never serialized/logged/written to disk.


**Where.** src/ultracua/locators.py:173-188 (_SPECOF_JS/specOf), :202-207 (describe); persisted flow.py:347/:601 (learn), :1198/:1280->:916 (heal); cache.py:249 (put, no chmod); cli.py:294-296; register: open-defects.md:1101-1131


**Evidence.** Reproduced by independent refuter. The register names this its own recurring shape: guard applied to the mechanism (capture) and never to its sibling (describe). The heal prompt is correctly [REDACTED] so every observable signal says redaction worked while disk carries plaintext.


**Disposition / fix shape.** Thread session.redact/_secret_values(spec) into describe()'s capture path (the sibling capture already has it); consider chmod on FlowCache.put like audit.capture's 0o700 dir. Check the third transcription sites while there.


### `R3.7` (medium)

**What.** _ROW_OF_JS does not mirror anchorOf's walk despite its comment claiming 'MIRROR anchorOf's walk exactly': anchorOf skips a row-like container with EMPTY collapsed text and keeps climbing; _ROW_OF_JS stops at the first li/tr unconditionally. A nested icon-only actions list (row > ul.actions > li > icon control) makes capture record the OUTER row's identity while the guard measures the INNER li — got != spec.anchor_id, resolve returns None, every replay refuses on a page that has not drifted. Fail-loud (no inviolable breached) but the flow is permanently unusable, the error accuses phantom drift, and with a heal provider every replay of that step routes into an LLM call.


**Where.** src/ultracua/locators.py:296-303 (_ROW_OF_JS climb) vs :164-167 (anchorOf's 'if (t)' text condition); broken claim at :293-298; register: open-defects.md:1133-1158


**Evidence.** Reproduced by independent refuter. Authoring-silent because learn/record actuate via [data-ultracua-ref=...] and never call resolve; only replay binds through resolve (flow.py:1082).


**Disposition / fix shape.** Make _ROW_OF_JS carry the same non-empty-text condition — and per the R3.1 lesson, prefer sharing one JS walk between anchorOf and _ROW_OF_JS rather than fixing the transcription (this is exactly the two-transcriptions shape R3.1 closed for rowIdOf). Re-run drift_bench; a probe exercising only refusal cannot distinguish a working guard from a broken one.


### `R3.8` (medium)

**What.** The R10 transient-retry fix does not hold on the WRITE path: _update_meta = _load_meta -> mutate -> _save_meta with no check for quarantine==meta_unreadable between them, so after 3 failed read attempts the poisoned meta is serialised OVER the healthy sidecar — with NO .corrupt.* backup (the new path skips _preserve_corrupt, unlike the code it replaced) while the log asserts 'leaving the file untouched'. One transient WinError 32 during _record_run (every replay) irrecoverably destroys approval, contracts, shape, steps_hash, read_pin. The release() variant saves a completely blank meta, silently forgetting an H9 quarantine.


**Where.** src/ultracua/flows.py:616-623 (OSError fall-through returning _poisoned_meta) + :527-535 (_update_meta), reached from _record_run (:683), release (:1205), approve (:1164), _quarantine (:1213), unapprove (:1177), relearn pin-clear (:2023); register: open-defects.md:1160-1193


**Evidence.** Reproduced by independent refuter. The register states the round-2 summary claim at open-defects.md:306 ('a transient meta read retries and never destroys a healthy sidecar') is false as measured; the quarantine text instructs inspecting a .corrupt.* copy that is never created on this path.


**Disposition / fix shape.** Refuse to _save_meta a meta whose quarantine is meta_unreadable (fail the update loud, leave the file alone) — enforce once in _update_meta, the mechanism all six callers go through, not per caller. Restore _preserve_corrupt semantics on any path that does overwrite.


### `R3.9` (low)

**What.** The new unconditional undeclared-write skip in run_all is invisible to the documented cron contract: FleetRun(status='skipped') feeds neither the exit code (exits 0) nor --alert-webhook (fires only on failed). First skip class a flow can enter with NO human act — _author_steps' wire promotion marks any click-triggered non-telemetry POST mutating, incl. GraphQL/RPC read-POSTs (the ordinary SPA read shape), so a re-learn after a redesign silently retires a monitoring flow from cron while every automated signal reports green. No escape hatch exists for a read (declaring spec.mutate demands a confirm a read cannot satisfy; --include-writes correctly refuses).


**Where.** src/ultracua/flows.py:2317-2329 (the skip) vs src/ultracua/cli.py:750 (webhook gate) and :752 (exit code); register: open-defects.md:1195-1219


**Evidence.** Reproduced by independent refuter (symptom real, stated cause corrected by the refuter — one of two round-3 findings so annotated).


**Disposition / fix shape.** Make 'skipped-because-undeclared-write' visible to the cron contract (count toward exit/webhook or a distinct alerting channel); interacts with R3.13's quarantine idea and with the GraphQL residual — register forbids fixing the GraphQL case by narrowing what counts as a write (open-defects.md:882-886).


### `R3.10` (low)

**What.** The new redaction (extractor scrub _redacted_body_text and the five-field snapshot.capture loop) has no minimum-term-length floor, unlike audit._redact — the sibling it cites by name — which refuses terms <4 chars. _secret_values includes the LOGIN USERNAME; a short/common-substring term (numeric id '1234', handle 'bo', a PIN) shreds page text/url/title/element names before the strong-tier extractor sees them ('Open tickets: 12345' -> 'Open tickets: [REDACTED]2345'), yielding mangled values or found=False on data plainly on the page.


**Where.** src/ultracua/flows.py:718-738 (_redacted_body_text) and src/ultracua/snapshot.py:278-295, vs sibling src/ultracua/audit.py:194-201 ('if val and len(str(val)) >= 4'); term list flows.py:1291-1303 (_secret_values); register: open-defects.md:1221-1244


**Evidence.** Reproduced by independent refuter (symptom real, cause corrected); the refuter claims only the deterministic input corruption — the wrong-answer half is LLM-mediated and was not provable key-lessly.


**Disposition / fix shape.** Carry audit._redact's >=4-char floor into both new scrub sites — or better, share the one _redact implementation across all three (the R3.1 one-implementation lesson).


### `R3.11` (low)

**What.** Narrowing except Exception to (ValueError, UnicodeDecodeError, OSError) broke _load_meta's explicit 'It never raises' contract: json.loads raises RecursionError on deep nesting, which now propagates straight out of health() (no try/except of its own) — flow status and the MCP tools/list loop traceback on ONE bad meta file, losing the entire fleet view, the specific outcome both docstrings promise cannot happen. Fail-loud, low realistic probability (locally written file), a regression this diff introduced.


**Where.** src/ultracua/flows.py:608-615 (the narrowed except pair); contract at :587; health() at :1409 with docstring :1412-1413; register: open-defects.md:1246-1265


**Evidence.** Reproduced by independent refuter.


**Disposition / fix shape.** Add RecursionError (or route unexpected exceptions) to the corrupt-meta path so the never-raises contract holds — enforce at _load_meta, the mechanism, not in each caller.


### `R3.12` (medium)

**What.** DryRunArbiter's act window has the SAME single-slot overlapping-tail shape R4 fixed in _author_steps: open_window overwrites one slot per mutating step, close_window sets a 2s (write_window_ms) tail, so on a multi-write flow a write deferred from commit A arriving inside commit B's slot is recorded as HeldWrite(step=B, intent=B.intent) in the report a human approves from. Ranked medium not high: dry_run HOLDS every write so nothing mis-fires — damage confined to the report — and it is partly self-revealing (idempotency_key comes from real headers, step/intent from the window). STATUS: NOTICED while redesigning R3.2 by applying the register's own sibling rule; explicitly recorded as NOT REPRODUCED — a code-reading finding only.


**Where.** src/ultracua/dryrun.py:249-263 (open_window/close_window/_state), called from src/ultracua/flow.py:1092 and :1216-1217; register: open-defects.md:1272-1304


**Evidence.** NOT reproduced end to end — the register's own rule ('a fix built on a wrong diagnosis is worse than none; two earlier items were misdiagnosed exactly this way') requires a repro first. Prescribed repro: two-commit flow, first commit POSTs on ~100ms setTimeout, dry-run it, check whether the held write's step/intent name commit 1 or commit 2; if the arbiter's existing drain(write_settle_ms) already covers the gap, REFUTE and record it as such.


**Disposition / fix shape.** Register's fix shape if confirmed (open-defects.md:1301-1304): close_window already runs after a drain, so the exclusivity mechanism exists — bound attribution to the drained window rather than a 2s tail that outlives it. 'Do not add a second slot; that is the patch R4 tried.' Also interacts with whatever causal design closes R3.2 — same attribution family.


### `R3.13` (medium)

**What.** A learn refusal is NON-TERMINAL: a refused flow caches nothing and records nothing, so every mode='auto' invocation re-authors, re-drives the browser, and the page re-fires the same write un-keyed — a strictly ACROSS-INVOCATION hazard (best-of-N within one call already breaks on performed_write). STATUS: confirmed 2/2 by the audit, then MEASURED against shipped code; RECORDED, NOT FIXED in 0.74.0; explicitly NOT a regression — measured identical before and after: 0.74.0 = 3 runs, 3 POSTs, 0 Idempotency-Keys, refusing loudly; 0.73.0 = 3 runs, 3 POSTs, 0 keys, reporting SUCCESS. 0.74.0 made the hazard visible, not smaller.


**Where.** src/ultracua/flow.py (_learn's refusal), src/ultracua/flows.py:_learn_once; register: open-defects.md:1306-1349


**Evidence.** Measured directly (table at open-defects.md:1321-1329). The entry also records its own earlier draft was WRONG — it reproduced with a form-POST shape that does not refuse under the shipped consistency rule, proving nothing about shipped code; re-measuring on a shape that actually refuses changed the conclusion from 'widened by 0.74.0' to 'unchanged by it'.


**Disposition / fix shape.** Register's fix shape (open-defects.md:1344-1349): a persistent refusal marker via FlowMeta.quarantine (machinery exists — _poisoned_meta already uses it for unreadable sidecars), so the second invocation refuses WITHOUT driving a browser. A real behaviour change (quarantine feeds health(), MCP tool list, run_all skip logic, CLI) — wants its own slice and its own audit. Check siblings: record() refuses the same class with the same non-terminal property.


### `PROCESS-1` (info)

**What.** Defect density in fix code is ~3x the code being fixed, AND RISING: round 2 found 10 defects in ~1059 lines of fix code; round 3 found 11 in 387, none refuted. 'The fix-audit-fix loop is not converging on its own.'


**Where.** docs/open-defects.md:6-8, :710-713


**Evidence.** Register's own counts, verified against the round scoping statements (round 3 scope = git diff 9d7de9c..HEAD, 387 insertions, ALL fix code).


### `PROCESS-2` (info)

**What.** Patch-on-patch is the thing to be most suspicious of: three round-3 findings are the SAME shape as the finding they were fixing, one level down (R3.1: per-branch test not copied to the branch that outranks it; R3.4: null-check the sibling callers have and the new one does not; R3.6: guard applied to capture, not sibling describe). 'Adding another patch to each is what produced this round. Prefer changing the shape so the invariant is enforced ONCE.' Round 3's response was accordingly three REDESIGNS, not patches.


**Where.** docs/open-defects.md:715-726, :728-734


**Evidence.** Register's own conclusion; the two shipped redesigns (R3.1 single _ROWID_JS + discrimination rule; R3.4 CacheUnreadableError type) are documented at :735-747 and :888-911.


### `PROCESS-3` (info)

**What.** AUDIT THE FIX, not just the code it fixes — pre-merge, not one release later. The reverted R3.2 redesign was fully green (754 tests, drift_bench byte-identical to baseline, every regression test verified to fail against pre-fix source) and still critically wrong (silently credited the wrong step); only an adversarial pass aimed at the FIX found it. 'Three rounds in a row where fix code was the defect source, and the first time the check that caught it ran before the merge rather than one release later.'


**Where.** docs/open-defects.md:922-927; CLAUDE.md restates it


**Evidence.** The revert record at :749-792 with the measured defer table.


### `PROCESS-4` (info)

**What.** The 757-test suite is regression-shaped, not weak — measured: 9/9 mutation defects caught, entry-point coverage broad (88 run_cached / 131 replay / 120 record call sites), the 'unfalsifiable' AST flag was itself wrong. But mutation testing only probes guards that EXIST, and every shipped defect was a guard that was MISSING: ~44 findings across three audit rounds, NOT ONE discovered by the suite. Four of nine mutations were caught by exactly ONE test — a guard is only as covered as the one test written beside it, and one such test passed against pre-fix source until corrected.


**Where.** docs/open-defects.md:624-657


**Evidence.** The measurement section header says 'measured, 0.75.0'; corroborated by CLAUDE.md's summary of the same numbers.


### `PROCESS-5` (info)

**What.** Reproduce before fixing, AGAINST THE CODE YOU ARE SHIPPING: A1 was misdiagnosed (blamed anchor never ran); R3.13's first draft used a shape that does not refuse under shipped code and 'demonstrated nothing'; R10's stated cause was corrected by its refuter; the round-2 fix for R1 'appeared to work for the wrong reason' (mis-escaped selector made every row anchor refuse — probes showing 'refuses, no POST' looked like a pass, only drift_bench caught it). Corollary: a probe that only exercises the refusal path cannot tell a working guard from a broken one — re-run the bench.


**Where.** docs/open-defects.md:88-91, :296-303, :1294-1299, :1337-1342


**Evidence.** Four independent instances recorded in the register itself.


### `PROCESS-6` (info)

**What.** The single most-repeated defect shape: a guard living in a wrapper or on one path instead of in the MECHANISM the paths share. Round 1: A1/A3/A6/A7/A9/A12/A13 (table at :98-105). It struck the fixes too (R6 fourth transcription, R3.5 fifth surface) and even the guard FOR it (the 0.74.0 refusal lived in _learn_once, covering one of three callers, and was moved into _learn). Prescriptions: push guards down to the mechanism; when fixing, check siblings; guard at the FUNCTION boundary covering reads added later (the R3.4 run_all lesson at :905-911); write-safety fixes add a DIMENSION to tests/test_write_safety_invariants.py rather than a bespoke test.


**Where.** docs/open-defects.md:93-108, :262-263, :851-854, :905-911, :696-700


**Evidence.** Register's structural-finding section plus three later recurrences it documents.


### `PROCESS-7` (info)

**What.** Domain-specific negative results the plan must respect: (a) every purely temporal write-attribution rule is RULED OUT by measurement — 'there is no constant that is both long enough to catch a deferred write and short enough not to swallow the next step'; attribution needs a causal signal (recorder's __ucturn/attributedSeq); (b) 'loud-and-useless beats silent-and-wrong' — the stated reason for the revert; (c) do NOT fix the GraphQL read-POST over-count by narrowing what counts as a write — a design panel flagged any allowlist/shape-probe/heuristic as reintroducing the silent-wrong being removed ('anything that decides a POST is benign without proof will eventually decide a commit is benign'); (d) both halves of the write-safety property are load-bearing — without the 'must remain learnable' clause it is satisfied by refusing everything, a regression that actually shipped (8/24 matrix cells caught it); (e) 'a matrix is only as good as the axis you forgot' — print what each cell exercised before believing it.


**Where.** docs/open-defects.md:787-792, :785, :882-886, :661-694


**Evidence.** Each is stated with its measurement in the register; the refusing-everything regression is documented with the 54-pass/8-fail comparison at :672-680.


---

## Verification machinery inventory (branch feat/shared-causal-attribution, pyproject at 0.76.0): tests/ has 84 t

_Verification machinery inventory (branch feat/shared-causal-attribution, pyproject at 0.76.0): tests/ has 84 test files, 809 collected tests (pytest --collect-only). CI (.github/workflows/ci.yml) runs on push-to-main + PR only (no schedule/cron), matrix ubuntu-latest + windows-latest, and executes exactly one gate: `uv run --group bench --group providers --group mcp pytest -q` (ci.yml:46). drift_bench IS CI-gated, but only because tests/test_drift_bench.py runs `benchmarks.drift_bench.measure()` inside pytest and asserts its invariants (incl. element-wise baseline vector and silent_wrong). Evals are NOT run anywhere automatically — no reference to evals/ in .github/, and evals/README.md:6-7 says "deliberately not part of CI". The six-releases-red claim is verified: evals/scenarios/h03b_idempotency.py:170 documents a grep-shaped check "RED since 0.64.0 — invisible because the eval suite is manual and not part of CI", and evals/results/eval-20260802-171005.json (v0.70.0) shows that fail (h03b.idem.gate_folds_slot_values, mints_header=False). Worse, evals/run.py:258-260 exits 0 on 'fail' (regression) and 1 only on 'error' — so wiring evals into CI as-is still wouldn't redden on a regression. I ran the full key-less eval tier fresh on this branch (eval-20260804-232546.json, v0.76.0, $0): 430 pass / 0 fail / 0 error / 152 missing — no currently-red evals today. Test-shape census: property/invariant-shaped files are test_write_safety_invariants.py (24-cell cross-product of one property), test_drift_bench.py (bench-as-invariant), test_drift_corpus.py (mutation-model decision surface), test_regression_gate.py (cost/fidelity ceilings incl. replay.llm_calls==0); the other ~80 files are scenario-shaped (one bespoke test per known defect), matching the register's structural finding. Confirmed hole in the flagship invariant file: it asserts SOME step is gated, never that the gate is on the step that wrote — the exact R3.2 silent-wrong shape its own docstring names._


### `H1-gate-on-wrong-step-invisible-to-matrix` (high)

**What.** tests/test_write_safety_invariants.py asserts only that SOME step is mutating (`gated = [i for i,s in enumerate(flow.steps) if s.mutating]; assert gated`), never that the gated index IS the commit step — even though the test knows commit_first and its own docstring (lines 26-27) names 'the gate lands on the step that never writes' (R3.2's silent-wrong) as a motivating defect. A regression that gates the benign 'Filter' sibling instead of the commit passes the cache-time assertion. The replay unkeyed-POST check (lines 195-198) partially compensates, but only catches wrong-step gating if it leaves the commit POST unkeyed on the wire — a gate implementation whose transient key header covers the whole run would pass with the gate on the wrong step, wrong precondition scope, wrong confirm binding.


**Where.** tests/test_write_safety_invariants.py:184-188 (assert), :195-198 (partial behavioral backstop), :26-27 (docstring naming the very shape it doesn't assert)


**Evidence.** Read the full file. Line 184: `gated = [i for i, s in enumerate(flow.steps) if s.mutating]`; line 185: `assert gated, ...`. No assertion relates `gated` to the commit step index despite `commit_first` being a matrix dimension. Contrast the new (uncommitted) tests/test_causal_attribution.py:123-125 which does assert exact placement: `marked = [i ...]; assert marked == [1]` — but that is one scenario, not the matrix.


**Disposition / fix shape.** Add the placement assertion to the matrix: compute expected commit index from commit_first (0 or 1 as authored, adjusted for any dropped steps), assert `gated == [expected]` — i.e. the gate is on the commit AND nowhere else (a gated benign sibling is also wrong: it burns keys and preconditions on a read). This is 'add a dimension to the property file' exactly as CLAUDE.md prescribes, and it retroactively makes the matrix able to catch R3.2/R4-shaped wrong-step attribution that it is currently blind to.


### `H2-evals-never-run-automatically-and-fail-exits-0` (high)

**What.** Evals are executed nowhere automatically, and the runner's exit policy means even manual runs don't gate: evals/run.py returns 1 only when a scenario ERRORS; a 'fail' (defined at run.py:16-17 and README.md:64 as 'a shipped capability misbehaved... investigate it like a bug') exits 0. This is the mechanism behind the verified six-release blind spot: h03b_idempotency.py:170 comment — check 'had been RED since 0.64.0 — invisible because the eval suite is manual and not part of CI'; the red state is captured in evals/results/eval-20260802-171005.json (v0.70.0, h03b.idem.gate_folds_slot_values, check note mints_header=False). CI (ci.yml) has no schedule trigger and its only test step is pytest (line 46); grep of .github/ finds zero references to evals. Current state verified by running the full key-less tier fresh on this branch: eval-20260804-232546.json, v0.76.0, 102 scenarios, 430 pass / 0 fail / 0 error / 152 missing (aspirational), $0, exit 0 — nothing currently red.


**Where.** evals/run.py:258-260 (exit policy); .github/workflows/ci.yml:3-6 (triggers: push main + PR only, no cron), :42-46 (single pytest step); evals/scenarios/h03b_idempotency.py:170; evals/README.md:6-7


**Evidence.** run.py:258-260: '# Exit 0 even on low scores... Exit 1 only if a scenario ERRORED' / `return 1 if any(r["counts"].get("error") for r in rows) else 0`. Ran `uv run --no-sync python -m evals.run` (default key-less tier): exit 0, report eval-20260804-232546.json, fail=0 error=0 across all groups.


**Disposition / fix shape.** Two-part fix, both cheap: (1) add a `--gate` (or make it default) exit-nonzero-on-fail>0 mode to evals/run.py — 'fail' is by the suite's own definition a regression; (2) add a CI job (or at minimum a scheduled workflow) running the key-less tier (`uv run python -m evals.run`, $0, ran locally on this machine in this session). The core/shipped-behavior groups (core, h03b shipped checks) are the ones worth gating; horizon 'missing' already doesn't affect exit.


### `H3-shipped-eval-check-quality-grep-shaped` (medium)

**What.** The eval check that went red for six releases is grep-shaped: h03b_idempotency.py builds `mints_header` by substring-searching flow.py source for 'idempotency_key(' + 'set_transient_headers' + 'Idempotency-Key' rather than observing a header on the wire. It went red not because behavior regressed but because a function was renamed — and a rename in the other direction (keep the name, break the behavior) would keep it green. Several sibling checks in the same file are also inspect/src-grep 'partial credit' probes (rs_params, substitutes, slot_values signature checks at ~lines 153-166). By contrast the file's behavioral scenarios (h03b.idem.parameterized_write_row_keyed, line ~181) assert actual per-row distinct / per-retry stable keys against a fixture — that is the durable shape.


**Where.** evals/scenarios/h03b_idempotency.py:~153-176 (src/signature grep checks; the RED-since-0.64.0 comment at :170)


**Evidence.** Read h03b_idempotency.py:150-200. `mints_header = ("idempotency_key(" in src and "set_transient_headers" in src and "Idempotency-Key" in src)`. The in-file comment concedes 'It previously grepped for the old name and so had been RED since 0.64.0'.


**Disposition / fix shape.** Where a check guards shipped behavior (not an aspirational probe), replace source-grep with wire observation — the fixtures.py 'records writes = the write-safety oracle' machinery already exists (evals/README.md:107). Source-grep checks are acceptable only for probing not-yet-built APIs (missing), never for regression-guarding shipped ones (fail).


### `H4-suite-shape-census-scenario-dominant` (medium)

**What.** Test census: 84 test files, 809 collected tests. Property/invariant-shaped files: test_write_safety_invariants.py (24 cells, one property = inviolable #3), test_drift_bench.py (runs benchmarks.drift_bench.measure() once, ~50s, asserts survival curve / per-tier recovery / silent_wrong allowlist / element-wise v1 baseline vector vs baselines/drift.json — this is how drift_bench is CI-gated), test_drift_corpus.py (mutation model as falsifiable claim about locators.resolve, no browser), test_regression_gate.py (cost/fidelity ceilings; replay.llm_calls == 0 at :35 is the inviolable-#1 gate; 10 files total assert llm_calls == 0 somewhere). Only 15 of 84 files use parametrize at all. Everything else — including all six uncommitted test_zz*/test_zzz* repro/refuter files on this branch and test_round2_fixes.py, test_wrongness_fixes.py — is scenario-shaped: one bespoke test per known defect. That matches the register's measured finding (test_write_safety_invariants.py docstring lines 5-15: nine mutation defects all caught, yet ~44 audit findings and 'not one of them was discovered by the test suite').


**Where.** tests/ (84 files; counts from `ls tests/test_*.py | wc -l` and `pytest tests/ -q --collect-only` = '809 tests collected'); tests/test_drift_bench.py:1-19; tests/test_regression_gate.py:35


**Evidence.** Collected counts run in this session. test_drift_bench.py header: 'drift-bench v2 as a CI GATE — key-less, deterministic... (~50 s)' and 'the paid --provider anthropic arm... is never wired into CI'.


**Disposition / fix shape.** Inviolable #3 has its property file; inviolables #1 and #2 do not. #1 is scattered as point assertions (llm_calls == 0 in 10 files, always on happy paths) — no property says 'no code path reachable in mode=replay can construct a provider call' (heal/replan/recovery paths are where sibling-guard gaps live per CLAUDE.md). Consider a #1 property test (e.g. a provider stub that raises on ANY call, threaded through every replay-mode entry point incl. heal and recovery ladder cells) rather than more per-path asserts.


### `H5-multiwrite-same-shape-milder` (low)

**What.** Milder instance of the H1 shape: tests/test_multiwrite.py:102-103 asserts `len(writes) == 2 and all(w.confirm...)` — which steps are the writes is not asserted at the e2e layer. Mitigation exists: the unit test test_attach_binds_by_commit_order_and_validates (:207-216) pins ordered confirm-to-write binding incl. out-of-order rejection, and the e2e cells check per-write server counters and per-write Idempotency-Keys (:108-110), so wrong-step marking that changes wire behavior is caught. The residual is the same as H1's: a wrong-index `mutating` marking whose wire behavior happens to survive.


**Where.** tests/test_multiwrite.py:102-103 (filter without index assert), :108-110 (wire backstop), :207-216 (ordered binding unit)


**Evidence.** Read tests/test_multiwrite.py:95-245 in this session.


**Disposition / fix shape.** If H1's placement assertion lands in the invariant matrix as a multiwrite-capable dimension (2 commits at chosen positions), this file's residual is covered for free — prefer that over patching this file separately, per CLAUDE.md's 'change the shape so the invariant is enforced ONCE'.


### `H6-ungated-benchmarks-inventory` (low)

**What.** benchmarks/ inventory and gating status. CI-GATED (via pytest wrappers): drift_bench.py (tests/test_drift_bench.py), drift_corpus.py model (tests/test_drift_corpus.py), recorder_ceiling.py (tests/test_recorder_ceiling.py, key-less MiniWoB demo-oracle vs baselines/recorder_ceiling.json), write_flow_bench.py scenarios (tests/test_write_flow_bench.py), miniwob oracle path (tests/test_miniwob.py, importorskip('miniwob') — CI installs --group bench so it runs there), shop_flow.py (fixture driving tests/test_regression_gate.py). NEVER GATED / manual-only: bench.py (learn-vs-replay latency), variance.py (the live-LLM variance harness + standing regression gate against baselines/*.json — only its pure aggregation/compare arithmetic is tested, per tests/test_variance.py header 'The real variance harness uses a live LLM and is manual/local'), webarena_run.py live path (needs Docker + ANTHROPIC_API_KEY; tests/test_webarena.py:224 skips integration unless the webarena-verified CLI is reachable, which it is not in CI), and every '--provider anthropic' paid arm (test_drift_bench.py header: 'never wired into CI').


**Where.** benchmarks/ (16 modules, listed in this session); tests/test_variance.py:1-5; tests/test_webarena.py:224; tests/test_drift_bench.py:16-19


**Evidence.** Headers of each gating test read in this session; tests/test_miniwob.py:14 `pytest.importorskip("miniwob")` + ci.yml:36/40 install `--group bench`.


**Disposition / fix shape.** The paid/live arms being manual is a deliberate design (key-less CI), not a defect — but the variance harness's standing-regression-gate function shares the eval problem: a gate that is only run when a human remembers gates nothing. If any baseline in baselines/ is meant to be load-bearing for LLM-learn quality, it currently has no automatic consumer; either say so in baselines/README.md or give it a scheduled paid run with a budget cap (evals/run.py --budget already models this pattern).


---

## User-facing surfaces audited by reading: cli.py (all 17 flow subcommands + root command), mcpserver/server.py,

_User-facing surfaces audited by reading: cli.py (all 17 flow subcommands + root command), mcpserver/server.py, flows.py (learn/replay/run_all/run_batch/canary/audit), daemon/server.py, README/STATUS/GUIDE vs docs/open-defects.md. R3.9 is verified still open: `flow run-all` exits 0 and posts no webhook when every flow was skipped. Two additional exit-code lies found beyond the register: the root `ultracua` command always exits 0 (even success=False, note never printed), and `flow learn` exits 0 on a failed/refused learn while dropping LearnResult.note (including the unattributable-write refusal text). `flow canary` exits 0 on an all-not-learned fleet. R3.11 (RecursionError escaping _load_meta's "never raises" contract into `flow status` and MCP tools/list) verified still present in code. MCP annotations (readOnlyHint/destructiveHint) are backed by the shared is_write_flow predicate and the write rail is genuinely defended; its main lie surface is silent tool-list shrinkage (stderr-log-only skips). README.md:85 mis-describes the defect register as "30 findings, all fixed" when round 3 has ~9 open. replay/run_batch/dry-run/audit exit semantics verified honest._


### `CLI-1` (high)

**What.** VERIFIED R3.9: `flow run-all` exits 0 and the alert webhook stays silent when the whole fleet was skipped — exit is `SystemExit(1 if failed else 0)` and webhook fires only `if failed`; FleetRun status 'skipped' (not-approved, write-without---include-writes, and the UNDECLARED-write skip a flow can enter with no human act via wire promotion) feeds neither channel. `0 ok, 0 failed, N skipped` prints and cron reports green.


**Where.** src/ultracua/cli.py:747-759 (failed = status=='failed' only; webhook at 757; exit at 759); src/ultracua/flows.py:2377-2387 (the three skip branches); docs/open-defects.md:1195-1219 (R3.9, listed open)


**Evidence.** cli.py:759 `raise SystemExit(1 if failed else 0)  # cron alerts on a non-zero exit`; cli.py:757 `if failed and args.alert_webhook`. GUIDE.md:576-578 documents 'exits non-zero if any flow failed' with no mention of the skip class; README.md:73 says 'exits non-zero for cron'.


**Disposition / fix shape.** Decide whether skipped-that-was-not-human-chosen (undeclared write, not-approved after a re-learn) should be a distinct exit code / webhook event; the EmptyFlowStoreError precedent (cli.py:1179-1183, exit 2/3 for zero flows) shows the shape already used for 'ran nothing'.


### `CLI-2` (high)

**What.** Root `ultracua --url --goal` command ALWAYS exits 0: `_amain` prints `success={report.success}` but never raises or sets an exit code, and `run_cached` returns FlowReport(success=False) rather than raising on every failure path (miss, escalate, verify-failed, unattributed-write refusal). Additionally `report.note` — which flow.py deliberately populates because 'a bare success=False with no reason ... is the fail-loud inviolable read as fail-quiet' (flow.py:761-763) — is never printed by _amain. A scripted caller checking $? sees success on total failure, and a human sees success=False with no reason.


**Where.** src/ultracua/cli.py:39-70 (_amain, no exit code, no note print); src/ultracua/flow.py:806-808 (note carried on FlowReport); src/ultracua/flow.py:133,151,154,711 (success=False returns, no raise)


**Evidence.** cli.py:61-64 prints only mode/success/llm_calls/healed; nothing in _amain or main() converts report.success into an exit code. Contrast: the daemon DOES surface note (daemon/server.py:72).


**Disposition / fix shape.** Exit nonzero on success=False and print report.note; this is the same surface the flow.py comment already identifies as needing the reason.


### `CLI-3` (medium)

**What.** `flow learn` exits 0 when learning FAILED, and drops LearnResult.note. On res.cached=False it prints only the generic 'WARNING: no replayable flow was cached (the agent took no clean steps).' and returns — exit 0, and the specific refusal reason in res.note is never shown. This includes the R3.2-adjacent unattributable-write refusal, whose carefully written note (flows.py:1057-1063: 'a write fired on the wire during discovery but no step could be attributed to it...') is invisible from the CLI; the operator instead reads a false diagnosis ('took no clean steps').


**Where.** src/ultracua/cli.py:184-185 (generic warning, no res.note, no exit code); src/ultracua/flows.py:1054-1063 and 1077-1083 (the notes being dropped)


**Evidence.** _flow_learn (cli.py:162-191) contains no print of res.note and no SystemExit on failure; contrast _flow_record which raises SystemExit(f'NOT recorded: {res.note}') at cli.py:921 — the sibling got it right.


**Disposition / fix shape.** Mirror _flow_record: print res.note and exit nonzero when not res.cached (or not res.found). Sibling-guard pattern from CLAUDE.md applies literally.


### `CLI-4` (medium)

**What.** `flow canary` exits 0 for a fleet that is entirely 'not-learned': exit condition is `status in ('stale','error')` only, so not-learned counts toward neither, and a wiped/relocated cache (the exact wrong-cwd class the EmptyFlowStoreError fix targeted for zero SPECS) reports '0 fresh, 0 stale/error (of N)' with exit 0 — cron reports the early-warning probe green while probing nothing.


**Where.** src/ultracua/cli.py:932-940 (stale = status in ('stale','error'); exit at 940); src/ultracua/flows.py:2666-2667 (not-learned status)


**Evidence.** cli.py:937 `stale = [r for r in results if r.status in ('stale', 'error')]`; 940 `raise SystemExit(1 if stale else 0)`. GUIDE.md:606 documents 'exits non-zero if any flow is stale' — not-learned is arguably staler than stale.


**Disposition / fix shape.** Consider treating an all-not-learned (or any not-learned?) fleet as nonzero, consistent with EmptyFlowStoreError's rationale (flows.py:2088-2090: 'reported success while doing nothing').


### `API-1` (medium)

**What.** VERIFIED R3.11 still in code: _load_meta's parse-failure catch is narrowed to `(ValueError, UnicodeDecodeError)` + OSError, but json.loads raises RecursionError (not a ValueError) on deep nesting — violating the function's explicit contract at flows.py:587-588 ('It never raises: health() and the MCP tools/list loop must not traceback on one bad flow'). Blast radius verified: cli.py `_flow_status` calls health(load_spec(name)) with no per-flow guard, and mcpserver `_tool_for` guards only CacheUnreadableError around health(), so one malicious/corrupt meta file tracebacks the entire fleet status view and the entire MCP tools/list.


**Where.** src/ultracua/flows.py:609-613 (narrowed except), 587-588 (violated contract); src/ultracua/cli.py:549-555 (unguarded per-flow loop); src/ultracua/mcpserver/server.py:153-161,177 (guard catches only CacheUnreadableError); docs/open-defects.md:1246-1254 (R3.11, open)


**Evidence.** flows.py:609 `except (ValueError, UnicodeDecodeError) as exc:` — RecursionError inherits from Exception directly. Also note cli.py:549-555 `flow status` additionally lets load_spec exceptions kill the whole listing (one unreadable spec file = no status for any flow).


**Disposition / fix shape.** R3.11's register entry proposes routing to _refuse_unreadable_meta; also consider a per-flow guard in _flow_status matching run_all/canary_all's one-boundary-guard shape.


### `MCP-1` (low)

**What.** MCP tool-list shrinkage is invisible to the client: unreadable spec (log.warning), unreadable cached recipe (log.error), stale approval (log.warning), and sanitized-name collision (log.warning) each silently DROP a tool from tools/list with no protocol-level signal — the client just sees fewer tools. Each skip is individually defensible (an unlisted tool beats an always-failing one, per the in-code comment) and each is logged to stderr, but an agent-side consumer has no way to distinguish 'flow retired' from 'flow broken'. The readOnlyHint/destructiveHint annotations themselves are honestly backed: is_write covers BOTH declared (spec.mutate) and cached-step-mutating flows via the shared is_write_flow, undeclared writes are never exposed, and the write rail (single-flight lock, ledger dedupe, elicit-or-refuse, record-after-confirm, landed-only ledger on WriteReadbackError) is real.


**Where.** src/ultracua/mcpserver/server.py:148-161 (skip paths), 180-199 (stale-approval + collision skips), 119-132 (_is_write_flow), 371-374 (annotations), 265-318 (write rail)


**Evidence.** server.py:185-187 `_log.warning('mcp: skipping %r — approval no longer matches...')` — stderr only. Also noted: FlowTool.output_schema is populated (server.py:213) but never advertised in _list_tools (no outputSchema field, documented as deferred to H9 at server.py:356).


**Disposition / fix shape.** Fact for the plan: any 'broken state degrades silently' claim about MCP should target list-shrinkage observability, not the annotations or the write rail, which verified sound.


### `DOC-1` (medium)

**What.** README.md:85 mis-describes the defect register: 'two adversarial rounds, 30 findings, all fixed in 0.64.0-0.72.0' — but docs/open-defects.md:6-14 records a THIRD round (11 findings + R3.12, 1 critical, none refuted) with 'the other eight are still open' (nine counting the reverted R3.2, per CLAUDE.md). A reader following README's own 'Read before starting new work' pointer gets a materially false safety summary of the project.


**Where.** README.md:85 vs docs/open-defects.md:1-14


**Evidence.** README.md:85: 'two adversarial rounds, 30 findings, all fixed in 0.64.0–0.72.0'; open-defects.md:6-7: 'ROUND 3 ... 11 findings, 1 critical, NONE refuted'; open-defects.md:13-14: 'The other eight are still open.'


**Disposition / fix shape.** One-line README table-cell update; also re-check GUIDE.md:577-578 and README.md:73 run-all exit claims when CLI-1 is resolved.


### `DOC-2` (low)

**What.** README Setup instructs bare `uv sync` (README.md:34) while CLAUDE.md and user memory state bare `uv sync` strips the bench/providers groups and breaks the test suite ('never bare uv sync — use uv sync --all-groups'). Fine for a pure end-user install, a trap for anyone following README then running the suite; no caveat or pointer distinguishes the two audiences.


**Where.** README.md:34 vs CLAUDE.md ('Never bare `uv sync` (it strips groups)')


**Evidence.** README.md:34 `uv sync  # create the venv + install deps`; memory file uv-sync-all-groups.md records this exact breakage.


**Disposition / fix shape.** Add '(contributors: uv sync --all-groups)' or align with ARCHITECTURE.md's contributor setup.


### `API-2` (info)

**What.** Failure-shape map of the flows API (verified honest except learn): replay() raises typed FlowReplayError subclasses for every failure incl. the write taxonomy (WriteUnverifiedError raised before any retry path, WriteReadbackError recorded ok=True by design); run_batch() raises FlowReplayError on preflight refusal and returns status failed/invalid otherwise, with the CLI exiting 1 on both and honest ledger handling of landed errors; dry_run() aborts exit 2; audit_flows() exits 2 on quarantine and 3 on unjudged/no-LLM ('UNJUDGED IS NOT CLEAN' printed); canary()/run_all() return per-flow status objects (their lies are CLI exit-code mapping, items CLI-1/CLI-4). learn() is the one API whose failure is return-fields-only (LearnResult.cached/found/note, no raise) — safe for programmatic callers who check fields, but it is what enables CLI-3.


**Where.** src/ultracua/flows.py:1985-2084 (replay raise paths), 2437-2644 (run_batch), 2218-2223 (AuditRun.exit_code), 970-1008 (learn returns best-effort result); src/ultracua/cli.py:213-216 (dry-run exit 2), 837-858 (run-batch exits), 711 (audit exit)


**Evidence.** flows.py:1990-2005: write_unverified and write_unreadable raised BEFORE retry_ok exists so neither can reach auth-refresh retry or relearn; cli.py:858 `raise SystemExit(1 if bad else 0)` with bad = status in ('failed','invalid') matching BatchRun's actual status vocabulary (ok/failed/invalid/planned, flows.py:2642).


**Disposition / fix shape.** Baseline facts: the plan's fail-loud work should concentrate on the four CLI mappings (CLI-1..4), not on replay/run_batch/dry-run/audit, which check out.


### `API-3` (info)

**What.** Daemon (JSON-RPC over stdio, plus Node client in clients/) is an honestly-documented but real bypass surface: 'run' calls run_cached directly and 'deliberately BYPASSES everything flows.replay() enforces: the approval gate, the write/mutate action-completion check, data-shape-drift detection, auth refresh, and the per-flow health record. It will happily learn-and-perform a write.' Unauthenticated, single-flight. It DOES return report.note (unlike the root CLI). No lie found — the contract is stated at the top of the file and repeated in STATUS.md:28 — but it is the one user-reachable surface where every trust control is absent by design.


**Where.** src/ultracua/daemon/server.py:12-19 (contract), 46-73 (_run returns success+note); STATUS.md:28


**Evidence.** server.py:15: 'It will happily learn-and-perform a write'; server.py:19: 'single-flight and UNAUTHENTICATED'.


**Disposition / fix shape.** Only relevant if the plan touches write attribution: the daemon is the second of the 'three callers' the unattributed-write refusal was moved into flow._learn to cover (flows.py:1041-1042).


### `CLI-5` (low)

**What.** Minor status-view soft spots, each individually small: (a) _audit_advisories swallows ALL exceptions to 0 (cli.py:540-541), so `flow status` shows no unreviewed-advisory line when the meta is unreadable — the habituation counter the feature exists to surface reads as zero exactly when state is broken; (b) _warn_if_approval_stale swallows import errors silently (cli.py:120-121, deliberate 'courtesy warning' rationale); (c) serve-mcp refuses an EMPTY store (exit 2) but a store whose flows all fail health/approval checks serves zero tools and stays up — explicitly allowed as 'a legitimate mid-setup state' (cli.py:492-497), with the count printed to stderr as the only signal.


**Where.** src/ultracua/cli.py:540-541, 120-121, 488-497


**Evidence.** cli.py:540-541 `except Exception: ... return 0  # a status line must never break flow status` — correct goal, but 0 is indistinguishable from 'none pending'.


**Disposition / fix shape.** (a) could return a sentinel and print 'advisories: unreadable' — same absent-vs-unreadable distinction _load_meta itself was redesigned around in 0.73.0 (R3.4 shape).


---

## Sweep of src/ultracua for accepted-bound/residual comments found ~16 distinct stated bounds

_Sweep of src/ultracua for accepted-bound/residual comments found ~16 distinct stated bounds. Pinned-and-loud ones are healthy (cross-origin drain refusal, contracts per-field disable, ghost-marker/worker reconciliation, row no-identity case, anchor truncation). Un-pinned SILENT bounds cluster in three areas: (1) locator identity — the role+name~ sole-candidate substring decoy and the positional row-token residual both silently actuate the wrong element/row at 0-LLM with no pinning test; (2) write detection blind spots — undeclared GET/sendBeacon writes record as reads (and verify-by-replay would re-fire them), a postMessage-triggered iframe write caches ungated, the learn path watches no WebSocket while the recorder does (sibling gap), and the R3.2 deferred-write-into-mutating-neighbour case passes the wire-vs-classifier consistency check; (3) secrets — the vision tier sends a raw screenshot where a secret typed into a plain input is legible, with no guard refusing the pairing and no test. One possibly-dead branch is self-labeled in flows._learn_once:1064-1083. All claims cite file:line verified by reading._


### `AB-1` (high)

**What.** Stated residual: a write DEFERRED out of its own step into a neighbour that happens to be classifier-mutating passes the wire-vs-classifier consistency check — the flow caches with the gate (Idempotency-Key, precondition, drift gate) on a step that never writes. Same residual as open R3.2; comment says it 'needs the causal signal'. Silent-wrong direction. No pinning test (tests/test_write_safety_invariants.py:81 only documents the exclusion; the tests/test_zz_* files are uncommitted audit probes, not stable pins).


**Where.** src/ultracua/flow.py:632-641 (check at 635-641; residual comment 632-634)


**Evidence.** Comment: 'Residual, stated: a write DEFERRED out of its own step and into a neighbour that happens to be classifier-mutating passes this check. That is the same residual R3.2 has and this slice does not claim to close.' Note: line 635 gates on `temporal_attributed`, which is uncommitted branch work.


**Disposition / fix shape.** This is exactly what feat/shared-causal-attribution (attribution.py WIRE_ATTRIB_JS + attribute()) targets; verify the causal path closes it AND add a pin test the way test_row_identity_redesign.py:186 pins its residual.


### `AB-2` (high)

**What.** Stated residual: when role+name~ (fuzzy substring name) is the ONLY surviving Tier-1 candidate and a substring decoy exists, it binds OUTRIGHT with no corroboration — a 0-LLM type/click on the WRONG element reporting success (inviolable #2). No test pins the residual: test_tier1_substring_anchor.py restates it in its header (lines 20-22) but its 3 tests (lines 76, 96, 107) cover only the reorder fix and the retained capability.


**Where.** src/ultracua/locators.py:482-485


**Evidence.** 'RESIDUAL, worth stating: when role+name~ is the ONLY surviving Tier-1 candidate and a substring decoy exists, it still binds outright with no corroboration. Closing that needs a css-agreement gate like Tier 2's, which measured at the same cost as full deletion. See HEALING.md.'


**Disposition / fix shape.** Either pin it (a KNOWN_WRONG_BINDS-style test like tests/test_locators.py:161) or adjudicate the css-agreement gate with benchmarks/drift_bench.py rather than accepting the corpus-cost claim from memory.


### `AB-3` (high)

**What.** Stated residual: a POSITIONAL token (data-index="2", id="row-2") discriminates among today's siblings so it IS accepted as a row identity, yet renumbers when a row is deleted — the identity outlives the record and a later replay can act on the WRONG row (e.g. cancel the wrong order: silent, write-adjacent). Not pinned: test_row_identity_redesign.py pins only the nothing-discriminating case (line 186) and no test in tests/ contains data-index/renumber for the redesign (grep confirmed no match).


**Where.** src/ultracua/locators.py:113-115


**Evidence.** 'RESIDUAL, unchanged and stated rather than hidden: a POSITIONAL token ... discriminates among today's siblings yet renumbers when a row is deleted, so the identity outlives the record it named. Nothing observable in a single capture separates that from a real key.'


**Disposition / fix shape.** Pin the wrong-row-after-deletion behavior as a documented-residual test (the file already has that pattern at line 186-187).


### `AB-4` (high)

**What.** Stated residual: an iframe/cross-realm write TRIGGERED by a recorded main-frame action (e.g. via postMessage) could cache UNGATED — sub-frame requests are deliberately excluded from the marker reconciliation (to avoid false-refusing pages with 3rd-party iframes). Silent. Only the false-refuse direction is pinned (tests/test_record.py:1656 test_record_write_iframe_post_does_not_false_refuse); the cache-ungated direction has no test.


**Where.** src/ultracua/attribution.py:130-135 (exclusion rationale 130-132, residual 133-135)


**Evidence.** 'SUB-FRAME (iframe) writes are DELIBERATELY excluded from that reconciliation ... an iframe/cross-realm write TRIGGERED by a recorded main-frame action — e.g. via postMessage — could cache ungated'


**Disposition / fix shape.** At minimum add a documented-residual pin test; the WebSocket part of the same comment block is AB-8.


### `AB-5` (high)

**What.** Stated residual (recorder): a write behind a GET link or navigator.sendBeacon is NOT auto-detected when the flow is not declared a write — it records as a READ flow, so verify-by-replay re-fires the GET-write (a real double-submit during recording verification) and every replay fires it ungated/un-keyed, silently. Only the DECLARED path is pinned (tests/test_record.py:223 test_record_write_flow_gates_a_formless_keyword_commit — the keyword classifier catches 'Delete account'); a bland-named undeclared GET-write has no test and no detection.


**Where.** src/ultracua/flows.py:3018-3023 (record docstring residual) and src/ultracua/flows.py:3146-3149 (GET-write with no wire signal = 'the acknowledged undetectable residual: cached approval-gated' — but approval-gating only applies when DECLARED)


**Evidence.** '**Residual:** a write behind a **GET** link or `navigator.sendBeacon` isn't auto-detected; declaring the flow a write (`spec.mutate`) still captures it safely ... don't rely on auto-detection for those — declare them.'


**Disposition / fix shape.** Cannot be closed by detection (GET is indistinguishable from a read); candidate mitigations are doc/CLI prominence and a pin test demonstrating the undeclared case records as a read.


### `AB-6` (high)

**What.** Stated residual (secrets): the VISION tier sends a raw screenshot to the grounding model, where a secret slot's value typed into a plain text input is legible — text observations are scrubbed (flow.py:115-118 redact; SNAPSHOT_JS masks input[type=password]) but screenshots are not. The only mitigation is a docstring sentence 'Don't pair a secret slot with vision grounding' — there is NO guard refusing the pairing (grep: no secret/grounding cross-check in flow.py or flows.py; _vision_decide at flow.py:181-184 screenshots unconditionally) and NO test (tests/test_vision.py contains no 'secret').


**Where.** src/ultracua/flows.py:147-149 (SlotSpec docstring); leak path src/ultracua/flow.py:181-187 (_vision_decide)


**Evidence.** 'The residual, stated rather than hidden: the VISION tier sends a raw screenshot, where a secret typed into a plain text input is legible. Don't pair a secret slot with vision grounding.'


**Disposition / fix shape.** The 4-point enforcement claim above it (flows.py:144-147) shows the project's pattern is to ENFORCE, not advise — a refuse-or-skip-vision guard when spec has a secret slot + grounding is the shape-consistent fix, plus a test.


### `AB-7` (medium)

**What.** Accepted cost, loud direction, UNPINNED: the learn-path write watcher over-counts a click-triggered non-telemetry read-POST (GraphQL/RPC query) as a write; since 0.74/0.75 promotion, that marks the flow a write which MCP/run_batch REFUSE until a human declares it. Loud and correctable by design, but no test pins the over-count-then-refuse behavior (grep for GraphQL/read-POST/over-count in tests/: zero matches), so a regression to the SILENT direction would be invisible.


**Where.** src/ultracua/flow.py:257-261 (watcher residual bound) and src/ultracua/flow.py:583-587 ('ACCEPTED COST, stated rather than hidden' on the promotion loop)


**Evidence.** 'the escape hatch is to declare it, never to expose it silently' (flow.py:587)


**Disposition / fix shape.** One fixture with a click-triggered JSON read-POST asserting the flow caches as a declared-write-required refusal.


### `AB-8` (medium)

**What.** Sibling-path gap (the register's named defect shape): the RECORDER treats any sent WebSocket frame as a write-suspect and refuses an undeclared one (recorder.py:376, page.on('websocket', _watch_ws) at recorder.py:500), but the LEARN path's watcher subscribes ONLY to 'request' (flow.py:341 and flow.py:1419 — grep confirms no websocket handler in flow.py). A flow whose commit goes over a WebSocket learns and caches as a READ; replay re-fires the frames per run, silently. attribution.py:133-134 also states sockets carry no marker and can't be gated.


**Where.** src/ultracua/flow.py:341 vs src/ultracua/recorder.py:500


**Evidence.** recorder.py:376: 'OR **any WebSocket frame sent** (a write-suspect...)'; flow.py has only page.on('request', ...) at 341 and 1419.


**Disposition / fix shape.** Apply the recorder's ws-suspect guard to the learn watcher (guard exists on sibling path — the exact pattern CLAUDE.md predicts); adjudicate false-positive cost (ws-using read pages) before shipping.


### `AB-9` (medium)

**What.** Stated residual: a NON-surfacing marker (aborted/CSP-blocked fetch that markered but never hit the wire) and an un-instrumentable worker write at the EXACT same (method,url) still offset 1:1 — the worker write would cache ungated. 'Irreducible without per-request correlation.' The per-(method,url) keying that shrinks it IS pinned (tests/test_record.py:1289 ghost-marker test, 1165 worker-offset test); the same-url coincidence itself is unpinned (arguably unpinnable — it is the defined blind spot).


**Where.** src/ultracua/recorder.py:402-404 and src/ultracua/attribution.py:136


**Evidence.** '(Residual: a NON-surfacing marker and a worker write at the EXACT same (method, url) still offset — irreducible without per-request correlation...)'


**Disposition / fix shape.** Accept; per-request correlation (e.g. a nonce query/header) is the only close and would alter page behaviour.


### `AB-10` (medium)

**What.** POSSIBLY-DEAD CODE, self-labeled: the belt-and-braces `not any(s.mutating)` refusal branch in flows._learn_once is 'possibly now UNREACHABLE — kept deliberately, and labelled honestly' after flow._learn took over refusal in the mechanism (flow.py:745-770). The comment instructs: 'If a later change proves it dead, DELETE it.' Also docs/open-defects.md:996 notes the promotion is 'simply dead for every step index >= 1' on the flows.learn() path in the R3 finding context. Note: the mutation-test report (open-defects.md:632) says removing 'the promotion loop's silent drop' IS caught by the suite, so any deletion must re-run that check.


**Where.** src/ultracua/flows.py:1064-1083 (branch at 1074-1083); superseding guard src/ultracua/flow.py:758-770


**Evidence.** 'BELT AND BRACES, and possibly now UNREACHABLE ... "Should" is doing real work in that sentence: it is a claim about a control-flow argument, not something measured, so the branch stays.'


**Disposition / fix shape.** If the plan touches attribution, decide this branch's fate by measurement (coverage trace through both callers), not argument — the comment explicitly requests that.


### `AB-11` (medium)

**What.** PINNED silent residual worth carrying into the plan: a row anchor longer than 60 chars is truncated at capture, so the wrong-row-bind identity check SKIPS it — a verbose-row table gets no wrong-row protection. Pinned honestly by tests/test_wrongness_fixes.py:83-102 (test_a_truncated_row_anchor_is_not_protected_and_that_is_documented), which asserts the wrong row DOES bind. Silent user bite exists but the test keeps it visible.


**Where.** pinned at tests/test_wrongness_fixes.py:83-102 (anchorOf 60-char slice in locators.py)


**Evidence.** 'a row longer than 60 chars gets no protection from the wrong-row bind above ... Closing this properly needs an identity token captured per row rather than a truncated text prefix.'


**Disposition / fix shape.** The row-identity work (AB-3) and this share one proper fix: a per-row identity token instead of text prefix.


### `AB-12` (low)

**What.** Stated residual, refusal-shaped and PINNED: a cross-origin navigation orphans the prior origin's undrained sessionStorage events — record() FAILS LOUD (refuses to cache) rather than caching a truncated flow. Pinned by tests/test_record.py:1700 test_record_refuses_a_cross_origin_demo. Not a silent bite. Related deferred item: iframe/shadow capture (recorder.py:67) — sub-frame demo events are never captured; a sub-frame WRITE still sets wrote['hit'] (recorder.py:416-419) so a declared write refuses loudly, but a READ demo with an iframe interaction silently records without that step (verify-by-replay may still pass); no test for that read case.


**Where.** src/ultracua/recorder.py:22-25 (cross-origin) and recorder.py:65-67 (iframe/shadow deferred item)


**Evidence.** '(iframe/shadow capture is a documented deferred item.)' — no corresponding read-flow truncation test found.


**Disposition / fix shape.** Low: add the read-flow-with-iframe-click truncation case to the recorder fidelity tests if recorder work is in scope.


### `AB-13` (low)

**What.** Accepted residual, narrow and UNPINNED: _select_values decodes a recorded single-select whose option value attribute is itself a JSON array literal (e.g. '["x"]') as a multi-select set. Comment argues option values are effectively never JSON arrays. Failure would be a loud select_option miss unless a sibling option matches the decoded scalar. No test exercises the pathological literal (tests/test_slots.py and test_recorder_fidelity.py cover only genuine multi-selects).


**Where.** src/ultracua/flow.py:1119-1132 (residual stated 1123-1126)


**Evidence.** '(Known narrow edge: a SINGLE-select whose option value attribute is itself a JSON array literal ... this is an accepted residual rather than a flag on every step.)'


**Disposition / fix shape.** One-line pin test if cheap; otherwise leave — loud in the common case.


### `AB-14` (low)

**What.** Escape hatch, human-set and PINNED: SlotSpec/contracts per-field `enabled: bool = True` off switch ('the narrow escape hatch vs a blanket release') — tested at tests/test_contracts.py:90 (effective_contracts with {'price': {'enabled': False}}). Human-only configuration; a user cannot be silently bitten without setting it themselves. Note however the register's open finding (open-defects.md:600-612) that a quarantine round-trip silently drops contracts/shape/read_pin entirely — a much larger version of the same 'contracts gone, nothing reports it' hazard, already filed.


**Where.** src/ultracua/contracts.py:43-47


**Evidence.** contracts.py:47: 'enabled: bool = True  # per-field off switch (the narrow escape hatch vs a blanket release)'


**Disposition / fix shape.** No action on the field itself; the meta-loss finding at open-defects.md:600 is the real item and is already registered.


### `AB-15` (low)

**What.** Stated residual, loud/recoverable: the audit judge's structural bound means injection can at worst cause a FALSE QUARANTINE (denial of service), recoverable with `flow release` — 'Injection cannot manufacture trust.' The structure is AST-pinned per the module docstring (audit never imports flows/cache/history/safety/ledger). tests/test_audit.py exists and exercises decide().


**Where.** src/ultracua/audit.py:27-31 (residual at 30-31), structural claims 15-25


**Evidence.** 'The residual risk is denial of service (a false quarantine), recoverable with `flow release`.'


**Disposition / fix shape.** No action; but note the flow-release recovery path is the same one open-defects.md:600-612 shows silently drops contracts — the two interact.


### `AB-16` (low)

**What.** Not a bound — observability note only: resolve()'s `sink=tr.meta` records WHICH locator candidate bound so drift_bench can see load migrating onto brittle positional css while the headline rate stays 100%. Behaviour-neutral; listed for completeness because the sweep term 'bound (' matched it.


**Where.** src/ultracua/flow.py:1270-1274


**Evidence.** '`sink=tr.meta` records WHICH candidate bound (observability only, no behaviour change)'


**Disposition / fix shape.** None. Also for completeness: two further PINNED documented residuals found during the test sweep — tokenless positional-css retarget (tests/test_locators.py:161-165, KNOWN_WRONG_BINDS) and two structurally-identical forms sharing role+name (tests/test_safety_integration.py:107).
