"""RECOVERY must never write, never choose a value, and a confirm must be a TRANSITION (A6 / A7 / A8).

Three findings about what happens when replay tries to REPAIR itself, plus the barrier that is supposed to
catch a write that never landed.

A6 — self-heal could bind a write and persist it as a read. `_maybe_heal` guarded on the RECORDED
`step.mutating` flag only; the action the model proposed was never classified. So a drifted link could be
re-grounded onto a submit button: the POST fires, the resulting navigation makes `state_changed` trivially
true so the heal is judged "good", and the submit button is cached with `mutating=False` — after which every
0-LLM replay re-fires it ungated and un-deduped. The sibling suffix-replan path guards this exact risk
TWICE (`block_mutations` pre-act, and a wire watcher post-hoc). The heal needs BOTH: the classifier alone
cannot see a formless `fetch(..., {method:'POST'})` behind a bland name.

A7 — a healed `type` step typed the MODEL's value while the sibling write step still minted its
Idempotency-Key from the CALLER's params. The POST carried the model's body under row 500's key, so a later
legitimate replay of row 500 minted that same key and was deduped away by the backend: a silently wrong
write AND a silently suppressed one. It also poisoned the cache, rewriting the slot site's frozen literal
to the model's guess for every later no-params run.

A8 — the whole-flow confirm was a bare post-hoc check. `step_confirms` is optional, so for a single-write
flow it is the ONLY barrier. A JS-only regression that stops the POST leaves the DOM unchanged (the
mutation gate passes) and a persistent banner from a previous order satisfies it, so an un-landed write read
as `{"status": "confirmed"}` — and under `run_batch(resume=...)` was written to the ledger as committed and
permanently skipped, against `ledger.py`'s stated invariant.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

from ultracua.browser import BrowserSession
from ultracua.cache import CachedStep, FlowCache, flow_key
from ultracua.flow import _maybe_heal, run_cached
from ultracua.flows import FlowSpec, FlowReplayError, MutateSpec, approve, record, replay, run_batch
from ultracua.locators import LocatorSpec
from ultracua.providers.scripted import ScriptedProvider
from ultracua.timing import StepTrace


class _Hits:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []   # (path, Idempotency-Key, body)


def _serve(hits: _Hits, page_fn):
    """`page_fn()` returns the HTML for GET / on each request, so a fixture can change between runs."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            self._send(page_fn() if path == "/" else f"<h1>{path}</h1>")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode() if length else ""
            hits.writes.append((self.path.split("?")[0],
                                self.headers.get("Idempotency-Key") or "", body))
            self._send("<h1>Order placed</h1><p>Confirmation #999</p>")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# ==================== A6. the heal must never bind a write ====================

_FORM_PAGE = ("<h1>Reports</h1>"
              "<form method='post' action='/save'>"
              "<input name='qty' value='3'><button type='submit'>Place order</button></form>")

# The POST *and* a real DOM change, so `state_changed` is satisfied and the wire watcher is the only thing
# that can catch this. Without the DOM change the "heal had no effect" check would refuse it by accident —
# safe, but for the wrong reason, and silent about a write having left the browser.
_JS_PAGE = ("<h1>Reports</h1>"
            "<button id='go' type='button' onclick=\"fetch('/save', {method:'POST', body:'qty=3'});"
            "document.getElementById('out').textContent = 'Saved the order';\">Continue</button>"
            "<p id='out'></p>")


async def _heal_against(page_html: str, proposal: dict, hits: _Hits):
    """Drive the REAL `_maybe_heal` against a page, with a step whose recorded locator is dead."""
    httpd, base = _serve(hits, lambda: page_html)
    session = await BrowserSession(headless=True).start()
    try:
        await session.goto(base + "/")
        step = CachedStep(intent="open the daily report", action="click",
                          locator=LocatorSpec(role="link", name="Daily report", tag="a"),
                          mutating=False)
        ok, note, did_heal = await _maybe_heal(
            session, step, ScriptedProvider([proposal]), StepTrace(index=0), "open the daily report",
            "locator unresolved or ambiguous (drift)")
        return ok, note, did_heal, step
    finally:
        await session.close()
        httpd.shutdown()
        httpd.server_close()


