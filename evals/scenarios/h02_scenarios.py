"""H2 evals: flows-as-tools everywhere — the MCP server horizon (ROADMAP H2).

The horizon: a stdio MCP server that registers every APPROVED READ flow as one typed tool
(output schema from `FlowSpec.extract_schema` / `FlowMeta.shape`) dispatching to
`flows.replay(require_approved=True, on_drift="raise", check_shape=True)` — never the raw
daemon `run`, which bypasses the safety gates by documented contract. Stage 2 adds streamable
HTTP + default-deny write exposure (elicitation, per-flow single-flight, completed-run ledger).

Most of the server surface is NOT built yet — those probes report `missing` (the gap). But the
dispatch target and its safety rails ARE shipped, so this module also proves the partial credit:
the approval gate refuses unapproved flows before any browser work, writes are default-deny,
and the spec store + output-schema fields the tool inventory needs already round-trip.

All scenarios are key-less: local Fixture pages + a scripted provider + real headless Chromium.
"""

from __future__ import annotations

import dataclasses
import inspect

from evals.core import Ctx, expect, missing, ok, fail, import_probe, probe, scenario
from evals.fixtures import Fixture, page


class _ClickTheLink:
    """Scripted key-less 'agent': click the first link, then declare done (the tests/ convention)."""

    def __init__(self) -> None:
        self._clicked = False

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        if not self._clicked:
            for el in obs.elements:
                if el.role == "link":
                    self._clicked = True
                    return Action(action="click", intent="open the report page", ref=el.ref), None
        return Action(action="done", intent="done"), None


# `learn(provider=..., router=...)` needs BOTH set to force one scripted attempt (else it builds a
# real provider -> needs an API key). Navigate-only flows (extract=None) never touch the router,
# so an inert placeholder keeps the whole learn path key-less.
_ROUTER_UNUSED = object()

# A port-9 (discard) URL that is NEVER dialed: the gates under test must refuse BEFORE any browser
# or network work — if a regression ever launched the browser first, the connection error (not a
# FlowReplayError) would flip the check to fail, which is exactly the loud signal we want.
_NEVER_DIALED = "http://127.0.0.1:9/"


@scenario(
    id="h02.mcpserver.module_surface",
    title="stage-1 MCP server module + serve-mcp entrypoints exist",
    group="h02", aspirational=True, tags=("mcp", "server", "cli"),
)
async def mcpserver_module_surface(ctx: Ctx):
    """Probes the H2 plan's named surfaces: `ultracua.mcpserver` (stage-1 stdio), its stage-2 HTTP
    sibling, and the `flow serve-mcp` CLI verb. Partial credit: the daemon's documented
    bypass-contract — the reason the MCP server must be a SEPARATE module — is already shipped."""
    from ultracua import flows

    checks = []
    # Capability: the stage-1 server package (plan item 1: mcpserver/server.py, official mcp SDK).
    pkg_ok, _ = import_probe("ultracua.mcpserver")
    checks.append(expect(pkg_ok, "ultracua.mcpserver package imports (stage-1 stdio server)",
                         "no MCP server module yet", aspirational=True))
    srv_ok, _ = import_probe("ultracua.mcpserver.server")
    checks.append(expect(srv_ok, "mcpserver.server registers approved READ flows as typed tools",
                         "stage-1 server module not built", aspirational=True))
    # Capability: stage-2 transport (streamable HTTP + server card) — a distinct later stage.
    http_ok, _ = import_probe("ultracua.mcpserver.http")
    checks.append(expect(http_ok, "mcpserver.http streamable-HTTP transport (stage 2)",
                         "stage-2 transport not built", aspirational=True))
    # Capability: a library-level serve entrypoint the CLI verb would wrap.
    st, _ = await probe(getattr, flows, "serve_mcp")
    checks.append(expect(st == "ok", "flows.serve_mcp entrypoint exists",
                         "no serve entrypoint on the flows API yet", aspirational=True))
    # Capability: the `flow serve-mcp` CLI verb (plan item 4). Source sniff on cli.py: the verb
    # string appearing in `_flow_main`'s subparsers is the landing signal, whatever its handler name.
    import ultracua.cli as cli
    st_src, src = await probe(inspect.getsource, cli)
    checks.append(expect(st_src == "ok" and "serve-mcp" in src, "`flow serve-mcp` CLI verb exists",
                         "verb not in cli.py", aspirational=True))
    # Partial credit (shipped): daemon/server.py's contract comment says its `run` BYPASSES the
    # flows.replay safety gates — the documented fence that forces the MCP server to dispatch to
    # flows.replay() and never the raw engine (hard constraint in the H2 plan).
    d_ok, dmod = import_probe("ultracua.daemon.server")
    doc = (getattr(dmod, "__doc__", "") or "") if d_ok else ""
    checks.append(expect(d_ok and "bypass" in doc.lower() and "flows.replay" in doc,
                         "daemon `run` documents that it bypasses the flows safety gates "
                         "(why the MCP server must dispatch to flows.replay, never the raw engine)",
                         "bypass contract comment not found in ultracua.daemon.server"))
    return checks


