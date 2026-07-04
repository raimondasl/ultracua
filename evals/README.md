# ultracua capability evals — manual, aspirational, cost-aware

A **manually-run** evaluation suite that measures ultracua against both its **shipped behavior**
(core learn/replay, recorder, write safety, drift resilience) and its **aspirational roadmap** —
the ROADMAP.md "Innovation horizons" candidates H1–H16. It is deliberately **not** part of CI:
the key-less pytest suite in `tests/` is the regression gate; this suite is the *target*, re-run
by hand to chart progress. **Low scores on horizon groups are expected and are the point.**

## Quick start

```bash
uv run python -m evals.run --list                 # inventory (107 scenarios)
uv run python -m evals.run --estimate             # $ cost table for any selection
uv run python -m evals.run                        # DEFAULT: key-less scenarios only ($0)
uv run python -m evals.run --group core           # partial: one group
uv run python -m evals.run --group h03,h07,h08    # partial: several horizons
uv run python -m evals.run --id h05.dryrun        # partial: id substring
uv run python -m evals.run --tag writes           # partial: by tag
uv run python -m evals.run --include-llm          # + real-LLM tier (costs real $)
uv run python -m evals.run --include-llm --include-live --budget 2.00   # everything, $ cap
uv run python -m evals.run --config myrun.json    # saved partial-run config
```

`--config` JSON: `{"groups": [...], "ids": [...], "tags": [...], "include_llm": true,
"include_live": false, "budget_usd": 2.5}` — CLI flags override.

