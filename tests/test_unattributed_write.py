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
from dataclasses import replace
from pathlib import Path

import pytest

import ultracua.flow as _flowmod
from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.flows import FlowSpec, MutateSpec, _learn_once, approve, record, replay
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

# AB-1. The write is deferred FORWARD — out of its own step and onto a classifier-mutating neighbour.
#
# This is the MIRROR of `_LATER_COMMIT` above, and the ordering nothing covered. There the bait is FIRST
# and the real commit second, so `acting_at_write` (the bait was not in flight) and `gated` (the bait)
# DISAGREE and the flow is correctly refused. Here the commit is first and defers into the bait, so the
# two AGREE — by accident, on the step that never writes — the consistency check is satisfied, and the
# flow caches with the gate, the precondition and the Idempotency-Key on the wrong row.
#
# Deterministic by construction, not by timing. Measured with a plain `setTimeout(..., D)` the outcome is
# a three-way race decided by a stopwatch: at D=60ms the gate lands on the wrong step 6 runs in 8, at
# D=150ms the flow is refused 6 in 8. Instead the write is armed by the commit and released by the BAIT's
# own click, via a `window` capture listener (which runs before any target handler) and a 0 ms timer
# (which fires in the next bare task) — so the write lands while the bait is the step in flight, every
# time, with no artificial load and nothing to tune.
_DEFERRED_ONTO_BAIT = """<h1>Panel</h1>
<button id='c' type='button'>Continue</button>
<button id='a' type='button'>Confirm address</button>
<script>
document.getElementById('c').addEventListener('click', function(){ window.__armed = 1; });
window.addEventListener('click', function(ev){
  if (window.__armed && ev.target && ev.target.id === 'a') {
    window.__armed = 0;
    setTimeout(function(){ fetch('/api/commit', {method:'POST', body:'x=1'}); }, 0);
  }
}, true);
for (const id of ['c','a']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""

# R4.5. The two below are `_DEFERRED_ONTO_BAIT` with the write pushed ONE MORE bare task out, and they
# differ in exactly one thing: whether the write leaves inside a click the PAGE dispatched.
#
# WHY ONE MORE TASK, and why that is not a fudge. The capture script closes a turn by arming
# `setTimeout(reset, 0)` from its own `document` capture listener. `window` capture runs BEFORE
# `document` capture, so the page's release timer is armed FIRST and therefore fires FIRST — at that
# point `__ucturn` is still 1 from the bait's click, and a synthetic commit there would read 2 and be
# refused for a reason that has nothing to do with R4.5. Hopping one further task puts the reset behind
# us and `__ucturn` back to 0: the ordinary deferred-write state, and the one an attacker-shaped page
# would find. Equal-delay timers fire in ARMING order, so this is deterministic by construction — the
# same property the R4.26 harness leans on, and the reason neither test below needs artificial load.
_DEFERRED_TWO_TASKS = """<h1>Panel</h1>
<button id='c' type='button'>Continue</button>
<button id='a' type='button'>Confirm address</button>
<button id='h' type='button' style='display:none'>Refresh</button>
<script>
document.getElementById('c').addEventListener('click', function(){ window.__armed = 1; });
window.addEventListener('click', function(ev){
  if (window.__armed && ev.target && ev.target.id === 'a') {
    window.__armed = 0;
    setTimeout(function(){ setTimeout(function(){
      fetch('/api/commit', {method:'POST', body:'x=1'});
    }, 0); }, 0);
  }
}, true);
for (const id of ['c','a']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""

# ...and the same page where the deferred task SYNTHESISES a commit and writes inside its dispatch.
_DEFERRED_VIA_SYNTHETIC_CLICK = """<h1>Panel</h1>
<button id='c' type='button'>Continue</button>
<button id='a' type='button'>Confirm address</button>
<button id='h' type='button' style='display:none'>Refresh</button>
<script>
document.getElementById('h').addEventListener('click', function(){
  fetch('/api/commit', {method:'POST', body:'x=1'});
});
document.getElementById('c').addEventListener('click', function(){ window.__armed = 1; });
window.addEventListener('click', function(ev){
  if (window.__armed && ev.target && ev.target.id === 'a') {
    window.__armed = 0;
    setTimeout(function(){ setTimeout(function(){
      document.getElementById('h').dispatchEvent(new MouseEvent('click', {bubbles: true}));
    }, 0); }, 0);
  }
}, true);
for (const id of ['c','a']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""

# The SECOND member of the laundering class, and the reason the test below is parametrized. `el.click()`
# is not an exotic variant — it is what the parked round-4 probe used — and it launders identically
# (measured 3/3 at 0.90.0). Pinning only `dispatchEvent` would let a shape-specific fix XPASS, and the
# strict marker would then be deleted as "fixed" with half the class still open.
_DEFERRED_VIA_EL_CLICK = _DEFERRED_VIA_SYNTHETIC_CLICK.replace(
    "document.getElementById('h').dispatchEvent(new MouseEvent('click', {bubbles: true}));",
    "document.getElementById('h').click();")

# R4.5 on the RECORD path, where the harm is a different and worse one. Same laundering, but the target
# is VISIBLE and the write reveals a confirm, so the flow is recordable as a declared write. The
# synthetic click is captured as a REAL STEP (`record` is not in attribution-only mode), so the recipe a
# human approves contains an action nobody performed — and replaying it fires the commit twice: once
# because the page still defers its own write, once because replay actuates the phantom step.
_R45_RECORD_PAGE = """<h1>Panel</h1>
<button id='c' type='button'>Continue</button>
<button id='a' type='button'>Confirm address</button>
<button id='h' type='button'>Refresh</button>
<script>
document.getElementById('h').addEventListener('click', function(){
  fetch('/api/commit', {method:'POST', body:'x=1'}).then(function(){
    document.querySelector('h1').textContent = 'Saved';
  });
});
document.getElementById('c').addEventListener('click', function(){ window.__armed = 1; });
window.addEventListener('click', function(ev){
  if (window.__armed && ev.target && ev.target.id === 'a') {
    window.__armed = 0;
    setTimeout(function(){ setTimeout(function(){
      document.getElementById('h').dispatchEvent(new MouseEvent('click', {bubbles: true}));
    }, 0); }, 0);
  }
}, true);
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


def _pin_the_ab1_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the AB-1 arm reachable BY CONSTRUCTION instead of by the host being fast enough.

    Every test below that spies on `_wire_writes_are_provable` depends on a wall-clock OVERLAP: the arm
    runs only when `_write_owner` sees TWO live candidates — step 0's `write_window_ms` grace tail still
    open under step 1's act window. Exceed the tail with one slow inter-step gap and the write acquires a
    unique owner, the whole block is SKIPPED, and the test is evaluating nothing.

    THAT IS NOT HYPOTHETICAL — it failed on an ubuntu CI shard (PR #143, `PREMISE LOST`), and the same
    test failed the same way once before as a bare `gated=[1]` (PR #142, which is why the premise pins
    exist at all). It reproduces on a fast quiet Windows host in seconds by shrinking the tail instead of
    waiting for a slow host, which is the same experiment from the other end:

        write_window_ms   AB-1 arm reached   gated
                  30000         yes          [0, 1]
                   2000         yes          [0, 1]      <- the default; ~10x local headroom
                    200         yes          [0, 1]
                     50         NO           [1]         <- the ubuntu failure, deterministically
                     10         NO           [1]

    So the cause is the overlap, not the platform: locally the inter-step gap is <200 ms against a 2000 ms
    tail, and a loaded shared runner exceeding 2 s is ordinary — this register has recorded 5 s localhost
    stalls twice under R4.24.

    WHAT THIS IS NOT. It is not a de-flake by suppression, which S17 forbids after that verb turned out to
    be hiding a live write-safety hole: the production default is untouched (this rebinds only the module
    reference these tests import), no assertion is weakened, the premise pins stay, and nothing reruns.
    It makes a test's own premise deterministic, which is R4.26's lesson applied to the harness.

    What it does NOT make deterministic is the product's behaviour at the boundary — see R4.28, filed
    from the table above: `_write_owner` becomes CONFIDENT because a neighbour's tail expired.
    """
    monkeypatch.setattr(_flowmod, "settings", replace(_flowmod.settings, write_window_ms=30_000))


def _attribution_state(cache: FlowCache, spec: FlowSpec, res=None) -> str:
    """What the attribution machinery ACTUALLY did, for a premise-loss message to report.

    THIS EXISTS BECAUSE TWO CONSECUTIVE DIAGNOSES OF THE SAME UBUNTU FAILURE WERE WRONG. The premise
    message used to assert a cause — "most likely an inter-step gap exceeded `write_window_ms`" — and a
    measured sweep then showed the pin above surviving gaps of 0/1/3/6 s while CI still lost the premise.
    An inference dressed as an observation is the move this register has had to withdraw before (R4.5's
    Idempotency-Key clause), and it costs a CI round every time.

    `mutating_sources` is the sensor that settles it, because the mark records WHICH SIGNAL set it:

        a step carrying `wire`      the write WAS observed and uniquely attributed to that step —
                                    `wrote_by_step` was non-empty, so the AB-1 block was skipped
        `overgate` present          AB-1 ran and blanketed; the arm was reached
        no `wire` anywhere          the write was never attributed to ANY step: either `_in_act_window`
                                    was false when the request fired, or the watcher had already been
                                    removed. Nothing to do with the tail length.
        `keyword` only              the classifier alone; the wire contributed nothing

    Those are different failures with different fixes, and the old message could not tell them apart.
    """
    flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
    if flow is None:
        note = getattr(res, "note", None)
        return f"no flow cached (cached={getattr(res, 'cached', None)!r}, note={note!r})"
    rows = [(s.intent, s.mutating, s.mutating_sources) for s in flow.steps]
    marks = {m for s in flow.steps for m in (s.mutating_sources or [])}
    if "wire" in marks:
        verdict = "the write WAS uniquely attributed (a step carries `wire`), so the AB-1 block was SKIPPED"
    elif "overgate" in marks:
        verdict = "AB-1 ran and blanketed (`overgate` present)"
    else:
        verdict = ("the write was attributed to NO step — it fired outside every act window / grace "
                   "tail, or after the watcher was removed. The tail length is not the variable here")
    return f"{verdict}. steps={rows}"


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
        assert res.cached is False, "a write nothing could claim must never cache"
        assert "attributed" in (res.note or "")
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None


    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_write_deferred_onto_a_mutating_neighbour_never_leaves_its_commit_ungated(
        tmp_path: Path) -> None:
    """AB-1. The sibling ordering of the test above, and the one the consistency check cannot judge.

    `acting_at_write` answers "which step was in flight when the wire saw a write" — NOT "which step
    caused it". When a write is deferred forward onto a classifier-mutating neighbour those two come
    apart, the check's agreement is an accident, and trusting it puts the Idempotency-Key, the
    precondition and the drift gate on a step that never writes while the real commit replays with
    none of them.

    The property, not the mechanism: a write that nothing could attribute must never leave the step
    that actually committed UNGATED. Refusing satisfies that; so does gating every candidate. What is
    not allowed is caching a confident-looking gate on the wrong row.
    """
    site = _Site(_DEFERRED_ONTO_BAIT)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        res = await _learn_once(
            spec, provider=_prov(("Continue", "continue"), ("Confirm address", "confirm the address")),
            router=None, cache=cache, verify_replay=False)
        assert site.posts == ["/api/commit"], "the fixture did not POST; this would prove nothing"
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        if flow is None:
            assert res.cached is False and res.note, "a refusal must be loud and carry its reason"
            return
        gated = [i for i, s in enumerate(flow.steps) if s.mutating]
        assert 0 in gated, (
            f"the commit is step 0 and it cached UNGATED while the gate sits on {gated} — a step that "
            f"never writes. It will replay with no drift gate, no precondition and no Idempotency-Key, "
            f"and it stays heal- and replan-eligible. "
            f"steps={[(s.intent, s.mutating) for s in flow.steps]}")
        for i in gated:
            assert flow.steps[i].precond_scope, (
                f"step {i} is marked mutating but carries no precondition, so its mutation gate has "
                f"nothing to refuse under drift")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_the_control_a_write_deferred_two_tasks_is_still_over_gated(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R4.5's CONTROL, and it is what makes the xfail below mean anything.

    Identical to the R4.5 page except that the deferred task issues the write directly instead of
    dispatching a click first. If this ever goes red, the extra task — not the synthetic click — is
    what moved the verdict, and the finding below is misdiagnosed rather than fixed.
    """
    _pin_the_ab1_overlap(monkeypatch)

    seen: list[bool] = []
    _real_provable = _flowmod._wire_writes_are_provable

    async def _spy(pg):
        out = await _real_provable(pg)
        seen.append(bool(out))
        return out

    monkeypatch.setattr(_flowmod, "_wire_writes_are_provable", _spy)

    site = _Site(_DEFERRED_TWO_TASKS)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        res = await _learn_once(
            spec, provider=_prov(("Continue", "continue"), ("Confirm address", "confirm the address")),
            router=None, cache=cache, verify_replay=False)
        assert site.posts == ["/api/commit"], "the fixture did not POST; this would prove nothing"
        # THE PREMISE PIN ITS SIBLING HAD AND THIS DID NOT — added after ubuntu CI failed here while
        # windows passed, which is this register's own sibling-guard shape created inside the slice that
        # documented it. The AB-1 arm is reached only when `_write_owner` finds TWO live candidates; if
        # the write is uniquely attributed instead (different timer/act-window interleaving, and Linux
        # schedules it differently from Windows) the whole block is skipped and `gated` is [1] — at
        # which point this test's failure message asserts a conclusion it cannot support, namely that
        # the extra bare task defeated the over-gating. That is a premise loss, and it must ERROR as one
        # rather than accuse the mechanism.
        if not seen:
            raise RuntimeError(
                f"PREMISE LOST: the AB-1 adjudication was never reached, so "
                f"`_wire_writes_are_provable` was never consulted. This control says nothing about "
                f"over-gating on such a run. OBSERVED: {_attribution_state(cache, spec, res)}")
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        if flow is None:
            assert res.cached is False and res.note, "a refusal must be loud and carry its reason"
            return
        gated = [i for i, s in enumerate(flow.steps) if s.mutating]
        assert 0 in gated, (
            f"deferring the write one extra bare task defeated the over-gating on its own, with no "
            f"synthetic click involved. gated={gated} "
            f"steps={[(s.intent, s.mutating) for s in flow.steps]}")
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.parametrize("shape,page", [
    pytest.param("dispatchEvent", _DEFERRED_VIA_SYNTHETIC_CLICK, id="dispatchEvent"),
    pytest.param("el.click()", _DEFERRED_VIA_EL_CLICK, id="el_click"),
])
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "R4.5 — OPEN against shipped 0.89.0. A page-synthesised click manufactures a provable cause, so "
    "`_wire_writes_are_provable` returns True, S6/AB-1 trusts the placement, and the real commit "
    "caches UNGATED. Reproduced 10/10. Remove this marker with the fix, never without it."))
