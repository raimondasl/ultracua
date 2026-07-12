"""H3 typed templates — parameterized replay (key-less, local fixtures).

Slice 1 (READ): records a read flow (types a value into an echoing input), marks that step as a slot,
then replays with params={...} and asserts the SUBSTITUTED value reached the live page — plus the
pre-flight validation, the idempotency slot channel, and slot serialization. Slices 1b/1c: recorder
slot auto-mining + the value-independence audit + site-metadata domain capture.

Slice 2a (WRITE): the parameterized-WRITE refusal is LIFTED — a write template runs each row through
one learned form-submit. The load-bearing safety artifact is the per-write `Idempotency-Key`: distinct
rows mint distinct keys (no suppressed write), a retry of one row mints the same key (no double-write).
A local `_CheckoutSite` records each POST's Idempotency-Key header + body (the double-write oracle), and
the tests assert the mutation gate, the confirm barrier, and the slot-schema approval gate all still fire.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from ultracua import flows
from ultracua.cache import FlowCache, flow_key
from ultracua.flows import (
    FlowReplayError,
    FlowSpec,
    MutateSpec,
    SlotSpec,
    validate_params,
)
from ultracua.safety import idempotency_key


class _EchoSite:
    """Serves one page whose text input echoes each value to the server via a SYNCHRONOUS GET
    (no async race with session close), so `.gets` is the oracle for what value reached the DOM."""

    def __init__(self) -> None:
        self.gets: list[str] = []

    def serve(self):
        site = self
        body = (
            "<!doctype html><html><body>"
            "<label for='q'>code</label><input id='q'>"
            "<script>document.getElementById('q').addEventListener('input', (e) => {"
            " const x = new XMLHttpRequest();"
            " x.open('GET', '/typed-' + encodeURIComponent(e.target.value), false); x.send();"
            "});</script></body></html>"
        )

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_GET(self) -> None:
                site.gets.append(self.path)
                b = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _record_slotted_read(site, base, cache, *, slot_name="code", enum=None):
    """Record a read flow that types 'alpha-7', mark its type step as `slot_name`, approve it."""
    spec = FlowSpec(name="tracking", start_url=base + "/", goal="enter the tracking code", headless=True,
                    slots={slot_name: SlotSpec(type="string", enum=enum)})

    async def _demo(pg) -> None:
        await pg.fill("#q", "alpha-7")
        await pg.locator("#q").blur()   # change fires on blur -> the `type` step is captured

    res = await flows.record(spec, demo=_demo, headless=True, cache=cache)
    assert res.cached, f"record didn't cache: {res.note!r}"
    # Mark the recorded type step as the slot site (slice 1's creation path is manual; slice 1b mines it).
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    flow = cache.get(key)
    typed = next(s for s in flow.steps if s.action == "type")
    typed.slot = slot_name
    cache.put(flow)
    flows.approve(spec, cache=cache)
    return spec


async def test_replay_substitutes_validated_param(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()
    httpd, base = site.serve()
    try:
        spec = await _record_slotted_read(site, base, cache, enum=["alpha-7", "beta-9"])

        # No params -> the FROZEN literal replays (backward compatible).
        site.gets.clear()
        await flows.replay(spec, params=None, cache=cache)
        assert "/typed-alpha-7" in site.gets and "/typed-beta-9" not in site.gets

        # params -> the SUBSTITUTED value reaches the live page (0-LLM), the frozen one does not.
        site.gets.clear()
        await flows.replay(spec, params={"code": "beta-9"}, cache=cache)
        assert "/typed-beta-9" in site.gets, f"substitution didn't reach the page: {site.gets}"
        assert "/typed-alpha-7" not in site.gets, "replayed the frozen literal instead of the param"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_preflight_rejects_out_of_domain_before_browser(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()
    httpd, base = site.serve()
    try:
        spec = await _record_slotted_read(site, base, cache, enum=["alpha-7", "beta-9"])
        site.gets.clear()
        # An out-of-enum value must fail loud BEFORE any page action (0-LLM pre-flight).
        with pytest.raises(FlowReplayError, match="one of"):
            await flows.replay(spec, params={"code": "gamma"}, cache=cache)
        assert site.gets == [], "pre-flight didn't refuse before touching the browser"
        # An unknown param name is refused too.
        with pytest.raises(FlowReplayError, match="unknown param"):
            await flows.replay(spec, params={"typo": "x"}, cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


class _CheckoutSite:
    """A `method=post` checkout form (the submit is mutating by METHOD) + a confirmation page. Records
    each write's (path, Idempotency-Key header, body) — the double-write / suppressed-write oracle: the
    SERVER's recorded header, never what the client believes it sent. `checkout_html`/`confirm_html` are
    swappable so a test can drift the form structure (mutation gate) or break the confirmation (barrier)."""

    CHECKOUT = ("<!doctype html><html><body><h1>Checkout</h1>"
                "<form method='post' action='/order'>"
                "<label for='qty'>quantity</label><input id='qty' name='qty' value='1'>"
                "<button type='submit'>Place the order</button></form></body></html>")
    CONFIRM_OK = "<!doctype html><html><body><h1>Order placed</h1></body></html>"
    CONFIRM_BAD = "<!doctype html><html><body><h1>Something went wrong</h1></body></html>"

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []  # (path, idempotency-key, body)
        self.checkout_html = self.CHECKOUT
        self.confirm_html = self.CONFIRM_OK

    def serve(self):
        site = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def _send(self, html: str) -> None:
                b = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                self._send(site.confirm_html if path == "/confirm" else site.checkout_html)

            def do_POST(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode("utf-8", "replace")
                site.writes.append((self.path.split("?")[0],
                                    self.headers.get("Idempotency-Key"), body))
                self.send_response(303)
                self.send_header("Location", "/confirm")
                self.end_headers()

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _record_write_flow(base, cache):
    """Record a WRITE flow that fills #qty + submits, mark the fill as the `qty` slot, approve it."""
    spec = FlowSpec(name="order", start_url=base + "/checkout", goal="place the order",
                    mutate=MutateSpec(confirm_text_contains="Order placed"),
                    slots={"qty": SlotSpec(type="string", pattern="[0-9]{1,3}")}, headless=True)

    async def _demo(pg) -> None:
        await pg.fill("#qty", "7")
        await pg.locator("#qty").blur()                       # change fires on blur -> a `type` step
        await pg.get_by_role("button", name="Place the order").click()
        await pg.get_by_text("Order placed").wait_for()

    res = await flows.record(spec, demo=_demo, headless=True, cache=cache)
    assert res.cached and res.is_write, f"record: cached={res.cached} is_write={res.is_write} note={res.note!r}"
    # Write-slot auto-mining is (correctly) refused, so mark the fill step as the `qty` slot explicitly.
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    flow = cache.get(key)
    for s in flow.steps:
        if s.action == "type":
            s.slot = "qty"
    cache.put(flow)
    flows.approve(spec, cache=cache)
    return spec


