# Self-healing & drift resilience

*How ultracua copes when a website's elements change between the time you **learn** a flow and the time
you **replay** it — and, just as important, where it deliberately **refuses to guess** and stops for a human
instead.*

This is a companion to [GUIDE.md](GUIDE.md) (the developer guide) and [STATUS.md](STATUS.md) (honest status
and measured numbers).

---

## TL;DR

- **Most healing is free and instant (0 LLM).** A learned step doesn't remember a brittle CSS selector — it
  remembers a *ranked set of resilient anchors* (test-id, role + accessible name, visible text, a short CSS
  path, a neighbouring landmark). At replay a resolver re-binds the element from those anchors, so an id
  change, a re-style, a move, a wrapper `<div>`, or a renamed label usually just works — **with no model call.**
- **When the passive resolver can't bind, an LLM can heal — but only if you let it.** LLM-based healing
  (re-grounding one step, or re-planning the broken tail of the flow) is a **fallback that is off on a plain
  replay** and is **categorically disabled for write steps**.
- **ultracua would rather stop than guess.** If a locator is *ambiguous* (matches more than one element), if a
  **write** step's context drifted, or if the returned **data looks wrong**, ultracua **fails loud and
  escalates to a human** instead of silently doing the wrong thing. This is the deliberate trade: it heals a
  lot for free, and refuses the risky cases by design.
