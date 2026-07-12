"""H3 slice 2 evals — the run_batch cluster: VOLUME + per-row FAIL-LOUD + RESUME (ROADMAP H3).

The `run_batch(spec, rows)` verb takes N pre-validated rows and drives ONE parameterized flow per row —
the VOLUME side of typed templates, a ROW-granular sibling of `run_all`. Slice 2b (the driver: all-or-
nothing pre-flight, duplicate-row refusal, max_rows approval bound, fail-loud isolation, dry-run) IS
shipped. Only the per-row RESUME ledger (slice 2c) remains — a durable row-keyed completed-run ledger
+ a resume entry point come out `missing` (the target). The BUILDING BLOCKS run_batch rides on:
  - `flows.run_all` — the fleet supervisor (safe defaults: approved-only + read-only);
  - `flows.validate_params` / `_preflight_row` — the pure 0-LLM per-row gate (out-of-domain -> loud, no I/O);
  - `safety.idempotency_key(..., slot_values=...)` — the row-value key channel (distinct rows ->
    distinct keys; same row on retry -> same key; None/{} -> the base key byte-identical), folded
    into the write gate (2a);
  - the APPROVAL floor: an unapproved parameterized write is refused by the approval gate — run_batch
    does not lower that floor.

Each scenario names the DANGER a run_batch surface must guard: double-write (a re-run re-fires a
landed row), suppressed-write (N rows share ONE dedupe key so a backend drops rows 2..N),
silent-batch-continue (row k fails but rows k+1.. proceed into a wrong state), and the volume trust
surface (ONE approval authorizing 500 distinct writes). Any regression on the shipped safety blocks
must fail LOUD; the 2c resume probes stay `missing` until that ledger ships.
"""

from __future__ import annotations

import dataclasses
import inspect

from evals.core import Ctx, expect, fail, import_probe, missing, ok, probe, scenario
from evals.fixtures import Fixture, page

# A parameterized-write checkout: a REAL method=post form (mutating by STRUCTURE) with a typed qty slot.
_CONFIRM = page("<h1>Order placed</h1>", title="confirm")
_QTY_CHECKOUT = page('<h1>Checkout</h1>'
                     '<form method="post" action="/order">'
                     '<label for="qty">quantity</label><input id="qty" name="qty" value="1">'
                     '<button type="submit">Place the order</button></form>')


# --- H3 slice 2b step 5: the run_batch verb + the safe supervisor posture it inherits ------------
@scenario(
    id="h03b.batch.run_batch_verb",
    title="run_batch verb exists (N rows, row-granular) + the safe read-only/approved-only posture it inherits",
    group="h03b", tags=("batch", "writes", "slots"),
)
async def run_batch_verb(ctx: Ctx):
    import ultracua.flows as flows_mod
    from ultracua.flows import FlowReplayError, FlowSpec, MutateSpec, SlotSpec, replay

    checks = []
    # SHIPPED (2b): the headline verb. A dedicated N-row driver is where per-row bounds + resume live —
    # run_batch is a ROW-granular sibling of run_all (one run per flow), with per-row failure isolation.
    checks.append(expect(callable(getattr(flows_mod, "run_batch", None)),
                         "flows.run_batch exists (N pre-validated rows -> one parameterized run each)",
                         "no run_batch verb — a volume batch has no home but run_all (flow-granular)"))
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
    # PARTIAL CREDIT (shipped APPROVAL floor): slice 2a LIFTED the blanket parameterized-write refusal —
    # a DECLARED write slot now runs through the normal gates. So run_batch's per-row parameterized writes
    # are bounded by APPROVAL, not a blanket ban. This UNLEARNED flow is stopped by the approval gate (a
    # write is human-verified before an unattended run), so a batch can't silently ride an unapproved
    # write path. This check going RED (an unapproved write RUNNING) would be a real write-safety regression.
    spec = FlowSpec(name="h03b-approval", start_url="http://127.0.0.1:9/checkout", goal="submit a row",
                    mutate=MutateSpec(confirm_text_contains="Saved"),
                    slots={"amount": SlotSpec(type="integer", min=1)})
    try:
        out = await replay(spec, params={"amount": 5}, cache=ctx.cache())
        checks.append(fail("an unapproved parameterized WRITE is refused by the approval gate",
                           f"replay RAN an unapproved parameterized write: {out!r}"))
    except FlowReplayError as exc:
        msg = str(exc).lower()
        checks.append(expect("not approved" in msg,
                             "an unapproved parameterized WRITE is refused by the approval gate (writes need sign-off)",
                             f"raised FlowReplayError but not the approval gate: {exc}"))
    return checks