async def test_parameterized_write_runs_with_row_keyed_idempotency(tmp_path, monkeypatch) -> None:
    # H3 slice 2a: a parameterized WRITE flow RUNS (the slice-1 refusal is lifted). The substituted value
    # reaches the POST body, and each write folds the run's row into the Idempotency-Key: DISTINCT rows ->
    # DISTINCT keys (a backend dedupe can't silently drop rows 2..N), a retry of the SAME row -> the SAME
    # key (a retry dedupes instead of double-writing). Exactly one write reaches the server per replay.
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(base, cache)
        del site.writes[:]   # drop the demo's write; the replays are the oracle

        await flows.replay(spec, params={"qty": "9"}, cache=cache)
        await flows.replay(spec, params={"qty": "8"}, cache=cache)
        await flows.replay(spec, params={"qty": "9"}, cache=cache)   # a re-run of the SAME row

        paths = [p for p, _, _ in site.writes]
        keys = [k for _, k, _ in site.writes]
        bodies = [b for _, _, b in site.writes]
        # Exactly one POST per replay to /order — the mutation gate never double-fires a single-write flow.
        assert paths == ["/order", "/order", "/order"], site.writes
        # The SUBSTITUTED value actuated on the wire (not the frozen "7"): row 0/2 -> qty=9, row 1 -> qty=8.
        assert "qty=9" in bodies[0] and "qty=8" in bodies[1] and "qty=9" in bodies[2], bodies
        assert all(k and k.startswith("uca-") for k in keys), keys
        kA, kB, kA2 = keys
        assert kA != kB, f"distinct rows shared ONE Idempotency-Key (suppressed-write risk): {kA} vs {kB}"
        assert kA == kA2, f"a retry of the SAME row minted a NEW key (double-write risk): {kA} vs {kA2}"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_write_schema_change_refused_until_reapproved(tmp_path, monkeypatch) -> None:
    # H3 slice 2a stale-approval guard: widening a slot's domain AFTER approval must refuse replay until
    # re-approval — an approval must never authorize a WIDER contract than the human reviewed (worst on a
    # write). The refusal precedes actuation: ZERO extra writes reach the server while the approval is stale.
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(base, cache)   # approved with qty pattern [0-9]{1,3}
        del site.writes[:]
        await flows.replay(spec, params={"qty": "5"}, cache=cache)   # matching approval -> runs
        assert len(site.writes) == 1

        # Widen the domain in place (pattern -> any string). The bound approval is now STALE.
        spec.slots["qty"] = SlotSpec(type="string")
        with pytest.raises(FlowReplayError, match="schema changed since approval"):
            await flows.replay(spec, params={"qty": "9"}, cache=cache)
        assert len(site.writes) == 1, "a stale-approval write actuated — the schema gate must precede the write"

        # Re-approving under the new schema re-binds the hash; replay works again.
        flows.approve(spec, cache=cache)
        await flows.replay(spec, params={"qty": "12"}, cache=cache)
        assert len(site.writes) == 2
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_write_confirm_barrier_fails_loud_when_unconfirmed(tmp_path, monkeypatch) -> None:
    # H3 slice 2a must NOT weaken the confirm barrier: if the write's completion signal never appears, the
    # flow fails LOUD (inviolable: never silently report a write as done). The write may actuate, but replay
    # raises rather than returning "confirmed".
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(base, cache)
        del site.writes[:]
        site.confirm_html = site.CONFIRM_BAD   # the confirmation page no longer says "Order placed"
        with pytest.raises(FlowReplayError):
            await flows.replay(spec, params={"qty": "9"}, cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_write_mutation_gate_refuses_on_drift(tmp_path, monkeypatch) -> None:
    # H3 slice 2a must NOT weaken the mutation gate: if the form's enclosing scope drifts (its interactables
    # changed since record), the gate refuses to re-drive the write — ZERO writes reach the server, even with
    # a valid param. A drifted write is a fail-loud, not a blind re-submit.
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(base, cache)
        del site.writes[:]
        # Add a field to the same form -> the submit's precond_scope fingerprint changes -> drift.
        site.checkout_html = site.CHECKOUT.replace(
            "<button type='submit'>",
            "<label for='coupon'>coupon</label><input id='coupon' name='coupon'><button type='submit'>")
        with pytest.raises(FlowReplayError):
            await flows.replay(spec, params={"qty": "9"}, cache=cache)
        assert site.writes == [], "the mutation gate let a drifted write reach the server"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_unbound_slot_param_refused_before_write(tmp_path, monkeypatch) -> None:
    # H3 slice 2a BINDING SAFETY (review finding): a declared+supplied slot that binds to NO recorded step
    # must be refused LOUD before any actuation — otherwise its value folds into the write's Idempotency-Key
    # (varying the key per value) while the FROZEN recorded literal is what's actually submitted: a silent
    # WRONG write + an un-dedup-able DOUBLE write (two identical writes under different keys).
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="order", start_url=base + "/checkout", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Order placed"),
                        slots={"qty": SlotSpec(type="string", pattern="[0-9]{1,3}")}, headless=True)

        async def _demo(pg) -> None:
            await pg.fill("#qty", "7")
            await pg.locator("#qty").blur()
            await pg.get_by_role("button", name="Place the order").click()
            await pg.get_by_text("Order placed").wait_for()

        res = await flows.record(spec, demo=_demo, headless=True, cache=cache)
        assert res.cached and res.is_write, res.note
        # DELIBERATELY do NOT bind any step's `.slot` — "qty" is declared on the spec but bound to no step.
        flows.approve(spec, cache=cache)
        del site.writes[:]
        with pytest.raises(FlowReplayError, match="aren't bound to any recorded"):
            await flows.replay(spec, params={"qty": "9"}, cache=cache)
        assert site.writes == [], "an unbound-slot write actuated — the binding guard must precede the write"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_parameterized_write_with_precheck_refused(tmp_path, monkeypatch) -> None:
    # H3 slice 2a PRECHECK SAFETY (review finding): a parameterized write must not lean on the one-shot
    # idempotency precheck — it probes a FIXED marker with no row awareness, so a generic end-state left by
    # one row could skip a DIFFERENT row's write as "already-done" (a silently suppressed write).
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(base, cache)   # bound qty slot, approved
        # Attach a one-shot precheck AFTER approval (does not change the slot-schema hash — that keys on
        # spec.slots, not mutate), so we get past the binding guard to the precheck refusal.
        spec.mutate = MutateSpec(confirm_text_contains="Order placed", precheck_text_contains="Order placed")
        del site.writes[:]
        with pytest.raises(FlowReplayError, match="row-blind|precheck"):
            await flows.replay(spec, params={"qty": "9"}, cache=cache)
        assert site.writes == [], "a row-blind-precheck parameterized write actuated"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_idempotency_key_canonicalization_is_injective() -> None:
    # H3 slice 2a (review finding): the row canonicalization must be INJECTIVE. A naive "|".join(f"{k}={v}")
    # collides two DISTINCT rows whose free-text values carry the '|'/'=' delimiters — which would mint ONE
    # key for two rows so a backend dedupe silently drops row 2 (a suppressed write). These two distinct rows
    # must mint DISTINCT keys.
    kA = idempotency_key("flow:pay", 2, "pay", slot_values={"memo": "a|payee=b", "payee": "c"})
    kB = idempotency_key("flow:pay", 2, "pay", slot_values={"memo": "a", "payee": "b|payee=c"})
    assert kA != kB, "delimiter-bearing distinct rows collided to one Idempotency-Key (suppressed-write risk)"
    # and the shipped canonicalization guarantees still hold under the injective encoding.
    base = idempotency_key("flow:pay", 2, "pay")
    assert idempotency_key("flow:pay", 2, "pay", slot_values=None) == base       # frozen unchanged
    assert idempotency_key("flow:pay", 2, "pay", slot_values={}) == base         # empty is not a new row
    assert (idempotency_key("flow:pay", 2, "pay", slot_values={"q": 2})
            == idempotency_key("flow:pay", 2, "pay", slot_values={"q": "2"}))    # str() coercion stable
    assert (idempotency_key("flow:pay", 2, "pay", slot_values={"a": "1", "b": "2"})
            == idempotency_key("flow:pay", 2, "pay", slot_values={"b": "2", "a": "1"}))  # order-independent


