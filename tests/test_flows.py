"""Flow spec + reusable extraction: define a task once, learn it, replay it returning data.

Uses a local two-page fixture + a scripted agent provider + a MockClient extraction router — no
live LLM, no network. See ROADMAP.md / src/ultracua/flows.py.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

from ultracua.cache import FlowCache, flow_key
from ultracua.extract import extract
from ultracua.flows import (
    FlowReplayError,
    FlowSpec,
    LoginSpec,
    MutateSpec,
    _load_meta,
    _save_meta,
    approve,
    health,
    learn,
    list_specs,
    load_spec,
    refresh_auth,
    replay,
    run_all,
    save_spec,
    unapprove,
)
from ultracua.llm.base import Router, Tier
from ultracua.llm.mock import MockClient
from ultracua.types import Action


def _extract_router(*datas) -> Router:
    """A Router whose successive extraction calls return {found: True, data: <each>}."""
    mc = MockClient(actions=[{"found": True, "data": d} for d in datas], tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


class _ClickFirstLink:
    """Scripted agent: click the first link once (navigating), then done."""

    def __init__(self) -> None:
        self.clicked = False

    async def decide(self, goal, obs, history):
        if not self.clicked:
            for el in obs.elements:
                if el.role == "link":
                    self.clicked = True
                    return Action(action="click", intent="open the answer page", ref=el.ref), None
        return Action(action="done", intent="done"), None


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a) -> None:
        pass


def _serve(directory: Path):
    handler = functools.partial(_QuietHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# --- extraction (pure, MockClient) ------------------------------------------------------------
async def test_extract_returns_structured_data() -> None:
    ex = await extract(_extract_router([{"month": "Jan", "count": 12}]), "monthly counts", "Jan: 12")
    assert ex.found and ex.data == [{"month": "Jan", "count": 12}]


async def test_extract_unwraps_spurious_nesting() -> None:
    ex = await extract(_extract_router([["000000299"]]), "the order id", "order 000000299")
    assert ex.data == ["000000299"]


async def test_extract_empty_page_is_not_found() -> None:
    ex = await extract(_extract_router("x"), "anything", "")
    assert not ex.found and ex.error


async def test_tool_extract_returns_forced_tool_input() -> None:
    from ultracua.extract import tool_extract
    from ultracua.llm.types import ToolDef

    mc = MockClient(actions=[{"x": 1, "y": "z"}], tool_name="grab")
    router = Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))
    tool = ToolDef(name="grab", description="d", input_schema={"type": "object"}, strict=False)
    assert await tool_extract(router, system="s", tool=tool, user_text="u") == {"x": 1, "y": "z"}


# --- flow learn -> replay (browser + scripted provider) ---------------------------------------
def _write_fixture(d: Path) -> None:
    (d / "page1.html").write_text(
        "<html><body><h1>Home</h1><a href='page2.html'>see the answer</a></body></html>", encoding="utf-8"
    )
    (d / "page2.html").write_text(
        "<html><body><h1>Answer</h1><p>The answer is 42.</p></body></html>", encoding="utf-8"
    )


async def test_flow_learn_then_replay_returns_data(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="answer", start_url=f"{base}/page1.html",
                    goal="open the answer page", extract="the answer number", headless=True)
    try:
        res = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        assert res.cached and res.found and res.data == 42 and res.steps  # learned a replayable flow

        # replay reproduces the navigation at 0 LLM and returns the extracted data
        data = await replay(spec, router=_extract_router(42), cache=cache)
        assert data == 42
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_replay_without_learned_flow_raises(tmp_path: Path) -> None:
    spec = FlowSpec(name="missing", start_url="http://127.0.0.1:1/x", goal="g", extract="d", headless=True)
    with pytest.raises(FlowReplayError):
        await replay(spec, router=_extract_router(1), cache=FlowCache(root=tmp_path / "empty"))


# --- Phase H: pinned 0-LLM reads --------------------------------------------------------------
def _serve_pin(state: dict):
    """/page1 -> link -> /v which shows the value in <p id='ans'> (or no element when state['gone'])."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            from urllib.parse import urlparse

            path = urlparse(self.path).path
            if path == "/page1":
                self._send("<html><body><h1>Home</h1><a href='/v'>see the value</a></body></html>")
            elif path == "/v":
                inner = "<p>no data</p>" if state.get("gone") else f"<p id='ans'>{state['value']}</p>"
                self._send(f"<html><body><h1>Value</h1>{inner}</body></html>")
            else:
                self._send("not found")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_pinned_read_replays_at_zero_llm_with_fresh_data(tmp_path: Path) -> None:
    state = {"value": "42"}
    httpd, base = _serve_pin(state)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="pin", start_url=f"{base}/page1", goal="open the value page",
                    extract="the value number", pin_read=True, headless=True)
    try:
        res = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        assert res.cached and res.found and res.pinned          # auto-pinned a deterministic read
        state["value"] = "99"                                    # the live value changes
        data = await replay(spec, cache=cache)                   # NO router/provider passed -> 0 LLM
        assert data == 99                                        # read today's value via the pin
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_pinned_read_fails_loud_when_the_pin_breaks(tmp_path: Path) -> None:
    state = {"value": "42"}
    httpd, base = _serve_pin(state)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="pinbreak", start_url=f"{base}/page1", goal="open the value page",
                    extract="the value number", pin_read=True, headless=True)
    try:
        assert (await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)).pinned
        state["gone"] = True                                     # the pinned element disappears
        with pytest.raises(FlowReplayError):                     # can't resolve -> fail loud (no wrong value)
            await replay(spec, cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_pin_read_falls_back_to_llm_when_unpinnable(tmp_path: Path) -> None:
    # the value is buried in prose ("The answer is 42."), not a unique element -> no pin -> LLM replay
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="nopin", start_url=f"{base}/page1.html", goal="open the answer page",
                    extract="the answer number", pin_read=True, headless=True)
    try:
        res = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        assert res.cached and res.found and not res.pinned       # couldn't pin
        data = await replay(spec, router=_extract_router(42), cache=cache)  # falls back to the extractor
        assert data == 42
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- Phase E: fleet supervisor (run_all) ------------------------------------------------------
async def test_run_all_classifies_ok_failed_skipped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)            # load_spec/save_spec use .ultracua/specs under cwd
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        # ok: a navigate-only flow (extract=None -> 0-LLM, no router), learned + approved
        ok = FlowSpec(name="ok", start_url=f"{base}/page1.html", goal="open the answer page", headless=True)
        save_spec(ok)
        await learn(ok, provider=_ClickFirstLink(), router=_extract_router(), cache=cache)
        approve(ok, cache=cache)
        # failed: learned + approved, but the cached flow is deleted -> replay raises (no learned flow)
        bad = FlowSpec(name="bad", start_url=f"{base}/page1.html", goal="open the answer page two", headless=True)
        save_spec(bad)
        await learn(bad, provider=_ClickFirstLink(), router=_extract_router(), cache=cache)
        approve(bad, cache=cache)
        cache.delete(flow_key(bad.goal, bad.start_url, bad.scope))
        # skipped (write): a mutate flow is skipped without include_writes
        save_spec(FlowSpec(name="w", start_url=f"{base}/page1.html", goal="g",
                           mutate=MutateSpec(confirm_text_contains="x")))
        # skipped (unapproved): a navigate-only flow that was never approved
        un = FlowSpec(name="un", start_url=f"{base}/page1.html", goal="open the answer page three", headless=True)
        save_spec(un)
        await learn(un, provider=_ClickFirstLink(), router=_extract_router(), cache=cache)

        results = {r.name: r for r in await run_all(names=["ok", "bad", "w", "un"], cache=cache)}
        assert results["ok"].status == "ok" and results["ok"].ok
        assert results["bad"].status == "failed" and not results["bad"].ok and results["bad"].error
        assert results["w"].status == "skipped" and "write" in results["w"].error
        assert results["un"].status == "skipped" and "approved" in results["un"].error
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_run_all_can_include_writes_and_unapproved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        un = FlowSpec(name="un", start_url=f"{base}/page1.html", goal="open the answer page", headless=True)
        save_spec(un)
        await learn(un, provider=_ClickFirstLink(), router=_extract_router(), cache=cache)  # not approved
        # default: skipped (unapproved); with approved_only=False it runs
        assert (await run_all(names=["un"], cache=cache))[0].status == "skipped"
        assert (await run_all(names=["un"], approved_only=False, cache=cache))[0].status == "ok"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_pin_cleared_on_relearn_without_pin_read(tmp_path: Path) -> None:
    state = {"value": "42"}
    httpd, base = _serve_pin(state)
    cache = FlowCache(root=tmp_path / "cache")
    key = flow_key("open the value page", f"{base}/page1", "flow:p")
    on = FlowSpec(name="p", start_url=f"{base}/page1", goal="open the value page",
                  extract="the value number", pin_read=True, headless=True)
    off = FlowSpec(name="p", start_url=f"{base}/page1", goal="open the value page",
                   extract="the value number", pin_read=False, headless=True)
    try:
        assert (await learn(on, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)).pinned
        assert _load_meta(cache, key).read_pin is not None
        await learn(off, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        assert _load_meta(cache, key).read_pin is None           # a pin_read=False re-learn clears the stale pin
    finally:
        httpd.shutdown()
        httpd.server_close()


class _NoSteps:
    """An unverifiable discovery attempt: emits `done` immediately, authoring nothing."""

    async def decide(self, goal, obs, history):
        return Action(action="done", intent="give up"), None


async def test_multi_sample_discovery_keeps_verified_attempt(tmp_path: Path, monkeypatch) -> None:
    import ultracua.flows as flows_mod

    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="ms", start_url=f"{base}/page1.html",
                    goal="open the answer page", extract="the answer number", headless=True)
    # attempt 1 authors nothing (unverified); attempt 2 is a good teacher. learn(samples=2) builds a
    # fresh (provider, router) per attempt via _router -> it must keep the verified second attempt.
    pairs = iter([(_NoSteps(), _extract_router()), (_ClickFirstLink(), _extract_router(42))])
    monkeypatch.setattr(flows_mod, "_router", lambda name: next(pairs))
    try:
        res = await learn(spec, samples=2, cache=cache)
        assert res.cached and res.found and res.data == 42  # kept the verified 2nd sample
        assert (await replay(spec, router=_extract_router(42), cache=cache)) == 42  # and it replays
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- Phase B: trust unattended ----------------------------------------------------------------
def _fixture_spec(tmp_path: Path, name: str):
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name=name, start_url=f"{base}/page1.html",
                    goal="open the answer page", extract="the answer", headless=True)
    return httpd, cache, spec


