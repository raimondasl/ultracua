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

Unlocks running a fleet of recurring jobs.

- A flow **registry with metadata** (last run, last success, drift history) and a simple
  **status / health** view (CLI + structured logs; a thin web UI later).
- **Scheduling stays the developer's job** initially — documented pattern: cron / Task Scheduler →
  `ultracua flow replay`. Don't build a scheduler yet.

### Phase D — "breadth" (later, separate)

NAVIGATE / MUTATE flows (submit forms, post, purchase) — the mutation gate + idempotency already
exist; wire action-completion verification. Bigger, and only if the use case needs write-actions,
not just data pulls.

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
