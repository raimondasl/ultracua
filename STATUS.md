# ultracua — status & observations (2026-07-01)

A dated, honest snapshot covering through the recorder arc: what's shipped, how proven it is, what the
latest benchmark runs measured, the known fragilities (with `file:line`), and the prioritized path forward. The
forward-looking phase plan lives in [ROADMAP.md](ROADMAP.md) — Phases A–D are shipped, and several
of the longer-term phases have since landed (E fleet supervisor, F suffix-replan, H pinned 0-LLM
reads, J CI); the full plan (E–J) is in *"Beyond Phase D"* there.

## Verdict

**A validated prototype of a genuinely novel pattern — the core thesis is proven, the replay
engine is the moat, and it is not yet hardened for unattended production.** Phases 0–4 (core
engine), A–C (the Flow API: define → learn → approve → replay → auth-refresh → health), and D
(write flows) are shipped and merged, and the ops layer has since hardened (logging, CI,
retry/backoff, fleet supervisor + freshness canary, a cross-process meta lock, and a standing
locator-resilience benchmark). **422 tests**, all key-less (real headless Chromium against local
fixtures, run in CI on Linux + Windows); version **0.56.0**. Secrets handling is a real strength:
credentials are env-sourced at runtime and **never persisted** — only the resulting `storage_state`
cookies are saved (atomically).

## What's shipped, by layer

