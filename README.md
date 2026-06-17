# ultracua

A Computer Use Agent (CUA) that drives a web browser at **5–10× human speed**.

The headline lever isn't faster clicking — it's removing the LLM from the repeat-run loop
by **learning a flow once and replaying it deterministically**. See **[PLAN.md](PLAN.md)**
for the full architecture, research basis, and roadmap.

> **Status: Phases 0–4 complete.** Learn a flow once, REPLAY it with **no LLM** at ~50 ms/step
> (measured **66–155× / task** vs learning it) — Phase 1. Mutating actions are gated +
> idempotent, interstitials escalate, locators self-heal (Phase 2). Provider-neutral
> **Anthropic / OpenAI / Gemini** adapters with fast/strong tiering + prompt caching (Phase 3).
> A **JSON-RPC daemon + Node/JS client**, concurrent `run_many`, completion verifiers, and
> WebMCP + vision actuation tiers the agent **auto-selects** (Phase 4). See [PLAN.md](PLAN.md).

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

**Measured (Opus discovery, 0-LLM replay).** The demo-shop flow replays **66× faster** than
learning it (243 ms vs 16.2 s; ~57 ms/step, 0 LLM calls). On MiniWoB++ `--all`, **8/10**
tasks learn-then-replay correctly at **0 LLM**, with **49–280×** total speedup (text-entry
up to 280×, `click-link` 157×, multi-step `click-button-sequence` 49×); **replay is 0-LLM on
10/10**. The 2 misses (`click-checkboxes`, `click-option`) are *discovery* failures — the LLM
can't reliably select a specific garbled-string label — not replay failures. The fast tier
(Haiku) also replays the demo-shop at **129×**, but is less reliable at clean termination, so
**strong is the default discovery tier**.

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

### Public benchmark: WebArena-Verified (offline)

