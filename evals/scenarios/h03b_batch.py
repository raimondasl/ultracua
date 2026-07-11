"""H3 slice 2 evals — the run_batch cluster: VOLUME + per-row FAIL-LOUD + RESUME (ROADMAP H3).

The horizon this module probes: a `run_batch(spec, rows)` verb that takes N pre-validated rows and
drives one parameterized flow per row on the `run_all` supervisor pattern — the WRITE side of typed
templates. Slice 2 is NOT built yet, so the run_batch capability surfaces come out `missing` (the
target); the shipped BUILDING BLOCKS it will ride on get partial credit (they PASS):
  - `flows.run_all` — the fleet supervisor (safe defaults: approved-only + read-only);
  - `flows.validate_params` — the pure 0-LLM per-row validator (out-of-domain -> loud refusal, no I/O);
  - `safety.idempotency_key(..., slot_values=...)` — the row-value key channel (distinct rows ->
    distinct keys; same row on retry -> same key; None/{} -> the base key byte-identical);
  - the frozen-only WRITE baseline: `replay` REFUSES a parameterized write today, so run_batch's
    per-row parameterized writes cannot silently ride the current engine.

Each scenario names the DANGER a run_batch surface must guard: double-write (a re-run re-fires a
landed row), suppressed-write (N rows share ONE dedupe key so a backend drops rows 2..N),
silent-batch-continue (row k fails but rows k+1.. proceed into a wrong state), and the volume trust
surface (ONE approval authorizing 500 distinct writes). As slice 2 lands these must flip
missing -> pass; any regression on the shipped safety blocks must fail LOUD.
"""

from __future__ import annotations

import dataclasses
import inspect

from evals.core import Ctx, expect, fail, import_probe, missing, ok, probe, scenario


# --- H3 slice 2 step 5: the run_batch verb + the safe supervisor posture it inherits -------------
@scenario(
    id="h03b.batch.run_batch_verb",
    title="run_batch verb exists (N rows on run_all) + the safe read-only/approved-only posture it inherits",
    group="h03b", aspirational=True, tags=("batch", "writes", "slots"),
)
async def run_batch_verb(ctx: Ctx):
    import ultracua.flows as flows_mod
    from ultracua.flows import FlowReplayError, FlowSpec, MutateSpec, SlotSpec, replay

    checks = []
    # ASPIRATIONAL: the headline verb. A dedicated N-row driver is where per-row bounds + resume live;
    # without it a volume write batch gets bolted onto run_all (one run per flow), which has no notion
    # of a row and no per-row failure isolation. Not built -> missing.
    checks.append(expect(callable(getattr(flows_mod, "run_batch", None)),
                         "flows.run_batch exists (N pre-validated rows -> one parameterized run each)",
                         "no run_batch verb — a volume batch has no home but run_all (flow-granular)",
                         aspirational=True))
    # PARTIAL CREDIT (shipped): the supervisor run_batch is planned on top of.
    checks.append(expect(callable(getattr(flows_mod, "run_all", None)),
                         "run_all supervisor exists (the fleet pattern run_batch builds on)"))
    # PARTIAL CREDIT (shipped): the SAFE POSTURE run_batch inherits — read-only + approved-only by
    # default. A batch verb that silently defaulted to firing writes would be a mass-write footgun.
    sig = inspect.signature(flows_mod.run_all)
    safe_defaults = (sig.parameters["approved_only"].default is True
                     and sig.parameters["include_writes"].default is False)
    checks.append(expect(safe_defaults,
                         "run_all defaults approved_only=True + include_writes=False (writes need explicit opt-in)",
                         f"unsafe defaults: {[(p.name, p.default) for p in sig.parameters.values()]}"))
    # PARTIAL CREDIT (shipped safety baseline): today `replay` REFUSES a parameterized WRITE (flows.py
    # ~955) — the write side stays FROZEN-ONLY until slice 2's row-keyed idempotency + write re-verify
    # exist. So run_batch's per-row parameterized writes CANNOT leak through the current engine: the
    # refusal fires before the browser opens. This check going RED would be a real write-safety regression.
    spec = FlowSpec(name="h03b-refuse", start_url="http://127.0.0.1:9/checkout", goal="submit a row",
                    mutate=MutateSpec(confirm_text_contains="Saved"),
                    slots={"amount": SlotSpec(type="integer", min=1)})
    try:
        out = await replay(spec, params={"amount": 5}, cache=ctx.cache())
        checks.append(fail("parameterized WRITE replay is refused today (writes stay frozen-only)",
                           f"replay accepted params on a write flow: {out!r}"))
    except FlowReplayError as exc:
        msg = str(exc).lower()
        checks.append(expect(any(w in msg for w in ("aren't supported", "read-only", "next slice")),
                             "parameterized WRITE replay is refused today (writes stay frozen-only)",
                             f"raised FlowReplayError but not the frozen-write refusal: {exc}"))
    return checks


