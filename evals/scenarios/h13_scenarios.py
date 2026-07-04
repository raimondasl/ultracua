"""H13 evals: contract-lane replay compilation — WebMCP pinning, cooperative lanes, wire reads.

ROADMAP H13: a learn-time lane compiler that probes an origin's machine contracts and compiles
verified flows onto the cheapest lane verify-by-replay confirms, in three strictly-additive
sub-tiers: (1) cooperative read lanes (`Accept: text/markdown`, RFC 9727 /.well-known/api-catalog)
via a `lanes.py` origin contract probe; (2) WebMCP tool pinning (name + JSON-Schema hash +
availability precondition as additive CachedStep fields) — gated on rewriting `webmcp.py` around
the REAL origin-trial API (`navigator.modelContext`, registration-side, no page-visible
enumeration); (3) Integuru-style wire-level HTTP replay (`wire.py`, READS ONLY in v1 — wire
writes are a double-write surface no barrier can close). A cross-lane canary (cheap lane vs DOM
lane value comparison) is load-bearing; downgrades are recorded in flow health, never silent.

Partial credit measured today (the substrates the lane compiler is specified to ride on):
- `webmcp.detect`/`webmcp.call` work end to end against the SPECULATIVE window.webmcp interface
- CachedStep already carries the webmcp_call step shape (tool + args) and the additive-Optional
  field pattern (StepConfirm precedent) that schema pinning will reuse without a schema bump
- `safety.is_write_request` (the v1 write-refusal classifier) + `PacingGovernor.gate`
- `BrowserSession(record_har_path=..., storage_state=...)` — capture + cookie-carrying substrates
- `flows.canary`/`canary_all` + FlowMeta failure-health recording (the downgrade recorder)

Everything here is key-less: local Fixture pages, real headless Chromium, $0.
"""

from __future__ import annotations

import inspect

from evals.core import Ctx, expect, import_probe, missing, probe, scenario
from evals.fixtures import Fixture, page

# The REAL WebMCP origin-trial surface (Chrome 149-156): a page REGISTERS tools on
# navigator.modelContext — registration-side only, with NO page-visible enumeration API.
# This stub reproduces exactly that shape so the detection gap is measurable key-lessly.
_REAL_MODELCONTEXT_PAGE = page("""
<h1>order</h1><p id="total">total: 42</p>
<script>
  (() => {
    const mc = { _tools: [], registerTool(t) { this._tools.push(t); } };
    try { Object.defineProperty(navigator, 'modelContext', { value: mc, configurable: true }); }
    catch (e) { navigator.modelContext = mc; }
    navigator.modelContext.registerTool({
      name: 'get_order_total',
      description: 'read the current order total',
      inputSchema: { type: 'object', properties: {} },
      async execute() { return { content: [{ type: 'text', text: '42' }] }; },
    });
  })();
</script>
""", title="real-modelcontext")

# The SPECULATIVE interface the shipped webmcp.py speaks (window.webmcp with listTools/callTool)
# — no real site exposes it, but ultracua's detect/call are wired against it end to end.
_SPECULATIVE_WEBMCP_PAGE = page("""
<h1>order</h1>
<script>
  window.webmcp = {
    listTools() { return [{ name: 'get_total', description: 'read the order total' }]; },
    async callTool(name, args) {
      if (name === 'get_total') return { total: 42 };
      throw new Error('no such tool');
    },
  };
</script>
""", title="speculative-webmcp")