@scenario(
    id="h02.errors.typed_taxonomy",
    title="typed replay-error taxonomy with machine-readable retry semantics",
    group="h02", aspirational=True, tags=("mcp", "errors", "fail-loud"),
)
async def typed_error_taxonomy(ctx: Ctx):
    """The H2 plan subclasses FlowReplayError into DriftError / ShapeDriftError / AuthExpiredError /
    EscalateError so the server maps failures to structured MCP tool errors (do-not-retry vs
    retry-after-refresh vs escalate) instead of exception strings an outer LLM might paper over."""
    from ultracua import flows

    checks = []
    # Partial credit (shipped): the taxonomy BASE exists — one loud error type for every untrusted
    # replay, the class the future subclasses hang off.
    checks.append(expect(isinstance(flows.FlowReplayError, type)
                         and issubclass(flows.FlowReplayError, RuntimeError),
                         "FlowReplayError base exists (fails loud, never returns wrong data)"))
    # Capability: each planned subclass, with its retry semantics for the outer agent.
    for name, meaning in (
        ("DriftError", "page/locator drift -> do-not-retry-without-relearn"),
        ("ShapeDriftError", "data structure changed -> do-not-retry"),
        ("AuthExpiredError", "session expired -> retry-after-refresh"),
        ("EscalateError", "interstitial/captcha -> escalate to a human"),
    ):
        st, val = await probe(getattr, flows, name)
        if st == "ok":
            # If it shipped, it must slot into the taxonomy (the plan's whole point: one base to catch).
            checks.append(expect(isinstance(val, type) and issubclass(val, flows.FlowReplayError),
                                 f"{name} subclasses FlowReplayError",
                                 f"{name} exists but is not a FlowReplayError subclass"))
        else:
            checks.append(missing(f"typed {name} ({meaning})", "not in ultracua.flows yet"))
    # Capability: a machine-readable retry/code flag on the error itself — the MCP server needs it
    # to set isError + retryable without string-parsing the message.
    err = flows.FlowReplayError("probe")
    checks.append(expect(hasattr(err, "retryable") or hasattr(err, "code"),
                         "replay errors carry a machine-readable retry/code flag",
                         "no .retryable / .code attribute yet", aspirational=True))
    return checks