# --- H3 slice 2: per-row PRE-FLIGHT — validate every row against the slot schema before any action --
@scenario(
    id="h03b.batch.per_row_preflight",
    title="per-row pre-flight: validate_params is the pure 0-LLM per-row validator (good row PASS, bad row LOUD)",
    group="h03b", aspirational=True, tags=("batch", "slots", "preflight", "fail-loud"),
)
async def per_row_preflight(ctx: Ctx):
    import ultracua.flows as flows_mod
    from ultracua.flows import FlowReplayError, FlowSpec, SlotSpec, validate_params

    checks = []
    # A typed READ template with a closed enum + a bounded integer — exactly the schema run_batch would
    # validate each row against BEFORE opening a browser for that row.
    spec = FlowSpec(name="h03b-lookup", start_url="http://127.0.0.1:9/orders", goal="look up an order",
                    slots={"region": SlotSpec(type="string", enum=["us", "eu", "apac"]),
                           "qty": SlotSpec(type="integer", min=1, max=100)})
    # PARTIAL CREDIT (shipped): a GOOD row resolves to a substitution dict — pure, 0-LLM, no I/O.
    good = validate_params(spec, {"region": "eu", "qty": 5})
    checks.append(expect(good == {"region": "eu", "qty": 5},
                         "a good row validates to its resolved substitution dict (pure 0-LLM pre-flight)",
                         f"resolved={good!r}"))
    # PARTIAL CREDIT (shipped): an OUT-OF-DOMAIN row fails LOUD. DANGER: an out-of-domain row (region
    # 'mars') must be rejected as a pure computation — never by opening the site and typing a garbage
    # value into a live field. validate_params raises before any browser action exists to take.
    try:
        validate_params(spec, {"region": "mars", "qty": 5})
        checks.append(fail("an out-of-domain row is refused before any browser action",
                           "validate_params accepted region='mars' (not in the enum)"))
    except FlowReplayError as exc:
        checks.append(expect("region" in str(exc),
                             "an out-of-domain row is refused before any browser action (site untouched)",
                             f"raised but did not name the offending slot: {exc}"))
    # PARTIAL CREDIT (shipped): a stale-schema / typo param is refused too (an unknown name can't be
    # silently dropped — that would drift the row off its intended slot).
    try:
        validate_params(spec, {"regionn": "eu"})
        checks.append(fail("an unknown param name is refused (typo / stale schema)",
                           "validate_params accepted an unknown param 'regionn'"))
    except FlowReplayError:
        checks.append(ok("an unknown param name is refused (typo / stale schema)"))
    # ASPIRATIONAL: run_batch must call this validator PER ROW, up front — a bad row at position k
    # should fail the batch (or that row) without ANY row's browser action. Probed as: does run_batch
    # accept a `rows` list to pre-validate? Not built -> missing.
    rb = getattr(flows_mod, "run_batch", None)
    has_rows = callable(rb) and "rows" in inspect.signature(rb).parameters
    checks.append(expect(has_rows,
                         "run_batch takes a rows=[...] list it pre-validates per row before acting",
                         "no run_batch verb to feed rows through validate_params", aspirational=True))
    return checks


