# ultracua — Roadmap: from validated prototype to usable

The core thesis — **learn a browser flow once, replay it deterministically at 0 LLM, 2–7× faster**
— is validated on real authenticated sites across two distinct apps (see
[PLAN.md](PLAN.md)). What's left to make ultracua *usable by a developer for a real recurring
task* is product / reliability engineering, **not** another research breakthrough. This file
sketches the thinnest path there. See the README's *"What it's for (and what it isn't yet)"*
section for the honest maturity framing this roadmap acts on.

## The target

A developer wants:

> *"Every morning, log into vendor portal X, pull the daily order count, hand it to my code
> (DB / Slack / CSV) — and tell me loudly if it broke."*

For that to be real they need to **define** the task, **verify** what was learned, **replay** it
returning data, and **trust** that a scheduled run either gives correct data or fails loudly —
never silently returns garbage.

## What already exists (this is assembly, not a rebuild)

| Need | Status |
|---|---|
| Learn → replay → self-heal → auto-relearn | ✅ `flow.run_cached` |
| Persisted flows | ✅ `FlowCache` (keyed JSON) |
| Safety: mutation gate, idempotency, interstitial, pacing | ✅ `safety.py` |
| Auth headers / pre-nav setup | ✅ `BrowserSession(extra_headers=…)` |
| Answer extraction → structured data | ⚠️ exists but **buried in the WebArena runner's `finalize`** |
| Cross-language invocation | ✅ JSON-RPC daemon + Node client |
| Multi-provider LLM | ✅ |

The gaps are a thin **flow API**, **verify-before-trust**, and **fail-loud replay** — plus pulling
extraction and auth out of the benchmark runner into reusable core.

## The path (thinnest first)

### Phase A — "define a flow, run it, get data back" (the MVP)

Unlocks the core use case.

- A small **`Flow` spec**: `name`, `start_url`, `goal`, `auth` (storage_state / cookies / headers),
  `extract` (a schema or instruction for what to pull).
- Generalize the WebArena runner's two buried pieces into reusable core: **extraction** (run →
  return structured data) and **auth** (beyond Magento's special header — storage_state + a login
  sub-flow).
- CLI / daemon verbs: `ultracua flow learn <name>` → returns `{steps, extracted_data}` to
  **inspect**; `flow approve <name>` → marks it trusted; `flow replay <name>` → 0-LLM nav +
  extraction, **returns the data**, and **raises on fingerprint drift / unresolved locator**
  instead of returning wrong data.
- *Reuse:* `run_cached`, `FlowCache`, the daemon. *New:* the `Flow` spec, generalized extract /
  auth, the `flow` verbs, replay-returns-data + raise-on-drift.

### Phase B — "trust it unattended" (reliability)

Unlocks scheduling without babysitting. **Done:**

- ✅ **Approval gate** — `learn` records a flow as unapproved; `approve()` marks it trusted;
  `replay(require_approved=True)` refuses to run an unapproved flow (a human verifies first).
- ✅ **Confidence via shape-consistency** — `learn` records the extracted data's structure; replay
  treats a change in that shape (vs the learned run) as drift — data-level drift detection on top
  of the page-fingerprint check.
- ✅ **Relearn-or-raise policy + fail-loud signal** — `on_drift="raise"` (default) raises a rich
  `FlowReplayError` a scheduler can alert on; `on_drift="relearn"` re-authors the flow instead.
- ✅ **Auth refresh** — `FlowSpec.login` (a `LoginSpec` with **env-sourced** credentials, or an
  async callable); on drift, replay re-logs-in to refresh the cookies and retries once.
  Credentials are read from the env at runtime and **never persisted** — the login isn't cached,
  and only the resulting `storage_state` cookies are saved.

### Phase C — "operate many flows" (lifecycle / ops)

Unlocks running a fleet of recurring jobs. **Done:**

- ✅ **Per-flow run history + health** — every `replay` records its outcome into the flow's meta
  sidecar (last run, last success, last error, run/success counts, consecutive failures).
  `health(spec)` (CLI `flow status`) summarizes each flow as `not-learned` / `never-run` /
  `healthy` / `failing` / `stale`.
- ✅ **Scheduling stays the developer's job** — documented pattern: cron / Task Scheduler →
  `ultracua flow replay` (exits non-zero on drift, so alert on failure; poll `flow status`).
  No scheduler built (by design).
- A thin web UI over `health()` is possible later but intentionally out of scope.

### Phase D — "breadth": NAVIGATE / MUTATE flows

Write-actions (submit forms, post, purchase). The mutation gate + idempotency-key + pacing +
interstitial detection already existed; this phase wired the missing **action-completion
verification**. **Done (thin slice):**

- ✅ **Write flows** — `FlowSpec.mutate` (a `MutateSpec`) marks a flow as a write and declares how
  to know it landed.
- ✅ **Action-completion verification** — after a write flow runs, a declarative `confirm_*` check
  (selector / page-text / URL, mirroring `LoginSpec`'s success check) must hold, or replay **fails
  loud** (`FlowReplayError`). A write is never reported as success just because a click didn't throw.
- ✅ **Never LLM-heal a write under drift** — a mutating step whose page fingerprint drifted now
  fails loud instead of diverting to the self-heal path (an LLM must never re-drive a write).
- ✅ **Opt-in idempotency precheck** — `MutateSpec.precheck_*` runs a cheap read-only pre-pass; if
  the end-state is already present, the write is **skipped** (`status="already-done"`). For one-shot
  writes (don't purchase twice); recurring writes leave it unset. No durable "committed" ledger (it
  would wrongly skip legitimate repeat writes).
- ✅ **Approval-gated by default** — a write flow refuses to replay until `approve`d, and refuses
  `on_drift="relearn"` (re-authoring would re-perform the write).

**Out of scope (documented):** per-step verification for multi-write flows; auto-recorded
postconditions; forcing the mutation gate on writes that `is_mutating`'s keyword heuristic misses
(type+Enter, navigate-to-POST, icon-only submit) — the whole-flow confirm check still catches those;
a HAR-asserted MUTATE benchmark (needs live containers).

## The MVP line

**Phase A + the fail-loud part of Phase B** = the minimum a developer could actually use:
*define → learn → verify → replay-returns-data → fails-loud.* Scheduling and the output sink are
theirs (cron + handle the returned data). Everything else is polish.

## Three design forks (worth deciding early)

1. **Extraction = LLM call or cached selector?** Today's extraction is one LLM call per run (cheap,
   flexible, but not literally 0-LLM for data tasks). Starting with LLM-extraction is fine; a later
   "cached deterministic read" option gets truly 0-LLM at the cost of brittleness. Offer both.
2. **What does `extract` look like to a developer?** A JSON schema (structured, validates) vs. a
   natural-language instruction (flexible). Probably both, schema preferred for the trust story.
3. **How trusted is "approved"?** Pure human-approve (safe, manual) vs. an auto-verifier that
   approves when confidence is high. Start human-approve; add auto-approve later.

## First step

**`Flow` spec + reusable extraction, decoupled from the WebArena runner:** lift `extract` and
`auth` out of `benchmarks/webarena_run.py` into `src/ultracua/`, add a `Flow` dataclass +
`flow.learn()` / `flow.replay()` that returns structured data and raises on drift, and a
`ultracua flow` CLI. That single change makes ultracua usable for a real data-pull outside the
benchmark — and everything else builds on it.
