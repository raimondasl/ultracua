"""H2 stage 2 — WRITE exposure over MCP, key-less (local write-oracle fixture + a fake `confirm`).

Approved DECLARED write flows are exposed ONLY behind `expose_writes=True`, and each call goes through the
write rail — per-flow single-flight mutex -> retry-dedupe ledger -> human confirm-or-refuse -> fire -> record
after confirm. The server-side `_CheckoutSite.writes` is the oracle: how many times a write actually reached
the wire. The Idempotency-Key is the correctness floor; the ledger + mutex + confirm are the rails.
"""

from __future__ import annotations

import asyncio

import pytest

from ultracua import flows
from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowSpec, MutateSpec, SlotSpec
from ultracua.ledger import RunLedger
from ultracua.mcpserver import call_flow_tool, list_flow_tools
from ultracua.mcpserver.server import WriteConfirmRequest

# Reuse the write fixture + record/bind/approve helper from the run_batch suite (tests/ is on sys.path).
from test_run_batch import _CheckoutSite, _record_write_flow


class _Confirm:
    """A fake elicitation confirm: records each WriteConfirmRequest it sees, returns `accept`."""

    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.calls: list = []

    async def __call__(self, req: WriteConfirmRequest) -> bool:
        self.calls.append(req)
        return self.accept


async def _serve_write_flow(site, base, cache, **kw):
    """Record + bind + approve + SAVE a write flow so list_flow_tools/call_flow_tool find it. Tool name='order'."""
    spec = await _record_write_flow(site, base, cache, **kw)   # clears the demo's own write
    flows.save_spec(spec)
    return spec


# --- (1) default-deny without the flag --------------------------------------------------------
async def test_write_default_deny_without_expose_writes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        await _serve_write_flow(site, base, cache)
        assert list_flow_tools(cache, expose_writes=False) == []          # not advertised
        out = await call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=False,
                                   confirm=_Confirm(True))
        assert not out.ok and out.code == "unknown_tool"                  # not dispatchable
        assert site.writes == []                                          # ZERO writes
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (2) fires once + confirmed ---------------------------------------------------------------
async def test_write_fires_once_with_confirm(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        await _serve_write_flow(site, base, cache)
        tool = {t.name: t for t in list_flow_tools(cache, expose_writes=True)}["order"]
        assert tool.is_write and tool.description.startswith("[WRITE")   # advertised as a write, warned
        confirm = _Confirm(True)
        out = await call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True, confirm=confirm)
        assert out.ok and isinstance(out.data, dict) and out.data.get("status") == "confirmed"
        assert len(site.writes) == 1 and "qty=9" in site.writes[0][2]    # fired exactly once, the arg on the wire
        assert len(confirm.calls) == 1                                    # elicited once
        assert confirm.calls[0].idempotency_keys and confirm.calls[0].arguments == {"qty": "9"}
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (3) decline -> not fired -----------------------------------------------------------------
async def test_write_declined_not_fired(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        await _serve_write_flow(site, base, cache)
        out = await call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True,
                                   confirm=_Confirm(accept=False))
        assert not out.ok and out.code == "declined"
        assert site.writes == []
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (4) no elicitation capability -> refused, not fired --------------------------------------
async def test_write_no_confirm_capability_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        await _serve_write_flow(site, base, cache)
        out = await call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True, confirm=None)
        assert not out.ok and out.code == "elicitation_unsupported"
        assert site.writes == []
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (5) RETRY-DEDUPE PIN: same args twice -> fires exactly once -------------------------------
async def test_retry_of_same_args_dedupes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        await _serve_write_flow(site, base, cache)
        confirm = _Confirm(True)
        first = await call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True, confirm=confirm)
        second = await call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True, confirm=confirm)
        assert first.ok and first.data.get("status") == "confirmed"
        assert second.ok and second.code == "already_done"              # deduped, NOT re-fired
        assert len(site.writes) == 1, "a retry of the same args double-fired the write"
        assert len(confirm.calls) == 1, "the deduped retry re-elicited the human"   # second never elicits
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (6) MUTEX single-flight: two concurrent identical calls -> fires once ---------------------
async def test_concurrent_identical_calls_single_flight(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        await _serve_write_flow(site, base, cache)
        confirm = _Confirm(True)
        a, b = await asyncio.gather(
            call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True, confirm=confirm),
            call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True, confirm=confirm))
        codes = sorted([a.code, b.code])
        assert a.ok and b.ok and codes == ["", "already_done"]          # one fired, one deduped
        assert len(site.writes) == 1, "the per-flow mutex let two concurrent calls both fire"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (7) distinct args -> each its own write --------------------------------------------------