async def test_require_approved_gate(tmp_path: Path) -> None:
    httpd, cache, spec = _fixture_spec(tmp_path, "gate")
    try:
        res = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        assert res.cached and not res.approved              # learned but unapproved
        with pytest.raises(FlowReplayError):                 # gate blocks an unapproved flow
            await replay(spec, require_approved=True, router=_extract_router(42), cache=cache)
        approve(spec, cache=cache)
        assert await replay(spec, require_approved=True, router=_extract_router(42), cache=cache) == 42
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_shape_change_is_flagged_as_drift(tmp_path: Path) -> None:
    httpd, cache, spec = _fixture_spec(tmp_path, "shape")
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)  # shape: number
        with pytest.raises(FlowReplayError):  # replay now extracts a list -> structure changed
            await replay(spec, router=_extract_router(["a", "b"]), cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_health_tracks_runs(tmp_path: Path) -> None:
    httpd, cache, spec = _fixture_spec(tmp_path, "health")
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        assert health(spec, cache=cache).status == "never-run"      # learned, not yet run

        await replay(spec, router=_extract_router(42), cache=cache)  # a good run
        h = health(spec, cache=cache)
        assert h.status == "healthy" and h.runs == 1 and h.successes == 1

        with pytest.raises(FlowReplayError):                          # shape drift -> failure
            await replay(spec, router=_extract_router(["a"]), cache=cache)
        h = health(spec, cache=cache)
        assert h.status == "failing" and h.consecutive_failures == 1 and h.runs == 2 and h.last_error
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_on_drift_relearn_reauthors(tmp_path: Path) -> None:
    httpd, cache, spec = _fixture_spec(tmp_path, "relearn")
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        # replay extracts a differently-shaped value (drift); on_drift=relearn re-authors and
        # returns the fresh data. Router needs two responses: the drifting attempt + the relearn.
        data = await replay(spec, on_drift="relearn", provider=_ClickFirstLink(),
                            router=_extract_router(["x"], ["x"]), cache=cache)
        assert data == ["x"]
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- Phase B: auth refresh --------------------------------------------------------------------
class _AuthHandler(http.server.BaseHTTPRequestHandler):
    """A tiny cookie-gated fixture: /home shows the data only with the auth cookie."""

    def log_message(self, *a) -> None:
        pass

    def _send(self, body: str, code: int = 200, headers=None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs, urlparse

        u = urlparse(self.path)
        authed = "auth=yes" in (self.headers.get("Cookie") or "")
        if u.path == "/login":
            self._send("<form action='/dologin' method='get'>"
                       "<input name='username' type='text'>"
                       "<input name='password' type='password'>"
                       "<button type='submit'>Sign in</button></form>")
        elif u.path == "/dologin":
            q = parse_qs(u.query)
            if q.get("password", [""])[0] == "secret":
                self._send("", 302, {"Location": "/home", "Set-Cookie": "auth=yes; Path=/"})
            else:
                self._send("", 302, {"Location": "/login"})  # wrong creds -> back to login, no cookie
        elif u.path == "/home":
            self._send("<h1>Home</h1><a href='/answer'>see the answer</a>" if authed else "<p>Please log in</p>")
        elif u.path == "/answer":
            self._send("<p>The answer is 42.</p>")
        else:
            self._send("not found", 404)


def _serve_auth():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _AuthHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_refresh_auth_requires_login_and_storage_state(tmp_path: Path) -> None:
    with pytest.raises(FlowReplayError):  # no login configured
        await refresh_auth(FlowSpec(name="a", start_url="http://127.0.0.1:1/", goal="g"))
    with pytest.raises(FlowReplayError):  # login but nowhere to save cookies
        await refresh_auth(FlowSpec(name="b", start_url="http://127.0.0.1:1/", goal="g",
                                    login=LoginSpec(url="http://127.0.0.1:1/login")))


async def test_replay_refreshes_expired_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_USER", "alice")
    monkeypatch.setenv("TEST_PASS", "secret")
    httpd, base = _serve_auth()
    ss = tmp_path / "state.json"
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(
        name="auth", start_url=f"{base}/home", goal="open the answer page",
        extract="the answer number", storage_state=str(ss), headless=True,
        login=LoginSpec(url=f"{base}/login", username_env="TEST_USER", password_env="TEST_PASS"),
    )
    try:
        await refresh_auth(spec)  # log in -> save cookies
        res = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        assert res.cached and res.data == 42

        ss.write_text('{"cookies": [], "origins": []}', encoding="utf-8")  # simulate expiry
        # replay drifts (logged out) -> auto auth-refresh -> retry succeeds. Two extractions:
        # the drifting attempt + the post-refresh retry.
        data = await replay(spec, router=_extract_router(42, 42), cache=cache)
        assert data == 42
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_failed_login_does_not_poison_storage_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_USER", "alice")
    monkeypatch.setenv("TEST_PASS", "wrong")  # wrong password -> login fails
    httpd, base = _serve_auth()
    ss = tmp_path / "state.json"
    good = '{"cookies": [{"name": "auth", "value": "yes"}], "origins": []}'
    ss.write_text(good, encoding="utf-8")  # a pre-existing working session
    spec = FlowSpec(name="poison", start_url=f"{base}/home", goal="g", storage_state=str(ss),
                    headless=True, login=LoginSpec(url=f"{base}/login", username_env="TEST_USER",
                                                   password_env="TEST_PASS"))
    try:
        with pytest.raises(FlowReplayError):
            await refresh_auth(spec)
        assert ss.read_text(encoding="utf-8") == good  # working cookies NOT overwritten
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_replay_reports_auth_refresh_failure(tmp_path: Path, monkeypatch) -> None:
    """When the auto auth-refresh itself fails, replay fails loud (reason names it) and records it."""
    monkeypatch.setenv("TEST_USER", "alice")
    monkeypatch.setenv("TEST_PASS", "secret")
    httpd, base = _serve_auth()
    ss = tmp_path / "state.json"
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(
        name="authfail", start_url=f"{base}/home", goal="open the answer page",
        extract="the answer number", storage_state=str(ss), headless=True,
        login=LoginSpec(url=f"{base}/login", username_env="TEST_USER", password_env="TEST_PASS"),
    )
    try:
        await refresh_auth(spec)  # log in once so learn can see the gated page
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        ss.write_text('{"cookies": [], "origins": []}', encoding="utf-8")  # expire the session
        monkeypatch.setenv("TEST_PASS", "wrong")  # ...and break the password so the refresh fails
        with pytest.raises(FlowReplayError) as ei:
            await replay(spec, router=_extract_router(42, 42), cache=cache)
        assert "auth refresh failed" in str(ei.value)
        assert health(spec, cache=cache).status == "failing"  # the failed run is recorded
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- Phase B/C: more trust/lifecycle coverage -------------------------------------------------
async def test_navigate_only_flow_needs_no_extraction(tmp_path: Path) -> None:
    """A flow with extract=None: reaching the end IS success; replay needs no LLM router at all."""
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="nav", start_url=f"{base}/page1.html",
                    goal="open the answer page", extract=None, headless=True)
    try:
        res = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(), cache=cache)
        assert res.cached and res.found and res.data is None and res.steps
        data = await replay(spec, cache=cache)  # no router/provider passed -> none is built
        assert data is None
        assert health(spec, cache=cache).status == "healthy"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_health_stale_after_window(tmp_path: Path) -> None:
    httpd, cache, spec = _fixture_spec(tmp_path, "stale")
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        await replay(spec, router=_extract_router(42), cache=cache)  # one good run
        assert health(spec, cache=cache).status == "healthy"
        # age the recorded last-success deterministically, then apply a stale_after window
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        m = _load_meta(cache, key)
        m.last_ok_ts -= 100
        _save_meta(cache, key, m)
        assert health(spec, cache=cache, stale_after=10).status == "stale"      # older than 10s
        assert health(spec, cache=cache, stale_after=1000).status == "healthy"  # within 1000s
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_unapprove_revokes_trust(tmp_path: Path) -> None:
    httpd, cache, spec = _fixture_spec(tmp_path, "unapprove")
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        approve(spec, cache=cache)
        assert health(spec, cache=cache).approved is True
        unapprove(spec, cache=cache)
        assert health(spec, cache=cache).approved is False
        with pytest.raises(FlowReplayError):  # the gate blocks again after revoking
            await replay(spec, require_approved=True, router=_extract_router(42), cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_relearn_preserves_approval(tmp_path: Path) -> None:
    httpd, cache, spec = _fixture_spec(tmp_path, "preserve")
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        approve(spec, cache=cache)
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(99), cache=cache)  # re-learn
        assert health(spec, cache=cache).approved is True  # trust survives a re-learn
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_approve_without_learned_flow_raises(tmp_path: Path) -> None:
    spec = FlowSpec(name="x", start_url="http://127.0.0.1:1/x", goal="g", extract="d")
    with pytest.raises(FlowReplayError):
        approve(spec, cache=FlowCache(root=tmp_path / "empty"))


