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

---

# Innovation horizons — 2026-07 research sweep

*Researched 2026-07-02 (v0.44.2): a repo gap-map + a 10-axis web sweep (competitive landscape,
record-&-replay research, local grounding models, agentic-web interop, flow generalization,
enterprise trust, speed/infra, action breadth, product distribution, AI frontier), merged/deduped,
then every surviving candidate **adversarially verified** — sources fetched and checked, feasibility
tiers challenged against the actual code, plans mapped to real modules. 29 agents; 27 weaker/duplicate
candidates dropped or folded (listed at the end). These are researched candidates, not commitments.*

**The landscape headline.** The field converged on ultracua's core thesis: Stagehand v3 caches
actions and replays sub-100ms at 0 LLM, browser-use shipped `workflow-use` ("RPA 2.0", record →
deterministic replay, explicitly not production-ready), and OpenAI shipped Codex Record & Replay
(June 2026 — but its replay is adaptive-LLM, not deterministic). **Learn-once/replay-deterministic
is no longer a differentiator by itself; write safety, verify-by-replay, fail-loud, and
auditability are** — and every incumbent is weak on exactly those. Meanwhile a cooperative-agent
infrastructure layer went live in 2026 (WebMCP Chrome origin trial, IETF Web Bot Auth + Cloudflare
signed agents + AWS WAF support, Visa TAP / Mastercard Agent Pay / ACP payment rails), and a
deterministic, auditable replayer is structurally the best-placed architecture to exploit it.

**Classification** (whole-candidate, post-verification):
- **Certainly achievable** — clear engineering on existing patterns. *Honest finding: no whole
  candidate survived verification at this tier — in every case the guarantee-bearing scope
  (write safety, fail-loud coverage, redaction) is the real work. Certainly-achievable **cores**
  are marked inside candidates; below-the-innovation-bar certain tasks are listed at the end.*
- **Achievable with focused effort** — known path, real integration/safety work (12 candidates).
- **Ambitious / research-adjacent** — frontier, needs experimentation; staged so the shippable
  rungs aren't hostage to the research rung (3 candidates).
- **Highly experimental** — open research problems; fail-safe staging mandatory (1 candidate).

## Cross-cutting prerequisites the sweep surfaced (fix once, deliberately)

Several candidates independently collide with the same six codebase facts — each is a small,
high-leverage fix that should land *before* (or as the first slice of) the features that need it:

1. **`safety.py:idempotency_key` basis is run-invariant** — `sha256(scope|step_index|intent)`
   mints the *same* dedupe key across runs. Parameterized slots, write-loops, and signed mandates
   all need a redesigned basis (slot/row/mandate-aware, canonicalization test-pinned): today 500
   distinct payloads would share ONE key (silent write suppression), and a naive per-run key would
   double-write on retry. One deliberate redesign, not three ad-hoc patches.
2. **`flows.py:_load_meta` resets `FlowMeta` on unknown keys** — a version-skew reader silently
   wipes run history, and would wipe any new trust-bearing flag (quarantine, attestation). Fix
   with an `_only_known`-style filter before ANY trust state lives in `FlowMeta`.
3. **`extract.py`'s 12k-char `innerText` truncation is silent** — a truncated page can yield a
   syntactically valid, silently incomplete extraction that passes shape checks. Must be reported /
   fail-loud; three candidates (value contracts, MCP tools, monitoring) inherit this hole.
4. **`webmcp.py` speaks a speculative interface** (`window.webmcp`/`listTools`) that no real site
   exposes — the actual Chrome origin-trial API is `navigator.modelContext` (registration-side, no
   page-visible enumeration; interception via init-script is required). Also: `webmcp_call` steps
   get no mutation classification or precondition capture today, so a mutating tool call would slip
   the write gate. Rewrite before building anything on WebMCP.
5. **The perception stack is top-frame / light-DOM everywhere** (snapshot, locators, recorder,
   mutation-gate scope hashing — zero `frame_locator`/shadow handling in `src/`). iframe/shadow
   work is a whole-stack change that forces **one deliberate `SCHEMA_VERSION` bump** (= fleet-wide
   relearn); batch every fingerprint-basis change into that single bump.
6. **`CachedStep.text` persists typed values in plaintext** — a recurring dependency for secret
   slots, evidence packs, telemetry export, and narration. A capture-time classification/redaction
   pass pays off across five candidates.

## Tier: achievable with focused effort

### H1. Attested 0-LLM replay + signed evidence packs

**What.** A signed-attestation layer over existing replay: a DSSE/ed25519-signed flow manifest at
`approve` time (flow+spec content hash, locator chains, recorder-attributed write signatures, an
egress origin allowlist captured at record time); a replay-time egress observer (block-mode for
read-only flows; attest-mode — observe, let write barriers complete, then fail loud — for writes);
a per-run attestation with the **measured** LLM-call count from `Router.totals` — `SEALED`
(provider=None, pinned/no-LLM extraction, heal disabled → provably 0 calls) vs `ATTESTED` (exactly
one non-actuating extraction call); a hash-chained evidence bundle (StepTraces, post-settle
screenshots, redacted HAR) with an offline verifier CLI.