ultracua also drives [WebArena-Verified](https://github.com/ServiceNow/webarena-verified)
(ServiceNow) — WebArena's audited rebuild with **deterministic** scoring (no LLM judge). The
**offline evaluator path runs key-less, native on Windows, with zero containers**:

```bash
uv run python -m benchmarks.webarena_bench --selfcheck   # producer->eval round-trip (default)
uv run python -m benchmarks.webarena_bench --demo        # re-score bundled demo logs 107/108
```

`--selfcheck` writes the gold answer for a RETRIEVE task (+ a minimal valid HAR), scores it
(→ 1.0), then an empty answer (→ 0.0) — proving the whole pipeline. `--demo` re-scores the
demo logs from a cloned repo (`--src`, or `ULTRACUA_WEBARENA_SRC`) and reproduces 107→0.0 /
108→1.0.

The adapter ([`benchmarks/webarena_env.py`](benchmarks/webarena_env.py)) **never imports**
`webarena-verified` — that package hard-pins `pydantic==2.12.0`, which conflicts with ours — so
it shells out to the pinned CLI in its own ephemeral env via `uv tool run --from
webarena-verified==…`. ultracua produces each `<task_id>/agent_response.json` + Playwright
`network.har` run dir (the [`BrowserSession(record_har_path=…)`](src/ultracua/browser.py)
producer side), then reads back the deterministic 0/1 `score`.

> **Offline reach.** The *evaluator* is fully offline. RETRIEVE tasks (~320 of 812) score from
> the response alone (but still need a valid HAR present — an empty-`entries` HAR is rejected).
> NAVIGATE (all 113) and most MUTATE tasks assert against real HTTP requests, so they need a
> genuine HAR captured against **live site containers** (Docker + WSL2) — deferred. Working/eval
> data is kept off the system drive under `settings.data_dir` (default `D:\ultracua-data`,
> `ULTRACUA_DATA_DIR`). A live-run config template is at
> [`benchmarks/configs/config.example.json`](benchmarks/configs/config.example.json).

**Live (containers).** [`benchmarks/webarena_run.py`](benchmarks/webarena_run.py) drives
ultracua against a real site container end to end — start the container, render the task at
`localhost`, drive the agent (auto-login header + HAR recording) through the learn/replay
cache, extract the structured answer, and score it:

```bash
# needs Docker + ANTHROPIC_API_KEY; pulls am1n3e/webarena-verified-shopping_admin (~1.2GB)
uv run python -m benchmarks.webarena_run --site shopping_admin --task-ids 94,199
```

`run_cached` gained `record_har_path` + pre-nav `extra_headers` for this. With header auto-login
(`X-M2-Admin-Auto-Login: user:pass`), a `networkidle` settle before the read, and a flattened
answer extractor, `shopping_admin` tasks **94 and 199 both learn and replay correctly at 0-LLM
navigation (~2×)** — the replay thesis on real dynamic-retrieval tasks. With agent-exploration
prompt nudges (explore instead of quitting; prefer direct URLs over hover menus), a 10-task
`shopping_admin` baseline reaches **learn 8/10, with 6/8 learned flows replaying at 0-LLM
navigation (2.1–4.6×)** — see PLAN.md. Remaining gaps: reliable caching + filter-heavy capability.

### Benchmark strategy

Phase 1 ships its own **local deterministic fixture set** (`benchmarks/`) — the one thing no
public benchmark provides: a learn-once/replay-deterministically cache benchmark with
speedup + correctness + self-healing signal. The planned public-benchmark adoption
(deterministic-first, live sites deferred):

| Layer | Benchmark | When | License |
|---|---|---|---|
| Fast inner-loop + drift sandbox | **MiniWoB++** (seed-deterministic, no Docker) | ✅ wired | MIT |
| Harness | **BrowserGym + AgentLab** (seed pinning, trace inspector, replay agent) | with MiniWoB++ | Apache-2.0 |
| Deterministic realism | **WebArena-Verified** (deterministic scoring + HAR replay) | ✅ offline wired; live sites deferred | Apache-2.0 |
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

## Bindings — drive it from any language (Phase 4)

The Python core is exposed over newline-delimited **JSON-RPC on stdio**
(`ultracua-daemon`, or `python -m ultracua.daemon`), so any language can drive it. A
**Node/JS client** lives in [`clients/node/`](clients/node/):

```js
const { UltracuaClient } = require('@ultracua/client');
const client = new UltracuaClient().start();        // spawns: uv run python -m ultracua.daemon
await client.call('health');                         // { status: 'ok', version: '…' }
await client.call('run', { url, goal, mode: 'auto', provider: 'anthropic' });
client.close();
```

Methods: `health`, `run` (learn / replay / auto → FlowReport summary), `cache.delete`. The
daemon process stays warm across calls (provider + cache reused). Verified end-to-end: a
Node process replays a learned flow through the Python daemon at **0 LLM calls** (~200 ms).
The protocol is the same for any language — the Python `DaemonClient` and Node client are
just thin wrappers over it.

## Actuation tiers (Phase 4)

A target is resolved through a tiered stack — the fastest tier that works wins:

1. **WebMCP** ([`webmcp.py`](src/ultracua/webmcp.py)) — if a site exposes structured tools
   (`window.webmcp.listTools()/callTool()`), call them directly (no DOM scraping, ~89% fewer
   tokens). The **agent auto-selects** these — detected tools are surfaced in the observation
   and the model emits `webmcp_call` (validated live: Claude chose `add_to_cart` over DOM
   scraping). Near-zero real-world coverage today.
2. **Cached selector replay** — the 0-LLM fast path (Phase 1).
3. **DOM / accessibility** — the default learn path (resilient locators).
4. **Vision** ([`vision.py`](src/ultracua/vision.py)) — last resort for canvas/WebGL/opaque
   widgets: screenshot → grounding model → `click_xy` pixel click, replayed deterministically
   (brittle to layout shifts). The agent **requests it via `need_vision`** when the DOM lacks
   the target (or it auto-fires on an empty snapshot). `MockGrounding` for tests;
   `AnthropicGrounding` uses Claude vision.

## Scale & verification (Phase 4)

- **`run_many`** runs many flows concurrently as separate **contexts in one browser**
  (far cheaper than many browser instances), capped by `concurrency`
  (env `ULTRACUA_CONCURRENCY`, default 4). Replay makes a *single* task fast; this makes
  *many* tasks fast (throughput / fan-out).

  ```python
  from ultracua import run_many
  reports = await run_many([
      {"url": u1, "goal": g1, "provider": p1},     # learn
      {"url": u2, "goal": g2, "mode": "replay"},   # replay, no LLM
  ], concurrency=4)
  ```

- **Completion verifier** — pass `run_cached(..., verifier=...)` to cache a *solved* flow
  even when the agent didn't cleanly emit `done` (the fast tier's failure mode this testing
  surfaced). Ships `keyword_completion` (cheap, key-less) and `llm_completion(router)` (a
  reliable model judge). Conservative by default — accuracy over hit-rate.

## Develop

```bash
uv run pytest                    # core tests (drive real Chromium)
uv run --group bench pytest      # also runs the MiniWoB++ integration test
node clients/node/smoke.js .     # cross-language smoke (needs Node; health check)
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
  verifiers.py    completion verifiers (keyword heuristic + LLM judge) — cache solved flows
  parallel.py     run_many — concurrent flows across contexts in one browser (throughput)
  vision.py       vision fallback tier: screenshot -> grounding model -> click_xy
  webmcp.py       WebMCP tier: detect + call site-exposed structured tools
  daemon/         JSON-RPC server (stdio) exposing the core + Python client (core+bindings)
  agent.py        the Phase 0 uncached loop (baseline)
  cli.py          `ultracua` entry point
clients/node/     Node/JS client (@ultracua/client) for the daemon
benchmarks/       deterministic fixtures + learn-vs-replay runner (local + MiniWoB++)
```
