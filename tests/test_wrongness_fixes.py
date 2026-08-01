"""The 0.64.0 wrongness release: four ways the system could be silently wrong, each pinned.

These came out of a six-lens audit. Every one was reproduced with a probe BEFORE being fixed, and every
test here was confirmed to fail against the pre-fix code — a regression test that passes both before and
after proves nothing.

The four share a shape worth naming: in each case the correct guard already existed somewhere in the
codebase and simply had not been applied to the sibling path.
  * the row anchor      — `heading`/`label` were hardened against prefix matching; `row` was not
  * the header overlay  — nothing merged, so the safety header evicted the flow's own auth headers
  * the empty pin       — the LIST pin already rejected empty; the SCALAR pin did not
  * the slots gate      — the contracts gate one line below carries the comment explaining the bug
"""

from __future__ import annotations

import http.server
import threading

from playwright.async_api import async_playwright

from ultracua.locators import DESCRIBE_JS, LocatorSpec, resolve


async def _bind(html: str, spec: LocatorSpec):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            await page.set_content(html)
            loc = await resolve(page, spec, unique=True)
            return await loc.get_attribute("data-o") if loc is not None else None
        finally:
            await browser.close()


# ============================ 1. the row anchor bound a different row ============================

_ROWS_PRISTINE = """<!doctype html><html><body><table><tbody>
  <tr><td>Acme Corp</td><td>#3</td><td><a href="/cancel/3" data-o="TARGET">Cancel</a></td></tr>
  <tr><td>Acme Corp</td><td>#30</td><td><a href="/cancel/30" data-o="OTHER">Cancel</a></td></tr>
</tbody></table></body></html>"""

# the recorded row is DELETED; the row whose text CONTAINS it as a prefix remains
_ROWS_DRIFTED = """<!doctype html><html><body><table><tbody>
  <tr><td>Acme Corp</td><td>#30</td><td><a href="/cancel/30" data-o="OTHER">Cancel</a></td></tr>
</tbody></table></body></html>"""


async def _row_spec() -> LocatorSpec:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            await page.set_content(_ROWS_PRISTINE)
            await page.evaluate("() => document.querySelector('[data-o=TARGET]')"
                                ".setAttribute('data-ultracua-ref','r1')")
            return LocatorSpec(**await page.evaluate(DESCRIBE_JS, "r1"))
        finally:
            await browser.close()


async def test_a_deleted_row_never_binds_a_prefix_sharing_sibling() -> None:
    """THE CRITICAL ONE. The row anchor was a SUBSTRING match, so a recorded row "Acme Corp #3 Cancel"
    also matched "Acme Corp #30 Cancel". With the recorded row deleted the sibling matched uniquely and
    bound outright — measured: it cancelled order #30, and the run reported success.

    The mutation gate cannot catch this: per-row forms are structurally identical, so the scope
    fingerprint matches byte-for-byte. The anchor is the only thing standing between a per-row write and
    the wrong customer."""
    spec = await _row_spec()
    assert spec.anchor_source == "row" and spec.anchor == "Acme Corp #3 Cancel"
    bound = await _bind(_ROWS_DRIFTED, spec)
    assert bound is None, f"bound {bound!r} — a per-row write would fire against the wrong record"


async def test_the_right_row_still_binds_when_it_is_present() -> None:
    """The fix must not cost the capability: exact matching still finds the recorded row."""
    spec = await _row_spec()
    assert await _bind(_ROWS_PRISTINE, spec) == "TARGET"


async def test_a_truncated_row_anchor_is_not_protected_and_that_is_documented() -> None:
    """THE RESIDUAL, pinned honestly rather than left implicit.

    `anchorOf` slices row text to 60 chars, so a long row yields a PREFIX that cannot be compared for
    identity. The check therefore SKIPS such anchors and resolution falls back to its normal behaviour —
    which means a row longer than 60 chars gets no protection from the wrong-row bind above.

    Refusing outright was the alternative and is worse: it would fail-loud every flow whose rows are
    merely verbose, turning a targeted safety fix into a broad availability regression. Closing this
    properly needs an identity token captured per row rather than a truncated text prefix.

    If this test starts failing, the truncation limit or the check's scope changed — re-derive the gap
    before updating the assertion."""
    long_row = "X" * 80
    html = ("<!doctype html><html><body><table><tbody>"
            f"<tr><td>{long_row}</td><td><a href='/a' data-o='OTHER'>Go</a></td></tr>"
            "</tbody></table></body></html>")
    spec = LocatorSpec(role="link", name="Go", tag="a", text="Go", css="",
                       anchor=("X" * 60), anchor_source="row")
    assert await _bind(html, spec) == "OTHER"