# --- H3 slice 2b BEHAVIORAL: run_batch drives a write batch SAFELY (server-side write oracle) ------
@scenario(
    id="h03b.batch.run_batch_write_safety",
    title="run_batch write batch: dry-run actuates nothing, bad/duplicate rows refuse ZERO writes, valid rows key distinctly",
    group="h03b", tags=("batch", "writes", "slots", "fail-loud"),
)
async def run_batch_write_safety(ctx: Ctx):
    from ultracua.cache import flow_key
    from ultracua.flows import (FlowReplayError, FlowSpec, MutateSpec, SlotSpec, approve, record,
                                run_batch)

    checks = []
    fx = Fixture({"/checkout": _QTY_CHECKOUT, "/confirm": _CONFIRM}, post_redirect="/confirm")
    with fx.serve() as base:
        cache = ctx.cache()
        spec = FlowSpec(name="batchorder", start_url=f"{base}/checkout", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Order placed"),
                        slots={"qty": SlotSpec(type="string", pattern="[0-9]{1,3}")}, headless=True)

        async def _demo(pw_page) -> None:
            await pw_page.fill("#qty", "7")
            await pw_page.locator("#qty").blur()
            await pw_page.get_by_role("button", name="Place the order").click()
            await pw_page.get_by_text("Order placed").wait_for()

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        # Bind the qty fill step explicitly (write mining never lifts a money field).
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        for s in flow.steps:
            if s.action == "type":
                s.slot = "qty"
        cache.put(flow)
        approve(spec, cache=cache)
        checks.append(expect(res.cached and res.is_write, "recorded a gated write flow to batch",
                             f"cached={res.cached} is_write={res.is_write}"))

        n0 = len(fx.writes)  # after the demo's one write; every refusal/dry-run below must add ZERO

        # DRY-RUN over 3 rows: plan + preview each key, actuate NOTHING.
        plan = await run_batch(spec, [{"qty": "9"}, {"qty": "8"}, {"qty": "7"}],
                               max_rows=10, dry_run=True, cache=cache)
        distinct = len({tuple(r.idempotency_keys) for r in plan.rows}) == 3
        checks.append(expect(plan.status == "planned" and len(fx.writes) == n0 and distinct,
                             "dry-run plans 3 rows with DISTINCT key previews and actuates NOTHING",
                             f"status={plan.status} writes_added={len(fx.writes) - n0} distinct={distinct}"))

        # ALL-OR-NOTHING pre-flight: one out-of-domain row refuses the WHOLE batch, zero writes.
        bad = await run_batch(spec, [{"qty": "9"}, {"qty": "NaN"}, {"qty": "8"}], max_rows=10, cache=cache)
        checks.append(expect(bad.status == "invalid" and len(fx.writes) == n0,
                             "a single invalid row refuses the batch pre-flight (good rows never actuate)",
                             f"status={bad.status} writes_added={len(fx.writes) - n0}"))

        # DUPLICATE-ROW refusal: two identical rows would mint one key -> a dedupe suppresses the 2nd.
        dup = await run_batch(spec, [{"qty": "9"}, {"qty": "9"}], max_rows=10, cache=cache)
        checks.append(expect(dup.status == "invalid" and len(fx.writes) == n0,
                             "duplicate rows are refused pre-flight (no silently-suppressed write), zero writes",
                             f"status={dup.status} writes_added={len(fx.writes) - n0}"))

        # APPROVAL BOUND: a write batch with no max_rows, and one that exceeds it, both refuse before acting.
        async def _refused(rows, **kw):
            try:
                await run_batch(spec, rows, cache=cache, **kw)
                return False
            except FlowReplayError:
                return True

        no_bound = await _refused([{"qty": "9"}])                        # max_rows omitted -> refuse
        over = await _refused([{"qty": "9"}, {"qty": "8"}], max_rows=1)   # exceeds the cap -> refuse
        checks.append(expect(no_bound and over and len(fx.writes) == n0,
                             "max_rows is required for a write batch AND enforced — both refuse, zero writes",
                             f"no_bound={no_bound} over={over} writes_added={len(fx.writes) - n0}"))

        # A VALID committed batch: 2 rows -> 2 writes, each carrying a DISTINCT wire Idempotency-Key.
        good = await run_batch(spec, [{"qty": "9"}, {"qty": "8"}], max_rows=10, cache=cache)
        wire_keys = [w.headers.get("idempotency-key") for w in fx.writes[n0:]]
        checks.append(expect(good.status == "ok" and good.ok_count == 2 and len(wire_keys) == 2
                             and wire_keys[0] != wire_keys[1] and all(k for k in wire_keys),
                             "a valid batch commits each row once with DISTINCT Idempotency-Key headers",
                             f"status={good.status} ok={good.ok_count} wire_keys={wire_keys}"))
    return checks


