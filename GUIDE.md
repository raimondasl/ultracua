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
`ULTRACUA_MODEL` (strong, default `claude-opus-4-8`), `ULTRACUA_TIER` (default `strong`).
`ULTRACUA_WINDOW_SIZE` (e.g. `1600x1000`) sizes the browser window — headed opens the OS window that
size and the page fills it, headless renders at that size; unset uses Playwright's default 1280×720.
(Programmatically: `BrowserSession(window_size=(1600, 1000))`.)

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

### Record a flow by demonstration (Phase I)

When the LLM can't reliably *author* a flow — the grounding-hard tasks where it picks the wrong element
(measured: a recorder cracks **9/9** such MiniWoB tasks vs LLM authoring **4/9** on the same seeds) — you
can **demonstrate** it instead. `flow record` opens a headed browser (size it with
`ULTRACUA_WINDOW_SIZE`, e.g. `1600x1000`); you click through the task, press
Enter, and it captures your interactions into the **same cached flow** the engine replays — then **verify-
by-replays** it (cached only if it reproduces 0-LLM) so it's trustworthy from the start.

It captures **clicks** (incl. checkboxes/radios), **typing**, **dropdown** choices (single & multi-select),
**Enter-to-submit** on a text field, and **scrolling**, and follows you **across same-origin page
navigations** without dropping a step (the demonstration is buffered in-page and drained as you go). *If the
demo crosses a **site/origin boundary** (e.g. an SSO or external-checkout redirect), recording **fails loud**
and is not cached — record the cross-origin portion as a separate same-origin flow. Capture runs in the
top frame only; iframe/shadow-DOM interactions aren't captured yet.*

```bash
uv run ultracua flow record --name pick-items --url <url> --goal "select the right items"
# → a browser opens; do the task; press Enter; it verifies + caches.
uv run ultracua flow approve --name pick-items     # then it runs unattended like any learned flow
uv run ultracua flow replay  --name pick-items
```

After capture, each recorded step's intent is auto-labeled by a best-effort post-hoc LLM caption
(improves self-heal hints + the `flow inspect` view); replay stays 0-LLM, and the captioning is
skipped if no API key is set.

**Read flows** verify-by-replay: cached only if their **navigation** reproduces 0-LLM on a fresh session
(you confirm it did the *right* thing by watching your own demo).

**Write flows** are captured **safely** when you **declare** the write up front with a confirm check
(`--confirm-text-contains` / `--confirm-selector` / `--confirm-url-contains` — the recorder can't infer the
action-completion signal). The demonstrated submit is recorded as a **gated** step (its enclosing-form
precondition captured at record time), so on replay the **mutation gate refuses it under form/section
drift** (fail loud, never a blind re-fire), it carries an **Idempotency-Key**, and it is **approval-gated**
— exactly like a *learned* write. A write is **not** verify-by-replayed (re-firing would double-submit);
approval is the human verification.

```bash
uv run ultracua flow record --name place-order --url <url> --goal "place the order" \
    --confirm-text-contains "Order placed"
uv run ultracua flow approve --name place-order    # verify your demo, then approve
```

A write demonstrated **without** a declared confirm check is **refused** with guidance to re-record. The
recorder trusts HTTP method semantics, so a write *behind a GET* link or via `sendBeacon` isn't
auto-detected — **declare those as writes** (`--confirm-*`) and they're captured gated + approval-gated all
the same; don't rely on auto-detection for them. The Python API is `record(spec, demo=…)` (set
`spec.mutate` for a write), returning a `RecordResult` (`is_write` flags a write flow).

### Serve flows to any AI assistant (MCP)

Expose your **approved read flows** as tools to any MCP client — Claude, Cursor, VS Code, ChatGPT — so
the assistant makes **one deterministic, verified tool call** instead of driving a browser step by step:

```bash
uv sync --group mcp                 # one-time: install the optional MCP SDK
uv run ultracua flow serve-mcp      # stdio MCP server; wire this command into your client's mcpServers
```

Each approved read flow becomes a **zero-argument tool** whose call dispatches to the safety-gated
`replay()` (`require_approved=True`, `on_drift="raise"`, `check_shape=True`) — never the raw engine. So a
tool call either returns today's verified data or **fails loud** with a typed error the assistant can act
on (`DriftError` / `ShapeDriftError` / `AuthExpiredError` / `EscalateError`, each carrying a
machine-readable `code` + `retryable` flag). **Write flows are default-deny** (opt in with
`--expose-writes`, below), and `learn` / `approve` / `record` are never tools — a calling assistant can't
author or self-approve a flow.

