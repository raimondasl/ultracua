"""Core evals: shipped WRITE safety — key-less, local fixtures + real headless Chromium.

The write-safety inviolables under test, with the SERVER as the oracle (what the fixture
actually RECEIVED — `fx.writes` / a module-local header-recording handler — never what the
client believes it sent):
- a learned form-submit write reaches the server EXACTLY ONCE per learn and once per replay,
  and the replayed write carries an Idempotency-Key so a server-side dedupe is possible;
- the mutation gate refuses a drifted form LOUD — no second write leaves the browser;
- a multi-write flow STOPS at the per-write completion barrier (Phase G) instead of silently
  reporting success when a write's confirm never appears;
- a declared idempotency precheck (MutateSpec.precheck_*) skips the write entirely when the
  end-state is already present;
- the idempotency-key BASIS: today (scope, step, intent) — a payload-aware basis (so two
  DIFFERENT payloads never share a dedupe key) is probed aspirationally.

evals/fixtures.py's Fixture records method/path/body but NOT request headers; per the eval
ground rules we extend locally instead of modifying it, so the Idempotency-Key checks use a
tiny module-local handler of the same dict-backed shape.
"""

from __future__ import annotations

import http.server
import threading

from evals.core import Ctx, expect, fail, missing, ok, probe, scenario
from evals.fixtures import Fixture, page


