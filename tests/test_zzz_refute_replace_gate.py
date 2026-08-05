"""REFUTER SCRATCH: does `wrote_by_step = set(causal_steps)` drop a temporally-attributed gate?

Two writes:
  step 0 "Alpha"  -> SYNCHRONOUS fetch POST from its own click handler   -> causally attributable
  step 1 "Beta"   -> setTimeout(0) fetch POST                            -> causally UNattributable

With a SHORT write window, step 0's grace tail is dead by the time step 1's write lands, so the
temporal rule CAN name step 1. 0.75.0 would therefore promote step 1 to mutating. 0.76.0 replaces
`wrote_by_step` with the causal set {0} and step 1 loses its gate.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

from ultracua.cache import FlowCache, flow_key
from ultracua.config import settings
from ultracua.flow import run_cached
from ultracua.providers.scripted import ScriptedProvider


class _Site:
    def __init__(self, page: str) -> None:
        self.page = page
        self.posts: list[str] = []

    def serve(self):
        site = self

        class _H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def _send(self, body: str, ctype: str = "text/html") -> None:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body.encode())))
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self) -> None:  # noqa: N802
                self._send(site.page if self.path.split("?")[0] == "/" else "{}")

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.posts.append(self.path.split("?")[0]
                                  + "|" + (self.headers.get("Idempotency-Key") or ""))
                self._send("{}", "application/json")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


_PAGE = """<h1>Panel</h1>
<button id='a' type='button' onclick="fetch('/api/a',{method:'POST',body:'x=1'})">Alpha</button>
<button id='b' type='button'
  onclick="setTimeout(function(){fetch('/api/b',{method:'POST',body:'x=1'})},0)">Beta</button>
<script>
for (const id of ['a','b']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""


def _prov() -> ScriptedProvider:
    return ScriptedProvider([
        {"action": "click", "role": "button", "name": "Alpha", "intent": "open the panel"},
        {"action": "click", "role": "button", "name": "Beta", "intent": "continue"},
        {"action": "done", "intent": "done"},
    ])


async def _run(tmp_path: Path, tag: str) -> tuple:
    site = _Site(_PAGE)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / f"c{tag}")
    goal = "work the panel"
    old = settings.write_window_ms
    object.__setattr__(settings, "write_window_ms", 30)  # step 0's tail dies before step 1 acts
    try:
        report = await run_cached(f"{base}/", goal, _prov(), cache, mode="learn", headless=True,
                                  verify_replay=False)
        flow = cache.get(flow_key(goal, f"{base}/"))
        marked = None if flow is None else [(s.intent, s.mutating) for s in flow.steps]
        print(f"\n[{tag}] posts={site.posts} unattr={report.extra.get('write_unattributed')} "
              f"cached={flow is not None} steps={marked}", flush=True)
        return site.posts, report, flow
    finally:
        object.__setattr__(settings, "write_window_ms", old)
        httpd.shutdown()
        httpd.server_close()


class _SlowProv:
    """A ScriptedProvider with a REAL LLM's latency. At default write_window_ms=2000 a real
    `decide()` round-trip outlives the previous step's grace tail, so the temporal rule names the
    current step — the ordinary production case, not a contrived one."""

    def __init__(self, inner, delay: float = 2.6) -> None:
        self._inner, self._delay = inner, delay

    async def decide(self, goal, obs, history):
        import asyncio
        await asyncio.sleep(self._delay)
        return await self._inner.decide(goal, obs, history)

    def __getattr__(self, k):
        return getattr(self._inner, k)


async def _run_default_window(tmp_path: Path, tag: str) -> tuple:
    site = _Site(_PAGE)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / f"d{tag}")
    goal = "work the panel"
    try:
        report = await run_cached(f"{base}/", goal, _SlowProv(_prov()), cache, mode="learn",
                                  headless=True, verify_replay=False)
        flow = cache.get(flow_key(goal, f"{base}/"))
        marked = None if flow is None else [(s.intent, s.mutating) for s in flow.steps]
        print(f"\n[{tag}] window={settings.write_window_ms} posts={site.posts} "
              f"unattr={report.extra.get('write_unattributed')} cached={flow is not None} "
              f"steps={marked}", flush=True)
        return site.posts, report, flow
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_default_window_asis(tmp_path: Path) -> None:
    posts, report, flow = await _run_default_window(tmp_path, "default-asis")
    assert any(p.startswith("/api/b") for p in posts)


async def test_default_window_0750_sim(tmp_path: Path, monkeypatch) -> None:
    import ultracua.flow as _f
    monkeypatch.setattr(_f.attribution, "attribute", lambda events, m: (set(), 0), raising=True)
    posts, report, flow = await _run_default_window(tmp_path, "default-0.75.0-sim")
    assert any(p.startswith("/api/b") for p in posts)


async def test_replay_of_the_asis_flow_fires_an_unkeyed_post(tmp_path: Path) -> None:
    """The consequence: the lost gate means replay re-fires /api/b with no Idempotency-Key."""
    site = _Site(_PAGE)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "r")
    goal = "work the panel"
    try:
        await run_cached(f"{base}/", goal, _SlowProv(_prov()), cache, mode="learn", headless=True,
                         verify_replay=False)
        flow = cache.get(flow_key(goal, f"{base}/"))
        assert flow is not None, "learn refused; nothing to replay"
        site.posts.clear()
        rep = await run_cached(f"{base}/", goal, None, cache, mode="replay", headless=True)
        print(f"\n[replay] success={rep.success} posts={site.posts}", flush=True)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_asis_0760(tmp_path: Path) -> None:
    posts, report, flow = await _run(tmp_path, "0.76.0-asis")
    assert any(p.startswith("/api/a") for p in posts), posts
    assert any(p.startswith("/api/b") for p in posts), "the deferred POST never landed"


async def test_with_causal_replace_disabled_simulating_0750(tmp_path: Path, monkeypatch) -> None:
    """Neutralise the causal signal -> `wrote_by_step` is exactly what 0.75.0 computed."""
    from ultracua import attribution as _a
    import ultracua.flow as _f

    monkeypatch.setattr(_f.attribution, "attribute", lambda events, m: (set(), 0), raising=True)
    posts, report, flow = await _run(tmp_path, "0.75.0-sim")
    assert any(p.startswith("/api/b") for p in posts), "the deferred POST never landed"
