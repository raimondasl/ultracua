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

from ..cache import CacheUnreadableError, FlowCache, flow_key
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


DIAGNOSTICS_TOOL = "ultracua_diagnostics"


@dataclass(frozen=True)
class SkippedFlow:
    """One saved flow that is NOT advertised, and why (MCP-1)."""

    spec_name: str
    code: str                       # one of the codes below; QUIET_SKIPS says which are ordinary
    detail: str                     # operator-facing, already carries the remedy where one exists


# THE QUIET SET, ENUMERATED — and this direction is the whole point. `flow run-all`'s exit code and its
# webhook each tested `status == "failed"`, so a third bucket satisfying neither went invisible in both
# and a flow could leave the fleet with cron reporting green (R3.9/CLI-1). The rule that came out of it:
# enumerate the outcomes that are ALLOWED to be quiet, never the ones that must be loud, so a skip class
# added tomorrow is loud by default because nobody put it here.
#
# Quiet means "ordinary, needs nobody": a flow that was never learned or approved is a normal
# intermediate state, and a write withheld without `--expose-writes` is the default-deny doing its job.
# Everything else means a human has something to fix.
# NOT IN HERE, DELIBERATELY: `recipe_unreadable` and `learn_refused`. Both arrive with `cached=False`
# and the first draft mapped them onto `not_learned`, which put a write-safety refusal in the quiet
# bucket. An allowlist protects against a new CODE defaulting to quiet; it cannot protect against an
# existing STATUS being mapped onto an existing quiet code, which is why `_tool_for` now branches on
# `health.status` and this comment exists.
QUIET_SKIPS = frozenset({"not_learned", "not_approved", "write_not_exposed"})


