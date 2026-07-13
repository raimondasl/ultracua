"""Stage-1 stdio MCP server: approved READ flows as typed, zero-argument tools.

Split into a pure, SDK-free CORE (`list_flow_tools` / `call_flow_tool` + the dataclasses) that
imports and unit-tests without the `mcp` package, and a thin SDK WIRING (`build_server` / `serve`)
that lazy-imports `mcp` only when you actually serve. That keeps `import ultracua.mcpserver` working
(and testable) on a machine without the SDK, and confines the optional dependency to one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..cache import FlowCache
from ..obs import get_logger

_log = get_logger("mcpserver")

_NAME_OK = re.compile(r"[^a-zA-Z0-9_-]+")


def _empty_input_schema() -> dict:
    """A zero-argument tool's inputSchema (also the byte-identical stage-1 shape for a no-slot flow)."""
    return {"type": "object", "properties": {}, "additionalProperties": False}


def slots_to_input_schema(slots: Optional[dict]) -> dict:
    """H2 stage 3: build a JSON-Schema `inputSchema` from a flow's `FlowSpec.slots` — one property per
    NON-SECRET slot (a secret resolves from `$env`, never a tool argument). Mirrors `_validate_one`
    field-for-field so the client-advertised schema and the server's `validate_params` agree; the SERVER
    stays authoritative (this schema is advisory — e.g. a client's ECMA `pattern` engine differs from
    Python's `re.fullmatch`). `additionalProperties:false` mirrors the unknown-param refusal. A flow with no
    (non-secret) slots yields exactly the zero-arg shape, so a slot-less flow is unchanged from stage 1."""
    props: dict = {}
    required: list = []
    for name, s in (slots or {}).items():
        if getattr(s, "secret", False):
            continue  # env-resolved — never a caller argument, never advertised
        p: dict = {"type": s.type}
        if s.enum is not None:
            p["enum"] = list(s.enum)
        if s.pattern is not None:
            p["pattern"] = f"^(?:{s.pattern})$"   # anchor to reproduce Python's re.fullmatch (advisory)
        if s.min is not None:
            p["minimum"] = s.min
        if s.max is not None:
            p["maximum"] = s.max
        if s.max_length is not None:
            p["maxLength"] = s.max_length
        props[name] = p
        if s.required:
            required.append(name)
    schema: dict = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = sorted(required)
    return schema


@dataclass
class FlowTool:
    """One MCP tool exposing one approved read flow."""

    name: str                       # sanitized MCP tool name (^[A-Za-z0-9_-]+$)
    spec_name: str                  # the ultracua flow spec it dispatches to
    description: str
    output_schema: Optional[dict] = None  # the flow's extract_schema, if any (advisory; see build_server)
    input_schema: dict = field(default_factory=_empty_input_schema)  # from the flow's slots (H2 stage 3)


@dataclass
class ToolOutcome:
    """The result of dispatching one tool call — SDK-agnostic so the core stays testable."""

    ok: bool
    data: Any = None
    code: str = ""            # machine-readable failure slug (from the FlowReplayError taxonomy)
    retryable: bool = False   # may the outer agent re-run as-is?
    message: str = ""


def _tool_name(spec_name: str) -> str:
    """Sanitize a flow spec name into a valid MCP tool name. Empty/degenerate -> a stable fallback."""
    n = _NAME_OK.sub("_", (spec_name or "").strip()).strip("_")
    return n or "flow"


def _is_write_flow(spec, cache: FlowCache) -> bool:
    """A flow is a WRITE — never exposed over this read surface — if it is DECLARED a write (`spec.mutate`)
    OR its cached steps in fact MUTATE. A flow learned as a "read" whose steps actually POST is an UNDECLARED
    write: replay still fires it (flow._replay_step gates on `step.mutating`), UNCONFIRMED (its confirm
    barrier keys off spec.mutate), so exposing it here would let an untrusted outer agent drive an unverified
    write through a read-only-annotated tool. Key off the ACTUAL mutating signal, exactly as `run_batch` does."""
    if spec.mutate is not None:
        return True
    from ..cache import flow_key
    cached = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
    return cached is not None and any(getattr(s, "mutating", False) for s in cached.steps)


def list_flow_tools(cache: Optional[FlowCache] = None) -> list[FlowTool]:
    """Enumerate the tools to advertise: every saved flow that is a READ (never a write — default-deny),
    is APPROVED, and is actually learned/cached. Reads the cwd-relative spec store + health sidecars
    (stage 1 is launch-cwd-relative; pinnable roots are a later stage). A broken spec file is skipped
    (logged), never allowed to abort the whole inventory. Name collisions after sanitizing are skipped
    loudly so one flow can never silently shadow another."""
    from .. import flows

    cache = cache or FlowCache()
    tools: list[FlowTool] = []
    claimed: dict[str, str] = {}
    for spec_name in flows.list_specs():
        try:
            spec = flows.load_spec(spec_name)
        except Exception as exc:  # noqa: BLE001 — a malformed spec must not kill the tool list
            _log.warning("mcp: skipping unreadable spec %r: %s", spec_name, exc)
            continue
        health = flows.health(spec, cache=cache)
        if not (health.approved and health.cached):  # only human-approved, learned flows
            continue
        if _is_write_flow(spec, cache):  # declared OR undeclared write -> default-deny, never exposed
            continue
        tname = _tool_name(spec_name)
        if tname in claimed:
            _log.warning("mcp: tool name %r from spec %r collides with spec %r — skipping the later one",
                         tname, spec_name, claimed[tname])
            continue
        claimed[tname] = spec_name
        # H2 stage 3: a slotted read flow becomes a PARAMETERIZED tool (inputSchema from its non-secret
        # slots); a no-slot flow stays zero-arg (identical stage-1 shape). Secret slots resolve from $env,
        # so they're omitted from the schema — note them in the description so a caller knows what's needed.
        desc = spec.goal or spec_name
        secret_envs = [s.secret_env for s in (spec.slots or {}).values()
                       if getattr(s, "secret", False) and s.secret_env]
        if secret_envs:
            desc += f" (reads secret env var(s), not passed as arguments: {', '.join('$' + e for e in secret_envs)})"
        tools.append(FlowTool(name=tname, spec_name=spec_name, description=desc,
                              output_schema=spec.extract_schema,
                              input_schema=slots_to_input_schema(spec.slots)))
    return tools