async def test_replay_hard_crash_is_recorded(tmp_path: Path, monkeypatch) -> None:
    """An unexpected exception during replay (browser/extract blowing up) must still land in health."""
    httpd, cache, spec = _fixture_spec(tmp_path, "crash")
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)

        async def _boom(*a, **k):
            raise RuntimeError("browser exploded")

        monkeypatch.setattr("ultracua.flows._attempt_replay", _boom)
        with pytest.raises(RuntimeError, match="browser exploded"):
            await replay(spec, router=_extract_router(42), cache=cache, auth_refresh=False)
        h = health(spec, cache=cache)
        assert h.status == "failing" and h.consecutive_failures == 1 and "browser exploded" in h.last_error
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- spec persistence (save_spec / load_spec / list_specs) ------------------------------------
def test_save_load_spec_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # _specs_dir() is relative to cwd
    spec = FlowSpec(
        name="round", start_url="http://x/", goal="g", extract="d",
        headers={"X-Auth": "t"}, storage_state="state.json",
        login=LoginSpec(url="http://x/login", username_env="U", password_env="P", timeout_ms=2000),
    )
    save_spec(spec)
    got = load_spec("round")
    assert got.name == "round" and got.headers == {"X-Auth": "t"}
    assert isinstance(got.login, LoginSpec)  # login rehydrates as a dataclass, not a dict
    assert got.login.username_env == "U" and got.login.timeout_ms == 2000


