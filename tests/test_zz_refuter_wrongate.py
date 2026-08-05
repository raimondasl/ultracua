"""REFUTER probe for the `causal REPLACE drops a temporally-attributed gate` finding.

Runs the AUDITED code (`wrote_by_step = set(causal_steps)`) in a PRIVATE copy of `ultracua.flow`
(exec'd into its own module object) so the shared working tree — which currently carries other
agents' probes — is never edited.

Fixture: two writes in one learn pass, with a REAL decide gap (2.6s > write_window_ms 2000) so each
step's grace tail has expired by the time the next one acts and the TEMPORAL rule can name a distinct
owner for each write.
  step 0 "Search"   -> setTimeout(fetch POST /api/deferred, 30)  : causally UNATTRIBUTABLE (seq null),
                                                                   temporally owned by 0
  step 1 "Continue" -> fetch POST /api/commit (synchronous)      : causally attributed to 1
"""

from __future__ import annotations

import asyncio
import http.server
import importlib.util
import pathlib
import threading
from pathlib import Path

import ultracua.flow as _real_flow
from ultracua import attribution
from ultracua.cache import FlowCache, flow_key
from ultracua.providers.scripted import ScriptedProvider

_UNION = "wrote_by_step = set(wrote_by_step) | set(causal_steps)"
_REPLACE = "wrote_by_step = set(causal_steps)"


def _flow_with_replace():
    src = pathlib.Path(_real_flow.__file__).read_text(encoding="utf-8")
    if _UNION in src:
        src = src.replace(_UNION + "   # PROBE: union instead of replace", _REPLACE)
        src = src.replace(_UNION, _REPLACE)
    assert "wrote_by_step = set(causal_steps)" in src, "could not build the REPLACE variant"
    assert _UNION not in src
    spec = importlib.util.spec_from_loader("ultracua._flow_replace_probe", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = _real_flow.__file__
    mod.__package__ = "ultracua"
    import sys
    sys.modules["ultracua._flow_replace_probe"] = mod
    exec(compile(src, _real_flow.__file__, "exec"), mod.__dict__)
    return mod


def _real_attribute(events, seq_to_step):
    """The real body of `attribution.attribute` (the shipped file currently carries an AUDITMUT stub)."""
    wrote: set = set()
    unattributed = 0
    for ev in events:
        if not isinstance(ev, dict) or ev.get("action") != "__wirewrite":
            continue
        seq = ev.get("seq")
        step = seq_to_step.get(seq) if isinstance(seq, int) else None
        if step is None:
            unattributed += 1
        else:
            wrote.add(step)
    return wrote, unattributed


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
                site.posts.append(self.path.split("?")[0])
                self._send("{}", "application/json")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


_MIXED = """<h1>Panel</h1>
<button id='s' type='button'
  onclick="setTimeout(function(){fetch('/api/deferred',{method:'POST',body:'x=1'});},30)">Search</button>
<button id='c' type='button'
  onclick="fetch('/api/commit',{method:'POST',body:'x=1'})">Continue</button>
<script>
for (const id of ['s','c']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""


class _Slow(ScriptedProvider):
    """A provider with a realistic decide gap, so every grace tail expires between steps."""

    async def decide(self, goal, obs, history):
        await asyncio.sleep(2.6)
        return await super().decide(goal, obs, history)


def _prov(*names_and_intents) -> _Slow:
    return _Slow(
        [{"action": "click", "role": "button", "name": n, "intent": i} for n, i in names_and_intents]
        + [{"action": "done", "intent": "done"}])


async def _run(tmp_path: Path, monkeypatch, *, causal: bool):
    monkeypatch.setattr(
        attribution, "attribute",
        _real_attribute if causal else (lambda e, s: (set(), 0)))
    mod = _flow_with_replace()
    site = _Site(_MIXED)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / ("c_causal" if causal else "c_base"))
    goal = "work the panel"
    try:
        report = await mod.run_cached(
            f"{base}/", goal, _prov(("Search", "look at the panel"), ("Continue", "continue")),
            cache, mode="learn", headless=True, verify_replay=False)
        flow = cache.get(flow_key(goal, f"{base}/"))
        print(("CAUSAL(0.76.0 replace)" if causal else "BASELINE(0.75.0)"),
              "posts:", site.posts, "extra:", report.extra,
              "flow:", None if flow is None else [(s.intent, s.mutating, s.precond_scope) for s in flow.steps])
        return site, report, flow
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_baseline_075_gates_both_writes(tmp_path: Path, monkeypatch) -> None:
    site, report, flow = await _run(tmp_path, monkeypatch, causal=False)
    assert set(site.posts) == {"/api/deferred", "/api/commit"}, site.posts
    assert flow is not None, "0.75.0 refused this flow — the comparison would be meaningless"
    by = {s.intent: s.mutating for s in flow.steps}
    assert by.get("look at the panel") is True, f"0.75.0 did not gate step 0 either: {by}"


async def test_076_replace_keeps_the_deferred_writers_gate(tmp_path: Path, monkeypatch) -> None:
    site, report, flow = await _run(tmp_path, monkeypatch, causal=True)
    assert set(site.posts) == {"/api/deferred", "/api/commit"}, site.posts
    if flow is None:
        return  # refused — loud, not a silent gate loss
    by = {s.intent: s.mutating for s in flow.steps}
    assert by.get("look at the panel") is True, (
        f"step 0 POSTed on the wire and cached UNGATED under the causal replace: {by}")