# --- H3 slice 2: per-row FAIL-LOUD isolation — row k's failure is its own report, no silent continue -
@scenario(
    id="h03b.batch.per_row_fail_loud",
    title="per-row fail-loud: run_batch returns {status, rows:[...]} so row k's failure is isolated, not silent",
    group="h03b", aspirational=True, tags=("batch", "fail-loud", "writes"),
)
async def per_row_fail_loud(ctx: Ctx):
    import inspect as _inspect

    import ultracua.flows as flows_mod
    from ultracua.flows import FleetRun, run_all

    checks = []
    # ASPIRATIONAL: the per-row result shape. DANGER: if row k throws, run_batch must record THAT row
    # as a failure and never silently roll on into rows k+1.. (each a real side effect) as if nothing
    # broke. Probe the {status, rows:[per-row outcome]} shape — call it once built, else missing.
    rb = getattr(flows_mod, "run_batch", None)
    if not callable(rb):
        checks.append(missing("run_batch returns a per-row result {status, rows:[...]} (row k isolated)",
                              "no run_batch verb — no per-row result surface to inspect"))
    else:
        st, out = await probe(rb, None, [])
        shaped = st == "ok" and isinstance(out, dict) and isinstance(out.get("rows"), list)
        checks.append(expect(shaped, "run_batch returns a per-row result {status, rows:[...]} (row k isolated)",
                             f"got {st}: {out!r}", aspirational=True))
    # ASPIRATIONAL: an EXPLICIT per-row isolation policy (stop-the-batch vs continue-and-report). A
    # write batch must not default to blindly continuing past a failed row; the policy has to be a
    # first-class, visible knob. Probe run_batch for a stop_on_error / on_row_error surface -> missing.
    knobs = {"stop_on_error", "on_row_error", "isolate", "continue_on_error"}
    has_policy = callable(rb) and bool(knobs & set(_inspect.signature(rb).parameters))
    checks.append(expect(has_policy,
                         "run_batch exposes an explicit per-row failure policy (stop vs continue, never silent)",
                         "no per-row failure-policy knob (a failed row must not silently continue the batch)",
                         aspirational=True))
    # PARTIAL CREDIT (shipped): the per-UNIT outcome record run_batch's per-row entries would mirror —
    # FleetRun already carries (name, ok, status, error), a self-contained pass/fail per unit.
    fr_fields = {f.name for f in dataclasses.fields(FleetRun)}
    checks.append(expect({"ok", "status", "error"} <= fr_fields,
                         "FleetRun models a per-unit outcome (ok/status/error) — the per-row shape to mirror",
                         f"FleetRun fields={sorted(fr_fields)}"))
    # PARTIAL CREDIT (shipped): run_all ISOLATES a failed unit today — a FlowReplayError in one flow
    # becomes that flow's FleetRun(status='failed'), it does NOT abort the whole gather. That per-unit
    # isolation is the exact contract run_batch must extend to rows. Source-inspect the supervisor.
    try:
        src = inspect.getsource(run_all)
    except (OSError, TypeError):
        src = ""
    checks.append(expect("except FlowReplayError" in src and "FleetRun" in src,
                         "run_all catches a per-unit FlowReplayError into a failed record (one failure ≠ batch crash)",
                         "run_all no longer isolates a per-unit failure — a failing unit could abort the batch"))
    return checks


