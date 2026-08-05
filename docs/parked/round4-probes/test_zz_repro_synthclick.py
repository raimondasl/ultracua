"""TEMPORARY audit repro — delete after the run."""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

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
                site.posts.append(self.path.split("?")[0])
                self._send("{}", "application/json")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _page(delay: int) -> str:
    """Continue arms a timer that CLICKS a hidden button, whose own handler POSTs.

    The auto-confirm / auto-submit-after-delay shape. The write is deferred; the synthetic click is
    the only thing between it and `__ucturn === 0`.
    """
    return f"""<h1>Panel</h1>
<button id='f' type='button'>Filter</button>
<button id='c' type='button'
        onclick="setTimeout(function(){{document.getElementById('h').click()}},{delay})">Continue</button>
<button id='h' type='button' style='display:none'
        onclick="fetch('/api/commit',{{method:'POST',body:'x=1'}})">h</button>
<button id='n1' type='button'>Alpha</button>
<button id='n2' type='button'>Beta</button>
<button id='n3' type='button'>Gamma</button>
<button id='n4' type='button'>Delta</button>
<script>
for (const id of ['f','c','n1','n2','n3','n4']) document.getElementById(id).addEventListener('click',
  function(){{ document.querySelector('h1').textContent = 'clicked ' + id + ' ' + Date.now(); }});
</script>"""


def _prov():
    acts = [
        {"action": "click", "role": "button", "name": "Filter", "intent": "filter the list"},
        {"action": "click", "role": "button", "name": "Continue", "intent": "continue"},
        {"action": "click", "role": "button", "name": "Alpha", "intent": "look at alpha"},
        {"action": "click", "role": "button", "name": "Beta", "intent": "look at beta"},
        {"action": "click", "role": "button", "name": "Gamma", "intent": "look at gamma"},
        {"action": "click", "role": "button", "name": "Delta", "intent": "look at delta"},
    ]
    return ScriptedProvider(acts + [{"action": "done", "intent": "done"}])


@pytest.mark.parametrize("delay", [100, 200, 300, 400, 600, 900])
async def test_repro(tmp_path: Path, delay: int) -> None:
    site = _Site(_page(delay))
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "work the panel"
    try:
        report = await run_cached(f"{base}/", goal, _prov(), cache, mode="learn", headless=True,
                                  verify_replay=False)
        flow = cache.get(flow_key(goal, f"{base}/"))
        marked = None if flow is None else [(i, s.intent, s.mutating) for i, s in enumerate(flow.steps)]
        print(f"\nNOTE={report.note!r} extra={report.extra}")
        print(f"\nDELAY={delay} posts={site.posts} success={report.success} "
              f"unattributed={report.extra.get('write_unattributed')} "
              f"cached={report.extra.get('cached')} steps={marked}")
    finally:
        httpd.shutdown()
        httpd.server_close()
