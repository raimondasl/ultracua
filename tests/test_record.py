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
# DECLARED write must capture the select as a GATED mutating step (via PER-WRITE attribution — the fetch
# marker ties the POST to the select that was actuated when it fired, since a formless select has no form
# method), idempotency-keyed, refusing under section drift.
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


# THE MASKING CLASS, CLOSED (per-write attribution). Two commits in one demo: (1) a SUBMIT button inside a
# POST <form> whose native submit is SUPPRESSED (preventDefault) so it fires NO write, and (2) a SEPARATE
# formless <button type=button> that fetch-POSTs — the real write. The suppressed submit is form-classified
# as mutating (harmless — gating a non-writing submit only adds a drift check), which under the OLD
# all-or-nothing fallback (gated behind `not any(mutating)`) suppressed attribution of the formless POST
# entirely, leaving it UNGATED / double-submittable on replay. Per-write attribution ties the POST's marker
# to the formless button's seq and gates EXACTLY that step — independently of the already-mutating submit.
def _serve_suppressed_submit_plus_formless(counter: dict, drift: bool = False):
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
                # drift grows the FORMLESS button's section on replay so its scope fingerprint diverges.
                extra = "<button type=button>noise</button>" if (drift and counter["gets"] > 1) else ""
                self._send(
                    "<h1>Account</h1>"
                    "<form action='/noop' method='post'><button>Validate</button></form>"
                    f"<section id='savesec'>{extra}<button type=button id='save'>Save</button></section>"
                    "<div id=out></div>"
                    "<script>"
                    " document.querySelector('form').addEventListener('submit',"
                    "   function(e){ e.preventDefault(); });"          # the submit fires NO write
                    " document.getElementById('save').addEventListener('click', function(){"
                    "   fetch('/save',{method:'POST'}).then(r=>r.text())"
                    "     .then(t=>{ document.getElementById('out').textContent=t; }); });"  # the REAL write
                    "</script>")
            else:
                self._send("not found", 404)

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?")[0] == "/save":
                counter["saves"] = counter.get("saves", 0) + 1     # the irreversible side effect
                counter["idem"] = self.headers.get("Idempotency-Key")
                self._send("SAVED-OK-777")                         # the confirm signal (ensures the POST landed)
            else:                                                  # /noop is never hit (submit is suppressed)
                counter["noop"] = counter.get("noop", 0) + 1
                self._send("noop")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _suppressed_then_formless_demo(page) -> None:
    await page.get_by_role("button", name="Validate").click()   # POST-form submit, preventDefault'd (no write)
    await page.get_by_role("button", name="Save").click()       # formless fetch POST — the real write
    await page.get_by_text("SAVED-OK-777").wait_for()           # let the POST land during the demo


