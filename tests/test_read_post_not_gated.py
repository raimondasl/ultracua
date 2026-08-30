"""THE FIX'S OWN CLAIM, end to end: a JSON-RPC read POST no longer gates the step.

WHY THIS FILE EXISTS SEPARATELY. `test_reads_over_post.py` imports `body_says_read`,
`READ_RPC_METHODS` and `MARK_BODY_READ`, and `READ_RPC_METHODS` is read at COLLECTION time by a
`parametrize`. So that whole module errors on a base that predates D7, and `scripts/red_in_ci.py`
correctly scores every one of its cells `inconclusive` -- "an import that does not exist on the base
is not evidence".

That gate then found a REAL hole rather than a technicality: nothing in the slice proved the feature
WORKS. The rule was pinned in isolation, and the hazard it introduces was pinned by a write-safety
cell -- which passes on the base too, because on the base there is no demotion and so no hazard. This
file is the missing half, and it imports ONLY symbols that predate D7 so it can run there and FAIL.

AGAINST THE BASE: the click fires a POST inside the act window, the wire watcher marks the step, and
`mutating` is True -- so both assertions below fail. That is the evidence `red_in_ci` asks for.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

# Deliberately NOT importing anything D7 added -- see the docstring.
from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowSpec, _learn_once
from ultracua.providers.scripted import ScriptedProvider


class _OdooishSite:
    """Serves a page whose button fires an Odoo-shaped `call_kw` READ."""

    def __init__(self) -> None:
        self.posts: list[str] = []

    def serve(self):
        site = self

        class _H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                body = (
                    "<h1>Pipeline</h1><button type=button id='sort'>Expected Revenue</button>"
                    "<script>document.getElementById('sort').addEventListener('click',function(){"
                    "  fetch('/web/dataset/call_kw/crm.lead/web_search_read',{method:'POST',"
                    "        headers:{'Content-Type':'application/json'},"
                    "        body:JSON.stringify({jsonrpc:'2.0',method:'call',params:{"
                    "            model:'crm.lead',method:'web_search_read',args:[],kwargs:{}}})});"
                    "  document.querySelector('h1').textContent='sorted';"
                    "});</script>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0) or 0)
                site.posts.append(self.path)
                out = json.dumps({"jsonrpc": "2.0", "result": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"


async def test_a_json_rpc_read_post_does_not_gate_the_step(tmp_path: Path) -> None:
    """R4.123. The step whose click fired ONE `call_kw` read must cache UNGATED, and must still say
    a POST was seen -- the demotion records `body_read` rather than silently dropping the evidence,
    which is what would make R4.27 invisible again."""
    site = _OdooishSite()
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="sort the pipeline", start_url=f"{base}/", headless=True)
        res = await _learn_once(
            spec,
            provider=ScriptedProvider([
                {"action": "click", "role": "button", "name": "Expected Revenue",
                 "intent": "sort by expected revenue"},
                {"action": "done", "intent": "done"},
            ]),
            router=None, cache=cache, verify_replay=False)
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        marks = [s.mutating_sources for s in flow.steps] if flow else None
        gated = [i for i, s in enumerate(flow.steps) if s.mutating] if flow else None
        print(f"    posts={site.posts}  cached={bool(res.cached)}  gated={gated}  marks={marks}")

        if not site.posts:
            raise RuntimeError(
                "PREMISE LOST: the click fired no POST, so this cell exercises nothing.")
        assert flow is not None and flow.steps, "the fixture learn cached no recipe"

        click = flow.steps[0]
        assert click.mutating is False, (
            "a `call_kw` READ still gated the step. That is R4.27: the step then loses self-heal and "
            "suffix-replan, and its mutation gate turns ordinary drift into a hard refusal.")
        assert "body_read" in (click.mutating_sources or []), (
            "the demotion left no trace. `MARK_BODY_READ` is what keeps R4.27 visible -- a step that "
            "silently loses its evidence cannot be told from one that never had any.")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_write_shaped_call_still_gates_the_step(tmp_path: Path) -> None:
    """THE OTHER DIRECTION, in the same shape. `create` travels the same route and the same envelope;
    only the method name differs, and it must still gate. Without this the cell above is satisfied by
    a demotion that fired on everything."""
    site = _OdooishSite()

    def _write_page(self) -> None:  # noqa: ANN001
        pass

    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c2")
    try:
        # Same server, but drive the WRITE route by rewriting the page's fetch target through the
        # goal-side script: simplest is a second site instance serving `create`.
        httpd.shutdown()
        httpd.server_close()

        class _WriteSite(_OdooishSite):
            def serve(self):
                outer = self

                class _H(http.server.BaseHTTPRequestHandler):
                    def log_message(self, *a) -> None:
                        pass

                    def do_GET(self) -> None:  # noqa: N802
                        body = (
                            "<h1>Pipeline</h1><button type=button id='go'>Create</button>"
                            "<script>document.getElementById('go').addEventListener('click',function(){"
                            "  fetch('/web/dataset/call_kw/crm.lead/create',{method:'POST',"
                            "        headers:{'Content-Type':'application/json'},"
                            "        body:JSON.stringify({jsonrpc:'2.0',method:'call',params:{"
                            "            model:'crm.lead',method:'create',args:[{}],kwargs:{}}})});"
                            "  document.querySelector('h1').textContent='created';"
                            "});</script>").encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)

                    def do_POST(self) -> None:  # noqa: N802
                        self.rfile.read(int(self.headers.get("Content-Length") or 0) or 0)
                        outer.posts.append(self.path)
                        out = json.dumps({"jsonrpc": "2.0", "result": 1}).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(out)))
                        self.end_headers()
                        self.wfile.write(out)

                h = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
                threading.Thread(target=h.serve_forever, daemon=True).start()
                return h, f"http://127.0.0.1:{h.server_port}"

        wsite = _WriteSite()
        httpd, base = wsite.serve()
        spec = FlowSpec(name="p", goal="create a lead", start_url=f"{base}/", headless=True)
        res = await _learn_once(
            spec,
            provider=ScriptedProvider([
                {"action": "click", "role": "button", "name": "Create", "intent": "create a lead"},
                {"action": "done", "intent": "done"},
            ]),
            router=None, cache=cache, verify_replay=False)
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        gated = [i for i, s in enumerate(flow.steps) if s.mutating] if flow else None
        print(f"    posts={wsite.posts}  cached={bool(res.cached)}  gated={gated} "
              f"performed_write={res.performed_write}")

        if not wsite.posts:
            raise RuntimeError("PREMISE LOST: the click fired no POST.")
        if flow is None:
            # A write that could not be attributed is REFUSED, which is also correct here.
            assert res.performed_write, "a write fired but the result does not say so"
            return
        assert gated, (
            "a `create` was demoted. Only the read allowlist may clear a POST, and `create` is not "
            "on it -- this is the direction that costs inviolable #3.")
    finally:
        httpd.shutdown()
        httpd.server_close()
