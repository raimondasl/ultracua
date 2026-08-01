# ultracua — working notes

A Computer Use Agent: **learn a browser flow once, replay it deterministically at 0-LLM, 5–10× faster,
failing LOUD on drift.**

## Read this first

**[docs/open-defects.md](docs/open-defects.md) — the standing defect register.** 1 open finding from an
adversarial audit, every one independently reproduced with a probe, with `file:line` and concrete failure
scenarios. Check it before starting new work and before touching anything it names; re-deriving it costs
~25 minutes of fan-out.

## The three inviolables

Violating any of these is a blocking defect, not a trade-off:

1. **Replay never calls an LLM.**
2. **Never silently return or act WRONG — fail LOUD.**
3. **Write safety** — never double-submit a write, never silently suppress one.

## How work is done here

- **`uv` for everything.** `uv run --no-sync pytest tests/... -q`, `uv run --no-sync python -m benchmarks.X`.
  Never bare `uv sync` (it strips groups) — use `uv sync --all-groups`.
- The full suite is **key-less** — real headless Chromium against local fixtures, no API key, ~15 min.
  It must be green before a commit.
- **One slice per PR**, branched off `main`, single-sourced version bump in `pyproject.toml` first
  (then `uv sync --all-groups`). The user reviews and merges; don't merge.
- Secrets are env-resolved and **never** serialized, logged or written to disk. Never paste a key value.
- Keep large/working data off `C:` (`D:\ultracua-data`). `.env`, `.ultracua`, `.scratch` are gitignored;
  `baselines/` is committed.

## Two habits that have repeatedly paid off

**Reproduce before fixing.** Several reported defects have been misdiagnosed — the symptom real, the stated
cause wrong. The most recent: an audit blamed a row anchor's substring matching when the anchor never ran at
all. A fix built on a wrong diagnosis is worse than none.

**Verify a regression test fails against the old code.** A test that passes both before and after proves
nothing. This has caught several no-op "fixes".

## The pattern that predicts the next bug

Most defects found here are **a guard that already exists on a sibling path and was never applied to the
mechanism** — the replan path guards something the heal path doesn't; the recorder guards something `learn()`
doesn't; `heading`/`label` anchors were hardened and `row` wasn't. When you fix one, check its siblings.

## Measurement, not assertion

`benchmarks/drift_bench.py` (key-less, ~60s, CI-gated) is the instrument for any change to `locators.resolve`
or the recovery ladder. It reports a 0-LLM survival curve by mutation intensity, per-tier recovery rates, and
`silent_wrong` — which must stay within its published allowlist. Use it to *adjudicate* a resolver trade
rather than argue it; that is what it was built for. `baselines/README.md` states plainly what each number
does and does not prove — keep that honesty when adding to it.
