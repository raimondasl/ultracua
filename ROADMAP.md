# ultracua — Roadmap: from validated prototype to usable

The core thesis — **learn a browser flow once, replay it deterministically at 0 LLM, 2–7× faster**
— is validated on real authenticated sites across two distinct apps (see
[PLAN.md](PLAN.md)). What's left to make ultracua *usable by a developer for a real recurring
task* is product / reliability engineering, **not** another research breakthrough. This file
sketches the thinnest path there. See [STATUS.md](STATUS.md) for the honest maturity framing and
measured numbers this roadmap acts on.

## The target

A developer wants:

> *"Every morning, log into vendor portal X, pull the daily order count, hand it to my code
> (DB / Slack / CSV) — and tell me loudly if it broke."*

For that to be real they need to **define** the task, **verify** what was learned, **replay** it
returning data, and **trust** that a scheduled run either gives correct data or fails loudly —
never silently returns garbage.

## What already exists (this is assembly, not a rebuild)

| Need | Status |
|---|---|
| Learn → replay → self-heal → auto-relearn | ✅ `flow.run_cached` |
| Persisted flows | ✅ `FlowCache` (keyed JSON) |
| Safety: mutation gate, idempotency, interstitial, pacing | ✅ `safety.py` |
| Auth headers / pre-nav setup | ✅ `FlowSpec.headers` / `BrowserSession.set_extra_http_headers()` (+ `storage_state`) |
| Answer extraction → structured data | ✅ core `ultracua.extract` (`extract` / `Extraction`), used by the Flow API **and** the benchmark runner |
| Cross-language invocation | ✅ JSON-RPC daemon + Node client |
| Multi-provider LLM | ✅ |

The gaps *were* a thin **flow API**, **verify-before-trust**, and **fail-loud replay** — plus pulling
extraction and auth out of the benchmark runner into reusable core. **All shipped in Phase A (below).**

## The path (thinnest first)

### Phase A — "define a flow, run it, get data back" (the MVP)

Unlocks the core use case. **Done:**

- ✅ A small **`Flow` spec** (`FlowSpec` in `flows.py`): `name`, `start_url`, `goal`, `auth`
  (`storage_state` / cookies / `headers` / a `LoginSpec` sub-flow), `extract` (a schema or
  instruction for what to pull).
- ✅ Generalized the WebArena runner's two buried pieces into reusable core: **extraction** (core
  `extract.py` → return structured data) and **auth** (beyond Magento's special header —
  `storage_state` + a login sub-flow).
- ✅ CLI / daemon verbs: `ultracua flow learn <name>` → returns `{steps, extracted_data}` to
  **inspect**; `flow approve <name>` → marks it trusted; `flow replay <name>` → 0-LLM nav +
  extraction, **returns the data**, and **raises `FlowReplayError` on fingerprint drift / unresolved
  locator** instead of returning wrong data.
- *Reuse:* `run_cached`, `FlowCache`, the daemon. *New (all landed):* the `Flow` spec, generalized
  extract / auth, the `flow` verbs, replay-returns-data + raise-on-drift.

### Phase B — "trust it unattended" (reliability)

Unlocks scheduling without babysitting. **Done:**

- ✅ **Approval gate** — `learn` records a flow as unapproved; `approve()` marks it trusted;
  `replay(require_approved=True)` refuses to run an unapproved flow (a human verifies first).
- ✅ **Confidence via shape-consistency** — `learn` records the extracted data's structure; replay
  treats a change in that shape (vs the learned run) as drift — data-level drift detection on top
  of the page-fingerprint check.
- ✅ **Relearn-or-raise policy + fail-loud signal** — `on_drift="raise"` (default) raises a rich
  `FlowReplayError` a scheduler can alert on; `on_drift="relearn"` re-authors the flow instead.
- ✅ **Auth refresh** — `FlowSpec.login` (a `LoginSpec` with **env-sourced** credentials, or an
  async callable); on drift, replay re-logs-in to refresh the cookies and retries once.
  Credentials are read from the env at runtime and **never persisted** — the login isn't cached,
  and only the resulting `storage_state` cookies are saved.

### Phase C — "operate many flows" (lifecycle / ops)

Unlocks running a fleet of recurring jobs. **Done:**

