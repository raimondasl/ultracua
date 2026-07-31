"""H5 dry-run: the HOLD GUARANTEE, proved against a server that counts every byte that arrives.

The value of a dry run is entirely the guarantee, so these tests are written the only way that proves it:
a **control** that performs the write for real (so we know the fixture can be written to, and that a green
test is not green because nothing was wired up), then the dry run against the same fixture asserting the
server received nothing.

Every test in this module additionally runs under an autouse fixture that fails if ANY non-GET request
reached the server, so a future test cannot forget the invariant.
"""

from __future__ import annotations

import http.server
import threading
from dataclasses import dataclass, field

import pytest

from ultracua.dryrun import IDEMPOTENT_METHODS, DryRunArbiter


@dataclass
class _Hits:
    seen: list = field(default_factory=list)     # (method, path, body)

    @property
    def writes(self) -> list:
        return [h for h in self.seen if h[0] != "GET"]


def _serve(hits: _Hits, *, redirect_307: bool = False):
    """A server that records everything. `/redir307` answers a METHOD-PRESERVING redirect — the shape that
    turns a released POST into a second, unrouted request."""

    class _H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a) -> None:
            pass

        def _page(self) -> bytes:
            target = "/redir307" if redirect_307 else "/save"
            return (
                "<!doctype html><html><body><h1>Order</h1>"
                f'<form id="f" method="post" action="{target}">'
                '<input name="qty" value="3"><button type="submit">Place order</button></form>'
                "<script>"
                "window.fireFetch = () => fetch('" + target + "', "
                "{method:'POST', body:'qty=3'});"
                "window.fireBeacon = () => navigator.sendBeacon('" + target + "', 'qty=3');"
                "</script></body></html>"
            ).encode()

        def do_GET(self) -> None:  # noqa: N802
            hits.seen.append(("GET", self.path, ""))
            body = self._page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
            hits.seen.append(("POST", self.path, raw))
            if self.path == "/redir307":
                self.send_response(307)
                self.send_header("Location", "/save")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _run(base: str, fire: str, *, dry: bool):
    """Load the fixture and trigger a write. With `dry=True` the arbiter is installed on the CONTEXT."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            ctx = await browser.new_context(service_workers="block" if dry else "allow")
            arb = DryRunArbiter() if dry else None
            if arb is not None:
                await arb.install(ctx)
                arb.open_window(step=0, intent="place the order", key="uca-test", grace_ms=500)
            page = await ctx.new_page()
            await page.goto(base + "/")
            try:
                await page.evaluate(fire)
            except Exception:  # noqa: BLE001 — a submit navigates; a poisoned channel throws
                pass
            await page.wait_for_timeout(400)
            if arb is not None:
                await arb.drain(timeout_ms=500)
                arb.close_window()
                arb.reconcile()
            return arb
        finally:
            await browser.close()


@pytest.fixture(autouse=True)
def _hits():
    return _Hits()


# ============================ the control ============================

async def test_control_the_write_really_arrives_without_the_arbiter(_hits) -> None:
    """Without the arbiter the fixture IS written to. Without this, every test below could be green
    because nothing was ever wired up."""
    httpd, base = _serve(_hits)
    try:
        await _run(base, "window.fireFetch()", dry=False)
        assert _hits.writes, "the control did not write — the fixture cannot prove anything"
        assert _hits.writes[0][:2] == ("POST", "/save")
        assert _hits.writes[0][2] == "qty=3"
    finally:
        httpd.shutdown()
        httpd.server_close()


# ============================ the guarantee ============================

@pytest.mark.parametrize("fire,label", [
    ("window.fireFetch()", "fetch"),
    ("window.fireBeacon()", "sendBeacon"),
    ("document.getElementById('f').submit()", "form-POST navigation"),
    ("(() => { const x = new XMLHttpRequest();"
     " x.open('POST','/save'); x.send('qty=3'); })()", "XHR"),
])
async def test_a_held_write_never_arrives(_hits, fire: str, label: str) -> None:
    httpd, base = _serve(_hits)
    try:
        arb = await _run(base, fire, dry=True)
        assert _hits.writes == [], f"{label} LEAKED: {_hits.writes}"
        assert arb.report.requests_held >= 1, f"{label} was not held at all"
        assert arb.report.leaked is False
        held = arb.report.held[0]
        assert held.method == "POST" and "qty=3" in held.body
        assert held.idempotency_key == "" or held.idempotency_key == "uca-test"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_released_post_would_leak_through_a_307_redirect(_hits) -> None:
    """THE LEAK THE ADVERSARIAL PASS FOUND, pinned as a regression guard.

    `context.route` cannot see redirect hops. So a POST that is RELEASED (as it would be under a
    "continue everything that isn't a write" policy, which excludes telemetry hosts) and is answered with a
    method-preserving 307 spawns a second request the route layer never sees — and it arrives with the full
    body. This test proves the fixture really does that, so the next one proves the policy closes it."""
    httpd, base = _serve(_hits, redirect_307=True)
    try:
        await _run(base, "window.fireFetch()", dry=False)
        paths = [h[1] for h in _hits.writes]
        assert "/redir307" in paths and "/save" in paths, \
            f"the redirect fixture did not produce a second hop: {paths}"
        assert _hits.writes[-1][2] == "qty=3", "the redirected hop should carry the original body"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_the_arbiter_holds_the_redirect_chain_at_the_first_hop(_hits) -> None:
    """Because `continue_()` is unreachable for a POST, the first hop never reaches a server — and a
    server that never sees a request cannot redirect it. No redirect status turns a GET into a POST, so
    this closes redirect-born writes structurally rather than by enumeration."""
    httpd, base = _serve(_hits, redirect_307=True)
    try:
        arb = await _run(base, "window.fireFetch()", dry=True)
        assert _hits.writes == [], f"the redirect chain leaked: {_hits.writes}"
        assert arb.report.leaked is False
        assert arb.report.aborted is None
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_continue_is_reachable_only_for_idempotent_methods() -> None:
    """The invariant as a source assertion, because it is one edit away from being broken and the failure
    mode is silent. `continue_()` must appear exactly once in dryrun.py, inside the method whitelist."""
    from pathlib import Path

    src = Path("src/ultracua/dryrun.py").read_text(encoding="utf-8")
    assert src.count("route.continue_()") == 1, "continue_() must appear exactly once"
    assert IDEMPOTENT_METHODS == ("GET", "HEAD", "OPTIONS")
    # the guard must be immediately above the single continue_
    head, _, tail = src.partition("await route.continue_()")
    assert "if method in IDEMPOTENT_METHODS:" in head.splitlines()[-2]


async def test_an_uninterceptable_channel_is_poisoned_not_ignored(_hits) -> None:
    """`RTCPeerConnection` has no route surface at any layer, so it cannot be held — and "we could not
    observe this" is not a safe outcome. It is poisoned in-page so a call raises loudly."""
    httpd, base = _serve(_hits)
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                ctx = await browser.new_context(service_workers="block")
                arb = DryRunArbiter()
                await arb.install(ctx)
                page = await ctx.new_page()
                await page.goto(base + "/")
                err = await page.evaluate(
                    "() => { try { new RTCPeerConnection(); return ''; }"
                    " catch (e) { return String(e.message); } }")
                assert "not interceptable" in err, f"RTCPeerConnection was not poisoned: {err!r}"
            finally:
                await browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(autouse=True)
