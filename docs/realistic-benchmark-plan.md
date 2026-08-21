# A realistic customer-scenario benchmark — feasibility and plan

**Status: PLAN, not built.** Researched 2026-08-14 against 0.107.0. Nothing here is measured except
where a `file:line` or an existing baseline is cited; the one number this plan itself produces is the
Gate-0 spike at the end.

**The question.** Can we find or construct a benchmark of *realistic customer scenarios* — not a copy of
the test suite, not the synthetic drift corpus — that reports successes, failures and **costs** (LLM
calls, 0-LLM reads, writes true-or-incorrect)? How complex, what does it cost to build, what does it
cost to run, and can it also run against older versions of this codebase?

**Verdict.** Feasible. **~4–6 weeks part-time** for all three arms (**v1 in ~2 weeks**), **≈$200–500**
one-time in LLM spend, **≈$0 per run** (the replay arms are 0-LLM by construction), on one Docker host
with no cloud bill. The versioning arm is cheap enough to include. Two small instrumentation PRs must
land first, and one 1-day spike gates the substrate choice.

**Why this is worth building beyond answering the question.** The 2026-08-09 architecture review's top
strategic finding was that this project meters the *safety* direction exactly (`silent_wrong` as a
CI-gated allowlist) while the *availability* direction — ordinary reads refused, over-gated or misfiled
as writes — has **no number anywhere**, and R4.27's 12/12 was found by accident while pricing a
different change. A customer-scenario benchmark with per-scenario outcome buckets **is** that number.
One build serves both purposes.

---

## 1 · What must be measured, and what already exists

Most of the machinery is here. The audit below is what a new harness can reuse, and the three things it
cannot.

| Needed | State | Where |
|---|---|---|
| Per-run LLM calls | **have** | `FlowReport.llm_calls` (`flow.py:71`) — counts `decide()` in learn/heal/replan; the field is byte-identical back to 0.30 |
| Tokens + $ | **partial** | `FlowReport.extra["usage"]` → `{calls, input/output/cache tokens, cost_usd}` (`obs.py:117-128`), populated only when the provider owns a `.router`; only `benchmarks/variance.py` reads it |
| **Hole (a): the replay-path extraction call** | **NOT COUNTED** | `flows.py:2716-2721` builds a standalone extraction `Router` on the replay path. Its `totals` are read **nowhere** — in `flows.py`, `router.totals` appears only in the H9 audit verb (`flows.py:3191, 3228-3230`), a different entry point. So a "0-LLM" data replay can make one un-counted, un-costed LLM call. |
| **Hole (b): usage through the high-level API** | **DISCARDED** | `_attempt_replay` (`flows.py:2094-2101`) reads only `report.traces`; `replay()` returns bare data or `{"status","data"}` (`flows.py:2723-2726`); `LearnResult` (`flows.py:197-208`) has no usage field. A caller of the Flow API cannot see what a flow cost. |
| Per-step outcomes, typed failures | **have** | `StepTrace.meta` (`ok`/`gate`/`gate_bound_by`/`idempotency_key`/`phase`); `FlowReplayError` taxonomy with `.code`/`.retryable`/`.landed` (`flows.py:277-405`) |
| Write true / double / suppressed | **pattern exists** | server-side counter oracle (`benchmarks/write_flow_bench.py:48-100`); golden act-trail via `data-oracle` (`benchmarks/drift_fixtures.py`); HAR capture (`run_cached(record_har_path=…)`, `flow.py:114`); the Idempotency-Key reaches the wire (`browser.py:214-217`), so a proxy can log it |
| **Reads refused / over-gated** | **NOWHERE** | No harness has a `refused` or `over_gated` bucket for reads. This is the availability gap, and it is the main thing the new bench adds. |
| Report format, baselines, regression gate | **have** | `benchmarks/variance.py` — records, Wilson CI, pass^k, hazard histogram, baseline compare with error-bar tolerance (`variance.py:77-179`) |

**The outcome vocabulary the bench adds.** Reads: `{ok, wrong_data, refused, over_gated}`, where
*over-gated* = cached as a write / approval-demanded / denied from `run_batch` or MCP. Writes:
`{true, incorrect_target, double, suppressed, refused_correctly, refused_wrongly}`, adjudicated by a
database query plus the received-Idempotency-Key log — **never by `landed`**, which this repo defines as
evidence-bounded rather than true.

**Prerequisite PRs.** Holes (a) and (b) are worth closing regardless of the benchmark: today a user of
the Flow API cannot see that their "0-LLM" replay is making a paid call. They are small, and they must
land first or the bench's headline cost number is wrong on day one.

---

## 2 · Substrate

A live web sweep of the 2024–2026 benchmark landscape found **nothing off-the-shelf that satisfies all
four axes** a learn-once/replay-many system needs: (a) the same task repeated many times with resettable
state; (b) server-side ground truth for writes; (c) freezable versions, ideally such that a version
*upgrade* is realistic drift; (d) one box, sane cost. So the substrate is a composition.