async def test_the_heal_refuses_a_proposal_the_classifier_can_see_is_a_write() -> None:
    """Guard 1, pre-act: a form-submit control is refused BEFORE it is clicked, so nothing fires at all."""
    hits = _Hits()
    ok, note, did_heal, step = await _heal_against(
        _FORM_PAGE,
        {"action": "click", "role": "button", "name": "Place order", "intent": "open the daily report"},
        hits)
    assert ok is False
    assert "MUTATING" in note
    assert did_heal is False                       # not counted as a heal — it never ran
    assert hits.writes == []                       # refused pre-act: the POST never fired
    assert step.locator.name == "Daily report"     # the cached recipe is untouched


async def test_the_heal_refuses_to_persist_a_proposal_that_wrote_on_the_wire() -> None:
    """Guard 2, post-act: a formless `fetch(..., {method:'POST'})` behind a bland name is invisible to
    `classify_mutation` — this is the case that makes guard 1 alone insufficient. The write cannot be
    un-fired, but persisting it as a non-mutating step is what turns one accident into an ungated write on
    every future replay, and that IS preventable."""
    hits = _Hits()
    ok, note, _did_heal, step = await _heal_against(
        _JS_PAGE,
        {"action": "click", "role": "button", "name": "Continue", "intent": "open the daily report"},
        hits)
    assert hits.writes, "the fixture did not POST; this test would prove nothing"
    assert ok is False
    assert "WRITE on the wire" in note
    assert step.locator.name == "Daily report"     # NOT re-pointed at the write control


async def test_a_legitimate_read_heal_still_works() -> None:
    """The control: neither guard may fire on an ordinary locator repair, or self-heal is dead."""
    hits = _Hits()
    ok, note, did_heal, step = await _heal_against(
        "<h1>Reports</h1><a href='/report'>Open the report</a>",
        {"action": "click", "role": "link", "name": "Open the report", "intent": "open the daily report"},
        hits)
    assert ok is True and did_heal is True, note
    assert step.locator.name == "Open the report"  # the repair WAS persisted
    assert hits.writes == []


async def test_an_unapproved_read_flow_never_accumulates_an_ungated_write(tmp_path: Path) -> None:
    """End to end through the public `run_cached`, in the configuration the docs recommend for unattended
    self-heal: an UNAPPROVED read flow in auto mode. Across a learn and two replays, the write control must
    never enter the recipe and the server must never see a POST."""
    hits = _Hits()
    page = {"html": "<h1>Reports</h1><a href='/report'>Daily report</a>"}
    httpd, base = _serve(hits, lambda: page["html"])
    cache = FlowCache(root=tmp_path / "c")
    goal = "open the daily report"
    try:
        assert (await run_cached(f"{base}/", goal,
                                 ScriptedProvider([{"action": "click", "role": "link",
                                                    "name": "Daily report", "intent": goal},
                                                   {"action": "done", "intent": "done"}]),
                                 cache, mode="learn", headless=True)).success
        # The link is GONE and a submit button stands where it was — the drifted page that tempts the heal.
        page["html"] = _FORM_PAGE
        heal = ScriptedProvider([{"action": "click", "role": "button", "name": "Place order",
                                  "intent": goal}] * 4)
        for _ in range(2):
            await run_cached(f"{base}/", goal, heal, cache, mode="repair", headless=True)

        assert hits.writes == [], f"a repair fired a write: {hits.writes}"
        flow = cache.get(flow_key(goal, f"{base}/"))
        assert flow.steps[0].locator.name == "Daily report"
        assert flow.steps[0].mutating is False
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== A7. a healed value is the caller's, never the model's ====================

# The picker sits OUTSIDE the form on purpose: inside it, drifting the picker would change the form's scope
# fingerprint and the mutation gate would refuse the write — the test would then pass for the wrong reason.
def _checkout(picker_label: str, picker_id: str) -> str:
    return (f"<h1>Checkout</h1>"
            f"<section id='picker'><label for='{picker_id}'>{picker_label}</label>"
            f"<input id='{picker_id}'></section>"
            f"<form method='post' action='/order'>"
            f"<input type='hidden' id='qty' name='qty' value='1'>"
            f"<button type='submit'>Place the order</button></form>"
            f"<script>document.getElementById('{picker_id}').addEventListener('input', function (e) {{"
            f"  document.getElementById('qty').value = e.target.value; }});</script>")


