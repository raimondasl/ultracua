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


# A SELECT-driven write: an onchange handler fires a POST (fetch). `classify_mutation` never flags a select,
# so without explicit select write-gating this would replay UNGATED (the adversarial review's C1/H4). A
# DECLARED write must capture the select as a GATED mutating step (via the gate-all wire-write fallback,
# since a formless select has no form method, so every scoped actuated step is gated when a write fired and
# no commit was classified), idempotency-keyed, refusing under section drift.
def _serve_select_write(counter: dict, drift: bool = False):
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
                extra = "<button>extra</button>" if (drift and counter["gets"] > 1) else ""
                self._send(
                    f"<h1>Order</h1>{extra}"
                    "<select id=qty aria-label='qty'><option value=''>--</option>"
                    "<option value='2'>two</option><option value='3'>three</option></select>"
                    "<div id=out></div>"
                    "<script>document.getElementById('qty').addEventListener('change',function(){"
                    " fetch('/save',{method:'POST'}).then(r=>r.text()).then(t=>{"
                    " document.getElementById('out').textContent=t;});});</script>")
            else:
                self._send("not found", 404)

        def do_POST(self) -> None:  # noqa: N802
            counter["orders"] = counter.get("orders", 0) + 1            # the irreversible side effect
            counter["idem"] = self.headers.get("Idempotency-Key")
            self._send("Order placed")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _select_order_demo(page) -> None:
    await page.select_option("#qty", "2")                          # onchange -> fetch POST (the write)
    await page.get_by_text("Order placed").wait_for()              # let the POST land during the demo


