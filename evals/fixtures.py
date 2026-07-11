"""Local HTTP fixtures for key-less eval scenarios.

Dict-backed threaded HTTP server (the tests/ convention, generalized): GET serves from a
`pages` dict of path -> HTML; POST/PUT/PATCH/DELETE are RECORDED into `fx.writes` (the write-
safety scenarios' oracle: "did a write actually reach the server, and what was its body?") and
answered from `post_responses` (default: 303 redirect to `post_redirect` — the classic
form-submit -> confirmation-page shape) so write flows can complete.

Usage:
    fx = Fixture({"/": "<html>...</html>", "/next": "..."})
    with fx.serve() as base:
        await session.goto(base + "/")
    assert fx.writes == []          # a read flow must not have written
"""

from __future__ import annotations

import http.server
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class WriteRecord:
    method: str
    path: str
    body: str
    # Request headers the write arrived with (lower-cased keys) — lets a risk eval assert the write's
    # `idempotency-key` (distinct per row, stable per row on retry) actually reached the server, which is
    # the double-write / suppressed-write oracle for parameterized writes.
    headers: dict = field(default_factory=dict)


class Fixture:
    def __init__(
        self, pages: dict[str, str], *,
        post_redirect: str = "/",
        post_responses: Optional[dict[str, tuple[int, str]]] = None,  # path -> (status, html)
    ) -> None:
        self.pages = dict(pages)
        self.post_redirect = post_redirect
        self.post_responses = dict(post_responses or {})
        self.writes: list[WriteRecord] = []
        self.gets: list[str] = []

    @contextmanager
    def serve(self) -> Iterator[str]:
        fx = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:  # keep eval output clean
                pass

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                fx.gets.append(path)
                html = fx.pages.get(path)
                if html is None:
                    self.send_error(404)
                    return
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _write(self) -> None:
                path = self.path.split("?")[0]
                n = int(self.headers.get("Content-Length") or 0)
                hdrs = {k.lower(): v for k, v in self.headers.items()}
                fx.writes.append(WriteRecord(self.command, path,
                                             self.rfile.read(n).decode("utf-8", "replace"), headers=hdrs))
                if path in fx.post_responses:
                    status, html = fx.post_responses[path]
                    body = html.encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:  # form-submit shape: redirect to the confirmation page
                    self.send_response(303)
                    self.send_header("Location", fx.post_redirect)
                    self.end_headers()

            do_POST = do_PUT = do_PATCH = do_DELETE = _write

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()
            httpd.server_close()


def page(body: str, *, title: str = "eval") -> str:
    """Minimal well-formed HTML wrapper for fixture pages."""
    return f"<!doctype html><html><head><title>{title}</title></head><body>{body}</body></html>"