- **What is actually measured** (key-less, CI-enforced — `benchmarks/drift_bench.py`): 0-LLM survival across
  a graded distribution of *compounding* DOM mutations, falling from **11/12** when one locator anchor is
  destroyed to **0/6** when all seven are; the heal machinery recovering **12/12** of those total-destruction
  cases *given* correct element identity; suffix-replan recovering **1/9** cases where the target is gone; a
  write **never** recovered across 14/14 drifted write rows; and **one published wrong-bind** (0.9% of rows),
  the positional-CSS retarget case. Each of those numbers has a sharp limit — read
  [the honest limits](#honest-limits) before quoting any of them, especially the heal figure, which is a
  *mechanism ceiling* measured with a perfect-vision provider and not a heal rate.

---

## The four layers of healing

Healing escalates from cheapest/safest to most expensive. In practice **Layer 1 does almost all of the work.**

| # | Layer | Uses an LLM? | On by default? | What it recovers |
|---|-------|:---:|:---:|------------------|
| 1 | **Resilient locator resolver** | No | **Yes** — always | Cosmetic & identity-preserving changes (id / class / style / move / wrapper / rename) as long as *some* unique anchor still matches |
| 2 | **Single-step self-heal** | Yes (+1 call) | No — needs a provider + `on_drift="relearn"` / repair mode | One step whose element genuinely drifted past every locator anchor |
| 3 | **Suffix re-plan** | Yes | No — same gate | A changed *tail* of the flow: re-author only the broken steps, keep the working prefix, re-cache |
| 4 | **Full re-learn** | Yes | No — last resort | Re-authoring the whole flow from scratch |

### Layer 1 — resilient locators (the workhorse, 0 LLM)

When a flow is learned or recorded, ultracua captures a **ranked hint set** for each element, then at replay
tries them in priority order and **the first hint that binds *exactly one* element wins**:

1. **Identity anchors** — `data-testid` → **role + accessible name** → placeholder → exact visible text → `id`.
2. **Two cross-checked guesses** — a fuzzy text-substring match **and** a short (≤5-hop) CSS path. The CSS path
   is trusted **only if the two agree**; a lone fuzzy match, or a CSS-vs-text disagreement, is *not* trusted.
3. **Neighbour anchor** — scope the role + name to the enclosing heading/landmark, to tell apart two otherwise
   identical controls.

The reason **role + name is so durable** is that the "name" is computed the way a screen reader (and
Playwright's `get_by_role`) computes it — `aria-labelledby` → `aria-label` → `<label for>` → a wrapping
`<label>` → placeholder / title / alt → visible text — and **deliberately not** the element's live `value`.
User-facing labelling stays stable even when the surrounding markup, classes, and ids churn.

There is **no LLM anywhere in this layer** — tiers 2 and 3 *are* a "0-LLM heal": they recover a rename or an
ambiguity with no model call. Implementation: [`src/ultracua/locators.py`](src/ultracua/locators.py),
[`src/ultracua/snapshot.py`](src/ultracua/snapshot.py).

### Layers 2–4 — LLM healing (opt-in, never for writes)

If **no** locator anchor binds a unique element, a plain replay **fails loud**. If you opted into recovery
(a provider is configured and you ran with `on_drift="relearn"` / repair mode), ultracua escalates in the
cheapest way that works:

- **Single-step heal** — hand the LLM a fresh page snapshot + the step's intent, let it re-ground *just that
  one step*, and **re-verify the healed bind is unique** before trusting it.
- **Suffix re-plan** — if a single step can't be fixed, re-author only the *remaining tail* from the current
  page, keep the working prefix, splice, and **re-cache** so the next run is 0-LLM again.
- **Full re-learn** — re-author the whole flow.

Two guarantees hold across all of these: **replay stays 0-LLM by default** (you only pay for the model when a
drift actually forces a heal), and **a healed/re-planned flow is re-cached**, so a one-time redesign costs one
LLM pass, not a model call on every future run. Implementation: [`src/ultracua/flow.py`](src/ultracua/flow.py),
driven via `on_drift` in [`src/ultracua/flows.py`](src/ultracua/flows.py).

---

## What survives, by change type

Assume an ordinary **read / click** step. **"0-LLM"** = the passive resolver re-binds with no model call;
**"fail loud"** = it returns nothing rather than guess, and escalates.

| Element change | Read / click step | Why |
|----------------|-------------------|-----|
| **id changed** | ✅ 0-LLM | no anchor keys on `id` alone |
| **class changed / re-styled** | ✅ 0-LLM | no anchor keys on class |
| **moved / re-parented** | ✅ 0-LLM | identity anchors are position-independent |
| **wrapper `<div>` added** | ✅ 0-LLM | the name comes from user-facing labelling, not structure |
| **sibling removed** | ✅ 0-LLM | identity anchors unaffected |
| **siblings reordered** | ✅ 0-LLM *usually* | a purely positional CSS could shift, but CSS is only trusted when it agrees with the text guess |
| **label renamed / lightly reworded** | ✅ 0-LLM *if* a stable anchor (e.g. the CSS path) still binds uniquely; **fail loud** if only a fuzzy text guess matches | a lone fuzzy match on a look-alike is never trusted |
| **`data-testid` changed** | ⚠️ escalates → heal | the strongest anchor is gone; other anchors may still bind |
| **role changed** (`<button>`→`<div>`) | ⚠️ survives only via test-id / id / CSS | role + name and the neighbour anchor no longer apply |
| **element genuinely removed** | 🛑 fail loud | it refuses to bind *some other* element instead |
| **two controls now share role + name** (ambiguity) | 🛑 fail loud, **unless** a stable heading/landmark disambiguates → then 0-LLM | it never silently actuates "the first one" |
| **full redesign** (many simultaneous changes) | ↪ escalates to LLM heal / re-plan / re-learn | **not covered by any measured evidence** — see limits |
| **any of the above on a WRITE step** | ✅ 0-LLM only if the target still binds uniquely **and** the enclosing form/section is unchanged; otherwise 🛑 **fail loud — never healed** | a write is never re-driven under uncertainty (double-submit risk) |

---

## The fail-loud boundaries (what ultracua deliberately does *not* heal)

These are the cases where ultracua **stops and tells a human** rather than risk a wrong action. All three
checks are 0-LLM and run *before* anything irreversible happens.

1. **Ambiguity → refuse.** Every important bind requires *exactly one* match. If a locator matches two or more
   elements, the resolver returns nothing — it **never** silently picks "the first one." Two identical forms on
   a page will not cause it to submit the wrong one.

2. **Writes are never healed or re-driven under drift.** Before a write (submit / place order / post) actuates,
   ultracua re-checks that the target still binds uniquely **and** that its enclosing *form/section* is
   unchanged. Any drift there → *"refusing to re-drive a write"*, no action. (Cosmetic churn **outside** the
   form — a banner, a badge — is tolerated; only the form's own scope matters.) The reason is **double-submit**:
   a first attempt might have committed the write before failing its confirmation check, so a blind re-drive
   could duplicate a real side effect. Writes carry idempotency keys and are never auto-retried under
   uncertainty.

3. **Wrong-but-plausible data → quarantine.** Even when every element binds perfectly, the *value* that came
   back can be wrong. ultracua's **value contracts** (see GUIDE.md → *Value contracts*) fail loud when a field
   changes type, goes null, flips sign, or a number moves too far from its own rolling history (a price silently
   going 129 → 40). That's not "healing" — it's a **sticky quarantine** that refuses every future run until a
   human investigates. An opt-in **LLM audit** covers the one case the deterministic checks can't see — a value
   that *creeps* a little each run — but it runs **out-of-band, never on the replay path**, and it is built so
   that **it can flag a flow for a human and can never bless one**: a finding may only ever quarantine, and only
   when pure Python independently corroborates it.

Beyond these: an **interstitial / CAPTCHA** wall is an escalation, not a heal (a machine can't proceed), and a
**cross-origin redirect** during learning is refused rather than cached.

---

## Why this design (the trade-off)

There are two ways to react when the page changed and a cached selector no longer fits:

- **Re-query an LLM to find the element** — adapt to *anything*, at the cost of a model call and the risk that
  the model binds a plausible-but-**wrong** element (or re-drives a write) without telling you.
- **Try harder deterministically, and if that fails, stop** — recover the common cosmetic/identity changes for
  free, and **refuse** the genuinely ambiguous or unsafe cases rather than guess.

ultracua is built firmly on the second philosophy, because its target is **unattended, repeated** work
(scheduled data pulls, internal portals, write flows) where a silent wrong action is worse than a loud failure.
The three rules the whole engine serves: **(1)** replay never calls an LLM on the happy path; **(2)** never
silently act wrong (ambiguity and conflicts fail loud; wrong-binds are held at exactly **0** on the drift
sandbox); **(3)** never re-drive a write under uncertainty.

The cost of that choice is honest: ultracua will **fail loud on some changes a more aggressive, always-ask-the-
LLM approach would have adapted to** — most notably a full redesign, where it escalates to an (opt-in) LLM
re-plan or re-learn rather than silently re-deriving the flow every run.

> For how this contrasts with a mainstream always-ask-the-LLM tool, see
> [docs/comparison-stagehand.md](docs/comparison-stagehand.md).

---

## Configuring healing

- **Plain replay is 0-LLM and needs no API key.** With no provider configured, only Layer 1 (resilient
  locators) is active; a drift past it **fails loud** — which is exactly what you want for a scheduled job that
  should page you rather than improvise.
- **Opt into LLM recovery** with a provider + `on_drift="relearn"` (CLI: `flow replay --on-drift relearn`).
  ultracua then escalates 0-LLM replay → single-step heal → suffix re-plan → full re-author, in that order, and
  re-caches the result so the *next* run is 0-LLM again.
- **⚠️ `on_drift="relearn"` is refused on an APPROVED flow.** Re-authoring rewrites the cached steps, so an
  unattended relearn would replay steps **no human reviewed** under the existing approval — and would
  re-baseline the value contracts a human blessed. So an approved flow fails loud instead, and recovery is a
  deliberate human action:

  ```bash
  uv run ultracua flow record --name daily-orders     # or: flow learn --name daily-orders
  uv run ultracua flow inspect --name daily-orders    # review what changed
  uv run ultracua flow approve --name daily-orders
  ```

  That sequence fixes **step** drift (a moved/renamed element). If the page **legitimately restructured** so the
  *data's shape* changed too, note that an approved flow keeps its blessed shape + value contracts **on purpose**
  — so re-authoring *while approved* will not adopt the new normal, and ultracua says so rather than letting
  "recorded + verified" imply otherwise. Re-baseline it explicitly, in this order:

  ```bash
  uv run ultracua flow unapprove --name daily-orders   # release the blessed baseline
  uv run ultracua flow learn     --name daily-orders   # (or `flow record` — a WRITE flow must use record)
  uv run ultracua flow approve   --name daily-orders   # bless the new one
  ```

  To let an unattended run self-heal instead, run the flow unapproved (`run-all --include-unapproved`).
- **Approval binds to the steps themselves, not just to the flow's name.** `flow approve` records a digest of
  the recipe you reviewed (each step's action, intent, locator, typed text, `mutating` flag, commit barrier and
  slot binding, in order). If anything later rewrites those steps — a heal or re-plan on an *unapproved* run, a
  `flow record`, a re-learn, a hand-edited cache file — replay **refuses** with `stale_approval` rather than run
  a recipe no human read. It is checked before the browser opens, so nothing has acted when it fires. `flow
  status` shows it as `APPROVAL STALE` and the MCP server stops advertising the tool. The way out is a human
  reading the new recipe:

  ```bash
  uv run ultracua flow inspect --name daily-orders
  uv run ultracua flow approve --name daily-orders
  ```

  Scope, stated honestly: this is **integrity, not authenticity**. The digest is unkeyed and sits in the meta
  sidecar next to the flow file it authenticates, so it catches the *system* re-authoring itself under a stale
  approval bit — it is not tamper-proofing against someone who can already write to your flow store. Signing
  is a separate, unbuilt concern.
- **Monitor a fleet** with `flow status` / `flow run-all` (a non-zero exit + a typed error on drift is the
  signal to alert on) and the cheap `flow canary` (does each flow still *start*?). See GUIDE.md → *Run a fleet*.

---

## Honest limits

These are the measured limits as of **drift-bench v2** ([`baselines/drift_v2.json`](baselines/drift_v2.json),
`benchmarks/drift_bench.py`). Several of them replace claims that were previously assertions.

- **0-LLM resilience is a CURVE, and it reaches zero.** The old headline — 12/12 cosmetic drifts survive —
  was true but saturated and uninformative: every drift bound at 0-LLM, so the number could only ever go
  down. v2 grades mutations by how many of the target's resolution *anchors* they destroy, and survival falls
  from **11/12 at k=1 to 0/6 at k=7** (all anchors gone, target still present). So: the resolver is highly
  resilient to one-or-two-anchor damage and **fails, loudly, when a change takes out everything it recorded**.
- **Heal now has a measured MECHANISM ceiling: 12/12.** Where the target is still present and every anchor is
  destroyed, the machinery recovers every time — re-grounds, verifies the effect, persists the locator,
  invalidates the approval. **But that is a ceiling, not a heal rate:** it is measured with a *perfect-vision*
  provider that reads ground-truth element identity off the fixture. `real recovery = ceiling × model
  accuracy`, and **the model-accuracy factor is still unmeasured** — the paid arm exists (`--provider
  anthropic`) and has not been run.
- **Suffix-replan recovers 1 of 9.** It only helps when the goal is reachable by *another route*; when the
  learned target is simply gone and nothing else leads to the goal, it correctly fails loud. Previously this
  tier had no number at all.
- **A write is never recovered — measured on 14/14 drifted write rows.** Heal refuses, replan skips it,
  `block_mutations` blocks it, and a write-firing repair is never cached. Zero double-submits, zero silently
  suppressed writes, zero writes at the decoy target.
- **There is one published wrong-bind, and it is exactly the positional-CSS case.** When a target whose only
  surviving anchor is a *positional* CSS path is removed and a same-tag sibling slides into its slot, the
  cached path re-matches the neighbour: replay actuates something no human approved, and if that element
  happens to reach a plausible page it reports **success**. Rate: 0.9% of rows. It is accepted rather than
  fixed because the obvious fix trades it for something worse — the same trust-a-unique-CSS rule is what
  recovers a *renamed* target, and demoting it was measured to turn two conflict cases into silent
  wrong-binds. It is named, counted, and gated as exactly that one row, never as a loosened tolerance.
  **This is also the strongest argument for how v2 judges outcomes:** it compares the actual ordered sequence
  of actuated elements against the learned one, so a mis-bind that still lands somewhere plausible is caught.
  v1 read only the final URL and would have scored this row a clean survival.
- **The pages are still synthetic.** v2 refutes *"every drift is a single cosmetic mutation on a ~2-section
  page"* — mutations now compound, span multiple pages, and hit form controls. It does **not** refute *"toy
  page"*: the HTML is still hand-written, and a benchmark over *real frozen page snapshots* remains unbuilt.
- **The intensity axis has no empirical prior.** `k` models `resolve()`'s decision surface, so the curve
  answers *how much damage does it take to break us* — a robustness score. It says nothing about *how often a
  real redesign does that much damage*.
- **No H9 layer is measured.** Value contracts, magnitude drift and the sampled judge are exactly as
  unmeasured as before; v2 drives the engine directly, so the `flows.py` trust layer is out of its reach.

*Everything above is verified against the code as of v0.61.0, and every number cited is reproducible
key-lessly with `uv run python -m benchmarks.drift_bench`.*