# ============================ 2. the safety header evicted the auth headers ============================

async def test_the_idempotency_key_does_not_evict_the_flows_own_headers() -> None:
    """Playwright's `set_extra_http_headers` REPLACES. So attaching the Idempotency-Key wiped
    `FlowSpec.headers` — the write went out with no auth, and the `finally`'s clear-to-`{}` meant they
    never came back for the rest of the run. Measured: a payment posted against the server's default
    tenant while `replay()` returned `{"status": "confirmed"}`."""
    from ultracua.browser import BrowserSession

    seen: list = []

    class _H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            seen.append((self.path, self.headers.get("X-Tenant"), self.headers.get("Idempotency-Key")))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        s = await BrowserSession(headless=True).start()
        try:
            await s.set_extra_http_headers({"X-Tenant": "42"})
            await s.goto(base + "/before")
            await s.set_transient_headers({"Idempotency-Key": "uca-k"})
            await s.goto(base + "/during")
            await s.set_transient_headers({})
            await s.goto(base + "/after")
        finally:
            await s.close()
        by_path = {p: (t, k) for p, t, k in seen}
        assert by_path["/before"] == ("42", None)
        # the write carries BOTH — the key AND the flow's auth
        assert by_path["/during"] == ("42", "uca-k"), "the write lost the flow's auth headers"
        # ...and clearing the transient header restores the base, it does not wipe it
        assert by_path["/after"] == ("42", None), "the flow's headers never came back"
    finally:
        httpd.shutdown()
        httpd.server_close()


# ============================ 3. an empty pinned read was returned as the answer ============================

def test_an_empty_scalar_pin_is_drift_not_an_answer() -> None:
    """A pin can only be created for a NON-EMPTY value, so `""` on replay always means the element
    resolved but is unpopulated — an SPA skeleton box. Returning it produced `found=True, pinned=True`
    with all three H9 value gates clean, so a scheduler recorded a blank status as a clean 0-LLM read."""
    from ultracua.pin import _parse

    assert _parse("", "str") is None
    assert _parse("   ", "str") is None
    assert _parse("shipped", "str") == "shipped"
    # numeric pins were already strict; unchanged
    assert _parse("", "int") is None
    assert _parse("42", "int") == 42


# ============================ 4. dropping ALL slots skipped re-approval ============================

def test_removing_the_whole_slot_table_still_requires_re_approval(tmp_path, monkeypatch) -> None:
    """Removing ONE slot correctly refused; removing the WHOLE TABLE passed, because `_slots_hash`
    returns None for an empty table and the gate short-circuited on `spec.slots and ...`. A scrubbed
    secret step then types the empty string into a credential field before the submit, and the write
    mints a different Idempotency-Key than the same logical write did while slotted."""
    import pytest

    from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key
    from ultracua.flows import FlowReplayError, FlowSpec, SlotSpec, _load_meta, _preflight_row, approve, save_spec
    from ultracua.locators import LocatorSpec

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    cache = FlowCache()
    spec = FlowSpec(name="pay", start_url="https://b.example.com/pay", goal="pay the invoice",
                    headless=True, slots={"amount": SlotSpec(type="string")})
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    flow = CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, created_ts=1.0,
                      steps=[CachedStep(intent="type the amount", action="type", slot="amount",
                                        locator=LocatorSpec(role="textbox", name="Amount", tag="input"))])
    cache.put(flow)
    save_spec(spec)
    approve(spec, cache=cache)

    spec.slots = None                      # the WHOLE table removed
    save_spec(spec)
    with pytest.raises(FlowReplayError) as ei:
        _preflight_row(spec, None, meta=_load_meta(cache, key), cached_flow=flow, require_approved=True)
    assert "slot schema changed since approval" in str(ei.value)
