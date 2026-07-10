"""H3b evals: the WRITE-template lifecycle (H3 slice 2 — write templates + run_batch + row-keyed
idempotency). RISK-FOCUSED and ASPIRATIONAL — these probe the DANGEROUS surfaces slice 2 must
handle SAFELY, so most capability probes come out `missing` (the target) while the shipped
building blocks slice 2 rides on earn PARTIAL CREDIT (`pass`). A `fail` here is reserved for a
SHIPPED write-safety property that MISBEHAVES — a real regression must fail loud.

The write-lifecycle inviolables under test:
- parameterizing a WRITE is REFUSED today (read-only guard) — a `params` write never opens a browser
  (slice-2-not-built baseline; flips to a real run when write templates ship);
- a write template's COMMIT step can NEVER be verify-by-replayed (re-firing = double-submit) — slice
  2 can only re-drive the pre-write PREFIX with a distinct value vector, so the commit's cross-value
  generalization stays structurally UNPROVEN (the honest limit these notes must state);
- mining a WRITE flow must NEVER auto-lift a slot — a silently-parameterized payee/amount is a
  money-moving injection surface; a write field is templatized only with explicit human sign-off;
- a slot-schema change since approve() must refuse replay until re-approved (a widened domain under a
  stale approval is an injection surface) — the approval gate + write-refuses-relearn are shipped, the
  schema-hash binding is not;
- the value-independence audit that gates READ templates today must gate WRITE slots too once mining
  can lift them (a write slot echoing into a later locator = a dead AND dangerous template).
"""

from __future__ import annotations

import dataclasses
import inspect

from evals.core import Ctx, expect, fail, missing, ok, probe, scenario
from evals.fixtures import Fixture, page

# --- write-flow fixtures (a REAL method=post form so the submit click is classified mutating by the
# form's STRUCTURE, not intent keywords; the POST lands in the fixture as the wire-write oracle) ----
_TRANSFER = page('<h1>Transfer</h1>'
                 '<form method="post" action="/transfer">'
                 '<label for="payee">payee</label><input id="payee" name="payee" value="">'
                 '<label for="amount">amount</label><input id="amount" name="amount" value="">'
                 '<button type="submit">Send money</button></form>')
_SENT = page("<h1>Transfer complete</h1><p>Sent</p>", title="sent")


async def _demo_transfer(pg) -> None:
    """Scripted 'human' demo of a money-moving write: fill payee + amount (the fields a wrongly-lifted
    slot would turn into an injection surface), then submit and wait for the landed confirmation."""
    await pg.fill("#payee", "Acme Corp")
    await pg.fill("#amount", "100.00")
    await pg.locator("#amount").blur()
    await pg.get_by_role("button", name="Send money").click()
    await pg.get_by_text("Transfer complete").wait_for()


