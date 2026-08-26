# The reshape plan — stop manufacturing the defect classes

**STATUS IS DATA, and this table is the only place to read it.** It is rendered from
`docs/plan/state.json`, and every row is adjudicated against the tree by `tests/test_plan_state.py` —
a `done` step must have its artifact present, a `pending` or `held` step must have it ABSENT. The four
tables below keep their reasoning columns; they no longer keep the answer. **This exists because the
status went stale in the worst possible direction**: 2.1 (B2) merged as PR #189 on 2026-08-20 and
§13's row naming it as *next* still read "never started" three days and six merged slices later, with
every row below it marked. The next instruction to act on that table was an instruction to build B2 a
second time.

<!-- generated:plan-status — edit docs/plan/state.json, then `python scripts/render_plan_status.py --write` -->
| step | phase | status | landed / trigger | what |
|---|---|---|---|---|
| 0 | 0 | done | 2026-08-20 | CI provisioning |
| 0.1 | 0 | done | #168 #170 | fast tier |
| 0.2a | 0 | done | #171 | B1's ten filed in the register |
| 0.2b | 0 | done | #172 | register as structured data |
| 0.3 | 0 | done | #175 | exit-set matrix over a fake engine |
| 0.4a | 0 | done | 0.4a | ratchets + red-proof |
| 0.4b | 0 | done | 0.115.0 | RED-in-CI |
| 0.5 | 0 | held | held → 1.1 (**fired**) | contract tests for the must-agree pairs |
| 0.6 | 0 | done | 0.136.0 | scheduled mutation sweep |
| 0.7 | 0 | held | held → 2.3 (**fired**) | shared fixture server |
| 0.8 | 0 | done | 2026-08-20 | tier marks from an observation |
| 1.1 | 1 | done | 0.115.0 | keyword-only engine chain |
| 1.2 | 1 | done | 0.109.0 | `flow release` reaches `release()` |
| 1.3 | 1 | done | 0.114.0 | a cannot-spend third state |
| 1.4a | 1 | done | 0.111.0 | distinct refusal codes |
| 1.4b | 1 | done | #191 | `outcome_of` + the tri-state readers |
| 1.5 | 1 | done | 0.110.0 | the single-exit RunRecord sink |
| 1.6 | 1 | done | 0.116.0 | `WriteClass` + `FlowSpec.key` |
| 1.7 | 1 | done | 0.117.0 | the printed door policy table |
| 1.8 | 1 | done | 0.118.0 | `RunOptions` / `RunHooks` |
| 2.1 | 2 | done | #189 | B2 — substrates, reset, readiness, boundary ledger |
| 2.2 | 2 | done | 0.113.0 | B3 — the outcome vocabulary |
| 2.3 | 2 | done | 0.125.0 | B4 — the 14-scenario corpus + server-side oracles |
| 2.4a | 2 | done | 0.137.0 | B5 — the weekly run, baseline gating, the honesty page |
| 2.4b | 2 | pending | — | B5 — the Odoo half of the baseline |

**22 of 25 steps done.** Every row is adjudicated against the tree by `tests/test_plan_state.py` — a `done` step must have its artifact and a `pending`/`held` step must not.
<!-- /generated:plan-status -->

Researched 2026-08-16
against `6d2aa90` / 0.108.0, immediately after PR #165 (B1, "the run record") merged. Every `file:line`
in this document was re-verified against the tree by hand before it landed; where a number comes from a
measurement made during the analysis and **not** re-run here, it says so. §12 is a SEQUENCING decision and
not a re-price: the day estimates below are the 2026-08-16 ones, untouched.

**The question this answers.** Every change and every fix has been breaking something. Only adversarial
audits catch it — the suite, four rounds running, has never found one. PR #165 needed two audit rounds
that found nine defects *in its own new code*, and a third pass run for this document confirmed ten
more. Expecting a human review to carry that is not realistic. So: **should the code be
re-architected, and if so how?**

**The answer.** Re-shape it; do not re-architect or rewrite it. The defect stream is a property of the
code's *shape* and of the *net* under it, not of the domain and not of care. A rewrite — or the
`flows/` package split that keeps suggesting itself — is ~15k lines of **fix code**, and fix code here
is measured at 5–16x the defect density of the code it replaces (§2), under a suite that has never
caught a missing guard. What is actually broken is a short list of concrete shapes that manufacture the
recurring classes. Those are removable one at a time, each ending with the old shape *inexpressible*
rather than merely re-tested — and the instruments that will adjudicate them have to exist first.

**What it costs.** Roughly **55–80 engineer-days** for Phases 0–2 together, audits included, with the
benchmark's B2–B5 running in parallel from day one. Phase 3 (~26 days) is listed, priced, and
deliberately **un-committed** — each item is taken only when a number indicts it.

**What it does not do.** It does not touch the six HIGH items behind `D5` — write attribution, row
identity, laundered commits, timer boundaries (~36 findings in classes c/e). They stay open,
over-gate-and-lose-recovery stays the shipped posture, and the benchmark's honesty page has to say so
beside every number.

---

## 0 · Method, and how to read the provenance

Three questions were asked in parallel by read-only agents, and their answers were then attacked:

| Instrument | What it did |
|---|---|
| 5 structural readers | `flow.py`; `flows.py`; the perception stack (`locators`/`snapshot`/`recorder`/`safety`/`pin`/`browser`); the outer surfaces (`cli`/`mcpserver`/`daemon`/`dryrun`/`audit`/`cache`/`obs`/`llm`/`vision`/`providers`); the suite + CI |
| 2 miners | a root-cause taxonomy over every id in `docs/open-defects.md` + the plan + the survey; git/`gh` metrics over all 165 merged PRs |
| 4 audit lenses on PR #165 | accounting correctness; the `RunRecord` control flow; the three inviolables; the quality of the tests the PR added |
| 10 verifiers | one per top finding, briefed to **refute** it; all 10 came back CONFIRMED, most with the severity corrected downward |
| 4 designers → 3 judges → 1 synthesis → 1 adversarial critic | four independently-briefed proposals (incremental seams; a facade; instrument-first; and a contrarian "freeze `src/` entirely"), scored on five axes, synthesised, then attacked. Verdict on the synthesis: **sound-with-changes**; those changes are folded in below and marked **(critic)** |

Three provenance levels are used throughout, because this register's own rule is that a measurement
taken under unknown conditions is not evidence:

* **verified here** — re-checked against the tree while writing this document (every `file:line`, every
  grep count in §2 and §4).
* **measured in analysis** — produced by an agent during the 2026-08-16 session, typically on a scratch
  copy of `src/` on `PYTHONPATH` (the repo was never modified). Reproducible, not re-run here.
* **inferred** — reasoning from code, not executed. Marked inline.

---

## 1 · The eight root causes

Every id in the register was classified. Eight causes account for the pattern; the first five are
structural and removable, the sixth is a sensor problem this plan makes *measurable* but does not solve.

### 1. Every policy question is answered N times by hand, so a fix IS one more hand copy
`classes a, l, f, k ≈ 57 findings`

Verified counts at `6d2aa90`:

