> HISTORICAL — original design plan (~#62, pre-recorder). The Phase 0-4 design still matches the code; see STATUS.md / ROADMAP.md for live status (recorder + Phases A-I shipped since).

# ultracua — Implementation Plan

A Computer Use Agent (CUA) that drives a web browser at **5–10× human speed**.

> Status: in progress. Python-first, `uv`-managed. Built on Playwright/CDP + a custom
> speed/agent layer, with a multi-provider LLM abstraction and a learn-once / replay-fast
> flow cache as the spine.
>
> **Done:** Phase 0 (walking skeleton) · Phase 1 (flow cache + deterministic replay;
> MiniWoB++ benchmark) · Phase 2 (mutation gate + idempotency, interstitial escalation,
> pacing governor, TTL/versioned cache, JS-listener snapshot coverage, self-healing locators) ·
> Phase 3 (provider-neutral content-block layer + native Anthropic/OpenAI/Gemini adapters,
> fast/strong tiering with escalation, prompt-cache breakpoint).
> **Phase 4 in progress.** Done: (1) **core+bindings daemon** — Python core over JSON-RPC on
> stdio (`ultracua-daemon`) + Python `DaemonClient` + Node/JS client (`clients/node/`),
> cross-language replay at 0 LLM; (2) **parallelism + completion verifier** — `run_many`
> (concurrent flows as contexts in one browser) and a pluggable `verifier` that caches
> solved-but-not-`done` flows (`keyword_completion` / `llm_completion`); (3) **vision + WebMCP
> tiers** — `vision.py` (empty DOM -> screenshot -> grounding -> `click_xy`, replayed
> deterministically; Mock/Anthropic grounding) and `webmcp.py` (detect + call site-exposed
> tools via the `webmcp_call` action); (4) **LLM-facing tier integration** — the agent's
> action schema exposes `webmcp_call`/`need_vision`, the observation lists WebMCP tools, and
> the model **auto-selects** them (validated live: Claude chose a WebMCP tool over DOM
> scraping).
> **Phases 0–4 complete.** Rust hot-kernel intentionally skipped (DOM work runs in-browser, so
> Python is not the bottleneck). Deferred: action batching. The OpenAI/Gemini adapters are wired (behind the `providers` extra); only the API keys are user-supplied.
>
> **Realism layer (in progress).** WebArena-Verified **offline** path wired
> (`benchmarks/webarena_env.py`): the deterministic evaluator is driven via an isolated
> `uv tool run` subprocess (it hard-pins `pydantic==2.12.0`, so it is never imported), ultracua
> produces the `agent_response.json` + Playwright `network.har` run dir, and scores are read
> back — validated key-less with zero containers (demo logs 107→0.0 / 108→1.0, and a producer→eval
> round-trip → 1.0). Working/eval data lives under `settings.data_dir` (default `D:\ultracua-data`,
> env `ULTRACUA_DATA_DIR`), kept off the system drive. Live site containers (Docker/WSL2) +
> NAVIGATE/MUTATE HAR-asserted scoring remain deferred (action batching is the ideal next lever
> to exercise against these long multi-step tasks).
>
> **Live slice (done).** `benchmarks/webarena_run.py` drives ultracua against a live
> `shopping_admin` container (Docker, header auto-login, HAR recording) and scores it end to
> end; `run_cached` gained `record_har_path` + pre-nav `extra_headers`.
>
> **Replay fidelity on dynamic retrieval — hardened.** The live run exposed three issues, now
> fixed: (1) the auto-login header was wrong (`X-M2-Admin-Auto-Login: user:pass`, not the stale
> `…-User` docstring) — the agent had been logging in by hand, polluting flows and tripping
> lockouts; (2) replay extracted before async grids settled (`networkidle` wait); (3) the answer
> extractor over-nested lists (`[["x"]]`) — now flattened. With these, `shopping_admin` tasks **94
> and 199 both LEARN and REPLAY correctly at 0-LLM navigation (~2×)**, and 199 dropped from 15
> flailing steps to 3 — the 5–10× replay thesis demonstrated on real dynamic-retrieval tasks. A
> small core fix stops the LLM agent leaking tool-call markup into cached steps. Remaining misses
> (give-up-after-1-step exploration; complex multi-filter aggregation) are **agent-capability**
> work, distinct from replay fidelity.
>
> **Agent exploration + baseline.** Two prompt nudges — *explore/navigate instead of quitting
> when the answer isn't on the current page*, and *prefer a direct URL over nested hover menus*
> (which don't reproduce on replay) — lifted live **learn success from 40% → 80%** (8/10) on a
> `shopping_admin` RETRIEVE baseline, with **6/8 learned flows replaying correctly at 0-LLM
> navigation (2.1–4.6×)**. The runner is now crash-safe on cache-miss / unscorable runs.
>
> **Reliable caching + consolidation.** `run_cached` now also caches a flow when the `finalize`
> hook signals completion (`{"solved": True}`) — a read task that solved via final full-text
> extraction caches even though the agent never emitted `done` (the agent's observation is a
> 1500-char snippet, so this full-text signal is more reliable than an observation-based
> verifier). A consolidated 10-task `shopping_admin` baseline holds at **~6/10 doing the full
> learn→replay thesis at 0-LLM navigation (2.0–7.3×)**. The remaining misses turned out **not**
> to be caching bugs: 345/78 (date/status filtering) and a flaky 198 (multi-step open-record)
> are **agent capability**; 183 produces no clean cacheable steps on a fiddly product-quantity
> filter (erratic flow). **Status: the 5–10× learn-once/replay-fast thesis is validated on real
> dynamic-retrieval tasks.** Further gains are capability work (filter-heavy tasks, multi-step
> fidelity), not the speed mechanism. Action batching remains shelved (replay is 0-LLM; one-time
> learn is 2–12 cheap calls).
>
> **Second site — cross-site evidence.** Added the Magento customer **storefront** (`shopping`,
> a different app + auth header `X-M2-Customer-Auto-Login: email:password`) to the runner's site
> registry — no other site-specific code. On single-lookup RETRIEVE tasks the thesis generalizes
> cleanly: **6/8 learn AND replay at 0-LLM navigation (1.8–6.3×)** (e.g. "order number of my most
> recent {cancelled,pending,complete} order", "total cost of my latest {…} order", "first
> purchase date"). Harder aggregation/semantic tasks (price ranges, spend-by-period, review
> mining) fail at *learn* on the storefront just as they do on the admin — a **consistent
> capability ceiling, not site-specific**. The new site exposed (and we fixed) two general
> robustness bugs: a snapshot racing a navigation ("execution context destroyed") now retries
> after settling, and the batch runner no longer dies when one task throws. **The speed mechanism
> + pipeline (auth, drive, HAR, deterministic scoring, 0-LLM replay) are site-agnostic, validated
> across two distinct apps.**

---

## 1. The one fact that determines the whole design

Across every source we surveyed, the result is unanimous: **per-step latency is dominated by
LLM/VLM inference, not by the browser.**

- OSWorld-Human (MLSys 2026): model calls are **76–96 %** of total task latency; actuation (a
  CDP click/type) is single-digit-% / **tens of ms**.
- Today's CUAs are **10–24× *slower* than humans**, and take **2.7–4.3× more steps** than needed.
- Even the fastest hosted "fast" model spends **~0.6–0.9 s on time-to-first-token (TTFT) alone**
  (Haiku 4.5 ≈ 0.58–0.83 s); frontier models ≈ 1.2–1.5 s. So an **LLM-in-the-loop step can never
  hit a sub-second budget.**

**Therefore the 5–10× goal is won by removing the model from the repeat-run loop — not by
faster clicking.** The lever is **flow caching / compile-and-replay**: discover a task once with
the LLM, freeze it to a deterministic program of resilient selectors + actions, and replay it with
**zero LLM calls** at **~50–150 ms/step**. This is validated in production by Stagehand (~80 %
sequential speedup, 10–100× per cached step), Skyvern (explore→replay, 2.3× faster + LLM-free),
browser-use Deterministic Rerun (~99 % cheaper), and Agentic Compilation (up to 1500× cheaper,
O(1) amortized).

### Honest expectations (set these up front)
| Path | What it is | Speed |
|---|---|---|
| **Cached / repeat flow** | Replay learned program, no LLM | **5–10×+ human** (the headline) |
| **Cold / novel flow** | First encounter, LLM in the loop | **~1.5–3× human** (learning run) |
| **Vision fallback** | Canvas/WebGL/opaque widgets | ~1–2 s/step (rare, by design) |

5–10× is a claim about **learned/repeat** flows. A never-seen flow runs the cold path **once**,
then is compiled so the **second** run is the 10×+ run.

---

## 2. Language decision: Python core (and why it's performant enough)

You asked for Python and to flag if it isn't performant enough. **It is** — because the work that
matters is **I/O-bound** (CDP messages to Chrome + LLM HTTP calls), which `asyncio` handles
excellently, and the dominant cost (the LLM round-trip) is language-independent.

- **Precedent:** browser-use — the fastest agent in our research — is **Python + CDP-native**.
- **Cached-step math:** a replay step = a sub-ms fingerprint check + a few CDP input ops (a few ms
  round-trip to Chrome) + an actionability wait + a post-action assertion. Python's per-step
  overhead is event-loop scheduling + a couple of JSON messages — **low single-digit ms**,
  comfortably inside the 50–150 ms budget.

### The only place Python could bite, and the escape hatches (in priority order)
The one CPU-bound hot path is **DOM pruning / diffing / serialization / hashing** on heavy pages.

1. **Run it in the browser (primary mitigation).** Do pruning + dirty-region diffing in injected
   JS via `Runtime.evaluate`; Python only receives the compact (~200–500 token) result. This moves
   the CPU work into V8 and removes ~all Python CPU concern.
2. **Fast Python libs** for any host-side parsing/hashing: `selectolax`/`lxml`, `orjson`, `xxhash`.
3. **Rust extension via PyO3/maturin** for a *single profiled* kernel (e.g. AOM/DOM-diff), built
   and managed by `uv`. PyO3 releases the GIL, so it also unlocks CPU parallelism if ever needed.

**Rule for reaching to another language:** only the serialization/diff kernel, and **only if
profiling shows it exceeds a few ms/step and dominates the cached fast-path budget.** Everything
else stays Python. We define the `napi`/PyO3 boundary up front (Phase 4) so it can drop in without
reshaping the core — but we do **not** build it speculatively.

### Honoring the earlier "both core + bindings" answer
Python **owns the core**. Cross-language reach is delivered *later* (Phase 4) the same way
Playwright does it, just inverted: the Python core runs as a **warm daemon** speaking
newline-delimited **JSON-RPC over stdio/UDS**, with thin clients (e.g. a TS client) on top. For a
Python-only consumer, the library *is* the core in-process (zero hop) — no daemon needed.

---

## 3. Architecture: a tiered actuation stack with a flow cache as the spine

The whole speed story is **which tier a step lands in**. Prefer the fastest tier that can resolve
the target.

```
                        ┌─────────────────────────────────────────────┐
   task + goal  ───────▶│            ultracua core (Python)            │
                        │                 asyncio                      │
                        └─────────────────────────────────────────────┘
                                          │
            ┌─────────────────────────────┼─────────────────────────────┐
            ▼            ACTUATION / PERCEPTION TIER ORDER                ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │ Tier 0  WebMCP native tools        (where a site exposes them; ~89% fewer  │
   │         no scrape, no cache needed   tokens) — stub behind the interface   │
   ├──────────────────────────────────────────────────────────────────────────┤
   │ Tier 1  CACHED FAST-PATH  ★ default  replay stored resilient-locator       │
   │         NO LLM · ~50–150 ms/step     program; gated by page fingerprint +  │
   │                                      Playwright actionability + assertion   │
   ├──────────────────────────────────────────────────────────────────────────┤
   │ Tier 2  DISCOVERY / LLM-in-loop      scoped sanitized AX/DOM snapshot       │
   │         ~1–3 s/step (cold)           (~200–500 tok) → model emits element   │
   │                                      id; compile result into the cache      │
   ├──────────────────────────────────────────────────────────────────────────┤
   │ Tier 3  VISION FALLBACK (rare)       transient screenshot + grounding model │
   │         ~1–2 s/step                   for canvas/WebGL/opaque widgets        │
   └──────────────────────────────────────────────────────────────────────────┘
```

### Core components (all in the Python core)
1. **Warm session host** — long-lived process holding a **persistent CDP/Playwright session** and
   warm LLM connections. Keeping these hot across calls is the single biggest *structural* win
   (collapses the 150–400 ms reconnect tax seen in multi-service setups to sub-ms in-process calls).
2. **Flow Cache + Replay Engine** *(the 5–10× lever)* — stores discovered flows as **parameterized
   tools/skills** (intent + ordered resilient-locator+action program + `@{{variables}}`), keyed by
   `SHA256(method + normalized URL + DOM-structure hash + scope)`. On hit: passive fingerprint
   comparison; clears threshold → deterministic CDP replay, zero LLM; drift → treat as miss.
3. **Scoped DOM/AX Snapshot Pipeline** — viewport + interactable elements only, strip hidden/volatile
   nodes (~85 % compression), shadow-DOM/iframe handling, **dirty-region diffing**, persistent ref
   IDs reused across steps. Runs in-browser (JS) to keep Python light. Prevents the DOM build from
   *becoming* the bottleneck (full AX snapshots take 3–26 s on heavy SPAs).
4. **Multi-Provider LLM Router** — thin in-process adapters over each provider's **native** API
   (details in §5). Routes routine steps to a **fast** tier, escalates novelty/failure to a
   **strong** tier. Only ever called on the cold/discovery path.
5. **Verification + Self-Healing Layer** — Playwright actionability + auto-retrying assertions +
   cheap state-diff per step; ranked semantic locator fallback chain; single intent-keyed LLM
   re-grounding that heals **the one broken step, not the whole task**.
6. **Safety / Pacing Governor** — idempotency keys on mutating steps, capped backoff + jitter
   honoring `Retry-After`, per-origin concurrency caps, human-plausible pacing, CAPTCHA/anti-bot
   detection → escalate-to-human.
7. **Transport + thin clients** *(Phase 4)* — JSON-RPC over stdio/UDS + a streaming event channel;
   Python in-process client (zero hop) and a thin TS client; PyPI wheel / npm package.

---

## 4. The flow cache (the spine), end to end

**LEARN (cold, first encounter).** Strong/fast tier reasons over a scoped sanitized snapshot and
emits an `observe()`-style resolution per step — `{resilient selector (role/text/label/test-id),
method, arguments, description, INTENT}`. On success the engine **compiles** the trajectory into a
parameterized tool/skill (intent + ordered locator+action program + `@{{vars}}`) — *not* a lone
selector (WALT granularity: most learned tools then run fully deterministically).

**REPLAY (fast, default, NO LLM).** Per step: passive fingerprint compare → if it clears the
safety threshold, execute the stored resilient locator via in-process CDP, gated by Playwright's
built-in actionability (visible/stable/enabled/editable/receives-events) + a post-action state-diff
assertion. ~50–150 ms, zero tokens, zero network. **Bias: accuracy over hit-rate — "a wrong cached
click is worse than a slow click."**

**SELF-HEAL (durability).** On drift/locator failure: (1) walk a ranked semantic fallback chain
(`id → aria/role → visible text → nearby label → position`), re-cache the primary on success;
(2) if it dead-ends, make **one** intent-keyed LLM re-grounding call, patch the single broken step,
continue; (3) if the flow fundamentally changed, fall back to full discovery and re-compile.
Storing **why** (intent), not just the selector, is what makes the cache repairable.

**HYGIENE.** Per-session, versioned, TTL'd entries; detect low cache-hit confidence (per-load
randomized DOMs, randomized URLs, A/B layouts) and fall back rather than force brittle hits.
**Mutating steps (submit/pay/send/delete) are never blind-replayed** — verification gate +
idempotency key required.

---

## 5. Multi-provider LLM abstraction

- **Native adapters, in-process — not an OpenAI-compat shim, not a network proxy.** The OpenAI-compat
  path is *disqualifying*: it silently drops the three features we most need — **prompt caching**,
  **strict tool args**, **extended thinking** (Anthropic itself calls it non-production). A network
  proxy (LiteLLM proxy / AI Gateway) adds **8–40 ms + a TCP hop per step** — keep those for offline
  eval / cost tracking / key rotation only.
- **Canonical message type = content blocks** (`text | thinking | tool_use | tool_result`), the
  superset you can losslessly down-convert to OpenAI's flatter `tool_calls[]` (stringified args).
  Adapters normalize the 3 concentrated differences: tool-schema shape, how tool calls surface
  (Claude/Google pre-parsed vs OpenAI stringified), tool-result return shape.
- **Tiering (per *session*, not per step** — switching models mid-session busts the prompt cache):
  **strong** = Opus 4.8 / Sonnet 4.6 for discovery; **fast** = Haiku 4.5 (~0.58–0.83 s TTFT, ~94 t/s,
  $1/$5), or local Qwen/Llama-8B on vLLM + **XGrammar** constrained decoding for the latency floor.
- **Prompt caching = the biggest cold-path latency lever** *if disciplined*: cache the stable
  `system + tools` prefix, put the **breakpoint *before* the volatile observation** (DOM/screenshot),
  pre-warm at session start. Naive full-context caching can *increase* latency.
- **Output-token discipline:** output tokens cost ~215× input per unit → keep actions to 10–15
  tokens; order **stable history before volatile DOM** for KV-cache hits.

---

## 6. Reliability, safety & the speed/accuracy tradeoff

- **Resilient locators only:** `getByRole > getByText/getByLabel > getByTestId`, never bare CSS/XPath.
- **Verification is tiered:** cheap rule-based pre/post state-diff first (Playwright assertions);
  escalate to an LLM judge only when ambiguous (LLM-judge ≈ 85.7 % human agreement — not free).
- **Real success rates are far below lab numbers** (WebVoyager ~90 % → Online-Mind2Web human-eval
  ~28–61 %). Design for **~40 % live failure** with verification + graceful fallback, not the
  optimistic number.
- **Going fast on the wire is itself a bot signal** (JA3/JA4 TLS, behavioral classifiers,
  429→IP-ban incl. shared block lists). **Measure speed as effective wall-clock per task won by
  removing LLM latency — *not* raw request rate.** Keep human-plausible pacing + per-origin caps.
- **CAPTCHAs are a hard wall** (best agents ~40 %): detect and **escalate to human**, never burn
  retries inline.
- **Mutations** need idempotency keys (client UUID, server-replayed) + backoff honoring `Retry-After`.

---

## 7. Python tech stack (`uv`-managed)

| Concern | Choice |
|---|---|
| Packaging / env | **`uv`** (`uv init`, `uv add`, `uv run`, `uv.lock`, `pyproject.toml`) |
| Browser actuation | **Playwright-Python** (actionability, resilient locators, auto-wait) + its `CDPSession` for the raw-input hot path & injected-JS DOM extraction |
| Concurrency | `asyncio` (I/O-bound); `multiprocessing`/in-browser work for CPU-bound snapshot processing |
| DOM/HTML host-side | `selectolax`/`lxml`, `orjson`, `xxhash` (fingerprints) |
| Schemas / validation | `pydantic` v2 (action/observation/cache schemas) |
| LLM SDKs | `anthropic`, `openai`, `google-genai` behind the native-adapter interface |
| Local fast tier (opt) | `vLLM` + **XGrammar** constrained decoding (Qwen/Llama-8B) |
| Native hot kernel (opt) | **Rust via PyO3 + maturin** — profiling-gated only |
| Tests / eval | `pytest` + `pytest-asyncio`; a small live-site benchmark harness |

---

## 8. Phased roadmap

**Phase 0 — Walking skeleton (warm core, LLM-in-the-loop, no cache).** Prove the warm-session
single-hop CDP path end-to-end and establish the latency baseline.
- Python core holding a warm Playwright/CDP session.
- Scoped, sanitized AX/DOM snapshot pipeline (viewport+interactable, dirty-region diff, in-browser).
- Single-provider (Claude Messages) adapter emitting `observe()`-style `{selector, method, args, intent}`.
- Playwright actionability + post-action state-diff verification.
- **Instrumented per-step latency breakdown** (snapshot / TTFT / generation / actuation).

**Phase 1 — Flow cache + deterministic replay (the 5–10× lever).** Demonstrate run-2-vs-run-1
10×+ on a repeat flow.
- Cache keyed by `SHA256(method + normalized URL + DOM-hash + scope)` + passive fingerprint gate.
- Deterministic CDP replay of resilient locators at 50–150 ms/step.
- Compile-to-tool/skill with `@{{variable}}` parameterization; drift → miss → re-discover.
- Benchmark: cached-flow wall-clock vs human and vs Phase-0 cold path.

**Phase 2 — Self-healing + safety + verification hardening.** Make the cache durable under drift
and safe for mutating actions, so the fast-path can be the trusted default.
- Ranked semantic fallback chain + re-cache on success.
- Intent-keyed single-step LLM re-grounding (heal the step, not the task).
- Idempotency keys + backoff + `Retry-After`; pacing governor; CAPTCHA/anti-bot escalation.
- Tiered verification; TTL/versioning + low-confidence detection.

**Phase 3 — Multi-provider abstraction + tiering + prompt caching.**
- Content-block canonical type + in-process native adapters (Claude/OpenAI/Gemini).
- Fast/strong tiering; per-session tier choice; prompt-cache breakpoint discipline + pre-warm.
- Model routing with confidence/verification-based escalation; action batching for predictable subflows.

**Phase 4 — Bindings, packaging, vision fallback, scale.**
- Daemon + JSON-RPC + streaming events; in-process Python client + thin TS client; PyPI/npm packaging.
- Vision fallback tier + WebMCP tier where present.
- Parallelism across 20–50 Playwright contexts (per-agent `userDataDir`) for throughput / fan-out.
- Optional speculative execution (cold path); optional Rust native-addon for the profiled DOM-diff kernel.
- End-to-end benchmark of the assembled stack on representative **live** sites.

---

## 9. Open questions (don't block Phase 0)

1. Cache **granularity** that generalizes best (per-action / per-subflow / parameterized site-tool)
   and how `@{{var}}` extraction is inferred during learn.
2. Concrete **fingerprint function + safety threshold** that minimizes both false-hits and false-misses.
3. **Local fast tier** (Qwen/Llama-8B + XGrammar) worth the ops cost vs hosted Haiku 4.5, given the
   cache removes most calls anyway?
4. **Cache portability:** per-user-private vs a shared "flow registry" (changes the security model —
   selectors can leak auth-gated structure).
5. **CAPTCHA policy:** sanctioned solver vs always-escalate-to-human (legal/ToS + detection-amplification).

---

## 10. Key sources

- OSWorld-Human — efficiency of computer-use agents (MLSys 2026): https://arxiv.org/html/2506.16042
- Browser Use — "Speed Matters": https://browser-use.com/posts/speed-matters
- Stagehand caching (cache-key recipe, passive validation): https://www.browserbase.com/blog/stagehand-caching
- Skyvern — explore→replay, intent metadata, self-heal: https://www.skyvern.com/blog/asking-ai-to-build-scrapers-should-be-easy-right/
- WALT — Web Agents that Learn Tools: https://arxiv.org/abs/2510.01524
- Agentic Compilation / the "rerun crisis": https://arxiv.org/html/2604.09718v1
- AgentRR — record & replay for LLM agents: https://arxiv.org/html/2505.17716v1
- Agent Workflow Memory (AWM): https://arxiv.org/abs/2409.07429
- "Don't Break the Cache" — prompt caching for agent loops: https://arxiv.org/pdf/2601.06007
- XGrammar — constrained decoding: https://arxiv.org/abs/2411.15100
- "An Illusion of Progress?" — Online-Mind2Web live eval: https://arxiv.org/html/2504.01382v4
- Playwright actionability / best-practices: https://playwright.dev/docs/actionability · https://playwright.dev/docs/best-practices
- Speculative Actions (lossless agent speedup): https://arxiv.org/pdf/2510.04371
- Claude prompt caching docs: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