async def test_a_page_synthesised_click_must_not_launder_a_deferred_write(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, page: str) -> None:
    """R4.5, against the SHIPPED mechanism rather than the parked branch it was filed on.

    S6/AB-1 does not attribute; it asks whether the page can PROVE the cause of every wire write, and
    over-gates when it cannot. R4.26 hardened that signal against a page SHADOWING `window.event` by
    reading the native getter captured before page scripts run. Nothing stops a page from PERFORMING a
    dispatch instead: there is no `isTrusted` check on the commit listener, so a deferred write that
    first dispatches a click on any actionable element, then writes inside that dispatch, is stamped
    with the synthetic commit's own seq and reads as provable.

    Measured at 0.90.0 — the marker carries `seq=null` in the control above and a non-null seq here,
    and the recipe gates only the bait, so the real commit (step 0) caches with no drift gate and no
    `precond_scope`, and stays heal- and suffix-replan-eligible. Parametrized over BOTH members of the
    laundering class: `dispatchEvent(new MouseEvent(...))` and `el.click()`. Pinning one would let a
    shape-specific fix XPASS and get this marker deleted with the other half still open.

    The property is the same one AB-1 pins, and it is deliberately disjunctive: a write nothing could
    honestly attribute must never leave the step that actually committed UNGATED. Refusing satisfies
    it; so does gating every candidate. Caching a confident-looking gate on the wrong row does not.

    STRICT + `raises=AssertionError` on purpose: every premise below raises RuntimeError, so a premise
    that rots ERRORS loudly instead of being swallowed as "expected failure" — the trap an xfail
    otherwise sets for the next person. When the fix lands this XPASSes and the suite goes red until
    the marker is deleted.

    THE THIRD PREMISE IS THE ONE THAT IS EASY TO MISS, and it was found by auditing this test rather
    than the code it tests. The AB-1 arm is reached only when `_write_owner` sees TWO live candidates —
    step 0's `write_window_ms` grace tail still open under step 1's act window. Let one inter-step gap
    exceed that tail (measured headroom on this host: ~1.8 s, and this repo documents 5 s stalls twice
    under R4.24) and the write acquires a unique owner, the whole AB-1 block is SKIPPED, and the
    assertion below still fails with a byte-identical message. The xfail would then read "expected
    failure, R4.5 still open" while never evaluating the decision point at all. So the adjudication is
    spied on, and its absence is an ERROR rather than a quiet expected failure.
    """
    _pin_the_ab1_overlap(monkeypatch)

    seen: list[bool] = []
    _real_provable = _flowmod._wire_writes_are_provable

    async def _spy(pg):
        out = await _real_provable(pg)
        seen.append(bool(out))
        return out

    monkeypatch.setattr(_flowmod, "_wire_writes_are_provable", _spy)

    site = _Site(page)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        res = await _learn_once(
            spec, provider=_prov(("Continue", "continue"), ("Confirm address", "confirm the address")),
            router=None, cache=cache, verify_replay=False)
        if site.posts != ["/api/commit"]:
            raise RuntimeError(
                f"PREMISE LOST ({shape}): the fixture did not POST exactly once (posts={site.posts}), "
                f"so this test is not exercising R4.5 at all and its xfail means nothing")
        if not seen:
            raise RuntimeError(
                f"PREMISE LOST ({shape}): the AB-1 adjudication was never reached, so "
                f"`_wire_writes_are_provable` was never consulted, and this test would otherwise XFAIL "
                f"for a reason that has nothing to do with R4.5. "
                f"OBSERVED: {_attribution_state(cache, spec, res)}")
        if not any(seen):
            raise RuntimeError(
                f"PREMISE LOST ({shape}): the marker was NOT laundered (provable={seen}), so the "
                f"synthetic click did not manufacture a cause and there is no R4.5 here to observe")
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        if flow is None:
            assert res.cached is False and res.note, "a refusal must be loud and carry its reason"
            return
        if len(flow.steps) < 2:
            raise RuntimeError(
                f"PREMISE LOST ({shape}): the recipe has {len(flow.steps)} step(s); this test needs "
                f"the commit at step 0 and the bait at step 1")
        gated = [i for i, s in enumerate(flow.steps) if s.mutating]
        assert 0 in gated, (
            f"R4.5 ({shape}): the commit is step 0 and it cached UNGATED while the gate sits on "
            f"{gated} — a step that never writes. The page manufactured a provable cause by "
            f"dispatching its own click, so the placement was trusted and the over-gating never ran. "
            f"steps={[(s.intent, s.mutating) for s in flow.steps]}")
        for i in gated:
            assert flow.steps[i].precond_scope, (
                f"step {i} is marked mutating but carries no precondition")
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "R4.5 on the RECORD path — OPEN against shipped 0.89.0, and WORSE than the learn-path harm: the "
    "synthetic click is captured as a real STEP, so an approved recipe contains an action no human "
    "performed and replay DOUBLE-SUBMITS while reporting success. Measured at 0.90.0."))