async def call_flow_tool(name: str, cache: Optional[FlowCache] = None, *,
                         arguments: Optional[dict] = None) -> ToolOutcome:
    """Dispatch one tool call to the safety-gated Flow API. Re-resolves the tool against the CURRENT
    approved-read inventory (so a flow unapproved since `tools/list` is refused, and an arg can never select
    a flow), then runs `flows.replay(params=..., require_approved=True, on_drift="raise", check_shape=True)`
    — never the raw engine. `arguments` (H2 stage 3) are the caller's slot values: they are VALIDATED against
    the closed slot domain inside `replay` (a bad arg -> `invalid_params` BEFORE any browser), never trusted.
    A typed FlowReplayError becomes a structured outcome (code + retryable) the caller can act on."""
    from .. import flows

    cache = cache or FlowCache()
    resolved = {t.name: t for t in list_flow_tools(cache)}.get(name)
    if resolved is None:
        return ToolOutcome(False, code="unknown_tool",
                           message=f"no approved read-flow tool named {name!r} (unlisted, unapproved, or a write)")
    spec = flows.load_spec(resolved.spec_name)
    if _is_write_flow(spec, cache):  # belt-and-suspenders: never dispatch a declared OR undeclared write
        return ToolOutcome(False, code="write_denied",
                           message=f"{resolved.spec_name!r} is a write flow — not exposed over MCP (reads only)")
    # One gate: ALL validation (unknown/out-of-domain/missing-required args, incl. a stray arg on a no-slot
    # flow) happens inside replay -> _preflight_row -> validate_params. `{}` on a slotted flow enforces
    # required; `None` on a no-slot flow is the exact stage-1 frozen replay.
    if arguments:
        params = dict(arguments)
    elif spec.slots:
        params = {}
    else:
        params = None
    try:
        data = await flows.replay(spec, params=params, require_approved=True, on_drift="raise",
                                  check_shape=True, cache=cache)
    except flows.FlowReplayError as exc:
        return ToolOutcome(False, code=getattr(exc, "code", "replay_error"),
                           retryable=bool(getattr(exc, "retryable", False)), message=str(exc))
    return ToolOutcome(True, data=data)


# --- SDK wiring (lazy — only `serve`/`build_server` need the `mcp` package) --------------------
def _require_mcp():
    try:
        import mcp  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without the optional dep
        raise RuntimeError(
            "the MCP server needs the `mcp` SDK, which isn't installed — run `uv sync --group mcp` "
            "(or `pip install 'mcp>=1.28.0'`), then retry `ultracua flow serve-mcp`"
        ) from exc


def build_server(cache: Optional[FlowCache] = None, *, name: str = "ultracua"):
    """Build the low-level MCP `Server` wiring the pure core to the SDK handlers. Read tools are
    annotated read-only; results are returned WRAPPED as `{"flow", "data"}` structured content (no
    declared outputSchema — a declared schema would both risk validation mismatch on scalars/lists
    and amplify trust in a silently-truncated extraction; that hardening waits on H9)."""
    _require_mcp()
    import mcp.types as mtypes
    from mcp.server import Server

    server = Server(name)

    @server.list_tools()
    async def _list_tools() -> list:
        out = []
        for t in list_flow_tools(cache):
            out.append(mtypes.Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,   # H2 stage 3: from the flow's non-secret slots (empty if none)
                annotations=mtypes.ToolAnnotations(readOnlyHint=True, openWorldHint=True),
            ))
        return out

    @server.call_tool()
    async def _call_tool(tool_name: str, arguments: dict):
        outcome = await call_flow_tool(tool_name, cache, arguments=arguments)
        if outcome.ok:
            return {"flow": tool_name, "data": outcome.data}
        # Full control over the error result: isError + a machine-readable code/retryable the outer
        # agent can branch on instead of string-parsing the message (never paper a drift over).
        return mtypes.CallToolResult(
            isError=True,
            content=[mtypes.TextContent(type="text", text=outcome.message)],
            structuredContent={"error": {"code": outcome.code, "retryable": outcome.retryable,
                                         "message": outcome.message}},
        )

    return server


async def serve(cache: Optional[FlowCache] = None, *, name: str = "ultracua") -> None:
    """Run the stdio MCP server until the client disconnects. Blocks; wire it to an MCP client's
    stdio transport (e.g. a Claude/Cursor `mcpServers` entry running `ultracua flow serve-mcp`)."""
    _require_mcp()
    from mcp.server.stdio import stdio_server

    server = build_server(cache, name=name)
    _log.info("mcp: serving %d approved read-flow tool(s) over stdio", len(list_flow_tools(cache)))
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