def test_save_spec_rejects_callable_login(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    async def _login(page):  # a callable login can't be serialized to JSON
        return None

    with pytest.raises(ValueError):
        save_spec(FlowSpec(name="cb", start_url="http://x/", goal="g", login=_login))


def test_load_spec_missing_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        load_spec("nope")


def test_load_spec_ignores_unknown_fields(tmp_path: Path, monkeypatch) -> None:
    """A spec written by a newer/older version (extra keys) must still load, not crash."""
    monkeypatch.chdir(tmp_path)
    d = tmp_path / ".ultracua" / "specs"
    d.mkdir(parents=True)
    (d / "fwd.json").write_text(
        '{"name": "fwd", "start_url": "http://x/", "goal": "g", "future_knob": 7, '
        '"login": {"url": "http://x/login", "legacy_opt": true}}',
        encoding="utf-8",
    )
    spec = load_spec("fwd")  # unknown top-level + unknown login keys are dropped, not fatal
    assert spec.name == "fwd" and isinstance(spec.login, LoginSpec)
    assert spec.login.url == "http://x/login"


def test_list_specs_sorted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    save_spec(FlowSpec(name="bravo", start_url="http://x/", goal="g"))
    save_spec(FlowSpec(name="alpha", start_url="http://x/", goal="g"))
    assert list_specs() == ["alpha", "bravo"]


# --- extract() edge cases ---------------------------------------------------------------------
async def test_extract_honors_custom_schema() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    ex = await extract(_extract_router({"n": 7}), "the number", "n is 7", schema=schema)
    assert ex.found and ex.data == {"n": 7}


async def test_extract_infers_found_from_data() -> None:
    # tool input without an explicit `found` key -> found is inferred from whether data is present
    mc = MockClient(actions=[{"data": [1, 2]}, {"data": None}], tool_name="submit")
    router = Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))
    ex1 = await extract(router, "nums", "1 2")
    assert ex1.found and ex1.data == [1, 2]
    ex2 = await extract(router, "nums", "nothing here")
    assert not ex2.found and ex2.data is None