**Unlocks.** Enterprises that ban LLM browser agents (banks, healthcare, government): approve a
signed manifest once, run unattended scheduled replays with a cryptographic receipt that zero LLM
calls occurred and zero non-manifest origins were contacted — prompt injection structurally
impossible *for SEALED replays* (the honest scope). Positioned as SOC 2 / EU-AI-Act-Article-12-*style*
audit evidence (the technical standards are still drafts — don't sell compliance).

**Plan.** (1) `attest.py`: canonical hashing + DSSE/ed25519 + offline verify, signed at
`flows.approve` into a new sidecar (not `FlowMeta` — prereq #2). (2) Origin capture in
`recorder._watch_request` (context-scoped, so iframe origins are at least observed). (3) Egress
observer at `flow._replay`'s existing watcher seam — enforce-mode **refused** for mutating flows.
(4) Attestation from measured `Router.totals` + hash-chained `on_step` traces + `record_har_path`
with post-capture redaction. (5) `FlowSpec.attest` levels; sealed flows convert heal into
propose-a-diff-for-re-approval.

**Risks.** Egress block-mode mid-write *creates* the partial-write hazard (structurally forbid
enforcement after the first mutating step). Overclaiming "0 LLM" makes the attestation itself
silently wrong — levels must be computed from measured totals, never declared. Redaction is a
gating prerequisite (a signed unredacted bundle is a permanent copy of leaked data). Allowlist noise
(CDN/A-B churn) → chronic false refusals → users disable it (needs eTLD+1 normalization + re-sign
tooling). Key custody must be documented honestly (same-box key ≠ insider-proof).

**Why now.** OpenAI conceded browser-agent prompt injection "is unlikely to ever be fully solved"
(Dec 2025) and shipped Lockdown Mode (Feb 2026); Anthropic measured a 23.6% un-mitigated attack
success rate ([claude.com/blog/claude-for-chrome](https://claude.com/blog/claude-for-chrome)); EU AI
Act Article 12 logging enforcement begins Aug 2026. *Certainly-achievable core: the crypto layer
(hash, sign, verify, hash-chain) — the repo already has `record_har_path`, `on_step`, and recorder
write attribution as ready evidence streams.*

### H2. Flows-as-tools everywhere (MCP server first)

**What.** Staged. Stage 1: an MCP server (official Python SDK, stdio) that registers every
**approved READ flow** as a typed tool (output schema from `FlowSpec.extract_schema`/`FlowMeta.shape`)
dispatching to `flows.replay(require_approved=True, on_drift="raise", check_shape=True)` — never the
raw daemon `run`, which bypasses the safety gates by contract. Stage 2: streamable HTTP + write
flows default-deny behind MCP elicitation + per-flow single-flight locking + a completed-run ledger.
Stage 3 (blocked on typed slots + the Phase-I auth daemon): parameterized inputs and per-caller
credentials. A2A card / n8n node / LangChain wrappers follow as thin packages.

**Unlocks.** Every MCP client (Claude, Cursor, VS Code, ChatGPT…) invokes one deterministic,
verified tool call instead of LLM-orchestrating ~30 `playwright-mcp` primitives per run — the
inverse of the per-step MCP browser model, and the widest distribution surface available. Honest
cap: zero-argument tools (one per literal flow) until slots land; extraction flows still cost the
server one LLM call unless pinned.

**Plan.** (1) `mcpserver/server.py`: enumerate `list_specs`, filter approved, register read tools.
(2) Typed error taxonomy (`DriftError`/`ShapeDriftError`/`AuthExpiredError`/`EscalateError`) with
machine-readable do-not-retry flags — an outer LLM must never paper over a drift. (3) Write safety:
`--expose-writes` opt-in + elicitation-or-refuse + per-flow-key mutex + request-id ledger (a client
timeout retry must not re-fire a write). (4) `flow serve-mcp` CLI; pin cache/spec roots absolute.
(5) Never register learn/approve/record as tools (no self-approving agents).

**Risks.** Retry-happy outer agents double-firing writes (the ledger + default-deny are the rail —
the idempotency header is not sufficient). Confused deputy: until the auth daemon exists every
caller rides the operator's identity. Hacking parameterization in at the MCP layer would bypass
flow identity + verify-by-replay + mint colliding idempotency keys — wait for slots. Typed output
schemas amplify trust in silently truncated extractions (prereq #3 first). Same-flow concurrency
needs the new mutex (none exists today).

**Why now.** ~10K public MCP servers, 41% of surveyed orgs in production, MCP under Linux
Foundation governance; `playwright-mcp` (34k+ stars) proves demand for per-step browser tools —
whole-verified-flows-as-tools is the empty quadrant. *Certainly-achievable core: stage-1 stdio
read-flow server.*

### H3. Typed flow templates (auto-parameterization)

**What.** Slots for recorded/learned flows: the recorder mines candidate variables from
fill/select/press steps on native controls and captures their **legal domains** from live site
metadata (`<select>` options, `pattern`/`required`/`min`/`max`/`datalist`) via the shared
`_SPECOF_JS`; one opt-in record-time LLM pass names/types slots (captioner-shaped, off the replay
path); the flow publishes a JSON-Schema input contract with 0-LLM pre-flight validation. A
**value-independence audit** refuses to templatize (fail loud) when the demo value leaked into any
post-fill locator, precondition basis, or navigate URL. Slot values fold into the idempotency-key
basis for writes (prereq #1); secret slots are env-resolved, never serialized. Verification:
two-binding verify-by-replay for READ templates; **prefix-only** for writes (the commit step can
never be re-run — this limit is documented, not papered over).

**Unlocks.** Record "submit expense report" once → run it for N CSV rows via a new `run_batch`
verb, each row pre-flight-validated, each write minting a distinct row-keyed idempotency key, each
row failing loud independently. Converts the recorder's measured 9/9 capability-ceiling win into
per-input reuse — the gap-map's #1 real-world coverage limit (flows are input-frozen today).

**Plan.** (1) `CachedStep.slot` + `FlowSpec.slots` (additive, no schema bump). (2) Domain capture
in `_CAPTURE_JS` change/keydown listeners via `_SPECOF_JS` (learn/record parity by construction);
write-flow slots require explicit human confirmation — never auto-lifted. (3) The audit refusal in
`flows.record` beside the cross-origin/undeclared-write refusals. (4) `replay(spec, params)` →
pre-flight → substitute at the fill/select/press sites; `flow_key` unchanged (values never enter
identity). (5) Idempotency rebase + slot-schema hash into the approval gate (schema change →
refuse until re-approved). (6) `run_batch` on the run-all pattern with per-row resume bookkeeping.

**Risks.** Idempotency both ways: one shared key silently suppresses rows 2..N; non-deterministic
canonicalization double-writes on retry — the derivation must be test-pinned. Value-echo pages
(review screens rendering "{invoice_id}") change the precondition scope → a safe but 100%-dead
template; the audit must catch it at authoring (weakening the fingerprint basis instead would
weaken the write gate). A wrongly lifted write slot (payee, account) is a money-moving injection
surface — closed enums by default, human confirmation mandatory. Secret values transit the
recorder buffer/captioner — mask at capture classification (prereq #6). Non-native widgets
(React-Select, custom date pickers) produce no domain metadata — v1 refuses, not silently skips.

**Why now.** browser-use `workflow-use` auto-extracts variables (pre-production), Codex R&R takes
per-run inputs (adaptive-LLM replay), Stagehand parameterizes but silently cache-misses to LLM on
drift — the deterministic + typed + fail-loud quadrant is empty.

### H4. In-profile capture & replay (extension recorder + CDP attach)

**What.** Record and replay in the user's real Chrome profile via a minimal MV3 extension that is a
**dumb CDP relay** (`chrome.debugger` proxied over localhost to Python — the architecture
`playwright-mcp --extension` already ships as Apache-2.0 reference). *Not* Chrome 144
`--autoConnect` + stock `connect_over_cdp` — that path is broken today (Chrome 136+ ignores
remote-debugging on the default profile; `playwright#40027` unresolved). The whole recorder
pipeline (capture JS, wire attribution, refusals) rides the relay unchanged, and all capture JS
stays in the Python package (injected at runtime), so extension review decouples from releases.
Reads first; writes over attach default-deny behind an explicit opt-in.

**Unlocks.** Flows behind corporate SSO/Okta/2FA, hardware keys, VPN-bound sessions,
extension-dependent sites: the user authenticates once in their own browser; ultracua inherits the
live session and real-profile fingerprint with **no storage_state export** (kills today's plaintext
cookie files for those flows). Recording SSO sites in-profile sidesteps the cross-origin refusal
because login happened before the demo started.

**Plan.** (1) `extension/` (debugger + nativeMessaging perms) + `attach.py` relay emulating the
browser-level CDP handshake. (2) `BrowserSession` attach mode (`cdp_endpoint`; hard-fail
`storage_state`/HAR combos; verify header-scoping or refuse mutating flows). (3) Thread through
`flows.record`/`record_demo` — the init-script + context-scoped watcher ride CDP domains
`chrome.debugger` exposes. (4) Profile-login precondition probe (reuse `LoginSpec` success checks);
`refresh_auth` refuses over attach — never drive a login form in the user's real profile.
(5) Debugger-detach (user clicks the infobar Cancel) between actuation and confirm = unconfirmed-
write failure via the Phase-G barrier. (6) Fake-relay key-less tests.

**Risks.** Concurrent human input mid-write → double-write (detectable, not preventable — hence
default-deny writes). The idempotency header may stamp the user's own traffic (verify tab-scoping
or refuse mutating flows). Platform risk is existential: Google tightened profile-CDP twice in 14
months. Enterprise policy (`ExtensionInstallBlocklist`, `DeveloperToolsAvailability=2`) blocks
exactly the Okta-corporate target market. Profile-pinned fingerprints (ad-blocker/Grammarly DOM)
break flow portability — flows are profile-pinned, documented.

**Why now.** Chrome 144 shipped consent-gated agent attach; Claude-in-Chrome validated the
extension+CDP trusted-input architecture; `workflow-use` validated extension recording (4.1k stars,
explicitly not production-ready). This feeds the authoring modality that measured 9/9 vs the LLM's 4/9.

### H5. Dry-run replay (shadow writes with held-commit review)

**What.** A replay mode (no heal, no replan) where a network arbiter holds every write: during a
mutating step's existing act window (the same bracket the idempotency header + write-settle already
use), any `is_write_request` match is intercepted (`context.route`), dropped, fulfilled with
minimal synthesized success, and recorded as a `HeldWrite` (step, intent, method, URL, body). Any
write **outside** an open window — including sub-frame writes — aborts loudly. Uninterceptable
channels are refused, not risked: WebSocket frames abort, service workers are blocked, sendBeacon
is double-covered by a JS drop-patch (the recorder's `recordWire` inverted). Confirm barriers
report "held — unverifiable"; the report states **"N of M writes reached"** and labels post-held
steps unrepresentative. Raw bodies are shown to the human, never persisted.

**Unlocks.** Review the exact POST bodies a new / drift-relearned / imported write flow *would*
send — before `approve()` — on sites with no staging environment; a CI pre-flight for registry
flows; a tripwire that a flow touches channels it can't safely gate. Because replay is
deterministic and the mutation gate still runs, the held-write report *predicts* the real run in a
way a stochastic agent's shadow sample cannot.

**Plan.** (1) `dryrun.py` `DryRunArbiter` (route handler + belt-and-braces init-script patch).
(2) Thread `dry_run` through `run_cached` → `_replay_step`, opening the window exactly where the
idempotency header is set/cleared. (3) Barrier → "held" outcome + representativeness labels.
(4) `flows.dry_run` verb — no approval gate (it's the pre-approval artifact), never records health.
(5) Key-less tests against a local server asserting a held write **never arrives**.

**Risks.** The catastrophic inversion — a leaky "dry" run that actually writes (a beacon quirk, an
SW fetch): the happy path is easy, the *guarantee* is the work; anything unprovably held must
abort. Partial coverage approving a subtly wrong picture (write #2's body computed from fake
write-#1 state) — the N-of-M honesty is mandatory. Raw bodies vs secrets-never-persisted (ephemeral
display only; any diff baseline redacted/hashed, advisory-only). First-party analytics POSTs
false-abort → allowlist temptation, where one wrong entry hides a real write.

**Why now.** Approval artifacts for agent writes are the enterprise adoption blocker (Chrome
Enterprise ships a "double-check safety system" for exactly this); pause/approve/resume-from-log is
a platform pattern (Cloudflare Agents SDK). Natural elevation partner for the roadmapped flow
import/export (safe first-run of imported flows).

### H6. Drift-repair bot (canary-triggered heal PRs)

**What.** Offline, failure-triggered repair that emits **reviewable heal PRs** instead of runtime
patches. Tier 1: 0-LLM HybridSimilo relocalization — stored element-property snapshots scored
against a new page-wide property harvest (the runtime snapshot is viewport-only/80-element-capped
and cannot feed this). Tier 2: per-element visual memory (crop+bbox+caption captured at pin time)
re-grounded by a local pointing model, hit-tested back to a DOM node, and required to round-trip
`resolve(unique=True)` — or refuse. READ flows: N verify-by-replay runs as PR evidence. WRITE
flows: prefix-only verification + forced back to unapproved (replaying a write to verify it =
double-submit; the evidence is honestly weaker). Healed flows re-enter the cache only via verify +
human approval; the replay path stays untouched and 0-LLM.

**Unlocks.** Read-fleet maintenance becomes review-queue triage — most DOM-drift breakages arrive
as evidence-bundled, confidence-ranked repin proposals ("Dependabot for browser flows"). Visual
memory lets reads survive redesigns that break every DOM locator at once.

**Plan.** (1) Similo property set into `_SPECOF_JS`/`LocatorSpec` (additive; learn+record parity
free). (2) `heal/harvest.py`: page-wide single-evaluate harvest — never called from replay.
(3) `heal/similo.py`: weighted scorer emitting (candidate, confidence, top-2 margin); narrow margin
= refuse. (4) Crops in `<key>.assets/` + optional `CachedStep.visual`; tier-2 regrounder as a
`GroundingProvider` impl. (5) `flows.heal` → `HealProposal` bundle; `heal-approve` applies under
the meta lock; write flows → unapproved. (6) Queue from canary/run-all failures; CLI-first.

**Risks.** 98.8% relocation accuracy ≈ 1-in-80 heals binding a plausible-but-*wrong* element — the
bot industrializes exactly the failure ultracua forbids unless thresholds + margins + refusal
branches are hard; pinned-read value-continuity checks help. Benchmark transfer: Similo numbers
come from version-upgrade suites, not full redesigns. iframe/shadow elements are unhealable until
the action-breadth pack lands (fail loud). Review fatigue → rubber-stamping (the human gate must
not become theater, especially on write repins). *Note: implementing improved-Similo-only captures
most of HybridSimilo's value with less machinery; Tier-1-for-reads alone is the certainly-achievable
core and doubles as the roadmapped "Similo-style 0-LLM heal tier" at `locators.resolve`.*

**Why now.** HybridSimilo (98.8%, 23× larger benchmark, [arXiv 2505.16424](https://arxiv.org/abs/2505.16424));
Playwright v1.56 shipped a native LLM Healer agent — the 0-LLM + human-gated + evidence-bundled
lane is empty.

### H7. Deterministic control flow (repeat-over-list, paginate-until, branch-on-state)

**What.** Extend the cached-flow IR from a linear step list to a small **closed** set of typed
nodes: `LoopStep` (row-template container locator + body resolved within each row + declared
cardinality bounds + row-key dedupe + hard caps), `PaginateStep` (next-control + deterministic
termination predicate + monotonic-progress + max-pages), and branch-on-state predicates. Structure
is synthesized once at discovery; replay executes it 0-LLM via an interpreter with fail-loud
guards. **Co-requisite** (the proposal's honest surprise): row-scoped 0-LLM cell pins — the
constrained, template-verified successor of the withdrawn list extractor — because control flow
alone leaves per-page extraction as one LLM call. v1 is read-only; write-loops are phase 2,
hard-gated behind per-iteration resume.

**Unlocks.** The bulk-read task class that is structurally inexpressible today: "extract every
order across all pages", per-row column reads, paginated aggregation — deterministic, 0-LLM, with
cardinality/dedupe/termination guards converting silent partial scrapes into loud failures. Phase 2
unlocks "approve every pending request matching X". This also partially explains the measured
discovery ceiling on aggregation tasks — structural, not just LLM capability.

**Plan.** (1) Discriminated-union nodes in `cache.py` (additive; unknown node types refuse loudly).
(2) Interpreter refactor of the linear replay loop; suffix-replan restricted to top level (mid-loop
failure fails loud). (3) `resolve(root=)` for within-row binding (`unique=True` = unique-within-row).
(4) Row-pin authoring verified at learn (learn-run vs replay-run row sets must match); fingerprint
the list container/filter controls as a loop precondition (a changed default sort must fail loud,
not return a well-shaped wrong row set). (5) Discovery emits loop nodes read-only. (6) Phase 2:
idempotency by row-identity (not index — prereq #1), per-iteration confirm templates + declared
write-cardinality bounds, per-iteration resume as a hard prerequisite.

**Risks.** Filter/sort drift → structurally valid, guard-passing, *wrong* row set (the container
fingerprint is the defense). Idempotency collision across iterations (one key for N writes →
suppression) vs index-keying (retry double-writes) — row-identity basis only. A loop failing at
iteration k re-fires writes 1..k-1 on re-run — write-loops without resume multiply today's
acceptable single-write risk by N. Virtualized/infinite-scroll lists (DOM recycling) must be
detected (duplicate/missing row keys) and refused, never scroll-and-guessed. No nested loops in v1
or the gate/confirm/health semantics stop being analyzable.

**Why now.** "Agentic compilation" of pagination/loops into deterministic blueprints runs at
<$0.10 vs ~$150 for 500 agent iterations; program-shaped skills beat text skills (+11.3%) — and
Rousillon/Helena (UIST 2017) proved deterministic loop-folding a decade ago.

### H8. Action-breadth verification pack (files + tabs + deep DOM)

**What.** Staged breadth with verification contracts. (1) **Files**: `download`/`upload` verbs —
`expect_download` + `save_as()` as the step completion barrier (Playwright's blocking API; raw CDP
lifecycle events are Experimental) with an `ArtifactContract` (filename glob + MIME + magic-bytes
probe, strict by default; size band advisory) and upload sha256 manifests with an explicit
per-flow idempotency basis. (2) **Volatile-ID locator blocklist** (`ember\d+`, React `useId`,
GUIDs) in `_SPECOF_JS` — days of work, immediate Salesforce-class win. (3) **Tab graph**: opener
lineage + settled-URL-pattern identity (never index), `expect_popup`/`switch_tab` verbs, loud
failure on unexpected/missing/ambiguous tabs — same-origin popups only (OAuth/3DS ceremonies are
OUT: they collide with the cross-origin refusal, the never-type-secrets contract, and 3DS's
adversarial-by-design dynamism). (4) **Same-origin iframe + open-shadow perception** via
`frame_path` addressing across the whole stack — the one deliberate `SCHEMA_VERSION` bump (prereq
#5). (5) Experimental closed-shadow/OOPIF behind a flag; writes into closed roots refused.

**Unlocks.** "Download the invoice PDF from N vendor portals monthly" with loud failure on an
error page renamed `.pdf` (the download IS the data — artifact contracts are the read-side twin of
the Phase-G write barriers); upload-bearing writes with provenance-keyed idempotency; print/export
popup flows; enterprise web-component UIs (Salesforce Lightning-class).

**Plan.** Stage-gated exactly as numbered — the deep-DOM tail must not consume the budget of the
high-certainty file/blocklist wins. Recorder: map file-input change events to `upload` (today they
record as `type` and replay throws); capture `page.on("download")`; per-frame injection/drain +
sub-frame request reconciliation preserving the unattributed-write refusal.

**Risks.** The schema bump forces a fleet relearn — batch all basis changes once. Loose artifact
tolerance silently accepts a truncated file; strict probes false-alarm on rebranding (default
strict; contract updates approval-gated, never auto-healed). Upload idempotency is a real fork:
content-hash re-fires on regenerated files; path-hash wrongly dedupes changed files — per-flow
declaration required. OOPIF wire attribution doesn't travel across realms — many cross-origin-frame
demos will refuse until attribution matures (the refusal invariant holds). Closed-shadow hooking is
nonstandard and anti-bot-detectable — opt-in, capture and gate must share the identical hook.

**Why now.** Files/tabs/deep-DOM are the top real-world flow blockers in practitioner failure
reports; Browserbase productized downloads-as-artifacts (paid demand); Salesforce session-random
IDs are the canonical enterprise locator pain. Attacks two of the highest-ranked coverage gaps
(iframe/shadow-DOM perception; files and multi-tab as unexpressible steps). *Elevates the
roadmapped action-breadth verbs from a list of primitives to verified contracts.*

### H9. Semantic-wrongness defense (value contracts + judge-sampled canary)

**What.** Two layers against plausible-but-wrong extracted data. **Layer 1 (hot path, key-less,
deterministic)**: per-field contracts (type, format regex, numeric range, null-rate ceiling,
list-count lower bounds, max-delta vs rolling median) checked right after the existing shape gate;
violation raises a **persisted quarantine** that future runs refuse until a human `flow release`s.
**Layer 2 (async, off the replay verb entirely)**: a budgeted, sampled LLM judge over persisted run
artifacts (screenshot + extracted data + scope text; 100% sampling of first-runs after any
heal/replan) whose verdict can ONLY quarantine-forward + escalate — never gates an in-flight run,
never approves, never clears, never touches write confirms. (The original "judge gates the current
run" was rejected: it would put an LLM call on the replay data-release path.)

**Unlocks.** Closes the same-shape-wrong-value class the docs admit is invisible today ("shape-drift
can't see wrong-but-present values") — the highest-leverage remaining instance of fail-loud, for
unattended pipelines feeding ERPs/pricing/compliance where wrong data is strictly worse than no data.
Honest scope: type/format/range/count violations and large jumps are caught deterministically;
subtle meaning changes (tax-inclusive price at similar magnitude) only probabilistically.

**Plan.** (1) `contracts.py` + `FlowSpec.contracts`, types/formats auto-seeded at learn. (2)
`FlowQuarantineError` + `quarantined` health status + `flow release` (fix `_load_meta` first —
prereq #2 — or quarantine can silently evaporate on version skew). (3) Per-field sketch history
JSONL (never raw values in meta). (4) Opt-in artifact capture behind `FlowSpec.audit` with bounded
retention. (5) `flow audit` verb: decompose-evidence-verdict judge, budget-metered, code-path-
enforced quarantine-only. (6) MockJudge key-less tests incl. the judge-cannot-approve invariant.

**Risks.** Contracts seeded from truncated extraction *defend* partial data as normal (prereq #3;
seed count-lower-bounds). False-positive fatigue at 1-run/day cadence (volatile fields trip delta
guards) → habituated release; warm-up advisory periods + per-field tolerances required. Judges are
game-able (manipulated inputs flip VLM judges at up to 90% FPR) — quarantine-only direction bounds
damage to false alarms; a clean judge verdict must never soften deterministic checks. New sensitive
data-at-rest (artifacts) — opt-in, bounded, never in export payloads.

**Why now.** WebJudge-class LLM judging reaches 85.7% human agreement (real but advisory-grade);
rule-based checks err conservative — the safe direction. *Elevates the shipped canary/health from
"does it bind" to "are the values sane".*

### H10. Drift-Watch: monitoring-as-product

**What.** A monitoring layer over existing fleet verbs: `flows.watch` replays a flow, appends the
typed extraction to per-flow history, semantically diffs vs the previous value (scalar delta, key
add/remove, list membership), and emits change/drift/heal events to webhook/Slack/email;
`heal_policy="report"` surfaces a successful heal as a change event instead of silently persisting
it. Scheduling stays cron-as-UX (the deliberate no-scheduler stance) with a per-flow min-interval
guard; watch **hard-refuses** mutating specs. The dashboard is a later deepening of the roadmapped
web UI. v1 is self-hosted and dev/ops-facing — not a consumer SaaS (credential custody + anti-bot
posture make that a different product).

**Unlocks.** Scheduled behind-login monitoring with typed semantic diffs ("price 129→149", "field
disappeared") — differentiated from Visualping-class tools by *transactional multi-step determinism
+ typed diffs + fail-loud drift*, and from Checkly by record-don't-code authoring. The 0-LLM-per-
check economics holds today for pinned scalar reads and navigate/confirm flows (dict/list checks
pay one extraction call until list pinning exists).

**Plan.** (1) `history.py` append-only JSONL + a truncation flag surfaced from `extract.py`
(prereq #3). (2) `diffing.py` + the watch verb — **"indeterminate" must never diff as "no change"**
(the monitoring-specific form of never-silently-wrong). (3) `heal_policy` threaded to `_maybe_heal`
(report mode returns not-healed + a HealEvent; the mutating-step bail untouched). (4) `alerts.py`
generalizing the run-all webhook. (5) Read-only refusal + min-interval + PacingGovernor.

**Risks.** A monitoring false-negative IS silent-wrong-data: changes below the truncation line or
outside innerText diff as "unchanged" — truncation must be its own loud event. Phantom diffs from
extraction nondeterminism ("$129.00" vs "129") cause alert fatigue — normalization is load-bearing.
High-frequency logged-in checks can get customer accounts flagged (vanilla Chromium, no stealth, by
design) — internal/vendor portals are the realistic wedge, min-interval mandatory. MFA session
decay must alert loudly and distinctly, or users get monitoring that silently stopped watching.

**Why now.** Visualping has 2M+ users (85% of Fortune 500 touched); Checkly runs 32.5M checks/day
requiring TypeScript authorship. Behind-login monitoring is table stakes; deterministic transactional
typed-diff monitoring is not offered by anyone.

### H11. Web Bot Auth signed-agent identity

**What.** An opt-in RFC 9421 (HTTP Message Signatures) request signer at the context-route layer:
per-deployment Ed25519 keys signing `@authority` + `signature-agent` (created/expires/keyid/nonce),
cached per-authority; generator tooling for the self-hosted `.well-known` key directory + Signature
Agent Card; guided Cloudflare signed-agents / AWS WAF registration; and **evidence-gated** typed
"identity rejected" failures (the `cf-mitigated` response header first, text heuristics only as
fallback) routed as an ops signal distinct from site drift. No stealth evasion — the opposite:
verifiable declared identity. (Corrected premise: this is an IETF chartered WG with the protocol
draft at individual-v00 — pin versions; not a finalized W3C spec.)

**Unlocks.** Every replay request carries verifiable cryptographic agent identity; bot-manager
blocks become a typed, attributable outcome instead of mystery drift. Actual *unblocking* is
contingent and narrower: concrete today on AWS WAF (verified WBA bots auto-allowed by default) and
Cloudflare-accepted deployments; NOT fleet-scale "stops tripping bot managers". Secondary payoff:
Visa TAP and Mastercard Agent Pay build on Web Bot Auth — this signer is the entry ticket (see H14).

**Plan.** (1) `botauth.py`: env-keyed Ed25519 + RFC 9421 minting (`http-message-signatures` PyPI —
Cloudflare ships TS/Rust only); JWKS directory + Agent Card emitters. (2) `BrowserSession`
`identity=` route handler, merging headers so it never clobbers the idempotency-key injection.
(3) `safety.challenge_evidence()` + `FlowIdentityRejectedError`; drift checks stay fully independent
so identity classification can never mask real drift. (4) Canary/run-all record identity-rejection
distinctly; alert messages distinguish "site drifted" from "identity rejected". (5) `flow identity
init|status` CLI + guided registration docs.

**Risks.** The gatekeeper: Cloudflare's accepted cohort is browser-infra vendors; long-tail
self-hosted acceptance is unproven — code cannot close this (only an ultracua-as-registered-operator
service could; a business decision). Hosting burden: a public HTTPS `.well-known` per deployment.
`route('**/*')` disables the browser HTTP cache — erodes the measured replay speedups; opt-in and
benchmarked. SW/WebSocket requests can't be signed — partial identity may look *more* anomalous.
The new error path must never retry a mutating step with a fresh signature (double-write).

**Why now.** IETF `webbotauth` WG chartered with Aug 2026 milestones; Cloudflare signed-agents
live; AWS WAF WBA support (Nov 2025, default-allow); Visa TAP / Mastercard Agent Pay verifiably
build on it. Pure philosophy fit: fail-loud at the network edge, zero LLM, declared identity.

### H12. Talk-through & point-and-teach recorder

**What.** An opt-in narration channel for the headed recorder: mic capture during the demo, local
ASR (faster-whisper; audio deleted after transcription), timestamp alignment to the action/wire
trace, and ONE record-time LLM fusion pass (exact `caption_intents` shape: opt-in, best-effort,
degrade to placeholders) compiling narration into (a) human-authored intent captions, (b) per-write
confirm predicates **validated against the recorded demo's own end-state** before attach, and (c)
slot/variability annotations stored as inert, reviewable metadata (no runtime substitution until H3
lands). Point-and-teach is a pointing *aid*: a local grounding model highlights the candidate
element for a spoken/typed instruction — **the human still physically clicks it**, so the normal
capture path records the step and grounding output provably never enters the flow artifact. Cut:
narrated branch conditions (linear IR can't express them) and immediate parameterized replay.

**Unlocks.** Non-programmers author richer, more *verifiable* flows by demonstrating and talking:
better self-heal hints and inspect output, stricter fail-loud write barriers for free
(demo-validated predicates), and human-labeled slot candidates that de-risk and pre-seed H3.

**Plan.** (1) `ts` in the capture events (transient — no schema impact). (2) `narrate=True` +
sounddevice + faster-whisper. (3) `fuse_narration` on the captioner contract; predicates checked
via `resolve(unique=True)` against the demo end-state, dropped loudly if they don't hold; the
caption asymmetry preserved (may upgrade a step to mutating, never downgrade). (4) `slot_hint`
CachedStep field surfaced in `flow inspect`. (5) `vision.LocalGrounding` + highlight overlay;
low-confidence grounds show candidates or decline. (6) Injected-transcript key-less tests.

**Risks.** Hallucinated predicates that pass on the demo but flap on personalization/animation —
demo-validation is necessary not sufficient; mark them narration-derived so humans can prune.
Mis-segmented ASR must never guess-attach intent to a *write* step (captions feed
`classify_mutation`'s keyword side). Spoken narration can contain credentials — loud opt-in UX,
transcript redaction, verified audio deletion. If local pointing is slower than just clicking, the
interaction fails its own usefulness bar (top product risk). Scope-creep pressure toward replay
healing must be resisted — authoring-only.

**Why now.** Speech-driven GUI agents are viable (UITron-Speech); think-aloud is a decades-validated
intent-elicitation method; voice narration at demonstration time — the cheapest source of intent,
slots, and verification predicates — has been productized by no one (the sweep's clearest whitespace).

## Tier: ambitious / research-adjacent

### H13. Contract-lane replay compilation (WebMCP pinning, cooperative lanes, wire-level reads)

**What.** A learn-time **lane compiler** that probes an origin's machine contracts and compiles
verified flows onto the cheapest lane verify-by-replay confirms — three strictly-additive sub-tiers
of very different difficulty: (1) cooperative read lanes (`Accept: text/markdown` on opt-in
Cloudflare zones; RFC 9727 `/.well-known/api-catalog`) — trivial mechanics, tiny 2026 coverage;
(2) WebMCP tool pinning (name + JSON-Schema hash + availability precondition) — requires the
`webmcp.py` rewrite first (prereq #4), adoption near-zero today (build thin, adoption-gated);
(3) Integuru-style **wire-level HTTP replay** — full request/response capture compiled into a
dependency-graph program with pinned response schemas, verified against the DOM-lane result before
caching — **READS ONLY** (wire-level writes are refused: an HTTP timeout is ambiguous about commit
and no DOM confirm can close the barrier — a double-write surface). The DOM flow remains the
verified floor; contract drift fails loud; downgrades are *recorded* in flow health, never silent.
A **cross-lane canary** (cheap lane vs DOM lane value comparison) is load-bearing, not optional.

**Unlocks.** Browser-free 0-LLM read replay on the analyzer-accepted subset (cookie-valid sessions,
no JS-computed tokens, not JA3-blocked): compiled reads from cron/Lambda/cheap VPS at 10–100×
lower unit cost — on that subset, not fleet-wide (auth refresh still needs the browser lane).

**Plan.** (1) `lanes.py` origin contract probe at learn/record. (2) WebMCP schema pinning as
additive CachedStep fields; refuse to cache any tool call not provably read-only. (3) Recorder
tool-invocation capture reconciled through existing wire attribution. (4) Markdown lane artifact
compiled *after* the DOM lane passes verify; `wire.py` executor; content-type flip/anchor miss =
fail loud + recorded downgrade. (5) Wire read compiler: HAR-lite capture → LLM-assisted dependency
graph (learn-time only, captioner pattern) → execute and compare vs DOM result before caching;
PacingGovernor wraps every wire call; hard-refuse any flow containing a write. (6) Cross-lane canary.

**Risks.** Wire lanes bypass `resolve(unique=True)` discipline — a schema-stable wrong response
passes every pin, so the cross-lane canary carries the fail-loud guarantee. Silent downgrade would
mask contract drift (recorded + surfaced only). Session decay: Lambda replay degrades to
fail-loud-until-browser-refresh. JA3 fingerprinting (optional `curl_cffi`). Direct internal-API
replay carries more ToS/CFAA exposure than UI automation on some portals. Two executable forms
double the drift/maintenance surface (lanes are sibling artifacts under one flow key, invalidated
together).

**Why now.** CMU API-Based-Agents: WebArena 14.8% (browse) → 38.9% (hybrid API+browse), successful
API tasks need ~2.1 calls; Integuru productized HAR→API compilation; Google killed Project Mariner
citing per-click brittleness. The genuinely novel part is *verified* compilation: wire result must
equal DOM result before caching — verify-by-replay applied above the DOM. No competitor does that.

### H14. Mandated money (cryptographic write mandates + agentic payment rails)

**What.** Two layers, resequenced. **Layer 1 (buildable core)**: an internal signed-mandate format —
Ed25519-signed grants (flow-scope pattern, per-write amount cap, cumulative budget, validity
window, max write count) verified **purely deterministically at the existing mutation gate** before
each write releases; amount caps bound via strict 0-LLM pinned reads of the on-page amount (exact
currency-token match; any ambiguity refuses); a crash-safe reserve-then-commit spend ledger that
fails **closed** (mandate suspended pending human reconcile — a conscious revision of the
no-durable-ledger stance, for mandate-bearing flows only); signed evidence packs recording the
mandate-to-write binding — scoped to **submission-side facts** (what ultracua submitted, never what
the merchant settled: tax/FX/fees are server-side). **Layer 2 (sandbox-gated adapters)**: ACP
buyer-side, AP2/FIDO interop, Visa TAP / Mastercard Agent Pay credentials; x402 enters the core
only as detect-and-escalate (recognize HTTP 402 as an interstitial), never in-core stablecoin
custody. Hard prerequisites: H3 slots (mandates over input-frozen flows only cap byte-identical
writes), the Phase-I service daemon, and H11 (TAP builds on Web Bot Auth).

**Unlocks.** Unattended writes under bounded standing authority: an approver signs one mandate;
scheduled replays execute payment/procurement flows all week; any write exceeding per-write,
cumulative, count, or time bounds fails loud at the gate **before actuation**; auditors verify the
chain offline. Near-term task class: internal/vendor-portal writes under budget (invoice approval,
order placement) — not open-web commerce (merchant admittance is gated).

**Plan.** (1) `mandate.py` (canonical serialization, sign/verify, `flow mandate issue|verify`).
(2) Gate enforcement inside `_replay_step`'s mutation gate — pure crypto + byte comparison, replay
stays 0-LLM; `MutateSpec.amount_pin` with pin-style strict parsing. (3) Spend ledger under the
meta-lock pattern, reserve-then-commit. (4) Idempotency basis widening (mandate_id + slot values —
prereq #1). (5) Evidence packs via the `on_step` seam + offline verify CLI. (6) Rails as versioned
adapters, last, sandbox-only.

**Risks.** The submission-vs-settlement gap is the silently-wrong-audit trap — packs must scope
their claims or the feature violates fail-loud in its most audit-sensitive spot. Locale amount
parsing ("1.999,00 €") mis-parsed at the gate could approve an over-cap write — a lenient parser
is worse than no mandate. Crash between write-fire and ledger-commit strands flows pending human
reconcile (per-write resume is a de-facto prerequisite for honest cumulative accounting). Rails
admittance + custody (x402 = irreversible stablecoin key custody; PCI-adjacent scope) keep Layer 2
highly-experimental. Learn/record-time enrichment must be firewalled from ever influencing mandate
scope (prompt-injection red-teams show the LLM layer is the weak point — which is exactly why
verification lives in the deterministic gate).

**Why now.** AP2 mandates moved to FIDO Alliance standardization (~60 orgs, Apr 2026); Visa TAP and
Mastercard Agent Pay (both build on RFC 9421 / Web Bot Auth); x402 crossed 100M+ settled
transactions; red-team literature ("Whispers of Wealth") shows cryptographic mandates don't protect
the LLM decision layer — favoring exactly ultracua's deterministic-gate architecture.

### H15. Air-gapped zero-key mode (local runtime; local authoring as gated frontier)

**What.** `ultracua --local`: every learn/record-time LLM touchpoint (authoring, structured
extraction, recorder captions, vision grounding, heal/replan) runs against a local llama.cpp/Ollama
backend as a first-class native `LLMClient` (`force_tool` = JSON-schema-constrained decoding). A
**model manifest** (model + quant + weights sha256 + engine + version) is pinned into the flow;
replay of an LLM-extraction read fails loud on manifest mismatch; pinned scalar reads stay 0-LLM
and unaffected. Staged: (1) local backend + extraction + captions; (2) local vision grounding
behind a per-(model,quant,engine) validation CI gate; (3) **experimental reads-only local flow
authoring** (UI-TARS-1.5-7B / Fara-7B class): coordinate proposals back-resolved via
`elementFromPoint` → `_SPECOF_JS` into ordinary locator-based CachedSteps, `block_mutations=True`
hard-defaulted — write flows stay recorder-authored. (Verifier split: steps 1–2 are
focused-effort against existing seams; the authoring rung is the research bet — ship the runtime
without holding it hostage to the frontier.)

**Unlocks.** Air-gapped and regulated environments (healthcare PHI, defense, EU data residency)
where screen content may never leave the machine: key-less, zero-egress learn/record/extract
(replay was already key-less for non-extracting flows). Eliminates the acknowledged
one-LLM-call-per-replay cost for non-pinned reads — marginal cost collapses to hardware. Best-of-N
becomes token-cost-free (not free: each candidate still costs a live verify replay through the
pacing governor, and N never applies to writes).

**Plan.** (1) `llm/local.py:LocalClient` (day-0 spike: the OpenAI adapter already honors
`OPENAI_BASE_URL` → Ollama's `/v1` validates the loop first). (2) `_llm_configured` learns
"local" = explicit env + fast reachability probe (a down endpoint must not turn key-less skips into
retry storms). (3) Manifest as additive fields on CachedFlow/FlowSpec (never FlowMeta — prereq #2).
(4) `vision.LocalGrounding` with explicit coordinate-space normalization + golden-screenshot CI
gate per quant. (5) `providers/local_agent.py` authoring experiment, shipped dark, measured on the
existing MiniWoB/drift-sandbox/recorder-ceiling harnesses before any claim.

**Risks.** The sharpest: a 4–8B quant extractor returning schema-valid but *wrong* data verifies
against itself (verify-by-replay re-extracts with the same wrong model) — learn-time cloud
cross-check when a key exists, prefer pins in air-gapped mode, per-model extraction gates in CI.
Discovery-time writes by a weaker author are live side effects, not discarded candidates —
reads-only is mandatory. Temp-0 ≠ deterministic across engines/backends (pin engine+version;
best-effort even then — no byte-equality marketing without a CI test). Vendor benchmark transfer is
unproven (Fara-7B: WebVoyager 73.5 vs Online-Mind2Web 34.1 — the spread proves it). Multi-GB model
ops + a (model × quant × engine) CI matrix is an ongoing cost commitment.

**Why now.** Small open GUI models went production-grade in 18 months (7–8B ScreenSpot-Pro:
~17–19 → 50–69; official Qwen3-VL GGUFs; Ollama schema-constrained decoding; MIT-licensed Fara-7B
built explicitly for on-device use). *Elevates Phase H from "a local fast tier" to a complete
zero-egress mode.*

## Tier: highly experimental

### H16. Fleet-telemetry training flywheel

**What.** A staged training program, not one feature. (1) An opt-in, **redaction-first episode
exporter** — snapshot element records + winning locator binds + verify/heal/barrier verdicts as
JSONL, a byproduct of normal runs, off the replay path (certainly-achievable core, ~1–2 weeks).
(2) WinDOM-style distillation of a ~2B local grounding/locator-rank model — corpus augmented by a
crawl-harvest mode reusing the production capture JS (per-tenant fleet labels are near-duplicate) —
deployed ONLY as a gated pre-LLM heal proposer and Phase-H fast authoring tier, never inside
`locators.resolve` or `_replay_step`. (3) A sandbox-only replay-reward RFT experiment (GRPO/PPO on
READ flows, error-rate-corrected binary reward) whose outputs enter the product exclusively through
an eval-gated promotion harness (must beat the incumbent on discovery success + heal precision with
zero write-safety-test regressions). The data consortium is descoped to a published episode schema.
(Corrected premise: fleet logs feed SFT/distillation — on-policy RL needs fresh sandbox rollouts;
**writes are untrainable by design**.)

**Unlocks.** A pinned, key-less local model as a 0-frontier-cost heal proposer and fast authoring
tier; the exporter turns every verified run into training labels with ground-truth success bits and
wire-level write attribution — a corpus video-mining pipelines (VideoAgentTrek: 39k videos → 1.5M
*synthetic* steps) cannot reconstruct. NOT delivered: month-over-month self-improvement from fleet
history alone.

**Plan.** (1) `telemetry.py` exporter on the `on_step`/verify/heal/barrier seams, captioner-style
opt-in, capture-time redaction (prereq #6). (2) Crawl-harvest bench script sharing `_SPECOF_JS`
byte-identically. (3) Heal proposer behind the mutating-bail + `resolve(unique=True)` +
`state_changed` gates. (4) Local authoring tier via the standard LLMClient/Router seams. (5) The
eval-gated promotion harness extending existing benchmarks. (6) RFT out-of-repo, sandbox-only.

**Risks.** Reward hacking: verify-by-replay certifies replayability, not semantic correctness — a
policy optimized on the mechanical reward converges on exactly the silent-wrong-data behavior the
project forbids (semantic canaries mandatory, and they reintroduce the noisy-judge problem). The
cleanest reward (write barriers' absent→present ground truth) is on the untrainable population.
One export leak destroys the trust the product sells — while honest refuse-when-uncertain redaction
rejects most authenticated-page episodes, collapsing corpus value from the other direction. Sites
drift, labels stale. GPU training/registry/promotion is a new competency and cost center; CI must
never depend on training.

**Why now.** RL against autonomous evaluators is published (+12.6pt with evaluator-noise modeling)
and productized (HUD eval-to-RFT); WinDOM distilled 2B grounding models from 54k Playwright-harvested
records with no human annotation; demonstration traces with verified success labels are the field's
scarcest training commodity — ultracua's recorder captures them natively.

## Dropped or deferred at merge (27 → the notable ones)

- **Tutorial-to-Flow / video-to-flow / DevTools-Recorder+rrweb import** — demonstration-supply
  expansions superseded near-term by in-profile capture (H4) + talk-through (H12); foreign traces
  lack wire attribution (writes go conservative-only). Revisit once the flywheel (H16) yields a
  strong local grounder.
- **Sub-flow skill mining + planner-over-flows (AWM-style)** — attacks the discovery bottleneck but
  is internal-economics, not use-expansion, and cold-starts on trace volume; fast-follow behind H3
  and H6, which generate the corpus it needs. (Elevates the name-dropped skill/workflow-memory item.)
- **Cross-site flow transfer** — abstraction granularity is the open research problem; evidence
  measures agent success, not deterministic-authoring speedup.
- **Cloud-browser backends / snapshot warm-starts / CDP-direct fastpath / speculative prefetch /
  learned network diet / OTel exporter** — real but pure infra/economics optimizations; fold into
  fleet-ops, the service daemon, and the benchmark harness rather than headline them. (The network
  diet and OTel exporter are certainly-achievable engineering tasks.)
- **Render-free replay (Lightpanda-class)** — removing layout removes the visibility/geometry
  signals the locator + fail-loud semantics rely on; attacks the guarantee at its root. The wire
  lane (H13) covers the same cost story more safely.
- **Compile-to-Playwright eject button** — genuine trust wedge; schedule as an export format under
  the existing flow import/export item.
- **Recorded compensation sagas** — substantive design; adopt when the roadmapped
  compensation/rollback item is scheduled.
- **Site world-model preflight** — a probabilistic twin inside a refuse-to-guess system: the
  sharpest architectural tension of all candidates, for the least certain payoff.
- **Windows-UIA desktop verb tier / canvas verb pack** — adjacent-product scope creep; revisit
  after H8 proves demand for side-step verbs.
- **Live-health flow registry** — value gates entirely on network effects; sequence after H2 + H10
  generate the install base.

## Suggested sequencing (horizons)

**Wave 0 — the six cross-cutting prerequisites** (small, deliberate, unblock everything).
**Wave 1 — leverage:** H3 typed templates (the #1 gap) + H2 stage-1 MCP server (the widest
distribution surface) + H9 layer-1 value contracts + H8 stages 1–2 (files + volatile-ID blocklist —
the cheap certain wins). **Wave 2 — the trust wedge:** H5 dry-run, H1 attested replay, H6
drift-repair tier 1, H10 Drift-Watch — these four compound: the same evidence/verification
machinery sells enterprise trust. **Wave 3 — reach:** H4 in-profile recorder, H7 control flow, H12
talk-through, H11 bot-auth identity. **Frontier (spike-gated, parallel):** H13 lanes (markdown
probe first, wire compiler as the research bet), H15 local runtime → authoring-dark, H14 mandate
core after H3 + the service daemon, H16 exporter first. Each frontier item ships its
certainly-achievable rung without waiting for its research rung.
