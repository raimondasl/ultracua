"""REPRO (audit scratch): a page-dispatched synthetic click launders a deferred write."""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.providers.scripted import ScriptedProvider


class _Site:
    def __init__(self, page: str) -> None:
        self.page = page
        self.posts: list[str] = []

    def serve(self):
        site = self

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
                self._send(site.page if self.path.split("?")[0] == "/" else "{}")

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.posts.append(self.headers.get("Idempotency-Key") or "")
                self._send("{}", "application/json")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# The auto-confirm shape: Continue arms a timer that SYNTHETICALLY clicks a hidden control,
# whose own handler POSTs. The write is genuinely DEFERRED out of the Continue click's turn.
_PAGE = """<h1>Panel</h1>
<button id='f' type='button'>Filter</button>
<button id='c' type='button' onclick="setTimeout(function(){document.getElementById('h').click()},__DELAY__)">Continue</button>
<button id='h' type='button' style='position:absolute;left:-9999px' onclick="fetch('/api/commit',{method:'POST',body:'x=1'})">hidden</button>
<script>
window.__n = 0;
for (const id of ['f','c']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id;
    const b = document.createElement('button'); b.type='button';
    b.textContent = 'marker' + (++window.__n); document.body.appendChild(b); });
</script>"""


def _prov():
    return ScriptedProvider([
        {"action": "click", "role": "button", "name": "Filter", "intent": "filter the list"},
        {"action": "click", "role": "button", "name": "Continue", "intent": "continue"},
        {"action": "click", "role": "button", "name": "Filter", "intent": "filter again"},
        {"action": "click", "role": "button", "name": "Filter", "intent": "filter once more"},
        {"action": "done", "intent": "done"},
    ])


import pytest


@pytest.mark.parametrize("delay", [40, 80, 120, 160, 220, 300])
async def test_repro(tmp_path: Path, delay: int) -> None:
    site = _Site(_PAGE.replace("__DELAY__", str(delay)))
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "work the panel"
    try:
        report = await run_cached(f"{base}/", goal, _prov(), cache, mode="learn", headless=True,
                                  verify_replay=False)
        print("POSTS:", site.posts)
        print("success:", report.success, "note:", report.note, "extra:", report.extra)
        print("write_unattributed:", report.extra.get("write_unattributed"))
        flow = cache.get(flow_key(goal, f"{base}/"))
        if flow is None:
            print("REFUSED (no cached flow)")
        else:
            print("CACHED:", [(s.intent, s.mutating) for s in flow.steps])
            site.posts.clear()
            replay = await run_cached(f"{base}/", goal, None, cache, mode="replay", headless=True)
            print("REPLAY success:", replay.success, "posts:", site.posts)
    finally:
        httpd.shutdown()
        httpd.server_close()
