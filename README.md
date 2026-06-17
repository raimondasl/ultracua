# ultracua

A Computer Use Agent (CUA) that drives a web browser at **5–10× human speed**.

The headline lever isn't faster clicking — it's removing the LLM from the repeat-run loop
by **learning a flow once and replaying it deterministically**. See **[PLAN.md](PLAN.md)**
for the full architecture, research basis, and roadmap.

> **Status: Phase 3 — multi-provider LLM + tiering.** Learn a flow once, REPLAY it with
> **no LLM** at ~50 ms/step (Phase 1); mutating actions are gated + idempotent, interstitials
> escalate, locators self-heal (Phase 2). Phase 3 adds a provider-neutral, content-block
> canonical layer with native **Anthropic / OpenAI / Gemini** adapters (no OpenAI-compat
> shim, no proxy); a **fast tier** (Haiku) drives routine steps and **escalates** to a
> **strong tier** (Opus/Sonnet) on low confidence, with prompt caching on the stable prefix.

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
`--provider anthropic|openai|gemini|mock`, `--tier fast|strong`, `--scope <name>`. Learned
flows live under `.ultracua/flows/`. Env: `ULTRACUA_FAST_MODEL` (default `claude-haiku-4-5`),
`ULTRACUA_MODEL` (strong, default `claude-opus-4-8`), `ULTRACUA_TIER` (default `fast`).

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

## Providers & tiering (Phase 3)

LLMs are reached through a provider-neutral, content-block canonical layer with thin
**native** adapters — Anthropic (Claude), OpenAI, Gemini — **not** an OpenAI-compat shim or a
network proxy (both drop prompt caching / strict tool args). The adapters normalize the
three concentrated differences: tool-schema shape (`input_schema` vs `function.parameters`
vs `functionDeclarations`), how tool calls surface (Claude/Gemini pre-parsed vs OpenAI
stringified args), and tool-result shape.

A **fast tier** (Haiku 4.5) drives routine element selection and **escalates** to a
**strong tier** (Opus 4.8 / Sonnet 4.6) when unsure; the stable system+tools prefix is
prompt-cached with the volatile observation placed after the breakpoint.

```bash
ULTRACUA_LLM_BACKEND=anthropic ULTRACUA_TIER=fast \
  uv run ultracua --url https://example.com --goal "..."
```

For the OpenAI / Gemini backends, install their SDKs (`uv sync --group providers`) and set
the relevant key (`OPENAI_API_KEY` / `GEMINI_API_KEY`).

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
  llm/            multi-provider abstraction: canonical types + anthropic/openai/gemini adapters + router (component 4)
  providers/      agent decision (llm_agent) + heuristic mock + scripted/oracle teachers
  verify.py       post-action state-diff (component 5)
  agent.py        the Phase 0 uncached loop (baseline)
  cli.py          `ultracua` entry point
benchmarks/       deterministic fixtures + learn-vs-replay runner (local + MiniWoB++)
```