An approved read flow that has **typed slots** (see below) is exposed as a **parameterized** tool: its MCP
`inputSchema` is built from the flow's slots (type / `enum` / `pattern` / range / `required`), and the
assistant's arguments are validated against that closed domain by the *same* `validate_params` the flow uses
— a bad argument comes back as a typed **`invalid_params`** (fix the args, don't retry) *before any browser
opens*, never a wrong-but-plausible result. **Secret** slots stay `$env`-resolved and are never tool
arguments (the tool description notes which env vars it reads). A flow with no slots stays a zero-argument
tool. The Python entrypoint is `await flows.serve_mcp()`.

#### Exposing write flows (opt-in, H2 stage 2)

Writes are **default-deny**. Add `--expose-writes` to also serve approved **declared** write flows —
each becomes a tool annotated **destructive** and prefixed `[WRITE — …]`:

```bash
uv run ultracua flow serve-mcp --expose-writes   # reads + approved declared writes; each write needs a confirm
```

A write is exposed **only** if it is a *declared* write (`spec.mutate` with a confirm barrier); an
**undeclared** write (mutating steps but `spec.mutate is None`) is *never* exposed on any surface — its
writes can't be verified, so replay would fire them unconfirmed. Every write call runs the **write rail**,
all inside a per-flow single-flight mutex:

1. **pre-flight** (0-LLM, no browser) — validate the args, then compute the write's Idempotency-Key(s);
2. **retry-dedupe** — if this *exact* write (same args → same key) already committed on a prior call, return
   `already_done` and **never re-fire** (a client-timeout retry can't double-write);
3. **elicit a human confirm** — the client is asked to confirm before anything fires; **no elicitation
   capability, a decline, or a transport error all mean refuse — a write never fires unconfirmed**;
4. **fire** via the safety-gated `replay()`, which verifies the write *landed* via the declared confirm
   barrier (a write is never verify-by-replayed — that would double-submit);
5. **record** to the durable resume ledger **strictly after** the write confirms.

The dedupe is **at-most-once per argument set**: an outer agent can't assert "this is a genuine repeat, not a
retry", so a re-run of identical args is refused (`already_done`) rather than risk a double-fire — a real
recurring write must vary a disambiguating slot. Secret slots stay `$env`-resolved and never appear in a tool
argument, the confirm preview, or the ledger. **Caveat:** until the Phase-I auth daemon lands, a calling
assistant rides the **operator's identity** (a confused-deputy gap) — expose writes only to a client you
trust; the CLI prints this loudly. HTTP transport stays stdio-only for now.

### Parameterized replay — typed slots (H3, read flows)

A recorded flow's typed/selected values can be turned into **typed slots** so one flow runs with
different per-run inputs, instead of recording it once per value:

```python
from ultracua import flows, FlowSpec, SlotSpec

spec = FlowSpec(name="daily-search", start_url="https://…", goal="search the catalog",
                slots={"query": SlotSpec(type="string", max_length=64),
                       "region": SlotSpec(type="string", enum=["us", "eu", "apac"])})
# (mark which cached steps a slot fills, then:)
await flows.replay(spec, params={"query": "blue widget", "region": "eu"})
```

Each `params` value is **validated 0-LLM before the browser opens** (type, `enum`, `pattern`, `min`/`max`,
`max_length`, `required`); an out-of-domain value **fails loud** — it never types a wrong value onto the
page. Values are substituted at the flow's slot-marked fill/select steps; `flow_key` is unchanged, so
values never enter the flow's identity, and a **`replay()` with no `params` replays the frozen literals
unchanged**. A `secret=True` slot resolves from its `secret_env` environment variable (never passed in
`params`, never serialized). Parameterizing a **WRITE** flow works too — see *Write templates* below.

**Auto-mining the slots.** Instead of declaring `FlowSpec.slots` by hand, let the recorder lift them:

```bash
uv run ultracua flow record --name daily-search --url … --goal "search the catalog" --mine-slots
```

`--mine-slots` (library: `record(spec, demo, mine_slots=True)`) turns each typed/selected value into a
**typed** slot named from the field, capturing its legal **domain** from the page: a `<select>`'s options
become a closed `enum`, an input's `pattern`/`maxlength`/`required` carry over, a numeric range becomes a
`number` slot — so a later `replay(params=…)` is validated against the real site domain. It also runs a
**value-independence audit**: if a demo value **echoes into a later step's locator, precondition, or
navigate URL** — which would make any other value break replay (a dead template) — recording **fails loud**
and reports the offending slot (`RecordResult.slot_findings`) rather than shipping a template that only
works for the demo value. Mining is opt-in and read-only, so a normal record is unaffected.

**Write templates.** A `params={…}` replay of a **WRITE** flow (one whose `spec.mutate` is set) runs each
row through the one learned form-submit, substituting the validated value at the write's fill/select steps.
The load-bearing safety artifact is the per-write **`Idempotency-Key`** header: the write actuation gate
folds the run's slot values into it, so **distinct rows mint distinct keys** (a backend dedupe can't
silently drop rows 2..N — the *suppressed-write* risk) and a **retry of the same row mints the same key**
(the backend dedupes it instead of writing twice — the *double-write* risk). A `params=None` replay keeps
the pre-2a key byte-identical, so an existing frozen single-write flow is unchanged.

Because a write is the highest-trust action, it carries extra gates. Auto-mining **never** lifts a write
field (a silently-parameterized payee/amount is a money-moving injection surface); instead you give **explicit
sign-off** by NAMING the fields to parameterize — `record(spec, demo, writable_slots={"amount"})` (CLI:
`flow record --writable-slots amount,qty`). Each name binds its **one** demonstrated field; a name matching
zero or more-than-one field is **refused** (so a money field is never bound to the wrong step), the
value-independence audit runs on it (a demo value echoing into a later locator → refuse), a pre-declared
typed `SlotSpec` (enum/pattern/range) is used when present, a secret field's plaintext is scrubbed from the
cache, and any field you *don't* name stays **frozen**. The approval is **bound to the slot schema**: if you
widen a slot's domain after
`approve()` (e.g. loosen a `pattern` to any string), replay **refuses until you re-approve** — a stale
approval must never authorize a wider contract than you reviewed. And every write still passes the
**mutation gate** (value-independent: a changed input value never shifts the form fingerprint, but page
*drift* refuses to re-drive), the **confirm barrier** (the completion signal must appear, or the flow fails
loud), and the 0-LLM pre-flight; a write is **never** verify-by-replayed (re-firing = double-submit).

**Running a batch of rows.** `run_batch(spec, rows)` drives one parameterized replay per row — "record once,
run for N rows" — and on the CLI, `ultracua flow run-batch --name <flow> --rows rows.csv` (JSON array or CSV):

```python
from ultracua import flows
report = await flows.run_batch(spec, [{"qty": "9"}, {"qty": "8"}], max_rows=100)
# report.rows[i] -> BatchRowResult(index, status, idempotency_keys, error, …)
```

Every row is **validated 0-LLM before any browser opens** (the same `_preflight_row` gate `replay` uses); if
**any** row is out-of-domain the whole batch is refused with **zero** writes (no half-run on malformed input).
Two rows that would mint the **same** `Idempotency-Key` are refused (a backend dedupe would silently drop the
second — add a disambiguating slot). A **write** batch requires **`max_rows`** (one approval must not authorize
unbounded writes). Rows run **sequentially**; `on_row_error="stop"` (default) halts on the first failure and
marks the rest `skipped`, `"continue"` runs and reports each. **`dry_run=True`** (the CLI default, until you
pass `--commit`) validates + previews every row's Idempotency-Key and **actuates nothing** — review the plan
before committing writes. Rows carry no secrets (those resolve from `$env`); the report stores only indices +
hashed keys.

**Resuming a batch that died partway.** Pass **`resume="<job-id>"`** (CLI: `--resume <id>`) to key a durable
per-row ledger: a re-run under the **same** job-id **skips** the rows that already committed (`status="resumed"`)
instead of re-firing their writes — so a batch that failed at row 300 of 500 finishes rows 300.. rather than
re-writing 1..299. A **fresh** job-id is an independent run (a legitimate recurrence — the token, not the
run-invariant key, states your intent). The Idempotency-Key stays the safety floor: a row lost to a crash
between the write landing and the ledger write re-fires with the *same* key and the backend dedupes it. The
CLI auto-mints + prints a job-id on the first `--commit` write batch, so even an unplanned crash is resumable.
(Per-write resume *within* one multi-write flow stays deferred — a stateless probe can't attribute page-state
to a specific write; such a row re-fires all its writes on resume, each key-deduped.)

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

### Multi-write transactions (Phase G)

A flow that performs **several writes** (a multi-page application, a multi-item order, approving N pending
items) declares a **per-write completion barrier** for each, in **commit order**, via
`MutateSpec.step_confirms`. Replay verifies each write *the moment it actuates* — as an **absent→present
transition** — and **fails loud, without proceeding to the next write**, if one can't be confirmed (so a later
write never fires after an earlier one silently failed). `confirm_*` stays the whole-flow/overall check.