| Option | Verdict | Why |
|---|---|---|
| **Odoo** (official Docker image, demo-seeded, Postgres) | **PRIMARY** | Reset by DB re-create (with the engineering in §5); write truth is SQL ("exactly one `sale.order`, these lines"); **the OWL frontend is a real SPA doing JSON-RPC read-POSTs — natively the transport R4.27's 12/12 misfile lives on**, so availability is measured on a real app rather than on toys we authored; real released version pairs for the drift arm; $0 infra |
| **ST-WebAgentBench / SuiteCRM** | FALLBACK | Dockerized CRM, per-task JS state setup, DB-checkable records, and a dual completion-vs-policy score that maps onto the over-refusal question. Used only if Gate-0 fails on Odoo. |
| **REAL** (11 deterministic replicas of real sites) | rejected | Best determinism in the field and programmatic write checks, but a hosted-replica dependency, single-version (no drift arm), and a CC BY-NC-SA question |
| **WebArena** (already in-repo: `benchmarks/webarena_run.py`) | KEEP AS FOOTNOTE | It ran once — shopping_admin 6/8, shopping 6/8, 2 replay regressions (STATUS.md:47). But reset is minutes-heavy, hosting is t3a.xlarge-class, images are one frozen version and hard to rebuild. Keep for literature comparability; do not extend. |
| **Similo / VON-Similo relocalization dataset** (10,376 element pairs, 30 real apps × 16 Wayback snapshots 2018–2023) | **FREE RESOLVER ARM** | Real old→new page pairs for `locators.resolve` alone. Retires half of "real frozen snapshots remain unbuilt" (ROADMAP.md:252) for ~0 cost. Limits in §8. |
| **Gitea** (`gitea/gitea:1.22`, SQLite) | **PAIRED SECOND SUBSTRATE** | Chosen after §9.5b showed Odoo saturates. Measured (§9.5c): **0 of 5** read controls classified as writes — all GETs — and writes marked by the **structural** classifier (`source=form_method`), the path Odoo never exercises. A read flow learns, **passes verify-by-replay**, caches as a read and replays 0-LLM. 242 MB image vs Odoo's 2.73 GB; a 2 MB SQLite file, so reset is deleting one file. Rich API for server-side truth; dense release history for the drift arm |
| Live public sites (the `examples/hn_digest.py` pattern) | SMOKE ONLY | One or two read-only flows as a reality anchor and a Windows-local smoke; non-deterministic, so never gated |
| **WooCommerce** (WordPress admin) | DEFERRED, needs its own spike | The only candidate that is **domain-matched** with Odoo — orders in both — which would let a scenario hold the task constant and vary only the transport. Not chosen for v1 because modern WooCommerce admin has React screens (Analytics certainly; the Orders list depends on version and HPOS), risking exactly the saturation the pairing exists to escape. Revisit only if the controlled comparison is wanted, and spike first |
| **Redmine** | ALTERNATIVE to Gitea | Classic Rails, same server-rendered properties, a more business-shaped domain than dev tooling. Hold in reserve if Gitea's domain framing becomes a problem; not measured |
| WorkArena · Mind2Web-Live/WebCanvas · WebVoyager · τ²-bench · TheAgentCompany | rejected | ServiceNow instances are un-freezable; live-web tasks rot with no reset or server truth; τ-bench has no browser — though **its final-DB-state-vs-expected oracle is exactly the pattern our write oracles should copy** |

---

## 3 · The scenario corpus — three cohorts, scored separately

The sharpest finding of this plan's own adversarial pass: a scenario set drawn only from
`docs/open-defects.md` is **the defect register re-typed**. It would measure whether known bugs stay
fixed — which the suite already does — and tell a customer nothing about the population nobody has
enumerated. This project's own R3.7 lesson ("every existing shape was built from the same mental image
of a row") applies to the benchmark itself. Hence three cohorts, reported separately.

### Cohort A — register-derived (~22): does the known-hard population hold?

| Scenario | Type | Population it pins |
|---|---|---|
| `daily-order-count`, `slotted-customer-lookup`, `login-then-pull` | read, form/auth | the canonical ROADMAP flow; slots; auth expiry mid-flow (R3.5 / R4.11) |
| `filter-orders`, `export-csv`, `paginate`, `refresh-dashboard`, `view-detail` | read, JSON-RPC | **R4.27's 12/12 misfile class** — the core of the availability arm |
| `debounced-search`, `deferred-pagination`, `kanban-heartbeat-read`, `autosave-bg-read` | read, debounced/bg | the eight-shape read population (`open-defects.md:3108`) |
| `create-lead`, `confirm-sale-order`, `quote-to-order` | write | single write; SPA write over JSON-RPC; multi-write (`step_confirms`) |
| `cancel-order-row-N`, `cancel-shared-aria-row` | write, row identity | R3.7 / R4.34 / R4.37 — the oracle asserts **which** order changed, never that one did |
| `batch-invoice-20` (kill at row 7, resume) | write, batch | resume ledger; oracle is the invoice↔source-order **linkage set**, not the count (§5) |
| `idempotent-replay`, `auth-expired-write`, `confirm-banner-no-request` | write, guards | replay of a landed write (asserting the mechanism *ran*); auth-retry refusal for writes; the accepted `landed` residual as a pinned expected-fail |
| `slow-debounce-write` | **manual arm** | R4.26-family settle straddle — timing-sensitive, so excluded from the gated set and built as a deterministic mechanism-on-demand fixture |

### Cohort B — populations Odoo structurally cannot contain (~5)

Real customer flows constantly hit things a clean self-hosted ERP never shows, and two are documented
deferrals here (iframe/shadow capture, `recorder.py:67`; the perception stack is top-frame/light-DOM
everywhere, ROADMAP.md:434-436). They belong in the bench **because they will score badly** — that is
the honest number: an nginx-injected consent banner; a `srcdoc` payment-iframe checkout (expect a loud
refusal — pin the direction); a `fr_FR` localization pass over the reads; a file-upload write; and the
A/B variant layer from §4.

### Cohort C — sealed (~6)

Authored from real customer-flow descriptions by someone who has **not** read the defect register,
sealed until first run, reported as their own cohort. This is the only defense against the benchmark
inheriting its authors' mental image — the blindness that defeated both R3.7 attempts. **The A-vs-C
score gap is itself a finding**: it estimates how much of the headline is register-overfit.

---

## 4 · The three arms

**Arm 1 — correctness + availability (the core; CI-viable).** Learn each scenario once on a pinned Odoo,
then replay N times with resets between. Emits per scenario and rolled up: outcome bucket, LLM
calls/tokens/$ (learn, plus any heal or extraction on replay — the whole point of holes (a)/(b)),
wall-clock, pass^k. Key-less once learned. **Nightly on one ubuntu Docker host, not per-PR** — the suite
is already ~21 min against a 25-min CI timeout. This arm alone converts "12/12 reads misfiled" from an
anecdote into a gated number.