| Layer | Shipped & solid | Thin / fragile |
|---|---|---|
| **Engine** (`flow.py`) | learn → replay → heal loop; **verify-by-replay before cache** (a learned read flow is cached only if it reproduces on a fresh 0-LLM replay — most discovery failures are caught here); **suffix-replan** (re-author the broken tail, keep the prefix) when single-step heal can't fix a drifted step; ranked resilient locators (testid → role+name → **neighbor-anchor** → css) that **fail loud on an ambiguous bind, never silently first-match** (`resolve(unique=True)`); mutation gate that **fails loud, never LLM-heals a write** — incl. *refless* Enter-submits, now gated on the **focused field's** form-scope (#55), not the whole page; stuck/interstitial detection; pacing governor; resilience measured by a standing **drift-sandbox** (12/12 cosmetic drifts survive 0-LLM, 0 wrong-binds) | a mutating **navigate** submit (rare) still falls back to the whole-page fingerprint; a purely *positional* css whose target is removed can retarget a moved-in neighbor (documented residual in `resolve`); the heal LLM call still grounds from a single snapshot (no multi-modal/vision tie-break) |
| **Flow API** (`flows.py`) | full lifecycle; approval gate; data-shape drift; fail-loud `FlowReplayError`; auth-refresh; fleet health + **fleet supervisor** (`flow run-all`: concurrent replay, pass/fail/skip classification, non-zero exit, alert webhook) + a cheap read-only **freshness canary** (`flow canary`: does each flow still *start*? — catches entry-page rot before a scheduled run fails); Phase D writes with action-completion + opt-in idempotency precheck; the health read-modify-write is now **cross-process locked** (#54), so concurrent scheduled processes can't lose a health update | no built-in **scheduler** (by design — point cron / Task Scheduler at `flow run-all` + `flow canary`); the canary is intentionally shallow (entry step only — mid-flow drift still needs the full `run-all`) |
| **Providers** (`llm/`, `providers/`) | provider-neutral types; Anthropic path with real prompt-cache + streaming TTFT; reusable extraction; **Router retry/backoff/timeout** (transient-aware, capped exponential + jitter); **per-run token + est. $ cost accounting** (`FlowReport.extra["usage"]`); **all three adapters' `.complete()` glue covered by key-less live-path tests**; JSON-RPC daemon + Node client | the live-path tests replay **recorded/synthetic** responses, not a real API call (no keys in CI); Gemini's test injects the SDK response object rather than exercising its HTTP/deserialization layer |
| **Ops / packaging** | config via `ULTRACUA_*` env; data kept off C:; `.env` gitignored; **stdlib logging** with a per-run `run_id` contextvar; **GitHub Actions CI** (Linux + Windows, key-less suite); **single-sourced version** (`importlib.metadata`) | the JSON-RPC daemon is single-flight, unauthenticated, and (now **documented as**) the raw *engine* surface that bypasses the Flow safety gates — engine-only, not a service; a real service daemon (auth + the Flow verbs) is Phase I |
| **Recorder** (`recorder.py`, `flow record`) | **capture fidelity** (click / type / select / press(Enter) / scroll); **nav handshake** (survives same-origin navigation via a sessionStorage queue drained post-nav); **write gate + per-write attribution** (an init-script instruments fetch / XHR.send / sendBeacon to tie each non-idempotent request to the commit in its synchronous turn — declared writes are gated + approval-gated + idempotency-keyed, and an un-instrumentable / ambiguous write is **refused, never cached ungated**); verify-by-replay then cache; **intent caption** (best-effort post-hoc LLM relabel for self-heal hints + inspect + the keyword side of `classify_mutation`; replay stays 0-LLM) | **cross-origin** is a **loud refusal** (orphaned writes fail loud, not silently cached); **iframe / shadow-DOM** capture and **WebSocket** writes are not yet attributable |

## Benchmarks (run 2026-06-18)

| Benchmark | Provider | Result | Speedup | Notes |
|---|---|---|---|---|
| Demo-shop (4-step) | Anthropic | replay correct, 0-LLM | **58.0× total / 49.8× per-step** | learn 12.4 s (5 Opus calls, TTFT-dominated ~1.3–3 s); replay 215 ms |
| Demo-shop | scripted (key-less) | correct, 0-LLM | 1.2× | expected — scripted teacher has ~0 LLM latency |
| MiniWoB++ ×10 | Anthropic `--all` | **6/10 learn+replay; 10/10 replay 0-LLM** | **37.5–94.0×** on successes | **all 4 failures are at LEARN (discovery); zero replay failures** |
| MiniWoB++ ×3 | oracle (key-less) | 3/3 replay, 3/3 0-LLM | 1.2–1.3× | harness sanity, no LLM latency |
| WebArena-Verified | — | not re-run this session (needs Docker/WSL2) | — | prior on-disk live runs: shopping_admin **6/8**, shopping **6/8** replay@1.0, with **2 replay regressions** (tasks 126, 150) |

A saved prior MiniWoB run scored **8/10 @ 49–280×**; today's fresh run scored 6/10 @ 37–94×, with
`click-link` and `focus-text-2` failing to *author* this time. That swing is the headline insight,
not noise (see below).

### Discovery reliability — best-of-N (measured 2026-06-19→20, Anthropic, MiniWoB++ ×10, 5 reps)

This is the swing above, now pinned down and acted on. Records in [`baselines/`](baselines/).

| Config | Per-task success | Variance | Cost (5 reps) |
|---|---|---|---|
| **N=1** (baseline, raw authoring) | **52%** | **± 13%** (40–70%, i.e. 4–7/10) | $4.24 |
| **N=3 best-of-N** (re-author, keep first verify-passing) | **60%** | **± 0%** (6/10 *every* rep) | $6.58 (**1.55×**) |
| **N=3 + reflexion** (feed a failed sample's lesson forward) | **52%** | ± 4% (mostly 5/10) | $8.32 (**−8 pts, +26%**) |

The headline is the **variance collapse, not the +8 points**: best-of-N drove run-to-run spread to
**zero** — discovery is now deterministic. Cost rose only **1.55×** (not 3×) thanks to adaptive
early-stop.

**Reflexion is a measured dead-end (kept opt-in, OFF by default).** Feeding a failed attempt's
LLM-written lesson to the next sample made it *worse* (60%→52%, +26% cost): the advice misdirects an
otherwise-clean re-roll. This is the actionable finding — the remaining 40% is a genuine **capability
ceiling** (≈4 tasks like garbled-label checkboxes), unmoved by *either* more sampling or reflection. So
the next reliability gain had to come from **capability**, NOT more discovery-loop cleverness — the loop
is measured-done. That lever **shipped**: the **recorder** (`flow record`) was measured against this exact
MiniWoB ceiling and scored **9/9 vs the LLM's 4/9** (#64). (`variance --bench miniwob --all --samples 3
[--reflect]`.)

**Honest caveats on the headline numbers:**

- **"Speedup" is an in-process micro-timing.** `learn.total` includes LLM latency; `replay.total`
  does not. It measures *"what fraction of step time was the LLM,"* not end-to-end wall-clock
  (excludes process/browser startup).
- **"Replay 0-LLM" means 0-LLM *navigation*.** Data-retrieval replays still make **one uncounted
  LLM extraction call** in `finalize`; `llm_calls` only counts self-heal. (Exception: a `pin_read`
  flow whose answer has a stable `id`/`data-test-id` anchor reads the value deterministically —
  0-LLM end-to-end, no extraction call. Phase H, #36.)
- **Live replay is not perfectly faithful** (2/8 shopping regressions) — real evidence that some
  multi-step flows don't reproduce cleanly today.

## The core finding: discovery is the bottleneck, not replay

The replay engine is the moat and it is solid — 0-LLM, correct, and 37–94× faster on everything
that learned. **All variance is at discovery (learn time).** Today's 6/10 vs. the prior 8/10
differ only in which flows the LLM managed to *author*; replay failed on nothing it learned. Two of
the four misses (`click-checkboxes`, `click-option`) are the known garbled-label selection failure;
the other two (`click-link`, `focus-text-2`) were plain discovery variance this run.

**Implication for the roadmap:** invest in (1) discovery reliability, (2) replay fidelity on real
multi-step/auth pages, and (3) operability — *not* in making replay faster (it already is).

## Top fragilities a real deployment would hit

*This is the original pre-#27 audit, kept for the record — items 1–7 have since been fixed (see
**Near-term priorities** below for the PR that landed each); only #8 remains open.*

1. **No observability** — the codebase uses no `logging`; a failed scheduled replay surfaces only a
   `FlowReplayError` string. Nothing to debug a 3am failure with. *(biggest production gap)*
2. **Over-sensitive mutation-gate fingerprint** (`snapshot.py:131`) — hashes role+name+tag of every
   snapshotted element, so an unrelated banner/badge trips "drift" and **refuses valid writes**.
3. **Self-heal can corrupt the cache** — `_maybe_heal` (`flow.py:462`) re-grounds one step but never
   re-validates with `state_changed`, so a heal that binds the wrong element is persisted
   (`flow.py:488`).
4. **Non-atomic, unlocked meta sidecars** (`flows.py` `_save_meta` / `_record_run`) — concurrent
   replays (or `run_many`) can lose health updates or corrupt `.meta.json`.
5. **No LLM-call resilience** — `.complete()` has no retry/backoff/timeout; one transient 429 fails
   an entire learn.
6. **Dropped cost telemetry** — adapters populate `Usage` (tokens) but it is discarded at
   `llm_agent.py:129`; there is no $/run accounting despite "$0 replay" being a headline.
7. **Packaging mismatch** — `pyproject` version `0.1.0` vs runtime `__version__` `0.15.0` (the daemon
   reports the latter).
8. **OpenAI/Gemini are live-untested** — translation is unit-tested; the real network path of every
   adapter is exercised only via `MockClient`. OpenAI's `max_tokens` likely breaks newer models.

## Near-term priorities

**Update: all seven shipped** across PRs #27 (1–3), #28 (4–5), #29 (6–7) — and the longer-term
phases have kept landing since: **#33–#35 CI (Phase J), #36 pinned 0-LLM reads (Phase H), #37 fleet
supervisor (Phase E), #38 suffix-replan (Phase F)**. The suite grew from 105 → **145** tests
(key-less); version **0.22.0** *at the time* — it has since grown to **422 tests / 0.56.0** as the
trust-hardening below landed. Original near-term list with the PR that landed each:

1. ✅ **Correctness/packaging nits** (#27) — single-sourced the version; `_save_meta` / `cache.put`
   atomic (temp + `os.replace`). *Handled fragilities 4, 7.*
2. ✅ **Observability** (#27) — stdlib `logging` across learn/replay/heal/auth with a `run_id`;
   token usage + $ cost surfaced (`FlowReport.extra["usage"]`); daemon logs to stderr. *Handled 1, 6.*
3. ✅ **LLM-call resilience** (#27) — retry/backoff/timeout around `Router.complete`. *Handled 5.*
4. ✅ **Precision-aware mutation gate** (#28) — the gate fingerprints the target's enclosing
   form/section, not the whole page, so unrelated churn no longer false-flags a write. *Handled 2.*
5. ✅ **Heal hardening** (#28) — re-validate (`state_changed`) after a healed click (no-effect heals
   aren't persisted); `resolve()` prefers a unique candidate over an ambiguous first-match. *Handled 3.*
6. ✅ **Discovery reliability** (#29) — `learn(samples=N)` re-authors and keeps the first verified
   attempt (CLI `flow learn --samples N`).
7. ✅ **Write/auth benchmark** (#29) — `benchmarks/write_flow_bench.py`: write action-completion,
   one-shot idempotency, and auth-refresh recovery from session expiry, against a local fixture.

None of the near-term fragilities are open. A second wave of trust-hardening has since landed:
**#54** cross-process meta lock + `BrowserSession.start()` leak fix + CLI tests; **#55–#57** the
refless-submit write gate (focused-field scope) + idempotency-on-Enter + `unique=True` write target;
**#58/#59/#61** neighbor-anchor disambiguation + `unique=True` read actuation (closing the last
silent-wrong-bind) hardened against substring/cross-tag mis-binds; **#60** the **drift-sandbox**
locator-resilience benchmark; the **freshness canary** + daemon-contract docs; and the **recorder arc**
(**#63–#72** + intent caption) — capture fidelity, the same-origin nav handshake, the write gate with
**per-write attribution**, and the post-hoc intent caption, all via `flow record`. The recorder was
measured against the exact MiniWoB capability ceiling and scored **9/9 vs the LLM's 4/9** (#64), so it
**closed** that ~40% gap. Of the longer-term phases, **E fleet supervisor, F suffix-replan, H pinned
reads, J CI, the I recorder core, and the G multi-write completion barrier** are landed — the last adds a
**per-write barrier** (`MutateSpec.step_confirms`): replay verifies each write as it actuates (an absent→present
transition) and fails loud without proceeding to the next write; multi-write barriers are record-only and not
auto-retried after auth-refresh. A small ops nicety also landed post-#74: **`BrowserSession(window_size=…)`** +
the `ULTRACUA_WINDOW_SIZE` env, which sizes the headed/demo browser window so the page fills it (headless
renders at that size; unset = Playwright's default 1280×720) (#75). Two **Wave-0 hardening** fixes from the
innovation-horizons sweep also landed: `_load_meta` now drops **unknown** meta fields instead of resetting a
flow's approval + run history (forward-compat, no silent trust wipe), and `extract` now **reports truncation**
(a page longer than the extractor's window) — the read path fails loud when a value is "not found" on a
truncated page, instead of treating it as a clean absence. A **manual capability-eval suite** also landed
([evals/](evals/README.md), 0.45.0): 107 scenarios / 419 checks covering the shipped core AND aspirational
probes for every ROADMAP horizon (H1–H16), key-less by default ($0), with an LLM/live tier (~$1.35 full),
partial-run filters, a `--budget` cap, and per-run cost estimates vs measured spend — run by hand, never CI.
The first **innovation-horizon feature** then shipped — **H2 flows-as-tools, stage 1** (0.46.0): an
`ultracua flow serve-mcp` **MCP server** ([`mcpserver/`](src/ultracua/mcpserver/)) that exposes every
**approved READ** flow as one deterministic, zero-argument tool to any MCP client (Claude / Cursor / VS
Code / …), dispatching to the safety-gated `replay()` (never the raw engine); **writes are default-deny**,
learn/approve/record are never tools (no self-approval), and a new **typed `FlowReplayError` taxonomy**
(`DriftError` / `ShapeDriftError` / `AuthExpiredError` / `EscalateError`, each with a machine-readable
`code` + `retryable`) lets a caller react to a failure by kind. **H2 stage 3 (typed slot inputs)** then
shipped (0.54.0), unblocked by H3: an approved read flow that has **slots** is now a **parameterized** MCP
tool — its `inputSchema` is built from `FlowSpec.slots` (one JSON-Schema property per non-secret slot;
secrets stay `$env`-resolved and out of the schema; `additionalProperties:false`), and a tool call dispatches
through `replay(spec, params=…)` so every argument is validated against the closed slot domain by the SAME
`validate_params` the flow uses — a bad arg is a typed **`invalid_params`** (caller-fixable, non-retryable)
raised *before any browser opens*, cleanly distinct from an operator-config gap (`replay_error`) or a
replay-time drift/auth failure. A no-slot flow stays a zero-argument tool (byte-identical to stage 1).
**H2 stage 2 (opt-in write exposure)** then shipped (0.55.0): behind a `serve-mcp --expose-writes` opt-in
(default-deny), an approved **DECLARED** write flow (a `spec.mutate` with a confirm barrier) becomes a
tool annotated **destructive**, and every call runs the **write rail** — entirely inside a per-flow
single-flight `asyncio` mutex: 0-LLM pre-flight → **retry-dedupe** against the durable `RunLedger` (a repeat
of the same args returns `already_done`, never re-fires) → **elicit a human confirm** (MCP `elicit_form`;
no capability / decline / transport-error ⇒ refuse, never fire) → fire via the safety-gated `replay()` →
record **strictly after** the write confirms. An **undeclared** write (mutating steps but no `spec.mutate`)
is *never* exposed on any surface; secret slots stay `$env`-resolved and out of the confirm preview; the
Idempotency-Key is the correctness floor, the ledger/mutex/confirm are the rails. A 3-lens adversarial
review (double-fire/race, elicit-bypass, exposure/secret) came back clean. **HTTP transport** and stage 3's
**per-caller credentials** stay deferred to a later slice + the Phase-I auth daemon (until then a caller
rides the operator's identity — a documented confused-deputy cap, loud in the tool description + CLI).
**H9 value contracts, layer 1** then shipped (0.56.0): the deepest remaining fail-loud gap closed — replay
checked the extracted data's *shape* but not its *values*, so a same-shape-but-WRONG value (a price 129→0, a
field that went null, a 500-row list that collapsed to 3) was returned as if correct. Now a conservative
per-field VALUE contract (type / non-null / positive-sign / a high-confidence format / a list count-floor /
null-rate ceiling) is **auto-seeded at learn** ([`contracts.py`](src/ultracua/contracts.py)) and checked
right after the shape gate on replay — pure Python, **0 LLM on the hot path**. A violation raises a typed
`FlowQuarantineError` and **persists a quarantine**, so every future run (single / `run_batch` / MCP / fleet)
refuses 0-LLM at pre-flight until a human `flow release`s it. Reasons are **value-free by construction** (only
type names / counts / bounds — no raw values at rest); seeding is single-sample-safe + truncation-aware; the
human overlay `FlowSpec.contracts` is approval-hashed (a tighten *or* loosen re-blesses); the write rail is
untouched (contracts are read-side only). A 3-lens adversarial review (silent-wrong-value escape, quarantine
bypass, truncation/secret/re-approval) gated it. The **numeric magnitude** case (129→40, same sign, above
floor) needs a human `min`/`max` now — layer-2's rolling-median delta + the sampled-LLM judge are the next slice.
Then **H3 typed templates, slice 1** shipped (0.47.0): flows stop being input-frozen — a `SlotSpec` +
`FlowSpec.slots` typed input contract, a 0-LLM **pre-flight validator** (`validate_params`: type / enum /
pattern / min-max / required / env-resolved secrets — an out-of-domain value fails loud before the browser
opens), and **`replay(spec, params={…})`** that substitutes validated per-run values at a flow's
slot-marked fill/select steps (`flow_key` unchanged — values never enter identity; a no-params replay is
byte-identical to before). READ-side only: parameterizing a WRITE flow is refused, and `idempotency_key`
grew an additive slot-value channel ready for it. **Slice 1b** then added **recorder auto-mining** (opt-in
`flow record --mine-slots` / `record(mine_slots=True)` auto-lifts a read flow's typed/selected values into
typed slots) and the **value-independence audit** — if a mined value echoes into a later
locator/precondition/URL (a dead template) it **refuses to templatize, fail loud** (`RecordResult.slot_findings`
reports it). **Slice 1c** then added **site-metadata domain capture**: the recorder captures each field's
legal domain (a `<select>`'s options, an input's `pattern`/`required`/`max_length`/`min`/`max`/`datalist`)
onto `CachedStep.slot_domain`, and mining types each slot from it — a `<select>`'s options become a closed
`enum`, input constraints carry over — so pre-flight validates against the real site domain.
Then **H3 slice 2a** shipped the **WRITE side** (0.50.0): the parameterized-WRITE refusal is lifted — a
write template runs each row through one learned form-submit, and the write actuation gate folds the run's
slot values into the per-write **`Idempotency-Key`** (distinct rows → distinct keys, so a backend dedupe
can't silently drop rows 2..N; a retry of one row → the *same* key, so it dedupes instead of double-writing;
`None`/`{}` params keep the pre-2a key byte-identical, so frozen single-write flows are unchanged). A new
**slot-schema approval gate** (`FlowMeta.slots_hash`, bound at `approve()`) refuses replay if a slot's domain
widened since approval — a stale approval must never authorize a wider contract than the human reviewed (an
injection surface, worst on a write). The **mutation gate** (value-independent — a changed input value never
shifts the form fingerprint), the **confirm barrier**, and the 0-LLM **pre-flight** still guard every write,
and a write is still never verify-by-replayed. An adversarial review hardened three write-safety edges before
merge: a param that would fold into the key without substituting at a recorded type/select step is refused
loud (else the frozen value ships under a per-row key — a wrong + un-dedup-able double write); a parameterized
write can't lean on the row-blind one-shot precheck (which could skip a distinct row as "already-done"); and
the idempotency-key row canonicalization is **injective** (JSON, not a raw delimiter-join) so two free-text
rows can't collide to one key.
Then **H3 slice 2b** shipped the **VOLUME driver** (0.51.0): **`run_batch(spec, rows)`** drives ONE
parameterized flow once per row (a row-granular sibling of `run_all`), plus a `flow run-batch` CLI (dry-run by
default; JSON/CSV rows). Its safety posture: **all-or-nothing pre-flight** (every row validated 0-LLM through
the shared `_preflight_row` — the 2a guards extracted so `replay` and `run_batch` share one source of truth —
before ANY actuation; one bad row refuses the whole batch, zero writes); **duplicate-row refusal** (two rows
that would mint the same Idempotency-Key are refused — a backend dedupe would silently suppress the second);
**`max_rows` required for a write batch** (one approval must not authorize unbounded writes); **fail-loud
isolation** (`on_row_error="stop"` halts on the first failure and marks the rest skipped; `"continue"` reports
each); and a **dry-run** that validates + previews each row's Idempotency-Key (byte-identical to the wire key)
and actuates nothing. Sequential + secret-safe (rows carry no secrets; the report stores only indices + hashed
keys).
Then **H3 slice 2c** shipped the **per-row resume ledger** — "the hardest part" (0.52.0): a new
[`ledger.py`](src/ultracua/ledger.py) `RunLedger` (durable append-only JSONL, keyed by each row's
Idempotency-Key), and **`run_batch(resume="<job-id>")`**. A batch that died at row 300 of 500, re-run under
the SAME job-id, **skips** the ~299 rows that already committed (status `"resumed"`) instead of re-firing
their writes — finishing 300.. rather than double-writing 1..299. The **Idempotency-Key stays the correctness
floor**: a row lost to a crash window re-fires with the *same* key and the backend dedupes it — the ledger is
a pure optimization above that floor, recorded **strictly after** each write confirms (`flush`+`fsync`), so
every crash window biases toward a harmless deduped re-fire, never a false skip of an un-landed write. The
**resume token resolves the recurring-vs-retry ambiguity** (a run-invariant key can't: same token = resume,
fresh token = independent run — the operator's statement of intent). A torn last line is tolerated; the CLI
`flow run-batch` auto-mints + prints a job-id so even the first run is resumable. Per-write resume *within* a
multi-write flow stays deliberately deferred (a stateless probe can't attribute page-state to a specific write).
Finally, the **write-slot binding surface** shipped (0.53.0), making write templates fully usable through the
PUBLIC API (no cache surgery): **`record(spec, demo, writable_slots={"amount"})`** (CLI `flow record
--writable-slots amount,qty`) — the **explicit human sign-off** that turns a demonstrated write field into a
parameter. A write field is *never* auto-lifted (mining stays read-only — a silently-parameterized payee/amount
is a money-moving injection surface); the author NAMES the fields, each binding its ONE demonstrated
type/select step (a name matching 0 or >1 fields **refuses** so a money field is never mis-bound). A pre-declared
typed `SlotSpec` (enum/pattern/range) wins over mining; the value-independence audit gates write slots too (a
demo value echoing into a later locator = a dead+dangerous template → refuse); a secret slot's plaintext is
scrubbed from the cache; a not-named field stays frozen. **H3 typed templates are now complete end-to-end** —
record a write once, run it for N rows via `run_batch` with row-keyed idempotency + resume.
Still open: the **Phase-I remainder** (web UI / service daemon / registry) and
**Phase-G** per-write one-shot resume, action breadth (file upload / multi-tab / iframes), compensation/rollback,
and dynamic-N writes.

See [ROADMAP.md → *Beyond Phase D*](ROADMAP.md) for the longer-term phases (E–J) with the concrete
use cases each unlocks and the gap each closes.