@scenario(
    id="h02.gates.approval_dispatch",
    title="the MCP dispatch target is shipped: replay(require_approved=True) refuses, then runs 0-LLM",
    group="h02", tags=("mcp", "replay", "trust"),
)
async def approval_gated_dispatch(ctx: Ctx):
    """Partial credit, end to end: the exact call the future MCP tool handler makes —
    flows.replay(require_approved=True) — already refuses an unapproved flow loudly and BEFORE any
    network work, and runs an approved one with no provider, router, or API key (the 0-LLM path)."""
    from ultracua import flows
    from ultracua.flows import FlowSpec

    checks = []
    fx = Fixture({
        "/": page('<a href="/report">open the daily report</a>'),
        "/report": page('<h1>Report</h1><p>total: 42</p>'),
    })
    with fx.serve() as base:
        cache = ctx.cache()
        spec = FlowSpec(name="h02-read", start_url=base + "/", goal="open the daily report page",
                        headless=True)  # navigate-only read: extract=None -> replay needs no LLM at all
        learned = await flows.learn(spec, provider=_ClickTheLink(), router=_ROUTER_UNUSED, cache=cache)
        checks.append(expect(learned.cached and learned.found, "scripted learn caches a replayable flow",
                             f"cached={learned.cached} found={learned.found} note={learned.note!r}"))

        # The tool-inventory gate: an UNAPPROVED flow must be refused (this is what keeps a drifted
        # or unreviewed flow out of an MCP client's hands).
        fx.gets.clear()  # learn's own traffic is done; anything from here on is the refusal's fault
        st, val = await probe(flows.replay, spec, require_approved=True, cache=cache)
        checks.append(expect(st == "error" and isinstance(val, flows.FlowReplayError)
                             and "not approved" in str(val),
                             "replay(require_approved=True) refuses an unapproved flow loudly",
                             f"status={st} exc={type(val).__name__}: {val}"))
        # The refusal must fire BEFORE any browser/page work — a tool call on an untrusted flow
        # should cost nothing and touch nothing.
        checks.append(expect(not fx.gets, "the refusal made zero page requests (gate precedes the browser)",
                             f"gets={fx.gets}"))

        # After operator approval, the same call runs — with NO provider/router/key in sight, which
        # is the structural 0-LLM proof the MCP server's 'deterministic tool' pitch rests on.
        flows.approve(spec, cache=cache)
        st2, _ = await probe(flows.replay, spec, require_approved=True, cache=cache)
        checks.append(expect(st2 == "ok", "approved flow replays with no provider/router/API key (0-LLM)",
                             f"status={st2}"))
        # The health sidecar the server's tool filter reads (approved + healthy) recorded the run.
        h = flows.health(spec, cache=cache)
        checks.append(expect(h.approved and h.runs >= 1 and h.status == "healthy",
                             "health records the run (the approved/healthy tool-inventory filter basis)",
                             f"approved={h.approved} runs={h.runs} status={h.status}"))
    return checks


@scenario(
    id="h02.contract.tool_schemas",
    title="tool contract plumbing: output schema + spec store exist; typed inputs / pinned roots do not",
    group="h02", tags=("mcp", "schema", "specs"),
)
async def tool_contract_schemas(ctx: Ctx):
    """The MCP tool contract = enumerate saved specs, type the OUTPUT from FlowSpec.extract_schema /
    FlowMeta.shape. That plumbing is shipped (partial credit). The INPUT side (typed slots) and
    launch-cwd-independent spec roots (plan item 4) are not — probed aspirationally."""
    import os

    from ultracua import flows
    from ultracua.flows import FlowMeta, FlowSpec

    checks = []
    spec_fields = {f.name for f in dataclasses.fields(FlowSpec)}
    # Partial credit: the tool OUTPUT schema basis — a JSON schema the server can advertise verbatim.
    checks.append(expect("extract_schema" in spec_fields,
                         "FlowSpec.extract_schema exists (the tool output-schema basis)"))
    # Partial credit: the learned-run shape sidecar — the output contract fallback when no schema is set.
    checks.append(expect("shape" in {f.name for f in dataclasses.fields(FlowMeta)},
                         "FlowMeta.shape records the learned output shape (schema fallback)"))

    # Partial credit: the tool-inventory substrate — save/load/list round-trips the schema. The spec
    # store is cwd-relative today, so chdir into ctx.tmp (never the repo's .ultracua).
    schema = {"type": "object", "properties": {"total": {"type": "number"}}, "required": ["total"]}
    spec = FlowSpec(name="h02-contract", start_url=_NEVER_DIALED, goal="read the total",
                    extract="the total", extract_schema=schema)
    old_cwd = os.getcwd()
    os.chdir(ctx.tmp)
    try:
        st, _ = await probe(flows.save_spec, spec)
        st2, loaded = await probe(flows.load_spec, "h02-contract")
        checks.append(expect(st == "ok" and st2 == "ok"
                             and getattr(loaded, "extract_schema", None) == schema,
                             "save_spec/load_spec round-trips extract_schema intact",
                             f"save={st} load={st2}"))
        checks.append(expect("h02-contract" in flows.list_specs(),
                             "list_specs enumerates the saved flow (the tools/list substrate)"))
    finally:
        os.chdir(old_cwd)

    # Capability (plan item 4): pin cache/spec roots absolute so a server launched from the wrong
    # cwd can't silently serve an empty tool list — save/load take no root parameter today.
    params = set(inspect.signature(flows.save_spec).parameters) | \
        set(inspect.signature(flows.load_spec).parameters)
    checks.append(expect(bool(params & {"root", "specs_dir", "dir", "path"}),
                         "spec store root is pinnable (not launch-cwd-relative)",
                         "save_spec/load_spec have no root/dir parameter", aspirational=True))
    # Capability (stage 3, blocked on H3 slots): a typed INPUT contract. Until then every flow is a
    # zero-argument tool — one tool per learned literal flow.
    checks.append(expect("slots" in spec_fields,
                         "FlowSpec carries a typed input contract (slots) — zero-arg tools until then",
                         "no slots field yet (flows are input-frozen)", aspirational=True))
    return checks