**Arm 2 — drift (redesigned; see §5).** Learn on Odoo *N*, replay against equivalently-seeded *N+1* /
*N+2*, scoring `{survived_0llm, healed(+$), drifted_loud, silent_wrong, refused}`. A version upgrade is
one coherent change applied to everyone; customers also see **stochastic A/B drift**, which attacks
replay determinism on a different axis — a thin nginx layer serving a mutated variant to *x*% of
sessions (~1 day, reusing `drift_bench`'s mutation vocabulary) covers that separately.

**Arm 3 — versioning (include it; it is cheap).** No git tags exist, but versions map 1:1 to first-parent
merge commits; `run_cached` has grown only additive keyword args since 0.30; **`FlowReport` is
byte-identical 0.30 → HEAD**; the CLI's `data:` stdout line is literally the same string at 0.30, 0.60
and HEAD. Mechanism: one driver, subprocess CLI per `git worktree`, `ULTRACUA_HOME` per worktree
(≥0.59), learn-per-version (**never share a store across versions** — meta sidecars accreted gates an
old binary will not enforce), and fixture-side ground truth always, because **exit codes are unreliable
before 0.87** (the S7b finding: four CLI surfaces reported failure as success to `$?`). Run **HEAD + 3
waypoints** — 0.60 (pre-write-hardening), 0.78 (post-`landed`), 0.87 (post-exit-truth) — every
behavioral epoch at ~5% of the cost of all ~90 versions.

Two costs to budget: scenarios using `params=` / `MutateSpec` / `run_batch` need per-epoch shims at 0.60,
and old versions' errors need a **hand-validated per-epoch error→bucket map** (spot-checked on ~5 flows
per waypoint) — otherwise the availability trend line, which is this arm's entire payoff, is an artifact
of the mapping. Also: pre-hardening versions learning live against Odoo can fire un-keyed writes
mid-learn, so per-waypoint DB resets are mandatory.

**If the versioning arm is ever cut**, what is lost is exactly one thing: the per-version trend of the
availability direction. The rest of the benchmark is unaffected.

---

## 5 · What the adversarial pass changed

Per the house rule — *audit the fix, not just the code it fixes* — the draft design got two independent
adversarial reviews (operational determinism; measurement validity). Both found real problems; the
design above already incorporates every fix. This table is the record, so nobody re-proposes a
refuted shape.

