"""H3 evals: typed flow templates (auto-parameterization) — ROADMAP.md H3.

The horizon: flows stop being input-frozen. The recorder mines slot candidates from fill/select/
press steps and captures their LEGAL DOMAINS from site metadata (<select> options, pattern/
required/min/max/datalist); the flow publishes a typed input contract with 0-LLM pre-flight
validation; `replay(spec, params={...})` substitutes values at the fill sites; a `run_batch` verb
runs N rows with row-keyed idempotency; and a value-independence AUDIT refuses to templatize when
the demo value leaked into a later locator/precondition basis (fail loud, never a dead template).

Today almost none of that exists — these scenarios probe each planned surface aspirationally
(`missing`, never `fail`) and give PARTIAL CREDIT to the shipped building blocks the plan rides
on: value-independent flow identity (`flow_key` has no value channel), frozen-literal replay of
recorded fill/select steps, deterministic idempotency-key derivation, and the recorder's
multi-page capture + verify-by-replay pipeline.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import time

from evals.core import Ctx, expect, missing, ok, probe, scenario
from evals.fixtures import Fixture, page


# --- H3 plan step 1: schema plumbing (CachedStep.slot + FlowSpec.slots, additive) ---------------
@scenario(
    id="h03.slots.schema_plumbing",
    title="slot schema plumbing: CachedStep.slot + FlowSpec.slots + SlotSpec (additive, no bump)",
    group="h03", aspirational=True, tags=("slots", "schema"),
)
async def slots_schema_plumbing(ctx: Ctx):
    from ultracua.cache import CachedFlow, CachedStep, flow_key
    from ultracua.flows import FlowSpec

    checks = []
    # Capability: a cached step can be MARKED as a slot site (H3 plan step 1). Field presence is
    # probed on the model (pydantic ignores unknown ctor kwargs, so a constructor probe would
    # silently "succeed" — the field table is the honest signal).
    checks.append(expect("slot" in CachedStep.model_fields,
                         "CachedStep carries a slot marker (which step is parameterized)",
                         "no `slot` field on CachedStep yet", aspirational=True))
    # Capability: a flow publishes a typed slot table (the JSON-Schema-ish input contract).
    # FlowSpec is a dataclass, so an unexpected kwarg raises TypeError -> `missing` via probe().
    status, exc = await probe(FlowSpec, name="t", start_url="http://127.0.0.1/x", goal="g",
                              slots={"query": {"type": "string"}})
    checks.append(expect(status == "ok", "FlowSpec accepts a typed slot table (slots=...)",
                         f"{type(exc).__name__}: {exc}", aspirational=True))
    # Capability: a first-class SlotSpec type ({type, enum, pattern, min/max, required, secret}).
    ok_f, flows_mod = True, None
    try:
        import ultracua.flows as flows_mod  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        ok_f = False
    checks.append(expect(ok_f and hasattr(flows_mod, "SlotSpec"),
                         "a SlotSpec type exists (typed slot contract)",
                         "no SlotSpec in ultracua.flows", aspirational=True))
    # PARTIAL CREDIT (shipped): the plan requires slots to be ADDITIVE — no SCHEMA_VERSION bump.
    # Prove the precondition holds today: a flow file written by a slots-aware FUTURE version
    # (extra `slot` key on a step + a top-level `slots` table) still loads on current code
    # instead of becoming a corrupt-entry miss that would force a fleet-wide relearn.
    cache = ctx.cache()
    key = flow_key("future slots flow", "http://127.0.0.1/x")
    cache.put(CachedFlow(key=key, goal="future slots flow", start_url="http://127.0.0.1/x",
                         steps=[CachedStep(intent="type the query", action="type", text="alpha")],
                         created_ts=time.time()))
    p = cache.root / f"{key}.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["steps"][0]["slot"] = "query"                                  # future per-step marker
    raw["slots"] = {"query": {"type": "string", "enum": ["alpha"]}}    # future flow-level table
    p.write_text(json.dumps(raw), encoding="utf-8")
    got = cache.get(key)
    checks.append(expect(got is not None and got.steps[0].text == "alpha",
                         "a flow file from a slots-aware future version still loads (additive schema)",
                         "unknown slot fields made the cached flow unreadable"))
    return checks


# --- H3 plan step 4: replay(spec, params) + 0-LLM pre-flight validation -------------------------
@scenario(
    id="h03.replay.params_preflight",
    title="parameterized replay: replay(spec, params={...}) + 0-LLM pre-flight validation",
    group="h03", aspirational=True, tags=("slots", "replay"),
)
async def replay_params_preflight(ctx: Ctx):
    import ultracua.flow as flow_mod
    import ultracua.flows as flows_mod

    checks = []
    # Capability: the replay verb takes per-run values. Signature inspection (not a live call):
    # deterministic in every future — a call probe would conflate "kwarg accepted but no cached
    # flow" (FlowReplayError) with "kwarg rejected" (TypeError).
    sig = inspect.signature(flows_mod.replay)
    checks.append(expect("params" in sig.parameters,
                         "flows.replay accepts params={...} (per-run slot values)",
                         "replay() has no params kwarg — flows are input-frozen", aspirational=True))
    # Capability: params thread through the engine to the fill/select/press substitution sites.
    rc_sig = inspect.signature(flow_mod.run_cached)
    checks.append(expect("params" in rc_sig.parameters,
                         "run_cached threads params to the substitution sites",
                         "no params channel through flow.run_cached", aspirational=True))
    # Capability: pure 0-LLM pre-flight validation that rejects an out-of-domain value BEFORE any
    # browser action (the "never silently wrong data" side of parameterization).
    has_preflight = any(hasattr(flows_mod, n) for n in
                        ("validate_params", "preflight", "validate_slots", "preflight_validate"))
    checks.append(expect(has_preflight,
                         "a 0-LLM pre-flight validator exists (out-of-domain -> loud refusal)",
                         "no pre-flight validation surface in ultracua.flows", aspirational=True))
    # PARTIAL CREDIT (shipped): the trust surface pre-flight EXTENDS already exists — replay's
    # unattended-use controls (approval gate, drift raises, shape check). Slots bolt onto these,
    # they don't replace them.
    have_trust = {"require_approved", "on_drift", "check_shape"} <= set(sig.parameters)
    checks.append(expect(have_trust,
                         "shipped replay trust controls (require_approved/on_drift/check_shape) exist",
                         f"replay signature lost its trust controls: {sorted(sig.parameters)}"))
    return checks


# --- H3 plan steps 5-6: run_batch verb + row-keyed idempotency ----------------------------------
@scenario(
    id="h03.batch.row_keys",
    title="batch replay: run_batch verb + row-keyed idempotency (distinct rows, distinct keys)",
    group="h03", aspirational=True, tags=("slots", "batch", "writes"),
)
async def batch_row_keys(ctx: Ctx):
    import ultracua.flows as flows_mod
    from ultracua.safety import idempotency_key

    checks = []
    # Capability: the run_batch verb (N pre-validated rows on the run-all supervisor, per-row
    # fail-loud reports + resume bookkeeping).
    checks.append(expect(callable(getattr(flows_mod, "run_batch", None)),
                         "flows.run_batch exists (N rows, per-row fail-loud)",
                         "no run_batch verb", aspirational=True))
    # PARTIAL CREDIT (shipped): the supervisor pattern run_batch is planned on top of.
    checks.append(expect(callable(getattr(flows_mod, "run_all", None)),
                         "run_all supervisor exists (the pattern run_batch builds on)"))
    # PARTIAL CREDIT (shipped): same-row retry must mint the SAME key — the derivation is
    # deterministic today (the H3 risk list pins this: a wobbling canonicalization would turn a
    # retry into a double-write).
    k1 = idempotency_key("scope-a", 3, "submit the report")
    k2 = idempotency_key("scope-a", 3, "submit the report")
    checks.append(expect(k1 == k2 and k1.startswith("uca-"),
                         "idempotency-key derivation is deterministic (same-row retry, same key)",
                         f"{k1!r} != {k2!r}"))
    # Capability: a ROW-VALUE channel in the key basis. Today the basis is (scope, step_index,
    # intent) only — 500 parameterized rows would mint ONE key and a dedupe layer would silently
    # drop rows 2..N (the silent-missing-write H3 risk). The fix is an extra basis argument.
    sig = inspect.signature(idempotency_key)
    extra = set(sig.parameters) - {"scope", "step_index", "intent"}
    has_row_basis = bool(extra) or any(p.kind is inspect.Parameter.VAR_KEYWORD
                                       for p in sig.parameters.values())
    checks.append(expect(has_row_basis,
                         "idempotency-key basis accepts slot values (distinct rows -> distinct keys)",
                         f"basis is frozen at {tuple(sig.parameters)} — rows would share one key",
                         aspirational=True))
    return checks


# --- H3 plan step 2: recorder domain capture (legal domains from site metadata) -----------------
@scenario(
    id="h03.recorder.domain_capture",
    title="recorder captures a slot's LEGAL DOMAIN (select options, pattern/required/maxlength)",
    group="h03", aspirational=True, tags=("slots", "recorder"),
)
async def recorder_domain_capture(ctx: Ctx):
    from ultracua.recorder import record_demo

    checks = []
    # A native select (3 legal options) + a constrained input — exactly the metadata the H3 plan
    # says the change/keydown listeners should capture via _SPECOF_JS.
    fx = Fixture({"/": page(
        '<label for="country">country</label>'
        '<select id="country">'
        '<option value="opt-us">United States</option>'
        '<option value="opt-lt">Lithuania</option>'
        '<option value="opt-de">Germany</option>'
        '</select> '
        '<label for="qty">quantity</label>'
        '<input id="qty" type="text" pattern="[0-9]{1,3}" required maxlength="3">'
    )})
    with fx.serve() as base:
        async def _demo(pg) -> None:
            await pg.select_option("#country", "opt-lt")   # change -> a `select` step
            await pg.fill("#qty", "42")
            await pg.locator("#qty").blur()                # change fires on blur -> a `type` step

        flow, wrote, crossed, _ = await record_demo(
            base + "/", _demo, goal="set shipping country and quantity",
            cache=ctx.cache(), headless=True)

    sel = next((s for s in flow.steps if s.action == "select"), None)
    typ = next((s for s in flow.steps if s.action == "type"), None)
    # PARTIAL CREDIT (shipped): the recorder freezes the demonstrated CHOICE and LITERAL — the
    # exact values a future params={...} would substitute. These are the slot sites.
    checks.append(expect(sel is not None and sel.text == "opt-lt",
                         "recorded select freezes the demonstrated choice (the slot site exists)",
                         f"steps={[s.action for s in flow.steps]}"))
    checks.append(expect(typ is not None and typ.text == "42",
                         "recorded type freezes the typed literal (the slot site exists)",
                         f"steps={[s.action for s in flow.steps]}"))
    # Capability: does the select step carry its LEGAL OPTION DOMAIN (the un-chosen options)?
    # Without it, pre-flight validation has nothing to validate against. Scan the serialized step
    # for the two options the demo did NOT pick — present only if domain capture exists.
    sel_dump = sel.model_dump_json() if sel else ""
    checks.append(expect(("opt-us" in sel_dump) or ("opt-de" in sel_dump),
                         "select step carries the legal option domain (all options, not just the pick)",
                         "only the chosen value is captured — no domain metadata", aspirational=True))
    # Capability: does the type step carry the input's CONSTRAINT metadata (pattern/required/
    # maxlength/datalist)? That is the typed side of the slot contract for free-text fields.
    typ_dump = typ.model_dump_json() if typ else ""
    checks.append(expect(any(tok in typ_dump for tok in
                             ("[0-9]{1,3}", '"pattern"', '"required"', '"maxlength"', '"datalist"')),
                         "type step carries the input's constraint metadata (pattern/required/max)",
                         "no constraint metadata on the recorded step", aspirational=True))
    return checks


# --- shipped baseline: value-independent identity + frozen-literal replay -----------------------
@scenario(
    id="h03.frozen.value_baseline",
    title="shipped baseline: value-independent flow identity + frozen-literal 0-LLM replay",
    group="h03", tags=("slots", "replay", "baseline"),
)
async def frozen_value_baseline(ctx: Ctx):
    from ultracua.cache import flow_key
    from ultracua.flow import run_cached
    from ultracua.recorder import record_demo

    checks = []
    # PARTIAL CREDIT (shipped): flow identity is value-independent BY CONSTRUCTION — the key
    # basis is (goal, url, scope) only, so H3's "values never enter identity" requirement holds
    # today (plan step 4 keeps flow_key unchanged). Normalization: same goal/url, same key.
    k1 = flow_key("Submit  the Expense report", "http://127.0.0.1:9/app/")
    k2 = flow_key("submit the expense report", "http://127.0.0.1:9/app")
    checks.append(expect(k1 == k2, "flow_key normalizes goal/url (same task, same identity)",
                         f"{k1} != {k2}"))
    checks.append(expect(set(inspect.signature(flow_key).parameters) == {"goal", "url", "scope"},
                         "identity basis has NO value channel (values can't leak into the key)",
                         f"unexpected flow_key params: {tuple(inspect.signature(flow_key).parameters)}"))

    # PARTIAL CREDIT (shipped): a recorded fill replays its FROZEN literal into the live page at
    # 0 LLM calls — today's input-frozen contract, the baseline slots will substitute over. The
    # page echoes every input's value back to the fixture via a SYNCHRONOUS GET (sync XHR: no
    # async race with session close), so fx.gets is the oracle that the literal reached the DOM.
    fx = Fixture({"/": page(
        '<label for="q">tracking code</label><input id="q">'
        '<script>document.getElementById("q").addEventListener("input", (e) => {'
        ' const x = new XMLHttpRequest();'
        ' x.open("GET", "/typed-" + encodeURIComponent(e.target.value), false); x.send();'
        '});</script>'
    )})
    goal = "enter the tracking code"
    with fx.serve() as base:
        cache = ctx.cache()

        async def _demo(pg) -> None:
            await pg.fill("#q", "alpha-7")
            await pg.locator("#q").blur()   # change fires on blur -> the `type` step is captured

        flow, _, _, _ = await record_demo(base + "/", _demo, goal=goal, cache=cache, headless=True)
        typ = next((s for s in flow.steps if s.action == "type"), None)
        checks.append(expect(typ is not None and typ.text == "alpha-7",
                             "the cached step persists the demo literal (frozen at capture)",
                             f"steps={[(s.action, s.text) for s in flow.steps]}"))
        seen_before = fx.gets.count("/typed-alpha-7")
        report = await run_cached(base + "/", goal, None, cache, mode="replay", headless=True)
        checks.append(expect(report.success and report.llm_calls == 0,
                             "recorded flow replays with ZERO LLM calls",
                             f"success={report.success} llm_calls={report.llm_calls} note={report.note!r}"))
        checks.append(expect(fx.gets.count("/typed-alpha-7") > seen_before,
                             "replay re-typed the FROZEN literal into the live page (echo observed)",
                             f"gets={fx.gets}"))
    return checks


# --- H3 plan step 3: the value-independence audit (refuse a value-echo template) ----------------
@scenario(
    id="h03.audit.value_echo_refusal",
    title="value-independence audit: a demo value echoed into a later locator must refuse to cache",
    group="h03", aspirational=True, tags=("slots", "audit", "fail-loud"),
)
async def audit_value_echo_refusal(ctx: Ctx):
    from ultracua.flows import FlowSpec, RecordResult, record

    checks = []
    # The value-echo shape from the H3 risk list: the demo types "X17", and the NEXT page renders
    # that value inside an interactable the flow then clicks. Templatizing this flow would be a
    # safe-but-100%-dead template (every non-demo value changes the locator/scope basis), so the
    # audit must refuse at authoring. Today there is no audit — the flow caches happily.
    fx = Fixture({
        "/": page('<form action="/results" method="get">'
                  '<label for="q">query</label><input id="q" name="q">'
                  '<button type="submit">search</button></form>'),
        "/results": page('<a href="/detail">open report X17</a>'),   # the echoed demo value
        "/detail": page('<h1>report X17</h1><p>detail</p>'),
    })
    with fx.serve() as base:
        spec = FlowSpec(name="echo-demo", start_url=base + "/", goal="open the flagged report")

        async def _demo(pg) -> None:
            await pg.fill("#q", "X17")
            await pg.locator("#q").blur()                      # capture the `type` step
            await pg.click("button")                           # GET-form submit -> /results
            lk = pg.get_by_role("link", name="open report X17")
            await lk.wait_for()
            await lk.click()                                   # click the VALUE-ECHOING element

        res = await record(spec, demo=_demo, headless=True, cache=ctx.cache())

    # PARTIAL CREDIT (shipped): the capture itself — a multi-page same-origin demo lands as
    # [type, click, click] with the literal frozen. True whether or not a future audit refuses.
    acts = [s.action for s in res.steps]
    typed = next((s for s in res.steps if s.action == "type"), None)
    checks.append(expect(acts == ["type", "click", "click"] and typed is not None
                         and typed.text == "X17",
                         "recorder captures the multi-page value-echo demo faithfully",
                         f"actions={acts} typed={typed.text if typed else None}"))
    # PARTIAL CREDIT (diagnostic, shipped behavior): the leak is REAL — the demo value sits in a
    # post-fill LocatorSpec basis. This is precisely the surface the audit must scan (locator
    # text/name/anchor + precond bases + navigate URLs).
    last = res.steps[-1] if res.steps else None
    leak = last is not None and last.locator is not None and any(
        "X17" in (v or "") for v in (last.locator.name, last.locator.text, last.locator.anchor))
    checks.append(expect(leak, "the demo value leaked into a post-fill locator (the audit's input)",
                         f"locator={last.locator if last else None}"))
    # Capability: the audit refusal itself. Refusal = not cached AND a note naming the value-leak
    # cause (so a flaky verify-by-replay miss can't masquerade as an audit). Today it caches.
    note = (res.note or "").lower()
    refused = (not res.cached) and any(w in note for w in ("value", "slot", "leak", "independen"))
    checks.append(expect(refused,
                         "value-independence audit refuses to templatize the value-echo flow",
                         f"cached={res.cached} (today the leaky flow caches silently)",
                         aspirational=True))
    # Capability: an audit surface on the record result (a field reporting slot/leak findings).
    fields = {f.name for f in dataclasses.fields(RecordResult)}
    checks.append(expect(any(any(k in n for k in ("slot", "audit", "leak")) for n in fields),
                         "RecordResult exposes an audit/slot findings surface",
                         f"fields={sorted(fields)}", aspirational=True))
    # NOTE: verify-by-replay DID reproduce this flow (res.reproduced) — shipped machinery H3's
    # two-binding verification extends. Not asserted as its own check because it flips the day the
    # audit ships (the aspirational refusal check going green is the retirement signal).
    return checks
