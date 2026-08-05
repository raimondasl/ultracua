"""Causal write attribution on the learn path — the R3.2 close.

WHAT R3.2 COSTS TODAY. `_write_owner` arbitrates overlapping time windows, and with steps milliseconds
apart the candidate set is always {i-1, i}, so nothing past the FIRST step can be attributed. When the
classifier can also not see the commit — a formless `fetch` POST behind a bland label, the ubiquitous
SPA shape — the flow has no gate at all and 0.75.0 refuses it. Measured on the invariant matrix: the
`fetch-later-*-blind` and `xhr-later-*-blind` cells all refuse. Correct, and useless: that flow simply
cannot be authored.

No tuning fixes it. A tail long enough to catch a deferred write also overlaps the next step; a shorter
one misses the write. The 0.73.0 attempt to drain instead of arbitrate was measured to CREDIT THE WRONG
STEP and was reverted. The register's conclusion was that attribution needs a CAUSAL signal.

THE SIGNAL ALREADY EXISTS, one file over. `recorder.py` has carried it since Phase I: `__ucturn` counts
commits in the current SYNCHRONOUS turn (reset on the next macrotask), and `attributedSeq()` returns a
commit only when exactly one fired in that turn. A write dispatched from a click's own handler names
that click exactly, whatever the clock says; a DEFERRED write (timer, debounce, awaited round-trip)
reports null and is refused. `learn()` has never had it — the exact "guard on a sibling path, never
applied to the mechanism" pattern this project keeps producing.

These tests are written BEFORE the extraction and are expected to FAIL on 0.75.0. They state what the
shared machinery must deliver:

  * a classifier-blind commit on a LATER step is attributed to that step and cached with its gate;
  * it replays 0-LLM carrying an Idempotency-Key;
  * a DEFERRED write is still refused — the causal signal says "unattributable", not "probably that
    one", which is the whole reason it is trustworthy;
  * the machinery is ONE implementation shared with the recorder, not a transcription (R3.1's lesson:
    two copies of a JS snippet that must agree had already silently diverged).
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.providers.scripted import ScriptedProvider


class _Site:
    def __init__(self, page: str) -> None:
        self.page = page
        self.posts: list[str] = []           # the Idempotency-Key header, "" when absent

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
                site.posts.append(self.headers.get("Idempotency-Key") or "")
                self._send("{}", "application/json")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _page(commit_js: str) -> str:
    """Step 0 is inert; step 1 is the commit, behind a BLAND label so the keyword classifier is blind
    and only the wire can identify it."""
    return f"""<h1>Panel</h1>
<button id='f' type='button'>Filter</button>
<button id='c' type='button' onclick="{commit_js}">Continue</button>
<script>
for (const id of ['f','c']) document.getElementById(id).addEventListener('click',
  function(){{ document.querySelector('h1').textContent = 'clicked ' + id; }});