@scenario(
    id="h13.webmcp.api_reality_gap",
    title="webmcp.detect misses the REAL navigator.modelContext surface; the speculative one ships e2e",
    group="h13", aspirational=True, tags=("webmcp", "lanes", "detect"),
    notes="H13 prereq #4: rewrite webmcp.py around navigator.modelContext registerTool interception",
)
async def webmcp_api_reality_gap(ctx: Ctx):
    """The WebMCP pinning tier is gated on speaking the API that actually exists. This scenario
    measures BOTH sides of that gap on real Chromium: a page registering a tool via the real
    origin-trial surface goes undetected today (`missing`), while the speculative window.webmcp
    interface — which no real site exposes — is detected and callable (shipped, partial credit)."""
    from ultracua import webmcp
    from ultracua.browser import BrowserSession

    checks = []
    fx = Fixture({"/real": _REAL_MODELCONTEXT_PAGE, "/speculative": _SPECULATIVE_WEBMCP_PAGE})
    with fx.serve() as base:
        session = await BrowserSession(headless=True).start()
        try:
            # -- the REAL registration-side API ------------------------------------------------
            await session.goto(base + "/real")
            # Fixture oracle: the page really did register a tool on navigator.modelContext —
            # anchors the missing-check below (a broken stub must not masquerade as the gap).
            n_tools = await session.page.evaluate(
                "() => (navigator.modelContext && navigator.modelContext._tools || []).length")
            checks.append(expect(n_tools == 1,
                                 "fixture registers a tool via the real navigator.modelContext surface",
                                 f"tools_registered={n_tools}"))
            # THE GAP (plan step 1a): detect() must learn to see registration-side tools (via
            # add_init_script registerTool interception — there is no enumeration API to poll).
            # Today it looks for window.webmcp/listTools and returns None here -> missing.
            st, detected_real = await probe(webmcp.detect, session.page)
            checks.append(expect(st == "ok" and bool(detected_real),
                                 "detect sees a tool registered on the REAL modelContext API",
                                 f"probe={st} detected={detected_real!r}", aspirational=True))
            # The interception installer itself (an init-script hook a session opts into at
            # learn/record time, replacing polling-based detection) has no surface yet.
            has_intercept = any(hasattr(webmcp, n) for n in
                                ("install_intercept", "intercept_registrations", "init_script",
                                 "INTERCEPT_JS", "capture_registrations"))
            checks.append(expect(has_intercept,
                                 "webmcp exposes a registerTool interception installer (init-script hook)",
                                 "no interception surface in ultracua.webmcp", aspirational=True))

            # -- the SPECULATIVE interface (shipped behavior, documented as partial credit) ----
            await session.goto(base + "/speculative")
            detected_spec = await webmcp.detect(session.page)
            checks.append(expect(bool(detected_spec) and detected_spec[0].get("name") == "get_total",
                                 "detect finds the speculative window.webmcp interface (shipped tier)",
                                 f"detected={detected_spec!r}"))
            # Invocation is wired end to end too — the call plumbing the pinned tier will reuse.
            st_call, res = await probe(webmcp.call, session.page, "get_total", {})
            checks.append(expect(st_call == "ok" and isinstance(res, dict) and res.get("total") == 42,
                                 "webmcp.call invokes a speculative tool and returns its value",
                                 f"probe={st_call} result={res!r}"))
        finally:
            await session.close()
    return checks


