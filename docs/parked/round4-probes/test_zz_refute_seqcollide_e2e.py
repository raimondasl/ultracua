"""REFUTER e2e: a write on origin A, then a cross-origin hop, then another click.

Does the seq restart on the new origin steal the gate from the step that really wrote?
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.providers.scripted import ScriptedProvider


def _serve(page_fn):
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str, ctype: str = "text/html") -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body.encode())))
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            self._send(page_fn() if self.path.split("?")[0] == "/" else "{}")

        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            posts.append(self.path)
            self._send("{}", "application/json")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


posts: list = []


async def test_cross_origin_seq_collision_moves_the_gate(tmp_path: Path) -> None:
    posts.clear()
    b_httpd, b = _serve(lambda: "<h1>B</h1><button id='f' type='button'>Filter</button>")
    a_httpd, a = _serve(
        lambda: "<h1>A</h1>"
        "<button id='c' type='button' onclick=\"fetch('/api/commit',{method:'POST',body:'x=1'})\">"
        "Continue</button>"
        f"<a id='n' href='{b}/'>Next page</a>")
    cache = FlowCache(root=tmp_path / "c")
    goal = "work the panel"
    prov = ScriptedProvider([
        {"action": "click", "role": "button", "name": "Continue", "intent": "continue"},
        {"action": "click", "role": "link", "name": "Next page", "intent": "open the next page"},
        {"action": "click", "role": "button", "name": "Filter", "intent": "filter the list"},
        {"action": "done", "intent": "done"},
    ])
    try:
        report = await run_cached(f"{a}/", goal, prov, cache, mode="learn", headless=True,
                                  verify_replay=False)
        print("posts:", posts)
        print("extra:", report.extra)
        flow = cache.get(flow_key(goal, f"{a}/", "default"))
        if flow is None:
            print("REFUSED / not cached")
        else:
            print("STEPS:", [(s.intent, s.mutating) for s in flow.steps])
    finally:
        a_httpd.shutdown(); a_httpd.server_close()
        b_httpd.shutdown(); b_httpd.server_close()