async def test_relearn_with_params_refused(tmp_path, monkeypatch) -> None:
    # H3 slice 2a (review follow-up): on_drift='relearn' + params is refused LOUD — a re-author re-builds
    # the flow WITHOUT the params, so it would run the frozen defaults and silently return data for the
    # wrong value (inviolable #2). The refusal precedes any browser work (the dead URL is never dialed).
    monkeypatch.chdir(tmp_path)
    spec = FlowSpec(name="r", start_url="http://127.0.0.1:9/", goal="read the total",
                    slots={"q": SlotSpec(type="string")})
    with pytest.raises(FlowReplayError, match="can't be combined with params"):
        await flows.replay(spec, params={"q": "x"}, on_drift="relearn", cache=FlowCache())


def test_idempotency_key_slot_channel() -> None:
    base = idempotency_key("flow:w", 3, "submit")
    # Same base with no slots -> unchanged (existing single-write flows keep their keys).
    assert idempotency_key("flow:w", 3, "submit", slot_values=None) == base
    # Distinct rows -> distinct keys; same row on retry -> same key; key order doesn't matter.
    r1 = idempotency_key("flow:w", 3, "submit", slot_values={"amt": "10", "who": "a"})
    r2 = idempotency_key("flow:w", 3, "submit", slot_values={"who": "a", "amt": "10"})  # reordered
    r3 = idempotency_key("flow:w", 3, "submit", slot_values={"amt": "20", "who": "a"})
    assert r1 == r2 and r1 != r3 and r1 != base