async def test_record_write_suppressed_submit_does_not_mask_a_formless_post(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_suppressed_submit_plus_formless(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="mask", start_url=f"{base}/", goal="validate then save",
                        mutate=MutateSpec(confirm_text_contains="SAVED-OK-777"))
        res = await record(spec, demo=_suppressed_then_formless_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True and res.performed_write is True
        assert counter["saves"] == 1                              # the demo saved exactly once
        assert counter.get("noop") is None                       # the suppressed submit fired NO write

        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        assert flow is not None
        # THE FIX: the SEPARATE formless POST is gated on its OWN commit — not masked by the mutating submit.
        save = [s for s in flow.steps if s.action == "click" and s.locator and s.locator.name == "Save"]
        assert len(save) == 1 and save[0].mutating and save[0].precond_scope   # attributed + gated

        approve(spec, cache=cache)
        result = await replay(spec, cache=cache)
        assert result == {"status": "confirmed", "data": None}
        assert counter["saves"] == 2                              # exactly one more — gated, no double-submit
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_record_write_formless_post_refuses_under_section_drift(tmp_path) -> None:
    # The masking-class fix must remain FAIL-LOUD: once the formless POST is gated on its own commit, a
    # section drift on that commit refuses the write on replay (no blind re-fire).
    counter: dict = {}
    httpd, base = _serve_suppressed_submit_plus_formless(counter, drift=True)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="maskdrift", start_url=f"{base}/", goal="validate then save",
                        mutate=MutateSpec(confirm_text_contains="SAVED-OK-777"))
        res = await record(spec, demo=_suppressed_then_formless_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True
        assert counter["saves"] == 1
        approve(spec, cache=cache)
        with pytest.raises(FlowReplayError):                      # the Save section drifted -> gate refuses
            await replay(spec, cache=cache)
        assert counter["saves"] == 1                              # the formless write was NOT re-fired
    finally:
        httpd.shutdown()
        httpd.server_close()


# DEFERRED WRITE -> FAIL LOUD. A write fired OUTSIDE its actuation's synchronous turn (a setTimeout/debounce
# whose fetch lands only AFTER a later benign click) cannot be tied to the right commit by `__uclast` — the
# in-page heuristic would mis-attribute it to the later benign click and cache the flow with the REAL commit
# left ungated (the adversarial-review fail-open). `__uclast` is therefore valid only for the synchronous turn
# (cleared on the next macrotask), so a deferred write reads null -> is UNATTRIBUTED -> `record` REFUSES the
# whole flow rather than cache a write that would replay ungated. (Fail-loud is always safe; fail-open is the
# bug class.)
def _serve_deferred_write(counter: dict):
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
                self._send(
                    "<h1>Editor</h1>"
                    "<button type=button id='commit'>Commit</button>"
                    "<button type=button id='next'>Next</button><div id=out></div>"
                    "<script>"
                    " document.getElementById('commit').addEventListener('click', function(){"
                    "   setTimeout(function(){ fetch('/save',{method:'POST'}).then(r=>r.text())"
                    "     .then(t=>{ document.getElementById('out').textContent=t; }); }, 120); });"
                    " document.getElementById('next').addEventListener('click', function(){});"  # benign
                    "</script>")
            else:
                self._send("not found", 404)

        def do_POST(self) -> None:  # noqa: N802
            counter["saves"] = counter.get("saves", 0) + 1
            self._send("DEFERRED-SAVED")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_record_write_deferred_write_outside_its_turn_is_refused(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_deferred_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="deferred", start_url=f"{base}/", goal="commit then move on",
                        mutate=MutateSpec(confirm_text_contains="DEFERRED-SAVED"))

        async def _demo(page) -> None:
            await page.get_by_role("button", name="Commit").click()   # arms a write that fires 120ms LATER
            await page.get_by_role("button", name="Next").click()     # a benign click in between
            await page.get_by_text("DEFERRED-SAVED").wait_for()       # the deferred POST lands during the demo

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        # The write fired outside Commit's turn (after Next), so it was UNATTRIBUTED -> the flow is REFUSED,
        # NOT cached with the real commit ungated.
        assert res.cached is False and "gated" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None   # never cached
        assert counter["saves"] == 1                                  # only the demo's own write; no replay
    finally:
        httpd.shutdown()
        httpd.server_close()


# NESTED SYNTHETIC COMMIT -> FAIL LOUD. A single user click on a wrapper control whose handler dispatches a
# nested click on a hidden control (`hidden.click()`) AND then fires a formless write: the nested click shares
# the wrapper's synchronous turn, so the last-writer-wins __uclast can't tell which of the two commits issued
# the write. Recording it under last-writer-wins would gate the (benign) nested commit and cache the wrapper's
# real write UNGATED (the adversarial-review fail-open). The per-turn commit count (>1 in this turn) marks the
# write UNATTRIBUTABLE -> `record` REFUSES rather than gate the wrong step.
def _serve_nested_commit_write(counter: dict):
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
                self._send(
                    "<h1>Wrapper</h1>"
                    "<section id='secA'><button type=button id='alpha'>Alpha</button></section>"
                    "<section id='secB'><button type=button id='beta'>Beta</button></section><div id=out></div>"
                    "<script>"
                    " document.getElementById('beta').addEventListener('click', function(){});"  # benign target
                    " document.getElementById('alpha').addEventListener('click', function(){"
                    "   document.getElementById('beta').click();"                # nested SYNTHETIC commit, same turn
                    "   fetch('/save',{method:'POST'}).then(r=>r.text())"        # the REAL write, same turn
                    "     .then(t=>{ document.getElementById('out').textContent=t; }); });"
                    "</script>")
            else:
                self._send("not found", 404)

        def do_POST(self) -> None:  # noqa: N802
            counter["saves"] = counter.get("saves", 0) + 1
            self._send("NESTED-SAVED")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_record_write_nested_synthetic_commit_is_refused(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_nested_commit_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="nested", start_url=f"{base}/", goal="press the wrapper",
                        mutate=MutateSpec(confirm_text_contains="NESTED-SAVED"))

        async def _demo(page) -> None:
            await page.get_by_role("button", name="Alpha").click()   # ONE gesture -> nested Beta click + write
            await page.get_by_text("NESTED-SAVED").wait_for()

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        # Two commits share the turn -> the write is unattributable -> REFUSED, not cached with Alpha ungated.
        assert res.cached is False and "single" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None
        assert counter["saves"] == 1                                  # demo only; no cached flow -> no replay
    finally:
        httpd.shutdown()
        httpd.server_close()


# AWAITED (deferred) write -> FAIL LOUD. A Save click whose handler does `fetch(prefetch).then(() => fetch(POST))`
# — the write fires only after an awaited macrotask round-trip, in a LATER turn (__ucturn back to 0). The
# in-page signal can't PROVE the deferred write's cause (a load-armed write would look identical), so it is
# left UNATTRIBUTED and `record` REFUSES rather than risk gating the wrong step / caching an ungated write.
# (Trading this validate-then-submit coverage for safety is deliberate: fail-loud, with re-record guidance.)
def _serve_awaited_write(counter: dict):
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
                self._send(
                    "<h1>Save</h1><section id='sec'><button type=button id='save'>Save</button></section>"
                    "<div id=out></div>"
                    "<script>document.getElementById('save').addEventListener('click', function(){"
                    "  fetch('/prefetch').then(function(){ return fetch('/save',{method:'POST'}); })"  # awaited
                    "    .then(r=>r.text()).then(t=>{ document.getElementById('out').textContent=t; }); });</script>")
            elif path == "/prefetch":
                self._send("pre")                                     # a real GET round-trip (a later macrotask)
            else:
                self._send("not found", 404)

        def do_POST(self) -> None:  # noqa: N802
            counter["saves"] = counter.get("saves", 0) + 1
            counter["idem"] = self.headers.get("Idempotency-Key")
            self._send("ASYNC-SAVED")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_record_write_awaited_deferred_write_is_refused(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_awaited_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="awaited", start_url=f"{base}/", goal="save after prefetch",
                        mutate=MutateSpec(confirm_text_contains="ASYNC-SAVED"))

        async def _demo(page) -> None:
            await page.get_by_role("button", name="Save").click()
            await page.get_by_text("ASYNC-SAVED").wait_for()          # the POST lands after the awaited GET

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        # The write fires in a LATER turn (deferred) -> its cause isn't provable in-page -> REFUSED, fail loud.
        assert res.cached is False and "single" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None
        assert counter["saves"] == 1                                  # demo only; no cached flow -> no replay
    finally:
        httpd.shutdown()
        httpd.server_close()


# LOAD-ARMED write + ONE unrelated commit -> FAIL LOUD. The fail-open the deferred-attribution branch once
# opened: a page arms a POST on LOAD (setTimeout) and the demo has exactly ONE benign click. If the deferred
# write were attributed to that sole commit, the benign click would be gated while the load-armed write replays
# UNGATED on every page load (outside any step's gate). It must be left unattributed -> `record` refuses.
def _serve_load_armed_write(counter: dict):
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
                self._send(
                    "<h1>Dashboard</h1><button type=button id='next'>Next</button><div id=out></div>"
                    "<script>"
                    " setTimeout(function(){ fetch('/save',{method:'POST'}).then(r=>r.text())"        # armed on LOAD
                    "   .then(t=>{ document.getElementById('out').textContent=t; }); }, 120);"
                    " document.getElementById('next').addEventListener('click', function(){});"       # benign
                    "</script>")
            else:
                self._send("not found", 404)

        def do_POST(self) -> None:  # noqa: N802
            counter["saves"] = counter.get("saves", 0) + 1
            self._send("LOAD-SAVED")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_record_write_load_armed_write_with_single_commit_is_refused(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_load_armed_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="loadarmed", start_url=f"{base}/", goal="open the dashboard",
                        mutate=MutateSpec(confirm_text_contains="LOAD-SAVED"))

        async def _demo(page) -> None:
            await page.get_by_role("button", name="Next").click()    # one benign, non-writing commit
            await page.get_by_text("LOAD-SAVED").wait_for()          # the load-armed POST lands during the demo

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        # The load-armed write must NOT be attributed to the sole benign click (that would cache it ungated).
        assert res.cached is False and "single" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None
        assert counter["saves"] == 1                                  # demo only; not cached -> never re-fired
    finally:
        httpd.shutdown()
        httpd.server_close()


# sendBeacon attribution: a click whose handler calls navigator.sendBeacon (the entry point Playwright surfaces
# inconsistently — caught ONLY via the init-script marker, not page.on("request")). The beacon fires inside the
# click's synchronous turn, so __uclast attributes it to that click, which is gated; under section drift the
# gate refuses BEFORE the click, so the beacon is never re-fired.
def _serve_beacon_write(counter: dict, drift: bool = False):
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
                extra = "<button type=button>noise</button>" if (drift and counter["gets"] > 1) else ""
                self._send(
                    f"<h1>Survey</h1><section id='sec'>{extra}"
                    "<button type=button id='track'>Track</button></section><div id=out></div>"
                    "<script>document.getElementById('track').addEventListener('click', function(){"
                    "  document.getElementById('out').textContent='BEACON-SENT';"  # confirm set synchronously
                    "  navigator.sendBeacon('/save'); });</script>")
            else:
                self._send("not found", 404)

        def do_POST(self) -> None:  # noqa: N802
            counter["saves"] = counter.get("saves", 0) + 1
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self._send("ok")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _beacon_demo(page) -> None:
    await page.get_by_role("button", name="Track").click()
    await page.get_by_text("BEACON-SENT").wait_for()


async def test_record_write_gates_a_sendbeacon(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_beacon_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="beacon", start_url=f"{base}/", goal="record the survey",
                        mutate=MutateSpec(confirm_text_contains="BEACON-SENT"))
        res = await record(spec, demo=_beacon_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True
        # The sendBeacon write is attributed to its click via the init-script marker and gated.
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        writes = [s for s in flow.steps if s.mutating]
        assert len(writes) == 1 and writes[0].action == "click" and writes[0].precond_scope
        approve(spec, cache=cache)
        assert await replay(spec, cache=cache) == {"status": "confirmed", "data": None}
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_record_write_sendbeacon_refuses_under_drift(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_beacon_write(counter, drift=True)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="beacondrift", start_url=f"{base}/", goal="record the survey",
                        mutate=MutateSpec(confirm_text_contains="BEACON-SENT"))
        res = await record(spec, demo=_beacon_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True
        assert counter["saves"] == 1
        approve(spec, cache=cache)
        with pytest.raises(FlowReplayError):                          # the section drifted -> gate refuses
            await replay(spec, cache=cache)
        assert counter["saves"] == 1                                  # the beacon was NOT re-fired
    finally:
        httpd.shutdown()
        httpd.server_close()


# XHR-driven write: a click whose handler issues XMLHttpRequest.open('POST')+send(). The send fires inside the
# click's synchronous turn -> attributed to the click -> gated, idempotency-keyed, no double-submit on replay.
def _serve_xhr_write(counter: dict):
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
                self._send(
                    "<h1>Form</h1><section id='sec'><button type=button id='go'>Send</button></section>"
                    "<div id=out></div>"
                    "<script>document.getElementById('go').addEventListener('click', function(){"
                    "  var x=new XMLHttpRequest(); x.open('POST','/save');"
                    "  x.onload=function(){ document.getElementById('out').textContent=x.responseText; };"
                    "  x.send(); });</script>")
            else:
                self._send("not found", 404)

        def do_POST(self) -> None:  # noqa: N802
            counter["saves"] = counter.get("saves", 0) + 1
            counter["idem"] = self.headers.get("Idempotency-Key")
            self._send("XHR-DONE")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_record_write_gates_an_xhr_post(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_xhr_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="xhr", start_url=f"{base}/", goal="send via xhr",
                        mutate=MutateSpec(confirm_text_contains="XHR-DONE"))

        async def _demo(page) -> None:
            await page.get_by_role("button", name="Send").click()
            await page.get_by_text("XHR-DONE").wait_for()

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True
        assert counter["saves"] == 1
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        writes = [s for s in flow.steps if s.mutating]
        assert len(writes) == 1 and writes[0].action == "click" and writes[0].precond_scope
        approve(spec, cache=cache)
        assert await replay(spec, cache=cache) == {"status": "confirmed", "data": None}
        assert counter["saves"] == 2                                  # exactly one more — gated, no double-submit
    finally:
        httpd.shutdown()
        httpd.server_close()


# TWO formless writes in ONE demo, each gated on its OWN commit with a DISTINCT precondition — the per-write
# generalization of the masking fix (write A's gate cannot absorb write B's).
def _serve_two_formless_writes(counter: dict):
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
                self._send(
                    "<h1>Two</h1>"
                    "<section id='secA'><button type=button id='a'>Alpha</button></section>"
                    "<section id='secB'><button type=button id='b'>Beta</button></section><div id=out></div>"
                    "<script>"
                    " document.getElementById('a').addEventListener('click', function(){"
                    "   fetch('/a',{method:'POST'}).then(r=>r.text()).then(t=>{document.getElementById('out').textContent=t;}); });"
                    " document.getElementById('b').addEventListener('click', function(){"
                    "   fetch('/b',{method:'POST'}).then(r=>r.text()).then(t=>{document.getElementById('out').textContent=t;}); });"
                    "</script>")
            else:
                self._send("not found", 404)

        def do_POST(self) -> None:  # noqa: N802
            p = self.path.split("?")[0]
            if p == "/a":
                counter["a"] = counter.get("a", 0) + 1
                self._send("A-OK")
            elif p == "/b":
                counter["b"] = counter.get("b", 0) + 1
                self._send("B-OK")
            else:
                self._send("not found", 404)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_record_write_two_formless_writes_each_gated_independently(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_two_formless_writes(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="twowrites", start_url=f"{base}/", goal="alpha then beta",
                        mutate=MutateSpec(confirm_text_contains="B-OK"))

        async def _demo(page) -> None:
            await page.get_by_role("button", name="Alpha").click()
            await page.get_by_text("A-OK").wait_for()
            await page.get_by_role("button", name="Beta").click()
            await page.get_by_text("B-OK").wait_for()

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True
        assert counter["a"] == 1 and counter["b"] == 1
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        writes = [s for s in flow.steps if s.mutating]
        # BOTH clicks are gated, EACH on its own commit — and their preconditions DIFFER (distinct sections),
        # so neither write's gate absorbed the other's.
        assert len(writes) == 2 and all(w.action == "click" and w.precond_scope for w in writes)
        names = sorted(w.locator.name for w in writes if w.locator)
        assert names == ["Alpha", "Beta"]
        assert writes[0].precond_scope != writes[1].precond_scope
    finally:
        httpd.shutdown()
        httpd.server_close()


# MASKING GUARD (from the describe-reuse work): a benign GET-form submit is classified mutating (via the
# override) but fires NO POST, so it must NOT mask a separate formless POST. Per-write attribution gates the
# formless POST on its own marker; the GET-form submit is gated independently by the override. Both stay gated.
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
        # THE MASKING HOLE, CLOSED: the formless POST "Save" is a GATED mutating step on its own marker — the
        # benign GET-form "Go" did NOT mask it.
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        save = [s for s in flow.steps if s.action == "click" and s.locator and s.locator.name == "Save"]
        assert len(save) == 1 and save[0].mutating and save[0].precond_scope
    finally:
        httpd.shutdown()
        httpd.server_close()


# TYPE-driven autosave -> FAIL LOUD (supersedes the prior gate-all behaviour). An input autosaves (fires a POST
# on `input`) and the demo also has a benign button click. A `type` is NOT a commit for per-write attribution
# (COMMIT = click/press/select), so the autosave POST fires in a turn with no commit (__ucturn===0) -> it is
# DEFERRED -> unattributed -> the flow is REFUSED. (The prior gate-all fallback OVER-GATED here — gating the
# benign click + the type — but that same over-gating fails OPEN on a load-armed write; per-write attribution
# refuses the ambiguous case instead. Re-record so the write fires directly from a single action.)
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
            self._send(
                "<h1>Profile</h1><button id=tab>Details</button>"
                "<input id=name aria-label='name'><div id=out></div>"
                "<script>document.getElementById('tab').addEventListener('click',function(){});"  # benign
                "document.getElementById('name').addEventListener('input',function(){"
                " fetch('/save',{method:'POST'}).then(r=>r.text()).then(t=>{"
                " document.getElementById('out').textContent=t;});});</script>")

        def do_POST(self) -> None:  # noqa: N802
            counter["saves"] = counter.get("saves", 0) + 1
            self._send("Saved")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _type_autosave_demo(page) -> None:
    await page.get_by_role("button", name="Details").click()   # a BENIGN scoped click (no write)
    await page.fill("#name", "Ada")                            # input -> autosave POST (the deferred write)
    await page.get_by_text("Saved").wait_for()


async def test_record_write_flow_type_autosave_is_refused(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve_type_autosave_write(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="autosave", start_url=f"{base}/", goal="save the name",
                        mutate=MutateSpec(confirm_text_contains="Saved"))
        res = await record(spec, demo=_type_autosave_demo, headless=True, cache=cache)
        # The autosave POST fires in a commitless turn (a `type` isn't a commit) -> unattributed -> REFUSED,
        # never gating the benign Details click or caching the write ungated.
        assert res.cached is False and "single" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None
        assert counter["saves"] == 1                            # demo only; not cached -> never re-fired
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
