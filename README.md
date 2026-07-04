# ultracua

[![CI](https://github.com/raimondasl/ultracua/actions/workflows/ci.yml/badge.svg)](https://github.com/raimondasl/ultracua/actions/workflows/ci.yml)

A Computer Use Agent (CUA) that drives a web browser at **5–10× human speed** — by **learning a
flow once with an LLM and replaying it deterministically at 0-LLM**, not by clicking faster.

It sits between two unsatisfying options:

- **Hand-coded scripts** (Playwright / Selenium) — fast and free, but a developer hand-writes every
  selector and they break on UI changes.
- **Per-step LLM agents** (browser-use, computer-use) — handle novelty, but run the model *every
  step, every time*: slow, expensive, non-deterministic, hard to audit.

ultracua's middle path: an LLM **authors the flow once** (no hand-scripting), it **self-heals** minor
UI drift, then **replays with zero LLM** — fast, cheap, deterministic, auditable.

## What it's for (and what it isn't yet)

**Good fit** — *repeated* browser automation: scheduled data pulls from authenticated dashboards,
internal tooling, portal extractions where the flow is stable and run often. *"Every morning, log
into the vendor portal and pull yesterday's order count."* Learn once (~10–30 s, a few LLM calls);
each later run is seconds at **$0 in LLM** and reproducible.

**Not a fit (yet)** — a no-code "do anything" agent; one-off complex analysis (use a per-step LLM
agent for those); anything high-stakes run unsupervised without a human verifying the learned flow.

It's a **usable prototype of a real pattern, not a turnkey product.** The honest status, the measured
benchmark numbers, and the known gaps live in **[STATUS.md](STATUS.md)**.

## Setup

```bash
uv sync                              # create the venv + install deps (uv manages Python too)
uv run playwright install chromium   # one-time browser download
```

You'll need `ANTHROPIC_API_KEY` (e.g. in a gitignored `.env`) for the one-time *learn* run and the
per-run extraction call.

## Quickstart

Define a recurring task once, learn it, then replay it at 0-LLM — it returns structured data and
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

- **0-LLM replay** — a learned flow replays with no model calls (one cheap extraction reads the data).
- **Resilient, self-healing locators** — survive cosmetic DOM drift; one-step LLM re-grounding on real drift, or a **suffix-replan** that re-authors just the broken tail (keeping the working prefix) when the path changes.
- **Trust controls** — approval gate, data-shape drift detection, **fail-loud** `FlowReplayError`.
- **Auth refresh** — re-login on session expiry; credentials are env-sourced and **never persisted**.
- **Write flows** — submit / post / purchase with **action-completion verification** + idempotency.
- **Record by demonstration** — `ultracua flow record` captures a headed walkthrough into a cached **0-LLM** flow: reads are verify-by-replay; declared writes are **gated + approval-gated + idempotency-keyed**.
- **Fleet supervisor** — `flow run-all` replays every saved flow, reports pass/fail, alerts, exits non-zero for cron; `flow status` for history.
- **Multi-provider** — Anthropic / OpenAI / Gemini, fast/strong tiering, prompt caching.
- **Drive from any language** — JSON-RPC daemon + a Node/JS client.

## Documentation

| Doc | For |
|---|---|
| **[EXAMPLES.md](EXAMPLES.md)** | a worked, runnable real-site example — **start here** |
| **[GUIDE.md](GUIDE.md)** | developer guide: the Flow API + CLI in depth (auth, write flows, record by demonstration, health, providers) |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | how it works inside + how to contribute (engine, safety, tiers, benchmarks, layout) |
| **[STATUS.md](STATUS.md)** | honest status, measured benchmarks, known fragilities |
| **[ROADMAP.md](ROADMAP.md)** | what's next |
| **[evals/README.md](evals/README.md)** | the manual capability-eval suite: shipped behavior + the H1–H16 horizons, with $ cost estimates and partial runs |
| **[PLAN.md](PLAN.md)** | the original design + research basis |
| **[docs/recorder-spike.md](docs/recorder-spike.md)** | the record-by-demonstration design (capture → gated cache → 0-LLM replay) |

## License

[MIT](LICENSE) © Raimondas Lencevicius
