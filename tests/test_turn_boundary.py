"""R4.36: a write leaving inside a LATER, NON-COMMIT dispatch must not be credited to the last commit.

WHY THIS FILE EXISTS RATHER THAN A CELL IN `test_write_safety_invariants.py`. That matrix already
enumerates deferred writes, and every one of its REFUSED cells leaves the write in a BARE task — which
is why `inDispatch()` (R4.26's fix) closed them all, and why this shape walked straight through: here
the write leaves inside a dispatch, just not the commit's. The matrix gains a cell for the decision-point
state; this file reproduces the FIELD shape, which needs a scheduling construction a `(js, setup)` tuple
cannot express.

THE CONSTRUCTION, and it is deterministic — no artificial load, no retries. A commit opens a turn and the
turn is closed by `setTimeout(..., 0)`. The benign click's own handler busy-waits, so that reset is
OVERDUE by the time the handler returns; a real keystroke queued during the block is served ahead of the
overdue timer (Chromium prioritises input tasks, which is the same ordering R4.26 measured). The autosave
write therefore leaves inside the `input` dispatch while the click's turn is still open.

Measured against 0.104.0: 4/4 `cached=True`, with the recipe's ONLY step — the benign `Details` click —
carrying `mutating=True`, a `precond_scope`, and `mutating_sources=['wire']`. The wrong step gated as the
commit, which is R3.2's harm class, and the demonstrated write absent from the recipe entirely.

ONE KEYSTROKE IS LOAD-BEARING. With three, the second and third writes land after the reset, go
unattributed, and `record` refuses for a DIFFERENT write — the flow is refused, the misattribution is
still there, and the test would pass while proving nothing.
"""

from __future__ import annotations

import asyncio
import http.server
import threading

import pytest
from pathlib import Path

from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowSpec, MutateSpec, record

# The click is BENIGN — its handler only blocks. The write belongs to the field.
_BUSY_MS = 700
_TYPE_AFTER_MS = 150

_PAGE = """<h1>Profile</h1><button id=tab>Details</button>
<input id=name aria-label='name'><div id=out></div>
<script>
// Keep focus on the field: a click would otherwise focus the button and the keystroke would go there,
// which silently turns this fixture into one that exercises nothing.
document.getElementById('tab').addEventListener('mousedown', function (e) { e.preventDefault(); });
document.getElementById('tab').addEventListener('click', function () {
  var t0 = Date.now(); while (Date.now() - t0 < %d) {}
});
document.getElementById('name').addEventListener('input', function () {
  fetch('/save', {method: 'POST'}).then(function (r) { return r.text(); })
    .then(function (t) { document.getElementById('out').textContent = t; });
});
</script>""" % _BUSY_MS


def _serve(counter: dict, page: str = _PAGE):
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            self._send(page)

        def do_POST(self) -> None:  # noqa: N802
            counter["saves"] = counter.get("saves", 0) + 1
            self._send("Saved")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _demo(page) -> None:
    await page.focus("#name")

    async def _type_during_the_block():
        await asyncio.sleep(_TYPE_AFTER_MS / 1000)
        await page.keyboard.type("A")            # exactly ONE input event -> exactly ONE write

    typer = asyncio.create_task(_type_during_the_block())
    await page.get_by_role("button", name="Details").click()
    await typer
    await page.get_by_text("Saved").wait_for()


async def test_a_write_in_a_later_dispatch_is_not_credited_to_the_last_commit(tmp_path: Path) -> None:
    counter: dict = {}
    httpd, base = _serve(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="autosave", start_url=f"{base}/", goal="save the name",
                        mutate=MutateSpec(confirm_text_contains="Saved"))
        res = await record(spec, demo=_demo, headless=True, cache=cache)

        # THE PREMISE, pinned so the cell cannot go quietly vacuous: exactly one write was demonstrated.
        # If the fixture ever produces two, the flow refuses for the SECOND one and the assertion below
        # stops being about attribution at all.
        assert counter.get("saves") == 1, f"the shape changed: {counter} writes, expected exactly 1"

        assert res.cached is False, (
            "a write caused by typing was credited to the benign click and the flow cached: "
            f"note={res.note!r}")
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None
    finally:
        httpd.shutdown()
        httpd.server_close()


# ===================== R4.38: the same shape, laundered through `requestSubmit()` =====================
#
# `ACTIVATION_CAUSED` accepts `submit`/`reset`/`formdata` by TYPE NAME, with no check that the commit's
# activation had anything to do with the dispatch. `form.requestSubmit()` from an `input` handler — the
# mainstream Turbo/Rails/Stimulus autosave idiom — therefore puts the write inside a `submit` dispatch
# the whitelist accepts, and the write is credited to the benign click exactly as before.
#
# This is the SAME finding as the test above with one idiom changed, which is the whole point: the fix
# closed the direct-`fetch` shape and this one walks past it. Measured on 0.105.0 — `cached=True`, the
# recipe's only step the benign click with `mutating=True`, a `precond_scope` and
# `mutating_sources=['wire']`. It is NOT a regression: the predicate is strictly narrower than 0.104.0's
# (`NEW ⊆ OLD`), and this shape cached there too.

_WRITE_VIA_REQUEST_SUBMIT = """
document.getElementById('name').addEventListener('input', function () {
  document.getElementById('f').requestSubmit();
});
document.getElementById('f').addEventListener('submit', function (ev) {
  ev.preventDefault();
  fetch('/save', {method: 'POST'}).then(function (r) { return r.text(); })
    .then(function (t) { document.getElementById('out').textContent = t; });
});"""

_PAGE_SUBMIT = """<h1>Profile</h1><button id=tab>Details</button>
<form id=f><input id=name aria-label='name'></form><div id=out></div>
<script>
document.getElementById('tab').addEventListener('mousedown', function (e) { e.preventDefault(); });
document.getElementById('tab').addEventListener('click', function () {
  var t0 = Date.now(); while (Date.now() - t0 < %d) {}
});
%s
</script>""" % (_BUSY_MS, _WRITE_VIA_REQUEST_SUBMIT)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "R4.38 — OPEN. `ACTIVATION_CAUSED` matches the event's TYPE NAME with no provenance check, so a "
    "write laundered through a synthetic `submit`/`reset`/`formdata` dispatch is credited to the last "
    "commit — including from a bare task, the state R4.26's `inDispatch()` exists to refuse. Inviolable "
    "#3. Not a regression: 0.104.0 cached this too."))
async def test_a_write_laundered_through_requestsubmit_is_not_credited_either(tmp_path: Path) -> None:
    counter: dict = {}
    httpd, base = _serve(counter, page=_PAGE_SUBMIT)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="autosave-submit", start_url=f"{base}/", goal="save the name",
                        mutate=MutateSpec(confirm_text_contains="Saved"))
        res = await record(spec, demo=_demo, headless=True, cache=cache)
        assert counter.get("saves") == 1, f"the shape changed: {counter} writes, expected exactly 1"
        assert res.cached is False, (
            "a write caused by typing, laundered through requestSubmit(), was credited to the benign "
            f"click and the flow cached: note={res.note!r}")
    finally:
        httpd.shutdown()
        httpd.server_close()
