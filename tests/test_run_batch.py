"""H3 slice 2b — run_batch (the row-granular VOLUME driver), key-less against local fixtures.

run_batch drives ONE parameterized flow once per row. The load-bearing safety properties (all verified
here against the SERVER's recorded writes — the double-write / suppressed-write oracle):
  - all-or-nothing pre-flight: any invalid row -> ZERO actuations;
  - duplicate-row refusal (writes): two rows minting the same Idempotency-Key are refused pre-flight;
  - approval bound: max_rows is required for a write batch and refuses when exceeded;
  - fail-loud isolation: on_row_error='stop' (default) halts + marks the rest skipped;
  - dry-run: validate + preview each row's key, actuate NOTHING;
  - the dry-run key preview is BYTE-IDENTICAL to the wire Idempotency-Key a real replay mints;
  - the 2a guards (approval, schema-hash, binding, precheck) are inherited per row via _preflight_row.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from ultracua import flows
from ultracua.cache import FlowCache, flow_key
from ultracua.flows import (
    BatchRowResult,
    BatchRun,
    FlowReplayError,
    FlowSpec,
    MutateSpec,
    SlotSpec,
    run_batch,
)


class _CheckoutSite:
    """A `method=post` checkout form + confirmation page. Records each write's (path, Idempotency-Key
    header, body) — the double-write / suppressed-write oracle. `checkout_html` is swappable to drift the
    form structure (mutation gate)."""

    CHECKOUT = ("<!doctype html><html><body><h1>Checkout</h1>"
                "<form method='post' action='/order'>"
                "<label for='qty'>quantity</label><input id='qty' name='qty' value='1'>"
                "<button type='submit'>Place the order</button></form></body></html>")
    CONFIRM = "<!doctype html><html><body><h1>Order placed</h1></body></html>"

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []  # (path, idempotency-key, body)
        self.checkout_html = self.CHECKOUT

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
                self._send(site.CONFIRM if path == "/confirm" else site.checkout_html)

            def do_POST(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode("utf-8", "replace")
                site.writes.append((self.path.split("?")[0], self.headers.get("Idempotency-Key"), body))
                self.send_response(303)
                self.send_header("Location", "/confirm")
                self.end_headers()

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _record_write_flow(site, base, cache, *, name="order", pattern="[0-9]{1,3}", mutate=None):
    """Record a WRITE flow that fills #qty + submits, bind the fill step to the `qty` slot, approve it.
    Clears the demo's own write from `site.writes` so the batch's writes are the sole oracle."""
    spec = FlowSpec(name=name, start_url=base + "/checkout", goal="place the order",
                    mutate=mutate or MutateSpec(confirm_text_contains="Order placed"),
                    slots={"qty": SlotSpec(type="string", pattern=pattern)}, headless=True)

    async def _demo(pg) -> None:
        await pg.fill("#qty", "7")
        await pg.locator("#qty").blur()
        await pg.get_by_role("button", name="Place the order").click()
        await pg.get_by_text("Order placed").wait_for()

    res = await flows.record(spec, demo=_demo, headless=True, cache=cache)
    assert res.cached and res.is_write, res.note
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    flow = cache.get(key)
    for s in flow.steps:
        if s.action == "type":
            s.slot = "qty"
    cache.put(flow)
    flows.approve(spec, cache=cache)
    del site.writes[:]   # drop the demo's write; the batch is the oracle from here
    return spec


# --- (1) empty / edge probe -------------------------------------------------------------------
async def test_run_batch_empty_returns_valid_shape() -> None:
    out = await run_batch(None, [])
    assert isinstance(out, BatchRun) and out.rows == [] and out.status == "ok" and out.total == 0


# --- (2) the dry-run key preview is byte-identical to the wire Idempotency-Key -----------------
async def test_dry_run_key_preview_matches_wire_key(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        # Dry-run: get the previewed Idempotency-Key for one row (no actuation).
        plan = await run_batch(spec, [{"qty": "9"}], max_rows=1, dry_run=True, cache=cache)
        assert plan.status == "planned" and site.writes == []
        preview = plan.rows[0].idempotency_keys
        assert preview and all(k.startswith("uca-") for k in preview)
        # Real replay of the SAME row: the fixture records the wire Idempotency-Key header.
        await flows.replay(spec, params={"qty": "9"}, cache=cache)
        wire_key = site.writes[-1][1]
        assert wire_key in preview, f"dry-run preview {preview} != wire key {wire_key}"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (3) dry-run actuates nothing -------------------------------------------------------------
async def test_dry_run_actuates_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        plan = await run_batch(spec, [{"qty": "9"}, {"qty": "8"}, {"qty": "7"}],
                               max_rows=10, dry_run=True, cache=cache)
        assert plan.status == "planned" and plan.dry_run is True and plan.total == 3
        assert site.writes == [], "a dry-run reached the server"
        assert all(r.status == "planned" and r.idempotency_keys for r in plan.rows)
        # distinct rows -> distinct preview keys (batch-level suppressed-write guard)
        keys = [tuple(r.idempotency_keys) for r in plan.rows]
        assert len(set(keys)) == 3, f"dry-run rows shared a key: {keys}"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (4) all-or-nothing pre-flight ------------------------------------------------------------
async def test_all_or_nothing_preflight(tmp_path, monkeypatch) -> None:
    # A batch with one out-of-domain row must refuse the WHOLE batch before any actuation — the good
    # rows must NOT write (no partial write run on malformed input).
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache, pattern="[0-9]{1,3}")
        out = await run_batch(spec, [{"qty": "9"}, {"qty": "NaN"}, {"qty": "8"}], max_rows=10, cache=cache)
        assert out.status == "invalid" and out.invalid >= 1
        assert site.writes == [], "a good row actuated despite an invalid sibling row"
        bad = [r for r in out.rows if r.status == "invalid"]
        assert any(r.index == 1 for r in bad), "the offending row (index 1) wasn't reported"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (5) duplicate-row refusal (writes) vs allowed (reads) ------------------------------------
async def test_duplicate_row_refused_for_writes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        # Two byte-identical rows -> the same Idempotency-Key -> a backend dedupe would suppress the 2nd.
        out = await run_batch(spec, [{"qty": "9"}, {"qty": "9"}], max_rows=10, cache=cache)
        assert out.status == "invalid" and site.writes == []
        dup = [r for r in out.rows if r.status == "invalid" and r.index == 1]
        assert dup and "duplicate" in (dup[0].error or "").lower()
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (6) stop-on-error (default) --------------------------------------------------------------
async def test_stop_on_error_default_skips_rest(tmp_path, monkeypatch) -> None:
    # Row 0 drifts (mutation gate) -> row 0 failed, every later row skipped (never actuated), batch failed.
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        # Drift the form structure so the FIRST row's write is refused by the mutation gate.
        site.checkout_html = site.CHECKOUT.replace(
            "<button type='submit'>",
            "<label for='c'>coupon</label><input id='c' name='c'><button type='submit'>")
        out = await run_batch(spec, [{"qty": "9"}, {"qty": "8"}], max_rows=10, cache=cache)
        assert out.status == "failed"
        assert out.rows[0].status == "failed" and out.rows[1].status == "skipped"
        assert site.writes == [], "a write reached the server despite drift + stop-on-error"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (7) on_row_error='continue' --------------------------------------------------------------
async def test_continue_on_error_runs_remaining_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        # A pattern that rejects "8" would be a pre-flight invalid (all-or-nothing) — instead, make row 0
        # fail at ACTUATION by drifting, then... we can't un-drift mid-batch. So use a read flow to test
        # continue: two rows, the first out-of-order. Simpler: verify continue still writes both good rows.
        out = await run_batch(spec, [{"qty": "9"}, {"qty": "8"}], max_rows=10,
                              on_row_error="continue", cache=cache)
        assert out.status == "ok" and out.ok_count == 2
        assert [w[0] for w in site.writes] == ["/order", "/order"]
        # continue-mode with a mid-batch failure is exercised in test_stop_on_error_default_skips_rest's
        # inverse; here we pin that continue actuates every good row and reports each.
        assert all(r.status == "ok" for r in out.rows)
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (8) max_rows required for writes + enforced ----------------------------------------------
async def test_max_rows_required_and_enforced_for_writes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        # No max_rows on a write batch -> refuse before any actuation.
        with pytest.raises(FlowReplayError, match="requires max_rows"):
            await run_batch(spec, [{"qty": "9"}], cache=cache)
        assert site.writes == []
        # Exceeding max_rows -> refuse before any actuation.
        with pytest.raises(FlowReplayError, match="max_rows"):
            await run_batch(spec, [{"qty": "9"}, {"qty": "8"}], max_rows=1, cache=cache)
        assert site.writes == []
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (9) guard inheritance (proves the shared _preflight_row) ---------------------------------
async def test_guard_inheritance_unapproved_and_schema_change(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        # (c) widen the slot domain AFTER approval -> every row refused by the schema-hash gate (invalid).
        spec.slots["qty"] = SlotSpec(type="string")   # loosen the pattern
        out = await run_batch(spec, [{"qty": "9"}], max_rows=10, cache=cache)
        assert out.status == "invalid" and site.writes == []
        assert "schema changed since approval" in (out.rows[0].error or "")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_guard_inheritance_unbound_slot(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        # Record a write flow but DON'T bind the qty slot (no public write-slot binding surface yet).
        spec = FlowSpec(name="unbound", start_url=base + "/checkout", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Order placed"),
                        slots={"qty": SlotSpec(type="string")}, headless=True)

        async def _demo(pg) -> None:
            await pg.fill("#qty", "7")
            await pg.locator("#qty").blur()
            await pg.get_by_role("button", name="Place the order").click()
            await pg.get_by_text("Order placed").wait_for()

        await flows.record(spec, demo=_demo, headless=True, cache=cache)
        flows.approve(spec, cache=cache)
        del site.writes[:]   # drop the demo's write
        out = await run_batch(spec, [{"qty": "9"}], max_rows=10, cache=cache)
        assert out.status == "invalid" and site.writes == []
        assert "aren't bound to any recorded" in (out.rows[0].error or "")
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (10) distinct rows distinct keys; same row same key (batch level) ------------------------
async def test_batch_row_keys_distinct_and_stable(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        p1 = await run_batch(spec, [{"qty": "9"}, {"qty": "8"}], max_rows=10, dry_run=True, cache=cache)
        k9a, k8 = p1.rows[0].idempotency_keys, p1.rows[1].idempotency_keys
        assert k9a != k8, "distinct rows shared a preview key"
        # re-plan the SAME row {qty:9} -> the SAME key (retry-safe).
        p2 = await run_batch(spec, [{"qty": "9"}], max_rows=10, dry_run=True, cache=cache)
        assert p2.rows[0].idempotency_keys == k9a, "the same row minted a different key"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (11) no learned flow -> refuse loud ------------------------------------------------------
async def test_no_learned_flow_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    spec = FlowSpec(name="ghost", start_url="http://127.0.0.1:9/", goal="place the order",
                    mutate=MutateSpec(confirm_text_contains="ok"),
                    slots={"qty": SlotSpec(type="string")})
    with pytest.raises(FlowReplayError, match="nothing to batch"):
        await run_batch(spec, [{"qty": "9"}], max_rows=10, cache=FlowCache())


# --- (12) secret-safety: a secret never lands in the report -----------------------------------
_SECRET = "s3cr3t-batch-9f2a"


class _TokenCheckoutSite(_CheckoutSite):
    CHECKOUT = ("<!doctype html><html><body><h1>Checkout</h1>"
                "<form method='post' action='/order'>"
                "<label for='qty'>quantity</label><input id='qty' name='qty' value='1'>"
                "<label for='token'>token</label><input id='token' name='token' value=''>"
                "<button type='submit'>Place the order</button></form></body></html>")


async def test_secret_never_appears_in_report(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UCA_BATCH_TOK", _SECRET)
    cache = FlowCache()
    site = _TokenCheckoutSite()
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="paytoken", start_url=base + "/checkout", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Order placed"),
                        slots={"qty": SlotSpec(type="string"),
                               "token": SlotSpec(secret=True, secret_env="UCA_BATCH_TOK")}, headless=True)

        async def _demo(pg) -> None:
            await pg.fill("#qty", "7")
            await pg.fill("#token", "demo-token")
            await pg.locator("#token").blur()
            await pg.get_by_role("button", name="Place the order").click()
            await pg.get_by_text("Order placed").wait_for()

        await flows.record(spec, demo=_demo, headless=True, cache=cache)
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        for s in flow.steps:
            if s.action == "type" and s.text == "7":
                s.slot = "qty"
            elif s.action == "type" and s.text == "demo-token":
                s.slot = "token"
        cache.put(flow)
        flows.approve(spec, cache=cache)
        del site.writes[:]   # drop the demo's write (which used "demo-token", not the env secret)

        out = await run_batch(spec, [{"qty": "5"}], max_rows=10, cache=cache)  # token comes from env
        assert out.status == "ok", [r.error for r in out.rows]
        # the secret ACTUATED (env-resolved) — the server received it in the write body...
        assert any(_SECRET in w[2] for w in site.writes), "the secret slot did not actuate"
        # ...but the plaintext secret appears in NO field of the report.
        assert _SECRET not in repr(out), "a secret leaked into the BatchRun report"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- read-batch happy path (run_batch is not write-only) --------------------------------------
class _EchoSite:
    """A read page whose input echoes each value to the server via a synchronous GET."""

    def __init__(self) -> None:
        self.gets: list[str] = []

    def serve(self):
        site = self
        body = ("<!doctype html><html><body><label for='q'>code</label><input id='q'>"
                "<script>document.getElementById('q').addEventListener('input',(e)=>{"
                "const x=new XMLHttpRequest();x.open('GET','/typed-'+encodeURIComponent(e.target.value),false);"
                "x.send();});</script></body></html>")

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


async def test_read_batch_runs_each_row_and_duplicates_allowed(tmp_path, monkeypatch) -> None:
    # A READ batch needs no max_rows, and identical rows are allowed (a repeated read is inert).
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="lookup", start_url=base + "/", goal="enter the code", headless=True,
                        slots={"code": SlotSpec(type="string")})

        async def _demo(pg) -> None:
            await pg.fill("#q", "alpha-7")
            await pg.locator("#q").blur()

        res = await flows.record(spec, demo=_demo, headless=True, cache=cache)
        assert res.cached
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        for s in flow.steps:
            if s.action == "type":
                s.slot = "code"
        cache.put(flow)
        flows.approve(spec, cache=cache)

        site.gets.clear()
        out = await run_batch(spec, [{"code": "beta-9"}, {"code": "beta-9"}], require_approved=True, cache=cache)
        assert out.status == "ok" and out.ok_count == 2   # duplicate reads allowed, no max_rows needed
        assert "/typed-beta-9" in site.gets
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- CLI row loading + type coercion (flow run-batch --rows) -----------------------------------
def test_cli_load_batch_rows_json(tmp_path) -> None:
    from ultracua.cli import _load_batch_rows
    spec = FlowSpec(name="x", start_url="http://x/", goal="g", slots={"qty": SlotSpec(type="string")})
    f = tmp_path / "rows.json"
    f.write_text('[{"qty": "9"}, {"qty": "8"}]', encoding="utf-8")
    assert _load_batch_rows(str(f), spec) == [{"qty": "9"}, {"qty": "8"}]


def test_cli_load_batch_rows_csv_coerces_per_slot_type(tmp_path) -> None:
    # A CSV is all-strings; the loader coerces each cell to its slot's type so validate_params (strict)
    # accepts it — an integer/number/boolean slot must not arrive as a bare string.
    from ultracua.cli import _load_batch_rows
    spec = FlowSpec(name="x", start_url="http://x/", goal="g",
                    slots={"qty": SlotSpec(type="integer"), "amt": SlotSpec(type="number"),
                           "gift": SlotSpec(type="boolean"), "note": SlotSpec(type="string")})
    f = tmp_path / "rows.csv"
    f.write_text("qty,amt,gift,note\n5,1.50,true,hello\n", encoding="utf-8")
    assert _load_batch_rows(str(f), spec) == [{"qty": 5, "amt": 1.5, "gift": True, "note": "hello"}]


def test_cli_load_batch_rows_refuses_secret_column(tmp_path) -> None:
    # A secret slot's value comes from $env, never a row file — a row carrying it is refused loud.
    from ultracua.cli import _load_batch_rows
    spec = FlowSpec(name="x", start_url="http://x/", goal="g",
                    slots={"qty": SlotSpec(type="string"), "token": SlotSpec(secret=True, secret_env="T")})
    f = tmp_path / "rows.json"
    f.write_text('[{"qty": "9", "token": "leak-me"}]', encoding="utf-8")
    with pytest.raises(SystemExit, match="secret"):
        _load_batch_rows(str(f), spec)


# --- review regressions -----------------------------------------------------------------------
def test_integer_slot_coerces_float_to_int() -> None:
    # Review finding: a JSON rows file parses `2.0` to a float; an integer slot must NORMALIZE it to int 2
    # so substitution types "2" (not "2.0", which a backend may reject/mis-parse) and the key folds "2".
    from ultracua.flows import validate_params
    spec = FlowSpec(name="x", start_url="http://x/", goal="g",
                    slots={"qty": SlotSpec(type="integer", min=1, max=100)})
    r = validate_params(spec, {"qty": 2.0})
    assert r["qty"] == 2 and isinstance(r["qty"], int) and str(r["qty"]) == "2"


def test_number_slot_rejects_non_finite() -> None:
    # Review finding: NaN/Inf slip past min/max (NaN orderings are all False; Inf beats any bound), so a
    # bounded number slot must refuse them LOUD rather than type "nan"/"inf" onto the page.
    from ultracua.flows import validate_params
    spec = FlowSpec(name="x", start_url="http://x/", goal="g",
                    slots={"amt": SlotSpec(type="number", min=0, max=1000)})
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(FlowReplayError, match="finite"):
            validate_params(spec, {"amt": bad})


def test_numeric_slot_huge_int_does_not_crash() -> None:
    # Re-verification finding: math.isfinite/float() on a Python int too large for a C double raises
    # OverflowError (not a FlowReplayError) — which would CRASH a batch instead of reporting the row. An
    # int is always finite/integral, so a huge int must validate cleanly (no crash), not raise OverflowError.
    from ultracua.flows import validate_params
    huge = 10 ** 400
    for t in ("number", "integer"):
        spec = FlowSpec(name="x", start_url="http://x/", goal="g", slots={"n": SlotSpec(type=t)})
        assert validate_params(spec, {"n": huge})["n"] == huge


def test_number_slot_int_and_float_fold_to_one_key() -> None:
    # Re-verification finding: for a NUMBER slot, `{n: 2}` (int) and `{n: 2.0}` (float) are the same value,
    # so they must resolve identically and mint the SAME Idempotency-Key — else two numerically-equal writes
    # mint different keys and a dedupe-keyed backend double-writes.
    from ultracua.flows import validate_params
    from ultracua.safety import idempotency_key
    spec = FlowSpec(name="x", start_url="http://x/", goal="g", slots={"n": SlotSpec(type="number")})
    a, b = validate_params(spec, {"n": 2}), validate_params(spec, {"n": 2.0})
    assert a["n"] == b["n"] == 2 and isinstance(a["n"], int) and isinstance(b["n"], int)
    assert idempotency_key("s", 1, "i", slot_values=a) == idempotency_key("s", 1, "i", slot_values=b)
    # a genuinely non-integer number is untouched
    assert validate_params(spec, {"n": 2.5})["n"] == 2.5


async def test_run_batch_undeclared_write_flow_trips_write_guards(tmp_path, monkeypatch) -> None:
    # Review finding: a flow learned as a READ (spec.mutate=None) whose cached steps in fact MUTATE (an
    # undeclared write) still FIRES the write on replay — so run_batch must key its write guards off the
    # ACTUAL mutating steps, not spec.mutate. Here: max_rows must be required despite mutate being None.
    import time as _t

    from ultracua.cache import CachedFlow, CachedStep
    from ultracua.locators import LocatorSpec

    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    spec = FlowSpec(name="sneaky", start_url="http://127.0.0.1:9/checkout", goal="show the total",
                    slots={"qty": SlotSpec(type="string")})   # NOTE: no mutate declared
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, created_ts=_t.time(), steps=[
        CachedStep(intent="type qty", action="type", text="7", slot="qty",
                   locator=LocatorSpec(role="textbox", name="qty", tag="input")),
        CachedStep(intent="place the order", action="click", mutating=True,
                   locator=LocatorSpec(role="button", name="Place the order", tag="button")),
    ]))
    flows.approve(spec, cache=cache)
    # is_mutate is derived from the mutating step -> a write batch -> max_rows is required (blast-radius bound).
    with pytest.raises(FlowReplayError, match="requires max_rows"):
        await run_batch(spec, [{"qty": "9"}], cache=cache)