@dataclass(frozen=True)
class ToolListing:
    """What `tools/list` advertised AND what it dropped — the pair, so the second cannot be forgotten."""

    tools: "list[FlowTool]"
    skipped: "list[SkippedFlow]"

    @property
    def needs_attention(self) -> "list[SkippedFlow]":
        return [s for s in self.skipped if s.code not in QUIET_SKIPS]


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
    write through a read-only-annotated tool.

    Thin cache lookup over `flows.is_write_flow`, which is now THE definition — this used to be one of three
    independent transcriptions of it, and it is the one whose answer decides `readOnlyHint`."""
    from ..cache import flow_key
    from ..flows import is_write_flow
    if spec.write.declares_write:
        return True
    return is_write_flow(spec, cache.get(spec.key))


def list_flow_tools(cache: Optional[FlowCache] = None, *, expose_writes: bool = False) -> list[FlowTool]:
    """Enumerate the tools to advertise: every saved flow that is APPROVED and learned/cached. READ flows are
    always exposed. WRITE flows are default-deny UNLESS `expose_writes=True` (H2 stage 2) — and then only a
    DECLARED write (`spec.mutate`) with a confirm check (so replay can verify it landed); an UNDECLARED write
    (mutating steps but `spec.mutate is None`) is NEVER exposed (its writes are unverifiable). A broken spec is
    skipped (logged). Name collisions after sanitizing are skipped loudly (no silent shadowing)."""
    from .. import flows

    return build_listing(cache, expose_writes=expose_writes).tools


def build_listing(cache: Optional[FlowCache] = None, *, expose_writes: bool = False) -> ToolListing:
    """`list_flow_tools`, plus WHAT IT DROPPED AND WHY — the pair that closes MCP-1.

    Over stdio, stderr is not the protocol. Four paths dropped a flow with a `_log.warning`/`_log.error`
    and the client just saw fewer tools, so an agent could not tell "that flow was retired" from "that
    flow is broken and someone should look". Each individual skip is right — an unlisted tool beats a
    listed tool that always fails — and the invisible AGGREGATE was the defect.

    Returning the two together is what makes the second impossible to forget: `_tool_for` no longer has
    a `None` to return, so a future drop path cannot be added without naming itself.
    """
    from .. import flows

    cache = cache or FlowCache()
    tools: list[FlowTool] = []
    skipped: list[SkippedFlow] = []
    # RESERVED so a flow cannot shadow the diagnostics tool. Seeding `claimed` means a spec whose
    # sanitized name collides takes the ordinary collision path and reports itself, rather than either
    # silently winning (an agent calling "diagnostics" and replaying someone's flow) or being special-
    # cased here with a second, parallel rule.
    claimed: dict[str, str] = {DIAGNOSTICS_TOOL: "(reserved: the diagnostics tool)"}
    for spec_name in flows.list_specs():
        try:
            spec = flows.load_spec(spec_name)
        except Exception as exc:  # noqa: BLE001 — a malformed spec must not kill the tool list
            _log.warning("mcp: skipping unreadable spec %r: %s", spec_name, exc)
            skipped.append(SkippedFlow(spec_name, "spec_unreadable",
                                       f"the saved spec could not be read ({exc}); re-save or remove it"))
            continue
        try:
            verdict = _tool_for(spec, spec_name, cache, expose_writes=expose_writes, claimed=claimed)
        except CacheUnreadableError as exc:
            # This loop reads the cache TWICE — `health()` (which catches internally) and then
            # `_is_write_flow`, a second read at a second moment that a transient sharing violation can
            # fail on its own. Guard the whole body once rather than each read: an unreadable recipe must
            # DROP OUT of the advertised tools, never fall through to `_is_write_flow` returning False and
            # advertising an undeclared write as a read-only tool.
            _log.error("mcp: skipping %r — cached recipe unreadable: %s", spec_name, exc)
            skipped.append(SkippedFlow(spec_name, "recipe_unreadable",
                                       f"the cached recipe could not be read ({exc}); if this persists, "
                                       f"re-learn the flow"))
            continue
        if isinstance(verdict, FlowTool):
            tools.append(verdict)
        else:
            skipped.append(verdict)
    return ToolListing(tools=tools, skipped=skipped)


def _tool_for(spec, spec_name: str, cache: FlowCache, *, expose_writes: bool,
              claimed: "dict[str, str]") -> "FlowTool | SkippedFlow":
    """Decide whether ONE spec is advertised, and as what — returning EITHER outcome, never `None`.

    IT USED TO RETURN `None` FOR "not advertised", and that is what let MCP-1 exist: the reason lived
    only in a log line, so four different drops were indistinguishable to any consumer and a fifth could
    be added without anyone noticing. A `SkippedFlow` carries the reason out with the decision, so
    dropping a flow WITHOUT saying why is no longer expressible here.

    Split out of `list_flow_tools` so every cache read on this path sits inside a single guarded call.
    The loop reads the cache TWICE — `health()` and then `_is_write_flow` — at two different moments, so
    guarding them one at a time leaves the second exposed to a transient sharing violation of its own.
    """
    from .. import flows

    health = flows.health(spec, cache=cache)
    # BRANCH ON THE STATUS, NOT ON `cached` — the first draft of this slice keyed on the boolean and threw
    # the status away, which collapsed THREE different states into the quiet `not_learned`. Found by the
    # pre-merge audit, and it is this register's most-repeated shape landing inside the fix for it:
    # `flows.health` goes to deliberate effort to keep these apart, in words, at its own call site —
    #
    #     "it must not read as 'not learned' either, which is what silently flattening it to None
    #      would do. Surface it as its own status."          (the unreadable branch)
    #     "refused for firing a write nothing could account for, and it needs a human"   (R3.13)
    #
    # — and the consumer flattened them one layer up, reporting both as ordinary with the remedy "run
    # `flow learn`". `QUIET_SKIPS` could not catch it: these are not new CODES defaulting to quiet, they
    # are existing STATUSES mapped onto an existing quiet code, which an allowlist cannot see.
    if health.status == "unreadable":
        return SkippedFlow(spec_name, "recipe_unreadable",
                           f"the cached recipe could not be read ({health.last_error}); if this persists, "
                           f"re-learn the flow")
    if health.status == "refused":
        return SkippedFlow(spec_name, "learn_refused",
                           f"REFUSED at learn time and never cached ({health.last_error}) — this needs a "
                           f"human, not a re-run; see `flow status` and the refusal's own remedy")
    if not health.cached:
        return SkippedFlow(spec_name, "not_learned",
                           "not learned yet — run `flow learn` or `flow record`")
    if not health.approved:
        return SkippedFlow(spec_name, "not_approved",
                           "learned but not approved — review it and run `flow approve`")
    if health.approval_stale:
        # The approval bit is set but no longer binds the steps on disk (they were re-authored, or the
        # flow predates the binding). Pre-flight would refuse every call with `stale_approval`, so don't
        # advertise it at all — an unlisted tool an agent never calls beats a listed tool that always
        # fails. Logged, not silent: an operator needs to know why their tool vanished.
        _log.warning("mcp: skipping %r — approval no longer matches the flow's steps; review with "
                     "`flow inspect --name %s` and re-approve", spec_name, spec_name)
        return SkippedFlow(spec_name, "approval_stale",
                           f"the approval no longer matches the steps on disk; review with "
                           f"`flow inspect --name {spec_name}` and re-approve")
    is_write = _is_write_flow(spec, cache)
    if is_write:
        # A write is exposed ONLY behind --expose-writes AND only if it's a DECLARED write with a confirm
        # check. An undeclared write (mutating steps, no spec.mutate) has no confirm barrier -> replay
        # can't verify it landed -> never exposed. A declared write missing a confirm would only ever
        # refuse at preflight, so don't advertise it either.
        if not (expose_writes and spec.write.declares_confirm):
            return SkippedFlow(
                spec_name, "write_not_exposed",
                "a WRITE flow: exposed only with --expose-writes, and only when declared with a confirm "
                "check" if spec.write.declares_write else
                "an UNDECLARED write (mutating steps, no `mutate` spec) is never exposed — its writes "
                "cannot be verified; declare it with `flow set-mutate` or re-record it")
    tname = _tool_name(spec_name)
    if tname in claimed:
        _log.warning("mcp: tool name %r from spec %r collides with spec %r — skipping the later one",
                     tname, spec_name, claimed[tname])
        return SkippedFlow(spec_name, "name_collision",
                           f"its tool name {tname!r} is already taken by {claimed[tname]!r}; rename one "
                           f"of the flows")
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
    return FlowTool(name=tname, spec_name=spec_name, description=desc, is_write=is_write,
                    output_schema=spec.extract_schema,
                    input_schema=slots_to_input_schema(spec.slots))


def _diagnostics(cache: FlowCache, *, expose_writes: bool) -> "ToolOutcome":
    """MCP-1's answer: report what `tools/list` did NOT advertise, and why.

    Reports OBSERVATIONS, never a verdict about whether the fleet is healthy — the same shape as the
    audit judge. `needs_attention` is derived from `QUIET_SKIPS`, so the classification lives in one
    place and a skip code added later is "needs attention" until someone deliberately says otherwise.
    """
    listing = build_listing(cache, expose_writes=expose_writes)
    attention = listing.needs_attention
    return ToolOutcome(True, data={
        "advertised": [t.name for t in listing.tools],
        "not_advertised": [{"flow": s.spec_name, "code": s.code, "detail": s.detail,
                            "needs_attention": s.code not in QUIET_SKIPS}
                           for s in listing.skipped],
        "counts": {"advertised": len(listing.tools), "not_advertised": len(listing.skipped),
                   "needs_attention": len(attention)},
        "expose_writes": expose_writes,
    })


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
    if name == DIAGNOSTICS_TOOL:
        # Answered from a FRESH listing on every call, which is the reason this is a tool rather than a
        # line in the server's `instructions`: instructions are computed once at connect, and a flow whose
        # approval goes stale mid-session would never appear in them.
        return _diagnostics(cache, expose_writes=expose_writes)
    resolved = {t.name: t for t in list_flow_tools(cache, expose_writes=expose_writes)}.get(name)
    if resolved is None:
        return ToolOutcome(
            False, code="unknown_tool",
            message=f"no tool named {name!r} (unlisted, unapproved, its approval no longer matches the "
                    f"flow's reviewed steps, or a write not exposed)")
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
            return ToolOutcome(False, code=exc.code, retryable=exc.retryable, message=str(exc))
        return ToolOutcome(True, data=data)

    # WRITE path (H2 stage 2). An undeclared write must never reach here (list_flow_tools excludes it); re-check
    # on the CURRENT cache — belt-and-suspenders against a race / a direct call.
    if not spec.write.declares_write:
        return ToolOutcome(False, code="write_denied",
                           message=f"{resolved.spec_name!r}: an undeclared write (mutating steps, no confirm "
                                   f"barrier) — replay can't verify it landed, so it's never exposed")

    key = spec.key
    async with _lock_for(key):   # SINGLE-FLIGHT: two concurrent calls to this flow can't both fire
        # PRE-FLIGHT (0-LLM, no browser): validate the args + compute the write's Idempotency-Key(s). Any
        # violation (invalid_params / not-approved / stale slots_hash / unbound slot / precheck) fails here,
        # BEFORE any elicit or fire.
        try:
            _resolved, keys = flows.preflight_keys(spec, params, cache=cache, require_approved=True)
        except flows.FlowReplayError as exc:
            return ToolOutcome(False, code=exc.code, retryable=exc.retryable, message=str(exc))
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
                # A raise is not automatically "nothing happened". `exc.landed` means the write's confirm
                # TRANSITION was observed before the failure, so the side effect is certain — and leaving
                # it unrecorded defeats this rail's stated job verbatim ("a client timeout retry must not
                # double-write"), in the one case the process is alive and positively knows it committed.
                # Only for `landed` errors: a maybe stays unrecorded (ledger.py: "never a false skip").
                #
                # Do NOT re-derive this from the exception CLASS. `landed` is positional, not
                # typological: `flows.replay` stamps it on every error raised after the evidence point,
                # whatever the class. Keying off `WriteReadbackError` alone is R3.3, which left a
                # committed row unarmed because its failure happened to surface as `ShapeDriftError`.
                if keys and getattr(exc, "landed", False):
                    ledger.record(0, keys, getattr(exc, "code", "landed"))
                return ToolOutcome(False, code=exc.code, retryable=exc.retryable, message=str(exc))
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
        # MCP-1. ALWAYS listed, including when nothing was dropped — a tool that appears only when
        # something is wrong is one an agent has never seen and will not think to call. Its description
        # says when to reach for it, because that is the only discovery mechanism a tool has.
        out.append(mtypes.Tool(
            name=DIAGNOSTICS_TOOL,
            description=("Why is a flow missing from this tool list? Reports every saved ultracua flow "
                         "that was NOT advertised and the reason (not learned, not approved, approval "
                         "stale, unreadable, name collision, or a write withheld), so a shrunken list "
                         "can be told apart from a healthy one. Read-only; touches no browser."),
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
            annotations=mtypes.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
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