# --- H3 slice 2 step 6: ROW-KEYED idempotency — distinct rows distinct keys, retry same key ---------
@scenario(
    id="h03b.batch.row_keyed_idempotency",
    title="row-keyed idempotency: slot-value key channel (shipped) + the write gate must fold row values (missing)",
    group="h03b", aspirational=True, tags=("batch", "idempotency", "writes"),
)
async def row_keyed_idempotency(ctx: Ctx):
    import ultracua.flow as flow_mod
    from ultracua.safety import idempotency_key

    checks = []
    scope, idx, intent = "flow:h03b-order", 3, "submit the row"
    # PARTIAL CREDIT (shipped): DISTINCT rows -> DISTINCT keys. DANGER: 500 rows of one parameterized
    # write sharing ONE key would let a backend dedupe layer silently drop rows 2..N (suppressed-write) —
    # the batch reports success while only row 1 landed. The slot-value channel prevents that.
    k_r1 = idempotency_key(scope, idx, intent, slot_values={"amount": "10"})
    k_r2 = idempotency_key(scope, idx, intent, slot_values={"amount": "20"})
    checks.append(expect(k_r1 != k_r2 and k_r1.startswith("uca-") and k_r2.startswith("uca-"),
                         "distinct rows mint distinct keys (a dedupe layer can't collapse 500 rows to 1 write)",
                         f"{k_r1} vs {k_r2}"))
    # PARTIAL CREDIT (shipped): the SAME row on retry -> the SAME key, canonicalized (sorted keys, str
    # values) so key order can't wobble. DANGER: a wobbling key turns a retried row into a double-write.
    k_a = idempotency_key(scope, idx, intent, slot_values={"amount": "10", "region": "eu"})
    k_b = idempotency_key(scope, idx, intent, slot_values={"region": "eu", "amount": "10"})
    checks.append(expect(k_a == k_b,
                         "same row on retry mints the SAME key regardless of dict order (retry ≠ double-write)",
                         f"{k_a} vs {k_b}"))
    # PARTIAL CREDIT (shipped): None/{} slot_values -> the BASE key, byte-identical — existing
    # single-write flows are provably unchanged by the additive channel (no silent key drift).
    base = idempotency_key(scope, idx, intent)
    checks.append(expect(base == idempotency_key(scope, idx, intent, slot_values=None)
                         == idempotency_key(scope, idx, intent, slot_values={}),
                         "None/{} slot_values -> the base key byte-identical (single-write flows unchanged)",
                         f"base={base}"))
    # ASPIRATIONAL: the write ACTUATION gate must FOLD the run's slot values into the key it mints.
    # Today flow._replay_step mints idempotency_key(scope, idx, step.intent) with NO slot channel
    # (flow.py ~833) — so at actuation all N rows of a parameterized write would mint the SAME key and a
    # dedupe would drop rows 2..N. The shipped channel exists in safety.py; the gate doesn't feed it yet.
    fn = getattr(flow_mod, "_replay_step", None)
    try:
        gate_src = inspect.getsource(fn) if fn else ""
    except (OSError, TypeError):
        gate_src = ""
    gate_folds = bool(gate_src) and "slot_values" in gate_src
    checks.append(expect(gate_folds,
                         "the write actuation gate folds the run's slot values into the idempotency key",
                         "flow._replay_step still mints idempotency_key(scope, idx, intent) with no slot "
                         "channel — every row would share ONE key at actuation (suppressed-write)",
                         aspirational=True))
    return checks


# --- H3 slice 2: RESUME — a re-run after a mid-batch failure skips already-committed rows -----------
@scenario(
    id="h03b.batch.resume_ledger",
    title="resume: a durable row-keyed completed-run ledger so a re-run never re-fires a landed write (the hardest part)",
    group="h03b", aspirational=True, tags=("batch", "resume", "writes", "fail-loud"),
)
async def resume_ledger(ctx: Ctx):
    import ultracua.flows as flows_mod
    from ultracua.cache import StepConfirm
    from ultracua.flows import MutateSpec

    checks = []
    # ASPIRATIONAL: a durable completed-run LEDGER keyed by row identity. DANGER: a batch that dies at
    # row 300 must, on re-run, SKIP rows 1..landed rather than re-firing 300 already-committed writes
    # (mass double-write). Probe for a ledger surface — a module or a RunLedger/BatchLedger type. Not
    # built -> missing (this is the hardest, explicitly deferred, part of the write side).
    ok_led, _ = import_probe("ultracua.ledger")
    has_ledger = ok_led or any(hasattr(flows_mod, n) for n in ("RunLedger", "BatchLedger", "CommitLedger"))
    checks.append(expect(has_ledger,
                         "a durable row-keyed completed-run ledger exists (resume skips landed rows)",
                         "no ledger surface — a mid-batch re-run would re-fire already-committed rows",
                         aspirational=True))
    # ASPIRATIONAL: the resume ENTRY POINT — run_batch (or a resume verb) takes a resume/ledger handle
    # so a re-run is idempotent at ROW granularity, not just at HTTP-header granularity. Not built -> missing.
    rb = getattr(flows_mod, "run_batch", None)
    resume_kw = {"resume", "ledger", "resume_from", "completed"}
    has_resume = callable(rb) and bool(resume_kw & set(inspect.signature(rb).parameters))
    checks.append(expect(has_resume,
                         "run_batch exposes a resume/ledger entry point (row-granular idempotent re-run)",
                         "no resume surface on run_batch", aspirational=True))
    # PARTIAL CREDIT (shipped, documented-deferred): per-write RESUME is a KNOWN deferred concern, not
    # an oversight — StepConfirm's docstring states a stateless page probe can't safely attribute prior
    # page-state to a specific write, so a multi-write flow re-fires on a manual re-run until it's designed.
    # This documented boundary is why the resume probes above are `missing` (unbuilt), not `fail`.
    sc_doc = (StepConfirm.__doc__ or "").lower()
    checks.append(expect("resume" in sc_doc and "deferred" in sc_doc,
                         "per-write resume is documented as a deliberate deferred slice (not an accidental gap)",
                         f"StepConfirm docstring no longer marks resume deferred: {sc_doc[:120]!r}"))
    # PARTIAL CREDIT (shipped): the ONLY shipped idempotency skip is precheck_* — a STATELESS one-shot
    # end-state probe (per FLOW, not per row). It is NOT a durable ledger: it can't tell WHICH of 500
    # rows already landed, so run_batch cannot lean on it for resume. Confirm the shipped one-shot shape.
    ms_fields = {f.name for f in dataclasses.fields(MutateSpec)}
    checks.append(expect({"precheck_url", "precheck_text_contains"} <= ms_fields,
                         "precheck_* one-shot skip is shipped (stateless end-state probe, NOT a per-row ledger)",
                         f"MutateSpec precheck fields missing: {sorted(ms_fields)}"))
    return checks


