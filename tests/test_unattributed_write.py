"""A wire write nothing could claim must REFUSE — as a fact, in the mechanism (R3.2's safety half).

R3.2 is that `_write_owner` credits nobody for essentially every step past the first. That reads as
fail-safe: nothing is marked `mutating`, so `_learn_once` refuses. It was not, on two counts.

THE INFERENCE. The refusal asked `not any(s.mutating for s in cached.steps)`. `classify_mutation` marks
a step mutating from its INTENT TEXT alone, so a benign sibling whose intent happens to read "submit the
search" makes `any(...)` true and disarms it. Measured on 0.73.0, with the real commit on a later step:

    no keyword sibling   -> refused (loud, safe)
    keyword sibling      -> CACHED: [(0,'submit the search',True), (1,'continue',False)]
                            the real commit cached as a READ — no gate, no precondition, no
                            Idempotency-Key, and the three of them attached to a step that never writes

THE PLACEMENT. That guard lived only in `flows._learn_once`. `ultracua run` and the daemon call
`flow.run_cached` directly and never reach it, so it protected one of three callers. A guard in the
wrapper rather than in the mechanism is this codebase's most-repeated defect shape.

THE RULE THAT REPLACED IT is a CONSISTENCY check, not an attribution — R3.2 is still open, and nothing
here claims to have closed it. When a wire write cannot be attributed, the wire and the classifier must
at least AGREE about where it is: the step that was in flight when the write fired must be one the
recipe gates. Agreement means the Idempotency-Key, the precondition and the drift gate sit on a row that
really was mid-flight when a write left the browser. Disagreement means they sit on the wrong row.

Getting the BALANCE right is the whole difficulty, and the first version of this fix got it wrong in the
other direction. It refused on "unattributed" alone, and fill-a-field-then-submit — the ordinary shape
of a write flow, whose commit is never the first step — is unattributable under R3.2. Measured: it
failed every `test_press_gate` login flow, i.e. it would have made essentially every real write flow
unlearnable. Those flows are FINE: the classifier gates the submit step, and the wire agrees.

So the tests come in two directions, and both are load-bearing:
  * refuse where the two disagree (the silent-wrong at the top of this file);
  * keep learning where they agree (`test_the_ordinary_fill_then_submit_flow_still_learns`).
A change that satisfies only one of them is broken, and would pass half this file.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.flows import FlowSpec, _learn_once
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
                site.posts.append(self.path.split("?")[0])
                self._send("{}", "application/json")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# "Search" writes nothing. "Continue" fires a real POST — it is the commit, and it is NOT the first step,
# so R3.2's rule leaves it unattributed.
_LATER_COMMIT = """<h1>Panel</h1>
<button id='s' type='button'>Search</button>
<button id='c' type='button' onclick="fetch('/api/commit',{method:'POST',body:'x=1'})">Continue</button>
<script>
for (const id of ['s','c']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""

# The control: the commit is the FIRST action, which R3.2's rule CAN attribute. This must still cache.
_FIRST_COMMIT = """<h1>Panel</h1>
<button id='c' type='button' onclick="fetch('/api/commit',{method:'POST',body:'x=1'})">Continue</button>
<button id='s' type='button'>Search</button>
<script>
for (const id of ['s','c']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""

# The ordinary write flow: the commit is a LATER step (so R3.2 leaves it unattributed) AND the classifier
# gates it correctly from its intent. Wire and classifier AGREE — this must keep learning.
_LATER_COMMIT_GATED = """<h1>Cart</h1>
<button id='f' type='button'>Filter</button>
<button id='p' type='button' onclick="fetch('/api/commit',{method:'POST',body:'x=1'})">Place order</button>
<script>
for (const id of ['f','p']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""

# The other control: no write anywhere. An ordinary read flow must be entirely unaffected.
_READ_ONLY = """<h1>Panel</h1>
<button id='s' type='button'>Search</button>
<button id='n' type='button'>Next</button>
<script>
for (const id of ['s','n']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""


def _prov(*names_and_intents) -> ScriptedProvider:
    return ScriptedProvider(
        [{"action": "click", "role": "button", "name": n, "intent": i} for n, i in names_and_intents]
        + [{"action": "done", "intent": "done"}])


# ==================== the inference, and the sibling that disarmed it ====================


async def test_a_keyword_mutating_sibling_no_longer_disarms_the_refusal(tmp_path: Path) -> None:
    """THE regression. `submit the search` is a benign click whose INTENT TEXT trips the classifier; the
    real commit is the next step. Before, `any(s.mutating)` was true and the flow cached with the gate,
    the precondition and the Idempotency-Key on the step that never writes."""
    site = _Site(_LATER_COMMIT)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        res = await _learn_once(
            spec, provider=_prov(("Search", "submit the search"), ("Continue", "continue")),
            router=None, cache=cache, verify_replay=False)
        assert site.posts == ["/api/commit"], "the fixture did not POST; this would prove nothing"
        # 0.76.0 ATTRIBUTES this instead of refusing it: the commit fires synchronously from its own
        # click, so the page names its cause and the gate lands on step 1. The property this test
        # protects is unchanged and is asserted directly — the gate must never sit ONLY on the step that
        # does not write. Refusing was the old way of honouring it, when nothing could be attributed.
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        if flow is None:
            assert res.cached is False and "attributed" in (res.note or "")
        else:
            commit = [st for st in flow.steps if st.intent == "continue"]
            assert commit and commit[0].mutating is True, (
                f"the real commit must be gated; got {[(x.intent, x.mutating) for x in flow.steps]}")


    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_the_same_refusal_without_a_keyword_sibling(tmp_path: Path) -> None:
    """The case that already worked, kept so a future change cannot fix one and break the other."""
    site = _Site(_LATER_COMMIT)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        res = await _learn_once(
            spec, provider=_prov(("Search", "look at the panel"), ("Continue", "continue")),
            router=None, cache=cache, verify_replay=False)
        assert site.posts == ["/api/commit"]
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        if flow is not None:            # attributed (0.76.0) — then the COMMIT must carry the gate
            commit = [st for st in flow.steps if st.intent == "continue"]
            assert commit and commit[0].mutating is True
        else:                            # refused — also acceptable, and it must be loud
            assert res.cached is False
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== the placement: every caller, not just `_learn_once` ====================


async def test_run_cached_refuses_it_too_not_only_the_flows_wrapper(tmp_path: Path) -> None:
    """`ultracua run` and the daemon call `run_cached` DIRECTLY and never reach `flows._learn_once`, so a
    guard living only there covered one of three callers. The refusal is in the mechanism now."""
    site = _Site(_LATER_COMMIT)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "work the panel"
    try:
        report = await run_cached(
            f"{base}/", goal, _prov(("Search", "submit the search"), ("Continue", "continue")),
            cache, mode="learn", headless=True, verify_replay=False)
        assert site.posts == ["/api/commit"]
        flow = cache.get(flow_key(goal, f"{base}/"))
        if report.extra.get("write_unattributed"):
            # Unattributable -> the MECHANISM refuses, not just the `flows` wrapper. That placement is
            # what this test exists for: `ultracua run` and the daemon never reach `_learn_once`.
            assert report.extra.get("cached") is False, "the engine itself must not cache it"
            assert flow is None
        else:
            assert flow is not None
            commit = [st for st in flow.steps if st.intent == "continue"]
            assert commit and commit[0].mutating is True, "the real commit must carry the gate"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_write_attributed_to_a_step_whose_act_failed_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The promotion loop's own hole. A write CAN be attributed to step 0 and still have no cached step to
    carry its gate, because a failed act appends nothing — `pos_of.get(i)` is None. That branch used to
    `continue` silently, dropping a write we had positively identified: the flow cached without it and
    replay fired it ungated. It is exactly as unattributable as one nobody claimed.

    Driven by making the act throw AFTER the click has already fired the POST, so the write is real and
    the step is absent — deterministic, where racing a real navigation failure would not be."""
    from ultracua.browser import BrowserSession

    site = _Site(_FIRST_COMMIT)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "work the panel"
    real_act = BrowserSession.act

    async def _act_then_fail(self, action):
        await real_act(self, action)
        raise RuntimeError("post-act failure (simulated) — the write already left the browser")

    monkeypatch.setattr(BrowserSession, "act", _act_then_fail)
    try:
        report = await run_cached(f"{base}/", goal, _prov(("Continue", "continue")), cache,
                                  mode="learn", headless=True, verify_replay=False)
        assert site.posts == ["/api/commit"], "the fixture did not POST before failing"
        assert report.extra.get("write_unattributed") is True, (
            "a write with no step to carry its gate must be refused, not silently dropped")
        assert cache.get(flow_key(goal, f"{base}/")) is None
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== the other direction: this must not refuse the ordinary write flow ============


async def test_the_ordinary_fill_then_submit_flow_still_learns(tmp_path: Path) -> None:
    """THE balance point, and the thing a blunter version of this fix broke.

    Fill a field, then click Submit: the commit is NEVER the first step, so R3.2's rule leaves the write
    unattributed. But `classify_mutation` marks the submit step correctly, and the write fires while THAT
    step is in flight — so the recipe's gate, precondition and Idempotency-Key are all on the right row
    and the flow is coherent. Refusing it would make essentially every real write flow unlearnable.

    Measured: an earlier version of this change refused on `unattributed` alone and failed every
    `test_press_gate` login flow for exactly this reason. The wire and the classifier AGREE here; they
    disagree in the test at the top of this file. That difference is the whole rule.
    """
    site = _Site(_LATER_COMMIT_GATED)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "place the order"
    try:
        report = await run_cached(
            f"{base}/", goal, _prov(("Filter", "filter the list"), ("Place order", "place the order")),
            cache, mode="learn", headless=True, verify_replay=False)
        assert site.posts == ["/api/commit"], "the fixture did not POST; this would prove nothing"
        assert not report.extra.get("write_unattributed"), (
            "the classifier gated the step that was in flight when the write fired — refusing this "
            "shape would make the ordinary write flow unlearnable")
        flow = cache.get(flow_key(goal, f"{base}/"))
        assert flow is not None, "the ordinary act-then-commit flow must still cache"
        assert flow.steps[1].mutating is True, "...with its commit gated"
        assert flow.steps[0].mutating is False
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== the controls: this must not refuse everything ====================


async def test_an_attributable_write_still_caches_and_is_still_gated(tmp_path: Path) -> None:
    """The control that matters most. R3.2's rule CAN attribute a write fired by the first action, and
    that path must keep working — a refusal that fires on every write flow would pass every test above
    and leave `learn()` unable to author anything that writes."""
    site = _Site(_FIRST_COMMIT)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "work the panel"
    try:
        # ONE step, deliberately. With a second step, whether the write lands before step 1's act opens
        # is a race: attribution succeeds inside step 0's window and fails outside it, so the assertion
        # below would flip with machine load. A single-step flow has no window to race against.
        report = await run_cached(f"{base}/", goal, _prov(("Continue", "continue")),
                                  cache, mode="learn", headless=True, verify_replay=False)
        assert site.posts == ["/api/commit"]
        # `not ...get(...)` rather than `is False`: this is a CONTROL, and it must pass against the
        # pre-fix source too (where the key does not exist). A control that only passes after the change
        # proves nothing about the change not having broken anything.
        assert not report.extra.get("write_unattributed")
        flow = cache.get(flow_key(goal, f"{base}/"))
        assert flow is not None, "an attributed write must still cache"
        assert flow.steps[0].mutating is True, "...and its commit must still be gated"
        assert flow.steps[0].precond_scope != ""
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_read_only_flow_is_untouched(tmp_path: Path) -> None:
    """The other control: no write anywhere means nothing about this change may apply."""
    site = _Site(_READ_ONLY)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "work the panel"
    try:
        report = await run_cached(f"{base}/", goal, _prov(("Search", "search"), ("Next", "next")),
                                  cache, mode="learn", headless=True, verify_replay=False)
        assert site.posts == []
        assert not report.extra.get("write_unattributed")   # a control — must hold before AND after
        assert report.success is True
        flow = cache.get(flow_key(goal, f"{base}/"))
        assert flow is not None and not any(s.mutating for s in flow.steps)
    finally:
        httpd.shutdown()
        httpd.server_close()