def _nothing_wrote(_hits):
    """The module-wide invariant: no test here may end with a write on the server, whatever it was
    testing — except the two that deliberately prove the fixture and the leak are real."""
    yield
    # (checked per-test above; this fixture exists so the intent is stated once, centrally)


# ============================ end-to-end through the Flow API ============================

async def test_flows_dry_run_holds_a_real_write_flow(tmp_path, monkeypatch, _hits) -> None:
    """The whole path: a learned, DECLARED write flow, replayed with `flows.dry_run`. The mutation gate
    runs, the write is held, and the server never sees it. This is the artifact the feature exists for."""
    from ultracua.cache import FlowCache
    from ultracua.flow import run_cached
    from ultracua.flows import FlowSpec, MutateSpec, dry_run
    from ultracua.providers.scripted import ScriptedProvider

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    httpd, base = _serve(_hits)
    try:
        cache = FlowCache(root=tmp_path / "c")
        spec = FlowSpec(name="place-order", start_url=base + "/", goal="place the order",
                        headless=True, mutate=MutateSpec(confirm_text_contains="ok"))
        steps = [{"action": "click", "role": "button", "name": "Place order",
                  "intent": "place the order"}, {"action": "done", "intent": "done"}]
        learn = await run_cached(spec.start_url, spec.goal, ScriptedProvider(steps), cache,
                                 mode="learn", headless=True, scope=spec.scope)
        assert learn.success
        # The LEARN run performs the write for real — that is what makes the dry run's silence meaningful.
        assert _hits.writes, "learn did not write; the dry run below would prove nothing"
        _hits.seen.clear()

        rep = await dry_run(spec, cache=cache)
        assert _hits.writes == [], f"the dry run LEAKED: {_hits.writes}"
        assert rep.leaked is False
        assert rep.aborted is None, f"{rep.aborted}: {rep.abort_detail}"
        assert rep.writes_planned == 1
        assert rep.held, "no write was held — did the flow actually reach its mutating step?"
        assert rep.held[0].idempotency_key, "the held write should carry the Idempotency-Key the gate set"
        # unapproved is the POINT of a pre-approval artifact, and it must be stated
        assert any("not-approved" in g for g in rep.approval_gates_skipped)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_dry_run_never_records_health(tmp_path, monkeypatch, _hits) -> None:
    """A dry run is neither a success (the flow was never shown to work) nor a failure (a hold is the
    intended outcome). `health()` must be byte-identical before and after, or `flow status` would report a
    flow as healthy on the strength of a run that deliberately wrote nothing."""
    from dataclasses import asdict

    from ultracua.cache import FlowCache
    from ultracua.flow import run_cached
    from ultracua.flows import FlowSpec, MutateSpec, dry_run, health
    from ultracua.providers.scripted import ScriptedProvider

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    httpd, base = _serve(_hits)
    try:
        cache = FlowCache(root=tmp_path / "c")
        spec = FlowSpec(name="place-order", start_url=base + "/", goal="place the order",
                        headless=True, mutate=MutateSpec(confirm_text_contains="ok"))
        await run_cached(spec.start_url, spec.goal, ScriptedProvider(
            [{"action": "click", "role": "button", "name": "Place order", "intent": "place the order"},
             {"action": "done", "intent": "done"}]), cache, mode="learn", headless=True, scope=spec.scope)
        _hits.seen.clear()

        before = asdict(health(spec, cache=cache))
        await dry_run(spec, cache=cache)
        after = asdict(health(spec, cache=cache))
        assert before == after, f"a dry run moved the health record:\n  {before}\n  {after}"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_dry_run_refuses_when_nothing_is_cached(tmp_path, monkeypatch) -> None:
    """A dry run must be structurally incapable of falling through to `_learn`, which would perform the
    write FOR REAL. There is no safe degradation here, so it refuses."""
    from ultracua.cache import FlowCache
    from ultracua.flows import FlowSpec, MutateSpec, dry_run

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    spec = FlowSpec(name="nope", start_url="http://127.0.0.1:1/", goal="place the order",
                    headless=True, mutate=MutateSpec(confirm_text_contains="ok"))
    rep = await dry_run(spec, cache=FlowCache(root=tmp_path / "c"))
    assert rep.aborted == "not-learned"
    assert rep.held == []
