"""A tool that vanishes from `tools/list` must be able to say WHY (MCP-1, plan slice S10b).

THE DEFECT. `list_flow_tools` dropped a saved flow at four points — unreadable spec, unreadable cached
recipe, stale approval, sanitized-name collision — each with a `_log.warning`/`_log.error` and a bare
`continue`/`return None`. Over stdio **stderr is not the protocol**: the client simply sees fewer tools,
so an agent cannot tell "that flow was retired" from "that flow is broken and someone should look".

Every individual skip is RIGHT and none of them changes here — an unlisted tool an agent never calls
beats a listed tool that always fails, which is the in-code argument and it stands. The defect was that
the AGGREGATE was invisible.

WHY THE FIX IS SHAPED THIS WAY, and it is the register's rule rather than taste:

  * ENUMERATE THE QUIET OUTCOMES, NEVER THE LOUD ONES. `flow run-all` had two cron channels that each
    tested `status == "failed"`, so a third bucket satisfying neither went invisible in both and a flow
    could leave the fleet with cron reporting green (R3.9/CLI-1). So `QUIET_SKIPS` lists the outcomes
    ALLOWED to be silent — not learned, not approved, a write withheld by default-deny — and everything
    else is `needs_attention` by construction, including a code somebody adds next year.

  * A DROP WITHOUT A REASON IS NOW INEXPRESSIBLE. `_tool_for` returned `Optional[FlowTool]`, so "not
    advertised" and "here is why" were separable and only the first was structural. It now returns
    `FlowTool | SkippedFlow`; there is no `None` to return.

  * A DIAGNOSTIC TOOL, NOT AN `instructions` LINE. `InitializationOptions.instructions` exists and would
    have been cheaper, but it is computed ONCE at connect: a flow whose approval goes stale mid-session
    would never appear in it. The tool answers from a fresh listing per call.

  * ALWAYS LISTED, even when nothing was dropped. A tool that appears only when something is wrong is
    one the agent has never seen and will not think to call.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key
from ultracua.locators import LocatorSpec
from ultracua.mcpserver import server as srv


def _spec(tmp_path: Path, name: str, **kw):
    from ultracua.flows import FlowSpec, save_spec
    spec = FlowSpec(name=name, start_url="http://127.0.0.1:9/", goal=f"goal {name}", **kw)
    save_spec(spec)
    return spec


def _learn_and_approve(spec, cache: FlowCache, *, approve: bool = True) -> None:
    from ultracua.flows import approve as _approve
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, created_ts=1.0,
                         steps=[CachedStep(intent="go", action="click",
                                           locator=LocatorSpec(role="link", name="x", tag="a"))]))
    if approve:
        _approve(spec, cache=cache)


# ==================== the quiet set is an allowlist ====================


def test_a_flow_that_is_merely_unapproved_is_QUIET_but_still_reported(monkeypatch, tmp_path) -> None:
    """The two halves that make this useful rather than noisy: an ordinary intermediate state must not
    read as a problem, and must still be VISIBLE — an operator asking "where is my tool" gets an answer
    either way."""
    monkeypatch.chdir(tmp_path)
    cache = FlowCache(root=tmp_path / "c")
    spec = _spec(tmp_path, "pending")
    _learn_and_approve(spec, cache, approve=False)

    listing = srv.build_listing(cache)
    assert [s.spec_name for s in listing.skipped] == ["pending"]
    assert listing.skipped[0].code == "not_approved"
    assert listing.needs_attention == [], "an unapproved flow is an ordinary state, not a fault"
    assert "flow approve" in listing.skipped[0].detail, "a report must name the remedy"


def test_a_stale_approval_NEEDS_ATTENTION(monkeypatch, tmp_path) -> None:
    """The case an agent most needs distinguished from "retired": the tool worked yesterday, the recipe
    was re-authored, and pre-flight would now refuse every call."""
    monkeypatch.chdir(tmp_path)
    cache = FlowCache(root=tmp_path / "c")
    spec = _spec(tmp_path, "stale")
    _learn_and_approve(spec, cache)
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    flow = cache.get(key)
    flow.steps.append(CachedStep(intent="extra", action="click",
                                 locator=LocatorSpec(role="link", name="y", tag="a")))
    cache.put(flow)                                   # re-authored -> the approval no longer binds

    listing = srv.build_listing(cache)
    codes = {s.spec_name: s.code for s in listing.skipped}
    assert codes.get("stale") == "approval_stale", codes
    assert [s.spec_name for s in listing.needs_attention] == ["stale"]
    assert "re-approve" in dict((s.spec_name, s.detail) for s in listing.skipped)["stale"]


def test_the_quiet_set_is_an_ALLOWLIST_so_a_new_code_is_loud_by_default() -> None:
    """The direction that matters, asserted as a property of the classification rather than of today's
    codes. If someone adds a skip reason and forgets to classify it, it must land in `needs_attention` —
    the opposite default is how a third status went invisible in two channels at once."""
    made_up = srv.SkippedFlow("whatever", "a_code_nobody_classified", "…")
    listing = srv.ToolListing(tools=[], skipped=[made_up])
    assert listing.needs_attention == [made_up], (
        "an unclassified skip code was treated as quiet; QUIET_SKIPS must be an allowlist")
    assert srv.QUIET_SKIPS == {"not_learned", "not_approved", "write_not_exposed"}, (
        "the quiet set changed. That is allowed, but it is a DECISION: each member is an outcome that "
        "may pass unremarked forever, so add one only with the argument written down.")


# ==================== the drop-without-a-reason path is gone ====================


def test_tool_for_cannot_drop_a_flow_without_saying_why() -> None:
    """STRUCTURAL, because the behavioural cells above only cover the reasons that exist today.

    `_tool_for` used to return `Optional[FlowTool]`, which is what let four different drops be
    indistinguishable. Every non-tool exit must now construct a `SkippedFlow`; a bare `return None`
    would restore the defect silently.
    """
    src = inspect.getsource(srv._tool_for)
    fn = ast.parse(src.lstrip()).body[0]
    offenders = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Return):
            continue
        v = node.value
        if v is None or (isinstance(v, ast.Constant) and v.value is None):
            offenders.append(node.lineno)
        elif not (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                  and v.func.id in ("SkippedFlow", "FlowTool")):
            offenders.append(node.lineno)
    assert not offenders, (
        f"`_tool_for` returns something other than a FlowTool or a SkippedFlow at line(s) {offenders} "
        f"(relative to the function). A drop that does not name itself is MCP-1.")

    # POSITIVE CONTROL — the same walk over a function that DOES return None, so a scan that silently
    # matches nothing fails here instead of passing everywhere.
    ctl = ast.parse("def f(x):\n    if x:\n        return None\n    return FlowTool()\n").body[0]
    assert [n.lineno for n in ast.walk(ctl)
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
            and n.value.value is None], "the scan does not detect its own positive control"


# ==================== the surface ====================


@pytest.mark.asyncio
async def test_the_diagnostics_tool_is_listed_even_when_nothing_is_wrong(monkeypatch, tmp_path) -> None:
    """A tool that only appears when something is broken is one the agent has never seen."""
    monkeypatch.chdir(tmp_path)
    cache = FlowCache(root=tmp_path / "c")
    spec = _spec(tmp_path, "healthy")
    _learn_and_approve(spec, cache)

    listing = srv.build_listing(cache)
    assert [t.spec_name for t in listing.tools] == ["healthy"] and listing.skipped == []

    out = await srv.call_flow_tool(srv.DIAGNOSTICS_TOOL, cache, arguments={})
    assert out.ok and out.data["counts"] == {"advertised": 1, "not_advertised": 0, "needs_attention": 0}


@pytest.mark.asyncio
async def test_the_diagnostics_tool_reports_the_reason_a_tool_vanished(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache(root=tmp_path / "c")
    _learn_and_approve(_spec(tmp_path, "good"), cache)
    _learn_and_approve(_spec(tmp_path, "pending"), cache, approve=False)

    out = await srv.call_flow_tool(srv.DIAGNOSTICS_TOOL, cache, arguments={})
    assert out.ok, out.message
    assert out.data["advertised"] == ["good"]
    reported = {r["flow"]: r for r in out.data["not_advertised"]}
    assert reported["pending"]["code"] == "not_approved"
    assert reported["pending"]["needs_attention"] is False


def test_a_flow_cannot_shadow_the_diagnostics_tool(monkeypatch, tmp_path) -> None:
    """The name is RESERVED by seeding `claimed`, so a flow that sanitizes to it takes the ordinary
    collision path and reports itself — rather than winning silently (an agent asking for diagnostics
    and replaying somebody's flow) or being special-cased with a second, parallel rule."""
    monkeypatch.chdir(tmp_path)
    cache = FlowCache(root=tmp_path / "c")
    _learn_and_approve(_spec(tmp_path, srv.DIAGNOSTICS_TOOL), cache)

    listing = srv.build_listing(cache)
    assert [t.name for t in listing.tools] == [], "a flow shadowed the diagnostics tool"
    assert [s.code for s in listing.skipped] == ["name_collision"]
    assert listing.needs_attention, "a shadowed flow is a real problem for its owner, not a quiet one"


def test_list_flow_tools_still_returns_only_flow_tools(monkeypatch, tmp_path) -> None:
    """The compatibility clause. Five test files assert `list_flow_tools(...) == []`; the diagnostics
    tool lives at the PROTOCOL boundary, not in the core's flow list, so none of them become false."""
    monkeypatch.chdir(tmp_path)
    cache = FlowCache(root=tmp_path / "c")
    assert srv.list_flow_tools(cache) == []
    _learn_and_approve(_spec(tmp_path, "one"), cache)
    assert [t.spec_name for t in srv.list_flow_tools(cache)] == ["one"]


# ==================== what the pre-merge audit found IN this slice ====================


def test_a_LEARN_REFUSED_flow_is_never_reported_as_merely_not_learned(monkeypatch, tmp_path) -> None:
    """THE AUDIT'S CATCH, and it is this register's most-repeated shape inside the fix for it.

    The first draft branched on `health.cached` and discarded `health.status`. Three states arrive with
    `cached=False`, and two of them need a human: a recipe that cannot be READ, and a flow REFUSED at
    learn time for firing a write nothing could account for (R3.13). Both were reported as the quiet
    `not_learned` with the remedy "run `flow learn`" — a write-safety refusal filed as ordinary, which is
    the "cron reporting green" shape this whole slice exists to remove.

    `QUIET_SKIPS` could not catch it: these are not new CODES defaulting to quiet, they are existing
    STATUSES mapped onto an existing quiet code, and an allowlist cannot see that. `flows.health` keeps
    them apart deliberately — its own comments say so — and the consumer flattened them one layer up.
    """
    monkeypatch.chdir(tmp_path)
    cache = FlowCache(root=tmp_path / "c")
    spec = _spec(tmp_path, "refused-write")
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.remember_refusal(key, code="write_unattributed",
                           reason="fired a write nothing could account for")

    listing = srv.build_listing(cache)
    skip = {s.spec_name: s for s in listing.skipped}["refused-write"]
    assert skip.code == "learn_refused", (
        f"a learn-time write refusal reported as {skip.code!r}; `flows.health` distinguishes it as "
        f"`refused` precisely so a consumer cannot call it 'not learned'")
    assert skip.code not in srv.QUIET_SKIPS
    assert [s.spec_name for s in listing.needs_attention] == ["refused-write"], (
        "a flow refused for a write nobody could account for was reported as needing nobody")
    assert "needs a human" in skip.detail


def test_an_UNREADABLE_recipe_is_loud_even_when_it_fails_every_read(monkeypatch, tmp_path) -> None:
    """The second half of the same finding. `flows.health` catches `CacheUnreadableError` internally and
    returns `cached=False`, so a PERSISTENTLY unreadable recipe never reached `build_listing`'s
    `except CacheUnreadableError` — the loud handler written for it could only fire in the narrow window
    where the first cache read succeeds and the second fails. Keyed on the status, both now work."""
    monkeypatch.chdir(tmp_path)
    cache = FlowCache(root=tmp_path / "c")
    spec = _spec(tmp_path, "locked")
    _learn_and_approve(spec, cache)

    real_get = FlowCache.get

    def _boom(self, k):                      # every read fails, the AV/indexer sharing-violation shape
        from ultracua.cache import CacheUnreadableError
        raise CacheUnreadableError("locked by another process")

    monkeypatch.setattr(FlowCache, "get", _boom)
    listing = srv.build_listing(cache)
    monkeypatch.setattr(FlowCache, "get", real_get)

    skip = {s.spec_name: s for s in listing.skipped}["locked"]
    assert skip.code == "recipe_unreadable", f"got {skip.code!r} — an unreadable recipe read as ordinary"
    assert listing.needs_attention, "an unreadable recipe needs a human"
