"""Stage-1 stdio MCP server: approved READ flows as typed, zero-argument tools.

Split into a pure, SDK-free CORE (`list_flow_tools` / `call_flow_tool` + the dataclasses) that
imports and unit-tests without the `mcp` package, and a thin SDK WIRING (`build_server` / `serve`)
that lazy-imports `mcp` only when you actually serve. That keeps `import ultracua.mcpserver` working
(and testable) on a machine without the SDK, and confines the optional dependency to one place.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from ..cache import FlowCache, flow_key
from ..ledger import RunLedger
from ..obs import get_logger
from ..safety import origin_of

_log = get_logger("mcpserver")

_NAME_OK = re.compile(r"[^a-zA-Z0-9_-]+")

# H2 stage 2: a per-flow-key single-flight lock so the SAME write flow can't run concurrently (two racing
# tool calls). Module-level; get-or-create is synchronous (no `await` between lookup and insert), so it's
# atomic under the one event loop the stdio server runs on. Reads take NO lock (read parallelism preserved).
_flow_write_locks: dict = {}


def _lock_for(key: str) -> asyncio.Lock:
    lock = _flow_write_locks.get(key)
    if lock is None:
        lock = _flow_write_locks[key] = asyncio.Lock()
    return lock


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
    is_write: bool = False          # H2 stage 2: a WRITE tool (--expose-writes) — drives readOnlyHint=False,
    #                                 destructiveHint, the [WRITE] description prefix, and the elicit-or-refuse path


@dataclass
class WriteConfirmRequest:
    """H2 stage 2: the SECRET-FREE payload handed to the `confirm` callback before a write fires — what the
    human sees to accept/decline. Carries the caller's slot `arguments` (secret slots resolve from `$env` and
    are refused in params, so never here) and the HASHED Idempotency-Key preview(s) — never the resolved dict
    (which holds env-resolved plaintext secrets)."""

    tool_name: str
    spec_name: str
    origin: str                     # origin of spec.start_url (for the human to recognize the target site)
    arguments: dict                 # the caller's slot args — secret-free
    idempotency_keys: list          # hashed `uca-…` previews (secret-safe)


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


def list_flow_tools(cache: Optional[FlowCache] = None, *, expose_writes: bool = False) -> list[FlowTool]:
    """Enumerate the tools to advertise: every saved flow that is APPROVED and learned/cached. READ flows are
    always exposed. WRITE flows are default-deny UNLESS `expose_writes=True` (H2 stage 2) — and then only a
    DECLARED write (`spec.mutate`) with a confirm check (so replay can verify it landed); an UNDECLARED write
    (mutating steps but `spec.mutate is None`) is NEVER exposed (its writes are unverifiable). A broken spec is
    skipped (logged). Name collisions after sanitizing are skipped loudly (no silent shadowing)."""
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
        is_write = _is_write_flow(spec, cache)
        if is_write:
            # A write is exposed ONLY behind --expose-writes AND only if it's a DECLARED write with a confirm
            # check. An undeclared write (mutating steps, no spec.mutate) has no confirm barrier -> replay
            # can't verify it landed -> never exposed. A declared write missing a confirm would only ever
            # refuse at preflight, so don't advertise it either.
            if not (expose_writes and spec.mutate is not None and spec.mutate.has_confirm()):
                continue
        tname = _tool_name(spec_name)
        if tname in claimed:
            _log.warning("mcp: tool name %r from spec %r collides with spec %r — skipping the later one",
                         tname, spec_name, claimed[tname])
            continue
        claimed[tname] = spec_name
        # H2 stage 3: a slotted flow becomes a PARAMETERIZED tool (inputSchema from its non-secret slots).
        # Secret slots resolve from $env, so they're omitted from the schema — note them in the description.
        desc = spec.goal or spec_name
        secret_envs = [s.secret_env for s in (spec.slots or {}).values()
                       if getattr(s, "secret", False) and s.secret_env]
        if secret_envs:
            desc += f" (reads secret env var(s), not passed as arguments: {', '.join('$' + e for e in secret_envs)})"
        if is_write:  # H2 stage 2: WARN loud — a write is irreversible + rides the operator's identity.
            desc = (f"[WRITE — performs a real, irreversible action on {origin_of(spec.start_url)}; runs under "
                    f"the operator's identity and needs an interactive confirm] " + desc)
        tools.append(FlowTool(name=tname, spec_name=spec_name, description=desc, is_write=is_write,
                              output_schema=spec.extract_schema,
                              input_schema=slots_to_input_schema(spec.slots)))
    return tools


async def call_flow_tool(
    name: str, cache: Optional[FlowCache] = None, *, arguments: Optional[dict] = None,
    expose_writes: bool = False,
    confirm: Optional[Callable[["WriteConfirmRequest"], Awaitable[bool]]] = None,
) -> ToolOutcome:
    """Dispatch one tool call to the safety-gated Flow API. Re-resolves the tool against the CURRENT approved
    inventory (a flow unapproved since `tools/list` is refused; an arg can never select a flow), then runs
    `flows.replay(params=..., require_approved=True, on_drift="raise", check_shape=True)` — never the raw
    engine. `arguments` (H2 stage 3) are validated against the closed slot domain inside `replay` (a bad arg ->
    `invalid_params` BEFORE any browser). A typed FlowReplayError becomes a structured outcome (code + retryable).

    A WRITE flow (H2 stage 2, only when `expose_writes=True`) takes the extra write rail, ALL under a per-flow
    single-flight mutex: pre-flight -> retry-dedupe ledger (a repeat of the same args returns `already_done`,
    never re-fires) -> ELICIT a human confirmation (`confirm`; None or decline -> refuse, never fire) -> fire ->
    record STRICTLY AFTER the write confirms. The Idempotency-Key is the correctness floor; the ledger, the
    mutex, and the human confirm are the rails against a retry-happy or racing outer agent."""
    from .. import flows

    cache = cache or FlowCache()
    resolved = {t.name: t for t in list_flow_tools(cache, expose_writes=expose_writes)}.get(name)
    if resolved is None:
        return ToolOutcome(False, code="unknown_tool",
                           message=f"no tool named {name!r} (unlisted, unapproved, or a write not exposed)")
    spec = flows.load_spec(resolved.spec_name)
    # Shared params rule: a real dict -> use it; a slotted flow with no args -> {} (enforce required); a
    # no-slot flow -> None (frozen replay). ALL arg validation happens inside replay/preflight_keys.
    params = dict(arguments) if arguments else ({} if spec.slots else None)

    if not resolved.is_write:
        # READ path — unchanged from stages 1/3: no lock, no ledger, no elicit.
        try:
            data = await flows.replay(spec, params=params, require_approved=True, on_drift="raise",
                                      check_shape=True, cache=cache)
        except flows.FlowReplayError as exc:
            return ToolOutcome(False, code=getattr(exc, "code", "replay_error"),
                               retryable=bool(getattr(exc, "retryable", False)), message=str(exc))
        return ToolOutcome(True, data=data)

    # WRITE path (H2 stage 2). An undeclared write must never reach here (list_flow_tools excludes it); re-check
    # on the CURRENT cache — belt-and-suspenders against a race / a direct call.
    if spec.mutate is None:
        return ToolOutcome(False, code="write_denied",
                           message=f"{resolved.spec_name!r}: an undeclared write (mutating steps, no confirm "
                                   f"barrier) — replay can't verify it landed, so it's never exposed")

    key = flow_key(spec.goal, spec.start_url, spec.scope)
    async with _lock_for(key):   # SINGLE-FLIGHT: two concurrent calls to this flow can't both fire
        # PRE-FLIGHT (0-LLM, no browser): validate the args + compute the write's Idempotency-Key(s). Any
        # violation (invalid_params / not-approved / stale slots_hash / unbound slot / precheck) fails here,
        # BEFORE any elicit or fire.
        try:
            _resolved, keys = flows.preflight_keys(spec, params, cache=cache, require_approved=True)
        except flows.FlowReplayError as exc:
            return ToolOutcome(False, code=getattr(exc, "code", "replay_error"),
                               retryable=bool(getattr(exc, "retryable", False)), message=str(exc))
        ledger = RunLedger.open(cache, key, "mcp", spec.scope)
        try:
            # RETRY-DEDUPE: this exact write (same args -> same key) already committed on a prior call? Return
            # already_done, never re-elicit / re-fire (a client timeout retry must not double-write).
            if keys and ledger.is_committed(keys):
                return ToolOutcome(True, data={"status": "already-done", "data": None}, code="already_done",
                                   message="this exact write already committed on a prior call (not re-fired)")
            # ELICIT-OR-REFUSE: a human confirms before the write fires. No capability -> refuse; decline or a
            # confirm transport error -> refuse. NEVER fire without an explicit accept.
            if confirm is None:
                return ToolOutcome(False, code="elicitation_unsupported",
                                   message="this write needs an interactive confirm the client can't provide "
                                           "(no elicitation capability) — refused, not fired")
            req = WriteConfirmRequest(tool_name=name, spec_name=resolved.spec_name,
                                      origin=origin_of(spec.start_url), arguments=dict(arguments or {}),
                                      idempotency_keys=list(keys))
            try:
                confirmed = await confirm(req)
            except Exception:  # noqa: BLE001 — a confirm/elicit error is a REFUSAL, never a fire
                return ToolOutcome(False, code="declined", message="the write confirmation could not be completed")
            if not confirmed:
                return ToolOutcome(False, code="declined", message="the write was declined at confirmation")
            # FIRE — the safety-gated replay re-runs the guards, actuates, and verifies the write LANDED via the
            # declared mutate confirm barrier. A write is NEVER verify-by-replayed (that would double-submit).
            try:
                data = await flows.replay(spec, params=params, require_approved=True, on_drift="raise",
                                          check_shape=True, cache=cache)
            except flows.FlowReplayError as exc:
                return ToolOutcome(False, code=getattr(exc, "code", "replay_error"),
                                   retryable=bool(getattr(exc, "retryable", False)), message=str(exc))
            # RECORD strictly AFTER the write confirmed. A crash before this leaves the row unrecorded -> a
            # re-run re-fires with the SAME key -> the backend dedupes (the key is the floor, ledger the optimization).
            if keys and isinstance(data, dict) and data.get("status") in ("confirmed", "already-done"):
                ledger.record(0, keys, data["status"])
            code = "already_done" if isinstance(data, dict) and data.get("status") == "already-done" else ""
            return ToolOutcome(True, data=data, code=code)
        finally:
            ledger.close()


# --- SDK wiring (lazy — only `serve`/`build_server` need the `mcp` package) --------------------
def _require_mcp():
    try:
        import mcp  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without the optional dep
        raise RuntimeError(
            "the MCP server needs the `mcp` SDK, which isn't installed — run `uv sync --group mcp` "
            "(or `pip install 'mcp>=1.28.0'`), then retry `ultracua flow serve-mcp`"
        ) from exc


def _make_confirm(session):
    """Wrap an MCP `ServerSession` into the pure core's `confirm` callback: elicit a human accept/decline for
    a write. ANY elicitation error is swallowed to a refusal (False) — a write NEVER fires on a failed confirm."""
    async def _confirm(req: "WriteConfirmRequest") -> bool:
        import mcp.types as mtypes

        msg = (f"CONFIRM WRITE — tool {req.tool_name!r} will perform a real, IRREVERSIBLE action on "
               f"{req.origin}, under YOUR (the operator's) identity.\n"
               f"arguments: {req.arguments}\nidempotency key(s): {req.idempotency_keys}\n"
               f"Accept to run it now; decline to refuse.")
        try:
            res = await session.elicit_form(
                message=msg,
                requestedSchema={"type": "object", "properties": {}, "additionalProperties": False})
            return getattr(res, "action", None) == "accept"
        except Exception:  # noqa: BLE001 — an elicit transport error is a refusal, never a fire
            return False
    return _confirm


def build_server(cache: Optional[FlowCache] = None, *, name: str = "ultracua", expose_writes: bool = False):
    """Build the low-level MCP `Server` wiring the pure core to the SDK handlers. Read tools are annotated
    read-only; WRITE tools (only when `expose_writes=True`) are annotated destructive and each call elicits a
    human confirmation (a client without elicitation capability is refused). Results are WRAPPED as
    `{"flow", "data"}` structured content (no declared outputSchema — that hardening waits on H9)."""
    _require_mcp()
    import mcp.types as mtypes
    from mcp.server import Server

    server = Server(name)

    @server.list_tools()
    async def _list_tools() -> list:
        out = []
        for t in list_flow_tools(cache, expose_writes=expose_writes):
            out.append(mtypes.Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,   # H2 stage 3: from the flow's non-secret slots (empty if none)
                annotations=mtypes.ToolAnnotations(
                    readOnlyHint=not t.is_write, openWorldHint=True,
                    destructiveHint=(True if t.is_write else None),
                    idempotentHint=(False if t.is_write else None)),
            ))
        return out

    @server.call_tool()
    async def _call_tool(tool_name: str, arguments: dict):
        # For a write, wire the human-confirm elicitation IF the client supports it; else the core refuses
        # (elicitation_unsupported). A read never elicits, so `confirm` is inert for it.
        confirm = None
        try:
            session = server.request_context.session
            if session.check_client_capability(
                    mtypes.ClientCapabilities(elicitation=mtypes.ElicitationCapability())):
                confirm = _make_confirm(session)
        except Exception:  # noqa: BLE001 — no request context / capability probe failure -> no confirm (refuse)
            confirm = None
        outcome = await call_flow_tool(tool_name, cache, arguments=arguments,
                                       expose_writes=expose_writes, confirm=confirm)
        if outcome.ok:
            return {"flow": tool_name, "data": outcome.data}
        # Full control over the error result: isError + a machine-readable code/retryable the outer agent can
        # branch on instead of string-parsing the message (never paper a drift / a decline over).
        return mtypes.CallToolResult(
            isError=True,
            content=[mtypes.TextContent(type="text", text=outcome.message)],
            structuredContent={"error": {"code": outcome.code, "retryable": outcome.retryable,
                                         "message": outcome.message}},
        )

    return server


async def serve(cache: Optional[FlowCache] = None, *, name: str = "ultracua",
                expose_writes: bool = False) -> None:
    """Run the stdio MCP server until the client disconnects. Blocks; wire it to an MCP client's stdio
    transport (e.g. a Claude/Cursor `mcpServers` entry running `ultracua flow serve-mcp`). With
    `expose_writes=True`, approved DECLARED write flows are also exposed — each call requires an interactive
    confirm and runs under the OPERATOR's identity (no per-caller auth until the Phase-I daemon)."""
    _require_mcp()
    from mcp.server.stdio import stdio_server

    server = build_server(cache, name=name, expose_writes=expose_writes)
    tools = list_flow_tools(cache, expose_writes=expose_writes)
    n_write = sum(1 for t in tools if t.is_write)
    _log.info("mcp: serving %d read-flow tool(s)%s over stdio", len(tools) - n_write,
              f" + {n_write} WRITE tool(s) (--expose-writes; each needs an interactive confirm, runs under the "
              f"operator's identity)" if expose_writes else "")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