def test_slot_spec_round_trips(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    spec = FlowSpec(name="rt", start_url="http://x/", goal="g",
                    slots={"q": SlotSpec(type="string", enum=["a", "b"], max_length=5),
                           "n": SlotSpec(type="integer", min=1, max=9, required=False)})
    flows.save_spec(spec)
    loaded = flows.load_spec("rt")
    assert isinstance(loaded.slots["q"], SlotSpec) and loaded.slots["q"].enum == ["a", "b"]
    assert loaded.slots["n"].type == "integer" and loaded.slots["n"].min == 1 and loaded.slots["n"].required is False


def test_validate_params_secret_from_env(monkeypatch) -> None:
    spec = FlowSpec(name="s", start_url="http://x/", goal="g",
                    slots={"token": SlotSpec(secret=True, secret_env="MY_TOKEN")})
    # A secret slot resolves from the env, and must NOT be passed in params.
    monkeypatch.setenv("MY_TOKEN", "s3cr3t")
    assert validate_params(spec, {}) == {"token": "s3cr3t"}
    with pytest.raises(FlowReplayError, match="must not be passed in params"):
        validate_params(spec, {"token": "x"})
    monkeypatch.delenv("MY_TOKEN")
    with pytest.raises(FlowReplayError, match="needs env var"):
        validate_params(spec, {})


# --- slice 1b: recorder auto-mining + the value-independence audit -----------------------------
class _EchoLinkSite:
    """Multi-page value-echo fixture: type a query, submit, then a results page renders that value
    INSIDE the link the flow clicks (the dead-template shape the audit must catch)."""

    PAGES = {
        "/": ("<!doctype html><body><form action='/results' method='get'>"
              "<label for='q'>query</label><input id='q' name='q'>"
              "<button type='submit'>search</button></form></body>"),
        "/results": "<!doctype html><body><a href='/detail'>open report X17</a></body>",
        "/detail": "<!doctype html><body><h1>report X17</h1></body>",
    }

    def serve(self):
        pages = self.PAGES

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_GET(self) -> None:
                html = pages.get(self.path.split("?")[0])
                if html is None:
                    self.send_error(404)
                    return
                b = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_mine_slots_creates_typed_slot_and_replays(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()   # input's accessible name is "code" (label for=q)
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="mined", start_url=base + "/", goal="enter the tracking code", headless=True)

        async def _demo(pg) -> None:
            await pg.fill("#q", "alpha-7")
            await pg.locator("#q").blur()

        res = await flows.record(spec, demo=_demo, headless=True, cache=cache, mine_slots=True)
        assert res.cached, res.note
        # Mining auto-lifted the typed value into a named slot on the spec, and marked the step.
        assert spec.slots and "code" in spec.slots and spec.slots["code"].type == "string"
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        typed = next(s for s in cache.get(key).steps if s.action == "type")
        assert typed.slot == "code"
        # And the mined slot is immediately usable: replay(params) substitutes it into the live page.
        flows.approve(spec, cache=cache)
        site.gets.clear()
        await flows.replay(spec, params={"code": "beta-9"}, cache=cache)
        assert "/typed-beta-9" in site.gets and "/typed-alpha-7" not in site.gets
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_mine_slots_audit_refuses_value_echo(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoLinkSite()
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="echo", start_url=base + "/", goal="open the flagged report", headless=True)

        async def _demo(pg) -> None:
            await pg.fill("#q", "X17")
            await pg.locator("#q").blur()
            await pg.click("button")                       # GET-form submit -> /results
            lk = pg.get_by_role("link", name="open report X17")
            await lk.wait_for()
            await lk.click()                               # click the VALUE-ECHOING link

        res = await flows.record(spec, demo=_demo, headless=True, cache=cache, mine_slots=True)
        # The audit refuses to templatize a dead template: not cached, note names the value leak, and the
        # finding is reported. (A non-mining record of the same flow would cache normally — mining is opt-in.)
        assert not res.cached, "audit should have refused the value-echo template"
        assert "value-independence audit" in res.note and "echoes" in res.note
        assert any(f["value_leak"] for f in res.slot_findings)
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None  # nothing cached
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_value_leaks_scans_content_fields_not_structural_css() -> None:
    # The audit scans the CONTENT-bearing fields resolve() binds on (name/text/anchor/testid/placeholder/
    # elem_id) — a value echoed into any of them is a dead template. It does NOT scan the structural css
    # path (tag names + nth-of-type), so a value that's a mere substring of a tag name isn't a false leak.
    from ultracua.cache import CachedStep
    from ultracua.flows import _value_leaks
    from ultracua.locators import LocatorSpec

    def step(**loc) -> CachedStep:
        base = {"role": "link", "name": "", "tag": "a"}
        base.update(loc)
        return CachedStep(intent="x", action="click", locator=LocatorSpec(**base))

    assert _value_leaks("X17", [step(name="open report X17")])          # role+name
    assert _value_leaks("X17", [step(placeholder="Re-enter code X17")])  # placeholder (Tier-1 binder)
    assert _value_leaks("X17", [step(testid="report-X17")])              # data-testid
    assert _value_leaks("X17", [step(elem_id="row-X17")])                # element id
    # A tag-name-like value that only appears in a later STRUCTURAL css path is NOT a leak.
    assert _value_leaks("form", [step(name="results", css="main > form > a:nth-of-type(2)")]) is None


async def test_mine_slots_refuses_when_slots_predeclared(tmp_path, monkeypatch) -> None:
    # Opting into mining AND pre-declaring a typed slot table is a conflict — mining would clobber the
    # author's enum/pattern/range with bare string slots. Refuse loud rather than silently drop the domain.
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="conflict", start_url=base + "/", goal="enter the code", headless=True,
                        slots={"code": SlotSpec(type="string", enum=["a", "b"])})

        async def _demo(pg) -> None:
            await pg.fill("#q", "a")
            await pg.locator("#q").blur()

        res = await flows.record(spec, demo=_demo, headless=True, cache=cache, mine_slots=True)
        assert not res.cached and "won't overwrite" in res.note
        assert spec.slots["code"].enum == ["a", "b"]   # the declared domain is untouched
    finally:
        httpd.shutdown()
        httpd.server_close()


