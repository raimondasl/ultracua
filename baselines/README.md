# Discovery-reliability baselines

Standing benchmark records, so later changes — most immediately **best-of-N authoring** (Tier-2) — can
be measured and gated against a fixed reference instead of a single noisy run. Only the **MiniWoB
best-of-N** baselines come from the variance harness
([`benchmarks/variance.py`](../benchmarks/variance.py)) against a **real LLM** (Anthropic); `drift.json`
is produced by `drift_sandbox.py` (scripted/**key-less**) and `recorder_ceiling.json` by
`recorder_ceiling.py`.

| File | Bench | Captured | Headline |
|---|---|---|---|
| `drift_v2.json` | **drift-bench v2** (10 scenarios, 171 rows × 2 arms) | 2026-08-05 | 0-LLM survival **falls 10/12 → 0/6** across mutation intensity k=1…7 (k50=6); **23 recovery-eligible rows where v1 had 0**; heal MECHANISM **14/14**, replan **2/10**; **1 published wrong-bind** (the *token-less* positional retarget, 0.9% of rows — its token-bearing half closed in 0.62.0) — the key-less CI gate |
| `demo.json` | demo-shop (4-step) | 2026-06-19 | 5/5 replay, speedup **86.3× ± 20.9**, ~$0.27 — no discovery variance (cost/speedup reference) |
| `miniwob.json` | MiniWoB++ ×10 (N=1) | 2026-06-19 | replay success **52% ± 13%** (40–70%), pass^k=0, ~$4.24 — the discovery-reliability reference |
| `miniwob_bestof3.json` | MiniWoB++ ×10 (**N=3 best-of-N**) | 2026-06-20 | **60% ± 0%** (6/10 every rep), ~$6.58 (1.55×) — best-of-N vs the N=1 baseline: +8 pts and **variance → 0** |
| `miniwob_reflect3.json` | MiniWoB++ ×10 (**N=3 + reflexion**) | 2026-06-20 | **52% ± 4%** (mostly 5/10), ~$8.32 — reflexion measured **net-harmful** vs best-of-N (−8 pts, +26% cost) |
| `drift.json` | drift-sandbox v1 (2 scenarios, 17 DOM drifts) | 2026-06-23 | **0-LLM resilience 12/12 (100%)** cosmetic drifts, **wrong-binds 0** — **SUPERSEDED by `drift_v2.json`**, which ports all 17 rows verbatim and gates this record's outcomes element-wise as its `v1_parity` block. Kept as the historical reference and a manually runnable cross-check; the CI gate now lives in v2 |
| `recorder_ceiling.json` | recorder ceiling (MiniWoB++, 3 tasks × 3 seeds) | 2026-06-24 | **recorder 9/9 vs LLM authoring 4/9** on the *same* seeds — recorder solves all garbled-label instances 0-LLM, **re-grounding by role+name+css (ids stripped)**; the LLM (N=1) solves only single-target and **misses every multi-target selection** (the grounding ceiling) |

## The 2026-08-13 re-baseline — the row S1b added for R3.7 could not fail for R3.7 (R4.33)

**Deliberate re-baseline** (slice R4.33). The table below records `row-nested-action` as the row that
lets the bench adjudicate the row-guard container walk. It cannot, and that was measured rather than
argued: the scenario's survival curve, `bound_by` histogram and ladder rate are **byte-identical with
and without a fix for R3.7**. Two independent reasons, and fixing either alone is not enough:

* the control carries the visible text "Details", so the inner `<li>` does not collapse to the empty
  string and the capture walk anchors on it exactly as the bind walk does;
* even with an icon-only control, the row's only identity is the `href` of the link INSIDE that `<li>`,
  so `rowIdOf` returns the same string for the `<li>` and the `<tr>` and the two walks agree anyway.

`row-nested-action` is kept, with its claim corrected — a labelled nested action list is a legitimate
shape, just not that one. **`row-nested-icon` is added** and is the faithful version: an icon-only
control AND an `id` on the `<tr>`, so the outer row has an identity the nested container does not share.

**While R3.7 is open this scenario binds NOTHING, including on its pristine arm.** That is the point of
it. `pristine_anchors` declares what the page OFFERS, not what currently survives; the empty survival is
the finding, made visible in the instrument for the first time.

| | before | after |
|---|---|---|
| corpus | 171 rows | 185 rows |
| `silent_wrong` | 2 (0.58%) | **2 (0.54%)** — same absolute; no new wrong bind |
| survival k1 / k3 | 20/24 / 22/24 | 20/27 / 22/27 — the same numerators; the new rows add 3 to each denominator and survive none |
| heal MECHANISM | 25/26 (0.96) | 36/38 (0.95) — one new decline (`row-nested-icon/reparent`) |
| predicted agreement | 84.3% | 78% — 9 new mismatches, ALL on `row-nested-icon` and all with ONE cause |

**Read the survival drop correctly.** The numerators are unchanged: nothing that used to bind stopped
binding. The curve fell because 14 rows were added that bind nothing, and they bind nothing because of
an OPEN finding. **12 of the 14 should move when R3.7 closes**; the remaining two (`target_removed`,
`target_replaced_crosstag`) carry `target_present=False` and must never bind at all. That the row CAN
move was verified rather than assumed: simulating a fix takes `bound_by` from `{'none': 14}` to
`{'css': 4, 'none': 9, 'role+name': 1}` and survival from `0/3` to `k1 1/3, k3 2/3`.

**The 9 new prediction mismatches do NOT have a single cause — and this paragraph said the opposite
until an audit checked it.** Two of them are pre-existing `predict()` gaps that appear wherever their
primitive appears, not row-guard effects at all: `decoy_substring+strip_aria_label` already mismatches
on `cart-row`, `row-positional`, `row-shared-action` and `row-nested-action` in the unchanged baseline,
and `reparent` on three of those. Under a simulated R3.7 fix the list was measured going 9 → 6 with a
tenth appearing.

So the instruction is the reverse of what was first written here: **triage these per row when R3.7
closes, and do not read the count as the signal.** A shrinking list is not evidence the fix worked, and
a residual list is not evidence it did not.

**One thing the faithful row surfaced immediately, which is the whole argument for fixing an
instrument** — three of its heal-eligible rows re-ground to a **byte-identical** recipe
(`steps_hash` unchanged), so the heal reports a persisted repair that changes nothing. Filed as
**R4.35** and named in `KNOWN_NO_DIGEST_MOVE`; no other corpus row does this, because every other drift
changes the page enough that the re-grounded spec differs. R4.35 records a larger claimed population
(7 rows, by "the next replay still refuses" rather than "the digest moved") as UNVERIFIED — it needs a
replay-after-heal experiment this bench does not run, and should not be quoted until someone does it.

## The 2026-08-05 re-baseline — four row-identity shapes the corpus was blind to

**Deliberate re-baseline** (slice S1b of `docs/correctness-plan.md`). Three planned slices name this
bench as their adjudicator — S9/R3.7 (the row-guard container walk), D2/AB-2 (fuzzy sole-candidate
binds) and D3/AB-3 (positional row tokens) — and the corpus could not express any of them. Every
`_cart_rows` row carried a DISTINCT `href`, so the row guard always found a discriminating token and
the interesting cases never arose. "Byte-identical to baseline" would have been a guarantee about
cases the bench never ran.

Four scenarios added, all targeting row 3 so they read like `cart-row`:

| scenario | the shape, and the slice it lets us adjudicate |
|---|---|
| `row-shared-action` | one shared endpoint + one shared `data-testid`; per-record identity ONLY in a hidden input — R3.1's discrimination rule, and where a priority order fabricates an identity every sibling has |
| `row-positional` | the only per-row tokens are `data-index` / `id="row-N"`: they discriminate today and RENUMBER on delete, so the identity outlives the record (**D3**) |
| `row-nested-action` | ~~the control sits in `tr > td > ul.actions > li`, where `anchorOf` climbs past an empty-text container and `_ROW_OF_JS` stops at the first `li` (**S9**)~~ — **this claim was false; see R4.33 and the 2026-08-13 entry above.** The row measures a labelled nested action list, on which the two walks agree |
| `fuzzy-decoy` | "Save draft" substring-matches "Save" once `strip_aria_label` collapses the target's name; a SOLE surviving fuzzy candidate binds outright (**D2**) |

The rows share one `href` where the shape requires the href to be non-discriminating; a wrong-row bind
is still caught, because the act trail records the CLICKED element's `data-oracle` ("row7" where the
golden trail expects "go").

**What moved, and what it means.**

| | before | after |
|---|---|---|
| corpus | 115 rows | 171 rows |
| `silent_wrong` | 2 (0.87%) | **2 (0.58%)** — same absolute; no new wrong bind |
| heal MECHANISM | 14/14 (1.00) | 25/26 (0.96) — one decline, newly exercised |
| replan MECHANISM | 2/10 (0.20) | 2/18 (0.11) |
| predicted agreement | 92.6% | 84.3% |

The two numbers that matter are the first two: **the new shapes produced no new wrong bind**, so the
current row-identity rule handles all four. The mechanism and agreement rates FELL because the added
rows are genuinely harder, not because anything regressed — a rate over a harder corpus is not
comparable to the same rate over an easier one, which is precisely why this file records the corpus
size beside every rate. The `fuzzy-decoy` scenario's pristine arm also proved AB-2's hazard is real
before any mutation: with the target labelled simply "Save", the pristine learn bound "Save draft"
outright, because Playwright's role-name matching is substring by default.

## drift-bench v2 — what it measures, and what it does not

[`benchmarks/drift_bench.py`](../benchmarks/drift_bench.py) (+ `drift_corpus.py`, `drift_fixtures.py`,
`oracle_provider.py`). Key-less, deterministic, ~49 s, gated in CI by `tests/test_drift_bench.py`.

**Why v2.** v1 reported 12/12 on twelve hand-written cosmetic drifts. That number could not move and could
not teach: **every** drift binds at 0-LLM, so the recovery machinery is never consulted — and that is now
*measured*, not inferred (`uv run python -m benchmarks.drift_sandbox --provider mock` shows `llm_calls == 0`
on all 12). v1's `--provider` arm was in fact worse than unmeasured: it passed a `provider_name=` kwarg
`run_cached` does not accept, so it raised `TypeError` and **had never executed once**. Both are fixed.

**The intensity axis.** `k` = how many of the target's resolution *anchors* a mutation destroys
(test-id, role+name exact, role+name substring, placeholder, exact-text, id, css path, neighbour anchor).
Compositions are sampled from a seeded pool of realistic primitives, and `k` is **derived** from the
composition, never hand-set. Two primitives are fixture-conditional for a measured reason: `cssPath` returns
`#<id>` and stops for any id-bearing element, so such a target has no independent css anchor and a wrapper
cannot move it, while `hash_ids` takes out both at once.

**The headline numbers** (seed 7, 115 rows × 2 arms, as of 0.64.0):

| | v1 | v2 |
|---|---|---|
| 0-LLM survival | 12/12 (saturated) | a **curve**: k1 10/12, k2 8/12, k3 11/12, k4 9/12, k5 6/9, k6 4/9, **k7 0/6** (k50 = 6) |
| recovery-eligible rows | **0** | **24** (14 heal-eligible + 10 replan-eligible) |
| heal | unmeasurable | **14/14** mechanism ceiling |
| suffix-replan | unmeasurable | **2/10** — it recovers only when an alternate route exists |
| wrong outcomes | 0, judged by landed URL | **1 published hole**, judged by the actuated action sequence |
| write safety | not covered | recovery **refused on 14/14** drifted write rows; 0 double-submits, 0 suppressed, 0 wrong-target |

**`silent_wrong` is judged from a golden trail, not a URL** — every interactable carries a `data-oracle` id
and a capture-phase listener records the exact ordered (element, value) sequence actuated. This is strictly
stronger than v1: a mis-bind that does not navigate left v1 reporting `success` on the start page, which its
classifier labelled `drifted` — the *safe* bucket. Typing into a decoy field or picking a wrong option were
laundered.

**The published wrong-bind, and what 0.62.0 did to it.** A target whose only surviving anchor is a
*positional* css is removed and a same-tag sibling slides into its slot, so the cached path re-matches the
neighbour — 0-LLM clicks a route no human approved, lands on the correct-**looking** page and reports
success. **v1's URL check would have scored this a clean survival.**

0.62.0 closed **half** of it. `locators._testid_contradicted` withdraws the Tier-2 css-trust when the bound
element positively falsifies a recorded `data-testid`: that trust is a *rename hypothesis*, and a rename
cannot change a developer token, so the token's absence is evidence the path landed elsewhere. Measured:
`shop-4step/positional-css-retarget` went `wrong` → `drifted` at 0-LLM and `wrong` → `replanned` in the
recovery arm, with **no** v1 row, `cart-row` row or write row moving.

**The other half is the common half, and it is still open.** 8 of the corpus's 10 Tier-2 css binds are on
targets that recorded no identity token at all, and for those nothing `describe()` captures separates *"the
target was renamed"* from *"a sibling slid into its slot"* — both renames the resolver must keep recovering
present exactly as the retarget does. That is an information limit of the recorded field set, not an
oversight. So the allowlist entry **moved rather than disappeared**: `anchor-link/positional-css-retarget-
tokenless` is a curated row that reproduces the surviving hole, and `silent_wrong` is still 2 (0.9% of rows).
Deleting the old entry without adding it would have converted a *measured* hole into an *assumed-closed*
one — the exact failure this bench exists to prevent.

**0.64.0 added a second, narrower identity check, on ROWS.** Deleting the row you recorded is what makes
its control unique, so Tier 1 would bind a different customer's control outright — the neighbour anchor that
would have objected is only consulted when role+name is AMBIGUOUS, which deleting the row removes. A row is
now identified by its `id` / `data-*` value / inner link target, and a missing row fails loud. Keyed on that
token and NOT on row text, deliberately: a text-keyed version was measured and cost FOUR rows of 0-LLM
survival by refusing rows that had merely been *edited* — a price change is not a different record.
Token-less rows are therefore unprotected, for the same information-limit reason as above. Net cost:
`resilience_by_k["1"]` 11/12 → 10/12, one row, inside the gate's tolerance.

The earlier cost was one row: `anchor-button/placeholder_rewrite+rename_full+testid_drop` (every identity token dead
*and* the css bind correct — indistinguishable from the retarget in the recorded-field lens) now fails loud
instead of binding. It is heal-eligible and the ladder recovers it, but it drags `resilience_by_k["6"]` from
5/9 to 4/9 and therefore the published **`k50` from 7 to 6**. That is a deliberate re-baseline, recorded
here rather than left for a reader to discover: the rung got *safer*, and one row moved it.

Ruled out **with measurement**, so they are not re-proposed: demoting or dropping the css candidate (turns
the two `conflict` rows into silent wrong-binds — measured during v1); any N-of-M positive corroboration rule
(both renames record zero corroborating tokens, so it refuses them — predicted to drop v1 parity to 10/12);
text similarity (the renames change text exactly as the retarget does); and the neighbour anchor (it
*matches* on the retarget row, since the decoy sits in the same section).

### What these numbers do NOT prove

- **The recovery rates are a MECHANISM CEILING, not a heal rate.** The key-less arm answers with
  `OracleProvider`, which reads a ground-truth marker off the page — a perfect-vision model at accuracy 1.0.
  It measures whether the machinery recovers: re-ground, reject a no-effect heal, persist the locator, splice
  the tail, preserve the prefix, move the approval digest. `real recovery = ceiling × model accuracy`, and
  **nothing here measures the second factor.** Only the paid `--provider anthropic` arm does, and it has not
  been run.
- **A perfect oracle is not automatically a correct one.** The first version keyed on "the first unperformed
  step" and manufactured a *false heal* the engine reported as `success=True, mode=replay+heal`. Only the
  golden trail caught it.
- **The pages are still synthetic.** Server-side injection buys deterministic, multi-page, compounding
  mutation; it does not buy realistic markup. v2 refutes *"every drift is a single cosmetic mutation on a
  ~2-section page"*. It does **not** refute *"toy page"*. Real frozen snapshots remain unbuilt.
- **`k` has no empirical prior.** It is a model of `resolve()`'s decision surface, so the curve answers *how
  many anchors must an adversary destroy before we break* — a robustness score. It does **not** answer *how
  likely is a real redesign to break us*. That is also why no area-under-curve scalar is gated: an
  equal-weighted average over an axis with no prior would encode this sampler's weighting as a fact.
- **`silent_wrong` bounds the wrong-bind rate at roughly <2.5% (95% CI) over ~114 rows, not at zero.** It is
  an absence of evidence on this population. And it is a *lower* bound in one corner: an act landing on a
  non-interactable with no handler logs nothing and reads as `drifted` — the safe direction, but silent.
- **Recovery is judged by outcome, not by trail equality.** A heal or replan is *allowed* to actuate a
  different element — that is what recovery is. What guards a recovery that reaches the goal via an element
  no human approved is not this bench: it is the **approval digest**, which moves on every persisted repair
  so an approved flow refuses (`stale_approval`, 0.60.0). Asserted here as `heal_invalidates_approval`.
- **The write rows measure the GATE, never recovery** — by design, at four independent refusal points. They
  say nothing about a real payment API's idempotency semantics, and nothing about the per-write commit
  barrier (`StepConfirm` is built only in `flows.py`, never on the `run_cached` path this bench drives).
- **Nothing at the `flows.py` layer is measured** — approval, `_preflight_row`, quarantine and the H9
  layers (contracts, magnitude, the sampled judge) are all structurally unreachable from `run_cached`, with
  the single deliberate exception of the approval digest above. Reading v2 as narrowing the H9 gap would be
  wrong.
- **The per-`k` rates are mildly PLATFORM-SENSITIVE, and the first version of this bench made that worse.**
  It forced `action_timeout_ms` to 1500 ms, which `browser.py` applies as the *context-wide* default for
  every Playwright operation — so a slow-but-successful operation on a slower machine became a fail-loud row,
  and GitHub's Windows runner drifted 1–2 rows more per read scenario than the development machine. The
  override is gone (it bought ~3 s of the ~52 s run) and the bench now measures the engine at its configured
  timeout, which is recorded and compared for equality. The residual sensitivity is why the per-`k` gate
  carries a 10-point tolerance while the safety invariants carry none.
- **`predicted_agreement` (94%) is a diagnostic, not a quality score.** It compares the resolver against a
  frozen model of its own decision surface; only *new* mismatches are gated. Write rows are excluded because
  the predictor models `resolve()` alone and a write step also passes the mutation gate — measured: every
  in-form mutation makes `order-form` drift with the locator resolving perfectly and the *gate* refusing.

---

**Drift-sandbox v1** ([`benchmarks/drift_sandbox.py`](../benchmarks/drift_sandbox.py)) — superseded, kept as
the historical record. It learns a flow then replays it against a distribution of realistic DOM drifts. It
runs **two
scenarios** so both `resolve()` code paths are exercised: **anchor-link** (the target is an `<a>` with
role+name *and* a neighbor-anchor heading — banner added, id removed, target wrapped / reordered /
re-classed, sibling inserted, heading renamed, target renamed, an ambiguous same-name twin, target removed)
and **span-link** (a *roleless* `<span>` "link" whose `describe()` role ∉ `KNOWN_ROLES`, so resolve skips
role+name AND the neighbor anchor and leans on text + the positional css — span-none, span-renamed,
span-reordered, plus two **conflict** drifts span-augmented-reordered / span-sibling-decoy). It scores how
many *cosmetic* drifts the resilient locator survives at 0-LLM and asserts the invariant **wrong-binds = 0**
(a drift never silently reaches the wrong target *page*), that **conflict** drifts fail loud (never bind the
`/wrong` decoy), the ambiguous twin is disambiguated, and a removed target fails loud. Because it needs no
API key it was the first bench to run in CI. Its CI test is now **retired**: v2 asserts all four of its
boolean invariants *and* compares its 12/12 plus its per-row outcome vector element-wise, so deleting
`tests/test_drift_sandbox.py` cost no coverage (and tightened the floor from `>= 0.8` to an exact `1.0`).
`drift.json` remains the reference v2's `v1_parity` block is compared against.

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

- **Same-seed contrast (MEASURED, `--provider anthropic`):** LLM authoring (N=1) solved **4/9** — *every
  single-target* instance (3 × `click-option`, 1 single-box `click-checkboxes`) — and **missed every
  multi-target** garbled selection (3 / 7 / 10 / 11 targets) **plus** the empty "Select nothing". The
  recorder's 9/9 cracks exactly the 5 the LLM can't. (One real-LLM run — the count can wiggle, but the
  single-vs-multi-target split is the robust signal; best-of-N doesn't move the multi-target ceiling, per
  STATUS. `click-checkboxes-large` is a stress extension of `click-checkboxes`.)
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

# Best-of-N variants (real LLM):
uv run --group bench python -m benchmarks.variance --bench miniwob --reps 5 --samples 3 --all --baseline baselines/miniwob_bestof3.json
uv run --group bench python -m benchmarks.variance --bench miniwob --reps 5 --samples 3 --reflect --all --baseline baselines/miniwob_reflect3.json

# Key-less baselines (no API key):
uv run python -m benchmarks.drift_sandbox --json baselines/drift.json
uv run python -m benchmarks.recorder_ceiling --json baselines/recorder_ceiling.json
```

Notes:
- These use a real LLM (key from `.env`) and are **manual/local — never wired into CI**. ~$4.5 for the
  pair above.
- The gate compares `replay_success_rate` (machine-independent) and total cost. `speedup` is recorded
  but **not gated** — it's an in-process micro-timing that depends on the machine.
- `pass_k` here is strict ("a rep passes only if ALL its tasks pass"); the per-task `replay_success_rate`
  mean is the more actionable discovery-reliability signal.

<!-- honesty:customer-bench -->

## `customer_v1_gitea.json` — the customer benchmark, GITEA ONLY (2026-08-26)

**What it is.** Three full passes of the seven Gitea corpus scenarios, folded by
`benchmarks.corpus_aggregate --baseline`. `availability_rate` mean **0.762** over **n=21**
scenario-observations (3 reps x 7 scenarios), cost $1.81 for the series.

**Why the name says `_gitea`.** It is half of what step 2.4 owes. `baselines/customer_v1.json` is
that step's artifact and covers BOTH substrates; writing this under that name would have marked the
step shipped while the Odoo half, the nightly and the honesty page do not exist. The plan-state test
caught exactly that and is the reason for the rename.

**What it does prove.** Five of the seven rows returned an identical verdict in all three passes,
including `gitea-comment` at **3/3 `true`** — a real write, learned once and replayed at 0 LLM calls
every time. `gitea-start-timer` is **0/3 `not_authored`**: a stable, reproducible product limitation
(R4.102, a control below the fold that never enters the agent's observation), which is what a
baseline wants a known failure to look like.

**What it does NOT prove.** `gitea-sort-list` gave THREE different outcomes in three passes
(`ok` / `refused` / `not_authored`) and is carried in `unstable` — its baseline row is recorded as
NOT passing, and no number here should be read as evidence about it. And three passes is a flake
detector, not a stability certificate: a row passing 3/3 could still be 80% reliable, which shows
3/3 about half the time.

**Why `cost_usd` is the MAXIMUM observed, not the mean.** `_cost_findings` regresses at
`baseline * 1.25`, and the three passes cost $0.3502 / $0.8421 / $0.6167 — an **82% spread**,
because a learn that fails spends its whole budget and which rows fail varies. A mean baseline
failed rep 2, one of the passes it was built from (R4.106). `cost_per_rep` carries the individual
values so the mean is recoverable.

**Why Odoo is absent.** Not an oversight, and not a lack of measurement — see R4.105. Odoo's three
reps gave mean 0.181 with std 0.203, and **58% of its 12 replay refusals are the mutation gate
refusing a step that R4.27 misfiled as a write**. A gated step loses self-heal, so drift becomes a
hard refusal rather than a recovery. A baseline written now would be dominated by a filed defect and
invalidated when it is fixed.


### The open defects these numbers are standing on

Every number above is measured under the product as it is TODAY, and some of what it measures is a
filed defect rather than a property of the approach. This list is machine-checked against
`docs/register/state.json` by `tests/test_honesty_page.py`, in both directions:

* **every id here must still be OPEN.** When one is fixed the caveat is stale and the number it
  qualifies may have moved — so the test goes red and somebody re-measures rather than leaving a
  reader to trust a paragraph that stopped being true;
* **every open finding cited anywhere in this section must appear here.** Mentioning one in prose
  without declaring it is how a caveat ends up in the narrative and out of the checked list.

A finding that is FIXED may still be cited above as history — R4.106 is, and that is fine. What may
not happen is a live caveat quietly becoming a historical one.

<!-- open-findings:customer-bench -->
* **R4.27** — Odoo serves list reads as JSON-RPC POSTs and the wire detector marks those steps
  mutating. That is what makes 58% of Odoo's replay refusals the mutation gate, and it is why Odoo
  has no baseline here at all.
* **R4.84** — three of the eight doors into the engine can re-author a write flow, which performs
  the write again. Nothing in this corpus exercises that path; the numbers say nothing about it.
* **R4.102** — an interactable control below the fold never enters the agent's observation, and
  nothing in the observation says there is more page. `gitea-start-timer`'s 0/3 IS this defect, so
  one seventh of the Gitea availability number is a known grounding limit rather than a replay one.
* **R4.105** — Odoo's own exclusion: mean 0.181, std 0.203, dominated by R4.27. Recorded here
  because "Odoo is absent" is a statement about the product's measured behaviour, not about effort.
* **R4.108** — the generic-operator half of the mutation sweep is unbuilt, so the suite behind
  these benchmarks is proven against curated mutations only.
<!-- /open-findings -->

<!-- /honesty:customer-bench -->
