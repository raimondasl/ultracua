# ultracua

A Computer Use Agent (CUA) that drives a web browser at **5–10× human speed**.

The headline lever isn't faster clicking — it's removing the LLM from the repeat-run loop
by **learning a flow once and replaying it deterministically**. See **[PLAN.md](PLAN.md)**
for the full architecture, research basis, and roadmap.

> **Status: Phase 0 — walking skeleton.** A warm browser session with a scoped
> DOM/accessibility snapshot, a single Claude adapter that picks the next action, post-action
> verification, and a per-step latency breakdown. No flow cache yet (that's Phase 1).

## Requirements

- [`uv`](https://docs.astral.sh/uv/) (manages Python itself — no separate Python install needed)

## Setup

```bash
uv sync                              # create the venv + install deps
uv run playwright install chromium   # one-time browser download
```

## Run it

Key-less smoke test (heuristic **mock** provider — no API key needed):

```bash
uv run ultracua --provider mock --url https://example.com --goal "open the more information link"
```

LLM-in-the-loop (needs `ANTHROPIC_API_KEY`):

```bash
# PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."
uv run ultracua --url https://example.com --goal "open the more information link"
```

Each step prints its latency breakdown, e.g.:

```
step 0: snapshot=18ms  ttft=620ms  gen=140ms  act=44ms  verify=15ms  total=837ms
         action={'action': 'click', 'intent': "...", 'ref': 'e3'}
         -> ok changed=True
```

`ttft` (time-to-first-token) is the dominant component — which is exactly why Phase 1's
cache, by replaying without an LLM call, is the path to 5–10×.

## Configuration

Environment variables (a local `.env` is loaded automatically):

| Var | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for the `anthropic` provider |
| `ULTRACUA_PROVIDER` | `anthropic` | `anthropic` or `mock` |
| `ULTRACUA_MODEL` | `claude-opus-4-8` | Discovery (strong-tier) model |
| `ULTRACUA_HEADLESS` | `1` | Set `0` to watch the browser |
| `ULTRACUA_MAX_STEPS` | `8` | Step budget per run |
| `ULTRACUA_MAX_ELEMENTS` | `80` | Cap on elements per snapshot |

## Develop

```bash
uv run pytest        # snapshot + timing tests (the snapshot test drives real Chromium)
```

## Layout

```
src/ultracua/
  browser.py      warm Playwright/CDP session (component 1)
  snapshot.py     scoped DOM/AX snapshot via injected JS (component 3)
  providers/      LLM adapters: anthropic, mock (component 4)
  verify.py       post-action state-diff (component 5)
  agent.py        the Phase 0 loop, instrumented
  cli.py          `ultracua` entry point
```
