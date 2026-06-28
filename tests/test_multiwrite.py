"""Phase G — MULTI-WRITE transactions: the per-write completion BARRIER.

A flow performs TWO writes, each with its own action-completion check. Replay verifies each write the moment
it actuates — as an absent->present TRANSITION — and FAILS LOUD (never proceeding to the next write) if one
can't be confirmed. The "human" is a scripted sequence of real interactions, so the suite stays key-less; both
writes are formless fetch-POSTs, so the recorder's per-write attribution gates each, then the barrier verifies.

(Per-write one-shot RESUME — skip an already-landed write on a re-run — is a separate deferred slice; not here.)
"""

from __future__ import annotations

import http.server
import threading

import pytest

from ultracua.cache import FlowCache, flow_key, StepConfirm
from ultracua.flows import (
    FlowReplayError, FlowSpec, MutateSpec, _attach_step_confirms, approve, load_spec, record, replay, save_spec,
)


def _serve(counter: dict, *, drift: bool = False, shared_confirm: bool = False):
    """Two-write page: 'Submit step 1' POSTs /save1 then shows its confirm; 'Submit step 2' POSTs /save2 then
    shows its confirm. drift -> on REPLAY (2nd GET) step 1 shows 'step 1 pending' (its confirm never holds).
    shared_confirm -> both writes show the SAME 'Saved' text (so step 2's confirm is already true before it
    actuates — exercises the baseline/transition guard)."""

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
                self._send("nf")
                return
            counter["gets"] = counter.get("gets", 0) + 1
            replaying = counter["gets"] > 1
            t1 = "Saved" if shared_confirm else "step 1 saved"
            t2 = "Saved" if shared_confirm else "step 2 saved"
            if drift and replaying:
                t1 = "step 1 pending"
            self._send(
                "<h1>Wizard</h1>"
                "<button id=w1>Submit step 1</button><button id=w2>Submit step 2</button>"
                "<div id=out></div>"
                "<script>"
                "document.getElementById('w1').addEventListener('click',function(){"
                f" fetch('/save1',{{method:'POST'}}).then(function(){{document.getElementById('out').textContent='{t1}';}});}});"
                "document.getElementById('w2').addEventListener('click',function(){"
                f" fetch('/save2',{{method:'POST'}}).then(function(){{document.getElementById('out').textContent='{t2}';}});}});"
                "</script>")

        def do_POST(self) -> None:  # noqa: N802
            n = self.path.split("?")[0].lstrip("/")  # save1 | save2
            counter[n] = counter.get(n, 0) + 1
            counter[n + "_idem"] = self.headers.get("Idempotency-Key")
            self._send("ok")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _two_write_demo(page) -> None:
    await page.get_by_role("button", name="Submit step 1").click()
    await page.get_by_text("step 1 saved").wait_for()
    await page.get_by_role("button", name="Submit step 2").click()
    await page.get_by_text("step 2 saved").wait_for()


def _spec(base: str, *, step_confirms, name="mw"):
    # The WHOLE-FLOW confirm is the overall/last-write signal (Phase D); step_confirms add the per-write barrier.
    return FlowSpec(name=name, start_url=f"{base}/", goal="do both steps",
                    mutate=MutateSpec(confirm_text_contains="step 2 saved", step_confirms=step_confirms))


# distinct per-write confirms, each anchored to its write by expects_intent (required for >1 write).
_OK_CONFIRMS = [
    StepConfirm(confirm_text_contains="step 1 saved", expects_intent="Submit step 1"),
    StepConfirm(confirm_text_contains="step 2 saved", expects_intent="Submit step 2"),
]


