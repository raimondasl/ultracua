# ultracua

[![CI](https://github.com/raimondasl/ultracua/actions/workflows/ci.yml/badge.svg)](https://github.com/raimondasl/ultracua/actions/workflows/ci.yml)

A Computer Use Agent (CUA) that **learns a browser flow once with an LLM, then replays it
deterministically** — not by clicking faster, but by taking the planning model out of the loop.
A replayed **write** makes no model call at all (measured: 0 calls on all 24 write replays in the
committed benchmark series); a **data read** makes one, to read the answer off the page (one call on
43 of 51 read replays, two on the other 8). That holds for `replay_flow()` / `flow replay`, which run
`mode="replay"`; the bare `ultracua <url> <goal>` CLI defaults to `--mode auto`, which may self-heal
or re-author and is *not* 0-LLM.

> **Status: active development stopped on 2026-09-09, at 0.181.0.** This is a finished
> experiment, not a maintained product. Everything described below was measured and the measurements
> stand; what stopped is new work. There are **72 open defects**, published in
> [docs/open-defects.md](docs/open-defects.md) along with what each one costs you — reading that
> register is the honest way to decide whether this fits your case. The benchmark numbers quoted here
> are dated observations of the versions named in each baseline's own `meta` and will not be re-cut.
> The method this project was really about — how a solo codebase keeps itself honest when its own
> test suite is not the instrument that finds its bugs — is written up in `CLAUDE.md` and the
> register, and both are worth more than the code.

It sits between two unsatisfying options:

- **Hand-coded scripts** (Playwright / Selenium) — fast and free, but a developer hand-writes every
  selector and they break on UI changes.
- **Per-step LLM agents** (browser-use, computer-use) — handle novelty, but run the model *every
  step, every time*: slow, expensive, non-deterministic, hard to audit.

ultracua's middle path: an LLM **authors the flow once** (no hand-scripting), it **self-heals** minor
UI drift, then **replays the navigation with zero LLM** — fast, cheap, deterministic, auditable.
(A data read still pays one extraction call; only writes and navigate-only reads are 0-LLM end to end.)

## What it's for (and what it isn't yet)

**Good fit** — *repeated* browser automation: scheduled data pulls from authenticated dashboards,
internal tooling, portal extractions where the flow is stable and run often. *"Every morning, log
into the vendor portal and pull yesterday's order count."* Learning a flow costs a handful of model
calls and cents — median **4 calls** and **$0.07** over the 87 measured learns in the committed
benchmark series (range 2–13 calls, $0.03–$0.18). Each later run re-drives the recipe with no
planning model in the loop. For an end-to-end wall-clock figure, run the shipped example: on
2026-09-09 `examples/hn_digest.py` learned in 30 s and replayed in 5 s against a live site — one
flow, one run, so treat it as an illustration rather than a benchmark.

**Not a fit (yet)** — a no-code "do anything" agent; one-off complex analysis (use a per-step LLM
agent for those); anything high-stakes run unsupervised without a human verifying the learned flow.

### What this is now — the scope the measurements actually support

Read this before adopting it. The claims above hold **inside a scope that has been measured on two
real applications**, and the honest boundary is narrower than the feature list below:

- **Supervised authoring, unattended replay.** A human reviews the learned recipe and approves it;
  the approval is bound to a digest of the steps reviewed, so a re-authored recipe refuses rather
  than running. Reads then run unattended. A **declared** write (`spec.mutate` set) sits behind a
  human checkpoint — `flow dry-run` → `flow inspect` → `flow approve`. A write the classifier merely
  *infers* is gated, idempotency-keyed and refused a re-author, but is **not** approval-gated.
- **Measured on:** a server-rendered app (Gitea 1.22) and one client-rendered SPA (Odoo 17 CRM),
  seven scenarios each, three passes each. `availability_rate` **0.762** (Gitea, cut 2026-08-26) and
  **0.714** (Odoo, cut 2026-09-05 from a series run at 0.169.0), each over n=21 scenario-observations.
  **Neither has been re-cut against 0.180.0**, and a later Gitea series measured 0.857 — so read them
  as the level, not the current rate. No silently-wrong outcome is recorded in any committed
  customer-benchmark series (`inviolable: []` throughout); note that `drift_bench`, a different
  instrument, publishes its own non-zero wrong-bind allowlist. The rows that do not pass are named
  and diagnosed in [baselines/README.md](baselines/README.md).
- **Expect first-run engineering on a new application.** Reaching that number on the SPA took ten
  `src/` fixes across ~40 releases (0.133.0 → 0.172.0): render readiness, reads served over POST,
  off-screen controls, an element cap, a scroll that landed on the wrong container. Most are general
  and now help both apps — but a third kind of app should be assumed to need its own.
- **Fails loud, by design:** anti-bot / CAPTCHA interstitials (the run escalates rather than burning
  retries), an ambiguous locator match, a write whose form scope drifted (never LLM-healed), a login
  the built-in form-filler cannot complete, and a WebSocket frame seen during `flow record` or
  `flow dry-run`.
- **Not supported — and these are gaps, not refusals.** 2FA / SSO logins need a user-supplied
  callable and are not first-class. **iframe and shadow-DOM** content is not captured at all (top
  frame only), and a sub-frame write is deliberately excluded from write reconciliation, so an iframe
  write triggered by a recorded action can cache **ungated**. A write issued from a **shared worker**
  is invisible to every request watcher Playwright offers (`R4.154`, open) — it can double-submit at
  learn and cache as a read. Reads served over **GraphQL POST** are still classified as writes
  (`R4.27`, open); through `replay()` that is a loud refusal, but through a `mode="auto"` door it
  quietly falls back to a re-author and you lose the 0-LLM replay.

It's a **usable prototype of a real pattern, not a turnkey product.** The honest status, the measured
benchmark numbers, and the known gaps live in **[STATUS.md](STATUS.md)**.

## Setup

```bash
uv sync --all-groups                 # create the venv + install deps (uv manages Python too)
                                     # NOT bare `uv sync` — it strips the bench/providers/mcp
                                     # groups and the test suite then fails to import
uv run playwright install chromium   # one-time browser download
```

You'll need `ANTHROPIC_API_KEY` (e.g. in a gitignored `.env`) for the one-time *learn* run and the
per-run extraction call.

## Quickstart

Define a recurring task once, learn it, then replay it with 0-LLM navigation — it returns structured data and
**fails loud** on drift instead of returning a wrong value:

```python
import asyncio
from ultracua import FlowSpec, learn_flow, approve_flow, replay_flow

spec = FlowSpec(
    name="daily-orders",
    start_url="https://portal.example.com/admin",
    goal="open the orders report",
    extract="the number of orders placed yesterday",   # → structured data
)
asyncio.run(learn_flow(spec))   # author once; inspect res.steps / res.data
approve_flow(spec)              # a human verifies before trusting it unattended
data = asyncio.run(replay_flow(spec))   # every run after: 0-LLM navigation, returns the data
```

Prefer a real, runnable walkthrough? **[EXAMPLES.md](EXAMPLES.md)** does this end-to-end against
Hacker News (read-only) and is built to record: `uv run python examples/hn_digest.py --headed`.

## Highlights

- **0-LLM navigation** — through `replay_flow()` / `flow replay` (`mode="replay"`) a learned flow re-drives every step with no model call; this is pinned key-lessly over the whole drift corpus by `tests/test_inviolable_properties.py`. A write is then 0-LLM end to end (0 calls on all 24 write replays in the committed series); a *data* read adds one extraction call to read the answer — measured **1 call on 43 of 51 read replays, 2 on the other 8**, where an auth-refresh retry re-extracts. `pin_read` would remove even that, but it only fires for a scalar answer that maps to exactly one element carrying an `id`/`data-testid`: measured pinnable on **0 of the corpus's 10 read scenarios**, so budget one call per read.
- **Resilient, self-healing locators** — survive cosmetic DOM drift; one-step LLM re-grounding on real drift, or a **suffix-replan** that re-authors just the broken tail (keeping the working prefix) when the path changes. **Measured**, key-lessly and in CI on a 187-row corpus: 0-LLM survival degrades with mutation intensity and reaches zero — 20/27 at k=1, 0/6 at k=7, non-monotonic in between (k50 = 6) — and the heal machinery recovers **36 of 39** heal-eligible rows *given* correct element identity ([drift-bench v2](baselines/README.md) — read the limits; that heal figure is a mechanism ceiling measured against a perfect-vision oracle, not a heal rate; the committed `drift_v2.json` was two releases stale when this line first said so and was re-recorded at 0.181.0, R4.159). Two wrong-bind classes are **published rather than hidden**, each pinned by its own corpus row.
- **Trust controls** — an approval gate **bound to the steps you reviewed** (re-author them and replay refuses, before the browser opens), data-shape + value-contract drift detection, **fail-loud** `FlowReplayError`.
- **Auth refresh** — re-login on session expiry; credentials are env-sourced and **never persisted**.
- **Write flows** — submit / post / purchase with **action-completion verification** + idempotency.
- **Dry run** — `flow dry-run` replays a write flow with **every write held**: see the exact body, URL and idempotency key it *would* send before you approve it. Nothing reaches the server, and anything that can't be *proven* held aborts rather than proceeding.
- **Record by demonstration** — `ultracua flow record` captures a headed walkthrough into a cached **0-LLM** flow: reads are verify-by-replay; declared writes are **gated + approval-gated + idempotency-keyed**.
- **Fleet supervisor** — `flow run-all` replays every saved flow, reports pass/fail, alerts, exits non-zero for cron — including when a flow was refused a run by something no human chose, or when nothing ran at all; `flow status` for history.
- **Multi-provider** — Anthropic / OpenAI / Gemini, fast/strong tiering, prompt caching.
- **Drive from any language** — JSON-RPC daemon + a Node/JS client.

## Documentation

| Doc | For |
|---|---|
| **[EXAMPLES.md](EXAMPLES.md)** | a worked, runnable real-site example — **start here** |
| **[GUIDE.md](GUIDE.md)** | developer guide: the Flow API + CLI in depth (auth, write flows, record by demonstration, health, providers) |
| **[HEALING.md](HEALING.md)** | how it self-heals (and deliberately doesn't) when a page's elements change: resilient locators, LLM heal/re-plan, and the fail-loud boundaries |
| **[docs/comparison-stagehand.md](docs/comparison-stagehand.md)** | ultracua vs. Stagehand — a design-philosophy comparison (drift/self-heal, write safety, data correctness), dated + sourced |
| **[docs/open-defects.md](docs/open-defects.md)** | the standing defect register — **four** adversarial rounds. Rounds 1–2 are fixed; of the 13 R3-numbered entries (round 3 found 11, R3.12–R3.13 were filed later), **2 remain open** (R3.2, R3.7); round 4 began as a pre-merge audit that *parked* a change rather than ship it and became a standing per-slice log — **159 findings, 72 open / 83 fixed / 4 parked**. Records the residuals it decided not to fix, including the decisions closed as NO CHANGE. Its R4 index is rendered from `docs/register/state.json` and its R3 count is parsed from the headings — both machine-checked; **this table row is prose and is not**, which is why it said "nine remain open" for five weeks after that stopped being true. Check the register's own index, not this row. **Read before starting new work.** |
| **[docs/correctness-plan.md](docs/correctness-plan.md)** | the plan behind Phases 0–7, grounded on the v0.75.0 survey (round-3 findings, test-machinery holes, unpinned residuals) — worst user harm first, test-first, one slice per PR. It does **not** sequence the round-4 register: most of the 72 open findings post-date it, and `docs/reshape-plan.md` §13 plus `docs/plan/state.json` are the operative order. |
| **[docs/correctness-survey.md](docs/correctness-survey.md)** | the measured inventory the plan is built on: **59 items** across the register, CI/eval machinery, the user-facing surface and the accepted residuals in `src/`, produced 2026-08-04 at v0.75.0 and not re-derived since. |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | how it works inside + how to contribute (engine, safety, tiers, benchmarks, layout) |
| **[STATUS.md](STATUS.md)** | honest status, measured benchmarks, known fragilities |
| **[ROADMAP.md](ROADMAP.md)** | what's next |
| **[evals/README.md](evals/README.md)** | the manual capability-eval suite: shipped behavior + the H1–H16 horizons, with $ cost estimates and partial runs |
| **[PLAN.md](PLAN.md)** | the original design + research basis |
| **[docs/recorder-spike.md](docs/recorder-spike.md)** | the record-by-demonstration design (capture → gated cache → 0-LLM replay) |

## License

[MIT](LICENSE) © Raimondas Lencevicius