async def test_extract_handles_missing_tool_call() -> None:
    from ultracua.llm.types import LLMResponse, Usage

    class _NoTool:
        async def complete(self, req):  # model answered without calling the submit tool
            return LLMResponse(blocks=[], model="m", stop_reason="end_turn",
                               usage=Usage(input_tokens=1, output_tokens=1), ttft_ms=1.0)

    router = Router(fast=Tier(_NoTool(), "m"), strong=Tier(_NoTool(), "m"))
    ex = await extract(router, "anything", "some page text")
    assert not ex.found and "no tool call" in (ex.error or "")


# --- Phase D: write (MUTATE) flows ------------------------------------------------------------
def _serve_mutate(counter: dict, drift: bool = False):
    """A tiny checkout fixture: /checkout -> click 'place the order' -> /order (the write, counted)
    -> a confirmation page. /done is a standalone 'already ordered' page for the idempotency precheck.
    With drift=True, the SECOND /checkout GET (i.e. replay) grows an extra element so its structural
    fingerprint diverges from the learned one — exercising the mutation gate."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            from urllib.parse import urlparse

            path = urlparse(self.path).path
            if path == "/checkout":
                counter["checkouts"] = counter.get("checkouts", 0) + 1
                extra = "<button>extra drift</button>" if (drift and counter["checkouts"] > 1) else ""
                self._send(f"<h1>Checkout</h1><p>cart not submitted</p>{extra}"
                           "<a href='/order'>place the order</a>")
            elif path == "/order":
                counter["orders"] = counter.get("orders", 0) + 1  # the irreversible side effect
                self._send("<h1>Order placed</h1><p>Confirmation #12345</p>")
            elif path == "/done":
                self._send("<h1>Order placed</h1><p>Confirmation #12345</p>")  # already-ordered state
            else:
                self._send("not found", 404)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_mutate_flow_confirms_write(tmp_path: Path) -> None:
    counter: dict = {}
    httpd, base = _serve_mutate(counter)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="order", start_url=f"{base}/checkout", goal="place the order",
                    mutate=MutateSpec(confirm_text_contains="Order placed"), headless=True)
    try:
        res = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(), cache=cache)
        assert res.cached and res.found              # learned + the write was confirmed at learn time
        approve(spec, cache=cache)                    # writes are approval-gated
        result = await replay(spec, cache=cache)
        assert result == {"status": "confirmed", "data": None}
        assert health(spec, cache=cache).status == "healthy"
        assert counter["orders"] == 2                 # exactly one write at learn + one at replay
        assert any(getattr(s, "mutating", False) for s in res.steps)  # the write step is gated
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_mutate_flow_fails_loud_when_unconfirmed(tmp_path: Path) -> None:
    counter: dict = {}
    httpd, base = _serve_mutate(counter)
    cache = FlowCache(root=tmp_path / "cache")
    # the confirm signal never appears on the page -> the write can't be verified
    spec = FlowSpec(name="badconfirm", start_url=f"{base}/checkout", goal="place the order",
                    mutate=MutateSpec(confirm_text_contains="Payment received"), headless=True)
    try:
        res = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(), cache=cache)
        assert res.cached and not res.found           # cached, but the write was NOT confirmed
        approve(spec, cache=cache)
        with pytest.raises(FlowReplayError):           # no completion signal -> fail loud
            await replay(spec, cache=cache)
        assert health(spec, cache=cache).status == "failing"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_mutate_flow_requires_a_confirm_check(tmp_path: Path) -> None:
    spec = FlowSpec(name="noconfirm", start_url="http://127.0.0.1:1/x", goal="g",
                    mutate=MutateSpec(), headless=True)  # mutate set but no confirm_* declared
    with pytest.raises(FlowReplayError):
        await replay(spec, cache=FlowCache(root=tmp_path / "empty"))


async def test_mutate_flow_is_approval_gated_by_default(tmp_path: Path) -> None:
    counter: dict = {}
    httpd, base = _serve_mutate(counter)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="approval", start_url=f"{base}/checkout", goal="place the order",
                    mutate=MutateSpec(confirm_text_contains="Order placed"), headless=True)
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(), cache=cache)
        with pytest.raises(FlowReplayError):  # unapproved write refused even without require_approved
            await replay(spec, cache=cache)
        approve(spec, cache=cache)
        assert (await replay(spec, cache=cache))["status"] == "confirmed"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_mutate_flow_refuses_relearn(tmp_path: Path) -> None:
    # re-authoring a write would re-perform it, so on_drift='relearn' is refused for write flows
    spec = FlowSpec(name="norelearn", start_url="http://127.0.0.1:1/x", goal="g",
                    mutate=MutateSpec(confirm_text_contains="ok"), headless=True)
    with pytest.raises(FlowReplayError):
        await replay(spec, on_drift="relearn", cache=FlowCache(root=tmp_path / "e"))


async def test_mutate_precheck_skips_when_already_done(tmp_path: Path) -> None:
    counter: dict = {}
    httpd, base = _serve_mutate(counter)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(
        name="idem", start_url=f"{base}/checkout", goal="place the order",
        mutate=MutateSpec(confirm_text_contains="Order placed",
                          precheck_url=f"{base}/done", precheck_text_contains="Order placed"),
        headless=True,
    )
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(), cache=cache)
        approve(spec, cache=cache)
        before = counter.get("orders", 0)
        result = await replay(spec, cache=cache)  # /done already shows the confirmation -> skip the write
        assert result == {"status": "already-done", "data": None}
        assert counter.get("orders", 0) == before  # the write (/order) was NOT re-fired
        assert health(spec, cache=cache).status == "healthy"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_mutate_spec_roundtrips(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    spec = FlowSpec(name="m", start_url="http://x/", goal="g",
                    mutate=MutateSpec(confirm_text_contains="Order placed",
                                      precheck_text_contains="Order placed", timeout_ms=2000))
    save_spec(spec)
    got = load_spec("m")
    assert isinstance(got.mutate, MutateSpec)  # rehydrates as a dataclass, not a dict
    assert got.mutate.confirm_text_contains == "Order placed"
    assert got.mutate.has_confirm() and got.mutate.has_precheck() and got.mutate.timeout_ms == 2000


async def test_mutate_flow_does_not_retry_write_after_auth_refresh(tmp_path: Path, monkeypatch) -> None:
    """A write that didn't confirm must NOT be re-driven by the auth-refresh retry (double-submit):
    without an idempotency precheck the retry is refused and the write fires at most once."""
    counter: dict = {}
    httpd, base = _serve_mutate(counter)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(
        name="nodouble", start_url=f"{base}/checkout", goal="place the order",
        mutate=MutateSpec(confirm_text_contains="this signal never appears"),  # confirm always fails
        login=LoginSpec(url=f"{base}/login"), headless=True,                     # login set -> retry path
    )
    refresh_calls: list = []

    async def _spy_refresh(s, **k):
        refresh_calls.append(s.name)  # must NOT be called for a write without a precheck

    monkeypatch.setattr("ultracua.flows.refresh_auth", _spy_refresh)
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(), cache=cache)
        approve(spec, cache=cache)
        before = counter.get("orders", 0)
        with pytest.raises(FlowReplayError) as ei:
            await replay(spec, cache=cache)
        assert counter.get("orders", 0) - before == 1  # the write fired ONCE; no retry double-fire
        assert refresh_calls == []                      # the auth-refresh retry was refused
        assert "double-submit" in str(ei.value)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_mutate_replay_fails_loud_on_drift_without_refiring(tmp_path: Path) -> None:
    counter: dict = {}
    httpd, base = _serve_mutate(counter, drift=True)  # replay's checkout page drifts
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="drift", start_url=f"{base}/checkout", goal="place the order",
                    mutate=MutateSpec(confirm_text_contains="Order placed"), headless=True)
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(), cache=cache)
        approve(spec, cache=cache)
        before = counter.get("orders", 0)
        with pytest.raises(FlowReplayError):       # mutation gate fails loud on page drift
            await replay(spec, cache=cache)
        assert counter.get("orders", 0) == before  # the write was NEVER re-fired under drift
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_mutate_flow_with_extract_returns_confirmation_data(tmp_path: Path) -> None:
    counter: dict = {}
    httpd, base = _serve_mutate(counter)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="orderx", start_url=f"{base}/checkout", goal="place the order",
                    extract="the confirmation number",
                    mutate=MutateSpec(confirm_text_contains="Order placed"), headless=True)
    try:
        res = await learn(spec, provider=_ClickFirstLink(), router=_extract_router("12345"), cache=cache)
        assert res.cached and res.found
        approve(spec, cache=cache)
        result = await replay(spec, router=_extract_router("12345"), cache=cache)
        assert result == {"status": "confirmed", "data": "12345"}  # found tracks confirm, data = extract
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_mutate_confirms_via_selector_and_url(tmp_path: Path) -> None:
    for name, mutate in (
        ("sel", MutateSpec(confirm_selector="h1")),           # <h1>Order placed</h1> on /order
        ("url", MutateSpec(confirm_url_contains="/order")),    # the post-write URL ends in /order
    ):
        counter: dict = {}
        httpd, base = _serve_mutate(counter)
        cache = FlowCache(root=tmp_path / f"cache-{name}")
        spec = FlowSpec(name=name, start_url=f"{base}/checkout", goal="place the order",
                        mutate=mutate, headless=True)
        try:
            await learn(spec, provider=_ClickFirstLink(), router=_extract_router(), cache=cache)
            approve(spec, cache=cache)
            assert (await replay(spec, cache=cache))["status"] == "confirmed"
        finally:
            httpd.shutdown()
            httpd.server_close()
