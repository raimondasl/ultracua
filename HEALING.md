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
- **The one measured guarantee:** on a synthetic drift sandbox, **12/12 cosmetic DOM changes survive at 0 LLM
  with 0 wrong binds** — a real, CI-enforced number, but for *single cosmetic mutations on a toy page*, not a
  full production redesign (see [the honest limits](#honest-limits)).

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
   human investigates.

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

---

## Configuring healing

- **Plain replay is 0-LLM and needs no API key.** With no provider configured, only Layer 1 (resilient
  locators) is active; a drift past it **fails loud** — which is exactly what you want for a scheduled job that
  should page you rather than improvise.
- **Opt into LLM recovery** with a provider + `on_drift="relearn"` (CLI: `flow replay --on-drift relearn`).
  ultracua then escalates 0-LLM replay → single-step heal → suffix re-plan → full re-author, in that order, and
  re-caches the result so the *next* run is 0-LLM again.
- **Monitor a fleet** with `flow status` / `flow run-all` (a non-zero exit + a typed error on drift is the
  signal to alert on) and the cheap `flow canary` (does each flow still *start*?). See GUIDE.md → *Run a fleet*.

---

## Honest limits

- **The 12/12 drift-sandbox number is a toy-page guarantee.** Every drift in
  [`baselines/drift.json`](baselines/drift.json) is a *single cosmetic mutation on a ~2-section synthetic page*.
  It proves the resolver survives id/class/style/move/wrapper/rename **without an LLM and without a single
  wrong bind** — a real, key-less, CI-enforced result — but it is **not** evidence about surviving a real
  multi-element production redesign.
- **LLM heal and re-plan have no measured success rate.** Each is proven correct by exactly one hand-broken
  unit fixture (`tests/test_heal.py`, `tests/test_replan.py`), not a measured distribution of real drifts.
- **A drift *benchmark* with a realistic mutation distribution is still open** (mutate real fixtures, measure
  heal success + cost across many mutations) — it's tracked under Phase F in [ROADMAP.md](ROADMAP.md).
- **A purely positional CSS whose target is removed can, in principle, retarget a moved-in neighbour** — which
  is precisely why the "two guesses must agree" rule exists and why conflict cases fail loud instead.

*Everything above is verified against the code as of v0.57.0.*
