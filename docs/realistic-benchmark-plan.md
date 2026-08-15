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
| Live public sites (the `examples/hn_digest.py` pattern) | SMOKE ONLY | One or two read-only flows as a reality anchor and a Windows-local smoke; non-deterministic, so never gated |
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

0. **Gate 0 — the 1-day spike (first).** Hand-learn two scenarios on Odoo (`filter-orders`,
   `cancel-order-row-N`) and inspect what `locators.resolve` binds: does the OWL UI expose stable
   accessible roles, or does everything bind positional CSS? This gates the substrate. Odoo fails →
   SuiteCRM; both fail → authored modern-transport fixtures (a weaker realism claim that still delivers
   the availability metric). **Result recorded in §9.**
1. **v1 (~2 weeks)** — instrumentation PRs; one Odoo version; 12 scenarios (the R4.27 six, one debounced
   read, the core writes, `idempotent-replay` with mechanism assertions); server-side oracles;
   `variance.py`-format report with the new buckets; nightly run. **Deliverable: the first gated
   availability number.**
2. **v2 (+1–2 weeks)** — the rest of cohort A (row-identity writes, batch/resume, auth), cohort B
   fixtures, the sealed cohort's first run, the A/B variant layer.
3. **v3 (+1 week)** — drift arm across two further Odoo versions; versioning arm at 4 waypoints; the
   Similo resolver arm; a `baselines/README.md` entry stating per number what it does and does not prove.

Each phase ships the way everything here ships: an adversarial pass aimed at the new harness code before
the PR — the corpus-authoring PRs especially, since **a benchmark cell that cannot fail** is this
codebase's best-documented trap.

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

## 9 · Gate-0 result

*(pending — filled by the spike; see the PR that adds this section)*
