"""`flows.record` — capture a human DEMONSTRATION into a verified, replayable flow (Phase I recorder, read
flows only). The "human" is a scripted sequence of real interactions (key-less + deterministic). A read
flow is verify-by-replayed and cached; a flow that fires a WRITE on the wire is refused — even when the
keyword classifier would miss it — so a write can never be cached as a read flow and replay ungated.
"""

from __future__ import annotations

import http.server
import threading

from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowSpec, record


def _serve():
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] == "/":
                self._send('<section id="s"><h2>Step</h2>'
                           '<a href="/done">Continue</a>'
                           '<form action="/save" method="post"><button>Go</button></form></section>')
            else:
                self._send(f"<h1>{self.path}</h1>")

        def do_POST(self) -> None:  # noqa: N802
            self._send("<h1>saved</h1>")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _read_demo(page) -> None:
    await page.get_by_role("link", name="Continue").click()  # a GET link — a read


async def _write_demo(page) -> None:
    await page.get_by_role("button", name="Go").click()      # submits a POST form — a write


async def test_record_read_flow_verifies_and_caches(tmp_path) -> None:
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="rd", start_url=f"{base}/", goal="continue to done")
        res = await record(spec, demo=_read_demo, headless=True, cache=cache)
        assert res.performed_write is False
        assert res.cached is True and res.reproduced is True          # captured, replayed 0-LLM, kept
        assert len(res.steps) == 1 and res.steps[0].action == "click"
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is not None
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_record_refuses_a_write_flow_via_the_wire_watcher(tmp_path) -> None:
    # The button says "Go" (no mutating keyword), so the classifier would MISS it — but the demo fires a
    # POST on the wire, which the watcher catches. A write is never cached as a read flow.
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="wr", start_url=f"{base}/", goal="press go")
        res = await record(spec, demo=_write_demo, headless=True, cache=cache)
        assert res.performed_write is True
        assert res.cached is False and "WRITE" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None  # not kept
    finally:
        httpd.shutdown()
        httpd.server_close()