class _FormSite:
    """A select (closed option domain) + a constrained text input — the site metadata slice-1c mines
    into typed slot domains (enum from the options; pattern/max_length/required from the input)."""

    BODY = ("<!doctype html><body>"
            "<label for='color'>color</label>"
            "<select id='color'><option value='red'>Red</option><option value='green'>Green</option>"
            "<option value='blue'>Blue</option></select> "
            "<label for='qty'>qty</label>"
            "<input id='qty' type='text' pattern='[0-9]{1,3}' required maxlength='3'></body>")

    def serve(self):
        body = self.BODY

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_GET(self) -> None:
                b = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_mine_slots_captures_site_metadata_domain(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _FormSite()
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="form", start_url=base + "/", goal="pick color and quantity", headless=True)

        async def _demo(pg) -> None:
            await pg.select_option("#color", "green")
            await pg.fill("#qty", "42")
            await pg.locator("#qty").blur()

        res = await flows.record(spec, demo=_demo, headless=True, cache=cache, mine_slots=True)
        assert res.cached, res.note
        # The <select>'s legal option domain became a closed enum on the mined slot.
        assert spec.slots["color"].enum == ["red", "green", "blue"]
        # The input's constraints (pattern / maxlength / required) carried onto its slot.
        qty = spec.slots["qty"]
        assert qty.pattern == "[0-9]{1,3}" and qty.max_length == 3 and qty.required is True
        # And pre-flight now validates against the captured domain: an out-of-enum color fails loud.
        flows.approve(spec, cache=cache)
        with pytest.raises(FlowReplayError, match="one of"):
            await flows.replay(spec, params={"color": "purple"}, cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_slotspec_from_domain_multiselect_is_not_a_strict_enum() -> None:
    # A single <select> -> a closed enum; a <select multiple> (value is a JSON-array string) must NOT
    # become a strict per-option enum, or it would reject its own demonstrated value.
    from ultracua.flows import _slotspec_from_domain

    assert _slotspec_from_domain({"options": ["a", "b"]}).enum == ["a", "b"]
    ms = _slotspec_from_domain({"options": ["a", "b"], "multiple": True})
    assert ms.enum is None and ms.type == "string"


class _MultiSelectSite:
    BODY = ("<!doctype html><body><label for='colors'>colors</label>"
            "<select id='colors' multiple>"
            "<option value='red'>Red</option><option value='green'>Green</option>"
            "<option value='blue'>Blue</option></select></body>")

    def serve(self):
        body = self.BODY

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_GET(self) -> None:
                b = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_mine_slots_multiselect_param_validates_and_actuates(tmp_path, monkeypatch) -> None:
    # A mined <select multiple> slot must accept its JSON-array value on the params path (the 1b regression
    # the strict individual-option enum caused). No strict enum + the array value replays without raising.
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _MultiSelectSite()
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="multi", start_url=base + "/", goal="pick the colors", headless=True)

        async def _demo(pg) -> None:
            await pg.select_option("#colors", ["green", "blue"])

        res = await flows.record(spec, demo=_demo, headless=True, cache=cache, mine_slots=True)
        assert res.cached, res.note
        assert spec.slots["colors"].enum is None   # multi-select isn't a strict per-option enum
        flows.approve(spec, cache=cache)
        # the JSON-array param validates (string slot) and actuates both options — no FlowReplayError.
        await flows.replay(spec, params={"colors": '["red", "green"]'}, cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()
