"""Write/auth flow benchmark — runnable evidence for Phase D (write flows + action-completion)
and Phase B (auth refresh), which the timing / MiniWoB / WebArena benchmarks don't cover.

    uv run python -m benchmarks.write_flow_bench                 # key-less (scripted teacher)
    uv run python -m benchmarks.write_flow_bench --provider anthropic

Three scenarios against a local cookie-gated fixture, each on a fresh server + cache:
  1. WRITE      — learn a write flow, approve, replay -> action-completion CONFIRMED, 0-LLM nav,
                  and replay is far faster than the learn run.
  2. IDEMPOTENT — a one-shot write with a precheck: after the write exists, a replay is SKIPPED
                  (status=already-done) and the order count does not increase (no double-submit).
  3. AUTH       — an authenticated READ flow recovers from session expiry: replay drifts (logged
                  out), auto re-logs-in, and returns the data.

Key-less runs use a scripted teacher for discovery and a mock extractor; `--provider anthropic`
authors + extracts for real (ANTHROPIC_API_KEY from .env).
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import os
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from ultracua.cache import FlowCache
from ultracua.flows import (
    FlowReplayError,
    FlowSpec,
    LoginSpec,
    MutateSpec,
    approve,
    learn,
    refresh_auth,
    replay,
)
from ultracua.llm.base import Router, Tier
from ultracua.llm.mock import MockClient
from ultracua.providers.scripted import ScriptedProvider

_PW = "secret"


def _serve(counter: dict):
    """A cookie-gated fixture: login -> checkout/home; /order is the counted write; /status is a
    NON-gated 'already ordered?' page for the idempotency precheck; /answer is the read target."""

    class _H(http.server.BaseHTTPRequestHandler):
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
                if parse_qs(u.query).get("password", [""])[0] == _PW:
                    self._send("", 302, {"Location": "/checkout", "Set-Cookie": "auth=yes; Path=/"})
                else:
                    self._send("", 302, {"Location": "/login"})
            elif u.path == "/checkout":
                self._send("<h1>Checkout</h1><a href='/order'>place the order</a>"
                           if authed else "<p>Please log in</p>")
            elif u.path == "/order":
                if not authed:
                    self._send("<p>Please log in</p>")
                    return
                counter["orders"] = counter.get("orders", 0) + 1
                self._send(f"<h1>Order placed</h1><p>Confirmation #{counter['orders']}</p>")
            elif u.path == "/status":
                self._send("<h1>Order placed</h1>" if counter.get("orders", 0) > 0 else "<p>no orders</p>")
            elif u.path == "/home":
                self._send("<h1>Home</h1><a href='/answer'>see the answer</a>"
                           if authed else "<p>Please log in</p>")
            elif u.path == "/answer":
                self._send("<p>The answer is 42.</p>")
            else:
                self._send("not found", 404)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _mock_router(*vals) -> Router:
    acts = [{"found": True, "data": v} for v in (vals or (42,))]
    mc = MockClient(actions=acts, tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


_CLICK_ORDER = [
    {"action": "click", "role": "link", "name": "place the order", "intent": "place the order"},
    {"action": "done", "intent": "order submitted"},
]
_CLICK_ANSWER = [
    {"action": "click", "role": "link", "name": "see the answer", "intent": "open the answer page"},
    {"action": "done", "intent": "done"},
]


def _setenv() -> None:
    os.environ["WB_USER"], os.environ["WB_PASS"] = "alice", _PW


async def _learn(spec, provider_name, steps, router, cache):
    if provider_name == "scripted":
        return await learn(spec, provider=ScriptedProvider(list(steps)), router=router, cache=cache)
    return await learn(spec, provider_name=provider_name, cache=cache)


# --- scenarios --------------------------------------------------------------------------------
async def scenario_write(provider_name: str, tmp: Path) -> dict:
    counter: dict = {}
    httpd, base = _serve(counter)
    _setenv()
    cache = FlowCache(root=tmp / "w")
    spec = FlowSpec(
        name="order", start_url=f"{base}/checkout", goal="place the order",
        storage_state=str(tmp / "w.json"), headless=True,
        login=LoginSpec(url=f"{base}/login", username_env="WB_USER", password_env="WB_PASS"),
        mutate=MutateSpec(confirm_text_contains="Order placed"),
    )
    try:
        await refresh_auth(spec)
        t0 = time.perf_counter()
        res = await _learn(spec, provider_name, _CLICK_ORDER, _mock_router(), cache)
        learn_ms = (time.perf_counter() - t0) * 1000
        approve(spec, cache=cache)
        t1 = time.perf_counter()
        result = await replay(spec, cache=cache)
        replay_ms = (time.perf_counter() - t1) * 1000
        passed = bool(res.cached and result.get("status") == "confirmed" and counter.get("orders", 0) >= 2)
        return {"name": "WRITE", "passed": passed, "status": result.get("status"),
                "orders": counter.get("orders", 0), "learn_ms": round(learn_ms),
                "replay_ms": round(replay_ms)}
    finally:
        httpd.shutdown()
        httpd.server_close()


async def scenario_idempotent(provider_name: str, tmp: Path) -> dict:
    counter: dict = {}
    httpd, base = _serve(counter)
    _setenv()
    cache = FlowCache(root=tmp / "i")
    spec = FlowSpec(
        name="order-once", start_url=f"{base}/checkout", goal="place the order",
        storage_state=str(tmp / "i.json"), headless=True,
        login=LoginSpec(url=f"{base}/login", username_env="WB_USER", password_env="WB_PASS"),
        mutate=MutateSpec(confirm_text_contains="Order placed",
                          precheck_url=f"{base}/status", precheck_text_contains="Order placed"),
    )
    try:
        await refresh_auth(spec)
        await _learn(spec, provider_name, _CLICK_ORDER, _mock_router(), cache)  # writes once
        approve(spec, cache=cache)
        after_learn = counter.get("orders", 0)
        result = await replay(spec, cache=cache)  # /status already shows the order -> skip
        passed = bool(result.get("status") == "already-done" and counter.get("orders", 0) == after_learn)
        return {"name": "IDEMPOTENT", "passed": passed, "status": result.get("status"),
                "orders": counter.get("orders", 0), "after_learn": after_learn}
    finally:
        httpd.shutdown()
        httpd.server_close()


async def scenario_auth(provider_name: str, tmp: Path) -> dict:
    counter: dict = {}
    httpd, base = _serve(counter)
    _setenv()
    cache = FlowCache(root=tmp / "a")
    ss = tmp / "a.json"
    spec = FlowSpec(
        name="report", start_url=f"{base}/home", goal="open the answer page",
        extract="the answer number", storage_state=str(ss), headless=True,
        login=LoginSpec(url=f"{base}/login", username_env="WB_USER", password_env="WB_PASS"),
    )
    try:
        await refresh_auth(spec)
        await _learn(spec, provider_name, _CLICK_ANSWER, _mock_router(42), cache)
        approve(spec, cache=cache)
        first = await (replay(spec, router=_mock_router(42), cache=cache) if provider_name == "scripted"
                       else replay(spec, provider_name=provider_name, cache=cache))
        ss.write_text('{"cookies": [], "origins": []}', encoding="utf-8")  # expire the session
        recovered = await (replay(spec, router=_mock_router(42, 42), cache=cache) if provider_name == "scripted"
                           else replay(spec, provider_name=provider_name, cache=cache))
        passed = bool(first == 42 and recovered == 42)
        return {"name": "AUTH", "passed": passed, "first": first, "recovered_after_expiry": recovered}
    except FlowReplayError as exc:
        return {"name": "AUTH", "passed": False, "error": str(exc)}
    finally:
        httpd.shutdown()
        httpd.server_close()


async def run(provider_name: str) -> int:
    print(f"write/auth flow benchmark: provider={provider_name}\n")
    with TemporaryDirectory() as td:
        tmp = Path(td)
        results = [
            await scenario_write(provider_name, tmp),
            await scenario_idempotent(provider_name, tmp),
            await scenario_auth(provider_name, tmp),
        ]
    for r in results:
        mark = "PASS" if r.get("passed") else "FAIL"
        extra = " ".join(f"{k}={v}" for k, v in r.items() if k not in ("name", "passed"))
        print(f"[{mark}] {r['name']:<11} {extra}")
    ok = sum(1 for r in results if r.get("passed"))
    print(f"\n== {ok}/{len(results)} scenarios passed ==")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="benchmarks.write_flow_bench")
    ap.add_argument("--provider", default="scripted", choices=["scripted", "anthropic", "openai", "gemini"])
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.provider)))
