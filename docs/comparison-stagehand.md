# ultracua vs. Stagehand

*A design-philosophy comparison, focused on self-healing / drift resilience, write safety, and data
correctness. Companion to [../HEALING.md](../HEALING.md).*

> **Dated & sourced.** Stagehand facts are as of **July 2026**, taken from Stagehand's own documentation
> (linked at the bottom); Stagehand is actively developed, so re-check before relying on specifics. ultracua
> facts are verified against the code at **v0.57.0**.

---

## Read this as philosophy, not a scoreboard

These two projects are at very different stages, and it would be misleading to pretend otherwise:

- **[Stagehand](https://github.com/browserbase/stagehand)** is a mature, widely-adopted, Browserbase-funded
  framework — first-class TypeScript **and** Python, a large community, production-hardened, with managed cloud
  browsers behind it.
- **ultracua** is, by its own [README](../README.md), *"a usable prototype of a real pattern, not a turnkey
  product"* — a solo project with no cloud, no anti-bot infrastructure, and a much narrower scope.

So this document compares **how each is designed to behave**, not which one is more finished. On raw maturity,
ecosystem, and breadth, Stagehand is ahead — that is stated plainly in [Where Stagehand leads](#where-stagehand-leads).

## The shared idea

Both converged on the same core insight, and it's worth naming: **learn (or LLM-drive) a browser flow once,
cache it, then replay it near-instantly at $0 in LLM tokens.** Stagehand caches an LLM-resolved action after the
first run; ultracua learns and caches a flow up front. Either way, the *repeat* run is fast and model-free. The
interesting differences are everything *around* that shared idea.

## The core difference: the reflex when an element drifts

This is the crux. When the page has changed and a cached step no longer fits, there are two possible reflexes:

- **Re-query an LLM to re-find the element** — adapt to almost anything, at the cost of a model call and the
  risk of binding a *plausible-but-wrong* element without saying so.
- **Try harder deterministically, and if that fails, stop** — recover the common cosmetic/identity changes for
  free, and **refuse** the genuinely ambiguous or unsafe cases.

**Stagehand takes the first reflex; ultracua takes the second.**

| | **Stagehand** | **ultracua** |
|---|---|---|
| **What a step remembers** | An LLM-resolved action, cached. The cache key is the natural-language instruction + the page's accessibility tree + URL + options; it stores the resolved selector plus a page fingerprint. | A **ranked *set* of resilient anchors** per element — `data-testid`, role + accessible-name, visible text, a short (≤5-hop) CSS path, and a neighbouring landmark — not a single selector. |
| **Authoring** | Developer writes natural-language calls inline: `act("click add to cart")`, `observe`, `extract`, `agent`. | LLM authors the flow once (`learn`), or a human records it (`record`); then a **human approves** it before any unattended run. |
| **Cache-hit replay** | Passively compares the current page fingerprint to what the entry was recorded against; if it clears a confidence threshold, replays the selector — **0 LLM**, ~10–100×. | Resolver re-binds from the anchor set, requiring **exactly one** match — **0 LLM**. |
| **On drift** | Fingerprint check fails → cache miss → **re-queries the LLM** to re-find the element (this *is* the "self-heal"). | The passive resolver tries the **other anchors first, still 0 LLM** — an id / class / style / move / wrapper / rename usually re-binds with **no model call at all**. Only if *every* anchor fails does it optionally fall back to an LLM heal → suffix re-plan → re-learn (opt-in; off on a plain replay). |
| **Ambiguous match (>1 element)** | Not documented as a hard stop. | **Fails loud** — never silently actuates "the first one." |
| **Reflex** | **Availability-first** — adapt to anything by asking the model again. | **Safety-first** — recover the cheap cases deterministically; **refuse and escalate** the uncertain ones. |

**Practical upshot:** Stagehand heals *more kinds of change* — including a genuine redesign — because it always
has the LLM to fall back on; the price is a model call and the possibility of a silently-wrong bind. ultracua
heals the common cosmetic/identity changes *for free* (it stored a set of anchors, not one selector, so an
id/class change rarely even needs the fallback) and deliberately **stops** on the ambiguous or unsafe cases
instead of guessing. See [../HEALING.md](../HEALING.md) for the change-by-change table.

## Where ultracua is deliberately different

These are the things ultracua is *for* — and Stagehand's documentation doesn't describe them, because
Stagehand's guarantees are about *action* replay, not *write* safety or *value* correctness:

- **Write safety.** A write (submit / place order / post) is **never re-driven under drift** — a first attempt
  might have committed before failing its confirmation, so a blind re-drive could double-submit. Writes carry
  per-row **idempotency keys**; exposing a write to an AI assistant over MCP requires a **human confirm** and
  provably **cannot fire twice**. Stagehand documents no human-approval, idempotency, or double-submit
  protection.
- **Value / semantic correctness.** Even when every element binds perfectly, the *value* can be wrong. ultracua
  **fails loud** on wrong-but-plausible data — a field that went null, a list that collapsed, a price that
  silently went 129 → 40 (H9 value contracts, layers 1 & 2). This is a check on the *data*, not the *action*.
- **Fail-loud as a contract.** Ambiguity, unconfirmed writes, and drifted forms **stop and page a human**. On
  ultracua's drift sandbox, `wrong-binds` is held at exactly **0** in CI — the "never silently do the wrong
  thing" guarantee, enforced.

## Where Stagehand leads

Being honest the other direction — Stagehand is clearly ahead on:

- **Maturity & ecosystem** — funded, widely adopted, production-hardened, first-class TS + Python, a large
  community and documentation base.
- **The `agent` primitive** — open-ended, LLM-driven tasks that ultracua doesn't attempt. ultracua is for
  *stable, repeated* flows, not "do anything."
- **Cloud & anti-bot infrastructure** via [Browserbase](https://www.browserbase.com/stagehand) — CAPTCHA
  solving, agent identity, CDP-screencast session replay, prompt observability, zero-infrastructure deployment.
  ultracua has none of this (local Playwright/CDP only).
- **Broader drift adaptation out of the box** — the always-available LLM fallback means less "fail loud"
  friction on messy, adversarial real-world sites.

## When you'd reach for each

- **Stagehand** — you want a mature, general AI browser agent that adapts to almost anything; you're fine
  paying a model call when a page shifts; you want managed cloud browsers and anti-bot handling. Best for
  **broad automation and open-ended tasks.**
- **ultracua** — you have a *stable, repeated, authenticated* flow (a scheduled data pull, an internal portal,
  a real write) that must run **unattended**, at **$0 LLM per run**, and must **fail loud rather than ever act
  wrong or double-submit**. Best for **the narrow, high-stakes, repeated slice** — where a silent wrong action
  is worse than a loud failure.

## One honest caveat that cuts against ultracua

ultracua's resilience is *measured* only on a **12-drift synthetic sandbox** (12/12 cosmetic changes survive
0-LLM, 0 wrong-binds). Stagehand's LLM-fallback approach has been exercised against far more real-world sites.
ultracua's "deterministic + fail-loud" story is real and CI-enforced, but its coverage of a genuine production
**redesign** is unproven — exactly the drift-*benchmark* gap flagged in [../HEALING.md](../HEALING.md) and under
Phase F of the [roadmap](../ROADMAP.md). Don't read "12/12, 0 wrong-binds" as "survives a redesign."

---

## Sources

- Stagehand — caching best practices: <https://docs.stagehand.dev/v3/best-practices/caching>
- Stagehand — `act()`: <https://docs.stagehand.dev/v3/basics/act>
- Stagehand v3 changelog: <https://www.browserbase.com/changelog/stagehand-v3>
- Browserbase / Stagehand overview: <https://www.browserbase.com/stagehand>
- Stagehand repo: <https://github.com/browserbase/stagehand>

*ultracua behavior is verified against the source at v0.57.0; see [../HEALING.md](../HEALING.md) for the
implementation citations.*
