# Apps that serve reads over POST — the approach survey (R4.27)

> **ANSWERED 0.141.0 — READ THIS FIRST. Measurement #1 was run and it says NO.** Demoting the wire
> marks does NOT make an Odoo replay green: the mutation-gate refusal simply becomes a LOCATOR
> refusal, on the same step, which never bound in either arm. Measured twice, once on a day-old
> recipe and once on one learned minutes earlier, so staleness is excluded — and measurement #2
> shows locator failures already dominate independently of any mark. **The lead candidate (B) is
> still a correct fix for the MARKING and is no longer the path to an Odoo baseline.** See "What the
> measurements said" below; the ranking that follows it is superseded by that section.
>
> **AND THE CAUSE NAMED IN THAT ANSWER WAS ITSELF WRONG (R4.115, 0.142.0).** Measurement #1's
> observation stands -- demotion does not change the outcome -- but "the locator" is not why.
> On a RENDERED page the failing spec binds uniquely on the first candidate; the replay is
> reading a page that has not painted (Odoo: 5 elements at `domcontentloaded`, 0 of 7 scenarios
> complete, settling 0.62-1.08 s; Gitea: 7 of 7 complete). Conclusion for this document is
> unchanged and now doubly held: a marking fix is not the Odoo path.

**Status: research, not a decision.** Nothing here is scheduled. It exists so that whoever next
picks up R4.27 does not re-derive nine approaches, and does not re-propose one of the six that are
dead. Written 2026-08-27 at 0.140.0, from a 14-agent survey grounded in live read-only probes of the
Odoo 17 substrate, the D6 capture artifacts, and source reads of `safety.py` / `flow.py` /
`flows.py` / `recorder.py` / `dryrun.py`.

`docs/ci-provisioning.md`'s rule applies to this file too: it holds the measurements because the
places they were taken (an agent transcript, a terminal, a live container) do not keep them.

## The problem, stated once

`safety.is_write_request(method, url)` classifies by HTTP METHOD. Any app serving reads over POST —
GraphQL SPAs, JSON-RPC, Odoo — has its read steps promoted to `mutating=True` (source `wire`) at
learn. The step then loses self-heal, suffix-replan, the auth-refresh retry and verify-by-replay, is
refused from MCP and `run_batch`, and at replay is judged by the mutation gate, which REFUSES on
drift rather than healing.

Measured: Odoo `availability_rate` **0.181** (std 0.203) against Gitea's **0.762** — same tasks,
different transport. **58%** of Odoo's replay refusals are the mutation gate on wire-marked steps
(R4.105). R4.27's original measurement was **12/12** GraphQL read controls cached as write flows.

**The write-safety machinery downstream is correct at every branch** — D6 established that by
measurement and was refuted for proposing to change it (R4.111). The defect is the classification
alone. That is what makes this tractable: nothing on the write rail needs weakening.

## What the survey established as fact

* **The request body is available with zero plumbing.** The learn watcher receives a full Playwright
  `Request`; `post_data` is a synchronous property, and `dryrun.py:229` already reads one. The
  promotion loop runs in the same function (`_author_steps`), so a body-based decision needs no
  signature change to the engine chain.
* **Odoo's discriminator is in the URL path for browser traffic.** The web client emits
  `POST /web/dataset/call_kw/<model>/<method>` — live-probed: `web_search_read`, `get_views`,
  `has_group`. The bare `/web/dataset/call_kw` form is also live (it is what `substrates.Odoo.rpc`
  constructs), so an implementation must handle both and assert they AGREE when both are present.
* **The navigate promotions come from page-load POSTs.** A CRM list view load fires four read POSTs;
  a bare backend load fires five, three on `/mail/*` routes. Covering the navigate class therefore
  needs a route-EXACT read list as well — exact and never prefix, because `/mail/message/post` is a
  write one path over.
* **The response side is barren, measured.** Odoo `call_kw` returns HTTP 200 for reads, writes AND
  errors, with identical headers. And `web_save` returns the saved record read back — a write whose
  response is exactly read-shaped, with no pathological server required.