@scenario(
    id="h13.pinning.tool_schema_fields",
    title="WebMCP schema pinning: tool_schema_hash + availability precond on CachedStep; mutation gate",
    group="h13", aspirational=True, tags=("webmcp", "pinning", "write-safety"),
    notes="H13 plan step 2: additive CachedStep pin fields; refuse to cache non-read-only tool calls",
)
async def pinning_tool_schema_fields(ctx: Ctx):
    """Pinning = name + JSON-Schema hash + availability precondition stored per step, compared at
    replay (absent or mismatched -> FlowReplayError, never heal). Probe the field surface, and the
    documented mutation-gate hole the pinning work must close: a mutating tool call currently has
    no classification signal, so it would slip past the never-blind-replay-a-write gate."""
    from ultracua.cache import CachedStep
    from ultracua.safety import classify_mutation

    sf = set(CachedStep.model_fields)
    checks = [
        # Partial credit: the webmcp_call step SHAPE already persists — tool name + args are
        # CachedStep fields today (the carrier the pin fields will sit beside).
        expect({"tool", "args"} <= sf,
               "CachedStep carries the webmcp_call step shape (tool + args)",
               f"fields={sorted(sf)}"),
        # The pin itself: a JSON-Schema hash of the tool's declared schema, re-checked at replay
        # so a silently-changed tool contract fails loud instead of returning schema-drifted data.
        expect(bool(sf & {"tool_schema_hash", "schema_hash", "tool_pin"}),
               "CachedStep stores a tool JSON-Schema hash (the replay pin)", aspirational=True),
        # The availability precondition: "this tool is still registered here" checked BEFORE
        # dispatch — the webmcp_call analogue of precond_fingerprint on DOM steps.
        expect(bool(sf & {"tool_precond", "tool_precondition", "tool_available"}),
               "CachedStep stores a tool availability precondition", aspirational=True),
        # THE SAFETY HOLE (spec'd to close with pinning): classify_mutation has no signal for
        # tool calls — a bluntly mutating tool call classifies as non-mutating today, so it
        # would bypass the mutation gate. The pinning slice must classify (or refuse to cache)
        # any webmcp_call not provably read-only.
        expect(classify_mutation("webmcp_call", intent="submit the payment", name="submit_payment"),
               "mutation gate classifies WebMCP tool calls (mutating tool call is flagged)",
               "classify_mutation returns False for action='webmcp_call'", aspirational=True),
    ]
    # Partial credit: the ADDITIVE-Optional field pattern the pins must follow is proven — an
    # old-shape step (no confirm/pin fields at all) still deserializes, so adding pin fields
    # needs NO schema bump and cannot invalidate the existing fleet (StepConfirm precedent).
    st, old_step = await probe(CachedStep.model_validate, {"intent": "x", "action": "click"})
    checks.append(expect(st == "ok" and getattr(old_step, "confirm", "sentinel") is None,
                         "additive-Optional field pattern proven (old-shape step deserializes)",
                         f"probe={st}"))
    return checks


@scenario(
    id="h13.lanes.origin_contract_probe",
    title="lanes.py origin contract probe (markdown negotiation + RFC 9727 api-catalog + ContractReport)",
    group="h13", aspirational=True, tags=("lanes", "markdown", "api-catalog"),
    notes="H13 plan steps 1 + 4: learn-time contract probe; markdown lane artifact after DOM verify",
)
async def lanes_origin_contract_probe(ctx: Ctx):
    """Tier 1 (cooperative read lanes): at learn/record time, probe the origin for machine
    contracts — Accept: text/markdown negotiation (pinning content-type + x-markdown-tokens) and
    the RFC 9727 /.well-known/api-catalog — and emit a per-origin ContractReport the authoring
    cascade consumes. Nothing exists yet; the cookie-carrying + artifact-storage substrates do."""
    from ultracua.browser import BrowserSession

    checks = []
    ok_lanes, lanes = import_probe("ultracua.lanes")
    checks.append(expect(ok_lanes, "ultracua.lanes imports (origin contract probe module)",
                         f"{type(lanes).__name__}", aspirational=True))
    if ok_lanes:
        # If the module lands it must expose the probe verb + the report type the cascade reads.
        has_probe = any(callable(getattr(lanes, n, None))
                        for n in ("probe_origin", "probe", "contract_probe"))
        checks.append(expect(has_probe and hasattr(lanes, "ContractReport"),
                             "lanes exposes probe_origin -> ContractReport",
                             f"probe={has_probe}", aspirational=True))
        # Real key-less exercise: a fixture origin that publishes an RFC 9727 catalog must show
        # up in the report (probe over localhost — no external network).
        fx = Fixture({
            "/": page("<h1>portal</h1>"),
            "/.well-known/api-catalog": '{"linkset": [{"anchor": "/", "service-desc": []}]}',
        })
        with fx.serve() as base:
            st, report = await probe(getattr(lanes, "probe_origin", getattr(lanes, "probe", None)), base)
            checks.append(expect(st == "ok" and report is not None,
                                 "contract probe detects the fixture's /.well-known/api-catalog",
                                 f"probe={st}", aspirational=True))
    else:
        checks.append(missing("lanes exposes probe_origin -> ContractReport", "module absent"))
        checks.append(missing("contract probe detects the fixture's /.well-known/api-catalog",
                              "module absent"))
    # Partial credit: the markdown probe is specified to carry the flow's auth cookies
    # (context.request over storage_state) — that cookie-seeding substrate ships today.
    sig = set(inspect.signature(BrowserSession.__init__).parameters)
    checks.append(expect("storage_state" in sig,
                         "cookie-carrying substrate ships (BrowserSession storage_state seeding)",
                         f"params={sorted(sig)}"))
    # Partial credit: the plan names FlowMeta.read_pin as a storage slot for the compiled
    # markdown-lane artifact (lane tag + pinned URL/content-type/anchor) — the slot exists.
    import dataclasses

    import ultracua.flows as flows

    meta_fields = {f.name for f in dataclasses.fields(flows.FlowMeta)}
    checks.append(expect("read_pin" in meta_fields,
                         "FlowMeta.read_pin slot ships (named home for the markdown lane artifact)",
                         f"fields={sorted(meta_fields)}"))
    return checks


