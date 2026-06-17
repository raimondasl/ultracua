# ultracua

A Computer Use Agent (CUA) that drives a web browser at **5–10× human speed**.

The headline lever isn't faster clicking — it's removing the LLM from the repeat-run loop
by **learning a flow once and replaying it deterministically**. See **[PLAN.md](PLAN.md)**
for the full architecture, research basis, and roadmap.

> **Status: Phase 2 — resilience & safety.** First run on a (goal, url) LEARNS the flow
> with an LLM and caches a resilient selector+action program; later runs REPLAY it with
> **no LLM** at ~50 ms/step. Resilient locators survive DOM drift (incl. JS-listener
> elements); a per-step LLM self-heal recovers when they don't. Mutating actions are never
> blind-replayed (page-fingerprint gate + idempotency keys); CAPTCHA/anti-bot interstitials
> escalate; a pacing governor keeps speed off the wire.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) (manages Python itself — no separate Python install needed)

## Setup

```bash
uv sync                              # create the venv + install deps
uv run playwright install chromium   # one-time browser download
```

## Run it

```bash
# First run LEARNS + caches (needs ANTHROPIC_API_KEY); second run REPLAYS with no LLM.
# PowerShell: $env:ANTHROPIC_API_KEY = "sk-ant-..."
uv run ultracua --url https://example.com --goal "open the more information link"
uv run ultracua --url https://example.com --goal "open the more information link"   # replays
```

Flags: `--mode auto|learn|replay`, `--fresh` (clear the cached flow first),
`--provider anthropic|mock`, `--scope <name>`. Learned flows live under `.ultracua/flows/`.

## Benchmark

A deterministic, key-less learn-vs-replay benchmark on local fixtures:

```bash
uv run python -m benchmarks.bench                      # scripted teacher (no API key)
uv run python -m benchmarks.bench --provider anthropic # real LLM learn run -> true speedup
```

It LEARNS a 4-step demo-shop flow, then REPLAYS it from cache and reports per-step latency,
the speedup, and replay correctness (reached the goal state, with **0 LLM calls**). The
scripted teacher has ~0 LLM latency, so a meaningful speedup ratio needs `--provider anthropic`.

### Public benchmark: MiniWoB++

ultracua also drives the public, seed-deterministic
[MiniWoB++](https://github.com/Farama-Foundation/miniwob-plusplus) suite (in the `bench`
dependency group):

```bash
uv sync --group bench                                               # one-time
uv run --group bench python -m benchmarks.miniwob_bench             # key-less oracle teacher
uv run --group bench python -m benchmarks.miniwob_bench --provider anthropic --all
```

It seeds a deterministic task instance, LEARNS it, then REPLAYS from cache with **0 LLM
calls**, scored by MiniWoB's own reward. (MiniWoB link tasks use `<span>` + JS listeners,
invisible to the DOM snapshot; button/input tasks are covered.)

### Benchmark strategy

Phase 1 ships its own **local deterministic fixture set** (`benchmarks/`) — the one thing no
public benchmark provides: a learn-once/replay-deterministically cache benchmark with
speedup + correctness + self-healing signal. The planned public-benchmark adoption
(deterministic-first, live sites deferred):

| Layer | Benchmark | When | License |
|---|---|---|---|
| Fast inner-loop + drift sandbox | **MiniWoB++** (seed-deterministic, no Docker) | ✅ wired | MIT |
| Harness | **BrowserGym + AgentLab** (seed pinning, trace inspector, replay agent) | with MiniWoB++ | Apache-2.0 |
| Deterministic realism | **WebArena-Verified** (deterministic scoring + HAR replay) | realism phase | Apache-2.0 |
| Live realism (WebVoyager / Online-Mind2Web) | — | late phase only | — |

## Safety (Phase 2)

The cached fast-path is built to be the *trusted default*:

- **Mutation gate** — steps classified as irreversible (submit/pay/send/delete/…) are never
  blind-replayed. Before such a step, the page fingerprint must match the one recorded at
  learn time; on drift it self-heals via one LLM call or fails closed rather than firing a
  wrong action.
- **Idempotency keys** — mutating replays carry an `Idempotency-Key` header so a retry can't
  duplicate a side effect.
- **Interstitial detection** — CAPTCHA / anti-bot pages are detected and the run escalates
  (`mode="escalate"`) instead of burning retries.
- **Pacing governor** — per-origin concurrency caps + optional human-plausible jitter +
  Retry-After backoff. A no-op by default (fast/local); pass a configured `PacingGovernor`
  to `run_cached(..., governor=...)` for live sites. Speed comes from removing LLM latency,
  not from hammering origins.

## Develop

```bash
uv run pytest                    # core tests (drive real Chromium)
uv run --group bench pytest      # also runs the MiniWoB++ integration test
```

## Layout

```
src/ultracua/
  browser.py      warm Playwright/CDP session (component 1)
  snapshot.py     scoped DOM/AX snapshot via injected JS (component 3)
  locators.py     cross-run-stable resilient locators: describe() + resolve()
  cache.py        flow cache: keyed JSON store of CachedStep programs (component 2)
  flow.py         run_cached — learn-and-record / no-LLM replay / self-heal / mutation gate
  safety.py       mutation classification, idempotency keys, pacing, interstitial detection (component 6)
  providers/      LLM adapters: anthropic, mock, scripted (component 4)
  verify.py       post-action state-diff (component 5)
  agent.py        the Phase 0 uncached loop (baseline)
  cli.py          `ultracua` entry point
benchmarks/       deterministic fixtures + learn-vs-replay runner (local + MiniWoB++)
```
