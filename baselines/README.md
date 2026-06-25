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
| `drift.json` | drift-sandbox (2 scenarios, 17 DOM drifts) | 2026-06-23 | **0-LLM resilience 12/12 (100%)** cosmetic drifts, **wrong-binds 0**, ambiguous twin disambiguated, 2 conflict drifts fail loud (never wrong), removed / cross-tag-twin targets fail loud — the **key-less, no-LLM** locator-resilience reference |
| `recorder_ceiling.json` | recorder ceiling (MiniWoB++, 3 tasks × 3 seeds) | 2026-06-23 | **recorder solved 9/9 garbled-label instances 0-LLM** (`click-checkboxes`/`-large`/`click-option`), **re-grounding by role+name+css (ids stripped)** to a positive `WOB_RAW_REWARD` — key-less proof that a *demonstration* of these tasks replays 0-LLM on the LLM's own grounding surface |

**Drift-sandbox** ([`benchmarks/drift_sandbox.py`](../benchmarks/drift_sandbox.py)) is the **only key-less
baseline** — it learns a flow then replays it against a distribution of realistic DOM drifts. It runs **two
scenarios** so both `resolve()` code paths are exercised: **anchor-link** (the target is an `<a>` with
role+name *and* a neighbor-anchor heading — banner added, id removed, target wrapped / reordered /
re-classed, sibling inserted, heading renamed, target renamed, an ambiguous same-name twin, target removed)
and **span-link** (a *roleless* `<span>` "link" whose `describe()` role ∉ `KNOWN_ROLES`, so resolve skips
role+name AND the neighbor anchor and leans on text + the positional css — span-none, span-renamed,
span-reordered, plus two **conflict** drifts span-augmented-reordered / span-sibling-decoy). It scores how
many *cosmetic* drifts the resilient locator survives at 0-LLM and asserts the invariant **wrong-binds = 0**
(a drift never silently reaches the wrong target *page*), that **conflict** drifts fail loud (never bind the
`/wrong` decoy), the ambiguous twin is disambiguated, and a removed target fails loud. Because it needs no
API key it runs in CI via `tests/test_drift_sandbox.py`; `drift.json` is the precise gate for `--baseline`.

**Found gap → fixed (the benchmark paying off):** the original miss was `target-renamed` — when the
target's visible label changes, `role+name` breaks and `resolve()`'s loose substring `text` candidate
grabbed an unrelated prose element containing the old name (a `<p>` "…then continue." single-matching a
renamed "Continue" link) *before* the id-anchored css could recover the link — a silent mis-resolution.
The fix was chosen **on this benchmark**: a `<span>`-link scenario was added because text-before-css also
protects positional-css-fragile span links, so the change was a measured trade, not an obvious win. Three
ordering options were measured — demote the substring below css (Opt1), drop it (Opt3), or **scope it to
the element's own tag** (Opt2). Opt1/Opt3 fixed the renames but **regressed `span-augmented-reordered`
into a silent wrong-bind** (the brittle positional css won and bound a `/wrong` decoy). Opt2 (tag-scoping)
fixed the prose leak, but an **adversarial review** then found tag-scoping alone could still single-match a
same-tag *sibling* sharing the cached substring and short-circuit the structural locators
(`span-sibling-decoy`). The shipped fix is tag-scoping **plus a cross-check**: the fuzzy substring and the
css path are two independent guesses; a unique css match is trusted unless the substring uniquely
*contradicts* it, and a lone substring match (with nothing to corroborate it) is **never** trusted — both
conflict drifts now **fail loud** instead of binding the decoy. The review also flagged that the *exact*
whole-text candidate was still un-scoped and could leak across tags (a removed roleless `<span>` whose
exact text reappears as a `<p>`/`<a>`), so exact-text is now tag-scoped too (`span-removed-crosstag-twin`
guards it). Result: **12/12 (100%) cosmetic resilience, wrong-binds 0**, conflict drifts fail loud, and
removed / cross-tag-twin targets fail loud. (Known residual, documented in `resolve`: a purely *positional*
css whose target is removed can retarget a moved-in neighbor with nothing to contradict it — closing that
would also break the legitimate `span-renamed` recovery that relies on positional css, so it's the accepted
cost of keeping css as a fallback tier.)

**Recorder ceiling** ([`benchmarks/recorder_ceiling.py`](../benchmarks/recorder_ceiling.py)) turns the
Phase-I recorder's lever from *asserted* to *measured*: for each seeded MiniWoB++ instance a "human"
demo-oracle reads the instruction's named targets, the recorder captures it, and the recorded flow replays
**0-LLM** to `reward > 0`. Crucially the id/test-id are **stripped from the recorded specs**, so replay
re-grounds by **role+name+css** — the same grounding surface the LLM mis-grounds (not MiniWoB's internal
`chN` ids). Key-less + gated in CI (`tests/test_recorder_ceiling.py`). Honest scope:

- The "**LLM fails these**" half is **asserted from STATUS's measured ceiling** (`miniwob.json`), **not run
  here** — the `--provider` arm runs LLM authoring on the *same* seeds for a true same-seed contrast (paid).
- `click-checkboxes` + `click-option` are in STATUS's measured miss set; `click-checkboxes-large` (7–11
  targets) is a **stress extension** of the same class, not separately measured against the LLM.
- The 9 instances span 0–11 targets (one is the trivial "Select nothing"); 4 are multi-target.
- *Semantic* `click-checkboxes-soft` is **excluded** — it needs a knowledge-bearing demonstrator (a human /
  an LLM caption), the honest boundary of a scripted oracle: the recorder routes around *grounding*, but
  the demonstration must still be *correct*.

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