async def test_multiwrite_barrier_both_confirm(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = _spec(base, step_confirms=list(_OK_CONFIRMS))
        res = await record(spec, demo=_two_write_demo, headless=True, cache=cache)
        assert res.is_write is True and res.cached is True
        assert counter["save1"] == 1 and counter["save2"] == 1            # each fired once during the demo
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        writes = [s for s in flow.steps if s.mutating]
        assert len(writes) == 2 and all(w.confirm and w.confirm.has_confirm() for w in writes)

        approve(spec, cache=cache)
        result = await replay(spec, cache=cache)
        assert result == {"status": "confirmed", "data": None}
        assert counter["save1"] == 2 and counter["save2"] == 2           # both writes replayed, in order
        assert (counter.get("save1_idem") or "").startswith("uca-")      # each carried an Idempotency-Key
        assert (counter.get("save2_idem") or "").startswith("uca-")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_multiwrite_barrier_stops_on_unconfirmed_write(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve(counter, drift=True)                            # on replay, step 1's confirm never holds
    try:
        cache = FlowCache(root=tmp_path)
        spec = _spec(base, step_confirms=list(_OK_CONFIRMS), name="mwdrift")
        res = await record(spec, demo=_two_write_demo, headless=True, cache=cache)
        assert res.cached is True
        assert counter["save1"] == 1 and counter["save2"] == 1
        approve(spec, cache=cache)
        with pytest.raises(FlowReplayError):                             # write 1's barrier fails
            await replay(spec, cache=cache)
        # THE BARRIER: write 2 is NEVER actuated after write 1's confirm fails (no silent run-past).
        assert counter["save2"] == 1                                     # still just the demo's fire
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_multiwrite_barrier_rejects_a_nonunique_confirm(tmp_path) -> None:
    # Both writes show the SAME "Saved" text, so step 2's confirm is ALREADY present before step 2 actuates.
    # The barrier requires an absent->present transition, so this is a false-PASS it must REJECT (fail loud)
    # rather than wave step 2 through on stale state from step 1.
    counter: dict = {}
    httpd, base = _serve(counter, shared_confirm=True)
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="mwshared", start_url=f"{base}/", goal="do both steps",
                        mutate=MutateSpec(confirm_text_contains="Saved", step_confirms=[
                            StepConfirm(confirm_text_contains="Saved", expects_intent="Submit step 1"),
                            StepConfirm(confirm_text_contains="Saved", expects_intent="Submit step 2"),
                        ]))

        async def _demo(page) -> None:
            await page.get_by_role("button", name="Submit step 1").click()
            await page.get_by_text("Saved").wait_for()
            await page.get_by_role("button", name="Submit step 2").click()

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        assert res.cached is True                                        # record doesn't run the barrier
        approve(spec, cache=cache)
        with pytest.raises(FlowReplayError):                            # step 2's confirm was already true -> reject
            await replay(spec, cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_multiwrite_attach_refuses_count_mismatch(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = _spec(base, step_confirms=[StepConfirm(confirm_text_contains="step 1 saved")],  # only 1 for 2 writes
                     name="mwcount")
        res = await record(spec, demo=_two_write_demo, headless=True, cache=cache)
        assert res.cached is False and "commit order" in res.note and "1:1" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None  # not kept
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_multiwrite_attach_requires_expects_intent(tmp_path) -> None:
    counter: dict = {}
    httpd, base = _serve(counter)
    try:
        cache = FlowCache(root=tmp_path)
        spec = _spec(base, step_confirms=[                              # right count, but no expects_intent
            StepConfirm(confirm_text_contains="step 1 saved"),
            StepConfirm(confirm_text_contains="step 2 saved"),
        ], name="mwnoanchor")
        res = await record(spec, demo=_two_write_demo, headless=True, cache=cache)
        assert res.cached is False and "expects_intent" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- _attach_step_confirms unit (no browser) -----------------------------------------------------------
def _flow(intents_mutating):
    from ultracua.cache import CachedFlow, CachedStep
    from ultracua.locators import LocatorSpec
    steps = []
    for intent, mut in intents_mutating:
        steps.append(CachedStep(intent=intent, action="click",
                                locator=LocatorSpec(role="button", name=intent, tag="button"),
                                mutating=mut, precond_scope=("s" if mut else "")))
    return CachedFlow(key="k", goal="g", start_url="u", steps=steps, created_ts=0.0)


def test_attach_binds_by_commit_order_and_validates() -> None:
    flow = _flow([("nav", False), ("place order", True), ("confirm email", True)])
    out, reason = _attach_step_confirms(flow, [
        StepConfirm(confirm_text_contains="Order placed", expects_intent="order"),
        StepConfirm(confirm_text_contains="Email sent", expects_intent="email"),
    ])
    assert reason == "" and out is not None
    writes = [s for s in out.steps if s.mutating]
    assert writes[0].confirm.confirm_text_contains == "Order placed"
    assert writes[1].confirm.confirm_text_contains == "Email sent"

    bad, why = _attach_step_confirms(flow, [StepConfirm(confirm_text_contains="x")])           # count
    assert bad is None and "1:1" in why
    bad2, why2 = _attach_step_confirms(flow, [                                                  # out of order
        StepConfirm(confirm_text_contains="Email sent", expects_intent="email"),
        StepConfirm(confirm_text_contains="Order placed", expects_intent="order"),
    ])
    assert bad2 is None and "expects_intent" in why2
    bad3, why3 = _attach_step_confirms(flow, [                                                  # missing anchor
        StepConfirm(confirm_text_contains="Order placed"),
        StepConfirm(confirm_text_contains="Email sent", expects_intent="email"),
    ])
    assert bad3 is None and "expects_intent" in why3
    bad4, why4 = _attach_step_confirms(flow, [StepConfirm(expects_intent="order"),              # no confirm_*
                                              StepConfirm(confirm_text_contains="x", expects_intent="email")])
    assert bad4 is None and "no confirm_* check" in why4


def test_single_write_attach_does_not_require_expects_intent() -> None:
    flow = _flow([("nav", False), ("place order", True)])
    out, reason = _attach_step_confirms(flow, [StepConfirm(confirm_text_contains="Order placed")])
    assert reason == "" and out is not None and [s for s in out.steps if s.mutating][0].confirm is not None


def test_is_multiwrite_and_spec_roundtrip(tmp_path, monkeypatch) -> None:
    assert MutateSpec(confirm_text_contains="a").is_multiwrite() is False
    assert MutateSpec(step_confirms=[StepConfirm(confirm_text_contains="a")]).is_multiwrite() is False
    assert MutateSpec(step_confirms=[StepConfirm(confirm_text_contains="a", expects_intent="a"),
                                     StepConfirm(confirm_text_contains="b", expects_intent="b")]
                      ).is_multiwrite() is True

    # H1: save_spec/load_spec round-trips StepConfirm objects (asdict won't serialize the pydantic models).
    monkeypatch.chdir(tmp_path)
    spec = _spec("http://x.test", step_confirms=list(_OK_CONFIRMS), name="rt")
    save_spec(spec)                                          # must not raise (was a TypeError)
    back = load_spec("rt")
    assert back.mutate.step_confirms[0].__class__ is StepConfirm
    assert back.mutate.step_confirms[0].confirm_text_contains == "step 1 saved"
    assert back.mutate.step_confirms[1].expects_intent == "Submit step 2"
    assert back.mutate.is_multiwrite() is True
