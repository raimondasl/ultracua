"""H3 write-slot binding (`writable_slots`) — the explicit human sign-off that makes a WRITE template
usable through the PUBLIC API (no cache surgery), key-less against local fixtures.

The load-bearing pin: record(spec, demo, writable_slots={"amount"}) -> approve() -> replay(params=...) with
the substituted value reaching the POST body AND folding into the row-keyed Idempotency-Key — while a field
NOT named stays frozen. Plus the safety refusals: a no-match / ambiguous name, a value-echo audit leak, a
read flow, mine_slots+writable_slots together, and a non-required secret; and the typed-domain + secret +
declared-but-unbound + approval-hash interactions.
"""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from ultracua import flows
from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowReplayError, FlowSpec, MutateSpec, SlotSpec

# Reuse the read echo fixture from the run_batch suite (tests/ is on sys.path).
from test_run_batch import _EchoSite


def _serve(html_for):
    """A tiny method=post fixture: GET serves html_for(path); POST records (path, idem-key, body) + 303."""
    class Site:
        def __init__(self) -> None:
            self.writes: list = []
            self.gets: list = []

        def serve(self):
            site = self

            class H(http.server.BaseHTTPRequestHandler):
                def log_message(self, *a) -> None:
                    pass

                def do_GET(self) -> None:
                    path = self.path.split("?")[0]
                    site.gets.append(path)
                    b = html_for(path).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(b)))
                    self.end_headers()
                    self.wfile.write(b)

                def do_POST(self) -> None:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(n).decode("utf-8", "replace")
                    site.writes.append((self.path.split("?")[0],
                                        self.headers.get("Idempotency-Key"), body))
                    self.send_response(303)
                    self.send_header("Location", "/done")
                    self.end_headers()

            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"

    return Site()


_DONE = "<!doctype html><html><body><h1>Transfer complete</h1></body></html>"
_TRANSFER = ("<!doctype html><html><body><h1>Transfer</h1>"
             "<form method='post' action='/transfer'>"
             "<label for='payee'>payee</label><input id='payee' name='payee'>"
             "<label for='amount'>amount</label><input id='amount' name='amount'>"
             "<button type='submit'>Send money</button></form></body></html>")


def _transfer_site():
    return _serve(lambda p: _DONE if p == "/done" else _TRANSFER)


async def _demo_transfer(pg) -> None:
    await pg.fill("#payee", "Acme Corp")
    await pg.locator("#payee").blur()
    await pg.fill("#amount", "100.00")
    await pg.locator("#amount").blur()
    await pg.get_by_role("button", name="Send money").click()
    await pg.get_by_text("Transfer complete").wait_for()


async def _record_transfer(site, base, cache, *, writable_slots=None, slots=None, name="transfer"):
    spec = FlowSpec(name=name, start_url=base + "/pay", goal="send the transfer",
                    mutate=MutateSpec(confirm_text_contains="Transfer complete"),
                    slots=slots, headless=True)
    res = await flows.record(spec, demo=_demo_transfer, headless=True, cache=cache,
                             writable_slots=writable_slots)
    return spec, res


def _steps(cache, spec):
    return cache.get(flow_key(spec.goal, spec.start_url, spec.scope)).steps