Reports are written to `evals/results/` (gitignored) as JSON: per-check statuses, per-group
scores, and **measured** LLM spend (from the router's usage totals) next to the estimates.

## Cost: full run vs parts

The suite is **key-less-first**: horizon probes and shipped-behavior checks run against local
HTTP fixtures with scripted providers + real headless Chromium — $0, no API key, no external
network. Real-LLM scenarios are isolated in the opt-in `llm`/`live` tiers.

| Selection | Scenarios | Est. LLM calls | Est. cost |
|---|---|---|---|
| **Default run** (key-less) | 102 | 0 | **$0.00** |
| `--include-llm` adds | 4 | ~22 | ~**$1.00** |
| `--include-live` adds | 1 | ~6 | ~**$0.35** |
| **Full suite** | **107** | ~28 | ~**$1.35** |

Any narrower selection: `uv run python -m evals.run --estimate <your filters>` prints the exact
table for that selection before you spend anything. `--budget N` is a hard cap at run time: once
estimated+measured spend would exceed it, remaining LLM scenarios are skipped (recorded as such).

Estimates are calibrated on this repo's **measured** baselines (`baselines/*.json`): one full
LLM learn+replay on a local fixture ≈ $0.27, a MiniWoB-style task learn ≈ $0.09, one
extraction/caption call ≈ $0.02 — and rounded up (estimates are a ceiling, not billing). After a
paid run, the report's `usage.cost_usd` per scenario is the measured truth; if estimates drift
from measurements, recalibrate the scenario metadata.

The paid tier is small **by design**: today's horizons are probed key-lessly (paying an LLM to
confirm an API doesn't exist would be waste). As horizon features ship, their modules gain real
`requires="llm"` scenarios and the full-suite cost grows — update the table above when it does.

## How to read the report

Each scenario runs 2–6 **checks**; every check ends in one of:

- **pass** — the capability works today.
- **missing** — the capability **is not built yet** (an aspirational probe found no API). This is
  the roadmap gap being measured, not a defect.
- **fail** — a **shipped** capability misbehaved. This is a regression: investigate it like a bug.
- **skip** — not runnable in this configuration (no API key, budget cap, tier excluded).
- **error** — the scenario itself crashed: an eval bug. The runner exits 1 so it can't hide.

`score = pass / (pass + fail + missing)`. Group scores near 1.0 for `core` mean the shipped
engine holds its promises; low scores on `h01`–`h16` chart how much of each horizon exists.
The interesting motion over time is `missing → pass` (features shipping) with `fail` staying 0.

**Important caveat on horizon scores:** they include *partial credit* for shipped building
blocks (by design — the report shows real current capability, not a flat zero). A horizon at
0.6 does NOT mean the feature is 60% built; the **`missing` count is the unbuilt-capability
gap.** The features themselves are ~0% built today.

### Snapshot at suite creation (v0.45.0, key-less run, 107 scenarios / 419 checks)

| Group | Score | Pass | Missing (unbuilt) | Fail |
|---|---|---|---|---|
| core | 0.95 | 76 | 4* | 0 |
| h01 attested replay | 0.46 | 10 | 12 | 0 |
| h02 flows-as-tools | 0.54 | 15 | 13 | 0 |
| h03 typed templates | 0.52 | 13 | 12 | 0 |
| h04 in-profile capture | 0.62 | 13 | 8 | 0 |
| h05 dry-run replay | 0.55 | 12 | 10 | 0 |
| h06 drift-repair bot | 0.59 | 16 | 11 | 0 |
| h07 control flow | 0.64 | 16 | 9 | 0 |
| h08 action breadth | 0.40 | 10 | 15 | 0 |
| h09 semantic wrongness | 0.44 | 10 | 13 | 0 |
| h10 Drift-Watch | 0.63 | 20 | 12 | 0 |
| h11 bot-auth identity | 0.50 | 12 | 12 | 0 |
| h12 talk-through recorder | 0.63 | 15 | 9 | 0 |
| h13 contract lanes | 0.50 | 13 | 13 | 0 |
| h14 mandated money | 0.50 | 13 | 13 | 0 |
| h15 air-gapped mode | 0.54 | 15 | 13 | 0 |
| h16 training flywheel | 0.70 | 19 | 8 | 0 |

\* the 4 core `missing` are aspirational probes deliberately placed in core modules (payload-aware
idempotency basis, recorder narrate/domain capture) — not regressions.

## Layout

```
evals/
  core.py               framework: @scenario registry, CheckResult, probes, Ctx
  fixtures.py           dict-backed local HTTP fixture (records writes = the write-safety oracle)
  run.py                the CLI runner (selection, estimate, budget, JSON reports)
  scenarios/
    core_replay.py      learn -> 0-LLM replay, miss-fails-loud, meta forward-compat  (exemplar)
    core_writes.py      write safety: exactly-once, mutation gate, multi-write barrier, precheck
    core_recorder.py    recorder fidelity, nav survival, cross-origin + undeclared-write refusals
    core_resilience.py  cosmetic drift, ambiguity fail-loud, truncation flag, pins, shape drift
    core_llm_live.py    the paid tier: real learn/extract/caption/best-of-N + one live HN flow
    h01_..h16_*.py      one module per Innovation horizon (aspirational probes + partial credit)
  results/              JSON reports (gitignored)
```

## Adding a scenario

Copy the style of `scenarios/core_replay.py`:

```python
from evals.core import Ctx, expect, scenario
from evals.fixtures import Fixture, page

@scenario(id="h03.slots.my_check", title="...", group="h03",
          aspirational=True, tags=("slots",))
async def my_check(ctx: Ctx):
    ...
    return [expect(cond, "what this measures", "note on miss", aspirational=True)]
```

Rules the suite enforces or expects:
- key-less scenarios (`requires="none"`) declare `est_llm_calls=0, est_cost_usd=0.0` (asserted),
  use only local fixtures, and must be deterministic.
- probe not-yet-built APIs with `probe()` / `import_probe()` / `expect(..., aspirational=True)` —
  a missing capability must report `missing`, never crash, never `fail`.
- reserve `fail` for shipped behavior misbehaving; give partial credit where a building block
  already exists (the report should show real capability, not a flat zero).
- write only inside `ctx.tmp`; never touch the repo's `.ultracua/`.
- `requires="llm"` scenarios estimate cost from the measured anchors above and skip themselves
  cleanly when no key is configured; `requires="live"` is read-only and polite (1–2 pages).
```
