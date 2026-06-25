"""`flows.record` — capture a human DEMONSTRATION into a verified, replayable flow (Phase I recorder). The
"human" is a scripted sequence of real interactions (key-less + deterministic).

A READ flow is verify-by-replayed and cached. A WRITE flow demonstrated WITHOUT a declared confirm check is
refused — even when the keyword classifier would miss it — so a write can never be silently cached as a read
and replay ungated. A WRITE flow DECLARED via a confirm check (`spec.mutate`) is captured SAFELY: its
form-submit is a gated mutating step (precond_scope captured inline), so on replay the mutation gate refuses
it under form drift, it carries an Idempotency-Key, and it is approval-gated — exactly like a learned write.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowReplayError, FlowSpec, MutateSpec, approve, record, replay


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
    # POST on the wire, which the watcher catches. A write demonstrated WITHOUT a declared confirm check is
    # refused (the recorder can't infer the action-completion signal) — never cached as a read flow.
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="wr", start_url=f"{base}/", goal="press go")  # no spec.mutate -> undeclared
        res = await record(spec, demo=_write_demo, headless=True, cache=cache)
        assert res.performed_write is True and res.is_write is True
        assert res.cached is False and "WRITE" in res.note and "confirm" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None  # not kept
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- WRITE CAPTURE: a DECLARED write demonstration is captured gated + idempotency-keyed --------------
def _serve_write(counter: dict, drift: bool = False):
    """A POST-form write fixture: GET / -> a <form method=post> with a 'Place order' submit -> POST /save
    increments the order counter (the irreversible side effect) and returns the 'Order placed' confirm
    signal. With drift=True, the SECOND GET / (i.e. replay) grows an extra input INSIDE the form so the
    submit's enclosing-form scope fingerprint diverges from the recorded one — exercising the mutation gate.
    """

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] == "/":
                counter["gets"] = counter.get("gets", 0) + 1
                extra = ("<input name='promo' aria-label='promo' />"
                         if (drift and counter["gets"] > 1) else "")
                self._send("<h1>Cart</h1>"
                           f"<form action='/save' method='post'>{extra}"
                           "<button>Place order</button></form>")
            else:
                self._send("not found", 404)

        def do_POST(self) -> None:  # noqa: N802
            counter["orders"] = counter.get("orders", 0) + 1            # the irreversible side effect
            counter["idem"] = self.headers.get("Idempotency-Key")       # the dedupe key the gate set (if any)
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)                                  # drain the form body
            self._send("<h1>Order placed</h1><p>Confirmation #999</p>")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _place_order_demo(page) -> None:
    await page.get_by_role("button", name="Place order").click()  # submits the POST form — the write


async def test_record_write_flow_caches_gated_and_idempotency_keyed(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="ord", start_url=f"{base}/", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Order placed"))
        res = await record(spec, demo=_place_order_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True       # a DECLARED write -> kept (not refused)
        assert res.performed_write is True                       # a POST fired on the wire during the demo
        assert counter["orders"] == 1                            # the demonstration itself placed it once

        # The submit is captured as a GATED mutating step carrying its precise (form-scoped) precondition.
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        assert flow is not None
        writes = [s for s in flow.steps if s.mutating]
        assert len(writes) == 1 and writes[0].precond_scope       # mutating + a non-empty precond_scope

        approve(spec, cache=cache)                                # writes are approval-gated
        result = await replay(spec, cache=cache)
        assert result == {"status": "confirmed", "data": None}    # the write landed + was confirmed
        assert counter["orders"] == 2                             # exactly ONE more write — no double-submit
        assert (counter.get("idem") or "").startswith("uca-")     # the write carried an Idempotency-Key
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_record_write_flow_mutation_gate_refuses_under_form_drift(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_write(counter, drift=True)               # replay's checkout form drifts
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="orddrift", start_url=f"{base}/", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Order placed"))
        res = await record(spec, demo=_place_order_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True
        assert counter["orders"] == 1                             # placed once during the demo
        approve(spec, cache=cache)
        with pytest.raises(FlowReplayError):                       # form/section drift -> gate refuses
            await replay(spec, cache=cache)
        assert counter["orders"] == 1                             # the write was NOT re-fired under drift
    finally:
        httpd.shutdown()
        httpd.server_close()


# A FORMLESS write: a keyword-named <button> with NO enclosing <form> that commits via a JS navigation (a
# write-behind-a-GET — the residual the engine's HTTP-method classifier can't see). Declaring it a write
# (MutateSpec) must still capture it GATED on its enclosing section: an empty precond_scope here would make
# the replay gate a no-op and let the write replay blind. (Closes the fail-open hole an earlier draft had:
# a declared-write mutating step that carried no precondition.)
def _serve_formless_write(counter: dict, drift: bool = False):
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/":
                counter["gets"] = counter.get("gets", 0) + 1
                extra = "<button>archive</button>" if (drift and counter["gets"] > 1) else ""
                self._send(
                    f"<h1>Account</h1>{extra}<button id='del'>Delete account</button>"
                    "<script>document.getElementById('del').addEventListener('click',"
                    " function(){ location.href = '/del'; });</script>")
            elif path == "/del":
                counter["dels"] = counter.get("dels", 0) + 1  # the irreversible side effect (a GET-write)
                self._send("<h1>Deleted!</h1>")
            else:
                self._send("not found", 404)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _delete_demo(page) -> None:
    await page.get_by_role("button", name="Delete account").click()  # JS navigates to /del — a GET-write
    await page.get_by_role("heading", name="Deleted!").wait_for()    # let the navigation land


async def test_record_write_flow_gates_a_formless_keyword_commit(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_formless_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="del", start_url=f"{base}/", goal="delete the account",
                        mutate=MutateSpec(confirm_text_contains="Deleted!"))
        res = await record(spec, demo=_delete_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True
        assert counter["dels"] == 1                              # the demo committed once
        # THE HOLE, CLOSED: a formless keyword commit is a mutating step that DOES carry a precondition.
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        writes = [s for s in flow.steps if s.mutating]
        assert len(writes) == 1 and writes[0].precond_scope      # gated, not an ungated mutating step

        approve(spec, cache=cache)
        result = await replay(spec, cache=cache)
        assert result == {"status": "confirmed", "data": None}
        assert counter["dels"] == 2                              # exactly one more — no double-submit
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_record_write_flow_formless_commit_refuses_under_drift(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_formless_write(counter, drift=True)     # replay's section drifts (an extra control)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="deldrift", start_url=f"{base}/", goal="delete the account",
                        mutate=MutateSpec(confirm_text_contains="Deleted!"))
        res = await record(spec, demo=_delete_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True
        assert counter["dels"] == 1
        approve(spec, cache=cache)
        with pytest.raises(FlowReplayError):                      # section drift -> the gate refuses
            await replay(spec, cache=cache)
        assert counter["dels"] == 1                              # the write was NOT re-fired under drift
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_record_write_flow_requires_a_confirm_check(tmp_path) -> None:
    # spec.mutate set but with NO confirm check -> a write flow can't be confirmed, so it's refused.
    counter: dict = {}
    httpd, base = _serve_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="noconf", start_url=f"{base}/", goal="place the order", mutate=MutateSpec())
        res = await record(spec, demo=_place_order_demo, headless=True, cache=cache)
        assert res.cached is False and "confirm check" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None  # not kept
    finally:
        httpd.shutdown()
        httpd.server_close()