</script>"""


_SYNC = "fetch('/api/commit',{method:'POST',body:'x=1'})"
_DEFERRED = "setTimeout(function(){fetch('/api/commit',{method:'POST',body:'x=1'})},50)"


def _prov(trailing: bool = False):
    acts = [
        {"action": "click", "role": "button", "name": "Filter", "intent": "filter the list"},
        {"action": "click", "role": "button", "name": "Continue", "intent": "continue"},
    ]
    if trailing:   # keeps the session alive long enough for a DEFERRED write to land
        acts.append({"action": "click", "role": "button", "name": "Filter", "intent": "filter again"})
    return ScriptedProvider(acts + [{"action": "done", "intent": "done"}])


async def test_a_classifier_blind_commit_on_a_later_step_is_attributed_to_it(tmp_path: Path) -> None:
    """THE point of the extraction. The commit is step 1, fires synchronously from its own click, and
    nothing about its name or form tells the classifier it writes. Only the page knows — and it does
    know, because the write leaves `fetch` inside that click's own synchronous turn.

    On 0.75.0 this REFUSES (`_write_owner` cannot attribute past step 0 and no step is gated), so the
    flow is unauthorable. It must cache, with the gate on step 1 and nothing else."""
    site = _Site(_page(_SYNC))
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "work the panel"
    try:
        report = await run_cached(f"{base}/", goal, _prov(), cache, mode="learn", headless=True,
                                  verify_replay=False)
        assert site.posts, "the fixture did not POST; the rest would prove nothing"
        assert not report.extra.get("write_unattributed"), (
            "the write left fetch inside the click's own synchronous turn — the page can name its "
            "cause exactly, so it must not be reported unattributable")
        flow = cache.get(flow_key(goal, f"{base}/"))
        assert flow is not None, "a causally attributed write must CACHE, not refuse"
        marked = [i for i, s in enumerate(flow.steps) if s.mutating]
        assert marked == [1], f"the commit is step 1 and only step 1; got {marked}"
        assert flow.steps[1].precond_scope != "", "the gated step must carry its precondition"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_that_attributed_write_replays_0_llm_carrying_an_idempotency_key(tmp_path: Path) -> None:
    """Attribution is only worth having if it makes the gate real. The whole point of marking step 1
    mutating is that replay keys it — otherwise a resume double-submits."""
    site = _Site(_page(_SYNC))
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "work the panel"
    try:
        await run_cached(f"{base}/", goal, _prov(), cache, mode="learn", headless=True,
                         verify_replay=False)
        assert cache.get(flow_key(goal, f"{base}/")) is not None, "learn refused; nothing to replay"
        site.posts.clear()
        replay = await run_cached(f"{base}/", goal, None, cache, mode="replay", headless=True)
        assert replay.success, f"the cached flow did not replay 0-LLM: {replay.note}"
        assert site.posts, "replay fired no POST — the recipe lost the write"
        assert all(site.posts), (
            f"a replayed write carried no Idempotency-Key: {site.posts} — a resume double-submits")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_deferred_write_is_still_refused_never_guessed(tmp_path: Path) -> None:
    """The signal's honesty, and the reason it can be trusted at all.

    A write dispatched on a timer runs in a FRESH turn, so `__ucturn` is 0 and the page reports no
    cause. It must refuse — not fall back to "the step that was acting", which is precisely the
    mis-attribution the reverted 0.73.0 drain produced (measured: a 450ms debounce credited to step 1,
    an 800ms one to step 2, while the real commit was step 0).

    This is a CAPABILITY BOUND, stated rather than hidden: a debounced commit is not authorable by
    `learn()`, and `record()` refuses it for the identical reason via the identical code."""
    site = _Site(_page(_DEFERRED))
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "work the panel"
    try:
        report = await run_cached(f"{base}/", goal, _prov(trailing=True), cache, mode="learn",
                                  headless=True, verify_replay=False)
        assert site.posts, "the deferred POST never landed; this test would prove nothing"
        assert report.extra.get("write_unattributed") is True, (
            "a deferred write has no provable cause — reporting one would be a guess")
        assert cache.get(flow_key(goal, f"{base}/")) is None, "and it must not cache"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_turn_machinery_is_shared_with_the_recorder_not_transcribed() -> None:
    """R3.1's lesson, applied before it can bite. `rowIdOf` existed twice, the copies had to agree, and
    they had silently diverged by the time an audit looked. The turn/wire machinery is the same hazard:
    the recorder and the learn path must compute attribution identically or one of them is wrong.

    Pinned structurally, because a behavioural test cannot see a divergence that has not happened yet."""
    from ultracua.attribution import WIRE_ATTRIB_JS
    from ultracua.recorder import _CAPTURE_JS

    assert WIRE_ATTRIB_JS.strip(), "the shared snippet must not be empty or this passes vacuously"
    assert "__ucturn" in WIRE_ATTRIB_JS and "attributedSeq" in WIRE_ATTRIB_JS
    assert WIRE_ATTRIB_JS in _CAPTURE_JS, (
        "the recorder must INCLUDE the shared snippet verbatim, not keep its own copy")
    assert _CAPTURE_JS.count("const attributedSeq") == 1, (
        "attributedSeq is defined more than once — the two copies can diverge, which is R3.1")


# ==================== what the causal signal must NOT do ====================


def test_a_telemetry_beacon_is_not_a_causal_write() -> None:
    """The sibling guard both other consumers already have. `recordWire` marks every non-idempotent
    request regardless of host — including `navigator.sendBeacon` — but `flow._watch_request` filters
    through `safety.is_write_request` and so does the recorder. The learn path's causal branch was the
    one consumer without it.

    Measured consequence: on any site with Segment/GA click instrumentation, an inert click whose
    handler fires a beacon becomes `causal_steps={that step}`, which then REPLACED the temporal set and
    moved the gate off the real commit. Pure, so this is provable without a browser."""
    from ultracua.attribution import attribute

    events = [
        {"action": "__commit", "seq": 1},
        {"action": "__wirewrite", "method": "POST", "url": "https://api.segment.io/v1/t", "seq": 1},
    ]
    wrote, unattributed = attribute(events, {1: 0})
    assert wrote == set(), "a telemetry beacon is not a state-changing write and must not gate a step"
    assert unattributed == 0, "...nor be counted as a write whose cause we failed to prove"


def test_a_real_write_is_still_attributed() -> None:
    """The control for the filter — it must reject telemetry, not everything."""
    from ultracua.attribution import attribute

    events = [
        {"action": "__commit", "seq": 4},
        {"action": "__wirewrite", "method": "POST", "url": "https://shop.example.test/api/order",
         "seq": 4},
    ]
    assert attribute(events, {4: 2}) == ({2}, 0)


def test_causal_attribution_never_removes_a_gate_the_temporal_rule_earned() -> None:
    """AUGMENT, NOT REPLACE — the rule this slice is built on, pinned directly because nothing else
    pinned it. The causal signal is better evidence where it speaks, but a write the temporal rule DID
    attribute is still a real write that needs a gate. Replacing the set wholesale dropped it: measured,
    a beacon-attributed step displaced the genuine commit and the flow cached with the commit as a read.
    """
    from ultracua.flow import _merge_attribution

    # temporal named step 3; causal names step 1. Both must end up gated.
    assert _merge_attribution({3}, {1}) == {1, 3}
    assert _merge_attribution(set(), {1}) == {1}
    assert _merge_attribution({3}, set()) == {3}, "no causal evidence must not erase the temporal gate"
