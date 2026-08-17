# The reshape plan — stop manufacturing the defect classes

**Status: PROPOSAL, not started.** Researched 2026-08-16 against `6d2aa90` / 0.108.0, immediately after
PR #165 (B1, "the run record") merged. Nothing here has been built. Every `file:line` in this document
was re-verified against the tree by hand before it landed; where a number comes from a measurement made
during the analysis and **not** re-run here, it says so.

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
| "what is the failure?" | **24** bare `raise FlowReplayError(` in `flows.py`, all sharing `code='replay_error'` | yes |
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
rest — and the fast ones **cannot be selected**, because there are no markers. *(All verified here; the
analysis additionally estimated ~887 browser-driving tests across ~61 files, which is consistent with
this but is not re-derived here — most tests reach Chromium indirectly through `run_cached`, so a
textual grep undercounts and only a runtime launch counter settles it. Step 0.1 builds that counter.)* The local suite is 21–31 min and Windows-only, which
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
R3.9/CLI-1 shape. And a reproduced example of the wrapper hole: `cli.py:652` returns
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
below, as proposed ids R4.42–R4.51.

| Proposed id | Finding | Where |
|---|---|---|
| R4.42 | An attempt whose `run_cached` **raises** drops its own LLM spend, traces and minted keys, and leaves `ok`/`failure_code` stale from the previous attempt — F2's fix wrapped the relearn leg only | `flows.py:2191-2233` |
| R4.43 | `record.usage == {}` on the miss / escalate / precheck / pre-attempt-refusal / raise exits, against `RunRecord`'s own docstring ("always populated") | `flow.py:192`, `flow.py:1096-1099` |
| R4.44 | A usage-less later attempt flips a priced total to `None` with no reason flag (`_absorb_usage`'s sticky-`None` meets an absent key) | `flows.py:2097-2120` |
| R4.45 | `test_run_record_is_populated_on_a_FAILED_replay` asserts one default and two truthy values; population-only-at-success stays green | `tests/test_flows.py:1039-1065` |
| R4.46 | Eleven wiring mutations of the record plumbing are invisible to the whole suite | `flows.py:2843/2921/2924/2980/2987/2990` |
| R4.47 | `record.failure_code` speaks the internal `kind` vocabulary while the raise uses `_classify_replay_failure(kind).code`, and can name a different attempt than the exception | `flows.py:2182-2186` vs `:441` |
| R4.48 | `llm_calls` / `traces` / `healed_steps` / `total_ms` exclude the relearn while `usage` includes it | `flows.py:2971-2988` |
| R4.49 | The headline claim ("a 0-LLM replay now says observed zero") has no end-to-end pin: an engine reporting UNKNOWN on every replay passes every test | `flow.py:1085`, `:1240` |
| R4.50 | `BatchRowResult.landed` is a two-state bool that reads `False` on successful write rows and on crashed rows — the trap the same PR wrote a paragraph about | `flows.py:3562` |
| R4.51 | A key-less teacher (`ScriptedProvider`, `MockProvider`) is classified as an *unobserved spender* (cost UNKNOWN), contradicting `flow.py:915-916`; and `accounting_failed` is sticky across runs | `obs.py:235-241` |

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
  `genai.Client()` — **not matched at all**. Two of four constructions are outside the pin's inference.
  **verified, and new: the `genai.Client()` hole is not R4.41 and is not filed.**
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

| # | Step | Disposes | Pinned by | Size |
|---|---|---|---|---|
| 0.1 | **Fast tier.** New `tests/conftest.py`: a session plugin wrapping `BrowserType.launch` **and** `PlaywrightContextManager.__aenter__` *on the class* — **(critic)** `start()` is a one-line delegate to `__aenter__` and 21 test sites use `async with`, so wrapping `start` alone is half-inert. `--store-browser-marks` writes per-test launch counts; `--tier fast` deselects listed browser tests **and** installs a raiser, so an unlisted browser test fails loud. Key scrub. The 138 s + 40 s tests move to their own CI job; `.test_durations` regenerated | g, h; the 21–31 min loop | violation armed and seen red under `--tier fast` for both entry styles; a browser cell increments the counter by exactly 1; every collected id classified or collection fails naming ids | S · 1.5 d |
| 0.2a | **File B1's ten in the prose register** as R4.42–R4.51 (~10 lines). **(critic)** `tests/test_register_count.py:167-180` fails any strict xfail whose reason names no open `R3.x`/`R4.x` id, and `_ID_IN_CODE` (`:195`) recognises only that shape — so this must land **before** 0.3, or 0.3 lands red | ordering hazard | the existing count guard | XS · 0.2 d |
| 0.2b | **Register as structured data.** `docs/register/<id>.yaml` (id, title, status, severity, inviolable, class letter, attempts, `blocked_by`, `next_attempt_requires`, pins, disposition); `scripts/render_register.py` generates the index/state blocks; the 4,401 narrative lines move **unchanged** to `docs/register/history/` (frozen, append-only). `D5` becomes a schema rule: open + ≥2 attempts ⇒ `blocked_by` + `next_attempt_requires` required. **(critic)** no line-count target for CLAUDE.md — its operational lessons are the one text every session reliably loads; pin only "no hand-typed counts outside the generated block" | l, k; the 170k-token read | schema (anti-vacuity ≥83 ids, ≥10 open); render byte-equality; rendered counts equal today's; every strict xfail names an open id; a PR that deletes an xfail or adds an id-naming test must flip that id's status in the same diff | S · 1.5 d |
| 0.3 | **Exit-set matrix over a fake engine.** `tests/_fake_engine.py` installed by patching the bindings `flows.py` holds — `run_cached`, `_precheck_done`, `refresh_auth`, `learn`, and **(critic)** `_make_finalize`/`_make_pre_write` so the `out` dict is scripted explicitly; all six added to a module-bindings AST pin. Drives `replay()` through every exit **derived by AST** over its own `raise`/`return` nodes — **(critic)** not a hand list — including preflight refusal, `on_step` raising inside the auth-refresh retry, and record reuse across calls. Fidelity cells for read **and write** shapes against the real engine on existing fixtures. The ten B1 defects become strict-xfail cells RED against main; the vacuous cell gets specific values; the 11 wiring mutants registered and proved killed | g; RED-first for 1.5 | every derived exit hit ≥1 cell (printed); premise counts per cell; fidelity cells; `prove_red` 11/11; xfails machine-checked RED | M · 3–5 d |
| 0.4 | **RED-in-CI + red-proof + ratchets.** Run each PR's *new* test ids against main's `src/` in a worktree; a src-touching PR whose new tests all pass against main fails. **(critic)** ImportError-against-main is *inconclusive*, not a label-shaped opt-out — a PR adding a src module ships a registered mutant instead; the job's self-test is a unit test of its verdict function. `assert_ratchet(name, derived_sites)` fails on growth **and** staleness | g, f | the job asserts it found ≥1 new test when the diff adds `def test_`; each ratchet asserts a minimum hit count first | S · 1.5 d |
| 0.5 | **Contract tests for every "must agree" pair**; `node --check` over every assembled `*_JS` payload; add `Client` to `_SDK_CTORS` and raise the anti-vacuity threshold (test-only) | l, f | each pair asserts a minimum match count before equality; delta sets frozen | S · 1 d |
| 0.6 | **Scheduled mutation sweep (S16).** The nine known mutants + the 11 B1 wiring mutants + generic operators on a frozen hot-file list, one at a time on a scratch copy; weekly; a survivor not in `known_survivors` fails | g, a | `known_survivors` only shrinks; the nine known mutants must be killed every run | M · 2 d |
| 0.7 | **Shared fixture server** (`serve()`, a `Site` recorder with a `saves` count, `reveal_sync`, common stubs); migrate ~8 files as proof. Off the critical path | g, n | ratchet on files defining their own handler class; collection count unchanged | M · 3 d |

### Phase 1 — bounded `src/` changes (~20–30 days incl. audits)

| # | Step | Disposes | Pinned by | Size · WS |
|---|---|---|---|---|
| 1.1 | **Keyword-only engine chain.** `*` after the identity prefix in `_learn`/`_learn_n`/`_replay`/`_verify_by_replay`/`_replay_step`/`_author_steps`; internal call sites rewritten to keywords. `run_cached`'s public signature unchanged. **(critic)** the arity pin cannot see a *mis-keyed* placeholder (`flow.py:752` passes four `None`s whose swap is type-silent), so a forwarding-identity test comes with it: stub each inner coroutine, pass a unique sentinel per kwarg, assert every kwarg arrives under the same name with `is` identity, DELIBERATE_DROPS asserted both ways, shown RED against a mis-keyed scratch copy | i | AST positional-arity pin + the forwarding-identity cell; RED-in-CI; suite + 0.3 goldens byte-identical | S · 1.5 d + audit · no |
| 1.2 | **`flow release` reaches `release()`.** **(critic)** *delete* the pre-check at `cli.py:652`, do not widen it: `health()` reports `refused` only when the flow is **not** cached (`flows.py:2068`), so a refusal recorded with a stale recipe still reads "nothing to release". `release()` returns what it cleared; the CLI prints that. ≤10 src lines | R3.13 remedy hole (d, j) | RED CLI cells for **both** shapes (learn-refused uncached; refused-with-stale-recipe) driven through argparse | S · 0.5 d · no |
| 1.3 | **A "cannot-spend" third state in `obs.py`.** **(critic)** only the `totals = UsageTotals()` variant — never a new attribute probe on an owner, because a `spends: ClassVar` probe re-creates commit `00888b4`, which tripped inviolable #1's `_Exploding` tripwire one week ago. AST cell: `RouterWatch.__init__` reads only `router`/`totals`. `accounting_failed` becomes run-scoped. Fix the readers so `None` renders as *unknown* and never sums to 0.0 | R4.51; b, c, p | `observe(ScriptedProvider([]))` → observed, 0.0; the `_Spender` stub stays unobserved; `test_a_navigate_only_replay_never_reaches_a_provider[repair]` named as a pin; drift_bench baseline unchanged | S · 1 d + audit · no |
| 1.4 | **Distinct codes + one `outcome_of` + tri-state on the siblings.** Codes for the ~10 base-class refusals; `outcome_of(exc) → Outcome(code, retryable, landed)` replaces the getattr triples that *construct* `BatchRowResult`/`ToolOutcome`/`DryRunReport`/`FleetRun`. **(critic)** the two ledger-arming reads (`flows.py:3771`, `mcpserver/server.py:444`) stay byte-identical — they are R3.3's consumers. Must land before B3 freezes its vocabulary | R4.47, R4.50; j, b | ratchet bare `raise FlowReplayError(` 24 → 0; a cell per subclass at its raise site asserting (code, retryable, landed); `test_landed_arms_the_ledger.py` + the write-safety matrix (both clauses) named as pins | M · 2 d + **2 audits** · **yes** |
| 1.5 | **THE SINK — single-exit `RunRecord`.** Replace the 10 write sites and the three helpers with one `_RecordSink`: append-only `AttemptRecord`s + `finish(exc_or_None)` called exactly once, **total by construction** (an internal error becomes `record.note`, never an exception over the original). `replay()`'s body becomes `_replay_body`; the wrapper does `try … except BaseException as exc: sink.finish(exc); raise`. Per-attempt usage comes from a watch the *wrapper* owns, so the raise path is a non-event and **`flow.py` is untouched**. `failure_code` derives from `exc.code`. **(critic)** landed/committed rule: True if any attempt evidenced True; else **None** if any attempt is unknown (raised, precheck-skip, relearn-raise) or none completed; else False — and today's values for both precheck exits and the retry-raise→repair path are frozen in the 0.3 golden **before** the sink is written; `exc.landed` arming stays byte-identical. `LearnResult` gains a **required** `report=` keyword | R4.42–R4.49; a, b, i, n, p | the ten strict xfails **flip** (strict forces it); every other cell byte-identical or in an argued golden diff; AST: no `record.<field>` write outside the sink; ratchet 10 → 1; `prove_red` 11/11 still killed; cells for reuse-across-calls, usage equality while both watches coexist, relearn-with-no-report | M · 3 d + **2 audits** · **yes** |
| 1.6 | **`WriteClass` value object + `FlowSpec.key`.** The *named* questions the raw sites ask — `declares_write`, `is_write`, `needs_confirm`, and **(critic)** two separately named multiwrite questions (`declares_multiple_barriers` for `MutateSpec.is_multiwrite`, `recipe_has_multiple_writes` for `cached_writes`) because `_auth_retry_allowed` keeps them as separate arms with different messages; collapsing them is R3.5's own failure shape. Carries **no refusal**. `FlowSpec.key` replaces the 24 transcriptions | k, a, l, f | ratchets 33 → 0 and 24 → 0; a **printed per-site conversion table** so a mistranslated site is visible on sight; write-safety matrix (both clauses) and the exit-set matrix byte-identical | M · 2.5 d + **2 audits** · **yes** |
| 1.7 | **Printed door `Policy` table** naming what each raw door permits *today* (CLI root, daemon, `run_many`, MCP, `replay`), including `auto_reauthor_writes=True` — so `flow.py:187-189` becomes a visible decision item rather than a refactor side effect. Daemon validates `mode`/`provider` against closed sets before the engine | d, m | a test prints the Policy per door and asserts equality with a committed table; a liveness corpus of read flows replays green through every door | S · 1 d · no |
| 1.8 | **`RunOptions`/`RunHooks`** threaded through the engine instead of 20–23 scalars; `run_cached` keeps its public kwargs. Every existing asymmetry preserved as-is in a DELIBERATE_DROPS table (R4.12 included — *preserved, not fixed*). Separate PR from 1.1 | i, a, d | introspection: every field read in `run_cached` or reachable in every inner function it applies to, except DELIBERATE_DROPS — checked **both ways**; a hook-fire count table per (mode, hook) captured before and asserted after | M · 3 d + audit · no |

### Phase 2 — the benchmark, harness-side, from day 1 (~14 days, parallel)

| # | Slice | Depends on | Notes |
|---|---|---|---|
| 2.1 | **B2** — substrates (Odoo + Gitea), reset, readiness hook, harness skeleton, and the **boundary ledger**: derive every module-level binding of `build_client`/`build_router`/`get_provider` from the live import graph and wrap them, so cost is metered where `Router.complete` meters it (`llm/base.py:71-75`), never from `RunRecord`. **(critic)** score with `count` and derive the 0-LLM claim per call *site* — a data replay legitimately extracts once, so a blanket `refuse` mode would bucket every extracting read as `llm_reached`, which is over-refusal in the instrument | nothing in `src/` | vision/gemini configs refused as a **stated bound** until the SDK pin covers them |
| 2.2 | **B3** — outcome vocabulary + record/baseline format; QUIET as an allowlist; the aggregator **fails** on cost `None`; `refused` sub-buckets derive from 1.4's codes, never message labels; `RunRecord` consumed only as a cross-check → a `record_disagrees` bucket | 1.4 | if 1.4 slips, sub-labels are reported unbucketed and re-derived later |
| 2.3 | **B4** — the 14-scenario paired corpus + server-side oracles + the key-logging proxy; write oracles assert the changed record's **linkage/identity**, never a count; `--arm-oracles` shows every oracle RED before any scored run; a scenario with zero premise counters is not scored | 2.2 | the long pole; adversarial pass on this PR |
| 2.4 | **B5** — baseline, nightly (sharing 0.6's workflow), and an honesty page whose open-id list is pinned to the register's open set | 2.3 | ubuntu-only nightly is a documented blind spot |

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

**PR 2a then 2b — register.** 2a files R4.42–R4.51 in the prose register (~10 lines) so PR 3's xfails
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
| R4.45 (vacuous cell) | PR 3 | rewritten with specific values; a raise-path cell added |
| R4.42, R4.43, R4.44 | 1.5 | the wrapper watch + `finish(exc)` around the one `_attempt_replay` call and the one `learn()` call; explicit zero on the miss exit; `UsageTotals` objects summed, never re-summed dicts |
| R4.46 (11 mutants) | PR 3 now; 0.6 weekly | golden cells kill all 11 **before** the sink lands; the sweep keeps them dead |
| R4.47 (vocabulary) | 1.5 + 1.4 | `failure_code = exc.code` at finish; distinct codes so B3 never buckets by message |
| R4.48 (relearn excluded) | 1.5 | `LearnResult(report=…)` as a required keyword — a missed site is a `TypeError` |
| R4.49 (no e2e pin) | PR 3, then B2 | the ok cell asserts usage == the fake router's delta with cost 0.0 and no unobserved flag; after B2, one e2e cell asserts `record.usage` == the harness ledger's delta on a real extracting replay |
| R4.50 (`BatchRowResult.landed`) | 1.4 | tri-state, with its ok-write value stated as a golden cell first |
| R4.51 (key-less teacher; sticky flag; readers) | 1.3 | as above |

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