async def test_concurrent_distinct_args_each_fire(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        await _serve_write_flow(site, base, cache)
        confirm = _Confirm(True)
        a, b = await asyncio.gather(
            call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True, confirm=confirm),
            call_flow_tool("order", cache, arguments={"qty": "8"}, expose_writes=True, confirm=confirm))
        assert a.ok and b.ok
        bodies = sorted(w[2] for w in site.writes)
        assert len(site.writes) == 2 and any("qty=9" in x for x in bodies) and any("qty=8" in x for x in bodies)
        keys = [w[1] for w in site.writes]
        assert keys[0] != keys[1], "distinct rows shared an Idempotency-Key"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (8) undeclared write is never exposed nor dispatched, even with the flag ------------------
async def test_undeclared_write_never_exposed_even_with_flag(tmp_path, monkeypatch) -> None:
    import time as _t

    from ultracua.cache import CachedFlow, CachedStep
    from ultracua.locators import LocatorSpec

    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    spec = FlowSpec(name="sneaky", start_url="http://127.0.0.1:9/checkout", goal="show the total",
                    slots={"qty": SlotSpec(type="string")})   # NOTE: no mutate declared
    flows.save_spec(spec)
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, created_ts=_t.time(), steps=[
        CachedStep(intent="type qty", action="type", text="7", slot="qty",
                   locator=LocatorSpec(role="textbox", name="qty", tag="input")),
        CachedStep(intent="place the order", action="click", mutating=True,
                   locator=LocatorSpec(role="button", name="Place the order", tag="button")),
    ]))
    flows.approve(spec, cache=cache)
    assert "sneaky" not in {t.spec_name for t in list_flow_tools(cache, expose_writes=True)}
    out = await call_flow_tool("sneaky", cache, arguments={"qty": "9"}, expose_writes=True, confirm=_Confirm(True))
    assert not out.ok and out.code in ("unknown_tool", "write_denied")


# --- (9) a declared READ never elicits + is unaffected ----------------------------------------
async def test_read_never_elicits(tmp_path, monkeypatch) -> None:
    from test_mcp_server import _record_slotted_read
    from test_run_batch import _EchoSite

    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()
    httpd, base = site.serve()
    try:
        await _record_slotted_read(site, base, cache)   # records + saves + approves a slotted echo READ
        confirm = _Confirm(True)
        site.gets.clear()
        out = await call_flow_tool("lookup", cache, arguments={"code": "beta-9"}, expose_writes=True,
                                   confirm=confirm)
        assert out.ok and "/typed-beta-9" in site.gets
        assert confirm.calls == [], "a read flow elicited a write confirmation"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (10) a bad arg fails BEFORE eliciting or firing ------------------------------------------
