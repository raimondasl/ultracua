"""REFUTER probe: does the in-page seq restart after a CROSS-ORIGIN hop, and does that collide?"""

from __future__ import annotations

import http.server
import threading

from ultracua import attribution
from ultracua.browser import BrowserSession


def _serve(page: str):
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
            self._send(page if self.path.split("?")[0] == "/" else "{}")

        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._send("{}", "application/json")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


_PAGE_A = """<h1>A</h1>
<button id='c' type='button'
  onclick="fetch('/api/commit',{method:'POST',body:'x=1'})">Continue</button>"""
_PAGE_B = """<h1>B</h1><button id='f' type='button'>Filter</button>"""


async def test_seq_restarts_cross_origin() -> None:
    a_httpd, a = _serve(_PAGE_A)
    b_httpd, b = _serve(_PAGE_B)
    try:
        async with BrowserSession(headless=True) as sess:
            page = sess.page
            await attribution.install(page)
            await page.goto(f"{a}/")
            await page.evaluate(attribution.LEARN_ATTRIB_JS)
            await page.click("#c")
            await page.wait_for_timeout(300)
            batch_a = await attribution.drain(page)
            print("ORIGIN A drain:", batch_a)

            await page.goto(f"{b}/")
            await page.click("#f")
            await page.wait_for_timeout(200)
            batch_b = await attribution.drain(page)
            print("ORIGIN B drain:", batch_b)

            seqs_a = [e.get("seq") for e in batch_a if isinstance(e, dict)]
            seqs_b = [e.get("seq") for e in batch_b if isinstance(e, dict)]
            print("A seqs:", seqs_a, "B seqs:", seqs_b)
            # Compose them exactly the way flow._author_steps does.
            events = list(batch_a) + list(batch_b)
            seq_to_step: dict = {}
            for step, batch in ((0, batch_a), (1, batch_b)):
                for ev in batch:
                    if isinstance(ev, dict) and ev.get("action") == "__commit":
                        if isinstance(ev.get("seq"), int):
                            seq_to_step[ev["seq"]] = step
            print("seq_to_step:", seq_to_step)
            print("attribute():", attribution.attribute(events, seq_to_step))
    finally:
        a_httpd.shutdown(); a_httpd.server_close()
        b_httpd.shutdown(); b_httpd.server_close()
