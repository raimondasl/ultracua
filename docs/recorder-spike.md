# Phase-I recorder — scoping spike

**Status:** spike / prototype (branch `spike/phase-i-recorder`). Prototype: [`src/ultracua/recorder.py`](../src/ultracua/recorder.py); proof: [`tests/test_recorder_spike.py`](../tests/test_recorder_spike.py); fixture: `benchmarks/fixtures/recorder_checkboxes.html`.

## Verdict

Two claims, held to different standards:

- **Pipeline — PROVEN.** A demonstration of a grounding-hard task (garbled-label checkboxes + a typed note,
  no ids) is captured into an ordinary `CachedFlow` and **replays 0-LLM** (`llm_calls == 0`, key-less, in
  CI), reproducing the exact result. The mechanism is real and non-circular (the locator is read from the
  node the click *landed on*, not the demo's selector). Because the output is the same artifact the replay
  engine already consumes, the recorder is a new authoring **front-end**, not a new engine.
- **Lever — ASSERTED, not yet measured.** "This moves the ~40% ceiling" rests on *reasoning* plus one
  synthetic fixture; **no LLM was run against it**. The gating next step is to demo the ~4 real MiniWoB
  ceiling tasks and confirm they replay — only that turns the lever claim into a measured result.

**Recommendation: proceed to a full build.** It's a **medium** build (front-end over the existing engine);
the risk is concentrated in a few identified places — intent, **write capture (currently a no-op gate —
see §2)**, capture fidelity, product UX — not in the core idea.

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

1. **Intent assignment** *(medium — and on the trust path)*. The replay engine doesn't need `intent` (it
   resolves by locator), but self-heal hints, the idempotency key, and **the keyword side of
   `classify_mutation`** do — so intent isn't just UX, it feeds *write classification*. The spike derives a
   placeholder from the element (`"click qux"`), so a real "Submit order" click could be mis-classified
   **non-mutating** and replay ungated (compounds §2). Options: a one-line human label per step; a single
   post-hoc LLM caption pass (cheap, one call, off the replay path); or infer from the element.
   **Recommend** the post-hoc LLM caption — but it must run **before** `classify_mutation` (or classification
   must move to a structural-only signal, see §2). Keeps replay 0-LLM.
2. **Write capture** *(medium, TRUST-CRITICAL — bigger than "just `precond_scope`")*. Two gaps, both real:
   (a) the recorder captures **no structural mutation signal at all** — `_step_from_event` calls
   `classify_mutation(..., ctx={})`, so it runs keyword-only; the learn path gets the form-method via
   `mutation_context(el)`, which the recorder must also capture inline at record time. (b) Even if a step
   *is* flagged mutating, it carries **no `precond_scope` and no `precond_fingerprint`**, so the write gate
   is a **no-op** (`flow.py` falls through to `precond_fingerprint` which is empty) → a recorded write
   would replay **completely ungated**. Out of scope here (selection tasks are non-mutating, verified), but
   the full build must capture `mutation_context(el)` **and** `scope_fingerprint(el)` inline for mutating
   clicks / Enter-submits, and route recorded writes through approval + the gate. Adversarial-review gated.
3. **Capture fidelity** *(medium)*. The spike covers `click` + text `type` (both tested). The full build
   needs: `select` (dropdowns), keyboard `press` (Enter-submit, shortcuts), `scroll`, **hover/mouseover
   menus**, **right-click / context menus**, **drag**, **post-click dynamic content** (a click reveals
   controls the demonstrator immediately uses), file upload, date pickers, multi-tab, and shadow DOM /
   iframes. **Untested in the spike (deferred, NOT shown-safe):** the label→input synthetic-click double-fire
   across browsers, and — importantly — the **exfiltration-vs-navigation race**: the spike's final action
   mutates the same page, so a *navigating* click is never exercised; the fixed `wait_for_timeout(80)` flush
   is a **latent flake** under real navigation (the context can tear down before the last `expose_function`
   call is delivered). The build needs a synchronous handshake (a `pagehide`/`sendBeacon` flush or a store
   drained post-navigation), not a fixed timeout.
4. **Locator quality** *(small, but a correctness risk)*. The spike inlines `role/name/css` and sets
   `anchor=null`; this `specOf`/`cssPath` is a hand-rolled near-duplicate of `DESCRIBE_JS`. If the two drift,
   recorded specs and learn-path specs resolve **differently** — undermining the "same artifact" claim. The
   full build must share **one** `specOf` between `describe()` and the capture script (so recorded steps also
   get the neighbor-anchor), not just for DRY but for resolution parity.
5. **Verification** *(small)*. A recorded flow should pass **verify-by-replay** before it's trusted
   (re-run it 0-LLM on a fresh session; cache only if it reproduces) — the same gate the learn path uses.
6. **Product surface** *(medium)*. A `flow record` CLI that opens a **headed** browser, the human performs
   the flow, and a clear stop signal (a hotkey, or closing the tab) ends capture → inspect → approve. Plus
   the "what did I just record?" review UI (`recorded_steps_summary` is a stub of this).

## Effort estimate (rough)

| Piece | Size | Notes |
|---|---|---|
| **MiniWoB ceiling validation (GATING)** | S–M | demo the ~4 ceiling tasks, confirm they replay — **the real proof of the lever**; coupled to capture hardening because those tasks exercise `select` / dynamic content / nav, so do it *with* (not after) the capture work |
| Capture core (this spike, hardened) | S–M | label/nav handshake (not a fixed timeout), shadow/iframe, `select`/`press`/`scroll`/hover/drag |
| `describe()` reuse + verify-by-replay | S | share **one** `specOf` (resolution parity); gate on reproduce |
| Intent (post-hoc LLM caption) | S | one off-replay-path call; must run *before* `classify_mutation` |
| Write capture + gate integration | M | trust-critical (capture `mutation_context` + `precond_scope`; route via approval+gate) → adversarial review |
| `flow record` CLI + review/approve UX | M | headed browser, stop signal, inspect |

**Total: a medium build (~1–2 focused PRs of capture+integration, then the CLI/UX), de-risked by this
spike.** The single highest-value next step is the **MiniWoB ceiling validation** — demo the ~4 tasks
sampling can't crack and confirm they replay, turning the "moves the ceiling" claim from reasoning into a
measured result.

## Recommendation

Proceed. Build order: **harden capture *and* run the MiniWoB ceiling validation together** (they're
coupled — the ceiling tasks exercise the untested `select`/dynamic/nav paths, and the validation is what
turns the lever from asserted to measured) → `describe` reuse + verify-by-replay → intent caption → write
capture (reviewed) → the `flow record` CLI/UX. Do not invest in the CLI/UX before the ceiling validation
confirms the lever.