async def test_invalid_arg_precedes_elicit(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        await _serve_write_flow(site, base, cache, pattern="[0-9]{1,3}")
        confirm = _Confirm(True)
        out = await call_flow_tool("order", cache, arguments={"qty": "NaN"}, expose_writes=True, confirm=confirm)
        assert not out.ok and out.code == "invalid_params"
        assert confirm.calls == [] and site.writes == []                 # never elicited, never fired
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (11) record-after-confirm: a fire that fails its confirm is NOT recorded (re-fires next) --
async def test_failed_write_not_recorded_so_retry_refires(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        await _serve_write_flow(site, base, cache)
        # Drift the form so the write is refused at actuation (the mutation gate fails loud).
        site.checkout_html = site.CHECKOUT.replace(
            "<button type='submit'>",
            "<label for='c'>coupon</label><input id='c' name='c'><button type='submit'>")
        confirm = _Confirm(True)
        first = await call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True, confirm=confirm)
        assert not first.ok                                              # drift -> failed, not confirmed
        # un-drift; a re-run RE-FIRES (the failed call was never recorded -> no false already_done).
        site.checkout_html = site.CHECKOUT
        del site.writes[:]
        second = await call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True, confirm=confirm)
        assert second.ok and second.code == "" and len(site.writes) == 1
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (12) secret-safe confirm preview ---------------------------------------------------------
async def test_confirm_preview_is_secret_free(tmp_path, monkeypatch) -> None:
    """A write flow with a bound secret slot: the env secret ACTUATES on the wire, but the human's
    confirm preview (arguments + hashed idempotency keys) carries no plaintext secret."""
    from ultracua.cache import flow_key as _fk

    from test_run_batch import _TokenCheckoutSite

    _SECRET = "s3cr3t-mcp-77"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UCA_MCPW_TOK", _SECRET)
    cache = FlowCache()
    site = _TokenCheckoutSite()
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="paytok", start_url=base + "/checkout", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Order placed"),
                        slots={"qty": SlotSpec(type="string"),
                               "token": SlotSpec(secret=True, secret_env="UCA_MCPW_TOK")}, headless=True)

        async def _demo(pg) -> None:
            await pg.fill("#qty", "7")
            await pg.fill("#token", "demo-token")
            await pg.locator("#token").blur()
            await pg.get_by_role("button", name="Place the order").click()
            await pg.get_by_text("Order placed").wait_for()

        await flows.record(spec, demo=_demo, headless=True, cache=cache)
        flow = cache.get(_fk(spec.goal, spec.start_url, spec.scope))
        for s in flow.steps:                       # bind the two typed fields to their slots
            if s.action == "type" and s.text == "7":
                s.slot = "qty"
            elif s.action == "type" and s.text == "demo-token":
                s.slot = "token"
        cache.put(flow)
        flows.save_spec(spec)
        flows.approve(spec, cache=cache)
        del site.writes[:]                          # drop the demo's own write

        confirm = _Confirm(True)
        out = await call_flow_tool("paytok", cache, arguments={"qty": "9"}, expose_writes=True, confirm=confirm)
        assert out.ok, f"{out.code}: {out.message}"
        assert any(_SECRET in w[2] for w in site.writes), "the secret slot did not actuate on the wire"
        req = confirm.calls[0]
        assert _SECRET not in repr(req), "a secret leaked into the confirm preview"
        assert "token" not in req.arguments, "a secret slot appeared in the preview arguments"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (13) a stale approval (slot schema changed since approval) refuses BEFORE elicit ----------
async def test_stale_approval_refused_before_elicit(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _serve_write_flow(site, base, cache)   # approved with pattern "[0-9]{1,3}"
        # Loosen the qty pattern AFTER approval and re-save without re-approving: slots_hash now drifts.
        spec.slots["qty"] = SlotSpec(type="string", pattern="[0-9]+")
        flows.save_spec(spec)
        confirm = _Confirm(True)
        out = await call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True, confirm=confirm)
        assert not out.ok and out.code == "replay_error" and "re-approve" in out.message
        assert confirm.calls == [] and site.writes == []    # refused pre-elicit, never fired
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (14) crash-window: the ledger record can be lost, but the Idempotency-Key is the floor ----
async def test_crash_window_shares_stable_idempotency_key(tmp_path, monkeypatch) -> None:
    """If the process dies AFTER firing but BEFORE `ledger.record` durably commits, a retry re-fires
    (the local ledger is a best-effort optimization, not the correctness floor). What makes that safe
    is that BOTH fires carry the SAME Idempotency-Key — a compliant server dedupes them. We pin that
    invariant here by making `record` raise (a crash stand-in) and asserting key-equality across fires."""
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        await _serve_write_flow(site, base, cache)

        orig_record = RunLedger.record

        def _boom(self, *a, **k):     # a crash right where the durable commit would happen
            raise OSError("disk full at fsync")

        monkeypatch.setattr(RunLedger, "record", _boom)
        confirm = _Confirm(True)
        with pytest.raises(OSError):
            await call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True, confirm=confirm)
        assert len(site.writes) == 1                          # the write DID fire before the crash

        monkeypatch.setattr(RunLedger, "record", orig_record)   # restore ONLY record (keep the chdir)
        second = await call_flow_tool("order", cache, arguments={"qty": "9"}, expose_writes=True, confirm=confirm)
        assert second.ok and len(site.writes) == 2            # re-fired (the lost record couldn't dedupe)
        assert site.writes[0][1] == site.writes[1][1], \
            "the crash-window re-fire used a different Idempotency-Key — a compliant server can't dedupe it"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (15) the CLI `serve-mcp --expose-writes` flag parses (and defaults off) -------------------
def test_cli_expose_writes_flag_parses(monkeypatch) -> None:
    from ultracua import cli

    seen: dict = {}
    monkeypatch.setattr(cli, "_flow_serve_mcp", lambda a: seen.__setitem__("v", getattr(a, "expose_writes", None)))
    cli._flow_main(["serve-mcp", "--expose-writes"])
    assert seen["v"] is True
    cli._flow_main(["serve-mcp"])
    assert seen["v"] is False        # default-deny at the CLI too