- ✅ **Per-flow run history + health** — every `replay` records its outcome into the flow's meta
  sidecar (last run, last success, last error, run/success counts, consecutive failures).
  `health(spec)` (CLI `flow status`) summarizes each flow as `not-learned` / `never-run` /
  `healthy` / `failing` / `stale`.
- ✅ **Scheduling stays the developer's job** — documented pattern: cron / Task Scheduler →
  `ultracua flow replay` (exits non-zero on drift, so alert on failure; poll `flow status`).
  No scheduler built (by design).
- A thin web UI over `health()` is possible later but intentionally out of scope.

### Phase D — "breadth": NAVIGATE / MUTATE flows

Write-actions (submit forms, post, purchase). The mutation gate + idempotency-key + pacing +
interstitial detection already existed; this phase wired the missing **action-completion
verification**. **Done (thin slice):**

- ✅ **Write flows** — `FlowSpec.mutate` (a `MutateSpec`) marks a flow as a write and declares how
  to know it landed.
- ✅ **Action-completion verification** — after a write flow runs, a declarative `confirm_*` check
  (selector / page-text / URL, mirroring `LoginSpec`'s success check) must hold, or replay **fails
  loud** (`FlowReplayError`). A write is never reported as success just because a click didn't throw.
- ✅ **Never LLM-heal a write under drift** — a mutating step whose page fingerprint drifted now
  fails loud instead of diverting to the self-heal path (an LLM must never re-drive a write).
- ✅ **Opt-in idempotency precheck** — `MutateSpec.precheck_*` runs a cheap read-only pre-pass; if
  the end-state is already present, the write is **skipped** (`status="already-done"`). For one-shot
  writes (don't purchase twice); recurring writes leave it unset. No durable "committed" ledger (it
  would wrongly skip legitimate repeat writes).
- ✅ **Approval-gated by default** — a write flow refuses to replay until `approve`d, and refuses
  `on_drift="relearn"` (re-authoring would re-perform the write).

**Out of scope (documented):** per-step verification for multi-write flows; auto-recorded
postconditions; forcing the mutation gate on writes that `is_mutating`'s keyword heuristic misses
(type+Enter, navigate-to-POST, icon-only submit) — the whole-flow confirm check still catches those;
a HAR-asserted MUTATE benchmark (needs live containers).

## Beyond Phase D — longer-term directions

Phases A–D made ultracua *usable for a single recurring data-pull or write*. The benchmark evidence
(see [STATUS.md](STATUS.md)) says the next gains are **not** in making replay faster — replay is
already 0-LLM and 37–94× faster — but in **discovery reliability, replay fidelity on real pages, and
operability**. These phases are sketched, not committed; each lists the concrete use case it unlocks
and the gap it closes.

### Discovery-reliability push (research-backed — recommended *next*, before G/I)

A 2026 literature + web sweep (browser-agent SOTA, programming-by-demonstration, self-healing locators,
eval rigor) converged on what our own benchmarks already showed: **discovery — the LLM authoring a
working flow — is the bottleneck, and it's attackable with cheap, 0-LLM-preserving, learn-time-only
changes.** This push is recommended before Phases G/I: it's days of work, it hits the proven
bottleneck, and it de-risks both (write-safety hardens G; pass^k measurement let us *prove* the recorder
helped for I — it shipped and beat the LLM on the ceiling task, #64). Techniques below are directional — mapped to our code, not leaning on any one paper's
exact numbers.

**Tier 1 — do now (one cohesive push):**
- ✅ **Verify-by-replay before cache** + **oracle calibration** (#43) — after `_learn` authors a flow,
  replay it 0-LLM on a fresh session and only `cache.put` if it reproduces; the gate is calibrated on
  injected known-good / known-broken flows. Fail-loud made concrete. (Skips write flows.) [`flow.py:_learn`]
- ✅ **pass^k + per-step hazard metrics** (#43) — report all-k-succeed (not just the mean rate) and which
  step index first fails. [`benchmarks/variance.py`, `FlowReport.step_traces`]
- ✅ **Write-safety classification** (#44) — `is_mutating` → `classify_mutation`, a DOM-structural
  classifier (form **method**: GET=read, POST/PUT/DELETE/PATCH=write) with a keyword fallback. Catches
  icon-only / bland-intent submits the keywords missed and stops false-firing on reads like "submit the
  search". [`safety.py`, `snapshot.mutation_context`, `flow.py:_author_steps`]
- ✅ **Grounding hygiene** (#45) — reading-order snapshot sort (refs + agent view follow visual order;
  the fingerprint is order-invariant so a layout nudge isn't false drift); real accessible-name
  (`aria-labelledby` / `<label for>` / wrapping `<label>`), which makes the captured name match
  `get_by_role`; and the role/AccName JS unified into one shared source across the three blocks.
  Neighbor-anchor *capture* is deferred to pair with the Tier-2 Similo 0-LLM heal tier, where it's used.
  [`snapshot.py`, `locators.py`]

**Tier 2 — next:** ✅ **best-of-N authoring** (#48) — re-author up to N times, keep the first sample the
verify-by-replay oracle confirms (`run_cached(samples=N)` / `flow.learn(samples=N)`; adaptive early-stop;
benchmark via `variance --samples N`). READ-ONLY by design: it stops the instant a write fires *on the
wire* (a same-origin non-idempotent request, caught even when the recipe's keyword/structural classifier
misses an Enter-submit or formless POST), never re-authoring a write. Temperature is now plumbed so it
actually resamples. Measured (#50): MiniWoB 52%→60% and run-to-run variance **±13%→0%** at 1.55× cost —
the remaining 40% is a *capability ceiling*, not variance.

⚠️ **reflexion retry** (#51, **measured net-harmful — kept opt-in, OFF**) — summarize a failed attempt
into one LLM-written lesson (`flow._reflect`) and feed it to the next sample (in the authoring goal only,
never the cache key). The hypothesis was that learning-from-failure beats blind resampling on the
ceiling tasks. **#52 measured the opposite**: MiniWoB 60%→52% at +26% cost — the advice misdirects an
otherwise-clean re-roll. The implementation + the `--reflect` harness stay (useful to re-test on harder
benchmarks), but it's off by default. **The discovery loop is now measured-done**; the remaining 40% is
a capability ceiling, so further gains belong to **capability** (the now-shipped Phase I recorder, #64 —
which beat the LLM on the ceiling task — / grounding), not the loop. *Still in Tier 2 (replay/extraction-side, orthogonal to the ceiling):* a Similo-style 0-LLM heal
tier; type-aware comparators + a scripted-oracle control arm in the variance gate; a **flow-staleness
canary**.

✗ **0-LLM DOM list extractor** (attempted, **reviewed-unsafe — pulled back, NOT shipped**) — extend the
scalar pin to lists/dicts by inducing a row selector + field paths from a learned list, gated by a
learn-time in-page round-trip (only cache an extractor that exactly reproduces the learned list). It
passed a 14-test key-less suite, then an **adversarial review browser-reproduced three ways it silently
returns wrong data** — the one outcome this project forbids: (1) **incomplete** — a row that drops a
state class on replay (`even/odd`, `featured`, `sold-out`) is silently omitted (for a digest flow, the
dropped row is the alert you needed); (2) **wrong cell** — `:nth-of-type` field paths aren't
`:scope`-scoped or class-anchored, so a column insert / nested element makes every row read a
wrong-but-present value (shape-drift can't see it); (3) **phantom** — a footer / `Total` / ad row
matching the row class is silently included. **Root cause: a learn/replay verification asymmetry** — all
exactness was checked at *learn* time, then replay trusted a positional, count-unbounded, full-class-set
selector against a never-verified page (the *scalar* pin re-verifies at replay via `resolve(unique=True)`;
the list reader didn't). A safe version is a large rewrite (container-scoped rows + `:scope` class-anchored
cells + a replay-time **completeness** re-verify: every container child of the row tag must be a valid
row → else fail loud) that narrows coverage hard — only structured tables with an *anchorable container,
classed cells, distinct field classes, and no in-container footer*, **refusing bare-text lists entirely** —
and still carries a residual phantom-mimic risk. That's a thin reward (one cheap LLM extraction call saved
per replay) for a rewrite that can still silently mislead, so it was **pulled back**: lists keep using the
reliable one-call LLM extractor. **If 0-LLM structured reads are revisited, JSON-LD** (the site's *own*
declared `ItemList`/object data) **is the safer mechanism** — authoritative, no induction, no positional
selectors.

**Tier 3 — later:** parameterized typed slots; skill / workflow memory as a discovery prior (scales with
flow volume); Phase G proper (barrier-commit multi-write + deterministic action primitives:
upload / iframe / date); a local fast tier under constrained decoding (spike); the WebMCP spec fix.

**Skip / spike-only:** plan-then-execute (rebuild risk + reduces best-of-N sample diversity → spike);
Set-of-Marks on the vision tier (it's our no-DOM last resort, and SoM's marks come *from* the DOM → skip).

### Phase E — Operability & trust at scale ("run a fleet unattended")

A thin supervisor that runs saved flows on a schedule with structured logging + a `run_id`,
fail-loud alerting, an auto-relearn policy for reads, and a health view over `flow_health`;
pluggable secret resolution (Vault / 1Password / cloud) so credentials never touch disk even as
paths; SQLite cache + atomic/locked storage for many flows.

- *Enables:* "a team runs 50 recurring authenticated data-pulls and is paged only when one breaks."
- *Closes:* no observability, no scheduler, non-atomic storage, fleet view limited to a CLI.

### Phase F — Replay fidelity & adaptive resilience ("survive a real redesign")

**Done:** relevant-subtree preconditions (Phase D's precision mutation gate — drift is judged on the
target's enclosing form/section, not the whole-page fingerprint); and **suffix re-planning heal** —
when single-step heal can't fix a drifted step, re-author only the *remaining tail* from the current
page, keep the working prefix, splice, and re-cache, instead of a full re-learn. It's wired into the
engine's `auto`/`repair` modes and into the Flow API's `on_drift="relearn"` (which now escalates
replay → suffix-replan repair → full re-author), and covered by a drift-sandbox test
([tests/test_replan.py](tests/test_replan.py)) that breaks a mid-flow step and asserts the tail heals
while the prefix is preserved. Writes still refuse re-planning under drift (double-submit risk).

**Still open:** a drift-sandbox *benchmark* (mutate fixtures, measure heal success/cost across many
mutations, not just a unit fixture); optional embedding/visual anchor as an extra locator rank for
renamed-but-same-purpose elements.

- *Enables:* "a vendor portal redesigns its checkout and the flow heals the changed step."
- *Closes:* fingerprint over-sensitivity, single-step-local heal. Still open: accessible-name
  brittleness, a drift benchmark.

### Phase G — Action breadth & multi-step writes ("real transactions, not just reads")

✅ **Multi-write completion barrier — shipped (thin slice).** A flow can now perform MULTIPLE writes, each with
its own **per-write completion barrier** (`MutateSpec.step_confirms` → `CachedStep.confirm`, a `StepConfirm`):
replay verifies each write the moment it actuates — as an **absent→present transition**, so an already-true
confirm can't be a false pass — and **fails loud, NOT proceeding to the next write**, if one can't be confirmed
(`flow.py` `_replay` loop; reuses the per-step mutation gate + idempotency key + the shared
`conditions.condition_present`). Per-write checks bind by **commit order** (count-checked; `expects_intent`
required for >1 write to anchor each confirm to its write) and refuse to cache on a mismatch; multi-write
barriers are **record-only** (the recorder has per-write wire attribution; the LLM-learn classifier can miss a
write). A multi-write flow is **not** auto-retried after auth-refresh (no per-write resume yet). Adversarially
reviewed (a first cut was blocked for false-pass / double-submit holes; re-scoped to the barrier + the fixes).

- *Enables:* "submit a multi-page application, place a multi-item order, approve N pending items each with its
  own submit" — verified write-by-write, fail-loud, no silent run-past an unconfirmed write.
- **Still open (later Phase-G PRs):** per-write one-shot **resume** (skip an already-landed write on a re-run —
  deferred because a stateless page probe can't safely attribute prior page-state to a specific write); the
  recorder `--confirm-*` per-write CLI flags (the engine + API + `record()` attach are done); a `{status,
  writes:[...]}` summary; declarative **compensation/rollback**; **dynamic-N** writes; and the
  **action-breadth** verbs — file upload/download, multi-tab, iframes, date pickers, autocomplete.
- *Closes:* the single-outcome-write limit. Still open: resume, action verbs, compensation, dynamic-N.

### Phase H — Cost & latency floor ("cheap and fast at scale")

A local/open fast tier (Qwen / Llama-8B + constrained decoding) for discovery and extraction; a
**pinned-selector deterministic read** so recurring data-pulls are *literally* 0-LLM (design fork #1
below); per-flow cost budgets.

- *Enables:* "1000 recurring data-pulls/day at near-zero marginal cost and sub-second latency."
- *Closes:* the uncounted per-run extraction cost, no cost accounting, hard cloud-LLM dependency.

### Phase I — Distribution & product surface ("usable by non-builders")

✅ **A recorder** (learn from a human demonstration — directly attacks the discovery-failure
bottleneck) — **shipped & hardened** ([`recorder.py`](src/ultracua/recorder.py), `flows.record`, the
`flow record` CLI). Captures click / type / select / press(Enter) / scroll with high fidelity, surviving
same-origin navigation (a sessionStorage queue drained post-nav). Declared writes are **gated +
idempotency-keyed + approval-gated**; a formless write is tied to its commit by **per-write attribution**
(the init-script instruments fetch / XMLHttpRequest.send / navigator.sendBeacon to attribute each
non-idempotent request to the commit in its synchronous turn), and an un-instrumentable
(worker / service-worker / cross-realm) or ambiguous/deferred write is **refused, never cached ungated**.
A best-effort post-hoc **intent caption** (`caption_intents`) relabels each step's intent for self-heal
hints / inspect output / the keyword side of `classify_mutation`; replay stays 0-LLM.
**Still open:** a web UI over `flow_health` + a flow inspector/editor; a real service daemon (auth,
multiple browser contexts, streaming traces, OpenAPI); flow import/export + a registry.

- *Enables:* "a non-engineer records a flow by demoing it once; an ops team manages flows in a UI."
- *Closes:* "discovery failed → needs an engineer." Still open: CLI-only surface (web UI),
  single-flight unauthenticated daemon.

### Phase J — Evaluation & confidence ("prove it keeps working")

**Done:** CI (GitHub Actions, Linux + Windows, key-less); a $0 regression gate on replay-fidelity +
cost ([tests/test_regression_gate.py](tests/test_regression_gate.py)); and recorded/synthetic
live-path tests for all three adapters' `.complete()` glue (Anthropic cassette + OpenAI MockTransport
+ Gemini SDK-object) — which surfaced and fixed two real bugs the never-run-live path had hidden
(OpenAI `max_tokens` rejection; Gemini response-parsing casing).

**Still open:** a *standing* benchmark harness with **variance / error bars** run on a schedule
(today's real-LLM runs are single-shot — the 6/10-vs-8/10 swing is why this matters); providers
exercised against a real API (the live-path tests are key-less, so they replay synthetic responses).

- *Enables:* "every change is gated on replay-fidelity and cost regressions across a benchmark matrix."
- *Closes:* no CI, untested live LLM path, SDK-upgrade breakage risk. Still open: single-run
  benchmarks (no error bars).

**Suggested sequencing:** the near-term fixes in [STATUS.md](STATUS.md) → **E** and **F** first (they
turn "validated prototype" into "trustworthy unattended tool") → **H** and **I** as the scale/adoption
multipliers → **G** and **J** alongside as breadth and confidence demand.

## The MVP line

**Phase A + the fail-loud part of Phase B** = the minimum a developer could actually use:
*define → learn → verify → replay-returns-data → fails-loud.* Scheduling and the output sink are
theirs (cron + handle the returned data). Everything else is polish.

## Three design forks (worth deciding early)

1. **Extraction = LLM call or cached selector?** Today's extraction is one LLM call per run (cheap,
   flexible, but not literally 0-LLM for data tasks). Starting with LLM-extraction is fine; a later
   "cached deterministic read" option gets truly 0-LLM at the cost of brittleness. Offer both.
2. **What does `extract` look like to a developer?** A JSON schema (structured, validates) vs. a
   natural-language instruction (flexible). Probably both, schema preferred for the trust story.
3. **How trusted is "approved"?** Pure human-approve (safe, manual) vs. an auto-verifier that
   approves when confidence is high. Start human-approve; add auto-approve later.

## First step — ✅ shipped (the foundation everything above builds on)

**`Flow` spec + reusable extraction, decoupled from the WebArena runner** landed: `extract` moved
into core `src/ultracua/extract.py`, the `FlowSpec` dataclass + `flow.learn()` / `flow.replay()`
(returns structured data, raises `FlowReplayError` on drift) landed in `src/ultracua/flows.py`, and
the `ultracua flow` CLI exists. That made ultracua usable for a real data-pull outside the
benchmark — everything else above builds on it.