_PRISTINE = _checkout("quantity", "q")
_DRIFTED = _checkout("amount", "amount")          # the picker is renamed -> the type step's locator dies


async def _recorded_checkout(tmp_path: Path, hits: _Hits, base: str):
    from ultracua.flows import SlotSpec

    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name="chk", start_url=f"{base}/", goal="place the order", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Order placed"),
                    slots={"qty": SlotSpec(type="string", pattern="[0-9]{1,3}")})

    async def _demo(page) -> None:
        await page.get_by_label("quantity").fill("7")
        await page.get_by_label("quantity").blur()
        await page.get_by_role("button", name="Place the order").click()
        await page.wait_for_selector("text=Order placed")

    res = await record(spec, demo=_demo, headless=True, cache=cache)
    assert res.cached, res.note
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    flow = cache.get(key)
    # Write-slot auto-mining is refused by design, so bind the slot by hand — the supported route is
    # `record(..., writable_slots=["qty"])`, which lands in the same place.
    typed = [i for i, s in enumerate(flow.steps) if s.action == "type"]
    assert typed, [s.action for s in flow.steps]
    flow.steps[typed[0]].slot = "qty"
    cache.put(flow)
    hits.writes.clear()
    return cache, spec, key


async def test_a_healed_slot_step_types_the_callers_row_not_the_models(tmp_path: Path) -> None:
    hits = _Hits()
    page = {"html": _PRISTINE}
    httpd, base = _serve(hits, lambda: page["html"])
    try:
        cache, spec, key = await _recorded_checkout(tmp_path, hits, base)
        page["html"] = _DRIFTED                    # the picker drifts; the write's own form does not

        await run_cached(url=spec.start_url, goal=spec.goal,
                         provider=ScriptedProvider([{"action": "type", "role": "textbox",
                                                     "name": "amount", "text": "3",
                                                     "intent": "set the amount"}]),
                         cache=cache, mode="repair", headless=True, scope=spec.scope,
                         params={"qty": "500"})

        # The model proposed "3". The caller said 500. If a write went out, it carries 500 — the body and
        # the Idempotency-Key must describe the SAME row.
        for _path, _key, body in hits.writes:
            assert "qty=500" in body, f"the wire carried the model's value: {body!r}"
        # ...and the frozen literal is NOT overwritten, so a later no-params run still submits what was
        # demonstrated rather than a model guess.
        assert cache.get(key).steps[[i for i, s in enumerate(cache.get(key).steps)
                                     if s.action == "type"][0]].text == "7"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_the_suffix_replan_is_refused_when_the_caller_bound_a_row(tmp_path: Path) -> None:
    """A7's sibling. `_author_steps` re-authors the tail from `goal` alone, which never mentions the row —
    so fixing only the heal moves the wrongness one branch over. `flows.replay` already refused
    params + on_drift='relearn'; that guard lived in the wrapper, never in the mechanism."""
    hits = _Hits()
    page = {"html": _PRISTINE}
    httpd, base = _serve(hits, lambda: page["html"])
    try:
        cache, spec, _key = await _recorded_checkout(tmp_path, hits, base)
        page["html"] = _DRIFTED

        report = await run_cached(url=spec.start_url, goal=spec.goal,
                                  provider=ScriptedProvider([{"action": "give_up", "intent": "no"}] * 4),
                                  cache=cache, mode="repair", headless=True, scope=spec.scope,
                                  params={"qty": "500"})
        assert report.success is False
        assert not any(t.meta.get("phase") == "replan" for t in report.traces)
        assert hits.writes == []
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== A8. the whole-flow confirm must be a TRANSITION ====================