# --- H3 slice 2: per-row PRE-FLIGHT — validate every row against the slot schema before any action --
@scenario(
    id="h03b.batch.per_row_preflight",
    title="per-row pre-flight: validate_params is the pure 0-LLM per-row validator (good row PASS, bad row LOUD)",
    group="h03b", tags=("batch", "slots", "preflight", "fail-loud"),
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
    # SHIPPED (2b): run_batch calls this validator PER ROW, up front — a bad row at position k fails the
    # batch (all-or-nothing pre-flight) without ANY row's browser action. Probed as: does run_batch accept
    # a `rows` list to pre-validate?
    rb = getattr(flows_mod, "run_batch", None)
    has_rows = callable(rb) and "rows" in inspect.signature(rb).parameters
    checks.append(expect(has_rows,
                         "run_batch takes a rows=[...] list it pre-validates per row before acting",
                         "no run_batch verb to feed rows through validate_params"))
    return checks


# --- H3 slice 2: per-row FAIL-LOUD isolation — row k's failure is its own report, no silent continue -
@scenario(
    id="h03b.batch.per_row_fail_loud",
    title="per-row fail-loud: run_batch returns a per-row {status, rows:[...]} so row k's failure is isolated",
    group="h03b", tags=("batch", "fail-loud", "writes"),
)
async def per_row_fail_loud(ctx: Ctx):
    import inspect as _inspect

    import ultracua.flows as flows_mod
    from ultracua.flows import FleetRun, run_all

    checks = []
    # SHIPPED (2b): the per-row result shape. If a row fails, run_batch records THAT row as failed and (in
    # stop mode) never silently rolls on into rows k+1.. as if nothing broke. run_batch returns a typed
    # BatchRun dataclass with a `.rows` list of per-row outcomes.
    rb = getattr(flows_mod, "run_batch", None)
    if not callable(rb):
        checks.append(missing("run_batch returns a per-row result {status, rows:[...]} (row k isolated)",
                              "no run_batch verb — no per-row result surface to inspect"))
    else:
        st, out = await probe(rb, None, [])
        shaped = st == "ok" and dataclasses.is_dataclass(out) and isinstance(getattr(out, "rows", None), list)
        checks.append(expect(shaped, "run_batch returns a per-row result (BatchRun.rows list, row k isolated)",
                             f"got {st}: {out!r}"))
    # SHIPPED (2b): an EXPLICIT per-row isolation policy (stop-the-batch vs continue-and-report). A write
    # batch must not default to blindly continuing past a failed row; the policy is a first-class knob
    # (`on_row_error`, default "stop"). Probe run_batch for it.
    knobs = {"stop_on_error", "on_row_error", "isolate", "continue_on_error"}
    has_policy = callable(rb) and bool(knobs & set(_inspect.signature(rb).parameters))
    checks.append(expect(has_policy,
                         "run_batch exposes an explicit per-row failure policy (stop vs continue, never silent)",
                         "no per-row failure-policy knob (a failed row must not silently continue the batch)"))
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
    title="row-keyed idempotency: slot-value key channel + the write gate folds the run's row values (shipped 2a)",
    group="h03b", tags=("batch", "idempotency", "writes"),
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
    # SHIPPED (2a): the write ACTUATION gate FOLDS the run's slot values into the key it mints —
    # flow._replay_step now mints idempotency_key(scope, idx, step.intent, slot_values=params). DANGER
    # guarded: the pre-2a shape (no slot channel) meant all N rows of a parameterized write minted the SAME
    # key at actuation and a dedupe would drop rows 2..N. This going RED is a real suppressed-write regression.
    fn = getattr(flow_mod, "_replay_step", None)
    try:
        gate_src = inspect.getsource(fn) if fn else ""
    except (OSError, TypeError):
        gate_src = ""
    gate_folds = bool(gate_src) and "slot_values" in gate_src
    checks.append(expect(gate_folds,
                         "the write actuation gate folds the run's slot values into the idempotency key",
                         "flow._replay_step mints idempotency_key(scope, idx, intent) with no slot "
                         "channel — every row would share ONE key at actuation (suppressed-write)"))
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
    title="volume trust surface: run_batch bounds a single-flow write batch (max_rows required) + a dry-run preview",
    group="h03b", tags=("batch", "writes", "approval"),
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
    # SHIPPED (2b): run_batch BOUNDS how much one approval authorizes — `max_rows` is REQUIRED for a write
    # batch (refuse if absent) and refuses when exceeded, so a single stale approval + a huge file can't
    # fan out unbounded writes.
    rb = getattr(flows_mod, "run_batch", None)
    bound_kw = {"max_rows", "approve_bound", "max_writes", "row_cap", "limit"}
    has_bound = callable(rb) and bool(bound_kw & set(inspect.signature(rb).parameters))
    checks.append(expect(has_bound,
                         "run_batch bounds how many writes one approval authorizes (per-row cap / max_rows)",
                         "no per-row approval bound — one approval could authorize unbounded writes"))
    # SHIPPED (2b): a DRY-RUN / preview — validate + plan all N rows and actuate NONE — so an operator can
    # review the full batch (and its row-keys) before a single write fires.
    dry_kw = {"dry_run", "preview", "plan_only", "plan"}
    has_dry = callable(rb) and bool(dry_kw & set(inspect.signature(rb).parameters))
    checks.append(expect(has_dry,
                         "run_batch supports a dry-run/preview (plan all rows, actuate none) before approval",
                         "no dry-run — an operator can't preview 500 rows before the first write fires"))
    return checks