@scenario(
    id="h13.wire.read_compiler_reads_only",
    title="wire.py 0-LLM HTTP read executor + compilability analyzer; write-refusal + pacing substrates",
    group="h13", aspirational=True, tags=("wire", "lanes", "write-safety"),
    notes="H13 plan step 5: HAR-lite capture -> dependency graph -> verified vs DOM; READS ONLY in v1",
)
async def wire_read_compiler_reads_only(ctx: Ctx):
    """Tier 3 (the research bet): compile a captured request/response graph into a browser-free
    0-LLM HTTP read program, verified against the DOM-lane result BEFORE caching. Hard rule: v1
    refuses any flow containing a write (an HTTP timeout is ambiguous about commit — a double-write
    surface no barrier can close). The executor is missing; its classifier + governor + capture
    substrates all ship and are exercised here."""
    from ultracua.browser import BrowserSession
    from ultracua.safety import PacingGovernor, is_write_request

    checks = []
    ok_wire, wire = import_probe("ultracua.wire")
    checks.append(expect(ok_wire, "ultracua.wire imports (browser-free HTTP read executor)",
                         f"{type(wire).__name__}", aspirational=True))
    if ok_wire:
        # The compiler half: dependency-graph builder + compilability analyzer, and an explicit
        # reads-only guard (the analyzer must refuse writes, not just fail to compile them).
        has_compile = any(callable(getattr(wire, n, None))
                          for n in ("compile", "compile_reads", "analyze", "compile_flow"))
        checks.append(expect(has_compile, "wire exposes a compile/analyze surface",
                             aspirational=True))
    else:
        checks.append(missing("wire exposes a compile/analyze surface", "module absent"))
    # Partial credit: the v1 write-refusal's classifier ships and is CORRECT on the shapes that
    # matter — origin-independent writes (cross-origin POST is the double-submit gap a
    # same-origin-only check misses) and telemetry-aware breadth (no false-firing on beacons).
    table_ok = (is_write_request("POST", "http://127.0.0.1:9/api/order")
                and not is_write_request("GET", "http://127.0.0.1:9/api/order")
                and is_write_request("DELETE", "https://api.example.com/v1/item/3")
                and not is_write_request("POST", "https://www.google-analytics.com/g/collect"))
    checks.append(expect(table_ok,
                         "is_write_request classifies reads vs writes (the v1 refusal's classifier)",
                         "truth table over POST/GET/DELETE/telemetry-POST did not hold"))
    # Partial credit: PacingGovernor.gate ships and admits a caller — the per-origin wrapper
    # every wire call is specified to pass through (politeness survives leaving the browser).
    gov = PacingGovernor()
    entered = False
    async with gov.gate("http://127.0.0.1:1"):
        entered = True
    checks.append(expect(entered, "PacingGovernor.gate admits a wire-shaped caller (per-origin gate)"))
    # Partial credit: full request/response capture already exists as a context option —
    # record_har_path with embedded bodies (browser.py sets record_har_content='embed') is the
    # HAR-lite capture the learn-time graph builder consumes.
    sig = set(inspect.signature(BrowserSession.__init__).parameters)
    checks.append(expect("record_har_path" in sig,
                         "HAR capture substrate ships (record_har_path with embedded bodies)",
                         f"params={sorted(sig)}"))
    return checks