async def test_record_write_flow_gates_a_submitting_select(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_select_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="selord", start_url=f"{base}/", goal="set the quantity",
                        mutate=MutateSpec(confirm_text_contains="Order placed"))
        res = await record(spec, demo=_select_order_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True and res.performed_write is True
        assert counter["orders"] == 1                              # placed once during the demo
        # THE C1/H4 HOLE, CLOSED: the select write is a GATED mutating step, not an ungated select.
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        writes = [s for s in flow.steps if s.mutating]
        assert len(writes) == 1 and writes[0].action == "select" and writes[0].precond_scope

        approve(spec, cache=cache)
        result = await replay(spec, cache=cache)
        assert result == {"status": "confirmed", "data": None}
        assert counter["orders"] == 2                              # exactly one more — gated, no double-submit
        assert (counter.get("idem") or "").startswith("uca-")      # the POST carried an Idempotency-Key
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_record_write_flow_select_refuses_under_drift(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_select_write(counter, drift=True)         # replay's section grows a control
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="seldrift", start_url=f"{base}/", goal="set the quantity",
                        mutate=MutateSpec(confirm_text_contains="Order placed"))
        res = await record(spec, demo=_select_order_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True
        assert counter["orders"] == 1
        approve(spec, cache=cache)
        with pytest.raises(FlowReplayError):                        # section drift -> the gate refuses
            await replay(spec, cache=cache)
        assert counter["orders"] == 1                              # the select write was NOT re-fired
    finally:
        httpd.shutdown()
        httpd.server_close()


# A TYPE-driven write: an input autosaves (fires a POST on `change`). `classify_mutation` never flags a
# `type`, and there is ALSO a benign (non-writing) button click in the demo. The gate-all fallback must gate
# the TYPE (the real write) — not just the benign click — or the autosave would replay UNGATED (the
# describe-reuse review's H1). Exercises the MULTI-actuation gate-all path.
def _serve_type_autosave_write(counter: dict, drift: bool = False):
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            counter["gets"] = counter.get("gets", 0) + 1
            extra = "<button>extra</button>" if (drift and counter["gets"] > 1) else ""
            # Autosave on `input` (NOT `change`): replay actuates a `type` via Playwright's fill(), which
            # fires `input` but not `change`, so an input-triggered autosave re-fires on replay (a
            # change/blur-only autosave would not — a separate replay-fidelity limitation).
            self._send(
                f"<h1>Profile</h1>{extra}<button id=tab>Details</button>"
                "<input id=name aria-label='name'><div id=out></div>"
                "<script>document.getElementById('tab').addEventListener('click',function(){});"  # benign
                "document.getElementById('name').addEventListener('input',function(){"
                " fetch('/save',{method:'POST'}).then(r=>r.text()).then(t=>{"
                " document.getElementById('out').textContent=t;});});</script>")

        def do_POST(self) -> None:  # noqa: N802
            counter["saves"] = counter.get("saves", 0) + 1
            counter["idem"] = self.headers.get("Idempotency-Key")
            self._send("Saved")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _type_autosave_demo(page) -> None:
    await page.get_by_role("button", name="Details").click()   # a BENIGN scoped click (no write)
    await page.fill("#name", "Ada")
    await page.locator("#name").blur()                          # change -> autosave POST (the write)
    await page.get_by_text("Saved").wait_for()


async def test_record_write_flow_gates_a_type_autosave(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_type_autosave_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="autosave", start_url=f"{base}/", goal="save the name",
                        mutate=MutateSpec(confirm_text_contains="Saved"))
        res = await record(spec, demo=_type_autosave_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True and res.performed_write is True
        assert counter["saves"] == 1                            # saved once during the demo
        # THE H1 HOLE, CLOSED: the TYPE (the real write) is a gated mutating step — not left ungated while
        # only the benign click is gated.
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        typed = [s for s in flow.steps if s.action == "type"]
        assert len(typed) == 1 and typed[0].mutating and typed[0].precond_scope
        # Documented OVER-GATE (gate-all): the benign Details click is gated too — the safe direction (a
        # superfluous drift check on a non-writing step, never an ungated write). It actuates once regardless.
        clicks = [s for s in flow.steps if s.action == "click"]
        assert len(clicks) == 1 and clicks[0].mutating

        approve(spec, cache=cache)
        result = await replay(spec, cache=cache)
        assert result == {"status": "confirmed", "data": None}
        assert counter["saves"] == 2                            # exactly one more — gated, no double-save
        assert (counter.get("idem") or "").startswith("uca-")   # the autosave POST carried an Idempotency-Key
    finally:
        httpd.shutdown()
        httpd.server_close()


# A <select> inside a REAL <form method=post> that submits on change: here `classify_mutation` still says
# read, but the form METHOD is visible to the inline override (recorder._step_from_event), so the select is
# gated WITHOUT needing the wire-watcher fallback (the C1 form_method-override branch).
def _serve_select_form_write(counter: dict):
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            self._send("<form action='/save' method='post'>"
                       "<select id=q aria-label='qty' onchange='this.form.submit()'>"
                       "<option value=''>--</option><option value='2'>two</option></select></form>")

        def do_POST(self) -> None:  # noqa: N802
            counter["orders"] = counter.get("orders", 0) + 1
            counter["idem"] = self.headers.get("Idempotency-Key")
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self._send("<h1>Order placed</h1>")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_record_write_flow_gates_a_form_submitting_select(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_select_form_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="selform", start_url=f"{base}/", goal="choose quantity",
                        mutate=MutateSpec(confirm_text_contains="Order placed"))

        async def _demo(page) -> None:
            await page.select_option("#q", "2")                       # onchange submits the POST form
            await page.get_by_role("heading", name="Order placed").wait_for()

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True
        assert counter["orders"] == 1
        # Gated via the form_method OVERRIDE (not the wire fallback): a mutating select with a precond_scope.
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        writes = [s for s in flow.steps if s.mutating]
        assert len(writes) == 1 and writes[0].action == "select" and writes[0].precond_scope

        approve(spec, cache=cache)
        result = await replay(spec, cache=cache)
        assert result == {"status": "confirmed", "data": None}
        assert counter["orders"] == 2                                # one more — gated, no double-submit
        assert (counter.get("idem") or "").startswith("uca-")
    finally:
        httpd.shutdown()
        httpd.server_close()


# MASKING GUARD: a benign GET-form submit is classified mutating (via the override) but fires NO counted
# write, so it must NOT offset the wire-write tally and let a separate formless POST cache UNGATED. (The
# describe-reuse review's write-count masking finding.)
def _serve_get_form_plus_post(counter: dict):
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] == "/search":
                self._send("<h1>results</h1>")
            else:
                self._send(
                    "<form method=get action='/search'><input name=q aria-label='q'>"
                    "<button>Go</button></form>"
                    "<button id=save type=button>Save</button><div id=out></div>"
                    "<script>document.getElementById('save').addEventListener('click',function(){"
                    " fetch('/save',{method:'POST'}).then(r=>r.text()).then(t=>{"
                    " document.getElementById('out').textContent=t;});});</script>")

        def do_POST(self) -> None:  # noqa: N802
            counter["saves"] = counter.get("saves", 0) + 1
            self._send("Saved")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _save_then_search_demo(page) -> None:
    await page.get_by_role("button", name="Save").click()      # a FORMLESS POST — the real write
    await page.get_by_text("Saved").wait_for()
    await page.get_by_role("button", name="Go").click()        # a GET-form submit (benign read) — navigates
    await page.wait_for_load_state("domcontentloaded")


async def test_record_write_flow_get_form_does_not_mask_a_formless_post(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_get_form_plus_post(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="mask", start_url=f"{base}/", goal="save then search",
                        mutate=MutateSpec(confirm_text_contains="Saved"))
        res = await record(spec, demo=_save_then_search_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True
        assert counter["saves"] == 1
        # THE MASKING HOLE, CLOSED: the formless POST "Save" is a GATED mutating step — the benign GET-form
        # "Go" (classified mutating but firing no counted write) did NOT offset the write tally.
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        save = [s for s in flow.steps if s.action == "click" and s.locator and s.locator.name == "Save"]
        assert len(save) == 1 and save[0].mutating and save[0].precond_scope
    finally:
        httpd.shutdown()
        httpd.server_close()


# CROSS-ORIGIN refusal: a demo that navigates to a DIFFERENT origin orphans the prior origin's not-yet-drained
# events (incl. the navigating click) — the recording could be silently truncated, and a flow isn't always
# verify-by-replayed to catch it. `record` must FAIL LOUD rather than cache a possibly-incomplete flow.
def _serve_linking(target_url: str):
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<h1>origin A</h1><a href='{target_url}'>cross</a>".encode())

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_record_refuses_a_cross_origin_demo(tmp_path) -> None:
    httpd_b, base_b = _serve()                          # origin B (a different port = a different origin)
    httpd_a, base_a = _serve_linking(f"{base_b}/")      # origin A links to B
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="xo", start_url=f"{base_a}/", goal="hop across origins")

        async def _demo(page) -> None:
            await page.get_by_role("link", name="cross").click()   # A -> B : a CROSS-origin navigation
            await page.wait_for_load_state("domcontentloaded")

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        assert res.cached is False and "origin" in res.note.lower()
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None  # not kept
    finally:
        for h in (httpd_a, httpd_b):
            h.shutdown()
            h.server_close()


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
