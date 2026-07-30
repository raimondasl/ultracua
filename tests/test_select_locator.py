"""A learned `select` step must cache a LOCATOR — the regression guard for a flow that could never replay.

`_author_steps` only called `describe()` for `click`/`type`, so a `select` step was cached with
`locator: null`. At replay, `_replay_step` refuses a locator-less click/type/select as an "unreplayable
step", so the whole flow failed loud on EVERY run: a dropdown authored this way produced a cached flow that
was structurally un-replayable. Silent in the suite because every existing `select` test drives the RECORDER,
which builds its specs in JS and never goes through `_author_steps`.

Blast radius, stated precisely: this affects scripted / custom providers only. `providers/base.py`'s
`ACTION_TOOL` is a `strict` schema whose action enum has no `"select"`, so an LLM-authored flow cannot
contain one — the defect was latent for the LLM path and live for the scripted path (including this repo's
own benchmarks). All key-less; real Chromium against a local fixture.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from ultracua.cache import FlowCache
from ultracua.flow import run_cached
from ultracua.providers.scripted import ScriptedProvider

_FORM = """<!doctype html><html><head><meta charset=utf-8><title>Order</title></head><body>
  <h1>Order</h1>
  <label for="size">Size</label>
  <select id="size" name="size">
    <option value="s">Small</option>
    <option value="m">Medium</option>
    <option value="l">Large</option>
  </select>
  <a href="/done" id="go">Continue</a>
</body></html>"""


def _serve():
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            body = _FORM if path == "/" else f"<!doctype html><title>{path}</title><h1>{path}</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


_STEPS = [
    {"action": "select", "role": "combobox", "name": "Size", "text": "m", "intent": "choose the size"},
    {"action": "click", "role": "link", "name": "Continue", "intent": "continue"},
    {"action": "done", "intent": "done"},
]


async def test_a_learned_select_step_caches_a_locator_and_replays() -> None:
    httpd, base = _serve()
    try:
        with TemporaryDirectory() as td:
            cache = FlowCache(root=Path(td) / "c")
            learn = await run_cached(base + "/", "choose a size and continue",
                                     ScriptedProvider(list(_STEPS)), cache, mode="learn", headless=True)
            assert learn.success, learn.note

            flow = next(iter(cache.root.glob("*.json")), None)
            assert flow is not None, "the learned flow was not cached"
            cached = FlowCache(root=cache.root).get(flow.stem)
            assert cached is not None
            sel = [s for s in cached.steps if s.action == "select"]
            assert sel, "no select step was cached"
            # THE REGRESSION: this was None, which made the step unreplayable forever.
            assert sel[0].locator is not None, "a cached select step has no locator — it can never replay"
            assert sel[0].text == "m"

            # ...and the cached flow really does replay, 0-LLM, with no provider at all.
            replay = await run_cached(base + "/", "choose a size and continue", None, cache,
                                      mode="replay", headless=True)
            assert replay.success, replay.note
            assert replay.llm_calls == 0
    finally:
        httpd.shutdown()
        httpd.server_close()