| Finding | Severity | Disposition |
|---|---|---|
| The drift arm assumed an Odoo 16 DB opens under 17. **It does not** — Odoo Community has no in-place major-version migration (the official upgrade service is Enterprise; OpenUpgrade is partial). The arm as drafted was un-runnable. | **BLOCKER** | Redesigned: per-version **equivalent** seeding with asserted-equivalent state, and the baseline states that cross-version *data* equality is out of scope |
| "Reset in seconds" wrong three ways: a template DB needs zero connections (Odoo holds pooled ones); the first request after a reset recompiles asset bundles (tens of seconds → **false drift on whichever scenario runs first**); the filestore is on disk, not in Postgres | MAJOR | Restart-per-reset + a warmup request + filestore snapshot; budget 30–60 s/reset; run estimate raised 15–25 → **40–60 min** |
| Odoo demo data is **clock-coupled** — date-dependent oracles false-drift at a day boundary with no code change | MAJOR | `libfaketime` in the container, or date-independent oracles only |
| Timing-straddle scenarios violate this repo's own loaded-host rule (Postgres + Odoo + Chromium on one box; worse on a 4-core runner) | MAJOR | Moved to the manual/weekly arm and built as deterministic mechanism-on-demand fixtures (the R4.26 lesson: build the mechanism, don't fish for it) |
| The Docker arm is ubuntu-only — reinstates the single-OS blind spot that has already shipped fixture races | MAJOR | Accepted and documented; one Windows-local smoke scenario retained |
| Scenario set = the defect register re-typed (19 of 24 cited a finding); **zero scenarios the architecture is expected to lose** | MAJOR | Three-cohort corpus with a sealed cohort (§3) |
| **Success laundering** in the two flagship writes: the batch oracle was a *count* (a resume that double-invoices row 8 and skips row 7 still totals 20); and `idempotent-replay` can pass with the mechanism never exercised, because `_precheck_done` short-circuits and returns `already-done` **before any browser action** (`flows.py:2705-2708`) | MAJOR | Linkage-set oracles; assert the status string **and** the server-side Idempotency-Key log **and** mechanism call counts — the "arm the violation, count the mechanism" rule |
| Learn-cost calibration 3–6× low (MiniWoB pages vs Odoo's 10–50× DOM); the **failure tax** (~40–48% of learns fail and still cost money) unbudgeted | MEDIUM | Cost model rebuilt (§6); the bench reports cost-per-**successful**-learn with attempt counts |
| Old waypoints predate the error taxonomy the buckets read; Wayback fetches flake; per-scenario env vars force subprocess-per-scenario because `settings` is frozen at import (`config.py:150`) | MEDIUM | Hand-validated per-epoch map; vendor Similo snapshots locally; startup cost added to wall-clock. Also: **never commit HARs to `baselines/`** — they persist raw write bodies |

---

## 6 · Cost model

| Item | Estimate | Notes |
|---|---|---|
| Instrumentation PRs (holes a + b) | ~1–2 days | prerequisite; valuable on their own |
| Odoo compose + reset/warmup/filestore/faketime harness | ~3–4 days | the critique-adjusted reset engineering is most of it |
| Gitea substrate (compose, API seeding, reset) | ~0.5 day | 242 MB image, ~30 s boot, reset = delete one 2 MB SQLite file; adds little to nightly wall-clock |
| Scenario authoring, cohorts A+B (~27) + oracles | ~6–9 days | the long pole; ~2–3 h each including the arm-the-violation check per cell |
| Sealed cohort C (~6) | ~1–2 days | by a non-register-reader |
| Harness + outcome buckets + report/baseline format | ~2–3 days | mostly reuse of `variance.py` and `write_flow_bench.py` |
| Drift arm (equivalent seeding ×2 versions + A/B layer) | ~3–4 days | after the §5 redesign |
| Versioning arm (worktrees, epoch shims, error-map validation) | ~3–4 days | 4 waypoints |
| **Total build** | **~4–6 weeks part-time** | v1 subset ≈ 2 weeks (§7) |
| One-time LLM: core learns | **≈$60–250** | ~27–33 scenarios × $1.50–4 (Odoo-DOM-calibrated) × ~1.7 attempts (the 52–60% discovery failure tax) |
| One-time LLM: versioning re-learns | **≈$150–250** | ~12-scenario subset × 3 extra waypoints |
| Per-run LLM (replay arms) | **≈$0** | 0-LLM by design; any heal/extraction calls that do occur are themselves a reported metric |
| Per-run wall-clock | ~40–60 min | core arm including resets; drift arm ≈ +1 h; versioning ≈ 2 h, occasional |
| Infra | ≈$0 | one Docker-capable box; WebArena-class hosting explicitly avoided |

---

## 7 · Phasing and gates

**Gate 0 is done** (§9) — Odoo passes, Gitea is the measured pairing (§9.5c). What follows is v1 as
five slices, one PR each per house convention, in dependency order.

### v1 — "the first availability number", ~11–14 working days

**The deliverable, stated as the thing that must be true at the end:** for every scenario, on both
substrates, the bench reports outcome + LLM calls + tokens + $ + wall-clock; writes are adjudicated
server-side; and the headline is a *pair* of availability numbers with the Odoo/Gitea contrast beside
them.

| # | slice | days | why here |
|---|---|---:|---|
| **B1** | **The run record** (code, not bench) — *rescoped, see §7a* | 3–4 | An audit against the R1–R10 outcome list found the original scope ("thread usage through") was **roughly a third** of what v1 needs, and aimed at one field inside an object thrown away a line later. Publishing cost numbers before this lands publishes wrong ones. |
| **B2** | **Substrate lifecycle + harness skeleton** | 3 | `benchmarks/customer_bench.py`, compose for Odoo + Gitea, seeding, reset (Postgres template + filestore + warmup for Odoo; delete one SQLite file for Gitea), `libfaketime` for Odoo's clock-coupled demo data, and a **per-scenario readiness hook**. Carries R4.40's guard: a near-empty first observation is a **harness error, never a scored discovery failure**. Two smoke scenarios only. |
| **B3** | **Outcome vocabulary + record/baseline format** *(done, 0.113.0 — `benchmarks/outcomes.py`)* | 2 | Reads `{ok, wrong_data, refused, over_gated}`, writes `{true, incorrect_target, double, suppressed, refused_correctly, refused_wrongly}`, emitted in `variance.py`'s record shape so `compare_records` gating comes free. `over_gated` is derived from the recipe's `mutating` flags + `FlowMeta` + `FlowReplayError.code`, never guessed. |
| **B4** | **The v1 corpus + oracles** | 4–5 | 14 scenarios (below) and their server-side oracles: SQL for Odoo, API for Gitea, plus an Idempotency-Key logging proxy. The long pole, and the one where the house rules bite hardest — see the two gates below. |
| **B5** | **Baseline, nightly job, honesty page** | 1–2 | Capture `baselines/customer_v1.json`; a nightly (not per-PR — the suite is already ~21 min against a 25-min timeout) ubuntu Docker job; and a `baselines/README.md` entry saying per number what it does and does not prove. |

### 7a · B1 rescoped — "the run record"

The original B1 was *"thread usage into `LearnResult` and `replay()`"*. Audited against the R1–R10
outcome list, that is **necessary and about a third of what v1 needs** — and it aims at one field
inside a `FlowReport` that `_attempt_replay` discards a line later. Four gaps are *missing calls*, not
missing plumbing, so no amount of threading reaches them. All four were verified in source, not taken
from the audit:

| gap | verified at | what breaks in B3/B4 |
|---|---|---|
| **G1** `extra["usage"]` is **absent, not zero**, on `mode="replay"` | `flow.py:172` nulls the heal provider, so `_router` is None at `flow.py:1068` — its own comment reads "costs nothing there", treating an absent key as a saving | "0-LLM" and "no key configured" become indistinguishable — the headline claim is unfalsifiable on the exact path the bench measures |
| **G2** `llm_calls` counts **decides, not API calls** | one `llm += 1` (`flow.py:419`) covers a fast call *and* a strong retry (`providers/llm_agent.py:124`); best-of-N attempts and `_reflect` add none | the "complete count" acceptance criterion fails even after B1 |
| **G3** vision bypasses the Router entirely | `vision.py:66-68` constructs `AsyncAnthropic()` directly | a vision-tier scenario reports cost 0 |
| **G4** cost is priced at one model for two tiers | `UsageTotals.as_dict(model)` takes a single model (`obs.py:117`) and callers pass `settings.model` (`flow.py:1221`) while the fast tier ran `settings.fast_model` | every scenario that escalated is mispriced |

Plus four discards, all in the same function and therefore **one edit**: the `FlowReport` itself
(`flows.py:2252` — traces, timings, minted keys), `landed`/`committed` on the **success** path,
`run_batch` stringifying the exception so `code`/`retryable`/`landed` are lost, and no `on_step` reaching
`replay()` (so the harness cannot attach a request listener on the gated path — R6/R8-sent).

**The invariant, stated once:** *every LLM call the engine causes is counted by one run-scoped
accounting object, and every outcome the engine already computes leaves on the record the caller
receives — no caller re-derives an engine fact.*

Concretely: a `RunRecord` returned alongside `replay()`'s data **without changing the default return
shape**; `_attempt_replay` returns its report rather than dropping it; usage sourced from a run-scoped
accounting object passed *into* `run_cached` (covering the finalize and verifier routers, not just
`provider.router`) that emits **explicit zeros**; `BatchRowResult` keeps `code`/`retryable`/`landed`;
`on_step` threaded through. Counting moves to the Router, so R3's count never comes from `llm_calls`.

**Explicitly OUT of B1 — harness-side, and must not enter `src/`:** HAR enabling, `is_write_request`
wire counting, and every ground-truth judgement (`wrong_data`, `incorrect_target`, `double`,
`suppressed`, `refused_correctly` vs `refused_wrongly`, `over_gated`). This repo's measured hazard is
that fix code is its highest-defect-density area; a benchmark must not push observability hacks into the
engine.

**Ordering:** G1–G4 must land **before B3**, since they are the accounting *class* — and they want an
AST choke-point pin in the shape S14 used, because a patch-list has already failed here once at 105 real
clients. The four discards ride with B1; deferring them means touching `_attempt_replay`'s return twice,
and the second patch is this codebase's most-repeated defect shape. Distinct codes for the five
refusals that currently share `replay_error` can follow. `incorrect_target`/`double`/`suppressed` block
**B4**, not B1, and are fixture work.

**Status (0.108.0, branch `feat/b1-run-record`).** G1, G2 and G4 are landed with tests; G3's plumbing
exists but vision does not yet report into it; the discards are not started.

| gap | state |
|---|---|
| G1 explicit zero | **done** — and see the note below, because the first attempt at it was worse than the bug |
| G2 count from the Router | **done** (pinned, not changed — `llm_calls` deliberately still counts decides, since scripted teachers have no router and re-sourcing it would zero the count across most of the suite and all of `drift_bench`) |
| G4 per-response-model pricing | **done** |
| G3 vision spend | plumbing done (`RouterWatch` marks an unobservable spender), vision not yet reporting |
| G5–G7, G9, G10 (the discards) | not started — one edit to `_attempt_replay`'s return |

**The note, because it is the reusable lesson.** The first version of G1 emitted an unconditional zero
whenever no router was visible. That is *worse than the absent key it replaced*: a run holds up to three
routers, `mode="replay"` nulls the agent provider (`flow.py:172`), and the extraction router lives inside
the `finalize` closure — so an extracting replay observed nothing, and reported `cost_usd: 0.0` over a
real paid call. An absent key is a shrug; a zero is a claim. The shipped shape only claims zero when the
run could see **every** router it could have spent through, and otherwise reports
`unobserved_llm_path`. Whoever finishes G3 should keep that property: making vision visible is what lets
a vision-tier run claim zero honestly, and until then it must not.

### The v1 corpus (14), chosen so the contrast is the headline

Paired deliberately: the same read *intent* on both substrates, so a difference is attributable to
architecture rather than to task difficulty.

| Odoo (saturating) | Gitea (non-saturating) | what the pair isolates |
|---|---|---|
| `odoo-sort-list` | `gitea-sort-list` | the cleanest read: JSON-RPC POST vs GET |
| `odoo-filter-status` | `gitea-filter-state` | filtering — R4.27's core, vs query-param GET |
| `odoo-open-record` | `gitea-open-issue` | record navigation (Odoo's also trips the *keyword* classifier on "order") |
| `odoo-search` | `gitea-search` | debounced/typed search |
| `odoo-menu-nav` | `gitea-menu-nav` | the control Odoo does **not** promote — the in-substrate control group |
| `odoo-create-lead` (write) | `gitea-comment` (write) | wire-marked write vs `form_method`-marked write |
| `odoo-idempotent-replay` | `gitea-start-timer` | replay of a landed write; and a real write with **no enclosing form**, catchable only by the wire |

### Two gates B4 must pass, because this repo's history says so

1. **Arm every oracle.** Each one is demonstrated RED against a deliberately broken run before it is
   trusted. A count is never an oracle on its own — assert the *linkage set* — and `idempotent-replay`
   must assert the mechanism ran, since `_precheck_done` (`flows.py:2705-2708`) returns `already-done`
   before any browser action and would otherwise pass inert.
2. **An adversarial pass on the corpus PR specifically**, aimed at the new harness code. Five for five,
   it is the only instrument here that has ever caught a green-but-wrong change, and a benchmark cell
   that cannot fail is this codebase's best-documented trap.

### 7b · Two findings the B1 audit turned up on the way

Neither is a benchmark concern; both are recorded here because the audit found them and they should not
evaporate with it.

* **Vision-tier calls are invisible to cost accounting.** `vision.py:66-68` builds `AsyncAnthropic()`
  directly rather than going through the `Router`, so nothing it spends reaches `router.totals`. G3
  above; a cost blind spot, not a safety one.
* **The S14 choke-point pin has an allowlist entry that voids its own inference.**
  `tests/test_inviolable_properties.py:150` allows direct SDK construction in
  `src/ultracua/vision.py` alongside the three `llm/` leaf adapters. But the test's stated reasoning is
  *"if every direct SDK construction lives in a known leaf module, then `build_client` provably IS the
  choke point and patching it is sufficient"* — and that holds only for leaves `build_client` actually
  dispatches to. `vision.py` is not one: it constructs its own client and never calls `build_client`, so
  the `no_llm` fixture (which patches `build_client`) would **not** intercept a vision call. Reachable
  only on the opt-in recovery paths, so this is not an inviolable-#1 violation today — but it is the
  same shape as the defect S14 was written to close (the one that built 105 real clients with every cell
  green), and the allowlist is what re-opened it. Worth filing as R4.41 if the maintainer agrees;
  **not filed from here**, since the register count is machine-checked.

### v1 acceptance criteria

* Two availability numbers published per substrate — **controls whose traffic `is_write_request` flags**
  and **flows whose recipe ends up write-marked** — because §9.2 briefly conflated them.
* Per-scenario cost in calls, tokens and $, including the extraction call B1 makes visible.
* Every write verified server-side; `landed` used nowhere as an oracle.
* Gitea's row-identity shape measured (the gap §9.5c leaves open).
* Every oracle shown RED once.

### Deliberately NOT in v1

The drift arm, the versioning arm at 4 waypoints, cohort B's iframe/consent/locale fixtures, the sealed
cohort C, the A/B variant layer, and the Similo resolver arm. Those are v2 (+1–2 weeks) and v3
(+1 week) as before. Sealed cohort C should not slip far — it is the anti-overfit measure, and the
A-vs-C gap is what tells a reader how much of the headline is register-shaped.

---

## 8 · What the numbers will and will not prove

**Will prove:** per-scenario success/failure with server-verified write truth on one real ERP; the first
gated availability number (reads refused / over-gated / misfiled, by transport); true per-flow cost
including the currently-invisible extraction call; survival under one vendor's real upgrade gradient and
under stochastic A/B serving; and a per-ultracua-version trend of all of the above at four waypoints.

**Will not prove:** generality beyond n=1 primary app — Odoo is one vendor's DOM culture, and the Similo
arm adds 30-app breadth **for the resolver only**, and is *survivor-biased* (pairs exist only for
elements present in both snapshots, so it cannot score fail-loud on removed elements); anything about
bot-defended or CAPTCHA-walled sites (the engine escalates there by design); recorder-authored flows
(this measures LLM authoring — the recorder ceiling has its own baseline); or timing-straddle behavior
under load, which is deliberately moved to deterministic fixtures.

**Open questions:** which Odoo version pair drifts *realistically hard* (a major pair may be violently
loud; adjacent minors may be the graded middle — measure, don't argue); the license check on
redistributing seeded snapshots (Odoo CE is LGPL — likely fine, verify); and whether
`confirm-banner-no-request` sits in the gated set as an expected-fail or as documentation only (house
style says pin it either way).

---

## 9 · Gate-0 result — measured 2026-08-14

**Verdict: Odoo PASSES as the substrate.** Ran against `odoo:17` + `postgres:15` on this Windows host
(Docker 29.7.2, linux containers), `sale_management` with demo data — 20 demo orders, S00001–S00020.
Every probe below is **key-less**: ultracua's own perception stack (`snapshot.capture`,
`locators.describe`, `locators.resolve`) and `safety` classifiers, plus one `ScriptedProvider` learn. No
LLM was called and no paid API was touched.

Four findings change the plan. Two more are recorded because **the spike refuted its own hypotheses**,
and this register's rule is that a refuted inference is worth as much as a confirmed one.

### 9.1 Grounding — no test-ids, and the list saturates the snapshot budget

`describe()` on a Sales-Orders list cell captures **no `testid`, no `elem_id`, no `placeholder`**:

```
{"role": "td", "name": "S00016", "tag": "td", "text": "S00016",
 "css": "div > table > tbody > tr:nth-of-type(1) > td:nth-of-type(2)",
 "anchor": "S00016 08/14/2026 ... Gemini Furniture Marc Demo Order ",
 "anchor_source": "row", "anchor_id": "data-id:datapoint_2"}
```

Odoo marks up with semantic classes (`o_data_row`, `o_field_cell`) and `name="..."`, so **Tier-1 test-id
binding is unavailable across the whole app** and `role` comes back as the bare tag (`td`) rather than a
`KNOWN_ROLES` member. Binding therefore lands on `exact-text` for record cells and `role+name` for real
buttons — i.e. the app behaves like `drift_sandbox`'s *roleless span-link* scenario, which is a mild
vindication of that corpus's relevance. Measured binds: order-number cell → `bound_by='exact-text'`;
top-level button → `bound_by='role+name'`.

**Snapshot budget:** `capture(max_elements=80)` returned 80 elements on a page holding ~163 candidate
interactables. **An Odoo list view saturates the default budget**, so at learn time roughly half the page
is invisible to the author. This is a discovery-cost finding, not a blocker, but it belongs in the v1
cost model and may explain authoring failures when they appear.

### 9.2 The availability signal is present, strong, and *at the classifier* — 6 of 7

Every list read in Odoo is a POST to the RPC endpoint. Running each control and classifying its traffic
with `safety.is_write_request`:

| ordinary READ control | requests | classified WRITE | endpoint |
|---|---:|---:|---|
| sort by Number | 1 | **1** | `sale.order/web_search_read` |
| sort by Customer | 1 | **1** | `sale.order/web_search_read` |
| sort by Order Date | 1 | **1** | `sale.order/web_search_read` |
| open Filters menu | 0 | 0 | (pure client-side) |
| open a record (row click) | 9 | **4** | `sale.order/web_read` |
| back via breadcrumb | 1 | **1** | `sale.order/web_search_read` |
| debounced search + Enter | 1 | **1** | `sale.order/web_search_read` |

**6 of 7 ordinary read controls put a request on the wire that `is_write_request()` calls a write** —
R4.27's 12/12 reproduced on a real commercial ERP, first attempt.

**This measures the *classifier*. The engine-level consequence was measured separately — and it
follows.** See §9.5a: over 4 reps of an ordinary "sort this list" read, `performed_write` was **4/4**,
and the cached recipe records the step as:

```
step0: action=click  mutating=True  sources=['wire']  precond_scope=set
       intent='sort the list by customer'
```

So **clicking a column header is cached as a write**, promoted by the wire. Downstream that means the
flow is approval-gated, mints an Idempotency-Key, runs the mutation gate at replay, and is refused from
`run_batch`/MCP with no heal, replan, or auth-refresh retry — R4.27's over-gating **confirmed end to
end on a real ERP**, not merely at the classifier.

> **Correction.** The first version of this section hedged that promotion did *not* fire, on the
> strength of one run reporting `performed_write=False`. That run is the outlier and the hedge was
> wrong: its SPA had not rendered (§9.5), so the click never happened and there was no POST to
> attribute. The hedge was right to demand the measurement and wrong about its result.

One second-order consequence worth its own line: a promoted read **loses verify-by-replay**
(`flow.py:889`, `if verify_replay and not performed_write`). The write-flow exemption — which exists so
re-replaying a real write cannot double-submit — is applied to a pure read, so a misclassified read is
cached *without* the verification every other read gets. Nothing here is unsafe, but the read loses a
guard by being mistaken for a write.

> **Second correction, same section.** The sentence above was first written from *reading* `flow.py`,
> and the run it cited could not have shown it: `run_cached`'s own default is **`verify_replay: bool =
> False`** (`flow.py:117`) — the Flow API turns it on, the engine path does not — so verification was
> off in every probe, for a reason that had nothing to do with `performed_write`. Re-measured with the
> control that was missing:
>
> | | `performed_write` | `extra["verify"]` |
> |---|---|---|
> | `verify_replay=False`, promoting read | True | absent |
> | `verify_replay=False`, menu navigation | False | absent |
> | `verify_replay=True`, promoting read | True | **absent — skipped** |
> | `verify_replay=True`, menu navigation | False | **`passed`** |
>
> The claim survives; the evidence for it did not exist until now. Third time in this spike that a
> mechanism read correctly out of the source still needed a measurement to show which branch actually
> ran.

### 9.3 Row identity is positional — and it is the *availability* cost, not a silent-wrong one

The row identity `describe()` captures is `data-id:datapoint_N` — an OWL render-order token, not a
record key. Measured behaviour:

* **A pure read renumbers every row.** Clicking the Customer column header (a sort) moved **15 of 15**
  rows: `S00016: datapoint_2 → datapoint_56`. Re-resolving specs captured before the sort returned
  `REFUSED, bound_by='none', 'data-id:datapoint_2' -> data-id:datapoint_56` — a **refusal of a bind that
  was pointing at the correct record**. This is the R3.7 shape (loud, safe, wrong) on a real app, with no
  drift involved at all.
* **A real membership change also refuses.** Creating *and confirming* a new order that sorts first
  (`S00022` for "AAA Advance Corp") re-minted the tokens; the pre-change row-action spec resolved
  `REFUSED, 'data-id:datapoint_56' -> data-id:datapoint_59`. **No silent wrong-record bind was observed.**

So the direction of error on Odoo is the safe one — but the availability price is severe and concrete:
**a row-targeted flow refuses as soon as anything enters or reorders the list**, which on a live ERP is
daily. That is a scenario-design constraint for the corpus (`cancel-order-row-N` must enter by record
URL, not by row position, or it measures only the refusal) and it is precisely the kind of number this
benchmark exists to publish.

Note also which targets are safe and why: an order-number cell binds by `exact-text` on `S00016`, and
that text *is* the record identity, so it is robust. A row **action button** (`Order Upsell`, icon
controls) has no record-identifying text and depends entirely on the positional `anchor_id`. The corpus
must contain both; they behave differently.

### 9.4 Two hypotheses this spike refuted — recorded so they are not re-derived

* **"The datapoint token is session-monotonic, so a recipe with a refetch step can never rebind."**
  **Refuted.** Three fresh sessions produced identical tokens: `datapoint_2` on load and `datapoint_56`
  after the same sort, every time. The token is deterministic for a fixed step sequence, which is
  exactly what deterministic replay performs — so replay rebinds fine. The hazard is data change (§9.3),
  not session variance.
* **"A new record that sorts first will produce a silent wrong-record bind."** First attempt was
  **inconclusive, not negative** — the created order was a *draft*, so it never entered the filtered
  "Sales Orders" list and the data never actually shifted. The corrected run (create **and confirm**)
  produced a refusal, not a wrong bind (§9.3).

### 9.5 Two build risks found early, both cheap to have found now

* **An ordinary 2-step Odoo read did not cache** — **diagnosed, see §9.5a. Harness-shaped, not
  substrate-shaped; Odoo stands.**
* **`networkidle` never fires on Odoo** (the bus holds a long-poll open). The engine waits on it in four
  places — `flow.py:1135`, `flows.py:1008`, `flows.py:1072`, `flows.py:1144` — all wrapped in
  `try/except` and therefore **tolerated, not fatal**. The cost is real though: a write confirm burns 8 s
  and a form login 10 s of dead wall-clock per occurrence. Budget it, and do not misread it as "Odoo is
  slow".

### 9.5a Why the read flow did not cache — root cause, measured

**The engine's first snapshot ran before Odoo's OWL SPA had rendered.** The chain, read out of
`flow.py` and then reproduced:

1. `run_cached` navigates and snapshots immediately — there is no SPA-settle step between them.
   Measured: the first observation contained **5 elements**; once the list renders it contains **80**.
2. With the target absent, the scripted teacher returned `ref=None`.
3. `session.act` then ran `page.click('[data-ultracua-ref="None"]')` (`browser.py:263`), which matches
   nothing and times out, so `ok=False` (`flow.py:504`).
4. Steps are appended only `if ok:` (`flow.py:523`), so **no `CachedStep` was recorded**.
5. `if success and steps:` (`flow.py:880`) is therefore false, `cached_here` stays False
   (`flow.py:848`), and `extra["cached"]` is False (`flow.py:904`). Replay then misses.

What actually proves the gate never opened is `cached=False` **alongside** `success=True`: with
`success` true, an empty `steps` is the only way past `flow.py:880` without caching.

> **Correction.** This paragraph first offered a different tell — that `extra` carried no `"verify"`
> key, "set on both branches inside the gate, so its absence proves the gate never opened". That is
> wrong twice over: the key is set on the two branches of the *inner* `verify_replay` test, not on both
> paths, and `run_cached` defaults to `verify_replay=False` (`flow.py:117`), under which the caching
> branch omits the key too. So its absence proved nothing, and the follow-up sentence — that this was
> "why turning `verify_replay=False` changed nothing" — had the causation backwards: it changed nothing
> because it was **already** the default. The conclusion (`steps` was empty) was right; the argument
> given for it was not.

**Confirmed by fixing it.** Adding a `prepare` hook that waits for `tr.o_data_row` before the loop:

| arm | elements seen | target | cached | replay |
|---|---:|---|---|---|
| as Gate-0 ran it | 5 | `ref=None` | **False** | `mode=miss` |
| + wait for the list | 80 | `ref=e29` | **True** | `mode=replay, success=True, llm_calls=0` |

Over 4 reps with the hook: **cached 4/4, replay 4/4 at 0 LLM calls, `performed_write` 4/4,
`write_unattributed` 0/4.**

**Two things this leaves behind.**

* *For the benchmark:* every scenario needs a per-scenario readiness hook, and the harness must treat a
  near-empty first observation as a **harness error, not a learn failure** — otherwise the corpus will
  silently score substrate races as discovery misses, which is precisely the "a cell that cannot fail"
  trap one level up. Cheap and mandatory in v1.
* *For the engine (candidate finding, not filed):* on any SPA, `run_cached` can snapshot a near-empty
  page and burn the whole learn, and the failure is **quiet** — `success=True` with nothing cached in
  the original probe, and a later replay reporting only `no cached flow for key`, which reads as "never
  learned" rather than "learned against an unrendered page". Failing safe, but neither loud nor
  diagnosable. A settle-or-refuse step after navigation (or simply refusing to author from an
  observation below some element floor) would close it. Worth a register entry if the maintainer agrees;
  deliberately not filed from here, since the register count is machine-checked.

One run of the bare arm also tripped `write_unattributed=True`, taking the explicit refusal path at
`flow.py:863` — which sets `success=False` **and persists a refusal marker**. So the unrendered-page
configuration can leave a sticky refusal behind, not just an un-cached flow. It did not recur in the 4
hooked reps (0/4).

### 9.5b Does the misfiling SATURATE? — and is Odoo therefore still the right substrate

If *every* Odoo read promotes to a write, the availability metric has no dynamic range here, and whole
regimes — heal, replan, `run_batch`, MCP exposure, auth-refresh retry — are structurally unreachable for
reads, so the corpus could never score them. Measured across five distinct ordinary reads, learned
key-lessly:

| read flow | `performed_write` | recipe has a mutating step | marked by |
|---|---|---|---|
| sort by Customer | True | **yes** | `wire` |
| sort by Number | True | **yes** | `wire` |
| open a record | True | **yes** | `keyword,wire` |
| search for a customer | True | — (learn failed; see below) | — |
| navigate to Products (menu) | **False** | no | — |

**4 of 5 promoted; 3 of 5 produced a recipe containing a mutating step.** So it is high but **not**
total, and the split is principled rather than random: **a control that fetches data promotes; pure
client-side navigation does not.** Note also `open a record`, marked `keyword,wire` — the intent text
"open the order detail" trips the keyword classifier on `order`, so it would have been misfiled even
without the wire. That is the 28%-false-positive surface showing up unprompted on a real app.

**The honest reading, and it is two-sided.** The population that saturates *is* the population that
matters — list filtering, sorting, searching, opening records is what a customer read flow does. The
non-saturated band is menu navigation, which is not where the value is. So on Odoo:

* it is an **excellent instrument for the misfiling itself**, and the ideal *regression* substrate for
  any future fix to it — today's floor is near-total, so improvement would be unmistakable;
* it is a **poor instrument for everything downstream of that** on read flows, because a write-marked
  recipe cannot exercise heal, replan, `run_batch`, MCP, or auth-refresh at all.

**Consequent change to the plan (§2):** keep Odoo as primary, but the corpus needs a **second,
classic server-rendered substrate** where reads stay GETs, so the non-saturated regime is reachable and
the recovery/batch/MCP machinery can actually be scored. Without it the benchmark would report one very
loud number and be structurally silent on the rest — and would over-report ultracua as broken on reads
by measuring only the architecture where it misfiles.

**One flow failed to learn** (`search-a-customer`: `success=False`, nothing cached, and
`performed_write=True` with no recipe to hang it on). Not diagnosed — the scripted `type`+`press` pair
is the most likely culprit rather than the substrate, and it is called out here rather than quietly
dropped because a benchmark that silently loses a scenario is the failure mode this document keeps
warning about.

### 9.5c The pairing, measured — Gitea

Chosen by running the same four questions against `gitea/gitea:1.22` (SQLite, seeded via its API with a
repo and 8 issues) that Gate-0 asked of Odoo. Key-less, no LLM.

| | Odoo 17 | Gitea 1.22 |
|---|---|---|
| ordinary read controls classified as writes | **6 of 7** | **0 of 5** — every one a GET |
| how a write gets marked | `wire` promotion | **`source=form_method`** — the structural classifier |
| a read flow, end to end | cached with `mutating=True` | `cached=True`, `mutating=False`, **`verify=passed`**, replay `llm_calls=0` |
| image / reset unit | 2.73 GB / Postgres template + filestore + asset recompile | **242 MB** / one 2 MB SQLite file |

The end-to-end row is the one that decided it: a read that learns, **passes verify-by-replay**, caches
as a genuine read and replays at zero LLM calls — the regime Odoo structurally cannot reach. It also
supplied the `verify=passed` control that §9.2's correction needed.

Write marking is the other half. `Close Issue`, `Comment`, `Star` and `Unwatch` all come back
`mutating=True source=form_method` from real `<form method="post">` controls. Two mixed cases worth
keeping in the corpus: **`Start Timer`** is a genuine write with *no enclosing form*, so nothing but the
wire can catch it; and **`Edit`** is correctly not marked, because it only opens an editor.

**Two limits on this measurement, stated rather than smoothed over.** The G2 target happened to be a
bulk-select checkbox, which captured **`anchor_id=None`** — the overloaded state R3.7/R4.37 are about.
The issue-title link, which should pick up an href-based identity, was **not measured**, so Gitea's
row-identity shape is genuinely unknown beyond "different from `datapoint_N`"; measuring it is a v1
task, not a claim. And Gitea is dev tooling: the "pull yesterday's order count" framing is a stretch,
which is the one real argument for the deferred WooCommerce option in §2.

### 9.6 What Gate-0 changes in the plan

1. Substrate confirmed **but no longer sole**: proceed with Odoo (§9.5a resolved the open risk as
   harness-shaped), paired with **Gitea** so the non-saturated read regime is reachable at all
   (§9.5b measured the need, §9.5c measured the pairing). Filed also as a candidate engine finding,
   **R4.40**, in `docs/open-defects.md`.
2. The availability finding is now end-to-end, not classifier-only (§9.2): an ordinary list sort caches
   as `mutating=True, sources=['wire']`. v1 should publish **both** numbers — controls whose traffic
   `is_write_request` flags, and flows whose recipe ends up write-marked — because they are different
   questions and this spike briefly conflated them.
3. Every scenario carries a readiness hook, and a near-empty first observation is a harness error, never
   a scored discovery failure (§9.5a).
4. Scenario-design constraint: row-targeted write scenarios need a record-URL entry path, and the corpus
   must carry **both** a self-identifying target (order-number cell) and a positional-token target (row
   action button), because Gate-0 measured them behaving differently.
5. Wall-clock model: add the tolerated-`networkidle` dead time (8–10 s per login/confirm) to §6's
   estimate.
6. The snapshot-budget saturation (80 of ~163) belongs in the learn-cost model.

**What Gate-0 does not prove.** One app, one version, one list view, one host, on demo data — it says
nothing about write correctness end-to-end (no write scenario was driven through the engine), nothing
about drift across Odoo versions, and nothing about the LLM-authoring success rate on these pages, which
remains the plan's largest cost uncertainty. It answers the one question it was scoped to answer: does
Odoo's OWL UI ground stably enough to build on. It does.
