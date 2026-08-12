"""What the human-verdict primitive can and cannot DISPOSE of — measured, not sequenced.

`docs/correctness-plan.md` sequenced S18 (R4.5) behind "the write-provenance + annotation work", on the
argument that the primitive D5 demands — inference -> a human's verdict — is that work, and that R4.27's
disposition needs the same thing. Both halves shipped (0.92.0, 0.93.0). Landing a prerequisite is not the
same as satisfying a dependency, and this register's rule is to measure the second rather than infer it
from the first, so all three populations were run against 0.93.0:

    R4.27, twelve GraphQL-style READ controls   12/12 cached as write flows, 12/12 carry `wire`,
                                                12/12 demotion REFUSED
    R4.5 on the LEARN path                      promotion ALLOWED — the artifact is repairable
    R4.5 on the RECORD path                     the phantom step carries `wire`; demotion refused,
                                                promotion pointless, and neither verb changes step
                                                MEMBERSHIP, which is the whole harm there

So the primitive disposes of NEITHER named consumer, for two different reasons — one a deliberate
refusal, one a vocabulary bound — and S18 is exactly where it was. The full numbers are in the R4.27
entry of `docs/open-defects.md`.

This file pins the R4.27 half, which is the durable one: it is a property of the wire promotion plus
`_DEMOTABLE_MARKS`, independent of whether R4.5 is ever fixed. It is deliberately end-to-end. The unit
tests in `test_human_write_annotation.py` already pin "a `wire` mark is not demotable" against a
hand-built flow; what NOTHING pinned is that this population ARRIVES in that state through a real learn,
which is the step the plan's dependency argument actually turns on.
"""
from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowSpec, _learn_once, is_write_flow, mark_step
from ultracua.providers.scripted import ScriptedProvider
from ultracua.safety import classify_mutation

pytestmark = pytest.mark.asyncio

# An ordinary SPA read: a GraphQL *query*, which travels as a POST. Deliberately named so the keyword
# classifier says READ — "Next page" contains no `MUTATING_KEYWORDS` term — because that isolates the
# WIRE as the only thing that could mark it. Seven of R4.27's twelve also trip the keyword list; those
# are the classifier's ordinary false positives and prove less.
_GRAPHQL_READ = """<h1>Reports</h1>
<div id='out'>idle</div>
<button id='b' type='button'>Next page</button>
<script>
document.getElementById('b').addEventListener('click', function() {
  fetch('/graphql', {method: 'POST', headers: {'content-type': 'application/json'},
                     body: JSON.stringify({query: '{ rows { id } }'})});
  document.getElementById('out').textContent = 'page 2';
});
</script>"""


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
                self._send('{"data":{"rows":[]}}', "application/json")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_the_r427_population_arrives_non_demotable_so_the_verb_cannot_dispose_of_it(
        tmp_path: Path) -> None:
    """The plan's dependency, checked end-to-end rather than argued from the mark vocabulary.

    Both directions are loud on purpose. If R4.27 is ever CLOSED — a GraphQL query stops being filed as
    a write — this test fails with a message saying so, because at that moment the register, the plan's
    S18 sequencing and this pin all become stale together, and a pin that silently starts testing
    nothing is the failure mode this suite has already shipped once.
    """
    assert not classify_mutation("click", "go to the next page", "Next page"), (
        "premise: this control must be a keyword-classifier READ, or the wire is not the only thing "
        "that could mark it and the measurement proves less than it claims")

    site = _Site(_GRAPHQL_READ)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="r", goal="go to the next page of the report",
                        start_url=f"{base}/", headless=True)
        await _learn_once(
            spec,
            provider=ScriptedProvider([
                {"action": "click", "role": "button", "name": "Next page",
                 "intent": "go to the next page"},
                {"action": "done", "intent": "done"},
            ]),
            router=None, cache=cache, verify_replay=False)

        assert site.posts == ["/graphql"], (
            f"premise: the read must have queried over POST, or nothing exercises the wire promotion; "
            f"posts={site.posts}")
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        assert flow is not None, "premise: the flow must have cached for there to be anything to annotate"

        marked = [(i, s.mutating_sources) for i, s in enumerate(flow.steps) if s.mutating]
        assert is_write_flow(spec, flow) and marked, (
            "R4.27 APPEARS CLOSED: an ordinary GraphQL-style read no longer caches as a write flow. "
            "That is good news and this pin is now stale — update the R4.27 entry in "
            "docs/open-defects.md, re-check S18's sequencing in docs/correctness-plan.md, and delete "
            f"or rewrite this test. steps={[(s.intent, s.mutating) for s in flow.steps]}")

        idx, srcs = marked[0]
        assert "wire" in (srcs or []), (
            f"the mark's PROVENANCE is what decides demotability, so the measurement turns on it: "
            f"expected the wire to have recorded itself, got {srcs!r}")

        with pytest.raises(ValueError) as ei:
            mark_step(spec, idx, writes=False, cache=cache)
        assert "wire" in str(ei.value), (
            f"a refusal must name the evidence it declines to overrule; got {str(ei.value)!r}")

        after = cache.get(flow_key(spec.goal, spec.start_url, spec.scope)).steps[idx]
        assert after.mutating is True and (after.mutating_sources or []) == (srcs or []), (
            "a REFUSED annotation must leave the recipe byte-for-byte alone — a half-applied verdict "
            "would be worse than either outcome")
    finally:
        httpd.shutdown()
        httpd.server_close()
