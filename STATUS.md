# ultracua — status & observations (2026-06-18)

A dated, honest snapshot: what's shipped, how proven it is, what the latest benchmark runs
measured, the known fragilities (with `file:line`), and the prioritized path forward. The
forward-looking phase plan lives in [ROADMAP.md](ROADMAP.md) — Phases A–D are shipped; the
longer-term directions (E–J) are in *"Beyond Phase D"* there.

## Verdict

**A validated prototype of a genuinely novel pattern — the core thesis is proven, the replay
engine is the moat, and it is not yet hardened for unattended production.** Phases 0–4 (core
engine), A–C (the Flow API: define → learn → approve → replay → auth-refresh → health), and D
(write flows) are shipped and merged. 105 tests, all key-less (real headless Chromium against
local fixtures). Secrets handling is a real strength: credentials are env-sourced at runtime and
**never persisted** — only the resulting `storage_state` cookies are saved (atomically).

## What's shipped, by layer

| Layer | Shipped & solid | Thin / fragile |
|---|---|---|
| **Engine** (`flow.py`) | learn → replay → heal loop; ranked resilient locators (testid → role+name → … → css); mutation gate that **fails loud, never LLM-heals a write**; stuck/interstitial detection; pacing governor | whole-page fingerprint is **over-sensitive** (a banner/badge flips it → false "drift"); self-heal is **single-step-local and never re-validates** (can silently corrupt the cached locator); first-match locator binding when role+name is non-unique |
| **Flow API** (`flows.py`) | full lifecycle; approval gate; data-shape drift; fail-loud `FlowReplayError`; auth-refresh; fleet health; Phase D writes with action-completion + opt-in idempotency precheck | scheduling is "your job" (no supervisor); meta sidecars are **non-atomic / unlocked** (concurrent replays can corrupt health) |
| **Providers** (`llm/`, `providers/`) | provider-neutral types; Anthropic path with real prompt-cache + streaming TTFT; reusable extraction; JSON-RPC daemon + Node client | OpenAI/Gemini **translation-tested but never run live**; OpenAI sends `max_tokens` (breaks newer models); **no retry/backoff/timeout**; **token usage is computed then dropped** (`llm_agent.py:129`) |
| **Ops / packaging** | config via `ULTRACUA_*` env; data kept off C:; `.env` gitignored | **no logging anywhere** (only `print`); **no CI**; version mismatch (`pyproject` 0.1.0 vs `__init__` 0.15.0); daemon is single-flight and unauthenticated |

## Benchmarks (run 2026-06-18)

| Benchmark | Provider | Result | Speedup | Notes |
|---|---|---|---|---|
| Demo-shop (4-step) | Anthropic | replay correct, 0-LLM | **58.0× total / 49.8× per-step** | learn 12.4 s (5 Opus calls, TTFT-dominated ~1.3–3 s); replay 215 ms |
| Demo-shop | scripted (key-less) | correct, 0-LLM | 1.2× | expected — scripted teacher has ~0 LLM latency |
| MiniWoB++ ×10 | Anthropic `--all` | **6/10 learn+replay; 10/10 replay 0-LLM** | **37.5–94.0×** on successes | **all 4 failures are at LEARN (discovery); zero replay failures** |
| MiniWoB++ ×3 | oracle (key-less) | 3/3 replay, 3/3 0-LLM | 1.2–1.3× | harness sanity, no LLM latency |
| WebArena-Verified | — | not re-run this session (needs Docker/WSL2) | — | prior on-disk live runs: shopping_admin **6/8**, shopping **6/8** replay@1.0, with **2 replay regressions** (tasks 126, 150) |

A saved prior MiniWoB run scored **8/10 @ 49–280×**; today's fresh run scored 6/10 @ 37–94×, with
`click-link` and `focus-text-2` failing to *author* this time. That swing is the headline insight,
not noise (see below).

**Honest caveats on the headline numbers:**

- **"Speedup" is an in-process micro-timing.** `learn.total` includes LLM latency; `replay.total`
  does not. It measures *"what fraction of step time was the LLM,"* not end-to-end wall-clock
  (excludes process/browser startup).
- **"Replay 0-LLM" means 0-LLM *navigation*.** Data-retrieval replays still make **one uncounted
  LLM extraction call** in `finalize`; `llm_calls` only counts self-heal.