* **Prior art is unanimous.** No production system classifies POST-tunneled reads by method. GraphQL
  edge caches (Stellate, Apollo Router) parse the operation type from the body; API gateways use
  declared allow-lists (Hasura, Apollo PQL); MCP's `readOnlyHint` defaults pessimistic. The
  GraphQL-over-HTTP spec explicitly equates operation type with HTTP method safety ("GET requests
  MUST NOT be used for executing mutation operations"), so trusting a parsed `query` is the same bet
  as trusting GET — which this codebase already takes.
* **Granularity, twice.** Classify per OPERATION, never per request (the WAF blind spot is exactly
  "one HTTP transaction = one action" — batch arrays, aliased mutations). And per STEP: a demotion
  rule must require EVERY wire event in the step's act window to classify as a read.

## The verdicts

| Approach | Verdict | Reaches (of the 10 measured gated steps) |
|---|---|---|
| **B** — JSON-RPC read-method allowlist + route-exact read routes | viable with conditions — **the lead** | 10/10 |
| **A** — GraphQL operation-type parsing | viable with conditions | 0/10 (it is R4.27's *original* class) |
| **H** — unmark-but-key belt | viable as a rider on A/B only | 0 alone |
| **I** — accept and document | viable as the interim floor | — |
| framework fingerprint + vetted profile | the correct *packaging* for B | = B |
| substrate snapshot / state-diff oracle | validation instrument, never product | — |
| **C** — two-tier marking / recovery restoration | **dead** | 0/10 in its safe form |
| **G** — navigation-specific URL gate | **dead** | ~0–1 scenario |
| **D** — operator declaration (three granularities) | **dead** | — |
| **E** — response-side evidence | **dead** | ~0 |
| **F** — repetition / frequency evidence | **dead** | rep-flaky by construction |
| authorization-differential probe | **dead** as shippable | residuals only |

## B — the lead candidate

Demote a POST to read only when: the route matches `call_kw`, the envelope parses as JSON-RPC
`method:"call"`, and `params.method` is on a COMMITTED read allowlist — asserting the URL suffix and
body method agree when both are present. A second, route-exact list clears the four named page-load
read routes. **Everything else stays a write**: unknown methods, batch arrays, suffix/body
disagreement, non-`call` envelopes, unparseable bodies.

**Why the recorded rejections do not transfer.** The refused URL denylist was refused because "a
GraphQL MUTATION travels the same URL". An Odoo `create` does NOT travel under the name
`search_read` — the method name IS the operation, dispatched by `getattr(model, method)`. This is an
allowlist of reads failing closed (enumerate the quiet set, unknowns loud), which is the shape this
repo sanctions everywhere else. The body is a sensor class D5 has not spent. It refuses nothing, so
D0's block does not reach it — D6's own entry states that boundary.

**Its residual, stated at parity.** A server whose `search_read` actually writes defeats it. That
server defeats today's classifier by serving writes over GET. Not a new exposure — but note this is
parity only for the *stock* framework: a customized Odoo overriding a read verb to write is
currently GATED and would stop being, so that specific residual is NEW and must be filed honestly
rather than waved through as parity.

**Conditions the adversarial pass attached** (each is a condition, not a nicety):

1. **Measure the demoted population offline first**, over the existing capture artifacts. D0's
   standing order: "it went green… and was still wrong."
2. **Trim the allowlist to methods actually observed.** Five drafted entries were never live-probed;
   `onchange` in particular is a documented write-in-a-read-shaped-call hazard.
3. **Price the verify-by-replay re-arm.** Demotion does not merely lose a mark — it re-enables
   verify-by-replay, a full second browser pass. A wrongly-demoted write is **double-fired at
   learn**. Needs a named write-safety cell with the `saves == 1` premise, and a learn wall-time
   re-measure (`odoo-menu-nav` already sits at 21 actions against a 20-step ceiling).
4. **Reach every `is_write_request` consumer, or say why not.** The heal wire-guards
   (`flow.py:1632,1661`) are consumers too: left untouched, a demoted Odoo step that heals will
   refuse to persist the heal, because the healed act fires read-POSTs. Demoting at the promotion
   site alone leaves Odoo recovery poisoned.
5. **Demotion ADDS a provenance mark; it never strips `MARK_WIRE`.** Stripping it makes R4.27
   invisible again — `safety.py`'s own warning. `tests/test_annotation_disposition.py` exists to
   fail loudly when a GraphQL read stops being filed as a write and must be updated deliberately.
6. **No operator-extension door in any form.** The human-verdict sensor class is spent (`flow mark`
   refused 12/12); a demoting declaration re-spends it under D5.
7. **Adjudicate with the instruments that found the defect** — `gate_probe` on the new tree, then a
   3-rep corpus run — and claim nothing about 2.4b until the locator residual is separately
   measured.

## The graveyard, with the reason that kills each

* **C — two-tier marking (wire-only steps keep heal/replan).** Reaches 0/10: the measured refusals
  are GATE refusals, which return pre-act; heal is reachable only from an act exception. And the
  heal paths are themselves wire-guarded, so recovery stays poisoned even after restoration. Also a
  trust inversion — `safety.py` ranks `wire` as EVIDENCE and `keyword` as a guess, so the tier grants
  recovery exactly where the evidence is strongest. R4.111's transfer clause names it: "ANY
  replay-side softening… while the marking is wrong."
* **G — navigation gate on URL identity.** Vacuous on the motivating substrate: every Odoo backend
  page is origin+path `/web`, with all state in the hash the design must tolerate. And the safety
  claim inverts across runs — today the departure fingerprint blocks repeated replays of a
  commit-on-load navigate; under G every replay re-fires it.
* **D — operator declaration.** Per-origin: an Odoo `web_save` travels the identical origin, route
  and envelope as `web_search_read`, so wholesale demotion strips key, confirm and settle from a
  real write. Per-step: the human-verdict class is recorded spent; `approve` asserts INTENT (whose
  failure the approver owns), `declare-read` asserts a SERVER FACT the operator has no instrument
  for. Also operationally futile — marks are re-stamped on every auto-mode re-author. The one
  survivable fragment is a declaration that ARMS body evidence rather than demoting, which is a
  scoping knob inside B.
* **E — response-side evidence.** Measured barren (200 always, identical headers), and `web_save`'s
  read-shaped response makes it actively wrong. Epistemically it is the `landed` doctrine inverted:
  a demotion that is "only correct if the inference is true", which this register forbids building on.
* **F — repetition / frequency.** Inverts on two ordinary shapes: autosave (a repeating write) and
  N line-items in one learn (N real writes to one endpoint). The JSON-RPC envelope is structurally
  identical for read and write, so a value-excluding shape hash launders `create` into the read
  class — and including the value IS approach B. The threshold is a tuning constant, refused by name.
* **Authorization-differential probe** (re-issue the captured body under a write-denied role) — the
  one genuinely new sensor class the hunt found. Its safety premise fails on this substrate: Odoo
  does not ACL-check custom `call_kw` methods at entry, `sudo()`-escalating methods write under any
  role, and the probe re-transmits by design (a `/mail/message/post` probe double-posts). **Filed
  refuted-with-reason, not as an escalation path** — an earlier draft of the survey recommended it
  as one, and that recommendation is withdrawn here.
* **Quick deaths, each verified.** `Sec-Fetch-*` (context, not semantics — identical on
  `web_search_read` and `web_save`); CSRF-token presence (Odoo exempts JSON-RPC, the exact traffic
  at issue); response hashing across re-fires (the re-fire IS the double-fire); persisted-query
  exploitation (the agent cannot change what the app emits); HTTP `QUERY` (zero coverage today, one
  line when it arrives); WebMCP `readOnlyHint` (dropped at detection today, page-asserted and
  therefore attacker-controllable — may corroborate, never demote).

## One family nobody evaluated: the replay-time wire arbiter

Every approach above classifies at LEARN and acts on a static mark. The dry-run machinery already
proves requests can be held pre-send with the body in hand (`context.route`, `dryrun.py:227-244`).
A marked step could instead ACT under an arbiter that releases requests parsing as protocol-reads
and holds anything else — moving the decision to the moment full evidence exists, and making
recovery safe because no unexpected write can escape. It escorts the believed-write rather than
trusting it, so D6's "weaken the gate for believed writes" objection does not reach it.

Real costs: route overhead on every request, the shared-worker traffic the idempotency-proxy slice
already had to solve, and interaction with the write-settle waits. **Recorded as the successor
design to evaluate if B's fail-closed residual proves expensive** — not as a proposal.

## The unpriced assumption, and the $0 measurements that settle it

**"Fixing the marking improves Odoo availability" is assumed by everything above and measured by
nothing.** R4.111's tail already records the counter-signal: `odoo-sort-list`'s gate refusal was
itself `target missing/ambiguous` — a locator failure wearing a gate's message. A demotion may
merely convert "gate refused" into "healed and still failed", because the read path also resolves
with `unique=True`.

Run these before choosing anything. All are free or nearly so.

| # | Measurement | What it decides |
|---|---|---|
| 1 | **Cache-edit replay.** Copy the cached Odoo recipes, hand-flip wire-marked steps to `mutating:false`, replay 0-LLM against the live substrate. | Everything. Green ⇒ a marking fix unblocks 2.4b, proceed with B. Still refusing on locators ⇒ the second problem dominates and the ranking reorders. |
| 2 | Refusal-shape census over the existing 21 scenario records. | Quantifies the locator-ambiguity share — the ceiling on what B can recover. |
| 3 | Allowlist clearance rate over existing capture artifacts, with and without the route-exact half. | Whether the route half ships in slice 1. |
| 4 | Negative control: `odoo-create-lead`'s cached write steps must NOT demote; Gitea untouched. | The both-directions pin this repo demands before any demotion exists. |
| 5 | Bare-vs-suffixed `call_kw` check from the existing page-load captures. | Whether URL-suffix parsing suffices or the body parse is required in v1. |
| 6 | GraphQL adversarial fixture (batch, aliasing, `operationName`, string-literal `"mutation"`, persisted hashes) — offline. | Prerequisite for A claiming any measured validation; there is no GraphQL substrate in the corpus. |

Measurement 1 is the D6 lesson applied to the fix direction *before* the fix: buy the second sample
when the first gives a clean story.

## What the measurements said (0.141.0)

Measurements #1 and #2 were run. They cost **$0.0998** — one fresh Odoo learn; both replays and the
whole census were free.

### #1 — flipping the marks does not make the replay green

`benchmarks/mark_flip_probe.py` replays the SAME recipe twice against the SAME substrate, differing
only in whether the `wire`-sourced marks are set. On `odoo-sort-list`, step 1, the same click:

| | control (as learned) | demoted |
|---|---|---|
| step meta | `mutating: True`, **`gate: drift`** | no mark, **no gate** |
| bind | `gate_bound_by: none` | `bound_by: none` |
| note | `mutation gate: target missing/ambiguous` | `locator unresolved or ambiguous (drift)` |

**`bound_by: none` in BOTH arms.** The locator never resolved either way — the mutation gate was
reporting a locator failure it happened to reach first. Demotion changes the message, not the
outcome. Run twice: once on a day-old recipe, once on one learned minutes before, so this is not
staleness.

Demotion did have one real effect, and only one: it restored the auth-refresh retry (control: *"not
retrying — a recorded step is marked as WRITING"*; demoted: *"after auth refresh:"*). The retry then
failed identically.

`odoo-menu-nav` is worse for the thesis: both arms fail at **step 0**, an UNMARKED `navigate`. It
never reaches a marked step, so R4.27 is irrelevant to its failure entirely.

### #2 — locator failures already dominate, independently of the marking

Census over the three-rep series (18 non-passing Odoo replay rows):

| count | shape |
|---|---|
| 6 | **no replay attempted** — the learn produced nothing (a discovery failure, not a replay one) |
| 6 | gate said *page/form drift* |
| 4 | **locator unresolved/ambiguous, with no gate involved at all** |
| 1 | gate said *target missing/ambiguous* — which is itself a locator failure |
| 1 | data not found |

So of the twelve rows that actually attempted a replay, **four failed on locators with no mark in
the picture**, and measurement #1 shows the gated ones convert into that same failure when
unmarked. Roughly a third of Odoo's non-passing rows never produce a recipe at all.

### What this changes

* **R4.27 is a real correctness defect and NOT the Odoo blocker.** Fixing it would stop reads being
  misfiled — worth doing on its own terms — but the measured availability gain is approximately
  zero, because the steps it un-gates then fail on locator resolution.
* **2.4b must not be costed as "fix R4.27, get an Odoo baseline".** The dominant blockers are
  locator ambiguity on a generated DOM and, for about a third of rows, discovery producing no recipe.
* **The ranking in "Recommended sequence" is superseded.** B, A and H remain correct descriptions of
  a marking fix; none of them is the next thing to build for Odoo.
* Measurements **#3–#6 are deferred with their approaches** — each is an implementation prerequisite
  for B or A, and neither is now the next step. #5 (bare-vs-suffixed `call_kw`) was answered in
  passing by the survey's own live probe: browser traffic carries the method in the URL path, and
  the bare form is what `substrates.Odoo.rpc` constructs.

## What this survey does not claim

* **Not that the product gets Odoo wrong.** No Odoo scenario in any run has produced `wrong_data`,
  and `mode="auto"` falls through to a re-author, so the answer still arrives. What is lost is the
  0-LLM deterministic replay.
* **Not that B is scheduled.** Phase 3 is taken one at a time and only when a number indicts it.
  This file records the shape a future slice would take, and the measurements that must precede it.
* **Not that the covered list is provably complete.** The hunt's own completeness claim ("only a
  trusted refusing party escapes the double-fire circularity") is a judgement, labelled as one.