# --- H3 slice 2: the VOLUME trust surface — one approval authorizing N writes needs per-row bounds ---
@scenario(
    id="h03b.batch.approval_bounds",
    title="volume trust surface: run_all is flow-granular; a 500-row single-flow write batch needs per-row bounds",
    group="h03b", aspirational=True, tags=("batch", "writes", "approval"),
)
async def approval_bounds(ctx: Ctx):
    import ultracua.flows as flows_mod
    from ultracua.flows import FleetRun, run_all

    checks = []
    # PARTIAL CREDIT (shipped): run_all is FLOW-granular — `names` is a list of distinct flow NAMES,
    # one replay each (FleetRun carries no row concept). DANGER framing: a 500-ROW SINGLE-FLOW write
    # batch is a NEW trust surface run_all was never shaped for — one flow, many side effects.
    sig = inspect.signature(run_all)
    fr_fields = {f.name for f in dataclasses.fields(FleetRun)}
    checks.append(expect("names" in sig.parameters and not ({"rows", "row"} & fr_fields),
                         "run_all is flow-granular (one run per flow NAME) — a 500-row single-flow batch is new",
                         f"run_all params={list(sig.parameters)}, FleetRun fields={sorted(fr_fields)}"))
    # PARTIAL CREDIT (shipped): writes require EXPLICIT opt-in + approval. DANGER: today approval is
    # one-flow-one-write; a batch means ONE approval authorizes N distinct writes, so the safe defaults
    # (include_writes=False + approved_only=True) are the floor run_batch must not silently lower.
    checks.append(expect(sig.parameters["include_writes"].default is False
                         and sig.parameters["approved_only"].default is True,
                         "run_all keeps writes opt-in + approved-only (the floor a batch must not lower)",
                         f"defaults drifted: {[(p.name, p.default) for p in sig.parameters.values()]}"))
    # ASPIRATIONAL: run_batch must BOUND how much one approval authorizes — a per-row cap / max_rows /
    # explicit approved-row-count so a single stale approval can't authorize arbitrarily many writes.
    # Not built -> missing.
    rb = getattr(flows_mod, "run_batch", None)
    bound_kw = {"max_rows", "approve_bound", "max_writes", "row_cap", "limit"}
    has_bound = callable(rb) and bool(bound_kw & set(inspect.signature(rb).parameters))
    checks.append(expect(has_bound,
                         "run_batch bounds how many writes one approval authorizes (per-row cap / max_rows)",
                         "no per-row approval bound — one approval could authorize unbounded writes",
                         aspirational=True))
    # ASPIRATIONAL: a DRY-RUN / preview — validate + plan all N rows and actuate NONE — so an operator
    # can review the full batch (and its row-keys) before a single write fires. Not built -> missing.
    dry_kw = {"dry_run", "preview", "plan_only", "plan"}
    has_dry = callable(rb) and bool(dry_kw & set(inspect.signature(rb).parameters))
    checks.append(expect(has_dry,
                         "run_batch supports a dry-run/preview (plan all rows, actuate none) before approval",
                         "no dry-run — an operator can't preview 500 rows before the first write fires",
                         aspirational=True))
    return checks