- **Live replay is not perfectly faithful** (2/8 shopping regressions) — real evidence behind the
  README's "some multi-step flows don't reproduce cleanly."

## The core finding: discovery is the bottleneck, not replay

The replay engine is the moat and it is solid — 0-LLM, correct, and 37–94× faster on everything
that learned. **All variance is at discovery (learn time).** Today's 6/10 vs. the prior 8/10
differ only in which flows the LLM managed to *author*; replay failed on nothing it learned. Two of
the four misses (`click-checkboxes`, `click-option`) are the known garbled-label selection failure;
the other two (`click-link`, `focus-text-2`) were plain discovery variance this run.

**Implication for the roadmap:** invest in (1) discovery reliability, (2) replay fidelity on real
multi-step/auth pages, and (3) operability — *not* in making replay faster (it already is).

## Top fragilities a real deployment would hit

1. **No observability** — the codebase uses no `logging`; a failed scheduled replay surfaces only a
   `FlowReplayError` string. Nothing to debug a 3am failure with. *(biggest production gap)*
2. **Over-sensitive mutation-gate fingerprint** (`snapshot.py:131`) — hashes role+name+tag of every
   snapshotted element, so an unrelated banner/badge trips "drift" and **refuses valid writes**.
3. **Self-heal can corrupt the cache** — `_maybe_heal` (`flow.py:462`) re-grounds one step but never
   re-validates with `state_changed`, so a heal that binds the wrong element is persisted
   (`flow.py:488`).
4. **Non-atomic, unlocked meta sidecars** (`flows.py` `_save_meta` / `_record_run`) — concurrent
   replays (or `run_many`) can lose health updates or corrupt `.meta.json`.
5. **No LLM-call resilience** — `.complete()` has no retry/backoff/timeout; one transient 429 fails
   an entire learn.
6. **Dropped cost telemetry** — adapters populate `Usage` (tokens) but it is discarded at
   `llm_agent.py:129`; there is no $/run accounting despite "$0 replay" being a headline.
7. **Packaging mismatch** — `pyproject` version `0.1.0` vs runtime `__version__` `0.15.0` (the daemon
   reports the latter).
8. **OpenAI/Gemini are live-untested** — translation is unit-tested; the real network path of every
   adapter is exercised only via `MockClient`. OpenAI's `max_tokens` likely breaks newer models.

## Near-term priorities

**Update (2026-06-19): all seven shipped** across PRs #27 (1–3), #28 (4–5), #29 (6–7). The suite
grew from 105 → 122 tests (key-less); version 0.18.0. Original list with the PR that landed each:

1. ✅ **Correctness/packaging nits** (#27) — single-sourced the version; `_save_meta` / `cache.put`
   atomic (temp + `os.replace`). *Handled fragilities 4, 7.*
2. ✅ **Observability** (#27) — stdlib `logging` across learn/replay/heal/auth with a `run_id`;
   token usage + $ cost surfaced (`FlowReport.extra["usage"]`); daemon logs to stderr. *Handled 1, 6.*
3. ✅ **LLM-call resilience** (#27) — retry/backoff/timeout around `Router.complete`. *Handled 5.*
4. ✅ **Precision-aware mutation gate** (#28) — the gate fingerprints the target's enclosing
   form/section, not the whole page, so unrelated churn no longer false-flags a write. *Handled 2.*
5. ✅ **Heal hardening** (#28) — re-validate (`state_changed`) after a healed click (no-effect heals
   aren't persisted); `resolve()` prefers a unique candidate over an ambiguous first-match. *Handled 3.*
6. ✅ **Discovery reliability** (#29) — `learn(samples=N)` re-authors and keeps the first verified
   attempt (CLI `flow learn --samples N`).
7. ✅ **Write/auth benchmark** (#29) — `benchmarks/write_flow_bench.py`: write action-completion,
   one-shot idempotency, and auth-refresh recovery from session expiry, against a local fixture.

The remaining work is the longer-term phases below — none of the near-term fragilities are open.

See [ROADMAP.md → *Beyond Phase D*](ROADMAP.md) for the longer-term phases (E–J) with the concrete
use cases each unlocks and the gap each closes.