# =================================================================================================
# (1) SHIPPED baseline: a parameterized WRITE replay is REFUSED, pre-flight, before any browser work.
# =================================================================================================
@scenario(
    id="h03b.write.param_gate_refused",
    title="parameterized WRITE replay is refused pre-flight (read-only guard) — no browser dialed",
    group="h03b", tags=("writes", "gate", "baseline"),
)
async def param_gate_refused(ctx: Ctx):
    from ultracua.flows import FlowReplayError, FlowSpec, MutateSpec, replay

    checks = []
    # The fixture is SERVED and the write's start_url points at it — so `fx.gets == []` afterwards is a
    # real oracle that the refusal short-circuits BEFORE any navigation (a live, dial-able URL never
    # dialed), not merely that the port was closed.
    fx = Fixture({"/checkout": _TRANSFER})
    with fx.serve() as base:
        cache = ctx.cache()
        spec = FlowSpec(name="pw", start_url=f"{base}/checkout", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Transfer complete"), headless=True)
        raised = "NONE"
        try:
            await replay(spec, params={"amount": "9999.00"}, cache=cache)
        except FlowReplayError as e:
            raised = str(e)
        except Exception as e:  # noqa: BLE001 — a WRONG exception type is itself a regression
            raised = f"__WRONG__ {type(e).__name__}: {e}"
        lo = raised.lower()
        refused = raised != "NONE" and not raised.startswith("__WRONG__")
        # DANGER: if this guard lapses, a `params` write would flow into the un-built row-keyed-write
        # path and could double-submit / mishandle the write. It must stay frozen-only until slice 2.
        checks.append(expect(refused,
                             "parameterized WRITE replay raises FlowReplayError (shipped read-only guard)",
                             f"a params-write was NOT refused — got: {raised[:180]}"))
        # the refusal must NAME the gap (so an operator knows to drop params / wait for the next slice)
        msg_ok = ("parameter" in lo and "write" in lo
                  and ("read-only" in lo or "next slice" in lo or "supported yet" in lo))
        checks.append(expect(msg_ok,
                             "the refusal names parameterized writes as the next-slice gap",
                             f"message doesn't identify the param-write gap: {raised[:200]}"))
        # ORDERING: the write is refused WHOLESALE before per-slot validation. `spec` has no slots, so
        # if validate_params ran first, params={amount} would come back as an "unknown param" error.
        # DANGER: a write leaking through per-slot validation could let a mis-declared slot reach the
        # wire; the write gate must fire first, unconditionally.
        checks.append(expect(refused and "unknown param" not in lo,
                             "the write refusal PRECEDES slot validation (refused wholesale, not per-slot)",
                             f"got a per-slot validation error instead of the write gate: {raised[:200]}"))
        # server-side truth: NO GET reached the fixture — the refusal happened before the browser opened
        # DANGER: an unsupported write parameterization must never open a browser / touch the live site.
        checks.append(expect(fx.gets == [],
                             "no browser navigation dialed the start_url (refusal is pre-flight)",
                             f"the browser navigated before refusing: gets={fx.gets}"))
    return checks


# =================================================================================================
# (2) ASPIRATIONAL: PREFIX-ONLY verification. A write template's commit can't be replay-verified.
# =================================================================================================
@scenario(
    id="h03b.write.prefix_verification_horizon",
    title="write templates verify only the PRE-write prefix — the commit's generalization stays unproven",
    group="h03b", aspirational=True, tags=("writes", "verification", "aspirational"),
)
async def prefix_verification_horizon(ctx: Ctx):
    import ultracua.flow as flow_mod
    import ultracua.flows as flows_mod

    checks = []
    rep_params = set(inspect.signature(flows_mod.replay).parameters)
    rec_params = set(inspect.signature(flows_mod.record).parameters)
    verify_names = [n for n in dir(flows_mod)
                    if any(t in n.lower() for t in
                           ("verify_prefix", "prefix_verify", "verify_template", "verify_slots"))]
    # Capability: a prefix-verification surface — re-drive ONLY the pre-write steps (never the commit)
    # against a second value vector to prove the fill sites generalize.
    # HONEST LIMIT: the commit step can NEVER be verify-by-replayed (re-firing it = double-submit), so a
    # write template's cross-value generalization at the commit is structurally UNPROVABLE by replay —
    # slice 2 can only assert the PREFIX. This is the danger this whole scenario documents.
    has_prefix_verify = bool(verify_names) or ("verify" in rep_params) or ("verify_prefix" in rep_params) \
        or ("verify" in rec_params)
    checks.append(expect(has_prefix_verify,
                         "a prefix-verification surface exists (re-drive the pre-write prefix, never the commit)",
                         "no prefix-verify surface — a write template's commit can't be replay-verified "
                         "without double-submitting, so its cross-value generalization stays UNPROVEN",
                         aspirational=True))
    # Capability: a distinct-value-vector input to that verification (a second row to prove the prefix
    # isn't overfit to the demo value). Absent today.
    vv = any(k in rec_params for k in ("verify_values", "sample_values", "probe_values")) \
        or any(k in rep_params for k in ("verify_values", "sample_values"))
    checks.append(expect(vv,
                         "record/replay accept a second value vector to prove the prefix generalizes",
                         "no distinct-value-vector input — slice 2 could prove the prefix on ONE vector "
                         "only; commit generalization stays unproven either way", aspirational=True))
    # PARTIAL CREDIT (shipped): the never-re-run-a-write mechanism prefix verification rides on —
    # `_author_steps(block_mutations=...)` refuses to EXECUTE a mutating action on a re-drive.
    bm = "block_mutations" in inspect.signature(flow_mod._author_steps).parameters
    checks.append(expect(bm,
                         "flow._author_steps exposes block_mutations (re-drive prefix, refuse the commit)",
                         "lost the block_mutations control — the mechanism slice-2 prefix verification rides on"))
    # PARTIAL CREDIT (shipped): and the replay-repair path actually WIRES it on — a replay-triggered
    # re-author must never perform a NEW (unapproved) write.
    # DANGER: without this, a suffix-replan re-driving a drifted flow could re-fire the commit = double-submit.
    src = inspect.getsource(flow_mod)
    checks.append(expect("block_mutations=True" in src,
                         "the replay-repair path is wired to refuse NEW writes (block_mutations=True)",
                         "no block_mutations=True call site in flow.py — a replay re-author could double-submit"))
    return checks


# =================================================================================================
# (3) ASPIRATIONAL: mining a WRITE flow must NEVER auto-lift a slot (money-field injection safety).
# =================================================================================================
@scenario(
    id="h03b.write.mining_never_lifts_money_field",
    title="mining a WRITE flow lifts NO slots — a money field is never silently parameterized",
    group="h03b", tags=("writes", "recorder", "slots"),
)
async def mining_never_lifts_money_field(ctx: Ctx):
    from ultracua.flows import FlowSpec, MutateSpec, record

    checks = []
    fx = Fixture({"/pay": _TRANSFER, "/done": _SENT}, post_redirect="/done")
    with fx.serve() as base:
        spec = FlowSpec(name="wire", start_url=f"{base}/pay", goal="send the transfer",
                        mutate=MutateSpec(confirm_text_contains="Transfer complete"), headless=True)
        # mine_slots=True asks the recorder to auto-lift fill/select values into typed slots. For a WRITE
        # flow the recorder must RETURN before mining ever runs (the write path exits early) — the demo
        # fills payee + amount, exactly the fields that must NOT become parameters.
        st, out = await probe(record, spec, demo=_demo_transfer, headless=True,
                              cache=ctx.cache(), mine_slots=True)
        if st != "ok":
            checks.append(fail("record(write, mine_slots=True) runs",
                               f"recording the write flow errored: {type(out).__name__}: {out}"))
            return checks
        res = out
        # sanity + oracle: a real gated money write WAS demonstrated (so "no slots lifted" is meaningful,
        # not vacuous — a genuine POST left the browser and the flow cached as a write).
        checks.append(expect(res.is_write and res.cached and len(fx.writes) == 1,
                             "the write demo captured a gated, cached write (one POST reached the server)",
                             f"is_write={res.is_write} cached={res.cached} "
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
        # THE key safety check: NO slot was lifted despite the demo filling payee + amount.
        # DANGER: a wrongly-lifted payee/amount is a money-moving injection surface — a params={...}
        # redirect of funds or an altered amount. A write field must NEVER be silently parameterized.
        checks.append(expect(not res.spec.slots,
                             "mining lifted NO slots on the write (payee/amount not auto-parameterized)",
                             f"a WRITE field was silently lifted into a slot: {res.spec.slots}"))
        # and the audit/mining machinery never even ran on the write (the write path exits before it)
        checks.append(expect(not res.slot_findings,
                             "no slot-mining/audit ran on the write flow (write path exits before mining)",
                             f"slot_findings unexpectedly populated for a write: {res.slot_findings}"))
        # the commit was NOT verify-by-replayed — re-firing it would double-submit the transfer.
        # DANGER: verify-by-replaying a write is a double-write; a recorded write is trusted via the human
        # demo + the approval gate, never an automated re-run.
        checks.append(expect(res.reproduced is False,
                             "the write flow was NOT verify-by-replayed (re-firing the commit = double-submit)",
                             f"a write was replayed to verify it: reproduced={res.reproduced}"))
    # Capability: an EXPLICIT per-write slot-confirmation surface (a money field parameterized ONLY with
    # human sign-off). MutateSpec.step_confirms is per-write TEXT barriers, NOT slot approval — no
    # surface lets an author opt a write field into templating. Slice 2 must add one; auto-lift is banned.
    from ultracua.flows import MutateSpec as _MS
    mut_fields = {f.name for f in dataclasses.fields(_MS)}
    rec_params = set(inspect.signature(record).parameters)
    explicit = any(k in mut_fields for k in
                   ("slot_confirms", "confirm_slots", "writable_slots", "confirmable_slots", "approved_slots")) \
        or any(k in rec_params for k in ("confirm_slots", "writable_slots"))
    checks.append(expect(explicit,
                         "an EXPLICIT per-write slot-confirmation surface exists (money field lifted only with sign-off)",
                         "no per-write slot-confirmation surface — slice 2 must require explicit approval "
                         "before a write field is templatized, never auto-lift", aspirational=True))
    return checks


# =================================================================================================
# (4) ASPIRATIONAL: a slot-schema change since approve() must refuse replay until re-approved.
# =================================================================================================
@scenario(
    id="h03b.write.slot_schema_approval_gate",
    title="a slot-schema change since approve() must refuse replay until re-approved (schema-hash binding)",
    group="h03b", aspirational=True, tags=("writes", "approval", "slots", "aspirational"),
)
async def slot_schema_approval_gate(ctx: Ctx):
    import time

    from ultracua.cache import CachedFlow, CachedStep, flow_key
    from ultracua.flows import (FlowMeta, FlowReplayError, FlowSpec, MutateSpec, SlotSpec, _load_meta,
                                approve, replay, unapprove)

    checks = []
    meta_fields = {f.name for f in dataclasses.fields(FlowMeta)}
    # Capability: FlowMeta binds the APPROVED slot schema (a hash/fingerprint), so a later domain change
    # can be detected and force re-approval.
    # DANGER: without it, an operator who widens a slot domain AFTER approve() (e.g. drops a payee enum,
    # loosening it to any string) leaves replay honoring the NEW, un-approved contract — a stale-approval
    # injection surface, worst on a WRITE flow.
    has_hash_field = any(k in meta_fields for k in
                         ("slots_hash", "slot_schema_hash", "schema_hash", "slots_fingerprint",
                          "approved_slots_hash"))
    checks.append(expect(has_hash_field,
                         "FlowMeta binds the approved slot-schema (a hash/fingerprint field)",
                         "FlowMeta has no slot-schema hash — a widened domain under a stale approval "
                         "replays un-re-approved (stale-approval injection)", aspirational=True))

    # Behavioral probe (no browser): approve a SLOTTED flow, then widen its domain and re-load meta.
    cache = ctx.cache()
    goal, url = "search widgets", "http://127.0.0.1:9/app/"
    spec = FlowSpec(name="srch", start_url=url, goal=goal, slots={"query": SlotSpec(type="string")})
    key = flow_key(goal, url, spec.scope)
    cache.put(CachedFlow(key=key, goal=goal, start_url=url,
                         steps=[CachedStep(intent="type query", action="type", text="widget", slot="query")],
                         created_ts=time.time()))
    approve(spec, cache=cache)
    approved_after = _load_meta(cache, key).approved
    # a real operator edit: close the domain to a single value AFTER approval
    spec.slots["query"] = SlotSpec(type="string", enum=["only-this"])
    meta_after_edit = _load_meta(cache, key)
    # a schema-aware gate would flip approval off (or record a mismatching digest); today nothing changes.
    detected = (not meta_after_edit.approved) or any(("slot" in f or "schema" in f) for f in meta_fields)
    checks.append(expect(detected,
                         "approval is bound to the slot schema (a post-approval domain change needs re-approval)",
                         "approval survived a slot-domain change unchallenged — no digest recorded, "
                         "meta.approved stayed True", aspirational=True))

    # PARTIAL CREDIT (shipped): the approval gate itself toggles trust — the hook a schema-hash bolts onto.
    unapprove(spec, cache=cache)
    cleared = not _load_meta(cache, key).approved
    checks.append(expect(approved_after and cleared,
                         "the approval gate toggles trust (approve sets, unapprove clears)",
                         f"approve/unapprove did not toggle trust: approved={approved_after} cleared={cleared}"))

    # PARTIAL CREDIT (shipped): a WRITE flow REFUSES on_drift='relearn' LOUD — re-authoring would re-perform
    # the write, so a drifted write must escalate to a human re-learn + re-approve, never silently relearn.
    # DANGER: silent relearn of a write = an unapproved, un-verified write re-fired on drift.
    wspec = FlowSpec(name="wrelearn", start_url="http://127.0.0.1:9/x", goal="place the order",
                     mutate=MutateSpec(confirm_text_contains="Order placed"))
    raised = "NONE"
    try:
        await replay(wspec, on_drift="relearn", cache=cache)
    except FlowReplayError as e:
        raised = str(e)
    except Exception as e:  # noqa: BLE001
        raised = f"__WRONG__ {type(e).__name__}: {e}"
    lo = raised.lower()
    checks.append(expect("relearn" in lo and "write" in lo,
                         "a write flow refuses on_drift='relearn' LOUD (re-authoring would re-perform the write)",
                         f"write did not refuse silent relearn: {raised[:180]}"))
    return checks


# =================================================================================================
# (5) SHIPPED: the value-independence audit (read-side today) — the gate WRITE slots will reuse.
# =================================================================================================
@scenario(
    id="h03b.write.value_independence_audit_shipped",
    title="the value-independence audit refuses value-echo templates (the gate write slots must reuse)",
    group="h03b", tags=("writes", "audit", "slots"),
)
async def value_independence_audit_shipped(ctx: Ctx):
    from ultracua.flows import FlowSpec, RecordResult, record

    checks = []
    # PARTIAL CREDIT (shipped): the audit-findings surface exists on the record result. When slice 2 lets
    # mining lift WRITE slots, this SAME surface must carry their leak findings — a write slot echoing into
    # a later locator is a dead AND dangerous template.
    rr_fields = {f.name for f in dataclasses.fields(RecordResult)}
    checks.append(expect("slot_findings" in rr_fields,
                         "RecordResult exposes slot_findings (the audit surface write slots will reuse)",
                         f"no slot_findings on RecordResult: fields={sorted(rr_fields)}"))

    # SHIPPED behavior: the read-side value-independence audit REFUSES to templatize a value-echo flow.
    # The demo types 'X17'; the next page renders that value inside the link the flow then clicks, so every
    # non-demo value would break the locator (a dead template). The audit must refuse at authoring.
    # DANGER (write forward-look): once write slots can be mined, the same echo on a write is a dead+unsafe
    # template — this regressing to a silent cache is exactly the write-safety failure the suite must catch.
    leaky = Fixture({
        "/": page('<form action="/results" method="get">'
                  '<label for="q">query</label><input id="q" name="q">'
                  '<button type="submit">search</button></form>'),
        "/results": page('<a href="/detail">open report X17</a>'),
        "/detail": page('<h1>report X17</h1><p>detail</p>'),
    })
    with leaky.serve() as base:
        spec = FlowSpec(name="echo", start_url=base + "/", goal="open the flagged report")

        async def _demo_echo(pg) -> None:
            await pg.fill("#q", "X17")
            await pg.locator("#q").blur()
            await pg.click("button")
            lk = pg.get_by_role("link", name="open report X17")
            await lk.wait_for()
            await lk.click()

        st, out = await probe(record, spec, demo=_demo_echo, headless=True,
                              cache=ctx.cache(), mine_slots=True)
    if st != "ok":
        checks.append(fail("the value-independence audit runs on a value-echo demo",
                           f"recording errored: {type(out).__name__}: {out}"))
    else:
        res = out
        note = (res.note or "").lower()
        refused = (not res.cached) and any(f.get("value_leak") for f in res.slot_findings) \
            and any(w in note for w in ("value", "independen", "leak", "echo"))
        checks.append(expect(refused,
                             "the audit REFUSES a value-echo template and reports the leak (shipped, fail-loud)",
                             f"a leaky template cached silently: cached={res.cached} "
                             f"note={res.note[:120]!r} findings={res.slot_findings}"))

    # SHIPPED precision: the audit does NOT false-refuse a CLEAN flow — a lone non-echoing fill is lifted
    # into a typed slot and cached. Over-refusal would make templates useless; under-refusal ships a dead
    # template. This check pins the safe half so a regression in either direction fails loud.
    clean = Fixture({"/": page('<label for="q">tracking code</label><input id="q">')})
    with clean.serve() as base2:
        spec2 = FlowSpec(name="clean", start_url=base2 + "/", goal="enter the tracking code")

        async def _demo_clean(pg) -> None:
            await pg.fill("#q", "alpha-7")
            await pg.locator("#q").blur()

        st2, out2 = await probe(record, spec2, demo=_demo_clean, headless=True,
                                cache=ctx.cache(), mine_slots=True)
    if st2 != "ok":
        checks.append(fail("the audit mines a clean flow without false-refusing",
                           f"recording errored: {type(out2).__name__}: {out2}"))
    else:
        res2 = out2
        clean_ok = res2.cached and bool(res2.spec.slots) \
            and all(not f.get("value_leak") for f in res2.slot_findings)
        checks.append(expect(clean_ok,
                             "the audit lifts a clean slot and caches (no false-refusal — shipped precision)",
                             f"a clean flow was refused or not templatized: cached={res2.cached} "
                             f"slots={res2.spec.slots} findings={res2.slot_findings}"))
    return checks