def _serve_orders(hits: _Hits, state: dict):
    """A checkout whose "Order placed" banner is REAL history: it renders iff an order has actually been
    placed. That is what makes the stale-banner scenario honest rather than staged.

    `state["regressed"]` injects a JS-only regression — a `submit` listener that `preventDefault()`s. The
    form's interactables are byte-identical either way, so `scope_fingerprint` sees no drift and the
    mutation gate cannot be what catches this. The confirm is the only barrier, which is the point."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] != "/":
                self._send("<h1>ok</h1>")
                return
            banner = ("<div id='banner'>Order placed - thank you</div>"
                      if hits.writes and not state.get("no_banner") else "")
            block = ("<script>document.getElementById('f').addEventListener("
                     "'submit', function (e) { e.preventDefault(); });</script>"
                     if state.get("regressed") else "")
            self._send(f"{banner}<form id='f' method='post' action='/order'>"
                       f"<button type='submit'>Send the order</button></form>{block}")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            hits.writes.append((self.path, self.headers.get("Idempotency-Key") or "", ""))
            self._send("<div id='banner'>Order placed - thank you</div><h1>Done</h1>")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _send_order(page) -> None:
    await page.get_by_role("button", name="Send the order").click()


async def _recorded_order_flow(tmp_path: Path, base: str, name: str):
    """Record against a clean entry page (no order yet -> no banner), which is the LEGITIMATE case: the
    confirm is absent before and present after. The banner then persists for every later run."""
    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name=name, start_url=f"{base}/", goal="send the order", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Order placed"))
    res = await record(spec, demo=_send_order, headless=True, cache=cache)
    assert res.cached, res.note
    approve(spec, cache=cache)
    return cache, spec


async def test_recording_refuses_a_confirm_that_was_already_true_before_the_demo(tmp_path: Path) -> None:
    """The authoring-time cure — caught while the human is still watching, not on some later replay."""
    hits, state = _Hits(), {}
    httpd, base = _serve_orders(hits, state)
    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name="stale-author", start_url=f"{base}/", goal="send the order", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Order placed"))
    try:
        hits.writes.append(("/order", "", ""))     # a PREVIOUS order already left its banner
        res = await record(spec, demo=_send_order, headless=True, cache=cache)
        assert res.cached is False
        assert "already true" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_write_that_never_fires_is_not_confirmed_by_a_previous_orders_banner(tmp_path: Path) -> None:
    hits, state = _Hits(), {}
    httpd, base = _serve_orders(hits, state)
    try:
        cache, spec = await _recorded_order_flow(tmp_path, base, "stale")
        assert len(hits.writes) == 1               # the demo really placed one
        state["regressed"] = True                  # the POST stops firing; the DOM does not change
        with pytest.raises(FlowReplayError) as ei:
            await replay(spec, cache=cache)
        assert "already" in str(ei.value).lower()
        assert len(hits.writes) == 1               # ...and nothing was committed
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_write_that_does_fire_still_confirms(tmp_path: Path) -> None:
    """The control. The transition requirement must not break a legitimate flow — here the banner is
    suppressed on the entry page (absent BEFORE) and the write's own response carries it (present AFTER)."""
    hits, state = _Hits(), {}
    httpd, base = _serve_orders(hits, state)
    try:
        cache, spec = await _recorded_order_flow(tmp_path, base, "healthy")
        state["no_banner"] = True                  # a clean entry page on every later GET
        assert await replay(spec, cache=cache) == {"status": "confirmed", "data": None}
        assert len(hits.writes) == 2               # exactly one more write — no double-submit
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_the_resume_ledger_never_records_an_unlanded_write(tmp_path: Path) -> None:
    """`ledger.py`: "never a false skip of an un-landed write". A row confirmed by a stale banner was
    written to the ledger as committed and then permanently skipped on every resume."""
    hits, state = _Hits(), {}
    httpd, base = _serve_orders(hits, state)
    try:
        cache, spec = await _recorded_order_flow(tmp_path, base, "ledger")
        state["regressed"] = True
        before = len(hits.writes)

        out = await run_batch(spec, [{}], max_rows=1, resume="job1", cache=cache)
        assert out.rows[0].status != "ok", out.rows[0]
        assert len(hits.writes) == before          # nothing landed
        lines = [json.loads(ln)
                 for p in (Path(cache.root) / "ledgers").glob("*.jsonl")
                 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert not [ln for ln in lines if ln.get("kind") == "commit"], lines

        out2 = await run_batch(spec, [{}], max_rows=1, resume="job1", cache=cache)
        assert out2.rows[0].status != "resumed"    # retried, not permanently skipped
    finally:
        httpd.shutdown()
        httpd.server_close()