# --- (1) END-TO-END public-API pin ------------------------------------------------------------
async def test_writable_slots_end_to_end(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _transfer_site()
    httpd, base = site.serve()
    try:
        spec, res = await _record_transfer(site, base, cache, writable_slots={"amount"})
        assert res.cached and res.is_write, res.note
        amount = next(s for s in _steps(cache, spec) if s.action == "type" and s.text == "100.00")
        payee = next(s for s in _steps(cache, spec) if s.action == "type" and s.text == "Acme Corp")
        assert amount.slot == "amount" and payee.slot is None, "only the NAMED field is bound"

        flows.approve(spec, cache=cache)
        del site.writes[:]
        await flows.replay(spec, params={"amount": "250.00"}, cache=cache)
        body = site.writes[-1][2]
        assert "amount=250.00" in body, f"substituted amount didn't reach the wire: {body}"
        assert "Acme" in body, f"the frozen payee didn't replay: {body}"    # payee stayed the demo literal
        k1 = site.writes[-1][1]
        await flows.replay(spec, params={"amount": "9.99"}, cache=cache)
        k2 = site.writes[-1][1]
        await flows.replay(spec, params={"amount": "250.00"}, cache=cache)
        k3 = site.writes[-1][1]
        assert k1 != k2, "distinct amounts must mint distinct row keys (no suppressed write)"
        assert k1 == k3, "the same amount must mint the same row key (retry-safe)"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (2) NOT-LISTED field stays FROZEN --------------------------------------------------------
async def test_unlisted_field_stays_frozen(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _transfer_site()
    httpd, base = site.serve()
    try:
        spec, res = await _record_transfer(site, base, cache, writable_slots={"amount"})
        flows.approve(spec, cache=cache)
        del site.writes[:]
        # payee wasn't named -> it's not a slot -> a param for it is refused as unknown.
        with pytest.raises(FlowReplayError, match="unknown param"):
            await flows.replay(spec, params={"payee": "Evil Corp"}, cache=cache)
        assert site.writes == []
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (3) AUDIT refusal (value echoes into a later locator) ------------------------------------
_ECHO_FORM = ("<!doctype html><html><body>"
              "<form method='post' action='/pay'>"
              "<label for='amount'>amount</label><input id='amount' name='amount'>"
              "<button type='submit' id='pay'>Send</button>"
              "<script>document.getElementById('amount').addEventListener('input',(e)=>{"
              "document.getElementById('pay').textContent='Send '+e.target.value;});</script>"
              "</body></html>")


async def test_writable_slots_audit_refuses_value_echo(tmp_path, monkeypatch) -> None:
    # Filling #amount echoes the value into the submit button's accessible name — so a non-demo value would
    # retarget the WRONG button (a dead + dangerous write template). The value-independence audit refuses.
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _serve(lambda p: _DONE if p == "/done" else _ECHO_FORM)
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="echoamt", start_url=base + "/pay", goal="send",
                        mutate=MutateSpec(confirm_text_contains="Transfer complete"), headless=True)

        async def _demo(pg) -> None:
            await pg.fill("#amount", "X17")
            await pg.locator("#amount").blur()
            await pg.get_by_role("button", name="Send X17").click()
            await pg.get_by_text("Transfer complete").wait_for()

        res = await flows.record(spec, demo=_demo, headless=True, cache=cache, writable_slots={"amount"})
        assert not res.cached, "the audit should refuse a value-echo write template"
        assert "value-independence audit" in res.note and any(f["value_leak"] for f in res.slot_findings)
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (4) AMBIGUITY refusal (two fields derive the same name) ----------------------------------
_TWO_AMOUNTS = ("<!doctype html><html><body>"
                "<form method='post' action='/pay'>"
                "<label for='a1'>amount</label><input id='a1' name='a1'>"
                "<label for='a2'>amount</label><input id='a2' name='a2'>"
                "<button type='submit'>Send money</button></form></body></html>")


async def test_writable_slots_refuses_ambiguous_name(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _serve(lambda p: _DONE if p == "/done" else _TWO_AMOUNTS)
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="ambig", start_url=base + "/pay", goal="send",
                        mutate=MutateSpec(confirm_text_contains="Transfer complete"), headless=True)

        async def _demo(pg) -> None:
            await pg.fill("#a1", "10")
            await pg.locator("#a1").blur()
            await pg.fill("#a2", "20")
            await pg.locator("#a2").blur()
            await pg.get_by_role("button", name="Send money").click()
            await pg.get_by_text("Transfer complete").wait_for()

        res = await flows.record(spec, demo=_demo, headless=True, cache=cache, writable_slots={"amount"})
        assert not res.cached and "AMBIGUOUS" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (5) NO-MATCH refusal ---------------------------------------------------------------------
async def test_writable_slots_refuses_no_match(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _transfer_site()
    httpd, base = site.serve()
    try:
        spec, res = await _record_transfer(site, base, cache, writable_slots={"nope"})
        assert not res.cached and "no demonstrated type/select field" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (6) TYPED-DOMAIN (declared spec.slots wins) ----------------------------------------------
async def test_writable_slots_binds_declared_typed_domain(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _transfer_site()
    httpd, base = site.serve()
    try:
        # Author pre-declares a TYPED domain for `amount` AND names it writable -> the declared SlotSpec binds.
        spec, res = await _record_transfer(site, base, cache, writable_slots={"amount"},
                                           slots={"amount": SlotSpec(type="number", min=0, max=100)})
        assert res.cached
        flows.approve(spec, cache=cache)
        # Out-of-range refused by validate_params (proves the declared number domain bound, not a bare string).
        with pytest.raises(FlowReplayError, match="one of|<=|>="):
            await flows.replay(spec, params={"amount": 999}, cache=cache)
        del site.writes[:]
        await flows.replay(spec, params={"amount": 50}, cache=cache)   # in range -> runs
        assert "amount=50" in site.writes[-1][2]
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (7) WRITE-ONLY + MUTUAL-EXCLUSION (config refusals, no browser) ---------------------------
async def test_writable_slots_write_only_and_mutual_exclusion(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    # (a) writable_slots on a READ flow -> refused before any navigation.
    read_site = _EchoSite()
    httpd, base = read_site.serve()
    try:
        read_spec = FlowSpec(name="r", start_url=base + "/", goal="enter code", headless=True)

        async def _rdemo(pg) -> None:
            await pg.fill("#q", "x")
            await pg.locator("#q").blur()

        res = await flows.record(read_spec, demo=_rdemo, headless=True, cache=cache, writable_slots={"q"})
        assert not res.cached and "needs a declared write" in res.note
        assert read_site.gets == [], "a config error must not open the browser"
    finally:
        httpd.shutdown()
        httpd.server_close()
    # (b) mine_slots + writable_slots together -> refused before any navigation.
    site = _transfer_site()
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="both", start_url=base + "/pay", goal="send",
                        mutate=MutateSpec(confirm_text_contains="Transfer complete"), headless=True)
        res = await flows.record(spec, demo=_demo_transfer, headless=True, cache=cache,
                                 mine_slots=True, writable_slots={"amount"})
        assert not res.cached and "not both" in res.note
        assert site.gets == []
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (8) SECRET bind (masked in cache) + non-required refusal ----------------------------------
_SECRET = "s3cr3t-tok-42"
_PAY_TOKEN = ("<!doctype html><html><body><form method='post' action='/pay'>"
              "<label for='amount'>amount</label><input id='amount' name='amount'>"
              "<label for='token'>token</label><input id='token' name='token'>"
              "<button type='submit'>Send money</button></form></body></html>")


async def test_writable_slots_secret_masked_and_nonrequired_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UCA_WS_TOK", _SECRET)
    cache = FlowCache()
    site = _serve(lambda p: _DONE if p == "/done" else _PAY_TOKEN)
    httpd, base = site.serve()

    async def _demo(pg) -> None:
        await pg.fill("#amount", "100.00")
        await pg.locator("#amount").blur()
        await pg.fill("#token", "demo-token")
        await pg.locator("#token").blur()
        await pg.get_by_role("button", name="Send money").click()
        await pg.get_by_text("Transfer complete").wait_for()

    try:
        # A non-required secret writable slot -> refused (a missing $env would type a blank secret).
        bad = FlowSpec(name="sec0", start_url=base + "/pay", goal="send",
                       mutate=MutateSpec(confirm_text_contains="Transfer complete"),
                       slots={"token": SlotSpec(secret=True, required=False, secret_env="UCA_WS_TOK")},
                       headless=True)
        res0 = await flows.record(bad, demo=_demo, headless=True, cache=cache, writable_slots={"token"})
        assert not res0.cached and "secret but not required" in res0.note

        # A required secret writable slot -> bound, and the demo plaintext is SCRUBBED from the cache.
        spec = FlowSpec(name="sec1", start_url=base + "/pay", goal="send",
                        mutate=MutateSpec(confirm_text_contains="Transfer complete"),
                        slots={"token": SlotSpec(secret=True, required=True, secret_env="UCA_WS_TOK")},
                        headless=True)
        res = await flows.record(spec, demo=_demo, headless=True, cache=cache, writable_slots={"token"})
        assert res.cached, res.note
        tok = next(s for s in _steps(cache, spec) if s.slot == "token")
        assert tok.text == "", "the plaintext secret must be scrubbed from the cached step"
        raw = (cache.root / f"{flow_key(spec.goal, spec.start_url, spec.scope)}.json").read_text("utf-8")
        assert "demo-token" not in raw and _SECRET not in raw, "no secret plaintext may persist to disk"
        # replay resolves the secret from $env and refuses it in params.
        flows.approve(spec, cache=cache)
        del site.writes[:]
        await flows.replay(spec, params={}, cache=cache)     # token from env
        assert any(_SECRET in w[2] for w in site.writes), "the env secret didn't actuate"
        with pytest.raises(FlowReplayError, match="must not be passed in params"):
            await flows.replay(spec, params={"token": "x"}, cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (9) DECLARED-BUT-UNBOUND declared slot -> warn, replay guard refuses ----------------------
async def test_declared_but_unbound_slot_refused_at_replay(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _transfer_site()
    httpd, base = site.serve()
    try:
        # Declare TWO typed slots but only bind `amount`; `note` is declared-but-unbound.
        spec, res = await _record_transfer(
            site, base, cache, writable_slots={"amount"},
            slots={"amount": SlotSpec(type="string"), "note": SlotSpec(type="string")})
        assert res.cached
        flows.approve(spec, cache=cache)
        del site.writes[:]
        # The declared-but-unbound `note` binds no step -> the replay binding guard refuses a param for it.
        with pytest.raises(FlowReplayError, match="aren't bound to any recorded"):
            await flows.replay(spec, params={"amount": "5", "note": "hi"}, cache=cache)
        assert site.writes == []
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (10) APPROVAL-HASH interaction -----------------------------------------------------------
async def test_writable_slots_bound_slot_under_approval_hash(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _transfer_site()
    httpd, base = site.serve()
    try:
        spec, res = await _record_transfer(site, base, cache, writable_slots={"amount"},
                                           slots={"amount": SlotSpec(type="string", pattern="[0-9.]{1,8}")})
        flows.approve(spec, cache=cache)
        # Widen the bound slot's domain AFTER approval -> replay refuses until re-approved.
        spec.slots["amount"] = SlotSpec(type="string")
        with pytest.raises(FlowReplayError, match="schema changed since approval"):
            await flows.replay(spec, params={"amount": "5"}, cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (11) mine_slots on a write flow stays a no-op (regression) --------------------------------
async def test_mine_slots_on_write_lifts_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _transfer_site()
    httpd, base = site.serve()
    try:
        spec, res = await _record_transfer(site, base, cache)   # mine_slots default False, no writable_slots
        assert res.cached and res.is_write and not res.slot_findings
        assert all(s.slot is None for s in _steps(cache, spec)), "no write field auto-lifted"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (12) CLI parse + write-only pre-check -----------------------------------------------------
def test_cli_writable_slots_requires_confirm() -> None:
    from ultracua.cli import _flow_main
    # --writable-slots without a --confirm-* is refused up front (write-only sign-off).
    with pytest.raises(SystemExit) as ei:
        _flow_main(["record", "--name", "x", "--url", "http://127.0.0.1:9/", "--goal", "g",
                    "--writable-slots", "amount"])
    assert "needs a --confirm-" in str(ei.value.code)