@scenario(
    id="h13.canary.cross_lane_comparison",
    title="cross-lane canary: cheap-lane vs DOM-lane value comparison + recorded (never silent) downgrade",
    group="h13", aspirational=True, tags=("canary", "lanes", "fail-loud"),
    notes="H13 plan step 6: the only defense against schema-stable-but-semantically-wrong responses",
)
async def canary_cross_lane_comparison(ctx: Ctx):
    """Wire/markdown lanes bypass resolve(unique=True) discipline — a schema-stable WRONG response
    passes every pin. The cross-lane canary (periodically run BOTH lanes, compare extracted values,
    divergence = fail loud + demote to DOM lane in FlowMeta) is load-bearing, not optional. Probe
    the comparison surface; measure the shipped trigger + health-recording substrates it extends."""
    import dataclasses

    import ultracua.flows as flows

    checks = []
    # Partial credit: the trigger machinery the cross-lane canary extends ships — canary +
    # canary_all are the periodic freshness probes the lane comparison will piggyback on.
    checks.append(expect(callable(getattr(flows, "canary", None))
                         and callable(getattr(flows, "canary_all", None)),
                         "canary trigger machinery ships (canary + canary_all)"))
    # Partial credit, exercised: a canary over nothing-learned reports 'not-learned' — it never
    # fabricates freshness (the same honesty the lane verdicts must inherit). Returns before any
    # browser/network I/O, so this is a pure key-less behavior check.
    spec = flows.FlowSpec(name="h13-canary", start_url="http://127.0.0.1:9/", goal="read the total")
    st, res = await probe(flows.canary, spec, cache=ctx.cache())
    checks.append(expect(st == "ok" and getattr(res, "status", "") == "not-learned",
                         "canary fails loud on nothing-learned (never fabricates freshness)",
                         f"probe={st} status={getattr(res, 'status', None)!r}"))
    # The comparison mode itself: canary accepting a lane-comparison request has no surface yet.
    params = set(inspect.signature(flows.canary).parameters)
    checks.append(expect(bool(params & {"lanes", "lane", "compare", "compare_lanes"}),
                         "canary accepts a cross-lane comparison mode",
                         f"params={sorted(params)}", aspirational=True))
    # The verdict shape: CanaryResult carrying per-lane values / a divergence verdict so a
    # semantically-wrong cheap lane is DETECTED, not just schema-checked.
    cr_fields = {f.name for f in dataclasses.fields(flows.CanaryResult)}
    checks.append(expect(bool(cr_fields & {"lane", "lanes", "divergence", "values", "lane_values"}),
                         "CanaryResult carries per-lane values / a divergence verdict",
                         f"fields={sorted(cr_fields)}", aspirational=True))
    # The demotion record: divergence must demote the flow to the DOM lane IN FlowMeta — a
    # surfaced health event, not a silent fallback (silent downgrade would mask contract drift).
    meta_fields = {f.name for f in dataclasses.fields(flows.FlowMeta)}
    checks.append(expect(bool(meta_fields & {"lane", "lanes", "lane_health", "downgrades", "demoted"}),
                         "FlowMeta records lane demotion (recorded downgrade, never silent)",
                         f"fields={sorted(meta_fields)}", aspirational=True))
    # Partial credit: the downgrade RECORDER it needs ships — FlowMeta failure health
    # (last_error + consecutive_failures) written via the same _update_meta path.
    checks.append(expect({"last_error", "consecutive_failures", "last_error_ts"} <= meta_fields
                         and callable(getattr(flows, "_update_meta", None)),
                         "failure-health recording substrate ships (FlowMeta via _update_meta)",
                         f"fields={sorted(meta_fields)}"))
    return checks
