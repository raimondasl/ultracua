# Architecture & internals

How ultracua works inside, and how to work on it. For *using* the Flow API see [GUIDE.md](GUIDE.md);
for the original design rationale, research basis, and the phased build log see [PLAN.md](PLAN.md);
for the current honest status + measured numbers see [STATUS.md](STATUS.md).

## Contents

- [How replay works](#how-replay-works)
- [Recorder (demonstration capture)](#recorder-demonstration-capture)
- [Resilient locators](#resilient-locators)
- [Snapshot & fingerprint](#snapshot--fingerprint)
- [Pinned 0-LLM reads](#pinned-0-llm-reads)
- [Safety](#safety)
- [Multi-provider LLM layer](#multi-provider-llm-layer)
- [Actuation tiers](#actuation-tiers)
- [Scale & verification](#scale--verification)
- [Observability & resilience](#observability--resilience)
- [Cross-language daemon](#cross-language-daemon)
- [Benchmarks](#benchmarks)
- [Code layout](#code-layout)
- [Develop](#develop)

## How replay works

The spine is `run_cached` (`flow.py`). Flows are keyed by `SHA256(normalized goal + url + scope)`
and persisted as JSON under `.ultracua/flows/`.

- **Cache MISS → LEARN.** An LLM-in-the-loop discovery run drives the agent; each successful step is
  recorded as a `CachedStep` — a resilient `LocatorSpec` + the action + its `intent` + the page
  fingerprint at record time + a `mutating` flag. The flow is cached only if it succeeded *and* took
  at least one step.
- **Cache HIT → REPLAY.** Each step's locator is re-resolved on a fresh page and actuated via
  Playwright with **zero LLM**. A `finalize` hook reads the outcome (e.g. one extraction call for a
  data flow).
- **Self-heal.** When a single read step's locator no longer resolves, the engine fires one
  intent-keyed LLM call to re-ground just that step and patches the cached locator in place. A healed
  click is re-validated (`state_changed`) before it's trusted — a heal that changed nothing likely
  bound the wrong element, so it is not persisted. Mutating steps are **never** healed (see below).

`prepare` (post-nav) and `finalize` (pre-close) hooks let a caller seed a deterministic instance and
read a structured outcome; the finalize result lands in `FlowReport.extra["finalize"]`.

A `CachedFlow` now has **two authoring front-ends** — LLM-in-the-loop **discovery** (above) or human
**demonstration** (below) — both emitting the same `CachedStep` program that replay drives at 0-LLM.

## Recorder (demonstration capture)

`recorder.py` lets a human author a flow by demonstrating it once, as an alternative to LLM discovery.
The injected init-script shares `_SPECOF_JS` with `DESCRIBE_JS` (so captured steps carry the same
resilient locator hints replay resolves). Captured events — click / type / select / press(Enter) /
scroll — buffer in a **same-origin-nav-durable `sessionStorage` queue** that is drained on
`framenavigated` and again at the end, so a flow survives same-origin navigation. `_step_from_event`
assembles each event into a `CachedStep`, yielding a `CachedFlow` identical in shape to a discovered one.

Writes are gated the same way as the rest of the system: a declared write is approval-gated +
idempotency-keyed, and a **formless** write is gated by **per-write attribution** — the init-script
instruments `fetch` / `XMLHttpRequest.send` / `navigator.sendBeacon` to tie each non-idempotent request
to the commit in its synchronous turn. An **un-instrumentable** write (web-worker / service-worker /
cross-realm) or an **ambiguous/deferred** one is **refused, never cached ungated**; cross-origin demos
fail loud. A best-effort post-hoc LLM call (`caption_intents`) relabels each step's intent for self-heal
hints, `inspect` output, and the keyword side of `classify_mutation` — replay itself stays 0-LLM.

## Resilient locators

The snapshot's `data-ultracua-ref` is only valid within one snapshot, so cross-run replay needs
locators that survive a fresh page load. `describe()` (`locators.py`) captures a **ranked** hint set
for the chosen element (role + accessible name, test-id, id, placeholder, text, a short css path);
`resolve()` tries them in priority order — user-facing anchors (test-id, role+name, text) before
brittle ones (id, css) — mirroring Playwright's "prefer user-facing locators" guidance. A step
survives the loss of id / test-id / css as long as role + accessible name hold. When several
candidates match, `resolve()` prefers one that resolves **uniquely** over a blind first-match.

## Snapshot & fingerprint

`snapshot.py` runs the DOM walk, visibility filtering, and ref assignment **inside the page** (one
injected JS call), so Python stays off the hot path (full AX snapshots take seconds on heavy SPAs).
Python receives only the compact result (~viewport interactable elements + a short page-text snippet).

The **structural fingerprint** hashes `[role, name, tag]` of the snapshotted elements + url — *not*
coordinates or page text — so cosmetic drift doesn't trip it. The **mutation gate** uses a tighter,
per-step `scope_fingerprint`: the interactable controls in the *target's enclosing form/section*, so
unrelated page churn (a banner, a cart badge) doesn't false-flag a write as drift.

## Pinned 0-LLM reads

By default a data flow's replay reads the answer with one LLM extraction call. With `pin_read=True`,
`pin.py` instead locates — at learn time — the **unique deepest element** whose text equals the
extracted scalar value, records a locator anchored on that element's **`id` or `data-test-id`** (never
the value, and never a purely positional path — that could resolve to the wrong element after a layout
shift), and verifies it round-trips. On replay, `read_pin` resolves that locator **uniquely**
(`resolve(unique=True)` — an ambiguous match fails rather than guessing `.first`) and **strictly**
parses its live text to the value's type — **no model call, no API key**. Opt-in and best-effort:
anything without a stable, unambiguous, cleanly-parseable scalar isn't pinned (the flow keeps using the
extractor), and a pin that no longer resolves or parses fails loud rather than returning a wrong value.
See [GUIDE.md](GUIDE.md#pinned-0-llm-reads).

## Safety

The cached fast path is built to be the *trusted default*:

- **Mutation gate** — steps classified as irreversible writes are never blind-replayed. The classifier
  (`safety.classify_mutation`) is **DOM-structural first**: a click on a form-submit control is judged by
  the form's **method** — GET is an idempotent read (search / filter), POST/PUT/DELETE/PATCH is a write —
  which catches icon-only / bland-intent submits the keyword list misses and stops false-firing on reads
  like "submit the search"; with no form context it falls back to a keyword heuristic. Before a mutating
  step the target's scope fingerprint must match the one recorded at learn time; on drift the step **fails
  loud** (it is never LLM-healed — an agent must not re-drive a write under uncertainty).
- **Idempotency keys** — mutating replays carry an `Idempotency-Key` header so a server-honored retry
  can't duplicate a side effect. (The Flow API adds an opt-in *state precheck* for true one-shot
  idempotency — see [GUIDE.md](GUIDE.md#write-flows-submit--post--purchase).)
- **Interstitial detection** — CAPTCHA / anti-bot pages are detected and the run escalates
  (`mode="escalate"`) instead of burning retries.
- **Pacing governor** — per-origin concurrency caps + optional human-plausible jitter + Retry-After
  backoff. A no-op by default (fast/local); pass a configured `PacingGovernor` to
  `run_cached(..., governor=...)` for live sites. Speed comes from removing LLM latency, not from
  hammering origins.

## Multi-provider LLM layer

LLMs are reached through a provider-neutral, content-block **canonical** layer with thin **native**
adapters — Anthropic (Claude), OpenAI, Gemini — **not** an OpenAI-compat shim or a network proxy
(both drop prompt caching / strict tool args). The adapters normalize the three concentrated
differences: tool-schema shape (`input_schema` vs `function.parameters` vs `functionDeclarations`),
how tool calls surface (Claude/Gemini pre-parsed vs OpenAI stringified args), and tool-result shape.

A **fast tier** (Haiku 4.5) drives routine element selection and **escalates** to a **strong tier**
(Opus 4.8 / Sonnet 4.6) when unsure; the stable system+tools prefix is prompt-cached with the volatile
observation placed after the breakpoint. The `Router` (`llm/base.py`) also retries transient failures
(rate-limit / overload / 5xx / timeout) with capped backoff and accumulates token usage for cost
reporting. (All three adapters' `.complete()` glue is now covered by key-less live-path tests — see STATUS.md.)

## Actuation tiers

A target is resolved through a tiered stack — the fastest tier that works wins:

1. **WebMCP** (`webmcp.py`) — if a site exposes structured tools (`window.webmcp.listTools()/
   callTool()`), call them directly (no DOM scraping). The agent **auto-selects** these: detected
   tools are surfaced in the observation and the model emits `webmcp_call`. Near-zero real-world
   coverage today.
2. **Cached selector replay** — the 0-LLM fast path.
3. **DOM / accessibility** — the default learn path (resilient locators).
4. **Vision** (`vision.py`) — last resort for canvas / WebGL / opaque widgets: screenshot → grounding
   model → `click_xy` pixel click, replayed deterministically (brittle to layout shifts). The agent
   **requests it via `need_vision`** when the DOM lacks the target. `MockGrounding` for tests;
   `AnthropicGrounding` uses Claude vision.

## Scale & verification

- **`run_many`** (`parallel.py`) runs many flows concurrently as separate **contexts in one browser**
  (far cheaper than many browser instances), capped by `concurrency` (env `ULTRACUA_CONCURRENCY`,
  default 4). Replay makes a *single* task fast; this makes *many* tasks fast (throughput / fan-out).
- **Completion verifiers** (`verifiers.py`) — pass `run_cached(..., verifier=...)` to cache a *solved*
  flow even when the agent didn't cleanly emit `done` (a fast-tier failure mode). Ships
  `keyword_completion` (cheap, key-less) and `llm_completion(router)` (a model judge). Conservative by
  default — accuracy over hit-rate.

## Observability & resilience

The library logs through the `ultracua` logger (`obs.py`) — quiet by default (a `NullHandler`); the
CLI / daemon attach a handler, and every record carries a per-run `run_id`. Learn / replay surface
token usage + estimated `$` cost (`FlowReport.extra["usage"]`). Set `ULTRACUA_LOG_LEVEL` or pass
`--verbose`. Flow metadata sidecars and the flow cache are written atomically (temp + `os.replace`) so
a crash or a concurrent reader never sees a torn file.

## Cross-language daemon

The Python core is exposed over newline-delimited **JSON-RPC on stdio** (`ultracua-daemon`, or
`python -m ultracua.daemon`), so any language can drive it. A **Node/JS client** lives in
[`clients/node/`](clients/node/):

```js
const { UltracuaClient } = require('@ultracua/client');
const client = new UltracuaClient().start();        // spawns: uv run python -m ultracua.daemon
await client.call('health');                         // { status: 'ok', version: '…' }
await client.call('run', { url, goal, mode: 'auto', provider: 'anthropic' });
client.close();
```

Methods: `health`, `run` (learn / replay / auto → FlowReport summary), `cache.delete`. The daemon
process stays warm across calls (provider + cache reused); failures are logged to stderr (stdout
carries the protocol). The Python `DaemonClient` and the Node client are thin wrappers over the same
protocol.

## Benchmarks

ultracua ships a local deterministic fixture set plus adapters for public suites. Each is runnable;
the **current measured numbers live in [STATUS.md](STATUS.md)** (kept there so they don't drift in
two places).

```bash
# Local learn-vs-replay (demo-shop): key-less scripted teacher, or a real LLM learn run.
uv run python -m benchmarks.bench                       # scripted (no API key)
uv run python -m benchmarks.bench --provider anthropic  # real speedup

# Write/auth lifecycle (Phase D + auth refresh): action-completion, one-shot idempotency,
# and auth-refresh recovery from session expiry, against a local cookie-gated fixture.
uv run python -m benchmarks.write_flow_bench                       # key-less
uv run python -m benchmarks.write_flow_bench --provider anthropic

# MiniWoB++ (public, seed-deterministic; in the `bench` group).
uv sync --group bench
uv run --group bench python -m benchmarks.miniwob_bench             # key-less oracle
uv run --group bench python -m benchmarks.miniwob_bench --provider anthropic --all

# WebArena-Verified, offline evaluator (deterministic scoring, key-less, no containers).
uv run python -m benchmarks.webarena_bench --selfcheck   # producer->eval round-trip
uv run python -m benchmarks.webarena_bench --demo        # re-score bundled demo logs

# Variance harness: run a benchmark N times, report mean +/- spread + $ cost (real LLM; manual/local).
uv run python -m benchmarks.variance --bench demo --reps 5
uv run --group bench python -m benchmarks.variance --bench miniwob --reps 5 --all

# Standing baseline + regression gate: record a baseline once, then gate later runs against it
# (exit 1 if replay-success or cost regressed BEYOND the error bars; a drop within them is noise).
uv run python -m benchmarks.variance --bench demo --reps 5 --json baselines/demo.json   # record
uv run python -m benchmarks.variance --bench demo --reps 5 --baseline baselines/demo.json  # gate
```

Discovery (the learn run) is LLM-nondeterministic, so single benchmark runs are noisy. The **variance
harness** (`benchmarks/variance.py`) reps a benchmark and reports `mean ± stdev` of speedup /
success-rate plus the total `$` cost (read from `FlowReport.extra["usage"]`). It uses a real LLM
(key from `.env`) and is **manual/local — never wired into CI**.

To make it **standing**, `--json PATH` writes a machine-readable run record and `--baseline PATH`
gates a run against a saved one: it **exits non-zero only if `replay_success_rate` dropped below the
baseline mean by more than its error bars** (`max(0.05, baseline stdev)`) or cost rose >25% — a drop
*within* the spread is treated as noise, not a regression. `speedup` is reported but never gated (it's
a machine-dependent in-process micro-timing). The record/compare logic is pure and unit-tested
key-lessly in [`tests/test_variance.py`](tests/test_variance.py); only the actual run needs a key.

**WebArena-Verified** (ServiceNow) is WebArena's audited rebuild with **deterministic** scoring (no
LLM judge). The adapter ([`benchmarks/webarena_env.py`](benchmarks/webarena_env.py)) **never imports**
the package (it hard-pins `pydantic==2.12.0`, which conflicts) — it shells out to the pinned CLI in
its own ephemeral env via `uv tool run`. ultracua produces each `<task_id>/agent_response.json` +
Playwright `network.har` run dir, then reads back the deterministic 0/1 score. The *evaluator* is
fully offline; RETRIEVE tasks (~320 of 812) score from the response alone, while NAVIGATE (all 113)
and most MUTATE tasks assert against real HTTP and need a genuine HAR captured against **live site
containers** (Docker + WSL2) — driven by [`benchmarks/webarena_run.py`](benchmarks/webarena_run.py),
deferred for routine use. Working/eval data is kept off the system drive under `settings.data_dir`
(default `D:\ultracua-data`, `ULTRACUA_DATA_DIR`).

**Strategy** (deterministic-first; live sites deferred):

| Layer | Benchmark | Status |
|---|---|---|
| Fast inner-loop + drift sandbox | MiniWoB++ (seed-deterministic, no Docker) | wired |
| Deterministic realism | WebArena-Verified (deterministic scoring + HAR replay) | offline wired; live deferred |
| Write/auth lifecycle | local cookie-gated fixture | wired |
| Live realism (WebVoyager / Online-Mind2Web) | — | late phase only |

## Code layout

```
src/ultracua/
  browser.py      warm Playwright/CDP session
  snapshot.py     scoped DOM/AX snapshot via injected JS + scope fingerprint
  locators.py     cross-run-stable resilient locators: describe() + resolve()
  cache.py        flow cache: keyed JSON store of CachedStep programs
  flow.py         run_cached — learn-and-record / no-LLM replay / self-heal / mutation gate
  flows.py        the Flow API: FlowSpec/LoginSpec/MutateSpec, learn/approve/replay/health/auth
  recorder.py     demonstration recorder: capture init-script -> sessionStorage drain -> CachedFlow, plus per-write attribution (fetch/XHR/sendBeacon markers) and intent caption
  extract.py      reusable structured extraction (one forced-tool LLM call)
  safety.py       mutation classification, idempotency keys, pacing, interstitial detection
  obs.py          library logger (run_id) + token-usage / $ cost accounting
  llm/            multi-provider abstraction: canonical types + anthropic/openai/gemini adapters + router
  providers/      agent decision (llm_agent) + heuristic mock + scripted/oracle teachers
  verify.py       post-action state-diff
  verifiers.py    completion verifiers (keyword heuristic + LLM judge) — cache solved flows
  parallel.py     run_many — concurrent flows across contexts in one browser (throughput)
  vision.py       vision fallback tier: screenshot -> grounding model -> click_xy
  webmcp.py       WebMCP tier: detect + call site-exposed structured tools
  daemon/         JSON-RPC server (stdio) exposing the core + Python client
  agent.py        the uncached agent loop (baseline)
  cli.py          `ultracua` entry point
clients/node/     Node/JS client (@ultracua/client) for the daemon
benchmarks/       deterministic fixtures + learn-vs-replay runners (local + MiniWoB++ + WebArena)
examples/         runnable, copy-pasteable usage examples
```

## Develop

```bash
uv run pytest                    # full key-less suite (drives real Chromium against local fixtures)
uv run --group bench pytest      # also runs the MiniWoB++ integration test
node clients/node/smoke.js .     # cross-language smoke (needs Node; health check)
```

The suite is key-less and offline (real headless Chromium + local HTTP fixtures + scripted providers +
a mock extraction router), so it's deterministic and reproducible. The version is single-sourced from
`pyproject.toml` (read at runtime via `importlib.metadata`).

**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the full suite on every push to
`main` and every PR, on **Linux and Windows** — no secrets, since the suite is key-less. Two parts of
the suite are worth calling out:

- **Cassette test** (`tests/test_llm_cassette.py`) — replays a *recorded* Anthropic streaming response
  through the real SDK + adapter, so the live `.complete()` path (request build → stream →
  `get_final_message()` → parse) is covered with no network or key. Re-record when the SDK/API changes:
  `uv run python tests/test_llm_cassette.py --record` (needs a key; the cassette stores response-only,
  no secret).
- **Regression gate** (`tests/test_regression_gate.py`) — a `$0`, deterministic guard (scripted teacher
  over the demo-shop flow) that fails CI on a **cost or fidelity regression**: replay must stay 0-LLM,
  the learned flow's structure must not balloon, and replay must still reach the goal.