| Question | Answered at | Verified |
|---|---|---|
| "how does this argument reach the engine?" | `run_cached` has 27 params and calls `_learn_n` with 21 positional args (`flow.py:216`), `_learn` with 19 (`:222`), and `_learn_n → _learn` with **23 positional / 0 keyword** (`:1014`) | yes |
| "may this replay proceed?" | ~14 sites in 4 modules (`_preflight_row`, the precheck skip, `_auth_retry_allowed`, the kind hard-stops, the relearn gate, `run_all`'s ladder, `run_batch` x3, `dry_run`, `preflight_keys`, MCP `_tool_for` + `call_flow_tool`, `flow.py`'s mode dispatch) | inferred from the readers |
| "is this flow a write?" | **33** raw `spec.mutate is (not) None` — 25 in `flows.py`, 5 in `mcpserver/server.py`, 3 in `cli.py` — beside 7 uses of the `is_write_flow` predicate that exists to replace them | yes |
| "what is this flow's cache key?" | **24** transcriptions of `flow_key(spec.goal, spec.start_url, spec.scope)` (15 + 7 + 2) | yes |
| "what is the failure?" | ~~**24** bare `raise FlowReplayError(` in `flows.py`, all sharing `code='replay_error'`~~ **CLOSED at 1.4 (0.111.0)**: 15 new classes, 27 distinct codes, ratchet **29 -> 0** (24 raises + 1 value ref + 4 literals — the raise count alone could have read 0 while `_classify_replay_failure`'s indirect raise still resolved to the base) | yes |
| "what is the exit code?" | **34** `raise SystemExit` in `cli.py`, plus ~16 status→surface translations across 7 string vocabularies | yes |
| "did a write leave the browser?" | 4–5 transcriptions with **two** constants (`write_window_ms` 2000 vs `write_settle_ms` 1000) and one capability gap: a WebSocket write is watched by `record` and by nothing on the learn/heal path | measured in analysis |
| "which row is this?" | two DOM walks that must agree, joined by a comment (`locators.py:42` `_LANDMARKS` vs the `const LM` literal inside `_SPECOF_JS`) | measured in analysis |

Roughly 1,150 lines of in-page JS live as Python strings joined by `+`, with "MUST agree" comments and
no syntax check anywhere in CI.

### 2. Verdicts are stored where evidence should be; two-state values answer three-state questions
`classes b, c ≈ 35 findings`

* `'_pre_confirm' in out` (`flows.py:1092`) — key-presence as evidence.
* `anchor_id=None` carries **six** meanings at one gate: `locators.py:456` reads
  `not (spec.anchor_source == "row" and spec.anchor_id)` as "no guard", and the value means variously
  *no landmark*, *the anchor came from a label/heading so identity was never asked for* (R4.34), *the
  row genuinely has no token* (accepted residual), *the walk stopped at the wrong container* (R4.37),
  *the identity was redacted*, and *legacy flow*. This is exactly what defeated both R3.7 attempts, and
  it is why `D5` says attempt 3 must change the sensor class. **verified** (`locators.py:452-459`).
* `scope_fingerprint() == ""` reads as "no drift" on the replay side and "ungated" on the record side.
* B1 made `RunRecord.landed` three-state for a reason it wrote a paragraph about — and added a
  two-state `BatchRowResult.landed` one screen down in the same diff.

### 3. The net is regression-shaped, browser-only, and slow
`class g ≈ 21 findings`

The suite collects **1,091** tests and builds its fixtures by hand: **76** handler-class definitions
across 34 files and **53** `_serve` helpers, with no shared fixture — there is no `tests/conftest.py`
at all, only a 2-line root `conftest.py` that sets `sys.path`. Of the 836 tests `.test_durations`
measures, **414 take ≤0.5 s and total 9.6 s** (0.8% of measured time) while the other 422 carry the
rest — and the fast ones **cannot be selected**, because there are no markers. *(All verified here.)*

**Since measured, and the estimate was wrong.** This paragraph originally carried an analysis estimate of
"~887 browser-driving tests across ~61 files", hedged as not-re-derived because only a runtime launch
counter could settle it. Step 0.1 built that counter, and it settles it: **502 browser tests and 600 fast
ones of 1102**, with the fast tier measured at **46.8 s** against the full suite's 31 minutes. The
estimate was 1.8x too high. Recorded rather than quietly corrected, because "a number this document could
not reproduce" was the reason it was hedged, and the hedge earning its keep is the point.

**And the counter found a flaw in itself, which is the more useful result.** Attribution is
order-dependent: when a module shares browser work through a fixture, whichever test triggers it first is
charged for the launch and its siblings are charged nothing — so they classify as fast while being unable
to run without a browser at all. All 15 non-launching tests in `test_drift_bench.py` did exactly that, and
every one launched the moment the tier deselected the sibling priming the bench. The refusal made it
loud rather than slow, which is why it was found in the first run instead of by a confused reader later.
The manifest is therefore a **fixed point** — promote what the fast tier catches until it catches nothing
(`scripts/derive_test_tiers.py`, converged in one round) — and the CI fast job keeps it one. The local suite is 21–31 min and Windows-only, which
CLAUDE.md already records as weaker evidence than CI on two measured axes. The audit is the only
instrument that finds *missing* guards (7 of 7) and its precision is ~10% (20/2, 26/2, 22/3
candidates/confirmed), so refuters are mandatory and each round is expensive. The scheduled mutation
sweep (S16) does not exist. `measured in analysis`: the 11 wiring mutations of B1's record plumbing
survive the **entire** suite.

### 4. Fix code carries 5–16x the density of the code it replaces
Round 2: 9.4 defects/kLOC of fix code. Round 3: 28/kLOC, none refuted. B1: 9–12 in ~600 lines of a
*non-write-safety* accounting slice, and the audit run for this document confirmed ten more. F2's own
fix inside B1 guarded the relearn leg and left the three `_attempt_replay` legs open — the register's
"guard on a sibling path" predictor firing *inside* the PR that cites it. Three of B1's findings had
the same shape as the fix they were fixing.

**This is the argument against a rewrite, and it is the argument for making each step small,
deletion-heavy and mechanically pinned.**

### 5. Import-time binding and import-time config make instruments inert
`class h`

`flows.py:41` is `from .flow import run_cached` — patching `ultracua.flow.run_cached` never reaches
`_attempt_replay`; only the module binding does. `tests/test_inviolable_properties.py:565-574` already
patches it correctly and says why in a comment. `config.py` loads `.env` at import, which is how a
`provider=None` test drove real API calls locally (S8). Nine test files bind `async_playwright` at
module import, so a name-level fast-tier wrap would be inert — the S14 trap reproduced inside the
instrument meant to end it. **verified.**

### 6. Timer-as-boundary and sensor-class questions are not refactor problems
`classes c, e ≈ 36 findings`

R3.2 (4 attempts), R3.7/R4.34/R4.37 (2), R4.5 (2), R4.27, R4.38, R4.39. Attempts here have been green
and wrong five times. `D5` binds. The only admissible next move is a **zero-src shadow spike** of a new
sensor class, scored against server truth on a population that contains the two shapes that killed
attempts 1 and 2. Nothing in this plan attempts one.

### 7. State that must stay consistent lives in three prose documents
47 of the last 71 PRs touched `docs/open-defects.md` — the most-touched file in the repo last month,
ahead of `flows.py` at 31. The register is prepend-layered, so the current truth of an item is only
known after reading its whole stack (R3.7 ~150 lines, R4.22 ~250). Counts have drifted twice, which is
why a 227-line regex test exists to police prose. `docs/parked/README.md` is cited by `CLAUDE.md:30`
and by the register but does not exist on main. `flows.py` is ~40% comment/docstring; `flow.py` ~38%.

For LLM agents this is causal, not cosmetic: the mandatory read is ~170k tokens, so **every session
starts from a partial read** — which the register itself names as its failure mode.

### 8. Reporting surfaces enumerate the LOUD set; guards live in wrappers
`classes j, d ≈ 20 findings`

`cli.py:951` still enumerates `status in ("failed", "invalid")` — the fifth instance of the
R3.9/CLI-1 shape. And a reproduced example of the wrapper hole (now **R4.42**): `cli.py:652` returns
*"is not quarantined (status: refused) — nothing to release"* **before** `release()` is called at
`cli.py:655`, while `flow.py:207` tells the operator to clear a learn refusal with exactly that verb
and `flows.release()` (`flows.py:1646`) already does it. The R3.13 remedy is dead through the
operator's only surface. **verified.**

---

## 2 · What the history measures

| Half-month | PRs | feat | fix | docs | other | src +/− |
|---|---:|---:|---:|---:|---:|---|
| Jun 17–30 | 74 | 40 | 15 | 7 | 12 | +7,303 / −1,301 |
| Jul 1–15 | 19 | 13 | 2 | 3 | 1 | +2,446 / −286 |
| Jul 16–31 | 10 | 4 | 3 | 2 | 1 | +2,387 / −99 |
| **Aug 1–16** | **62** | **4** | **29** | **17** | 12 | +4,656 / −718 |

Last month (PR #95–#165, 71 PRs): **7 feat : 32 fix : 19 docs**. Src throughput is collapsing while PR
count is not — the src share of added lines fell from 34% to 11%, and 30 of the 62 August PRs touched
zero `src/` lines. A fix PR ships 2.5 test lines per src line (a feature ships 1.0); the median
last-month fix PR is 108 src / 307 test / 109 doc lines.

Concentration: 88 of 165 PRs touched `flow.py` or `flows.py`; 41 of 49 fix PRs touched at least one of
`flow.py` / `flows.py` / `recorder.py` / `locators.py` / `safety.py`.

CI: the Windows shard went 11.6 → 17.5 min in eight days (~0.75 min/day) against a 25-minute job
timeout — roughly ten days of headroom at that slope. `.test_durations` covers 836 of 1,091 collected
tests. `measured in analysis` from GitHub Actions job durations, not from the docs (CLAUDE.md's
"~21 min" is stale).

---

## 3 · PR #165 today: ten more, all one shape

Four lenses produced 35 raw findings; the top ten by severity were handed to independent skeptics
briefed to refute them. **All ten came back CONFIRMED**, severities mostly corrected to medium/low.
None violates an inviolable. They are **not yet filed in the register** — filing them is step 0.2a
below, as one family.

**Filed 2026-08-17 as `R4.44`–`R4.53`**, in filing order, as one family. They were labelled
`B1-A1…B1-A10` here while unfiled, and the mapping is in column 1 below. An earlier draft of this
document reserved R4.42–R4.51 for them; that was a mistake of exactly the kind this document is about —
reserving a block for unfiled work goes stale the moment anything else is filed first, which is what
happened (R4.42 and R4.43 are the two findings §4 surfaced).

| filed as | Finding | Where |
|---|---|---|
| **R4.44** (B1-A1) | An attempt whose `run_cached` **raises** drops its own LLM spend, traces and minted keys, and leaves `ok`/`failure_code` stale from the previous attempt — F2's fix wrapped the relearn leg only | `flows.py:2191-2233` |
| **R4.45** (B1-A2) | `record.usage == {}` on the miss / escalate / precheck / pre-attempt-refusal / raise exits, against `RunRecord`'s own docstring ("always populated") | `flow.py:192`, `flow.py:1096-1099` |
| **R4.46** (B1-A3) | A usage-less later attempt flips a priced total to `None` with no reason flag (`_absorb_usage`'s sticky-`None` meets an absent key) | `flows.py:2097-2120` |
| **R4.47** (B1-A4) | `test_run_record_is_populated_on_a_FAILED_replay` asserts one default and two truthy values; population-only-at-success stays green | `tests/test_flows.py:1039-1065` |
| **R4.48** (B1-A5) | Eleven wiring mutations of the record plumbing are invisible to the whole suite | `flows.py:2843/2921/2924/2980/2987/2990` |
| **R4.49** (B1-A6) | `record.failure_code` speaks the internal `kind` vocabulary while the raise uses `_classify_replay_failure(kind).code`, and can name a different attempt than the exception | `flows.py:2182-2186` vs `:441` |
| **R4.50** (B1-A7) | `llm_calls` / `traces` / `healed_steps` / `total_ms` exclude the relearn while `usage` includes it | `flows.py:2971-2988` |
| **R4.51** (B1-A8) | The headline claim ("a 0-LLM replay now says observed zero") has no end-to-end pin: an engine reporting UNKNOWN on every replay passes every test | `flow.py:1085`, `:1240` |
| **R4.52** (B1-A9) | `BatchRowResult.landed` is a two-state bool that reads `False` on successful write rows and on crashed rows — the trap the same PR wrote a paragraph about | `flows.py:3562` |
| **R4.53** (B1-A10) | A key-less teacher (`ScriptedProvider`, `MockProvider`) is classified as an *unobserved spender* (cost UNKNOWN), contradicting `flow.py:915-916`; and `accounting_failed` is sticky across runs | `obs.py:235-241` |

**The structural reading, which is the point.** The record is written at **10 sites in two functions** —
`flows.py:2184-2185, 2197-2199, 2224-2233, 2387, 2843, 2921, 2924, 2980-2981, 2987-2988, 2990` — with
three helper mutators (`_absorb_usage` :2097, `_forget_negative_write_evidence` :2123, `_mark_ok` :2141)
each covering a different subset of fields. Accounting exists at three layers with different lifetimes:
a watch inside `flow.py` per `run_cached` (lost on raise, because `FlowReport` is a return value),
`_absorb_usage` per `replay()`, and an ad-hoc third watch for the relearn. Every raise path is therefore
a special case at the boundary between a return-value report and an out-param record. **verified.**

And exactly **two** tests in the whole suite pass `record=` to `replay()` — `tests/test_flows.py:1026`
and `:1059` — so eight of those ten write sites have never been reached by a test. **verified.**

One hop downstream, every existing consumer un-makes B1's central guarantee: `benchmarks/variance.py:47`,
`benchmarks/drift_bench.py:651` and `evals/run.py:240` all do `.get("cost_usd") or 0.0`, collapsing
"unknown" back to zero. **verified.**

**Ten patches would be ten more copies of the shape that produced them.** Eight of the ten are literally
"a record site that was not written".

---

## 4 · What NOT to do

Recorded here so nobody re-proposes a refuted shape. Each was argued by at least one designer and
rejected by the judges or the critic.

* **Do not rewrite the engine, and do not split `flows.py` into a package first.** ~15k lines of fix
  code at the measured density is ~200 defects with no instrument to find them; and the S14
  import-binding trap silently detaches the ~27 `monkeypatch.setattr(flows_mod, …)` sites, turning green
  cells inert. A split is worth *pricing* only after the seams shrink, and only with an AST pin that
  package modules import sibling **modules**, never names.
* **Do not add a src-side engine/deps seam** (`engine: Engine = run_cached`). Monkeypatching the module
  bindings `flows.py` already holds reaches every attempt (`tests/test_inviolable_properties.py:565-574`),
  and a def-time default binds the *function object*, which would make existing patch sites inert. All
  three judges refuted this independently.
* **Do not fix the ten B1 defects one at a time.** They become strict-xfail cells in the exit-set matrix
  and are disposed by one sink plus two small companions.
* **Do not attempt R3.7 attempt 3, R3.2 attempt 5, R4.5 attempt 3, R4.27, R4.38 or R4.39 with a refined
  inference.** `D5` binds. `anchor_id=None` and the `setTimeout(…, 0)` turn reset are untouched here.
* **Do not unblock D0**, narrow what counts as a write, add a URL denylist, touch `MUTATING_KEYWORDS`,
  or add **any** refusal as part of a migration. `WriteClass` (step 1.6) carries no refusal.
* **Do not delete the explicit Unknown accounting state** or claim "zero recorded = zero spent" from an
  ambient ledger while the SDK choke-point pin leaks: `tests/test_inviolable_properties.py:149-150`
  allowlists `src/ultracua/vision.py` (R4.41), and `_SDK_CTORS` at `:148` is
  `("AsyncAnthropic", "AsyncOpenAI", "OpenAI", "GenerativeModel")` while `llm/gemini.py:101` constructs
  `genai.Client()` — **not matched at all**. Two of four constructions are outside the pin's inference,
  and `found_in_leaves` is exactly 3 against a `>= 3` floor. Filed as **R4.43** (distinct from R4.41),
  which also records the interaction: R4.41's own "cheap and wrong" remedy drops the count to 2, so the
  anti-vacuity assert fires first and the run misdiagnoses itself.
* **Do not sweep the fixture servers to HTTP/1.1** to chase an intermittent sub-resource drop
  (R4.56), and do not make the tests TOLERANT of it either. Measured: HTTP/1.1 buys 8 → 6
  connections per page load (25%, because Chromium opens ~6 in parallel regardless), and **8 of
  the 38 handler-defining files write a body with no `Content-Length`** — under HTTP/1.1 their
  clients wait for a close that never comes, so the sweep trades eight hang candidates for a
  quarter of the sockets, justified by one unreproduced failure. Tolerance is worse: the cause is
  unknown, and suppressing an unexplained signal is how R4.26 spent three releases mislabelled as
  a flake. The seam (step 0.7) is where this closes.
* **Do not hand-mark tests as browser/fast**, wrap `async_playwright` by name, or accept "any exception"
  as "refused".
* **Do not build the Chromium pool as a speed fix** (2.9-min ceiling, loop-bound `Browser`), and **do not
  use jsdom for the JS walks** (`innerText`, `getComputedStyle` and `window.event` differ — the walks
  depend on exactly those).
* **Do not edit JS logic while moving it to files**, unify cssPath depth (5 vs 6), merge the
  SNAPSHOT/SCOPE/ACTIONABLE selector lists, or generate the LM vocabulary from Python in the move PR.
  Byte-identical sha256 pin first; merging the selector lists moves every recorded `precond_scope` hash.
* **Do not fix semantics inside a refactor PR.** Each of these is filed with a repro pointer and taken
  as its own RED slice, never en route: R4.12 (`pre_write` never reaches `_learn`); the auto-mode
  fall-through that re-authors a cached **write** flow after the gate refused to re-drive it
  (`flow.py:187-189` — verified: there is no `mutating` check between the failed replay and `_learn`);
  `scope_fingerprint() == ""` passing the precise gate (`flow.py:1334`); the second substring classifier
  in `verifiers.py`; the `_kind3` discard (`flows.py:2961`).
* **Do not adopt a type checker, pydantic or a lint repo-wide in one PR.** A wall of red is a fix-code
  generator and green-by-ignore is the S14 shape. Type-check an allowlist of the new typed modules that
  grows per step.
* **Do not let B3 source cost or outcome from `RunRecord`** or push observability hacks into `src/` for
  the benchmark's sake.

---

## 5 · The plan

Four phases. Phase 0 touches no `src/`. Phase 1 is bounded src changes, each ending with the old shape
inexpressible. Phase 2 is the benchmark, harness-side, starting day 1 in parallel. Phase 3 is priced and
**un-committed**.

**The ordering that must not change:** the exit-set matrix (0.3) before the sink (1.5); distinct codes
(1.4) before B3; `--arm-oracles` before any scored B4 run; a shadow table before any `D5`-gated src
change.

### Phase 0 — instruments, zero `src/` (~12 days)

| # | Status | Step | Disposes | Pinned by | Size |
|---|---|---|---|---|---|
| 0.1 | done #168 #170 | **Fast tier.** New `tests/conftest.py`: a session plugin wrapping `BrowserType.launch` **and** `PlaywrightContextManager.__aenter__` *on the class* — **(critic)** `start()` is a one-line delegate to `__aenter__` and 21 test sites use `async with`, so wrapping `start` alone is half-inert. `--store-browser-marks` writes per-test launch counts; `--tier fast` deselects listed browser tests **and** installs a raiser, so an unlisted browser test fails loud. Key scrub. The 138 s + 40 s tests move to their own CI job; `.test_durations` regenerated | g, h; the 21–31 min loop | violation armed and seen red under `--tier fast` for both entry styles; a browser cell increments the counter by exactly 1; every collected id classified or collection fails naming ids | S · 1.5 d |
| 0.2a | done #171 | **File B1's ten in the prose register** as one family, taking the next ten free ids (~10 lines). **(critic)** `tests/test_register_count.py:167-180` fails any strict xfail whose reason names no open `R3.x`/`R4.x` id, and `_ID_IN_CODE` (`:195`) recognises only that shape — so this must land **before** 0.3, or 0.3 lands red | ordering hazard | the existing count guard | XS · 0.2 d |
| 0.2b | done #172 | **Register as structured data.** `docs/register/<id>.yaml` (id, title, status, severity, inviolable, class letter, attempts, `blocked_by`, `next_attempt_requires`, pins, disposition); `scripts/render_register.py` generates the index/state blocks; the 4,401 narrative lines move **unchanged** to `docs/register/history/` (frozen, append-only). `D5` becomes a schema rule: open + ≥2 attempts ⇒ `blocked_by` + `next_attempt_requires` required. **(critic)** no line-count target for CLAUDE.md — its operational lessons are the one text every session reliably loads; pin only "no hand-typed counts outside the generated block" | l, k; the 170k-token read | schema (anti-vacuity ≥83 ids, ≥10 open); render byte-equality; rendered counts equal today's; every strict xfail names an open id; a PR that deletes an xfail or adds an id-naming test must flip that id's status in the same diff | S · 1.5 d |
| 0.3 | done #175 | **Exit-set matrix over a fake engine.** `tests/_fake_engine.py` installed by patching the bindings `flows.py` holds — `run_cached`, `_precheck_done`, `refresh_auth`, `learn`, and **(critic)** `_make_finalize`/`_make_pre_write` so the `out` dict is scripted explicitly; all six added to a module-bindings AST pin. Drives `replay()` through every exit **derived by AST** over its own `raise`/`return` nodes — **(critic)** not a hand list — including preflight refusal, `on_step` raising inside the auth-refresh retry, and record reuse across calls. Fidelity cells for read **and write** shapes against the real engine on existing fixtures. The ten B1 defects become strict-xfail cells RED against main; the vacuous cell gets specific values; the 11 wiring mutants registered and proved killed | g; RED-first for 1.5 | every derived exit hit ≥1 cell (printed); premise counts per cell; fidelity cells; `prove_red` 11/11; xfails machine-checked RED | M · 3–5 d |
| 0.4 | **done** (0.4a; 0.4b at 0.115.0) | **RED-in-CI + red-proof + ratchets.** Run each PR's *new* test ids against main's `src/` in a worktree; a src-touching PR whose new tests all pass against main fails. **(critic)** ImportError-against-main is *inconclusive*, not a label-shaped opt-out — a PR adding a src module ships a registered mutant instead; the job's self-test is a unit test of its verdict function. `assert_ratchet(name, derived_sites)` fails on growth **and** staleness | g, f | the job asserts it found ≥1 new test when the diff adds `def test_`; each ratchet asserts a minimum hit count first | S · 1.5 d |
| 0.5 | held -> 1.1 — **TRIGGER FIRED** (1.1 landed 0.115.0 without it); `_SDK_CTORS` taken at 0.4a | **Contract tests for every "must agree" pair**; `node --check` over every assembled `*_JS` payload; add `Client` to `_SDK_CTORS` and raise the anti-vacuity threshold (test-only) | l, f | each pair asserts a minimum match count before equality; delta sets frozen | S · 1 d |
| 0.6 | **done, 0.136.0** | **Scheduled mutation sweep (S16).** Delivered: the nine known mutants as `tests/mutations/known_nine.py` (**9 killed, 0 survived** — the first time the 0.75.0 measurement has been reproducible, and SIX of the nine sites had moved and were re-expressed against the property rather than the line number); `scripts/mutation_sweep.py`, which DERIVES the registry set from `tests/mutations/` and the tier split from the tier manifest; and `.github/workflows/mutation-sweep.yml`, weekly plus `workflow_dispatch`. Seventy-five mutants across seven registries. **The generic-operator half is REFUSED on measurement and filed as R4.108** — 2848 mutants over the six hot files, 70.4 h serial against the fast tier against a 6 h job cap, and, worse, a narrow killer measured at 38% survival on the best-case file with **all six of those survivors killed by the fast tier**. The missing component is a coverage-derived killer selection, not more mutants | g, a | `known_survivors` only shrinks; the nine known mutants killed every run | M · 2 d |
| 0.7 | held -> 2.3 — not fired; §13 row 8 lands it there | **Shared fixture server** — `serve()` owning the protocol version, the `Content-Length` framing and the synchronous-reveal discipline, plus a `Site` recorder with a `saves` count and the common stubs; migrate ~8 files as proof. **Now evidence-backed (R4.56):** 38 files define a fixture handler, **8 of them write a body with no `Content-Length`**, and a sub-resource that silently fails to load surfaces as a JS `ReferenceError` inside the page rather than as a connection error. That is why the one-line sweep is REFUSED — measured, `HTTP/1.1` buys 8→6 connections per page load (25%) and would leave those 8 files hanging on an unframed body. The seam is the fix: correct by construction for every future fixture, and the 38 migrate as they are touched | g, n | ratchet on files defining their own handler class (only shrinks); collection count unchanged; the `serve()` self-test asserts framing + HTTP/1.1 + a `reveal_sync` page's state present on the FIRST snapshot | M · 3 d |

### Phase 1 — bounded `src/` changes (~20–30 days incl. audits)

| # | Order | Step | Disposes | Pinned by | Size · WS |
|---|---|---|---|---|---|
| 1.1 | **done 0.115.0** (6 · §13) | **Keyword-only engine chain.** `*` after the identity prefix in `_learn`/`_learn_n`/`_replay`/`_verify_by_replay`/`_replay_step`/`_author_steps`; internal call sites rewritten to keywords. `run_cached`'s public signature unchanged. **(critic)** the arity pin cannot see a *mis-keyed* placeholder (`flow.py:752` passes four `None`s whose swap is type-silent), so a forwarding-identity test comes with it: stub each inner coroutine, pass a unique sentinel per kwarg, assert every kwarg arrives under the same name with `is` identity, DELIBERATE_DROPS asserted both ways, shown RED against a mis-keyed scratch copy | i | AST positional-arity pin + the forwarding-identity cell; RED-in-CI; suite + 0.3 goldens byte-identical | S · 1.5 d + audit · no |
| 1.2 | 1 · DONE 0.109.0 | **`flow release` reaches `release()`.** **(critic)** *delete* the pre-check at `cli.py:652`, do not widen it: `health()` reports `refused` only when the flow is **not** cached (`flows.py:2068`), so a refusal recorded with a stale recipe still reads "nothing to release". `release()` returns what it cleared; the CLI prints that. ≤10 src lines | **R4.42**; R3.13 remedy hole (d, j) | RED CLI cells for **both** shapes (learn-refused uncached; refused-with-stale-recipe) driven through argparse | S · 0.5 d · no |
| 1.3 | 5 · §13 | **A "cannot-spend" third state in `obs.py`.** **(critic)** only the `totals = UsageTotals()` variant — never a new attribute probe on an owner, because a `spends: ClassVar` probe re-creates commit `00888b4`, which tripped inviolable #1's `_Exploding` tripwire one week ago. AST cell: `RouterWatch.__init__` reads only `router`/`totals`. `accounting_failed` becomes run-scoped. Fix the readers so `None` renders as *unknown* and never sums to 0.0 | B1-A10; b, c, p | `observe(ScriptedProvider([]))` → observed, 0.0; the `_Spender` stub stays unobserved; `test_a_navigate_only_replay_never_reaches_a_provider[repair]` named as a pin; drift_bench baseline unchanged | S · 1 d + audit · no |
| 1.4 | **3** · §13 | **Distinct codes + one `outcome_of` + tri-state on the siblings.** Codes for the ~10 base-class refusals; `outcome_of(exc) → Outcome(code, retryable, landed)` replaces the getattr triples that *construct* `BatchRowResult`/`ToolOutcome`/`DryRunReport`/`FleetRun`. **(critic)** the two ledger-arming reads (`flows.py:3771`, `mcpserver/server.py:444`) stay byte-identical — they are R3.3's consumers. Must land before B3 freezes its vocabulary | B1-A6, B1-A9; j, b | ratchet bare `raise FlowReplayError(` 24 → 0; a cell per subclass at its raise site asserting (code, retryable, landed); `test_landed_arms_the_ledger.py` + the write-safety matrix (both clauses) named as pins | M · 2 d + **2 audits** · **yes** |
| 1.5 | 2 · DONE 0.110.0 | **THE SINK — single-exit `RunRecord`.** Replace the 10 write sites and the three helpers with one `_RecordSink`: append-only `AttemptRecord`s + `finish(exc_or_None)` called exactly once, **total by construction** (an internal error becomes `record.note`, never an exception over the original). `replay()`'s body becomes `_replay_body`; the wrapper does `try … except BaseException as exc: sink.finish(exc); raise`. Per-attempt usage comes from a watch the *wrapper* owns, so the raise path is a non-event and **`flow.py` is untouched**. `failure_code` derives from `exc.code`. **(critic)** landed/committed rule: True if any attempt evidenced True; else **None** if any attempt is unknown (raised, precheck-skip, relearn-raise) or none completed; else False — and today's values for both precheck exits and the retry-raise→repair path are frozen in the 0.3 golden **before** the sink is written; `exc.landed` arming stays byte-identical. `LearnResult` gains a **required** `report=` keyword | B1-A1…B1-A8; a, b, i, n, p | the ten strict xfails **flip** (strict forces it); every other cell byte-identical or in an argued golden diff; AST: no `record.<field>` write outside the sink; ratchet 10 → 1; `prove_red` 11/11 still killed; cells for reuse-across-calls, usage equality while both watches coexist, relearn-with-no-report | M · 3 d + **2 audits** · **yes** |
| 1.6 | **done 0.116.0** (7 · §13) | **`WriteClass` value object + `FlowSpec.key`.** The *named* questions the raw sites ask — `declares_write`, `is_write`, `needs_confirm`, and **(critic)** two separately named multiwrite questions (`declares_multiple_barriers` for `MutateSpec.is_multiwrite`, `recipe_has_multiple_writes` for `cached_writes`) because `_auth_retry_allowed` keeps them as separate arms with different messages; collapsing them is R3.5's own failure shape. Carries **no refusal**. `FlowSpec.key` replaces the 24 transcriptions | k, a, l, f | ratchets 33 → 0 and 24 → 0; a **printed per-site conversion table** so a mistranslated site is visible on sight; write-safety matrix (both clauses) and the exit-set matrix byte-identical | M · 2.5 d + **2 audits** · **yes** |
| 1.7 | **done 0.117.0** (8 · §13) | **Printed door `Policy` table** naming what each raw door permits *today* (CLI root, daemon, `run_many`, MCP, `replay`), including `auto_reauthor_writes=True` — so `flow.py:187-189` becomes a visible decision item rather than a refactor side effect. Daemon validates `mode`/`provider` against closed sets before the engine | d, m | a test prints the Policy per door and asserts equality with a committed table; a liveness corpus of read flows replays green through every door | S · 1 d · no |
| 1.8 | **done 0.118.0** (9 · §13) | **`RunOptions`/`RunHooks`** threaded through the engine instead of 20–23 scalars; `run_cached` keeps its public kwargs. Every existing asymmetry preserved as-is in a DELIBERATE_DROPS table (R4.12 included — *preserved, not fixed*). Separate PR from 1.1 | i, a, d | introspection: every field read in `run_cached` or reachable in every inner function it applies to, except DELIBERATE_DROPS — checked **both ways**; a hook-fire count table per (mode, hook) captured before and asserted after | M · 3 d + audit · no |

### Phase 2 — the benchmark, harness-side, from day 1 (~14 days, parallel)

| # | Slice | Depends on | Notes |
|---|---|---|---|
| 2.1 | **B2** — substrates (Odoo + Gitea), reset, readiness hook, harness skeleton, and the **boundary ledger**: derive every module-level binding of `build_client`/`build_router`/`get_provider` from the live import graph and wrap them, so cost is metered where `Router.complete` meters it (`llm/base.py:71-75`), never from `RunRecord`. **(critic)** score with `count` and derive the 0-LLM claim per call *site* — a data replay legitimately extracts once, so a blanket `refuse` mode would bucket every extracting read as `llm_reached`, which is over-refusal in the instrument | nothing in `src/` | vision/gemini configs refused as a **stated bound** until the SDK pin covers them |
| 2.2 | **B3** — outcome vocabulary + record/baseline format; QUIET as an allowlist; the aggregator **fails** on cost `None`; `refused` sub-buckets derive from 1.4's codes, never message labels; `RunRecord` consumed only as a cross-check → a `record_disagrees` bucket | 1.4 | if 1.4 slips, sub-labels are reported unbucketed and re-derived later |
| 2.3 | **B4** — the 14-scenario paired corpus + server-side oracles + the key-logging proxy; write oracles assert the changed record's **linkage/identity**, never a count; `--arm-oracles` shows every oracle RED before any scored run; a scenario with zero premise counters is not scored | 2.2 | the long pole; adversarial pass on this PR |
| 2.4a | **B5** — the WEEKLY run (sharing 0.6's workflow, as specified), gated against `baselines/customer_v1_gitea.json`, plus a free `substrates` preflight on every PR and an honesty page whose open-id list is machine-checked against the register in BOTH directions | 2.3 | ubuntu-only weekly is a documented blind spot; the run REFUSES without a key rather than skipping |
| 2.4b | **B5** — the Odoo half. Blocked, not deferred: 58% of Odoo's replay refusals are the mutation gate refusing a step R4.27 misfiled as a write, so a baseline cut now is dominated by a filed defect (R4.105) | D6 | not a plan step, hence `pending` rather than `held` |

### Phase 3 — un-committed, one at a time, only when a number indicts it (~26 days)

Statuses as StrEnums + one Verdict per surface family + a single `SystemExit` funnel (ratchet 34 → 2) ·
typed channels (`FlowReport` fields with `extra` as a compat view; usage attached once at
`run_cached`'s single exit; `StepMeta`; `ResolveReport`) — **(critic)** the `did_heal` → `HealOutcome`
split is a *semantic* change and gets its own RED slice · `approval_bindings_stale` + `fleet_admission`
· bare-`except` tagging and ratchet (the `scope_fingerprint == ""` refusal is priced separately on an
idle host) · JS to resource files behind a byte-identical sha256 pin with `node --check` and a
per-module Chromium harness · the ambient run-scoped ledger at `Router.complete` (**gated** on the SDK
pin covering all four constructions *and* the harness ledger being live as a cross-check) ·
`FinalizeOutcome` replacing the `out` dict in **both** consumers — the R3.3 surface that cost six
predicate versions, so last, alone, or not at all · the zero-src `D5` shadow spikes · `.env` out of
import.

---

## 6 · The first three PRs

**PR 1 — fast tier** (tests/CI only). `tests/conftest.py` with the class-level launch counter and the
raising fast tier; `tests/.browser_tests.json`; heavy tests to their own CI job; `.test_durations`
regenerated; `scripts/check_shard_coverage.py` extended. *Acceptance:* under `--tier fast` both
`BrowserSession().start()` and `async with async_playwright()` raise; a browser cell increments the
counter by exactly 1; every collected id is classified or collection fails naming ids; the tier runs in
<60 s with zero launches asserted.

**PR 2a then 2b — register.** 2a files B1's ten in the prose register (~10 lines) so PR 3's xfails
name ids the existing guard recognises. 2b migrates to YAML + a renderer, freezes the narrative, and
replaces the regex test. A human spot-checks the six `D5`-blocked entries by hand.

**PR 3 — exit-set matrix** (tests + scripts only). *Acceptance:* runs in the fast tier in <3 s; every
AST-derived exit hit by ≥1 cell (printed); fidelity cells green for read and write shapes; the ten
strict xfails RED against main (machine-checked); `tests/test_flows.py:1039` now fails if the population
block at `flows.py:2217-2233` is removed; `prove_red` reports 11/11 wiring mutants killed where today
all 11 survive.

**Re-price checkpoints.** After PR 3, and again after 1.5's audit. If the fake engine turns out to be
another inert stub, or 1.5's audit finds more than ~3 findings, the plan is re-priced before Phase 1
continues. **(critic)**: the headline estimate must carry second audit rounds for 1.4/1.5/1.6 and one
audit each for 1.1/1.3/1.8 — B1 was estimated at 3–4 days, took 8 commits and two audits, and still
carries ten confirmed defects.

---

## 7 · Disposition of the ten B1 defects

| Finding | Disposed by | How |
|---|---|---|
| B1-A4 (vacuous cell) | PR 3 | rewritten with specific values; a raise-path cell added |
| B1-A1, B1-A2, B1-A3 | 1.5 | the wrapper watch + `finish(exc)` around the one `_attempt_replay` call and the one `learn()` call; explicit zero on the miss exit; `UsageTotals` objects summed, never re-summed dicts |
| B1-A5 (11 mutants) | PR 3 now; 0.6 weekly | golden cells kill all 11 **before** the sink lands; the sweep keeps them dead |
| B1-A6 (vocabulary) | 1.5 + 1.4 | `failure_code = exc.code` at finish; distinct codes so B3 never buckets by message |
| B1-A7 (relearn excluded) | 1.5 | `LearnResult(report=…)` as a required keyword — a missed site is a `TypeError` |
| B1-A8 (no e2e pin) | PR 3, then B2 | the ok cell asserts usage == the fake router's delta with cost 0.0 and no unobserved flag; after B2, one e2e cell asserts `record.usage` == the harness ledger's delta on a real extracting replay |
| B1-A9 (`BatchRowResult.landed`) | 1.4 | tri-state, with its ok-write value stated as a golden cell first |
| B1-A10 (key-less teacher; sticky flag; readers) | 1.3 | as above |

---

## 8 · Process changes

* **Suite tiers**, machine-enforced: `fast` (pre-commit, <60 s, raises on any Chromium launch, keys
  scrubbed) → `browser` (PR CI, 2 shards × 2 OS + a heavy job, union asserted) → `nightly` (weekly
  mutation sweep, shape grammar, customer bench). Local requirement becomes the fast tier plus the
  browser files a change names; **the full key-less suite stays CI's merge gate on both OSes**. Keep
  "green before merge"; drop "green before commit" — the 21–31-minute local run is measured to be
  weaker evidence than CI, and its cost is what pushed B1's author to targeted subsets, which is how
  seven breakages got through.
* **Register as data.** YAML is the source of truth; prose state blocks are generated; narrative is
  frozen history; the entry rides in the same PR as the change, enforced by a check that can actually go
  red (a deleted xfail or a new id-naming test must flip a status in the same diff). CLAUDE.md keeps its
  operative rules and lessons and loses hand-typed counts. The mandatory session read drops from ~170k
  tokens to ~10–30k.
* **PR shape**, enforced rather than advised: commit 1 = the pin/golden/ratchet with its anti-vacuity
  self-test, shown RED against main; commit 2 = the change. A refactor PR carries **zero** semantic
  edits; anything semantic found en route is filed with a repro pointer. Ratchets only shrink;
  `--update-golden` requires the diff in the PR body.
* **Audit policy.** Mandatory pre-merge adversarial audit in an isolated worktree for any src PR
  touching `flow.py`, `flows.py`, `recorder.py`, `locators.py`, `safety.py`, `cache.py`, `obs.py` or
  >100 src lines — and a **second** audit of the rework for 1.4/1.5/1.6, because B1 needed two rounds.
  Findings reproduced by hand before acting (~10% precision). Mechanical steps get the red-proof job and
  the golden diff instead.
* **Freeze outside the plan** for Phases 0–1: nothing lands in the hot files except the numbered steps,
  deletion-shaped ≤10-line wrapper fixes for reproduced operator holes (1.2 is the template), and
  **(critic)** reproduced inviolable-class findings, which carry the full RED-first + matrix-dimension +
  audit requirements. Without that last exemption a critical finding would have no sanctioned path.
* **Comments.** As a function is touched, narrative defect history becomes a `# pinned:` line naming a
  test id plus a register id; a new "never/always" sentence must cite a test id or not be written. No
  bulk edit.

---

## 9 · How we will know it is working

| Metric | Baseline (measured) | Target |
|---|---|---|
| fix : feat PRs | 32 : 7 last month; Aug 1–16 (29+17) : 4 | **worse** during Phases 0–1 (every step is non-feat), then ≤2:1 within the month after 1.6, ~1:1 by the second |
| confirmed audit findings per src slice | B1: 10 in ~600 src lines; round 3: 28/kLOC | ≤3 per slice on flow-API surfaces; "same shape as the fix it fixes" → 0 |
| local pre-commit signal | 21–31 min, Windows-only | `--tier fast` <60 s, zero launches asserted |
| CI per-shard | Windows 17.1 + 17.5 min, +0.75 min/day, 25-min timeout | −1.5–3 min immediately, then a flat slope; `.test_durations` 836/1091 → 1091/1091 |
| wiring mutants killed | 0 of 11 (B1); 9 of 9 (known) | 11/11 after PR 3; 9/9 every sweep; `known_survivors` only shrinks |
| ratchets (AST-derived, only shrink) | bare `raise FlowReplayError(` **24**; raw `spec.mutate` **33**; `flow_key(spec.` **24**; record write sites **10**; `SystemExit` **34** | 0 · 0 · 0 · 1 · 2 |
| exit-set coverage | 2 e2e cells reach 2 of 10 record write sites | every AST-derived exit hit ≥1 cell, printed |
| `record_disagrees` (nightly bench) | bench does not run | 0 after 1.5; any nonzero is a numbers-indicted slice, not an audit |
| register | 26 open + 4 parked; ~170k-token read | B1 family → 0 by 1.5; open count otherwise **flat** (honest — the `D5` six stay open); read ~10–30k tokens |

---

## 10 · What this plan does not prove, and where it could be wrong

* **The measured fix-code density applies to the instruments and to the sink itself.** The fake engine
  can be inert — S14's stub was wrong twice past every self-test — the sink is fix code on `replay()`,
  and the matrix is built from one author's mental image, which is exactly what defeated both R3.7
  attempts. The fidelity cells, the AST-derived exit set, the totality cell and the audits are
  mitigations, not proof. Hence the re-price checkpoints.
* **Behaviour preservation under the wrapper split is asserted, not proven.** ~27 flows-name
  monkeypatch sites and the name-coupled AST guard at `tests/test_landed_arms_the_ledger.py:807` must
  keep pointing at the function that owns the returns, and a golden over a *fake* engine cannot see a
  page-side timing change. `drift_bench` and the write-safety matrix can — and both are load-sensitive
  and have passed wrong code before (0.73.0, 0.76.0, both R3.7 attempts).
* **The "cannot-spend" state (1.3) is a design decision** either direction of which is defensible;
  getting it wrong flips the whole key-less population's cost between zero and unknown. Pin both
  directions and record the decision before the sink lands.
* **The benchmark may not discriminate.** Odoo can saturate at the classifier (R4.27's 12/12) and
  Gitea's row-identity shape is unmeasured, so weeks may buy a *confirmation* — "over-gated by write
  classification", which D0 and R4.27 already say cannot be fixed by narrowing — rather than a
  direction.
* **Classes c/e (~36 findings) are not reduced.** If next month's defects come from the attribution and
  row-identity surface, the ratio improves less than predicted.
* **Estimates are ranges for a reason.** B1 was estimated at 3–4 days and took 8 commits, two audits and
  still shipped ten confirmed defects. Phases 0–2 are 55–80 days *with* the second audit rounds; the
  fake-engine matrix and the JS move are 1000+-line instruments and are priced ×1.5–2 of a naive guess.
* **Timing on the developer host is noise** (517 MB free, swapping). Steps that wrap the session or
  touch page-side JS are adjudicated on CI or an idle host, or recorded as "unmeasured".
* **This plan is a hypothesis, like every plan in this repo.** `docs/correctness-plan.md` records that 4
  of 7 of its own prescribed fix shapes were wrong because they were written before the code was read.
  Every step above was written with the code open and its `file:line` claims re-verified — and that is
  the same standard those four met. Reproduce before building, and audit the fix, not just the code it
  fixes.

---

## 11 · Relationship to the existing plans

* `docs/correctness-plan.md` stays the sequencing document for **findings**. This document does not
  re-sequence it and closes none of its open items; it changes the *shape* the next fixes land in and
  the instruments that adjudicate them. S13 (evals: port or delete), S15 (pin the residuals) and S16
  (mutation sweep) overlap: S16 **is** step 0.6.
* `docs/realistic-benchmark-plan.md` stays the benchmark's design. Phase 2 here is its B2–B5 with one
  change of substance: **the bench sources every number from surfaces the engine cannot bypass** and
  treats `RunRecord` as an advisory cross-check with a `record_disagrees` bucket — so B3 does not wait
  for the sink, and a residual record defect becomes a number a nightly shows instead of something only
  the next audit finds. §7a's rule ("the benchmark must not push observability hacks into the engine")
  is unchanged and, if anything, strengthened.
* `CLAUDE.md` keeps its inviolables, D0/D5 and its operative rules. Step 0.2b changes where the *state*
  lives, not where the *rules* live.

---

## 12 · Phase 1's execution order — decided 2026-08-18, after Phase 0 shipped

Four of Phase 0's seven instrument steps are built. The open question was whether to finish **0.5,
0.6 and 0.7** first or start Phase 1, and it is answered by **derivation rather than preference**: every
Phase-1 row's *Pinned by* cell was scanned for the instrument it names.

| Phase-1 step | Instruments its own pins name |
|---|---|
| 1.1 | RED-in-CI (**0.4**) + the 0.3 goldens |
| 1.2 | none |
| 1.3 | none |
| 1.4 | ratchet (**0.4**) |
| 1.5 | ratchet (**0.4**) + the 0.3 goldens |
| 1.6 | ratchet (**0.4**) + the exit-set matrix (0.3) |
| 1.7 | none |
| 1.8 | none |

**No Phase-1 step names 0.5, 0.6 or 0.7.** The only gate is 0.4, and only its *ratchet* half is on the
critical path — RED-in-CI is named by 1.1 alone. So 0.4 splits, and the three held steps get a **trigger**
instead of a queue position.

### The order

> **SUPERSEDED by §13's re-derived order, and left standing as the record of a decision rather than as
> a plan.** Its rows carry no status markers on purpose: the answer to "has this shipped" is the STATUS
> INDEX at the top of this document, generated from `docs/plan/state.json`. Reading a status out of a
> superseded table is how 2.1 came within one instruction of being built twice.

<!-- order-table:historical -->

| # | Step | Why here | Audits |
|---|---|---|---|
| 1 | **0.4a — the ratchet library** | Instrument BEFORE touching `src/`. `assert_ratchet(name, derived_sites)` with the six shapes seeded from a derivation, never a typed number, failing on growth **and** on staleness (a ratchet whose derivation finds zero sites is an ERROR, the rule `prove_red` already applies to a stale mutation). 1.4, 1.5 and 1.6 each name a ratchet; landing it first means their counts are frozen before their own edits move them | — |
| 2 | **1.2 — `flow release` reaches `release()`** | ≤10 `src/` lines, no audit, and a user-visible bug: verified live at `cli.py:652`, `if h.status != "quarantined" and not rebaseline` returns early, while `health()` sets `status = "refused"` at `flows.py:2073` — so a learn-refused flow prints *"nothing to release"* and `release()` is never called. A cheap first exercise of the new ratchets against a real `src/` diff | no |
| 3 | **1.5 — THE SINK** | The 0.3 goldens were built for it **and are perishable**: six strict xfails sit RED in `tests/test_replay_exit_matrix.py` naming R4.44/45/49/51/53/57, and `strict=True` makes them flip or fail. It is also the step on the benchmark's critical path — B1's record IS the benchmark's tracking machinery, and this disposes R4.44–R4.53 as a family | **2** |
| 4 | **1.4 — distinct codes** *(1.4a done)* | 2.2 (B3) freezes an outcome vocabulary and must not freeze it against `code='replay_error'` × 24. Ratchet 24 → 0 — landed as **29 → 0**, the honest measure (24 raises + 1 value ref + 4 literals) | **2** |
| 5 | **1.3 — the cannot-spend third state** | Completes the accounting story B3 consumes: 2.2's aggregator is specified to FAIL on a cost of `None`, which is only correct once `None` means *unknown* rather than *unset* | yes |
| 6 | **0.4b + 1.1 — RED-in-CI, then the keyword-only chain** | 1.1's own pin. 0.4b reuses `prove_red`'s scratch-copy machinery — swap "a mutation" for "main's `src/`" — so it is a smaller job now than when it was priced | yes |
| 7 | **1.6 — `WriteClass` + `FlowSpec.key`** *(done, 0.116.0)* | The largest ratchet consumer; wants both 0.4a and 1.4's vocabulary settled. **The two figures quoted here were both wrong, in the way CLAUDE.md warns about**: `spec_mutate_raw` was 27 (not 33) and landed at 0; `flow_key_transcriptions` was 25 (not 24) and landed at **2**, which is its FLOOR — `FlowSpec.key`'s own body, and `cli.py`'s raw `ultracua <url> <goal>` entry point, whose argparse Namespace is not a FlowSpec. Evidence was a 4410-cell DIFFERENTIAL against main over `is_write_flow`, `_auth_retry_allowed` and `_preflight_row`, 0 differing | **2** |
| 8 | **1.7** *(done, 0.117.0)*, **then 1.8** *(done, 0.118.0)* — **PHASE 1 COMPLETE** | 1.7 made `auto_reauthor_writes=True` a visible decision, and MEASURED it: the three doors that can re-perform a write by re-authoring — `cli_root`, `daemon_run`, `run_many` — are exactly the three with no flow-level gates. Filed as **R4.84**, recorded rather than changed, because closing it is a refusal. The daemon's validation also found a SILENT one: `grounding` was an equality test against one literal, so any other value ran without grounding and said nothing (**R4.83**, fixed). 1.8 moved every call site and was therefore last, in its own PR. **It INVERTED the risk 1.1 addressed**: a parameter list ENFORCED withholding (`_learn` could not read `params`; `_replay` could not read `grounding`) and a bundle does not, so `RECEIVED_BEFORE_1_8` carries that enforcement now — measured, `_learn` reads 14 of the 23 it used to receive and `_verify_by_replay` 0 of 11. The hook-fire counts were captured BEFORE anything moved and are unchanged. R4.12 preserved by a named `hooks.without("pre_write")` rather than closed as a side effect | 1.8 only |

### The three held steps, and what un-holds them

**TWO OF THE THREE TRIGGERS HAVE SINCE FIRED AND NEITHER STEP WAS TAKEN** (noticed 2026-08-22): 1.1
landed at 0.115.0 without 0.5, and 1.5 landed at 0.110.0 — five slices before this was written down.
A hold is a decision to defer *until a named event*; once that event happens, silence is no longer the
same decision. Both now carry `trigger_fired` in `docs/plan/state.json`, so the index at the top of this
document states it rather than leaving it to be re-derived by whoever next reads these three bullets.
Neither is re-opened here — that is a call to make with a slice in hand, not in a documentation fix.

* **0.5 — contract tests for the "must agree" pairs.** Held to land beside **1.1** (both are AST-pin work
  over the same tree). **One line of it is taken early, with 0.4a:** `_SDK_CTORS`
  (`tests/test_inviolable_properties.py:148`) does not contain `Client`, and that tuple is what makes
  `llm.build_client` provably the choke point for inviolable #1. Verified safe to widen — the only
  `Client(` construction in `src/` is `genai.Client()` at `llm/gemini.py:101`, inside a leaf adapter, so
  the pin lands green.
* **0.6 — the scheduled mutation sweep. DONE at 0.136.0, and the reasoning here was half right.**
  It correctly said the per-PR half already existed and that the missing piece was the part a merge
  gate cannot afford. It named that part as *generic operators*, and the part that actually shipped is
  the **nine known mutants** — whose killers are page-side properties needing a browser, which is why
  they could never have lived in `red-proof` (it installs no Playwright deliberately). The generic half
  was sized and refused: see R4.108, and the row above.
* **0.7 — the shared fixture server.** Held to **2.3 (B4)**. Its payoff is proportional to the number of
  fixtures WRITTEN after it exists, and B4's 14-scenario paired corpus is by far the largest such batch.
  Landing it now migrates 8 files and prevents nothing; landing it with B4 makes 14 new fixtures correct
  by construction. R4.56 stays open and cited until then.

### A hole in 1.5's acceptance criterion, found while writing this

§7 promises "the ten strict xfails **flip**" as the sink's acceptance test. **Only six exist.** The cells
in `tests/test_replay_exit_matrix.py` name R4.44, R4.45, R4.49, R4.51, R4.53 and R4.57. Of B1's ten:

* **R4.48** is covered by a different instrument (all eleven mutants killed by `prove_red`) — fine.
* **R4.52** (`BatchRowResult.landed` as a two-state bool) belongs to **1.4**, not the sink.
* **R4.46** (a usage-less later attempt flips a priced total to `None` with no reason flag) and **R4.50**
  (`llm_calls`/`traces`/`healed_steps`/`total_ms` exclude the relearn while `usage` includes it) are
  squarely the sink's territory and have **no RED cell at all**.
* **R4.47** (the FAILED-path cell asserts one default and two truthy values) is a *test-quality* finding
  about `tests/test_flows.py:1039`, and PR #175 did not touch that file — so 0.3's "the vacuous cell gets
  specific values" was **not** delivered.

So 1.5 opens by writing RED cells for R4.46, R4.47 and R4.50 — before the sink, not after it. That is
the plan's own RED-first rule, and it would otherwise have been discovered mid-slice, when the tempting
move is to declare the sink's green suite sufficient.

### What could make this order wrong

* **If 1.5's audit finds more than ~3 findings**, §6's re-price checkpoint fires and this order is
  re-derived, not continued.
* **If B3's schedule moves ahead of the sink**, 1.4 swaps with 1.5 — the dependency 2.2 → 1.4 is the one
  hard edge in Phase 2, and nothing about 1.5 blocks it.
* **If a fixture defect of R4.56's shape costs a slice**, 0.7 stops being held; the trigger is a *second*
  occurrence, not a first.
* The claim "no Phase-1 step names 0.5/0.6/0.7" is only as good as the *Pinned by* cells, which were
  written on 2026-08-16 by the same pass that wrote the steps. A step whose pins are under-specified will
  look independent when it is not.

---

## 13 · The re-price, fired by §12's own trigger (2026-08-19, after 1.5)

§12 said: *"If 1.5's audit finds more than ~3 findings, §6's re-price checkpoint fires and this order is
re-derived, not continued."* **Two adversarial audits of 1.5 returned twelve**, plus a third on one
decision that reversed it. The trigger fired at 4x its threshold, so this section re-derives the order
from measurement rather than continuing it. Three of the re-deriver's own going-in assumptions were
refuted by the numbers below; those are marked.

### What the programme has actually cost

Derived from git over the fourteen merged commits, `src/` vs `tests/`+`scripts/` vs docs, added lines:

| | `src/+` | `tests/`+`scripts/+` | docs+ |
|---|---|---|---|
| Phase 0 (0.1, 0.2b, 0.3, 0.4a) | **0** | 3 651 | 384 |
| 1.2 | 54 | 210 | 49 |
| 1.5 (goldens + sink + audit round) | 390 | 1 356 | 284 |
| register filings (R4.56/57/59, R4.22) | 0 | 65 | 124 |
| **total** | **444** | **5 282** | **841** |

**11.9 lines of instrument per line of `src/`.** That is the instrument-first bet, priced. It is not a
complaint: 444 lines of `src/` closed ten register findings and the suite grew from 1 103 to 1 191
tests, but anyone re-reading §5's day estimates should know the ratio they implied.

**The audit yield, which is the number that matters for pricing what is left.** 1.5 changed 273 `src/`
lines and its audits returned twelve findings — **one finding per ~23 `src/` lines** — of which one was
HIGH and in the unsafe direction, and one reversed a design decision that had already been taken. The
fix round then cost a further 117 `src/` and 547 test lines: **the audit round is ~40% of the slice
again.** Any remaining step of comparable size should be priced at 1.4x its build cost, not 1x.

### THE FINDING: the manifest tax crossed its own threshold while nobody was looking

`scripts/manifest_cost.py` was built at 0.109.0 with an executable rule: switch away from the
full-rewrite derivation only when (a) cumulative measured marks-phase cost exceeds 4 h **and** (b) the
last ten deltas contain no de-classification. As of this re-price:

```
marks phase:  8 measured, 5.00 h total
PASS (a) measured marks-phase cost 5.00 h > 4.0 h threshold
FAIL (b) 3 de-classification(s) in the last 10 delta(s)
```

**Clause (a) has flipped.** Five hours of wall-clock have gone into re-deriving the tier manifest, and
five Phase-1 steps remain, each needing one or two full runs. Clause (b) still refuses **merge-mode** —
correctly, and permanently as far as the data goes.

> **CORRECTION (2026-08-20).** "Permanently as far as the data goes" was falsified within 24 hours, by
> the rule itself. Clause (b) read a ROLLING WINDOW of the last ten deltas, so once the sole
> de-classifying revision scrolled out, `manifest_cost.py report` began printing **"VERDICT: SWITCH to
> merge-mode"** on every invocation — the repo's one executable rule recommending the design CLAUDE.md
> refuses. Nothing about the workflow had changed; the window had moved. Clause (b) now reads ALL of
> history (de-classification is a CAPABILITY, not a recent-events statistic: one observation proves
> merging would freeze a real event, and a quiet stretch afterwards does not unprove it), the verdict
> is DO NOT switch again, and `test_an_OLD_declassification_still_refuses_the_merge` arms the exact
> regression. The sentence above is left standing because being wrong this fast is the finding. But the rule's own docstring names a third option
that costs neither: **derive the marks from CI's existing full run**, which is ~0 marginal minutes on
both OSes with de-classification intact. It was filed as a CANDIDATE with "the rule below is what says
when it is worth building". The rule now says it.

So a new step, **0.8 — CI-derived marks**, enters the plan, and it goes FIRST: its payback period
(~35 min per slice x 5 remaining slices) is shorter than the work remaining, which is the only
condition under which building a tool mid-programme is not a detour.

### Three assumptions this re-derivation refuted

1. **"Fold a scan inventory into 0.4b, because three structural scans were disarmed at once."**
   REFUTED. An AST inventory finds **35 named `src/` functions across 8 test files**, and **7 of the 8
   already fail loudly when a named function is absent** — which is why all three disarmed scans in 1.5
   went red rather than quiet. The rule belongs in CLAUDE.md (it is there); a step does not.

2. **"Take 1.3 before 1.4, because it is smaller and would measure whether audit cost scales down."**
   REFUTED on the critical path. §5's Phase-2 table makes **2.2 (B3) depend on 1.4 and on nothing
   else**; 1.3, 1.6, 1.7 and 1.8 are on no benchmark dependency. Taking 1.3 first buys information
   about audit cost and delays the only Phase-1 step the benchmark waits on. Information is worth less
   than the thing being waited for.

3. **"1.4 is unchanged in scope."** REFUTED, in its favour. 1.4's *Disposes* cell names B1-A6 and
   B1-A9; **B1-A6 is R4.49, which the sink already closed**. 1.4's remaining content is R4.52
   (`BatchRowResult.landed` as a two-state bool), R4.61 (`write_unverified` reporting `committed=False`,
   filed at 1.5's audit round and disposed here), the `bare_flow_replay_error` ratchet 24 -> 0, and
   `outcome_of`. Smaller than the plan priced it.

### The thing this re-derivation found that nobody had asked about

> **Written 2026-08-19; true for one day. Kept verbatim because the FINDING was right and acted on —
> B2 merged the next afternoon as PR #189 — and because what happened to this paragraph afterwards is
> the reason the status index at the top of this document exists.**
>
> **Phase 2 has never started, and 2.1 (B2) has been unblocked since day one.** Its dependency cell
> reads `nothing in src/`. Every one of the fourteen merged commits has been Phase 0 or Phase 1. The
> plan's own §5 says Phase 2 is *"the benchmark, harness-side, from day 1 (~14 days, parallel)"* — the
> parallelism was priced in and has not happened.
>
> That matters because the benchmark is the stated reason the reshape exists. B2 needs no `src/`
> change, so it carries **no audit burden at all** — the cost driver this re-price just measured at
> 1.4x. It is the only remaining work with that property.

**What actually happened, and it is a defect in this document rather than in the work.** B2 shipped on
**2026-08-20** — `benchmarks/substrates.py`, the two-substrate compose, `customer_bench.py`, the
boundary ledger, three test files, 1750 lines — and then **six more slices merged on top of it**, each
one marking its own row done and none of them touching this one. The row above stayed "never started"
for three days. It was not ambiguous and nobody had to interpret it: it was simply never re-read.

Two small things made it invisible. The B2 commit **bumped no version** (0.110.0 either side), so the
usual "which release was this" thread that leads back to a plan row was absent; and the status lived in
four tables with three different column shapes, so there was no single place a reader could be wrong in
only one way. Both are now closed by construction — `docs/plan/state.json` is the single status, and
`tests/test_plan_state.py` requires the TREE to agree with it in **both** directions, so a step whose
code is present and whose row says pending is a red test naming the step.

### The re-derived order

**STRICTLY LINEAR.** Confirmed with the maintainer on 2026-08-19: nothing here runs in parallel, one
slice at a time. That is not a scheduling detail — §5 priced Phases 0–2 at 55–80 engineer-days *"with
the benchmark's B2–B5 running in parallel from day one"*, so with a linear order the programme's total
is the SUM of the phases rather than the max. Phase 2 alone was priced at ~14 days. §5's headline
number is therefore an underestimate for how the work is actually being done, and should be read as
the parallel-world figure it says it is. No attempt is made here to re-derive it: the day estimates
have never been measured against reality, and inventing a new one would be the same typed-number
failure §13 exists to correct.

<!-- order-table:operative -->

| # | step | why here | audits |
|---|---|---|---|
| 0 | **~~CI capacity~~ → CI provisioning** *(done, 2026-08-20)* | PREREQUISITE, and it blocked everything below because 0.8 cannot exist until CI's full run completes. **It was not a capacity problem.** Measured over 58 ubuntu jobs: the SUITE is 12.4–13.8 min, flat, and is the fastest arm; the variance is entirely `playwright install --with-deps chromium`, which killed 8 of 58 jobs (6 of them still inside `apt-get` at the wall, having run ZERO tests). Fix: delete `--with-deps` — it installs nine FONT packages and no libraries. See below and `docs/ci-provisioning.md` | 3 lenses |
| 1 | **0.8 — marks from an observation** *(done, 2026-08-20)* | the manifest is now built from runs that already happened, CI's four shards included. NOTE the rule this was filed under does NOT authorise it: `manifest_cost.verdict` answers ONE question about merge-mode, and its clause (b) is a merge-specific safety clause that this design preserves by construction rather than trades away. Shipped on its own argument. Zero `src/` | 1 |
| 2 | **2.1 — B2** *(done, PR #189, 2026-08-20)* | unblocked since day one and never started; zero `src/`, therefore zero audit burden; it is the goal. **Shipped the day after this row was written and the row was not marked for three days** — see the paragraph above, and the status index at the top, which exists because of it. Delivered: the two-substrate compose (Odoo + Gitea, images pinned to a minor, `libfaketime` for Odoo's clock-coupled demo data), both resets, the per-scenario readiness hook, R4.40's not-a-skeleton guard, the boundary ledger, and two smoke scenarios. **No version bump**, which is part of why it went unnoticed | none |
| 3 | **1.4 — distinct codes** *(1.4a done, 0.111.0; 1.4b done, PR #191)* | the ONE Phase-1 step B3 waits on. Scope shrank: R4.49 is already closed. **SPLIT IN TWO**: 1.4a is the VOCABULARY (15 classes, 24 sites re-classed, the ratchet, no behaviour change but the codes); 1.4b is the READERS (`outcome_of`, R4.52's tri-state, R4.61). Different blast radii — one changes what a refusal is CALLED, the other what a record MEANS — so the plan's two audits aim at two diffs rather than one | 2 |
| 4 | **2.2 — B3** *(done, 0.113.0)* | unblocked by 1.4, and it consumed 1.4b's seam directly: `ScenarioRun` records the refusal through `outcome_of` rather than stringifying it, and `CODE_FAMILY` is a TOTAL partition of `flows.REGISTRY` so a code minted later fails the suite instead of falling into a default | 1 |
| 5 | **1.3 — cannot-spend** *(done, 0.114.0)* | completed the accounting story B3 consumes. The third state is DECLARED via `UsageTotals.cannot_spend()` rather than probed, and `accounting_failed` became a run-scoped COUNTER — a sticky bool cannot be deltaed, because two consecutive failures leave it True both times. Measured end to end: `drift_bench` now states its own total as a real `0.0` where it published `null` | 1 |
| 6 | **0.4b + 1.1** *(done, 0.115.0)* | 1.1's own pin is RED-in-CI. 0.4b shipped `scripts/red_in_ci.py` and measured itself on 1.3: 34 new ids, 10 `guards`. The rows that came back `no_guard` name its two structural limits -- a scan that reads `src/` BY PATH (R4.75) and anything under `benchmarks/`/`scripts/`, which sit at `sys.path[0]` and cannot be swapped by PYTHONPATH at all (R4.77). 1.1 took `engine_positional_params` 98 -> 7, and 7 is the END STATE: each call's subject | 1 |
| 7 | **1.6 -> 1.7 -> 1.8** *(done, 0.116.0 / 0.117.0 / 0.118.0 — **PHASE 1 COMPLETE**)* | unchanged; 1.6 is the largest ratchet consumer, 1.8 moves every call site and stays last | 2 / 0 / 1 |
| 8 | **2.3 — B4** (done, 0.125.0) | **DONE.** The 14-scenario paired corpus, its server-side oracles (SQL for Odoo, API for Gitea) and the Idempotency-Key logging proxy, on top of the substrates 2.1 already built and the vocabulary 2.2 already froze. §7 calls it the long pole and the one where the house rules bite hardest, and it shipped in five PRs (#200 substrates, #201 oracle machinery, #202 the Gitea seven, #203 the Odoo seven, then the proxy). **0.7 did NOT land with it, and the stated reason was refuted rather than deferred**: B4 added ZERO fixture handlers, because its scenarios run against live containers, so the "14 new fixtures correct by construction" payoff does not exist. It did NOT spend the ≈$60–250 either — every slice was key-less; the core learns are 2.4's, and they need a human's go-ahead | **adversarial pass on this PR** |
| 9 | **0.6 — the scheduled mutation sweep** *(done, 0.136.0)* | held behind 1.5 since the order was written and its trigger fired at 0.110.0; taken here because **2.4's weekly run is specified as sharing this workflow**, so the workflow has to exist first. It also lands the instrument that would catch a bad write-rail fix BEFORE the write-rail fix arrives (Phase 3's D6, whose trigger fired at 0.134.0) — the plan's own "strengthen the net before relying on it" rule, arriving for free rather than by design. Delivered 9/9 on the nine known mutants, a DERIVED registry list, and a measured refusal of the generic-operator half (R4.108) | 1 |
| 10 | **2.4a — B5's runnable half** *(done, 0.137.0)* | the WEEKLY run (decided 2026-08-26: not nightly — ~$1.72 a pass is ~$52/month), gated against the Gitea baseline, plus the honesty page's machine-checked open-id block. **The Odoo half SPLIT OUT as 2.4b** rather than holding the whole step: it is blocked on R4.27 and everything else was ready. Two things this slice measured rather than assumed — the gate's tolerance (a weekly pass must land ≥4/7 against the baseline's Wilson bound, versus 5/7, 5/7, 6/7 observed) and whether CI has a Docker daemon at all (R4.109) | 1 |
| 11 | **D6 — the mutation-gate narrowing** | correctness-plan Phase 3, trigger fired at 0.134.0. Opens with a ~$0.10 measurement of which action types Odoo's failing steps are, BEFORE any `src/` change. **2.4b unblocks behind it** | its own adversarial pass |

**What changed from §12:** 0.8 inserted at the front on measured evidence; 2.1 promoted from "parallel
someday" to explicitly next, because it is the goal and carries no audit cost; 1.3 moves after B3
rather than before it. The tail is unchanged, and its reasons are unchanged.

**The 2.3 and 2.4 rows were added on 2026-08-22**, when this table ran out of unfinished rows and the only
place left saying what came next was §5's design table. (0.6 was inserted before 2.4 on 2026-08-26,
which is why the numbering below runs past nine.) A table that stops naming the next step is the
same failure as a row that stops being true; both were found in the same reading.

**Two of §12's three held steps have had their triggers fire, and neither was acted on** (line below
said "none of which have fired", and that was written when it was true). **0.5** is held to 1.1, which
landed at 0.115.0; **0.6** is held to *after 1.5*, which landed at 0.110.0 — five slices ago. Both are
now carried as `trigger_fired: true` in the status index rather than as a sentence, because a held
step whose trigger has fired is a decision somebody owes, not a state. **0.7**'s trigger is 2.3 and has
not fired; row 8 above lands it there.

**What did not change and should not be re-litigated:** 1.4 before B3 (the one hard edge in Phase 2);
0.5 with 1.1, 0.6 after 1.5, 0.7 with 2.3 (§12's triggers — ~~none of which have fired~~ **two of the
three have; see above**); 1.8 last.

### Step 0 — and it was the wrong step, which is the point

**Everything the paragraph that stood here asserted was false, and all of it was refutable for the
price of one `gh run view`.** It is left described rather than deleted, because how it went wrong is
worth more than what it said.

It claimed an Unknown — *"whether ubuntu is now genuinely slower or hung once; `-q` prints no
progress, so the killed job's log cannot say, and one observation cannot distinguish them"* — and
proposed two cheap levers: raise `timeout-minutes` 25 → 40, and regenerate `.test_durations`.

Measured (`docs/ci-provisioning.md`, 58 ubuntu jobs):

* the dead jobs died in **step 4**, `playwright install --with-deps chromium`, *before pytest was ever
  invoked*. `-q` is irrelevant; that step's log is apt's own timestamped output and says exactly what
  happened. There were **eight** such observations, not one.
* the **suite** is 12.4–13.8 min across every job that reached it, dead flat, and ubuntu is the
  **fastest** arm (windows 15.2–17.7). Nothing supported a capacity finding about either.
* `--with-deps` installs **nine font packages and zero libraries** on this runner image — 21.1 MB of
  CJK/Cyrillic/Thai glyph coverage the suite never renders — over a mirror that killed 8 of 58 jobs.
* **windows was the control the whole time**: same download, no apt, 18/18 at 18–29 s.

Both levers were therefore no-ops. Regenerating `.test_durations` cannot help a job that collects zero
tests, and the shards were already balanced (~4%: ubuntu 13.7/13.1, windows 16.3/15.4). Raising the
budget to 40 would have rescued 2 of 8 and bought the other 6 a fifteen-minute-longer death. **Neither
was taken; `ci.yml`'s job budgets are unchanged.**

**Why it went wrong is the reusable part.** A job named `tests (ubuntu-latest 2/2)` died having run no
tests, and both the maintainer and the author of this section read `cancelled` as "the suite got
slower" — because the observable outcome carries no attribution. A concurrency cancellation, an apt
hang and a genuinely over-long suite are the *same string*. That is the third inviolable ("never
silently return or act WRONG — fail LOUD") wearing a CI hat, and it wrote a wrong prerequisite into
this plan one day after §13 was written to stop exactly that kind of thing.

So the shipped step 0 is: delete the flag, budget **every** authored step except the one whose duration
IS the signal (the suite), and pin the CLASS in `tests/test_ci_provisioning.py` — one browser install,
shared by both OSes, no apt anywhere — with a standing arming cell that mutates the workflow FIVE ways
and requires each to be caught (four violations that must be caught, and one legal shape that must not
be: a comment mentioning `apt-get`, which the paragraph explaining the removal necessarily does).

**Refused, and recorded so it is not re-proposed:** a `timeout-minutes` on the suite step (a second
ceiling under the job wall, on the growing arm, with nothing to acknowledge it — the D0 shape); a job
budget raise (the removal restores ~11 min of headroom under the existing 25); a smoke-launch step
(it would be a second transcription of `browser.py`'s launch path, and headless resolves to a
*different* binary, so it could certify the wrong one); and any claim that a job-level `cancelled` now
means the suite over-ran — the `always()`/`failure()` tail and GitHub's own injected steps make that
unprovable, and an earlier draft asserted it with a worked sum that was wrong on the failure path.

**And the honest acceptance note:** a clean streak after this merge is NOT evidence. **44 of the 58
jobs completed with the install under two minutes while the flag was still present**, and the fault is
episodic — eight clean overnight hours sit between two failure episodes. So "ten green ubuntu runs" is
satisfied by the UNCHANGED workflow and cannot distinguish the fix from the base rate; that is
green-is-not-evidence pointed at this step's own adjudicator. What is checkable is static (the pin fails
if the flag returns) and distributional (the ubuntu install step should sit in the windows band,
~18–30 s, not merely "under a minute").

### What would make THIS order wrong

* If 1.4's audits return more than ~4 findings, the 1.4x audit multiplier measured here is an
  underestimate and Phase 1's remaining estimate needs re-deriving again — not the order, the *price*.
* ~~If step 0's two cheap levers do not bring ubuntu back inside the budget, the cause is a hang rather
  than balance~~ — **this fired before either lever was pulled.** The antecedent was already satisfied
  when it was typed, and the diagnosis it priced as "a different size of job" cost one `gh run view`.
  The lesson generalises past this row: a bullet that names the condition under which a plan is wrong
  is worth checking *at the time of writing*, not only afterwards.
* If B2 turns out to need a `src/` change after all — the boundary ledger derives module-level bindings
  from the live import graph, which §5 asserts is possible without one — then 2.1 acquires an audit
  burden and its promotion above 1.4 loses its justification.
* The 11.9:1 instrument ratio is measured over a programme whose whole point was building instruments.
  It should FALL through Phase 1 and Phase 2. If it does not, the ratio is not a phase-0 artifact but
  the cost of working in this codebase, and that is a different conversation about the plan's premise.
