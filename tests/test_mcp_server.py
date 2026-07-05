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
from ultracua.cache import FlowCache
from ultracua.flows import (
    DriftError,
    EscalateError,
    FlowReplayError,
    FlowSpec,
    MutateSpec,
    ShapeDriftError,
)
from ultracua.mcpserver import call_flow_tool, list_flow_tools


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
