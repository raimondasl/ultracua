"""A2: a demonstrated DOCUMENT-CLASS write was dropped from the recipe while replay said "confirmed".

A `<form method=post>` submission leaves the browser as a NAVIGATION, not a fetch/XHR. The recorder's
per-write attribution instruments `fetch` / `XMLHttpRequest.send` / `navigator.sendBeacon`, so a form POST
emitted no marker; and `_watch_request` tallied only `resource_type in ("fetch","xhr")`, so it was not
counted on the wire either. `unattributed_writes` therefore stayed 0 for a write that genuinely happened.

WHY IT SURVIVED THIS LONG — the re-probe corrected the filed finding. A flow whose ONLY write is
document-class was already refused: the belt-and-suspenders `wire_write and not gated` arm in `flows.record`
fires when nothing at all is gated. The hole needs a SECOND, correctly-gated write in the same flow to
disarm that arm — the filed "save draft, then send" shape. So these tests all carry a sibling fetch-POST
commit; without it they would pass against the pre-fix code and prove nothing.

The decisive asymmetry, pinned by `test_a_masked_fetch_write_is_still_refused` below: the SAME masking shape
is refused when the draft write goes over `fetch` and was cached when it went over a form POST. Nothing
about the shape was unsafe — only the transport the recorder could not see.

Two outcomes, depending on whether the commit can be attributed:
  * fired from a non-actionable element (a `<div>`, an href-less `<a>`) -> NO step is captured, the write is
    unattributable, and recording must REFUSE. Previously: cached, and the write never fired again.
  * fired from a `<button type=button>` calling `form.submit()` -> the click IS captured and is the sole
    commit in the write's synchronous turn, so it must be GATED (mutating + precond_scope + an
    Idempotency-Key). Previously: cached as a READ, then re-fired on every replay with no gate and no key.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowSpec, MutateSpec, approve, record, replay


class _Site:
    """A composer with TWO writes, which is what makes the hole reachable.

    `/draft` is the document-class write under test — a `<form method=post target=sink>` whose submission
    is triggered by `commit_html`. It targets a hidden iframe so the page does NOT navigate away, which is
    what lets the demo continue to the second write.

    `/send` is an ordinary fetch-POST behind a plainly-named button. It is the MASK: it records correctly,
    so `gated` is non-empty and the `wire_write and not gated` refusal cannot fire on the draft."""

    def __init__(self, commit_html: str, commit_script: str) -> None:
        self.commit_html, self.commit_script = commit_html, commit_script
        self.writes: list[tuple[str, str]] = []      # (path, Idempotency-Key)

    def serve(self):
        site = self

        class _H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def _send(self, body: str, ctype: str = "text/html") -> None:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self) -> None:  # noqa: N802
                if self.path.split("?")[0] != "/":
                    self._send("<h1>ok</h1>")
                    return
                self._send(
                    "<h1>Composer</h1>"
                    "<iframe name='sink' style='display:none'></iframe>"
                    "<form id='f' method='post' action='/draft' target='sink'>"
                    "<input name='body' value='hello'></form>"
                    f"{site.commit_html}"
                    "<button id='send' type='button'>Send order</button>"
                    "<p id='out'></p>"
                    "<script>"
                    f"{site.commit_script}"
                    "document.getElementById('send').addEventListener('click', function () {"
                    "  fetch('/send', {method: 'POST'})"
                    "    .then(function () { document.getElementById('out').textContent = 'Sent!'; });"
                    "});"
                    "</script>")

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                site.writes.append((self.path.split("?")[0],
                                    self.headers.get("Idempotency-Key") or ""))
                self._send("{}", "application/json")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# The commit is a bare <div> — matched by NONE of the recorder's ACTIONABLE selector, so no step at all.
_DIV = ("<div id='draft'>Store draft</div>",
        "document.getElementById('draft').addEventListener('click', function () {"
        "  document.getElementById('f').submit(); });")

# An href-less <a>. `a[href]` is in ACTIONABLE; a bare <a> is not — so it behaves exactly like the <div>.
_ANCHOR = ("<a id='draft'>Store draft</a>",
           "document.getElementById('draft').addEventListener('click', function () {"
           "  document.getElementById('f').submit(); });")

# A <button type=button>. It IS captured, and its click is the sole commit in the write's synchronous turn,
# so this one is ATTRIBUTABLE and must end up gated rather than refused. The name is deliberately bland so
# the keyword classifier cannot rescue it.
_BUTTON = ("<button id='draft' type='button'>Store draft</button>",
           "document.getElementById('draft').addEventListener('click', function () {"
           "  document.getElementById('f').submit(); });")

# The CONTROL for the whole file: the same masking shape, but the draft write goes over fetch. This was
# ALWAYS refused, and pins that the bug was about the TRANSPORT, not the shape.
_DIV_FETCH = ("<div id='draft'>Store draft</div>",
              "document.getElementById('draft').addEventListener('click', function () {"
              "  fetch('/draft', {method: 'POST'}); });")


async def _demo(page) -> None:
    await page.click("#draft")
    await page.wait_for_timeout(350)                 # let the document-class POST leave the browser
    await page.get_by_role("button", name="Send order").click()
    await page.get_by_text("Sent!").wait_for()


async def _record(tmp_path: Path, site: _Site, base: str, name: str):
    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name=name, start_url=f"{base}/", goal="save the draft then send", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Sent!"))
    res = await record(spec, demo=_demo, headless=True, cache=cache)
    return cache, spec, res


@pytest.mark.parametrize("commit", [_DIV, _ANCHOR], ids=["bare-div", "href-less-anchor"])
async def test_record_refuses_a_document_class_write_it_cannot_attribute(tmp_path: Path, commit) -> None:
    """The commit element is not actionable, so no step exists to gate. Refuse rather than cache a recipe
    that has silently lost the write."""
    site = _Site(*commit)
    httpd, base = site.serve()
    try:
        cache, spec, res = await _record(tmp_path, site, base, "draft-div")
        assert [p for p, _ in site.writes] == ["/draft", "/send"], site.writes   # the demo really wrote

        assert res.cached is False
        assert res.is_write is True
        assert "could not be tied to a single commit" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_record_gates_a_document_class_write_it_can_attribute(tmp_path: Path) -> None:
    """A `<button type=button>` calling `form.submit()` IS attributable — the click is the sole commit in
    the write's synchronous turn. It must be gated, not refused: refusing an attributable write would cost
    a legitimate recording."""
    site = _Site(*_BUTTON)
    httpd, base = site.serve()
    try:
        cache, spec, res = await _record(tmp_path, site, base, "draft-button")
        assert res.cached is True, res.note

        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        draft = next(s for s in flow.steps if (s.locator.name if s.locator else "") == "Store draft")
        # `_MUTATION_CTX_JS` reports submit=false for type="button" and the name carries no mutating
        # keyword, so NOTHING but the wire evidence can classify this step.
        assert draft.mutating is True
        assert draft.precond_scope != ""

        approve(spec, cache=cache)
        del site.writes[:]
        assert await replay(spec, cache=cache) == {"status": "confirmed", "data": None}

        by_path = dict(site.writes)
        assert sorted(by_path) == ["/draft", "/send"], site.writes    # fired exactly once each
        assert by_path["/draft"].startswith("uca-")                   # ...and the draft carried a KEY
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_masked_fetch_write_is_still_refused(tmp_path: Path) -> None:
    """The control that names the bug: the SAME masking shape over `fetch` was always refused. The form
    POST was cached only because the recorder could not see that transport."""
    site = _Site(*_DIV_FETCH)
    httpd, base = site.serve()
    try:
        _cache, _spec, res = await _record(tmp_path, site, base, "draft-fetch")
        assert res.cached is False
        assert "could not be tied to a single commit" in res.note
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== the false-refuse guard ====================

def _serve_plain_form(writes: list):
    """An ORDINARY `<button type=submit>` POST form — the overwhelmingly common shape. Its write is now
    counted on the wire for the first time, so without a matching `form` marker it would start
    false-refusing every such recording. This is the test that catches that."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            body = (b"<h1>Cart</h1><form method='post' action='/order'>"
                    b"<button type='submit'>Place order</button></form>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            writes.append((self.path, self.headers.get("Idempotency-Key") or ""))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Order placed</h1>")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _serve_clobbered_form(writes: list):
    """The same ordinary POST form, but carrying controls NAMED `action` and `method`.

    That is legal, unremarkable markup — and it CLOBBERS the DOM: `form.action` then evaluates to the
    `<input>` element, not the url (measured: `'[object HTMLInputElement]'`). A marker built from the IDL
    property would resolve to a nonsense url, fail to match the wire request, and false-refuse the whole
    recording. `getAttribute` cannot be clobbered, which is why the marker reads the raw attributes."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            body = (b"<h1>Cart</h1><form method='post' action='/order'>"
                    b"<input name='action' value='decoy'><input name='method' value='decoy'>"
                    b"<button type='submit'>Place order</button></form>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            writes.append((self.path, self.headers.get("Idempotency-Key") or ""))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Order placed</h1>")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_a_form_whose_controls_clobber_action_still_records(tmp_path: Path) -> None:
    writes: list = []
    httpd, base = _serve_clobbered_form(writes)
    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name="clobber", start_url=f"{base}/", goal="place the order", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Order placed"))

    async def _place(page) -> None:
        await page.get_by_role("button", name="Place order").click()

    try:
        res = await record(spec, demo=_place, headless=True, cache=cache)
        assert res.cached is True, res.note      # a clobbered form must not false-refuse
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_an_ordinary_submit_button_post_form_still_records_and_replays(tmp_path: Path) -> None:
    writes: list = []
    httpd, base = _serve_plain_form(writes)
    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name="plain", start_url=f"{base}/", goal="place the order", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Order placed"))

    async def _place(page) -> None:
        await page.get_by_role("button", name="Place order").click()

    try:
        res = await record(spec, demo=_place, headless=True, cache=cache)
        assert res.cached is True, res.note          # must NOT have started false-refusing
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        assert [s.mutating for s in flow.steps if s.action == "click"] == [True]

        approve(spec, cache=cache)
        del writes[:]
        assert await replay(spec, cache=cache) == {"status": "confirmed", "data": None}
        assert len(writes) == 1 and writes[0][1].startswith("uca-")
    finally:
        httpd.shutdown()
        httpd.server_close()
