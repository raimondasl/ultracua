# Discovery-reliability baselines

Standing benchmark records captured with the variance harness
([`benchmarks/variance.py`](../benchmarks/variance.py)) against a **real LLM** (Anthropic), so
later changes — most immediately **best-of-N authoring** (Tier-2) — can be measured and gated against a
fixed reference instead of a single noisy run.

| File | Bench | Captured | Headline |
|---|---|---|---|
| `demo.json` | demo-shop (4-step) | 2026-06-19 | 5/5 replay, speedup **86.3× ± 20.9**, ~$0.27 — no discovery variance (cost/speedup reference) |
| `miniwob.json` | MiniWoB++ ×10 (N=1) | 2026-06-19 | replay success **52% ± 13%** (40–70%), pass^k=0, ~$4.24 — the discovery-reliability reference |
| `miniwob_bestof3.json` | MiniWoB++ ×10 (**N=3 best-of-N**) | 2026-06-20 | **60% ± 0%** (6/10 every rep), ~$6.58 (1.55×) — best-of-N vs the N=1 baseline: +8 pts and **variance → 0** |
| `miniwob_reflect3.json` | MiniWoB++ ×10 (**N=3 + reflexion**) | 2026-06-20 | **52% ± 4%** (mostly 5/10), ~$8.32 — reflexion measured **net-harmful** vs best-of-N (−8 pts, +26% cost) |
| `drift.json` | drift-sandbox (11 DOM drifts) | 2026-06-22 | **0-LLM resilience 8/9 (89%)** cosmetic drifts, **wrong-binds 0**, ambiguous twin disambiguated, removed target fails loud — the **key-less, no-LLM** locator-resilience reference |

**Drift-sandbox** ([`benchmarks/drift_sandbox.py`](../benchmarks/drift_sandbox.py)) is the **only key-less
baseline** — it learns one flow then replays it against a distribution of realistic DOM drifts (banner
added, id removed, target wrapped / reordered / re-classed, sibling inserted, heading renamed, the target
renamed, an ambiguous same-name twin, the target removed). It scores how many *cosmetic* drifts the
resilient locator survives at 0-LLM and asserts the invariant **wrong-binds = 0** (a drift never silently reaches the wrong
target *page*; a mis-resolution that simply fails to progress — like `target-renamed` below — surfaces as
a resilience miss, not a wrong-bind). Because it needs no API key it runs in CI via
`tests/test_drift_sandbox.py`; `drift.json` is the precise gate for `--baseline`.

**Found gap (the benchmark paying off immediately):** the one miss is `target-renamed` — when the target's
visible label changes, `role+name` breaks and `resolve()`'s `text` candidate (a loose substring, tried
*before* css) grabs an unrelated prose element containing the old name instead of letting the id-anchored
css recover the link. The fix (reorder / scope the text candidate) is tracked as a follow-up and should be
*evaluated against this benchmark* — including a `<span>`-link drift, since text-before-css also protects
positional-css-fragile span links, so the reorder is a measured trade, not an obvious win.

**Best-of-N result (N=3 vs N=1):** re-authoring up to 3× and keeping the first verify-passing sample
lifted per-task success 52%→60% and — the real win — **collapsed run-to-run variance from ±13% to
zero** (every rep landed on exactly 6/10). Cost rose only 1.55× (adaptive early-stop, not 3×). The
remaining 40% is a capability ceiling, not variance. The regression gate prints "REGRESSION" against
`miniwob.json` *only* on cost (>25% by design) — success went up.

The MiniWoB number is the one that matters: it's where LLM authoring is unreliable (the bottleneck),
so it's the headroom best-of-N should close. The demo flow authors reliably (no variance).

## Re-running / gating

```bash
# Re-measure and FAIL (exit 1) if replay-success regressed beyond the error bars, or cost rose >25%:
uv run --group bench python -m benchmarks.variance --bench miniwob --reps 5 --all --baseline baselines/miniwob.json
uv run python -m benchmarks.variance --bench demo --reps 5 --baseline baselines/demo.json
```

Notes:
- These use a real LLM (key from `.env`) and are **manual/local — never wired into CI**. ~$4.5 for the
  pair above.
- The gate compares `replay_success_rate` (machine-independent) and total cost. `speedup` is recorded
  but **not gated** — it's an in-process micro-timing that depends on the machine.
- `pass_k` here is strict ("a rep passes only if ALL its tasks pass"); the per-task `replay_success_rate`
  mean is the more actionable discovery-reliability signal.
