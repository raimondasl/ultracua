"""Flow-staleness canary: a cheap, read-only probe that a saved flow still STARTS (its first cached
locator resolves on the start page) — fresh / stale / not-learned — without acting, writing, or recording
health. Catches entry-page rot early, before the scheduled run fails."""

from __future__ import annotations

import http.server
import threading

from ultracua.cache import FlowCache
from ultracua.flows import FlowSpec, canary, learn
from ultracua.llm.base import Router, Tier
from ultracua.llm.mock import MockClient
from ultracua.providers.scripted import ScriptedProvider

_STEPS = [
    {"action": "click", "role": "link", "name": "Continue", "intent": "continue"},
    {"action": "done", "intent": "done"},
]


def _serve(state: dict):
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] == "/":
                entry = '<a href="/done">Continue</a>' if state["entry"] else "<p>(removed)</p>"
                body = f"<section id=s><h2>Go</h2>{entry}</section>"
            else:
                body = "<h1>done</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _mock_router() -> Router:
    mc = MockClient(actions=[{"found": True, "data": None}], tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


async def test_canary_not_learned_then_fresh_then_stale(tmp_path) -> None:
    state = {"entry": True}
    httpd, base = _serve(state)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="go", start_url=f"{base}/", goal="continue", headless=True)

        # nothing cached yet -> not-learned (no navigation needed)
        assert (await canary(spec, cache=cache)).status == "not-learned"

        await learn(spec, provider=ScriptedProvider(list(_STEPS)), router=_mock_router(), cache=cache)
        assert (await canary(spec, cache=cache)).status == "fresh"      # the entry control resolves

        state["entry"] = False                                          # the entry control is gone
        r = await canary(spec, cache=cache)
        assert r.status == "stale" and "resolve" in r.detail.lower()    # caught BEFORE a scheduled run
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_canary_stale_when_start_page_unreachable(tmp_path) -> None:
    state = {"entry": True}
    httpd, base = _serve(state)
    cache = FlowCache(root=tmp_path)
    spec = FlowSpec(name="go", start_url=f"{base}/", goal="continue", headless=True)
    try:
        await learn(spec, provider=ScriptedProvider(list(_STEPS)), router=_mock_router(), cache=cache)
    finally:
        httpd.shutdown()  # the site is now down
        httpd.server_close()
    r = await canary(spec, cache=cache)
    assert r.status == "stale"  # an unreachable start page is itself a staleness signal
