# Phase-I recorder — scoping spike

**Status:** SHIPPED (#63–#72 + intent caption) — this is the original scoping doc, kept for the design
rationale + verdict; the open questions below are resolved inline (see each ✅). Code:
[`src/ultracua/recorder.py`](../src/ultracua/recorder.py); proof: [`tests/test_recorder_spike.py`](../tests/test_recorder_spike.py) (+ `test_recorder_fidelity.py`, `test_record.py`, `test_record_caption.py`); fixture: `benchmarks/fixtures/recorder_checkboxes.html`.

## Verdict

Two claims, held to different standards:

- **Pipeline — PROVEN.** A demonstration of a grounding-hard task (garbled-label checkboxes + a typed note,
  no ids) is captured into an ordinary `CachedFlow` and **replays 0-LLM** (`llm_calls == 0`, key-less, in
  CI), reproducing the exact result. The mechanism is real and non-circular (the locator is read from the
  node the click *landed on*, not the demo's selector). Because the output is the same artifact the replay
  engine already consumes, the recorder is a new authoring **front-end**, not a new engine.
- **Lever — MEASURED (the demonstration→replay half).** On the real MiniWoB++ ceiling tasks
  (`click-checkboxes`, `click-checkboxes-large`, `click-option`), a "human" demo-oracle reads the
  instruction's named targets, the recorder captures it, and the recorded flow **replays 0-LLM to a
  positive `WOB_RAW_REWARD`** on **9/9** seeded instances (0–11 targets; 4 multi-target) — gated key-less in
  CI (`tests/test_recorder_ceiling.py`; `benchmarks/recorder_ceiling.py`). Crucially the **ids are stripped
  from the recorded specs**, so replay re-grounds by **role+name+css** — the *same* surface the LLM
  mis-grounds, not MiniWoB's internal `chN` ids. **Same-seed contrast (measured, `--provider anthropic`,
  N=1): recorder 9/9 vs LLM authoring 4/9** — the LLM solves only *single-target* instances and **misses
  every multi-target garbled selection** (3/7/10/11 targets) and the empty "Select nothing"; the recorder's
  5-instance edge *is* the grounding ceiling. (One real-LLM run — the count can wiggle, but the
  single-vs-multi-target split is robust, and best-of-N doesn't move that multi-target ceiling per STATUS.)
  Honest scope: `click-checkboxes-large` is a stress extension of `click-checkboxes`; *semantic*
  `click-checkboxes-soft` is excluded — it needs a knowledge-bearing demonstrator (a human / an LLM
  caption), the honest boundary of a scripted oracle. The recorder routes around *grounding*, but the
  demonstration must still be *correct*.

**Recommendation: proceed to a full build. → DONE.** It was a **medium** build (a front-end over the existing
engine), and it shipped: intent ✅ (caption pass), write capture ✅ (per-write attribution), capture fidelity
✅ (nav/select/press/scroll), and the `flow record` product surface — none of the risk was in the core idea.

## Why (the lever)

The discovery loop is measured-done; the remaining ~40% MiniWoB miss is a **capability ceiling** — tasks
(garbled-label checkboxes, ambiguous options) where the LLM can't reliably **ground**: it picks the wrong
element no matter how many times it re-rolls, so more sampling doesn't help. A demonstration **removes
grounding from the loop**: a human clicks the right node and the recorder just reads it. It converts
"discovery failed → needs an engineer" into "demo it once," and makes the tool usable by non-engineers
(hence Phase I / *distribution*).

## Design

```
  LLM authoring (explore + ground)  ─┐
                                     ├─→  CachedFlow (steps + resilient LocatorSpec)  ─→  replay engine
  human demonstration (record)     ─┘                                                     (UNCHANGED)
```

- **Capture.** An injected init-script (`page.add_init_script`, re-installed on every navigation) listens
  in the **capture phase** for `click` on an actionable control and `change` on a text input. On each
  event it computes a `LocatorSpec` **inline** — reusing the shared `roleOf`/`nameOf` derivation so the
  captured name matches what `resolve()` expects — and exfiltrates it via a bound `expose_function`.
  Computing the spec **at event time** (not after) means a navigating click's target is described before
  it disappears.
- **Assemble.** Each event → a `CachedStep` (action + the captured `LocatorSpec`), assembled into a
  `CachedFlow` under the standard `flow_key`, written to the normal cache.
- **Reuse.** From there, **everything downstream is unchanged**: `resolve()` + neighbor-anchor, the drift
  gate, self-heal, the mutation gate, pinned reads, the canary, `run-all`, the drift-sandbox.

## What the prototype proves

`tests/test_recorder_spike.py`: a "human" (a scripted sequence of *real* interactions, so the spike is
key-less + deterministic) demonstrates the garbled-label task — tick **qux** and **foo**, type a note, show
the result. The recorder captures 3 clicks + 1 `type`, each pinned to a locator captured from the node the
click *landed on* (role + name + css; the checkboxes have **no id**), and the recorded flow **replays with
zero LLM calls** and reproduces `selected: qux,foo | note: abc123`. The exact task class that defeats the
LLM's autonomous grounding is trivially recordable.

What this does *not* show: **drift resilience** is inherited from the unchanged `resolve()` (tested in the
replay engine's own suite + the drift-sandbox), not demonstrated here — the spike proves capture + clean
*same-page* replay, not survival of a perturbed DOM. And it's **one synthetic fixture, no LLM run** — see
the lever caveat in the Verdict.

## Open questions / risks (what the full build must resolve)

1. ✅ **Intent assignment** *(medium — on the trust path — BUILT)*. The replay engine doesn't need `intent`
   (it resolves by locator), but self-heal hints, the idempotency key, and **the keyword side of
   `classify_mutation`** do — so intent isn't just UX, it feeds *write classification*. The spike derived a
   placeholder from the element (`"click qux"`), so a real "Submit order" click could be mis-classified
   **non-mutating** and replay ungated. **Done:** an intent-caption pass (`caption_intents` in `recorder.py`,
   opt-in via the `flow record` CLI) makes a single best-effort post-hoc LLM call (off the replay path) that relabels
   each step's intent, feeding self-heal hints, `inspect` output, and the keyword side of
   `classify_mutation`; read flows are kept text-only to avoid false-refusal. **Replay stays 0-LLM.**
2. ✅ **Write capture** *(medium, TRUST-CRITICAL — BUILT)*. Both gaps closed: (a) the capture now computes
   `mutation_context(el)` (form method) **and** `scope_fingerprint(el)` **inline** at record time — the same
   `_MUTATION_CTX_JS` + `SCOPE_JS` the learn path uses, hashed by the shared `hash_scope` for byte-identical
   parity with the replay gate; `_step_from_event(ev, write_flow=…)` records the submit as a gated mutating
   step (with `precond_scope`). (b) `flows.record()` no longer refuses writes: a **declared** write (the user
   supplies a `--confirm-*` check, since the recorder can't infer action-completion) is routed through
   approval + the mutation gate + idempotency exactly like a learned write — it refuses under form/section
   drift, never double-submits (no verify-by-replay), and is approval-gated. A **fail-closed guard** refuses
   to cache any declared write whose write step couldn't be gated, so a recorded write can never replay
   ungated. Declaring the write also closes the GET-/`sendBeacon`-write residual (gated + approval-gated
   rather than silently cached as a read). Adversarial-reviewed; covered by `tests/test_record.py` (POST-form
   + formless-keyword commits, gated, refusing under drift, idempotency-keyed).
3. ⚠️ **Capture fidelity** *(medium — PARTLY BUILT, adversarially reviewed)*. **Built + tested**
   (`tests/test_recorder_fidelity.py`, `tests/test_record.py`): `select` (dropdowns → a `select` step,
   replayed via `select_option`; **single AND multi-select** — the full selected set is JSON-encoded so a
   `<select multiple>` doesn't silently drop options); keyboard `press` (Enter-submit on a text input with no
   submit button — the "type then Enter" pattern; the field's value is captured as a `type` step **before**
   the press so replay fills then submits, never an empty field, and the press is captured only when no
   synthetic submit-button click would *also* fire, so replay never double-submits); `scroll` (debounced +
   coalesced, a best-effort viewport restore). **Write-safe by construction:** a `<select>` or Enter that
   **submits/posts** in a declared-write flow is captured as a **gated** mutating step (the formless case via
   **per-write attribution** — the init-script instruments `fetch` / `XMLHttpRequest.send` /
   `navigator.sendBeacon` and tags each non-idempotent request with the commit seq from its synchronous turn,
   so the gate binds to the actuated step that *caused* the wire write, not an arbitrary trailing click; an
   ambiguous/deferred request stays unattributed and the flow is **refused**, and an un-instrumentable
   worker / service-worker / cross-realm write surfaces with no marker and is likewise **refused**, never
   cached ungated) — so a select-/Enter-driven write can't replay ungated or double-submit. The
   **exfiltration-vs-navigation race is FIXED**: events are written **synchronously to `sessionStorage`**
   (survives same-origin navigation) and **drained post-navigation + at the end**, replacing the fixed
   `wait_for_timeout` flush; a test demonstrates a *navigating* click and asserts no step is dropped.
   **Cross-origin is now LOUD, not silent:** a cross-origin main-frame hop orphans the prior origin's events
   (per-origin `sessionStorage`), so `record()` **refuses to cache** rather than risk a truncated flow.
   Capture runs in the **top frame only** (a sub-frame's queue is never drained). **Still deferred (NOT
   shown-safe):** cross-origin / SSO recording, keyboard shortcuts / non-Enter keys, **hover/mouseover
   menus**, **right-click / context menus**, **drag**, **post-click dynamic content**, file upload, date
   pickers, multi-tab, shadow DOM / iframes, and the label→input synthetic-click double-fire across browsers.
4. ✅ **Locator quality** *(small, correctness — BUILT)*. The capture script no longer hand-rolls
   `specOf`/`cssPath`: it imports the **one** `_SPECOF_JS` that `DESCRIBE_JS` uses on the learn path
   (`locators.py`), so a recorded step resolves **identically** to a learned one (resolution parity by
   construction) AND now carries the **neighbor anchor** it used to set `null` — a recorded same-role+name
   control disambiguates by its section/row on replay. Covered by
   `tests/test_recorder_fidelity.py::test_recorded_step_carries_the_neighbor_anchor`.
5. **Verification** *(small)*. A recorded flow should pass **verify-by-replay** before it's trusted
   (re-run it 0-LLM on a fresh session; cache only if it reproduces) — the same gate the learn path uses.
6. **Product surface** *(medium)*. A `flow record` CLI that opens a **headed** browser, the human performs
   the flow, and a clear stop signal (a hotkey, or closing the tab) ends capture → inspect → approve. Plus
   the "what did I just record?" review UI (`recorded_steps_summary` is a stub of this).

## Effort estimate (rough)

| Piece | Size | Notes |
|---|---|---|
| ✅ **MiniWoB ceiling validation (was GATING)** | done | **DONE + same-seed contrast measured** — recorder **9/9** garbled-label instances 0-LLM, id-free (role+name+css), gated in CI; vs **LLM authoring 4/9** (`--provider anthropic`, N=1) — the LLM misses every multi-target selection. |
| ✅ **Capture core (nav handshake + select/press/scroll)** | done | sessionStorage store drained post-navigation (no fixed timeout); `select`/`press`/`scroll` captured + replayed, tested key-less. Deferred: shadow/iframe, hover/drag, non-Enter keys |
| ✅ **`describe()` reuse** | done | capture imports the **one** `_SPECOF_JS` `describe()` uses — resolution parity + recorded steps gain the neighbor anchor; tested key-less. (Verify-by-replay already gates read flows.) |
| ✅ **Intent (post-hoc LLM caption)** | done | `caption_intents` (recorder.py), opt-in via the `flow record` CLI (`record(caption=…)`) — one off-replay-path call relabels each step's intent; feeds self-heal + `inspect` + the keyword side of `classify_mutation`; read flows text-only (no false-refusal); replay stays 0-LLM; capture itself stays key-less |
| ✅ **Write capture + gate integration** | done | trust-critical — `mutation_context` + `scope_fingerprint` captured inline (shared `hash_scope` parity); declared writes routed via approval+gate+idempotency; fail-closed guard; adversarial-reviewed |
| `flow record` CLI + review/approve UX | M | headed browser, stop signal, inspect (`--confirm-*` now declares a write) |

**Total: a medium build (~1–2 focused PRs of capture+integration, then the CLI/UX), de-risked by this
spike — and the lever is now measured, not just argued.**

## Recommendation

Proceed. The gating MiniWoB ceiling validation is **done** (lever measured), the `flow record` CLI shipped,
**write capture has landed** (§2: declared writes replay gated + approval-gated + idempotency-keyed,
fail-closed; adversarial-reviewed), **capture fidelity is hardened** (§3: nav handshake via a sessionStorage
store drained post-navigation, plus `select`/`press`/`scroll`, tested key-less), and **`describe()` reuse is
done** (§4: capture shares the one `_SPECOF_JS` for resolution parity + neighbor-anchor on recorded steps),
and **intent caption has landed** (§1: `caption_intents`, one off-replay-path LLM pass feeding self-heal +
`classify_mutation`; replay stays 0-LLM). Remaining build order: **deferred capture** (shadow/iframe,
hover/drag, multi-tab) → **web UI / service daemon**.
