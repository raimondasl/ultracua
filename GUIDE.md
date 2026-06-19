# Developer guide

How to *use* ultracua — the Flow API and the `ultracua` CLI in depth. For a runnable real-site
walkthrough start with [EXAMPLES.md](EXAMPLES.md); for how it works inside see
[ARCHITECTURE.md](ARCHITECTURE.md).

## Contents

- [The one-shot agent](#the-one-shot-agent)
- [Recurring flows — the Flow API](#recurring-flows--the-flow-api)
- [Pinned 0-LLM reads](#pinned-0-llm-reads)
- [Discovery reliability](#discovery-reliability)
- [Trust for unattended runs](#trust-for-unattended-runs)
- [Auth refresh](#auth-refresh)
- [Write flows (submit / post / purchase)](#write-flows-submit--post--purchase)
- [Run a fleet](#run-a-fleet)
- [Fleet health](#fleet-health)
- [Providers & tiering](#providers--tiering)

## The one-shot agent

The lowest-level entry point runs a single goal through the flow cache: the **first** run on a
`(goal, url)` LEARNS and caches the flow; **subsequent** runs REPLAY it with no LLM.

```bash
# First run LEARNS + caches (needs ANTHROPIC_API_KEY); second run REPLAYS with no LLM.
# PowerShell: $env:ANTHROPIC_API_KEY = "sk-ant-..."
uv run ultracua --url https://example.com --goal "open the more information link"
uv run ultracua --url https://example.com --goal "open the more information link"   # replays
```

Flags: `--mode auto|learn|replay`, `--fresh` (clear the cached flow first),
`--provider anthropic|openai|gemini|mock`, `--tier fast|strong`, `--scope <name>`. Learned flows
live under `.ultracua/flows/`. Env: `ULTRACUA_FAST_MODEL` (default `claude-haiku-4-5`),
`ULTRACUA_MODEL` (strong, default `claude-opus-4-8`), `ULTRACUA_TIER` (default `fast`).

For *recurring* tasks, use the Flow API below — it adds named specs, structured extraction,
approval, drift handling, and health.

## Recurring flows — the Flow API

Define a recurring task once as a **`FlowSpec`**, **learn** it (LLM-authored, inspectable), then
**replay** it — 0-LLM navigation that **returns the extracted data and raises on drift** instead of
returning wrong data.

```python
import asyncio
from ultracua import FlowSpec, learn_flow, replay_flow, FlowReplayError

spec = FlowSpec(
    name="daily-orders",
    start_url="https://portal.example.com/admin",
    goal="open the orders report",
    extract="the number of orders placed yesterday",   # → structured data
    headers={"X-Auth": "…"},                            # or storage_state="state.json"
)

# Author once and eyeball what was learned:
res = asyncio.run(learn_flow(spec))      # res.steps, res.data, res.cached

# Then run it cheaply + deterministically (e.g. from cron); raises on drift:
try:
    data = asyncio.run(replay_flow(spec))   # 0-LLM navigation, returns the data
except FlowReplayError as e:
    ...  # site changed / data missing — alert instead of trusting a wrong value
```

Or from the CLI (saves the spec under `.ultracua/specs/`):

```bash
uv run ultracua flow learn  --name daily-orders --url <url> --goal "open the orders report" \
                            --extract "the number of orders placed yesterday" --header "X-Auth=…"
uv run ultracua flow replay --name daily-orders      # prints the data as JSON; exits 1 on drift
uv run ultracua flow inspect --name daily-orders     # spec + learned steps
uv run ultracua flow list
```

`auth` is `headers=` or `storage_state=` (a Playwright cookies JSON); `extract` is a natural-language
instruction (+ optional `extract_schema` for validated structure). Replay does 0-LLM **navigation**;
reading the answer is one cheap extraction call (set `extract=None` for navigate-only flows).

## Pinned 0-LLM reads

By default a data flow's replay does 0-LLM *navigation* but still makes **one** LLM extraction call to
read the answer. For a **scalar** answer that sits in an element with a stable `id` or `data-test-id`
(common in dashboards and internal tools), add `pin_read=True`: at learn time ultracua pins a locator
to that element, and replay reads it **deterministically — 0 LLM, no API key, typically sub-second**
(and one fewer paid call, every run).

```python
spec = FlowSpec(name="latest-version", start_url=…, goal="open the release page",
                extract="the latest version number", pin_read=True)
res = asyncio.run(learn_flow(spec))   # res.pinned is True iff a 0-LLM read was pinned
approve_flow(spec)
v = asyncio.run(replay_flow(spec))    # reads the live value with no model call
```

CLI: `ultracua flow learn --pin-read …` (it prints whether the pin was recorded).

It's **best-effort + safe**:

- A pin is recorded only when the value maps to **exactly one** element with a stable **`id` or
  `data-test-id`** (verified by reading it back). A purely positional anchor is **refused** — a layout
  shift could resolve it to the wrong element. If it can't pin (no stable anchor, the value is buried
  in prose, ambiguous, or not a scalar), the flow silently keeps using the LLM extractor; check
  `res.pinned` (or the CLI output) to see which path you got.
- The pin anchors on the element's **id / test-id**, never the value, so it returns *today's* value
  on each replay.
- Reads are **strict**: the live text must be one clean scalar of the recorded type. If the element
  is gone, the locator is ambiguous, or the text no longer parses cleanly (a second number appears,
  scientific notation, a range), replay **fails loud** (`FlowReplayError`) — it never returns a wrong
  value — and you re-learn.
- Structured (dict / list) answers aren't pinned yet — that's a follow-up.

## Discovery reliability

Discovery (the learn run) is the reliability bottleneck — the LLM sometimes fails to author a working
flow on a flaky/ambiguous page. `learn(spec, samples=N)` (CLI `flow learn --samples N`) re-authors up
to N times and keeps the first attempt the verifier confirms, trading LLM cost for a higher first-try
success rate.

## Trust for unattended runs

`replay(require_approved=True)` refuses any flow you haven't `approve_flow(spec)`d; replay also treats
a change in the data's *shape* vs the learned run as drift; and `on_drift="relearn"` recovers from
drift instead of raising. So a scheduled run either returns trustworthy data or fails loudly — point
cron at it and alert on a non-zero exit. (CLI: `ultracua flow approve --name …`; `flow replay
--require-approved --on-drift relearn`.)

`on_drift="relearn"` recovers in the cheapest way that works, escalating only as needed: a pure 0-LLM
replay first; then a **suffix-replan repair** that re-authors *only the broken tail* from the current
page while keeping the working prefix (so a locator/navigation change fixes itself without re-running
the whole flow, and re-caches); and finally a full re-author from scratch (which also handles a change
in the data's *shape*, since the steps still replay in that case). Write flows refuse `relearn`
entirely — re-driving a write under uncertainty could double-submit, so they fail loud for a human to
re-learn and re-approve.

## Auth refresh

For cookie sessions that expire, add `login=LoginSpec(url=…, username_env=…, password_env=…)` —
credentials are read from the env at runtime and **never persisted** (the login isn't cached; only the
resulting `storage_state` cookies are saved). On drift, replay re-logs-in and retries once, so a
long-lived recurring flow survives session expiry.

- `success_selector=` / `success_url_contains=` — for SPA logins that stay on the same URL (the
  default check is "navigated off the login page").
- `timeout_ms=` — bound the login form actions.
- `login=` may also be an `async (page) -> None` callable for non-standard / SSO logins.

From the CLI, attach a login to a saved flow with `ultracua flow set-login --name … --login-url …
--storage-state …`, then refresh cookies now with `ultracua flow login --name …` (it verifies the
login and reports success/failure).

## Write flows (submit / post / purchase)

Set `mutate=MutateSpec(…)` to make a flow a *write* flow. Because a click that doesn't throw isn't
proof a write landed, a write flow **must declare how it's confirmed** — `confirm_selector` /
`confirm_text_contains` / `confirm_url_contains` (mirrors `LoginSpec`'s success check). After replay
runs, that condition must hold or it **fails loud** (`FlowReplayError`).

```python
from ultracua import FlowSpec, MutateSpec, learn_flow, approve_flow, replay_flow

spec = FlowSpec(name="daily-order", start_url=…, goal="place the standing order",
                mutate=MutateSpec(confirm_text_contains="Order placed"))
asyncio.run(learn_flow(spec))   # performs the write once; inspect the steps
approve_flow(spec)              # a human verifies before unattended runs (writes are approval-gated)
res = asyncio.run(replay_flow(spec))   # {"status": "confirmed", "data": None}, or raises if unconfirmed
```

Write semantics:

- Every write replay returns a uniform `{"status": "confirmed" | "already-done", "data": <None unless
  extract is set>}`.
- Write flows are **approval-gated by default**, refuse `on_drift="relearn"` (re-authoring would
  re-perform the write), and a mutating step under page drift fails loud rather than letting an LLM
  re-drive it.
- **Idempotency (one-shot writes):** add `precheck_*` (a cheap read-only pre-pass) — if the end-state
  already holds, the write is **skipped** (`{"status": "already-done"}`); don't purchase twice. Leave
  `precheck_*` unset for *recurring* writes (placing today's order daily) so a legitimately-recurring
  state isn't skipped.

CLI: `ultracua flow learn --confirm-text-contains "Order placed" …`, or `flow set-mutate --name …
--confirm-*`.

## Run a fleet

Once you have several saved flows, **`ultracua flow run-all`** is the supervisor: it replays every
saved flow once (concurrently), prints a consolidated report, and **exits non-zero if any flow
failed** — so you point cron / Task Scheduler at it and alert on the exit code.

```bash
uv run ultracua flow run-all                      # read + approved flows only (safe default)
uv run ultracua flow run-all --json fleet.json    # also write a machine-readable run record
uv run ultracua flow run-all --alert-webhook https://hooks.slack.com/…   # POST on any failure
```

```
  [FAIL] vendor-status         3.1s  'vendor-status': replay failed (page drift?): …
  [OK]   daily-orders          0.2s  1284
  [OK]   latest-version        1.9s  "2.31.0"
  [SKIP] place-order                 write flow (use --include-writes)
  [SKIP] draft-flow                  not approved

== 2 ok, 1 failed, 2 skipped (of 5) ==
```

Safe defaults for unattended use: **read flows only** (write flows are skipped unless
`--include-writes`, since a blanket run shouldn't fire purchases) and **approved flows only**
(`--include-unapproved` to override). `--concurrency N` caps how many run at once (each uses its own
browser); `--on-drift relearn` re-authors read flows that drifted. The same API is `run_all_flows()`
in Python, returning a `FleetRun` per flow.

## Fleet health

Every `replay` (including via `run-all`) records its outcome, so you can also monitor a fleet's
*history*: `flow_health(spec)` (CLI `ultracua flow status`) reports each flow as `healthy` /
`failing` / `stale` / `never-run` with run counts and the last error (`flow status --stale-after
<hours>` flags a flow whose last success is too old). No scheduler is built in, by design — cron +
`flow run-all` is the pattern.

Add `--verbose` to `flow learn` / `flow replay` / `flow run-all` (and the example script) to log each
run with a `run_id` and its token usage + `$` cost.

## Providers & tiering

The agent reaches LLMs through a provider-neutral layer with native Anthropic / OpenAI / Gemini
adapters. A **fast tier** (Haiku 4.5) drives routine element selection and **escalates** to a
**strong tier** (Opus 4.8 / Sonnet 4.6) when unsure.

```bash
ULTRACUA_LLM_BACKEND=anthropic ULTRACUA_TIER=fast \
  uv run ultracua --url https://example.com --goal "..."
```

For the OpenAI / Gemini backends, install their SDKs (`uv sync --group providers`) and set the
relevant key (`OPENAI_API_KEY` / `GEMINI_API_KEY`). Resilience knobs: `ULTRACUA_LLM_MAX_RETRIES`
(default 3), `ULTRACUA_LLM_TIMEOUT_S` (default 60). The design of this layer is in
[ARCHITECTURE.md](ARCHITECTURE.md#multi-provider-llm-layer).