```python
from ultracua import FlowSpec, MutateSpec
from ultracua.cache import StepConfirm

spec = FlowSpec(name="checkout", start_url=…, goal="add the item then place the order",
    mutate=MutateSpec(
        confirm_text_contains="Order placed",                 # overall / last-write signal
        step_confirms=[                                        # one per write, in commit order
            StepConfirm(confirm_text_contains="Added to cart", expects_intent="Add to cart"),
            StepConfirm(confirm_text_contains="Order placed",  expects_intent="Place order"),
        ]))
```

- **Authoring:** **record** a multi-write flow (`record(spec, demo=…)` with `spec.mutate.step_confirms`) — the
  recorder's per-write attribution gives each write a real, gated commit. The LLM-learn path refuses multi-write
  barriers (its keyword classifier can miss a write); learn the reads, record the writes.
- **Binding:** each `StepConfirm` attaches to the Nth gated write in commit order, **count-checked**, and for a
  multi-write flow `expects_intent` (a substring of that write's button label / intent) is **required** to anchor
  each confirm to its write. A count mismatch, a missing/duplicate anchor, or an `expects_intent` that doesn't
  match its write **refuses to cache** — never a half- or mis-confirmed write flow. The CLI prints the
  confirm bound to each write; review it before `approve`.
- **Distinct confirms (important):** give each write a confirm **unique to its outcome** (prefer a write-specific
  `confirm_selector` / `confirm_url_contains` over shared text). The barrier requires an absent→present
  transition, so a confirm that's already true before the write (a leftover "Saved" banner from the previous
  write) **fails loud** rather than waving the write through.
