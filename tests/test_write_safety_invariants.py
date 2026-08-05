"""The write-safety inviolable, asserted ONCE over a generated space of flow shapes.

WHY THIS FILE EXISTS — the honest diagnosis, measured rather than assumed.

Mutation-testing the suite (nine deliberate defects, each a class this project has actually shipped:
the R3.2 refusal removed, the wire-vs-classifier check dropped, `cache.get` failing open, the
row-identity discrimination test removed, the Idempotency-Key dropped on replay, the precondition gate
disabled, write detection blinded, MCP exposing unapproved flows, the promotion loop's silent drop)
found that **all nine are caught**. The suite is not weak at what it covers.

But mutation testing can only probe guards that EXIST, and every defect this project has shipped was a
guard that was MISSING. That is the whole pattern: ~44 findings across three adversarial audit rounds,
and **not one of them was discovered by the test suite**. The suite is regression-shaped — one bespoke
test per known defect, each asserting a specific scenario — so it proves known bugs stay fixed and is
structurally incapable of failing for a bug nobody has thought of yet.

This file is the other shape. It states inviolable #3 as a PROPERTY and checks it across a cross-product
of flow shapes, so a combination nobody sat down and wrote a test for can still fail:

    If a POST reached the server while learning, then EITHER the flow was refused,
    OR the cached recipe has a gated step AND every POST on replay carries an Idempotency-Key.

Dimensions crossed here — each is a place a real defect has lived:
  * WHERE the commit is (first action vs a later one)     — R3.2 attributes only the first
  * HOW it commits (formless fetch / real form POST / XHR) — A9/A5: the classifier misses two of three
  * WHAT ELSE looks mutating (a benign sibling whose intent trips the keyword classifier) — R3.2's
    silent-wrong, where the gate lands on the step that never writes

Add a dimension here rather than a new bespoke test the next time a defect is found in this area. A cell
that fails is a real defect: the flow wrote without a gate, gated the WRONG step, or replayed a write
un-keyed.

**The gate must be on the step that WROTE** (slice S1, closing R4.6/H1). The first version of this file
asserted only that SOME step was gated, and round 4 showed what that permits: a telemetry beacon
promoted an inert click to `mutating`, the genuine commit cached as a read, and all 24 cells stayed
green. See the measurement at that assertion — on today's code the property is already protected, but
only as a side effect of two unrelated guards, and it halves the moment either is weakened.
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
    """Records every POST and whether it carried an Idempotency-Key."""

    def __init__(self, page: str) -> None:
        self.page = page
        self.posts: list[str] = []          # the Idempotency-Key header, "" when absent

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
                self._send("<h1>committed</h1>")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# --- the commit control, three ways a real page commits ---------------------------------------
# All three are SYNCHRONOUS on purpose: a deferred write is R3.2's open residual, and mixing it in
# would make these cells race rather than test the property.
_COMMITS = {
    # A formless JS POST. `classify_mutation` has no form context — the A9 shape.
    "fetch": "<button id='c' type='button' onclick=\"fetch('/commit',{method:'POST',body:'x=1'})\">"
             "@LABEL@</button>",
    # A real form POST. The classifier reads method=post and gates it correctly.
    "form": "<form action='/commit' method='post'><input type='hidden' name='x' value='1'>"
            "<button id='c' type='submit'>@LABEL@</button></form>",
    # XHR. Another entry point the classifier cannot see.
    "xhr": "<button id='c' type='button' onclick=\"var r=new XMLHttpRequest();"
           "r.open('POST','/commit');r.send('x=1')\">@LABEL@</button>",
}

_NOOP = "<button id='noop' type='button'>Filter</button>"


def _page(commit_html: str, *, commit_first: bool) -> str:
    noop = _NOOP
    body = (commit_html + noop) if commit_first else (noop + commit_html)
    return ("<h1>Panel</h1>" + body +
            "<script>document.querySelectorAll('button').forEach(function(b){"
            "b.addEventListener('click',function(){"
            "document.querySelector('h1').textContent='clicked '+b.id;});});</script>")


# (commit_kind, commit_first, sibling_intent, commit_intent).
#
# `commit_intent` is the dimension that matters most and the one an earlier draft of this file MISSED:
# with a mutating intent on the commit step, `classify_mutation` gates it from the words alone and every
# cell passed trivially — the matrix never exercised a classifier-BLIND commit, which is precisely where
# R3.2's silent-wrong lives. Instrumenting the cells is what exposed that; a matrix is only as good as
# the axis you forgot.
_MATRIX = [
    (kind, first, sibling, commit)
    for kind in ("fetch", "form", "xhr")
    for first in (True, False)
    for sibling in ("filter the list", "submit the filter")
    for commit in ("place the order", "continue")
]


def _classifier_can_see_it(kind: str, commit_intent: str) -> bool:
    """Can `classify_mutation` gate this commit WITHOUT any wire evidence? A real `<form method=post>`
    submit reads as mutating from the form; a control named/intended "place the order" reads as mutating
    from the keywords. Those two shapes are the ordinary write flow and must never be refused."""
    return kind == "form" or "order" in commit_intent


def _ids(case) -> str:
    kind, first, sibling, commit = case
    return (f"{kind}-{'first' if first else 'later'}"
            f"-{'bait' if 'submit' in sibling else 'plain'}"
            f"-{'loud' if 'order' in commit else 'blind'}")


@pytest.mark.parametrize("case", _MATRIX, ids=[_ids(c) for c in _MATRIX])
async def test_a_learned_write_is_never_cached_ungated_or_replayed_unkeyed(case, tmp_path: Path) -> None:
    """INVIOLABLE #3, over the cross-product. Every cell asserts the same property; none of them asserts
    a scenario. A failing cell means the flow either cached a write with nothing gated, or replayed a
    write with no Idempotency-Key — both are double-submit hazards, not style."""
    kind, commit_first, sibling_intent, commit_intent = case
    # The LABEL matters as much as the intent: `classify_mutation` reads the control's accessible name
    # too, so a button called "Place order" is gated however blandly the step's intent is worded. An
    # earlier draft varied only the intent and every cell came back gated — the axis did nothing.
    loud = "order" in commit_intent
    commit_label = "Place order" if loud else "Continue"
    site = _Site(_page(_COMMITS[kind].replace("@LABEL@", commit_label), commit_first=commit_first))
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = f"panel-{_ids(case)}"
    try:
        acts = [
            {"action": "click", "role": "button", "name": commit_label, "intent": commit_intent},
            {"action": "click", "role": "button", "name": "Filter", "intent": sibling_intent},
        ]
        if not commit_first:
            acts.reverse()
        prov = ScriptedProvider(acts + [{"action": "done", "intent": "done"}])

        report = await run_cached(f"{base}/", goal, prov, cache, mode="learn", headless=True,
                                  verify_replay=False)
        learn_posts = list(site.posts)
        if not learn_posts:
            pytest.skip(f"{_ids(case)}: the fixture did not POST during learn — nothing to assert")

        flow = cache.get(flow_key(goal, f"{base}/"))
        if flow is None:
            # REFUSED. Safe, and it must be loud rather than a quiet success.
            assert report.success is False, (
                "a flow that wrote and was not cached must report failure, not silent success")
            # ...but refusing is NOT a free pass. A commit the classifier can see for itself — a real
            # form POST, or a control plainly labelled "Place order" — is the ORDINARY write flow, and
            # it must stay learnable. Without this half the invariant is satisfied by refusing
            # everything, which is exactly the regression the first attempt at the R3.2 safety fix
            # shipped: it failed every `test_press_gate` login flow and would have made `learn()`
            # unable to author any write at all. Safety and liveness are both properties here.
            assert not _classifier_can_see_it(kind, commit_intent), (
                f"{_ids(case)}: the classifier gates this commit on its own, so the flow is coherent "
                f"and must still be learnable — refusing it makes the ordinary write flow unauthorable")
            return

        # CACHED. Then the gate must be on the step that WROTE — not merely on SOME step.
        #
        # Slice S1, closing R4.6. The measurement behind it, because the naive version of this claim was
        # wrong and had to be corrected. Mutation: relocate every gate onto step 0.
        #
        #   guards intact                          old 8/24 fail, new 8/24 — NO delta
        #   Idempotency-Key made flow-scoped AND
        #   the consistency check relaxed          old 6/24 fail, new 12/24 — detection DOUBLES
        #
        # So on today's code a misplaced gate is already caught, but only EMERGENTLY: the wire-vs-
        # classifier consistency rule refuses the flow, or the per-step key makes the replayed POST come
        # back unkeyed three assertions later. Neither of those guards is about gate placement, and when
        # they are weakened — a flow-scoped key and a relaxed consistency rule are both plausible
        # refactors — wrong-gate detection halves. This assertion makes the property ASSERTED rather than
        # emergent, and names the actual defect instead of reporting it as a missing Idempotency-Key.
        #
        # It is also the guard that would have caught round 4's clobber, where causal attribution moved
        # the gate onto a telemetry-firing step WITHOUT tripping the consistency rule.
        #
        # Over-gating is permitted deliberately: a spare Idempotency-Key on a step that does not write
        # costs nothing, a missing one costs a double-submit. So this asserts membership, not equality.
        gated = [i for i, s in enumerate(flow.steps) if s.mutating]
        assert gated, (
            f"{_ids(case)}: a POST reached the server during learn and the flow cached with NO mutating "
            f"step — it will replay with no gate, no precondition and no Idempotency-Key. "
            f"steps={[(s.intent, s.mutating) for s in flow.steps]}")
        commit_index = 0 if commit_first else 1
        assert commit_index in gated, (
            f"{_ids(case)}: the gate is on step(s) {gated} but the step that WROTE is {commit_index} "
            f"({flow.steps[commit_index].intent!r}). The Idempotency-Key, the precondition and the "
            f"drift gate all ride the wrong row; the real commit replays ungated. "
            f"steps={[(s.intent, s.mutating) for s in flow.steps]}")

        # ...and the write must actually ride a key on a 0-LLM replay.
        site.posts.clear()
        replay = await run_cached(f"{base}/", goal, None, cache, mode="replay", headless=True)
        assert replay.success, f"{_ids(case)}: the cached flow did not replay: {replay.note}"
        assert site.posts, f"{_ids(case)}: replay fired no POST, so the recipe lost the write entirely"
        unkeyed = [p for p in site.posts if not p]
        assert not unkeyed, (
            f"{_ids(case)}: {len(unkeyed)} of {len(site.posts)} replayed POST(s) carried NO "
            f"Idempotency-Key — a resume or retry double-submits. gated steps={gated}")
    finally:
        httpd.shutdown()
        httpd.server_close()