async def test_a_page_synthesised_click_must_not_become_a_recorded_step(tmp_path: Path) -> None:
    """R4.5's sibling consumer, and the one the first filing of this slice missed entirely.

    `attributedSeq` has TWO consumers. The learn path reads it through `_wire_writes_are_provable` and
    the worst it does is trust a placement. `record()` reads it for per-write ATTRIBUTION, and it is
    not in attribution-only mode — so `store()` captures the page's synthetic click as a step, with a
    real locator, and the laundered seq attributes the write to it. The result is not an over-gate:

      * a demo of TWO human clicks caches a THREE-step recipe, and the extra step is one nobody
        performed — approval then blesses an action a human never reviewed, which is what the whole
        approval-digest mechanism exists to prevent;
      * replay actuates that phantom step WHILE the page still defers its own write, so the commit
        fires TWICE, under two different Idempotency-Keys, and `replay()` returns normally.

    That is inviolable #3 broken outright and #2 with it, where the learn path only loses recovery
    features. Measured at 0.90.0: `record cached=True`, 3 steps for 2 human actions, replay OK, 2 POSTs.

    Disjunctive again: refusing the demo satisfies this test, and so does recording only the human's
    steps. What is not allowed is a phantom step, or a second POST.
    """
    site = _Site(_R45_RECORD_PAGE)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="rp", goal="work the panel", start_url=f"{base}/",
                        mutate=MutateSpec(confirm_text_contains="Saved"))

        async def _demo(pg) -> None:
            await pg.get_by_role("button", name="Continue").click()
            await pg.get_by_role("button", name="Confirm address").click()
            await pg.wait_for_timeout(900)

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        if len(site.posts) != 1:
            raise RuntimeError(
                f"PREMISE LOST: the demo did not produce exactly one POST (posts={site.posts}), so the "
                f"laundering path was not exercised")
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        if flow is None or not res.cached:
            assert res.note, "a refusal must carry its reason"
            return                                    # refusing is a correct outcome for this property

        def _target(step) -> str:
            try:
                return (step.locator.name or step.locator.text or "") or ""
            except Exception:                          # noqa: BLE001 - a spec shape we don't care about
                return ""

        phantom = [(i, _target(s)) for i, s in enumerate(flow.steps)
                   if _target(s) not in ("Continue", "Confirm address")]
        assert not phantom, (
            f"R4.5 (record): the demo performed 2 clicks and the recipe cached {len(flow.steps)} steps; "
            f"{phantom} was never performed by a human. The page dispatched it, `store()` captured it "
            f"as a real step, and approval will bless it. "
            f"steps={[(_target(s), s.mutating) for s in flow.steps]}")

        before = len(site.posts)
        approve(spec, cache=cache)
        try:
            await replay(spec, cache=cache)
        except Exception:                              # noqa: BLE001 - a loud failure is acceptable here
            pass                                       # the POST COUNT is the property, not the verdict
        fired = len(site.posts) - before
        assert fired <= 1, (
            f"R4.5 (record): replay fired {fired} POSTs for one operator request — the page deferred "
            f"its own write AND replay actuated the phantom step. Inviolable #3.")
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
        assert report.extra.get("write_unattributed") is True
        assert report.extra.get("cached") is False, "the engine itself must not cache it"
        assert cache.get(flow_key(goal, f"{base}/")) is None
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