- **Re-runs:** a multi-write flow's writes are treated as **recurring** — a re-run re-fires them (and a
  multi-write flow is **not** auto-retried after auth-refresh, to avoid re-firing an already-landed earlier
  write). Per-write one-shot **resume** (skip a write that already landed) and declarative
  **compensation/rollback** + **dynamic-N** ("approve however many items exist today") are deferred to later
  Phase-G PRs; the recorder `--confirm-*` CLI flags too (use the Python API for now).

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

**Catch rot early with `flow canary`.** `run-all` actually replays everything (and performs reads);
`flow canary` is a cheap, **read-only** freshness probe — it just navigates to each flow's start URL
and checks the first cached control still resolves, with **no actions, no writes, no health record**.
Point cron at it *more often* than `run-all` so a redesigned landing/login page is flagged the day it
changes, not when the nightly run fails. It exits non-zero if any flow is stale:

```bash
uv run ultracua flow canary            # probe every saved flow's entry point (0-LLM, read-only)
uv run ultracua flow canary --name daily-orders
```

```
  [STALE] vendor-status         entry control no longer resolves: 'open the status page'
  [FRESH] daily-orders
  [NEW]   draft-flow            learn the flow first

== 1 fresh, 1 stale/error (of 3) ==
```

It's intentionally shallow (entry step only — mid-flow drift is still caught by the full `run-all`).
The Python API is `canary(spec)` / `canary_all()`, returning a `CanaryResult` per flow.

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
adapters. The **strong tier** (Opus 4.8 / Sonnet 4.6) is the default driver; opt into a cheaper
**fast tier** (Haiku 4.5) with `ULTRACUA_TIER=fast` to drive routine element selection, which then
**escalates** to the strong tier when unsure.

```bash
ULTRACUA_LLM_BACKEND=anthropic ULTRACUA_TIER=fast \
  uv run ultracua --url https://example.com --goal "..."
```

For the OpenAI / Gemini backends, install their SDKs (`uv sync --group providers`) and set the
relevant key (`OPENAI_API_KEY` / `GEMINI_API_KEY`). Resilience knobs: `ULTRACUA_LLM_MAX_RETRIES`
(default 3), `ULTRACUA_LLM_TIMEOUT_S` (default 60). The design of this layer is in
[ARCHITECTURE.md](ARCHITECTURE.md#multi-provider-llm-layer).