@scenario(
    id="h02.writes.default_deny",
    title="write flows are default-deny at the dispatch layer; the retry-safe ledger is not built",
    group="h02", tags=("mcp", "writes", "write-safety"),
)
async def write_default_deny(ctx: Ctx):
    """Stage 2 exposes writes only behind elicitation + a completed-run ledger. The shipped rails the
    server will stand on: writes are approval-gated even without require_approved, an unconfirmable
    write is refused outright, run_all defaults exclude writes, and idempotency keys are stable.
    All refusals fire before any browser/network work (start_url is never dialed)."""
    from ultracua import flows
    from ultracua.flows import FlowSpec, MutateSpec
    from ultracua.safety import idempotency_key

    checks = []
    cache = ctx.cache()
    # Partial credit: a write flow with NO confirm check is fire-and-hope, so replay refuses it
    # before anything else — a tool must never report a write as done because a click didn't throw.
    spec_nc = FlowSpec(name="h02-w-noconfirm", start_url=_NEVER_DIALED, goal="submit the form",
                       mutate=MutateSpec())
    st, val = await probe(flows.replay, spec_nc, cache=cache)
    checks.append(expect(st == "error" and isinstance(val, flows.FlowReplayError)
                         and "confirm" in str(val),
                         "a write flow without a confirm check is refused outright",
                         f"status={st} exc={type(val).__name__}: {val}"))
    # Partial credit: writes are approval-gated EVEN WITHOUT require_approved — the stronger default
    # the MCP write tools inherit for free.
    spec_w = FlowSpec(name="h02-w-unapproved", start_url=_NEVER_DIALED, goal="submit the form",
                      mutate=MutateSpec(confirm_text_contains="Thanks"))
    st2, val2 = await probe(flows.replay, spec_w, cache=cache)
    checks.append(expect(st2 == "error" and isinstance(val2, flows.FlowReplayError)
                         and "not approved" in str(val2),
                         "an unapproved WRITE is refused even without require_approved",
                         f"status={st2} exc={type(val2).__name__}: {val2}"))
    # Partial credit: the fleet supervisor's safe-by-default posture (read-only + approved-only) —
    # the same defaults the server's tool registration filter mirrors.
    p = inspect.signature(flows.run_all).parameters
    ap, iw = p.get("approved_only"), p.get("include_writes")
    checks.append(expect(ap is not None and ap.default is True
                         and iw is not None and iw.default is False,
                         "run_all defaults to approved-only + writes excluded"))
    # Partial credit: idempotency keys are deterministic per (scope, step, intent) — the building
    # block the ledger extends. (Known-insufficient alone: it's a header sites may ignore.)
    k1 = idempotency_key("flow:h02", 3, "submit the form")
    k2 = idempotency_key("flow:h02", 3, "submit the form")
    k3 = idempotency_key("flow:h02", 4, "submit the form")
    checks.append(expect(k1 == k2 and k1 != k3,
                         "idempotency_key is stable per step and distinct across steps",
                         f"k1==k2:{k1 == k2} k1!=k3:{k1 != k3}"))
    # Capability: the completed-run ledger — a caller-supplied request id on replay, so a client
    # timeout retry can't re-fire a write that already committed.
    checks.append(expect("request_id" in inspect.signature(flows.replay).parameters,
                         "replay accepts a caller request id (completed-run ledger vs retry double-fire)",
                         "no request_id parameter yet", aspirational=True))
    return checks