# --- scripted pieces (key-less: no LLM, no network beyond localhost) ---------------------------
def _mock_router():
    """A Router backed by MockClient: `learn()` builds a REAL provider router when none is passed
    (which needs an API key), so a scripted one is always injected. The write flows here extract
    nothing, so the canned response is never even consulted."""
    from ultracua.llm.base import Router, Tier
    from ultracua.llm.mock import MockClient

    mc = MockClient(actions=[{"found": True, "data": None}], tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


class _PlaceOrder:
    """Scripted key-less 'agent' for a write flow: click the order submit button once, then done
    (the confirmation page has no matching button, so the second decide() falls through to done)."""

    def __init__(self) -> None:
        self._clicked = False

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        if not self._clicked:
            for el in obs.elements:
                if el.role == "button" and "order" in (el.name or "").lower():
                    self._clicked = True
                    return Action(action="click", intent="place the order", ref=el.ref), None
        return Action(action="done", intent="done"), None


# The checkout shape shared by the single-write scenarios: a REAL method=post form (so the click
# is classified mutating by the form's METHOD — the structural signal — not by intent keywords)
# whose POST lands in the fixture where the checks can count it.
_CHECKOUT = page('<h1>Checkout</h1><p>cart: 1 widget, not ordered yet</p>'
                 '<form method="post" action="/order">'
                 '<input type="hidden" name="qty" value="1">'
                 '<button type="submit">Place the order</button></form>')
_CONFIRM = page("<h1>Order placed</h1><p>Confirmation #777</p>", title="confirm")
# The drifted checkout: the form GAINED a field and a second submit button, so the enclosing-form
# fingerprint the mutation gate captured at learn no longer matches (the gate scopes its
# precondition to the write target's form/section — see flow._replay_step).
_DRIFTED_CHECKOUT = page('<h1>Checkout</h1><p>cart: 1 widget, not ordered yet</p>'
                         '<form method="post" action="/order">'
                         '<input name="coupon" placeholder="coupon code">'
                         '<button type="submit">Apply coupon</button>'
                         '<input type="hidden" name="qty" value="1">'
                         '<button type="submit">Place the order</button></form>')


def _serve_order_form(writes: list) -> tuple:
    """Fixture-shaped local server that ALSO records each write's Idempotency-Key header.
    GET /checkout -> the form page; POST /order -> recorded (method/path/body/idem), then the
    classic 303 -> /confirm ('Order placed') form-submit shape."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:  # keep eval output clean
            pass

        def _send(self, body: str, code: int = 200) -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/checkout":
                self._send(_CHECKOUT)
            elif path == "/confirm":
                self._send(_CONFIRM)
            else:
                self._send("not found", 404)

        def do_POST(self) -> None:  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            writes.append({
                "method": self.command, "path": self.path.split("?")[0],
                "body": self.rfile.read(n).decode("utf-8", "replace"),
                "idem": self.headers.get("Idempotency-Key"),  # the dedupe key, if one was carried
            })
            self.send_response(303)
            self.send_header("Location", "/confirm")
            self.end_headers()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@scenario(
    id="core.writes.form_submit_once_with_key",
    title="a learned form-submit write reaches the server ONCE per learn and ONCE per replay, keyed",
    group="core", tags=("writes", "idempotency"),
)
async def form_submit_once_with_key(ctx: Ctx):
    from ultracua.flows import FlowSpec, MutateSpec, approve, learn, replay

    checks = []
    writes: list = []
    httpd, base = _serve_order_form(writes)
    try:
        cache = ctx.cache()
        spec = FlowSpec(name="order", start_url=f"{base}/checkout", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Order placed"), headless=True)
        res = await learn(spec, provider=_PlaceOrder(), router=_mock_router(), cache=cache)
        # `found` tracks the Phase-D action-completion check: the write CONFIRMED, not fire-and-hope
        checks.append(expect(res.cached and res.found, "learn caches the flow and confirms the write",
                             f"cached={res.cached} found={res.found}"))
        # write safety at learn time: the server saw EXACTLY ONE write (no stray re-fire, and
        # learn must not verify-by-replay a write — that would double-submit)
        checks.append(expect(len(writes) == 1 and writes[0]["path"] == "/order",
                             "exactly ONE write reached the server during learn",
                             f"writes={[(w['method'], w['path']) for w in writes]}"))
        # the submit step must be classified mutating — that is what routes it through the
        # mutation gate (+ idempotency key) on every future replay
        checks.append(expect(any(getattr(s, "mutating", False) for s in res.steps),
                             "the submit step is classified mutating (gate-routed)"))

        approve(spec, cache=cache)  # writes are approval-gated by default
        result = await replay(spec, cache=cache)
        checks.append(expect(isinstance(result, dict) and result.get("status") == "confirmed",
                             "replay reports the write CONFIRMED (action-completion verified)",
                             f"result={result!r}"))
        # the no-double-submit inviolable: exactly one MORE write on replay, same form payload
        # (a differing body would be silently-wrong data reaching the server)
        checks.append(expect(len(writes) == 2 and writes[1]["body"] == writes[0]["body"],
                             "exactly ONE write on replay, carrying the same payload as learned",
                             f"writes={[(w['method'], w['path'], w['body']) for w in writes]}"))
        # the replayed write must carry the Idempotency-Key the mutation gate mints — the header a
        # server-side dedupe keys on so a retry can never duplicate the side effect
        idem = writes[1]["idem"] if len(writes) > 1 else None
        checks.append(expect((idem or "").startswith("uca-"),
                             "the replayed write carried an Idempotency-Key (uca-...)",
                             f"idem={idem!r}"))
    finally:
        httpd.shutdown()
        httpd.server_close()
    return checks


@scenario(
    id="core.writes.drift_gate_refuses_refire",
    title="the mutation gate refuses a drifted form LOUD — no second write leaves the browser",
    group="core", tags=("writes", "fail-loud", "drift"),
)
async def drift_gate_refuses_refire(ctx: Ctx):
    from ultracua.flows import FlowReplayError, FlowSpec, MutateSpec, approve, health, learn, replay

    checks = []
    fx = Fixture({"/checkout": _CHECKOUT, "/confirm": _CONFIRM}, post_redirect="/confirm")
    with fx.serve() as base:
        cache = ctx.cache()
        spec = FlowSpec(name="driftorder", start_url=f"{base}/checkout", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Order placed"), headless=True)
        res = await learn(spec, provider=_PlaceOrder(), router=_mock_router(), cache=cache)
        checks.append(expect(res.cached and res.found and len(fx.writes) == 1,
                             "learn lands exactly one confirmed write",
                             f"cached={res.cached} found={res.found} writes={len(fx.writes)}"))
        approve(spec, cache=cache)

        # the form DRIFTS between learn and replay (new field + a second submit button): the
        # enclosing-form fingerprint no longer matches, so the gate must refuse — a blind re-fire
        # here could submit the WRONG thing (e.g. through the changed form)
        fx.pages["/checkout"] = _DRIFTED_CHECKOUT
        try:
            out = await replay(spec, cache=cache)
            checks.append(fail("replay fails LOUD under form drift", f"silently returned {out!r}"))
        except FlowReplayError:
            checks.append(ok("replay fails LOUD under form drift"))
        # the server-side truth: NO second write arrived — the refusal happened BEFORE actuation
        checks.append(expect(len(fx.writes) == 1, "NO second write reached the server under drift",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
        # the refused run is recorded, so fleet health (run-all/canary views) surfaces it
        checks.append(expect(health(spec, cache=cache).status == "failing",
                             "health records the refused run as failing",
                             f"status={health(spec, cache=cache).status}"))
    return checks


def _serve_wizard(counter: dict) -> tuple:
    """Two formless fetch-POST writes ('Submit step 1' -> POST /save1, 'Submit step 2' -> POST
    /save2), each showing its confirm text once its POST lands. On REPLAY (the 2nd page GET)
    write 2's confirm NEVER appears ('step 2 pending') — the per-write barrier must fail loud."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] != "/":
                self._send("nf")
                return
            counter["gets"] = counter.get("gets", 0) + 1
            t2 = "step 2 pending" if counter["gets"] > 1 else "step 2 saved"
            self._send(
                "<h1>Wizard</h1>"
                "<button id=w1>Submit step 1</button><button id=w2>Submit step 2</button>"
                "<div id=out></div>"
                "<script>"
                "document.getElementById('w1').addEventListener('click',function(){"
                " fetch('/save1',{method:'POST'}).then(function(){"
                "document.getElementById('out').textContent='step 1 saved';});});"
                "document.getElementById('w2').addEventListener('click',function(){"
                f" fetch('/save2',{{method:'POST'}}).then(function(){{"
                f"document.getElementById('out').textContent='{t2}';}});}});"
                "</script>")

        def do_POST(self) -> None:  # noqa: N802
            name = self.path.split("?")[0].lstrip("/")  # save1 | save2
            counter[name] = counter.get(name, 0) + 1
            self._send("ok")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@scenario(
    id="core.writes.multiwrite_barrier_fails_loud",
    title="a 2-write flow STOPS at the per-write barrier when write 2's confirm never appears",
    group="core", tags=("writes", "fail-loud", "multiwrite"),
)
async def multiwrite_barrier_fails_loud(ctx: Ctx):
    from ultracua.cache import StepConfirm, flow_key
    from ultracua.flows import FlowReplayError, FlowSpec, MutateSpec, approve, record, replay

    checks = []
    counter: dict = {}
    httpd, base = _serve_wizard(counter)
    try:
        cache = ctx.cache()
        spec = FlowSpec(
            name="wizard", start_url=f"{base}/", goal="do both steps",
            mutate=MutateSpec(
                confirm_text_contains="step 2 saved",  # the whole-flow (last-write) confirm, Phase D
                step_confirms=[                        # the per-write barriers, Phase G
                    StepConfirm(confirm_text_contains="step 1 saved",
                                expects_intent="Submit step 1", timeout_ms=2500),
                    StepConfirm(confirm_text_contains="step 2 saved",
                                expects_intent="Submit step 2", timeout_ms=2500),
                ]))

        async def _demo(pw_page) -> None:  # the scripted 'human' demonstration — keeps this key-less
            await pw_page.get_by_role("button", name="Submit step 1").click()
            await pw_page.get_by_text("step 1 saved").wait_for()
            await pw_page.get_by_role("button", name="Submit step 2").click()
            await pw_page.get_by_text("step 2 saved").wait_for()

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        # shipped building block (partial credit): the recorder captures BOTH declared writes and
        # attaches one barrier per write in commit order (count-checked, intent-anchored)
        checks.append(expect(res.cached and res.is_write, "record captures the declared 2-write flow",
                             f"cached={res.cached} is_write={res.is_write} note={res.note!r}"))
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        wsteps = [s for s in (flow.steps if flow else []) if s.mutating]
        checks.append(expect(len(wsteps) == 2 and all(s.confirm and s.confirm.has_confirm() for s in wsteps),
                             "both writes are gated with a per-write confirm attached",
                             f"gated_writes={len(wsteps)}"))

        approve(spec, cache=cache)
        # on replay write 2 actuates but its confirm never appears -> the barrier must FAIL LOUD;
        # returning 'confirmed' would be silently-wrong data about a landed transaction
        try:
            out = await replay(spec, cache=cache)
            checks.append(fail("replay fails LOUD when write 2 never confirms", f"returned {out!r}"))
        except FlowReplayError:
            checks.append(ok("replay fails LOUD when write 2 never confirms"))
        # no retry double-fire: each write hit the server ONCE in the demo + ONCE in the replay —
        # the failed barrier must not trigger any re-actuation of either write
        checks.append(expect(counter.get("save1") == 2 and counter.get("save2") == 2,
                             "each write reached the server exactly once per run (no double-fire)",
                             f"save1={counter.get('save1')} save2={counter.get('save2')}"))
    finally:
        httpd.shutdown()
        httpd.server_close()
    return checks


@scenario(
    id="core.writes.precheck_skips_when_done",
    title="the idempotency precheck skips the write when the end-state is already present",
    group="core", tags=("writes", "idempotency"),
)
async def precheck_skips_when_done(ctx: Ctx):
    from ultracua.flows import FlowSpec, MutateSpec, approve, health, learn, replay

    checks = []
    # /done is the standalone 'already ordered' end-state page the precheck pre-pass visits
    fx = Fixture({"/checkout": _CHECKOUT, "/confirm": _CONFIRM, "/done": _CONFIRM},
                 post_redirect="/confirm")
    with fx.serve() as base:
        cache = ctx.cache()
        spec = FlowSpec(
            name="idem", start_url=f"{base}/checkout", goal="place the order",
            mutate=MutateSpec(confirm_text_contains="Order placed",
                              precheck_url=f"{base}/done", precheck_text_contains="Order placed"),
            headless=True)
        res = await learn(spec, provider=_PlaceOrder(), router=_mock_router(), cache=cache)
        checks.append(expect(res.cached and res.found and len(fx.writes) == 1,
                             "learn lands exactly one confirmed write",
                             f"cached={res.cached} found={res.found} writes={len(fx.writes)}"))
        approve(spec, cache=cache)

        before = len(fx.writes)
        # the precheck sees 'Order placed' already on /done -> the one-shot write must be SKIPPED
        # and reported as such (never silently re-fired, never reported 'confirmed')
        result = await replay(spec, cache=cache)
        checks.append(expect(isinstance(result, dict) and result.get("status") == "already-done",
                             "replay reports already-done (skipped, not silently confirmed)",
                             f"result={result!r}"))
        # fx.writes is the server-side oracle: NO write arrived during the skipped replay
        checks.append(expect(len(fx.writes) == before, "NO write reached the server during the skip",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
        checks.append(expect(health(spec, cache=cache).status == "healthy",
                             "the skip counts as a healthy run (not a failure)",
                             f"status={health(spec, cache=cache).status}"))
    return checks


@scenario(
    id="core.writes.idem_key_payload_basis",
    title="idempotency key: stable (scope, step, intent) basis today; payload-aware basis probed",
    group="core", tags=("writes", "idempotency", "aspirational"),
)
async def idem_key_payload_basis(ctx: Ctx):
    from ultracua.safety import idempotency_key

    checks = []
    # TODAY's documented basis: (scope, step index, intent) -> a deterministic key, so a RETRY of
    # the same step replays with the SAME key and a server-side dedupe can drop the duplicate.
    k1 = idempotency_key("flow:order", 2, "place the order")
    k2 = idempotency_key("flow:order", 2, "place the order")
    checks.append(expect(k1 == k2 and k1.startswith("uca-"),
                         "same (scope, step, intent) -> the same stable uca- key", f"{k1} vs {k2}"))
    # ...and every component feeds the basis: a different step or intent must not collide (two
    # DIFFERENT writes sharing a key would let a server-side dedupe drop a legitimate write).
    k3 = idempotency_key("flow:order", 3, "place the order")
    k4 = idempotency_key("flow:order", 2, "cancel the order")
    checks.append(expect(len({k1, k3, k4}) == 3, "step index and intent both change the key",
                         f"{k1}, {k3}, {k4}"))

    # ASPIRATIONAL: a payload-aware basis. Today two runs of the same step with DIFFERENT form
    # payloads (qty=1 vs qty=2) mint the SAME key — a strict server-side dedupe would wrongly
    # drop the second, distinct write. The gap probed: a payload/slot-values input to the basis
    # (an unexpected-kwarg TypeError == not built yet, reported `missing`).
    st, out = await probe(idempotency_key, "flow:order", 2, "place the order", payload={"qty": 1})
    if st == "ok":
        st2, out2 = await probe(idempotency_key, "flow:order", 2, "place the order", payload={"qty": 2})
        checks.append(expect(st2 == "ok" and out != out2,
                             "payload kwarg distinguishes DIFFERENT payloads",
                             f"{out} vs {out2}", aspirational=True))
    else:
        checks.append(missing("payload kwarg distinguishes DIFFERENT payloads",
                              f"{type(out).__name__}: {out}"))
    st3, out3 = await probe(idempotency_key, "flow:order", 2, "place the order",
                            slot_values={"qty": "2"})
    if st3 == "ok":
        st4, out4 = await probe(idempotency_key, "flow:order", 2, "place the order",
                                slot_values={"qty": "3"})
        checks.append(expect(st4 == "ok" and out3 != out4,
                             "slot_values kwarg distinguishes DIFFERENT slot fills",
                             f"{out3} vs {out4}", aspirational=True))
    else:
        checks.append(missing("slot_values kwarg distinguishes DIFFERENT slot fills",
                              f"{type(out3).__name__}: {out3}"))
    return checks
