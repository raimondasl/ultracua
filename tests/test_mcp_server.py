"""H2 stage-1 MCP server — key-less tests (local fixture + scripted provider, real headless Chromium).

Covers the pure SDK-free core (`list_flow_tools` / `call_flow_tool`) + the typed error taxonomy.
The `mcp` SDK is an OPTIONAL dependency (group `mcp`), so the `build_server` smoke test skips when
it's absent; the module itself imports without the SDK by construction (mcp is lazy-imported only
inside build_server/serve). CI installs `--group mcp` so that smoke actually runs there.
"""

from __future__ import annotations

import functools
import http.server
import threading

import pytest

from ultracua import flows
from ultracua.cache import FlowCache, flow_key
from ultracua.flows import (
    DriftError,
    EscalateError,
    FlowReplayError,
    FlowSpec,
    MutateSpec,
    ParamValidationError,
    ShapeDriftError,
    SlotSpec,
)
from ultracua.mcpserver import call_flow_tool, list_flow_tools
from ultracua.mcpserver.server import _empty_input_schema, slots_to_input_schema

# Reuse the read echo fixture from the run_batch suite (tests/ is on sys.path).
from test_run_batch import _EchoSite


class _Site:
    """A tiny mutable page server: edit `.pages` between learn and replay to induce drift."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = dict(pages)

    def serve(self):
        site = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_GET(self) -> None:
                html = site.pages.get(self.path.split("?")[0])
                if html is None:
                    self.send_error(404)
                    return
                b = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


class _ClickLink:
    """Scripted key-less agent: click the first link, then declare done."""

    def __init__(self) -> None:
        self._clicked = False

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        if not self._clicked:
            for el in obs.elements:
                if el.role == "link":
                    self._clicked = True
                    return Action(action="click", intent="open the report", ref=el.ref), None
        return Action(action="done", intent="done"), None


def _pages(link: bool = True) -> dict[str, str]:
    home = "<a href='/report'>open the report</a>" if link else "<p>the link is gone</p>"
    return {"/": f"<!doctype html><html><body>{home}</body></html>",
            "/report": "<!doctype html><html><body><h1>Report</h1><p>total: 42</p></body></html>"}


async def _learn_read(base: str, name: str, cache: FlowCache, *, approve: bool = True) -> FlowSpec:
    spec = FlowSpec(name=name, start_url=base + "/", goal=f"open the report ({name})", headless=True)
    flows.save_spec(spec)  # so list_specs() (the tools/list substrate) can find it
    res = await flows.learn(spec, provider=_ClickLink(), router=object(), cache=cache)  # router unused (nav-only)
    assert res.cached, f"scripted learn failed: {res.note!r}"
    if approve:
        flows.approve(spec, cache=cache)
    return spec


# --- typed error taxonomy ---------------------------------------------------------------------
def test_taxonomy_subclasses_and_flags() -> None:
    for cls, code, retry in (
        (DriftError, "drift", False),
        (ShapeDriftError, "shape_drift", False),
        (EscalateError, "escalate", False),
        (flows.AuthExpiredError, "auth_expired", True),
    ):
        assert issubclass(cls, FlowReplayError)   # existing `except FlowReplayError` still catches them
        assert cls.code == code and cls.retryable is retry
    base = FlowReplayError("x")
    assert base.code == "replay_error" and base.retryable is False
    # H2 stage 3: a bad ARGUMENT is a caller-fixable ParamValidationError (distinct from a config gap).
    assert issubclass(ParamValidationError, FlowReplayError)
    assert ParamValidationError.code == "invalid_params" and ParamValidationError.retryable is False


# --- H2 stage 3: SlotSpec -> inputSchema (pure) ------------------------------------------------
def test_slots_to_input_schema_maps_and_excludes_secrets() -> None:
    slots = {"q": SlotSpec(type="string", pattern="[a-z]+", max_length=5),
             "n": SlotSpec(type="integer", min=1, max=9),
             "region": SlotSpec(type="string", enum=["us", "eu"], required=False),
             "token": SlotSpec(secret=True, secret_env="TOK")}
    sch = slots_to_input_schema(slots)
    assert sch["additionalProperties"] is False
    assert sch["properties"]["q"] == {"type": "string", "pattern": "^(?:[a-z]+)$", "maxLength": 5}
    assert sch["properties"]["n"] == {"type": "integer", "minimum": 1, "maximum": 9}
    assert sch["properties"]["region"]["enum"] == ["us", "eu"]
    assert "token" not in sch["properties"] and "token" not in sch.get("required", [])   # secret excluded
    assert sch["required"] == ["n", "q"]                                                  # sorted, non-secret


def test_no_slots_and_all_secret_are_zero_arg() -> None:
    assert slots_to_input_schema(None) == _empty_input_schema()
    assert slots_to_input_schema({"t": SlotSpec(secret=True, secret_env="T")}) == _empty_input_schema()


async def _record_slotted_read(site, base, cache, *, name="lookup", slots=None, secret_env=None):
    """Record an echo READ flow (types a value into #q, which echoes to /typed-<value>). If `slots` is None,
    auto-mine a `code` string slot; else declare `slots` and bind the type step to its single slot."""
    spec = FlowSpec(name=name, start_url=base + "/", goal="enter the code", headless=True, slots=slots)

    async def _demo(pg) -> None:
        await pg.fill("#q", "alpha-7")
        await pg.locator("#q").blur()

    res = await flows.record(spec, demo=_demo, headless=True, cache=cache, mine_slots=(slots is None))
    assert res.cached, res.note
    if slots is not None:   # declared slots -> bind the type step to the (single) declared slot name
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        only = next(iter(slots))
        for s in flow.steps:
            if s.action == "type":
                s.slot = only
        cache.put(flow)
    flows.save_spec(spec)   # so list_specs()/tools/list find it
    flows.approve(spec, cache=cache)
    return spec


# --- H2 stage 3: a slotted read is a parameterized tool + end-to-end dispatch -------------------
async def test_slotted_read_is_parameterized_and_dispatches(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()
    httpd, base = site.serve()
    try:
        await _record_slotted_read(site, base, cache)           # auto-mines a `code` slot
        tool = {t.name: t for t in list_flow_tools(cache)}["lookup"]
        assert tool.input_schema["properties"].get("code", {}).get("type") == "string"
        assert tool.input_schema["additionalProperties"] is False
        # END-TO-END: a valid arg reaches the page (the echo GET is the substitution oracle, not a frozen literal).
        site.gets.clear()
        out = await call_flow_tool("lookup", cache, arguments={"code": "beta-9"})
        assert out.ok, f"{out.code}: {out.message}"
        assert "/typed-beta-9" in site.gets and "/typed-alpha-7" not in site.gets
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- H2 stage 3: a bad arg is invalid_params BEFORE any browser --------------------------------
async def test_out_of_domain_arg_is_invalid_params_no_browser(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()
    httpd, base = site.serve()
    try:
        await _record_slotted_read(site, base, cache,
                                   slots={"code": SlotSpec(type="string", enum=["beta-9", "beta-8"])})
        site.gets.clear()
        bad = await call_flow_tool("lookup", cache, arguments={"code": "gamma"})   # not in the enum
        assert not bad.ok and bad.code == "invalid_params" and bad.retryable is False
        assert site.gets == [], "an invalid arg opened the browser (validate_params must refuse pre-flight)"
        # an unknown arg name and a required-missing arg are likewise invalid_params
        assert (await call_flow_tool("lookup", cache, arguments={"nope": "x"})).code == "invalid_params"
        assert (await call_flow_tool("lookup", cache, arguments={})).code == "invalid_params"  # required missing
        # a valid arg dispatches
        ok = await call_flow_tool("lookup", cache, arguments={"code": "beta-9"})
        assert ok.ok and "/typed-beta-9" in site.gets
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- H2 stage 3: a no-slot flow stays zero-arg and refuses stray args --------------------------
async def test_no_slot_flow_stays_zero_arg(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _Site(_pages())
    httpd, base = site.serve()
    try:
        await _learn_read(base, "daily", cache, approve=True)   # no slots
        tool = {t.name: t for t in list_flow_tools(cache)}["daily"]
        assert tool.input_schema == _empty_input_schema()
        assert (await call_flow_tool("daily", cache)).ok                      # zero-arg -> frozen replay
        assert (await call_flow_tool("daily", cache, arguments={"x": 1})).code == "invalid_params"  # stray arg
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- H2 stage 3: a secret slot is env-resolved, not a tool argument ----------------------------
async def test_secret_slot_env_resolved_not_an_argument(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()
    httpd, base = site.serve()
    try:
        # Treat the echo field AS a secret slot (env-resolved token typed into the page).
        await _record_slotted_read(site, base, cache,
                                   slots={"token": SlotSpec(secret=True, required=True, secret_env="UCA_MCP_TOK")})
        tool = {t.name: t for t in list_flow_tools(cache)}["lookup"]
        assert tool.input_schema == _empty_input_schema()          # secret excluded -> zero-arg tool
        assert "$UCA_MCP_TOK" in tool.description                    # ...but the env need is signalled
        # env set + no args -> the secret resolves and substitutes onto the page.
        monkeypatch.setenv("UCA_MCP_TOK", "s3cr3t9")
        site.gets.clear()
        ok = await call_flow_tool("lookup", cache)
        assert ok.ok and "/typed-s3cr3t9" in site.gets
        # passing the secret as an argument is refused (invalid_params) — it must come from $env.
        assert (await call_flow_tool("lookup", cache, arguments={"token": "x"})).code == "invalid_params"
        # env UNSET for a required secret -> a config gap (replay_error), NOT invalid_params.
        monkeypatch.delenv("UCA_MCP_TOK")
        unset = await call_flow_tool("lookup", cache)
        assert not unset.ok and unset.code == "replay_error"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- H2 stage 3 (review finding): an UNDECLARED write is never exposed nor dispatched ----------
async def test_undeclared_write_flow_is_never_exposed(tmp_path, monkeypatch) -> None:
    # A flow learned as a READ (spec.mutate=None) whose cached steps in fact MUTATE still FIRES the write on
    # replay (unconfirmed). It must be keyed off the ACTUAL mutating signal, not spec.mutate — never listed
    # as a read-only tool, never dispatched (a public surface must not let an outer agent drive it).
    import time as _t

    from ultracua.cache import CachedFlow, CachedStep
    from ultracua.locators import LocatorSpec

    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    spec = FlowSpec(name="sneaky", start_url="http://127.0.0.1:9/checkout", goal="show the total",
                    slots={"qty": SlotSpec(type="string")})   # NOTE: no mutate declared
    flows.save_spec(spec)
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, created_ts=_t.time(), steps=[
        CachedStep(intent="type qty", action="type", text="7", slot="qty",
                   locator=LocatorSpec(role="textbox", name="qty", tag="input")),
        CachedStep(intent="place the order", action="click", mutating=True,
                   locator=LocatorSpec(role="button", name="Place the order", tag="button")),
    ]))
    flows.approve(spec, cache=cache)
    assert "sneaky" not in {t.spec_name for t in list_flow_tools(cache)}, "an undeclared write was listed"
    out = await call_flow_tool("sneaky", cache, arguments={"qty": "9"})
    assert not out.ok and out.code in ("write_denied", "unknown_tool")   # refused, never dispatched


# --- H2 stage 3 SDK wiring: inputSchema is advertised ------------------------------------------
async def test_build_server_advertises_input_schema(tmp_path, monkeypatch) -> None:
    pytest.importorskip("mcp")
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()
    httpd, base = site.serve()
    try:
        spec = await _record_slotted_read(site, base, cache)
        from ultracua.mcpserver.server import build_server
        server = build_server(cache)
        # the low-level Server exposes the registered list_tools handler; call it to get the advertised tools.
        handler = server.request_handlers
        import mcp.types as mtypes
        result = await handler[mtypes.ListToolsRequest](mtypes.ListToolsRequest(method="tools/list"))
        tools = {t.name: t for t in result.root.tools}
        assert tools["lookup"].inputSchema == slots_to_input_schema(spec.slots)
        assert tools["lookup"].annotations.readOnlyHint is True
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- tool inventory: approved reads only ------------------------------------------------------
async def test_lists_only_approved_read_flows(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # .ultracua (specs + flows) lands under tmp, never the repo
    cache = FlowCache()
    site = _Site(_pages())
    httpd, base = site.serve()
    try:
        # An unapproved read flow must NOT be exposed (keeps unreviewed flows out of a client's hands).
        await _learn_read(base, "unapproved", cache, approve=False)
        assert list_flow_tools(cache) == []

        # A WRITE flow spec must NOT be exposed (default-deny) — filtered before health is even checked.
        wspec = FlowSpec(name="a-write", start_url=base + "/", goal="submit something",
                         mutate=MutateSpec(confirm_text_contains="Thanks"))
        flows.save_spec(wspec)
        assert "a-write" not in {t.spec_name for t in list_flow_tools(cache)}

        # An APPROVED read flow IS exposed, as a zero-argument tool.
        await _learn_read(base, "daily", cache, approve=True)
        tools = list_flow_tools(cache)
        names = {t.spec_name for t in tools}
        assert "daily" in names and "unapproved" not in names and "a-write" not in names
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- dispatch: happy path + unknown + drift-mapped-to-taxonomy --------------------------------
async def test_call_dispatches_and_maps_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _Site(_pages())
    httpd, base = site.serve()
    try:
        spec = await _learn_read(base, "daily", cache, approve=True)

        # Happy path: an approved read flow runs and returns an outcome (0-LLM; no key needed).
        out = await call_flow_tool("daily", cache)
        assert out.ok, f"expected ok, got code={out.code!r} msg={out.message!r}"

        # Unknown / unlisted tool: refused with a machine-readable code, never a crash.
        miss = await call_flow_tool("nope", cache)
        assert not miss.ok and miss.code == "unknown_tool"

        # Drift: the learned click target disappears -> replay fails loud -> a typed DriftError, which
        # the tool surfaces as a structured, NON-retryable outcome (not a wrong-but-plausible result).
        site.pages["/"] = _pages(link=False)["/"]
        drifted = await call_flow_tool("daily", cache)
        assert not drifted.ok and drifted.code == "drift" and drifted.retryable is False

        # And the underlying replay raises the taxonomy subclass directly (still a FlowReplayError).
        with pytest.raises(DriftError):
            await flows.replay(spec, require_approved=True, cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- SDK wiring smoke (mcp is a dev dep here) -------------------------------------------------
def test_build_server_constructs() -> None:
    # The `mcp` SDK is an OPTIONAL dependency (group `mcp`). The pure-core tests above run without it;
    # only this SDK-wiring smoke needs it, so skip cleanly when it's absent (CI installs `--group mcp`
    # so it actually runs there — same pattern as the provider live-path tests).
    pytest.importorskip("mcp")
    from ultracua.mcpserver.server import build_server

    server = build_server()  # lazy-imports mcp; must not raise with the SDK installed
    assert server.name == "ultracua"
