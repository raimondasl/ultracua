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
  * WHETHER THE WRITE WAS DECLARED (slice S2, R3.5) — every shape here is an UNDECLARED write, and none
    of them may be handed the auth-refresh retry, which re-runs the flow from the start and re-fires the
    commit; a DECLARED single write with a precheck still may be

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
# All three are SYNCHRONOUS on purpose: this matrix varies WHERE the commit is and WHAT it looks
# like, not WHEN its write leaves. That second axis is a dimension of its own and it lives in the
# `record`-path matrix at the bottom of this file — where, contrary to what this comment used to
# claim, it does NOT have to race (R4.26).
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
        for i in gated:
            assert flow.steps[i].mutating_sources, (
                f"{_ids(case)}: step {i} is gated but records NO source, so nothing downstream can tell "
                f"a keyword GUESS from wire EVIDENCE. Every cell of this matrix crosses a different "
                f"signal, which makes it the right place to notice a mark site that forgot provenance.")
        commit_index = 0 if commit_first else 1
        assert commit_index in gated, (
            f"{_ids(case)}: the gate is on step(s) {gated} but the step that WROTE is {commit_index} "
            f"({flow.steps[commit_index].intent!r}). The Idempotency-Key, the precondition and the "
            f"drift gate all ride the wrong row; the real commit replays ungated. "
            f"steps={[(s.intent, s.mutating) for s in flow.steps]}")

        # SLICE S2 / R3.5 — THE SAME RECIPE, SEEN FROM THE `flows` LAYER.
        #
        # Every cell above is an UNDECLARED write: a cached step mutates, and no `spec.mutate` declares
        # it. The auth-refresh retry re-runs the whole flow from `start_url`, so for any of these shapes
        # it would re-actuate the commit — which is what it did, under a byte-identical Idempotency-Key,
        # until `_auth_retry_allowed` started keying off `is_write_flow` instead of the declaration.
        #
        # It belongs in the MATRIX rather than only beside the fix because the property is about the
        # SHAPES: whatever commit style a future dimension adds, it inherits this automatically.
        #
        # Note what is deliberately NOT asserted here — that these flows are REFUSED. A flow-level
        # refusal was built for this slice and measured to break a large population of ordinary reads
        # (`classify_mutation` falls back to an unbounded substring match), so the guard is on the retry.
        # See `tests/test_undeclared_write_retry.py` and decision D0.
        from ultracua import flows as flows_mod

        undeclared = flows_mod.FlowSpec(
            name=goal, goal=goal, start_url=f"{base}/", headless=True,
            login=flows_mod.LoginSpec(url=f"{base}/login"))
        assert flows_mod._auth_retry_allowed(
            undeclared, flow, auth_refresh=True, parameterizing=False, landed=False)[0] is False, (
            f"{_ids(case)}: this recipe WRITES (gated steps={gated}) but declares no write, and the "
            f"auth-refresh retry would still re-run it from the start — re-firing the commit with no "
            f"confirm barrier able to detect it, under the same Idempotency-Key")
        # ...and the LIVENESS half, without which the property is satisfied by never retrying anything.
        # A DECLARED write carrying a whole-flow precheck may be retried — but ONLY if the recipe commits
        # once, because a whole-flow precheck models just the LAST write and cannot tell whether an
        # earlier one already landed.
        #
        # This arm is where the cost of counting `mutating` steps becomes visible, which is the point of
        # putting it in the matrix. The `bait` cells give the benign sibling a mutating-sounding intent
        # ("submit the filter"), so the classifier marks TWO steps and the flow loses its retry even
        # though it commits once. That is the over-count, priced per cell rather than argued: it is
        # fail-safe (a loud failure, never a double-submit) and it is real.
        retryable = flows_mod.FlowSpec(
            name=goal, goal=goal, start_url=f"{base}/", headless=True,
            login=flows_mod.LoginSpec(url=f"{base}/login"),
            mutate=flows_mod.MutateSpec(confirm_text_contains="committed",
                                        precheck_text_contains="committed"))
        allowed, why = flows_mod._auth_retry_allowed(retryable, flow, auth_refresh=True,
                                                     parameterizing=False, landed=False)
        # SLICE S3 / R3.3 — the same recipe, once the write is KNOWN to have committed. Whatever else is
        # true of a cell, a run whose confirm TRANSITION was observed must never be re-run from the start:
        # that re-fires the commit. Asserted for every shape so a future commit style inherits it, and
        # placed beside the retry arm because the two decisions share one predicate.
        #
        # SCOPE, stated so this is not mistaken for more than it is: `landed` is passed as a CONSTANT
        # here, so these cells pin how the retry gate CONSUMES the evidence, not how `landed` is
        # COMPUTED — which is where all four versions of the R3.3 fix were wrong. The computation is a
        # property over six distinct run shapes in `tests/test_landed_arms_the_ledger.py`; it cannot live
        # in this matrix, because none of these cells declares `spec.mutate` with a confirm barrier and
        # the arming is undefined without one.
        landed_allowed, landed_why = flows_mod._auth_retry_allowed(
            retryable, flow, auth_refresh=True, parameterizing=False, landed=True)
        assert landed_allowed is False and "committed" in landed_why, (
            f"{_ids(case)}: this run's write is known to have landed and the auth-refresh retry would "
            f"still re-run the flow from start_url, firing it again: allowed={landed_allowed} "
            f"why={landed_why!r}")
        if len(gated) == 1:
            assert allowed is True, (
                f"{_ids(case)}: a declared write whose recipe commits ONCE (gated={gated}) lost its "
                f"expired-session recovery — the property is now satisfied by never retrying: {why}")
        else:
            assert allowed is False and "marked as WRITING" in why, (
                f"{_ids(case)}: the recipe has {len(gated)} mutating steps (gated={gated}), so a "
                f"whole-flow precheck cannot tell whether an earlier one landed — the retry must be "
                f"declined AND must say why: allowed={allowed} why={why!r}")

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


# =============================================================================================
# THE SAME INVIOLABLE ON THE OTHER ENTRY POINT, crossed with WHEN THE WRITE LEAVES THE BROWSER.
#
# Everything above enters through `run_cached(mode="learn")`, whose attribution is `flow.py`'s
# post-hoc reconciliation of requests against steps. `record()` uses a DIFFERENT mechanism — in-page
# markers that name the commit each write came from — and nothing asserted this property over it.
# That gap is how R4.26 survived three releases: the recorder credited a deferred write to the NEXT
# click, cached the real commit UNGATED, and the only thing that ever noticed was one bespoke test
# that failed ~1 run in 20 and was read as flaky.
#
# The new dimension is WHEN the write leaves relative to its commit. The comment on `_COMMITS` used
# to say a deferred write would make cells "race rather than test the property" — which was true of
# the field ordering and is why nobody added it. The cells below do not race for it: they reproduce
# the same DECISION-POINT STATE from two documented orderings instead (see the R4.26 cell).
#
# BOTH DIRECTIONS ARE LOAD-BEARING, as everywhere in this file:
#   * `sync` and `microtask` MUST STAY LEARNABLE. A turn boundary closed too early — a microtask
#     reset, say — satisfies every safety cell here by refusing writes it could have attributed,
#     which is the D0 over-refusal shape wearing an attribution hat.
#   * every `timer*` cell MUST BE REFUSED, including against a page actively faking the signal the
#     fix reads. In-page evidence cannot prove what caused a deferred write, so the only honest
#     answer is to gate nothing and fail loud.
# =============================================================================================

# Each cell records the SHAPE it actually produced — whether the write left the browser inside an
# event dispatch or in a bare task — and that is asserted as a PREMISE below, so a cell which
# silently stops exercising its shape fails loudly instead of passing. Without it the deferred cells
# would go quietly vacuous the day anything perturbs their scheduling, which is the failure mode that
# let R4.26 live for three releases. `__nev` is the page's own copy of the NATIVE `window.event`
# getter, taken before anything below can shadow it, so the premise stays truthful in the hostile
# cell — where the naive read (`__postPlainEvent`) is the fake and the two deliberately disagree.
_NATIVE_EVENT_JS = ("var __nev = (function(){"
                    " var d = Object.getOwnPropertyDescriptor(window, 'event');"
                    " return (d && d.get) ? d.get : function(){ return window.event; }; })();")
_POST_JS = ("window.__postAt = performance.now();"
            "window.__postInDispatch = !!__nev.call(window);"
            "window.__postPlainEvent = !!window.event;"
            "fetch('/save',{method:'POST'}).then(function(r){return r.text();})"
            ".then(function(t){ document.getElementById('out').textContent = t; });")

# Commit arms the write; a `window` capture-phase listener re-issues it in a bare task during the NEXT
# click's still-open turn. Shared by R4.26's cell and the shadowing cell below.
_ARM_ON_NEXT_JS = ("window.addEventListener('click', function(ev){"
                   " if (window.__armed && ev.target && ev.target.id === 'next') {"
                   "  window.__armed = false;"
                   "  setTimeout(function(){" + _POST_JS + "}, 0); } }, true);")

_TIMING = {
    # The write leaves in the commit's own synchronous turn.
    "sync": (_POST_JS, ""),
    # A microtask continuation of the commit's turn — still unambiguously caused by this click.
    "microtask": ("Promise.resolve().then(function(){" + _POST_JS + "});", ""),
    # Deferred by a timer: the cause cannot be proven in-page (a load-armed write coincides
    # indistinguishably with an unrelated click), so it must be refused even though a human can see
    # which button caused it.
    "timer": ("setTimeout(function(){" + _POST_JS + "}, 120);", ""),
    # R4.26, deterministically. Commit ARMS the write; it leaves the browser in a timer task that runs
    # during the NEXT click's still-open turn, so the recorder credits the benign click and caches the
    # real commit UNGATED.
    #
    # Why this shape and not the field one: in the field the write is a plain `setTimeout(..., 120)`
    # held past the next click by a starved renderer, and that ordering is a race — measured 1 run in
    # ~40 under busy-spun cores, and 2 in 10 when the page starves its own main thread, because
    # Playwright's CDP traffic interleaves at the block boundary. The ordering is decided by the task
    # TYPE of the later commit, which is not something a test can pin:
    #
    #     later commit arrives as a TIMER task  -> the turn-reset timer runs first, no misattribution
    #     later commit arrives as an INPUT task -> the overdue write timer runs first, 3/3
    #
    # So the cell reproduces the same DECISION-POINT STATE — a write leaving in a bare task while a
    # later commit's turn is still open — without racing for it. Two documented orderings do the work:
    # a `window` capture-phase listener runs BEFORE the recorder's `document` one, and equal-delay
    # timers fire in arming order. The write's timer is therefore armed before the turn-reset of the
    # very commit it is about to be credited to, and beats it every time.
    "timer_armed_by_an_earlier_commit": ("window.__armed = true;", _ARM_ON_NEXT_JS),
    # R4.36. The write leaves inside a dispatch — just not the COMMIT'S dispatch. Every cell above
    # defers into a BARE task, which is why `inDispatch()` closed them all and why this shape survived
    # it: both of the old conjuncts (`__ucturn === 1`, `inDispatch()`) are TRUE here.
    #
    # Deterministic by listener phase, with no scheduling race. The arming listener is on `window` in
    # the BUBBLE phase, so it runs after the recorder's `document` capture listener has already opened
    # the turn for this click; it then synchronously dispatches a non-commit event whose handler
    # writes. The decision-point state is exactly the field one — turn open, current dispatch is not
    # the commit's — which in the field is produced by an overdue turn-reset and a keystroke
    # (tests/test_turn_boundary.py reproduces that shape end to end).
    "dispatch_of_a_later_noncommit_event": (
        "window.__armed_nc = true;",
        "window.addEventListener('click', function(ev){"
        " if (window.__armed_nc && ev.target && ev.target.id === 'next') {"
        "  window.__armed_nc = false;"
        "  var t = document.getElementById('out');"
        "  t.addEventListener('uc-later', function(){" + _POST_JS + "});"
        "  t.dispatchEvent(new Event('uc-later'));"
        " } }, false);"),
    # The same thing against a HOSTILE page. `window.event` is a configurable accessor, so a page can
    # redefine it to return a truthy fake and make every deferred write look like it left inside a
    # dispatch — which would hand R4.26 straight back. Measured: the redefinition succeeds, a plain
    # `window.event` read returns the fake, and the native getter captured at init still returns the
    # truth. This cell is what makes that hardening a tested claim rather than a comment.
    "timer_with_window_event_shadowed": (
        "window.__armed = true;",
        "Object.defineProperty(window, 'event', {configurable: true,"
        " get: function(){ return {type: 'FAKE'}; }});" + _ARM_ON_NEXT_JS),
}
_MUST_REFUSE = {"timer", "timer_armed_by_an_earlier_commit", "timer_with_window_event_shadowed",
                "dispatch_of_a_later_noncommit_event"}

# The SHAPE each cell produces — whether its write leaves the browser INSIDE an event dispatch —
# DECLARED, not derived. It used to be derived from `_MUST_REFUSE` ("a refused write is one that left
# outside a dispatch"), and that equivalence was itself the blind spot R4.36 walked through: a write can
# leave inside a dispatch that simply is not the COMMIT'S, and must still be refused. Keeping shape and
# verdict independent is what lets a cell exist for every combination of the two.
_IN_DISPATCH = {"sync", "microtask", "dispatch_of_a_later_noncommit_event"}


def _timing_page(commit_body: str, extra: str) -> str:
    return ("<h1>Editor</h1>"
            "<button type=button id='commit'>Commit</button>"
            "<button type=button id='next'>Next</button>"
            "<div id='out'></div>"
            "<script>" + _NATIVE_EVENT_JS +
            "document.getElementById('next').addEventListener('click',function(){"
            "window.__nextAt = performance.now();});"
            + extra +
            "document.getElementById('commit').addEventListener('click',function(){" + commit_body + "});"
            "</script>")


@pytest.mark.parametrize("timing", list(_TIMING))
async def test_a_recorded_write_is_gated_on_the_step_that_wrote_or_refused(timing, tmp_path: Path) -> None:
    """INVIOLABLE #3 over the `record` entry point. A cell fails when the recorder caches a write
    with the gate on a step that did not write (R4.26's harm), caches one with no gate at all, or —
    the other direction — refuses a write whose cause it can actually prove."""
    from ultracua.flows import FlowSpec, MutateSpec, record

    commit_body, extra = _TIMING[timing]
    site = _Site(_timing_page(commit_body, extra))
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = f"commit then move on ({timing})"
    seen: dict = {}
    try:
        spec = FlowSpec(name=f"rec-{timing}", start_url=f"{base}/", goal=goal,
                        mutate=MutateSpec(confirm_text_contains="committed"))

        async def _demo(page) -> None:
            await page.get_by_role("button", name="Commit").click()
            await page.get_by_role("button", name="Next").click()
            await page.get_by_text("committed").wait_for()
            seen.update(await page.evaluate(
                "({post: window.__postAt || 0, next: window.__nextAt || 0,"
                "  inDispatch: !!window.__postInDispatch,"
                "  plainEvent: !!window.__postPlainEvent})"))

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))

        assert len(site.posts) == 1, (
            f"{timing}: the demo must commit exactly once; saw {len(site.posts)} POST(s)")
        # PIN THE PREMISE — each cell must actually produce the write SHAPE it is named for, or it is
        # asserting the product's behaviour on an input it never generated.
        assert seen.get("inDispatch") is (timing in _IN_DISPATCH), (
            f"{timing}: this cell is supposed to issue its write "
            f"{'INSIDE' if timing in _IN_DISPATCH else 'OUTSIDE'} an event dispatch and did the "
            f"opposite, so it exercised the wrong shape entirely (inDispatch={seen.get('inDispatch')})")
        if timing.startswith("timer_"):
            # ...and for R4.26's cells specifically: the deferred write must land AFTER the benign
            # click, or no commit's turn was open to mis-credit it to and the cell proved nothing.
            assert seen.get("post", 0) > seen.get("next", 0) > 0, (
                f"{timing}: premise lost — the deferred write did not land after the benign click "
                f"(post={seen.get('post')}, next={seen.get('next')}), so this cell exercised nothing")
        # The hostile cell must actually be hostile: a NAIVE `window.event` read has to come back
        # truthy at the moment of the write — from the fake — or the shadowing never took and the cell
        # is just a duplicate of the one above it. Everywhere else the naive read agrees with the
        # native one, which is what makes the shadowed cell the only thing separating the two.
        # Stated as the invariant it actually is — the naive read AGREES with the native one everywhere
        # except the cell where the page fakes it — rather than derived from the verdict, which coupled
        # it to `_MUST_REFUSE` and made a refused-but-genuinely-in-a-dispatch cell inexpressible.
        shadowed = timing == "timer_with_window_event_shadowed"
        if shadowed:
            assert seen.get("plainEvent") is True and seen.get("inDispatch") is False, (
                f"{timing}: the shadowing never took — a naive `window.event` read must return the "
                f"page's FAKE (got {seen.get('plainEvent')}) while the native getter returns the truth "
                f"(got {seen.get('inDispatch')}), or this cell is a duplicate of the one above it")
        else:
            assert seen.get("plainEvent") == seen.get("inDispatch"), (
                f"{timing}: the naive `window.event` read ({seen.get('plainEvent')}) disagrees with the "
                f"native getter ({seen.get('inDispatch')}) on a cell that does not shadow it")

        if timing in _MUST_REFUSE:
            assert res.cached is False and flow is None, (
                f"{timing}: a write whose cause cannot be proven in-page was CACHED. steps="
                f"{[(s.intent, s.mutating) for s in flow.steps] if flow else None}")
            assert res.note, f"{timing}: refused silently — a refusal must carry its reason"
            return

        # ...and the liveness half: a write the recorder CAN attribute must stay learnable.
        assert res.cached is True and flow is not None, (
            f"{timing}: this write is caused by its own click, provably and in-page, and must stay "
            f"recordable — refusing it makes the ordinary write flow unauthorable. note={res.note!r}")
        gated = [i for i, s in enumerate(flow.steps) if s.mutating]
        for i in gated:
            assert flow.steps[i].mutating_sources, (
                f"{timing}: recorded step {i} is gated but records NO source — the learn arm above "
                f"records provenance and its sibling front-end must too.")
        assert 0 in gated, (
            f"{timing}: the gate is on step(s) {gated}, but step 0 ('Commit') is the one that WROTE. "
            f"The commit replays with no drift gate, no precondition and no Idempotency-Key while the "
            f"barrier sits on a step that never writes. "
            f"steps={[(s.intent, s.mutating) for s in flow.steps]}")
        assert flow.steps[0].precond_scope, (
            f"{timing}: step 0 is marked mutating but carries no precondition, so the mutation gate "
            f"has nothing to refuse under drift")
    finally:
        httpd.shutdown()
        httpd.server_close()


# ===================================================================================================
# DIMENSION: the HUMAN ANNOTATION verb (0.93.0) — the first thing in this arc that can UN-gate a write.
#
# Its first draft shipped a critical that every bespoke test in its own file passed straight through: a
# demote->promote round trip left `mutating=True` with NO precondition, so the mutation gate took
# neither branch and the write fired blind under drift. The bespoke tests all asserted about `mutating`
# and `mutating_sources` and none asserted about the GATE — which is exactly why the project rule is
# matrix over bespoke, and why the dimension belongs here rather than beside the feature.
#
# The property is the invariant `recorder.py` states in capitals and refuses to author against: a step
# that is marked mutating must have SOMETHING for the gate to check. No annotation, in any order, from
# any starting state, may produce a step that violates it.
# ===================================================================================================

_ANNOTATION_STATES = [
    # (label,                       mutating, sources,               scope,   fingerprint)
    ("keyword-only, scoped",        True,  ["keyword"],              "scope", ""),
    ("keyword+wire, scoped",        True,  ["keyword", "wire"],      "scope", ""),
    ("form_method, scoped",         True,  ["form_method"],          "scope", ""),
    ("overgate, scoped",            True,  ["overgate"],             "scope", ""),
    ("caption-only, scoped",        True,  ["caption"],              "scope", ""),
    ("no provenance, scoped",       True,  None,                     "scope", ""),
    ("empty provenance, scoped",    True,  [],                       "scope", ""),
    ("read step, learn-shaped",     False, None,                     "",      "fp"),
    ("read step, recorder-shaped",  False, None,                     "",      ""),
    ("already-human, scoped",       True,  ["human", "keyword"],     "scope", ""),
]

_ANNOTATION_SEQUENCES = [
    ("demote",              [False]),
    ("promote",             [True]),
    ("demote->promote",     [False, True]),
    ("promote->demote",     [True, False]),
    ("demote x2",           [False, False]),
    ("demote->promote->demote", [False, True, False]),
]


@pytest.mark.parametrize("state", _ANNOTATION_STATES, ids=lambda s: s[0])
@pytest.mark.parametrize("seq", _ANNOTATION_SEQUENCES, ids=lambda s: s[0])
def test_no_annotation_sequence_can_produce_a_write_with_no_mutation_gate(state, seq, tmp_path) -> None:
    """INVARIANT #2/#3 over the annotation verb: a step left MUTATING must have a precondition.

    60 cells. Each applies a sequence of human verdicts to a starting state and asserts the result is
    either refused or GATED — never `mutating=True` with an empty scope AND an empty fingerprint, which
    is a mutation gate that silently does nothing.
    """
    import time as _time

    from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key
    from ultracua.flows import FlowSpec, mark_step
    from ultracua.locators import LocatorSpec

    label, mutating, sources, scope, fp = state
    _seqlabel, verdicts = seq

    spec = FlowSpec(name="m", goal="g", start_url="http://x/")
    cache = FlowCache(root=tmp_path / "c")
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(CachedFlow(
        key=key, goal="g", start_url="http://x/", created_ts=_time.time(),
        steps=[CachedStep(intent="act", action="click",
                          locator=LocatorSpec(role="button", name="act", tag="button"),
                          mutating=mutating, precond_scope=scope, precond_fingerprint=fp,
                          mutating_sources=sources)]))

    refusals = 0
    for writes in verdicts:
        try:
            mark_step(spec, 0, writes=writes, cache=cache)
        except ValueError:
            refusals += 1                       # a refusal is always an acceptable outcome
        step = cache.get(key).steps[0]
        assert not step.mutating or step.precond_scope or step.precond_fingerprint, (
            f"[{label} | {_seqlabel}] the step is marked MUTATING with neither a scope nor a "
            f"fingerprint — its mutation gate takes neither branch, so it replays under any drift. "
            f"sources={step.mutating_sources!r}")

    # Anti-vacuity: a cell where EVERY verdict refused proves nothing about the invariant, so make the
    # census visible rather than letting a wholly-refusing matrix read as a passing one.
    print(f"[annotation] {label:<28} {_seqlabel:<26} refused {refusals}/{len(verdicts)}")


# ============================ dimension: WHEN the commit is dispatched (R4.29) ============================
#
# Added by the R4.29 slice. Every dimension above varies WHAT commits or WHERE; none varied WHEN, and the
# defect lived exactly there: `_author_steps` removed its request listener the instant the loop broke, so
# a commit the page had SCHEDULED but not yet dispatched was invisible from that moment — `wrote["hit"]`
# unset, the unattributable-write refusal never armed, no wire promotion, and the flow cached as an
# ordinary READ to be re-fired ungated on every replay.
#
# The offsets are all INSIDE `write_window_ms` (2 s), which is the span this file's subject already
# promises to attribute. A commit deferred BEYOND it is a different question and is pinned separately as
# a strict-xfail in `tests/test_watcher_drain.py` (R4.30) — not folded in here, because a matrix cell
# that is expected to fail stops being an invariant.
_DEFER_MS = [0, 150, 400, 900, 1500]


@pytest.mark.parametrize("defer_ms", _DEFER_MS, ids=[f"defer{d}ms" for d in _DEFER_MS])
async def test_a_commit_dispatched_LATE_is_still_seen_by_the_learn_watcher(
        defer_ms: int, tmp_path: Path) -> None:
    """INVIOLABLE #3 over WHEN the commit leaves. The bait carries no keyword, on purpose: a
    keyword-named control would be gated by the classifier and the cell would pass without the wire
    having seen anything, which is the shape that made R4.29 invisible for three CI rounds."""
    from ultracua.cache import FlowCache, flow_key
    from ultracua.flows import FlowSpec, _learn_once
    from ultracua.providers.scripted import ScriptedProvider

    body = ("fetch('/api/commit',{method:'POST',body:'x=1'});" if defer_ms == 0
            else "setTimeout(function(){fetch('/api/commit',{method:'POST',body:'x=1'});}, "
                 f"{defer_ms});")
    page = ("<h1>Panel</h1>"
            "<button type=button id='c'>Continue</button>"
            "<button type=button id='a'>Show details</button>"
            "<script>"
            "document.getElementById('a').addEventListener('click',function(){" + body + "});"
            "for (const id of ['c','a']) document.getElementById(id).addEventListener('click',"
            "function(){document.querySelector('h1').textContent='clicked '+id;});"
            "</script>")
    site = _Site(page)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        res = await _learn_once(
            spec,
            provider=ScriptedProvider([
                {"action": "click", "role": "button", "name": "Continue", "intent": "continue"},
                {"action": "click", "role": "button", "name": "Show details",
                 "intent": "show the details"},
                {"action": "done", "intent": "done"},
            ]),
            router=None, cache=cache, verify_replay=False)
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        gated = [i for i, s in enumerate(flow.steps) if s.mutating] if flow else None
        marks = [s.mutating_sources for s in flow.steps] if flow else None
        print(f"    defer={defer_ms:>4}ms  posts={len(site.posts)}  cached={bool(res.cached)}  "
              f"gated={gated}  marks={marks}")

        if not site.posts:
            raise RuntimeError(
                f"PREMISE LOST (defer={defer_ms}ms): the commit never reached the server, so this cell "
                f"exercises nothing. cached={res.cached!r}")
        if flow is None:
            assert res.cached is False and res.note, "a refusal must be loud and carry its reason"
            return
        assert gated, (
            f"defer={defer_ms}ms: a commit LEFT THE BROWSER and the recipe gates nothing — it will be "
            f"re-fired ungated and un-keyed on every replay. marks={marks}")
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------------------------------------------
# INVIOLABLE #3 over the READINESS RETRY (0.148.0). The replay now waits and re-resolves when the page
# has not painted -- and the whole danger of that mechanism is the mutation gate, where a `None` from
# `resolve` is what REFUSES a write. R4.115 measured the naive form binding `/cancel/30` where
# `/cancel/3` was recorded, by waiting for a competing candidate to disappear.
#
# The shipped retry is keyed on `saw_candidates`, not on the return value: if the page ANSWERED and the
# resolver refused, there is no wait and no second look. This dimension is that promise, driven over
# every refusal shape the gate can produce, on a page that keeps MUTATING throughout -- so a retry
# keyed on the return value would have time to find something, and only the sensor stops it.
#
# The bait is a per-row control with a real POST, because a cell that gates on a keyword-named button
# would pass with the wire having seen nothing (the shape that hid R4.29 for three CI rounds).
_REFUSAL_SHAPES = [
    # (id, page body, spec kwargs) -- each must leave the gate refusing, retry or no retry
    ("ambiguous-two-rows",
     "<table><tbody>"
     "<tr id='r3'><td>Acme #3</td><td><a href='/cancel/3'>Cancel</a></td></tr>"
     "<tr id='r30'><td>Beta #30</td><td><a href='/cancel/30'>Cancel</a></td></tr>"
     "</tbody></table>",
     dict(role="link", name="Cancel", tag="a", text="Cancel", css="tbody > tr > td > a",
          anchor="Acme #3 Cancel", anchor_source="row", anchor_id=None)),
    ("row-guard-wrong-record",
     "<table><tbody>"
     "<tr data-id='r30'><td>Beta #30</td><td><a href='/cancel/30'>Cancel</a></td></tr>"
     "</tbody></table>",
     dict(role="link", name="Cancel", tag="a", text="Cancel", css="tbody > tr > td > a",
          anchor="Acme #3 Cancel", anchor_source="row", anchor_id="r3")),
]


@pytest.mark.parametrize("shape", _REFUSAL_SHAPES, ids=[s[0] for s in _REFUSAL_SHAPES])
async def test_the_readiness_retry_never_walks_through_a_gate_refusal(shape) -> None:
    """INVIOLABLE #3 over the readiness retry. A refusal the resolver reached by LOOKING must stay a
    refusal, however long the page keeps changing afterwards."""
    from ultracua import flow as flow_mod
    from ultracua import locators as L
    from ultracua.browser import BrowserSession

    _id, body, spec_kw = shape
    # Mutates for a full second AFTER the first resolve: a retry keyed on `loc is None` would keep
    # looking right through it, which is exactly how the measured wrong-record bind happened.
    churn = ("<script>let n=0;const h=setInterval(function(){"
             "document.body.appendChild(document.createElement('span'));"
             "if(++n>25) clearInterval(h);},40);</script>")
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content(f"<body>{body}{churn}</body>")
        spec = L.LocatorSpec(**spec_kw)

        class _Tr:
            meta: dict = {}

        tr = _Tr()
        tr.meta = {}
        sink: dict = {}
        first = await L.resolve(s.page, spec, unique=True, sink=sink)
        assert first is None, f"{_id}: PREMISE LOST -- the gate resolve bound, so nothing is refused here"
        assert sink.get("saw_candidates") is True, (
            f"{_id}: the page ANSWERED and the resolver refused, so the sensor must say so -- "
            f"unset or False is what lets a retry walk through this refusal")

        out = await flow_mod._retry_if_unpainted(s, s.page, spec, tr, first, sink=sink)
        print(f"    {_id:>24}  refusal held={out is None}  retry={tr.meta.get('readiness_retry')!r}")
        assert out is None, (
            f"{_id}: the readiness retry produced a bind out of a REFUSAL. On the mutation gate that "
            f"is a write fired at whatever the page settled into -- R4.115 measured it landing on a "
            f"different customer's record under the recorded row's Idempotency-Key.")
        assert tr.meta.get("readiness_retry") == "refused"
    finally:
        await s.close()


# ---------------------------------------------------------------------------------------------------
# INVIOLABLE #3 over D7's BODY DEMOTION (0.150.0). `body_says_read` lets a POST whose own body names a
# read operation stop counting as a write, which is what un-gates Odoo's read steps (R4.27/R4.122).
#
# THE HAZARD IS NAMED AND IT IS THIS CELL'S SUBJECT. Clearing the request also clears `wrote["hit"]`,
# and `performed_write` is what SKIPS verify-by-replay. So a server whose read-named call actually
# writes would be replayed a second time at learn and fire TWICE -- a double-submit created by the
# demotion, at the exact moment the classification is wrong. It cannot be made conservative by keeping
# the run flag: `_learn_once` REFUSES a flow whose `performed_write` is set with no gated step, so the
# two must clear together.
#
# `saves == 1` IS THE PREMISE, borrowed from R4.36's harness for the same reason: a cell that merely
# asserts "a flow was cached" passes while the double-fire it exists to catch happens underneath.
class _RpcSite:
    """A JSON-RPC endpoint that COUNTS saves, and whose `search_read` really writes."""

    def __init__(self, page: str) -> None:
        self.page = page
        self.saves = 0
        self.calls: list[str] = []

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
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0) or 0)
                site.calls.append(self.path)
                # THE TREACHEROUS SERVER: a call named `search_read` that commits. This is the
                # residual `docs/reads-over-post.md` states at parity -- and the parity claim is what
                # this cell exists to keep honest.
                site.saves += 1
                self._send('{"jsonrpc":"2.0","result":[]}', "application/json")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"


async def test_a_body_demoted_call_that_really_writes_is_never_fired_twice(tmp_path: Path) -> None:
    """INVIOLABLE #3 over the D7 demotion. The click fires ONE `search_read` that the server treats
    as a commit; `body_says_read` clears it, so verify-by-replay is no longer skipped. The flow may
    be cached or refused -- that is not this cell's business -- but the server must never see the
    save twice."""
    from ultracua.cache import FlowCache
    from ultracua.flows import FlowSpec, _learn_once
    from ultracua.providers.scripted import ScriptedProvider

    page = (
        "<h1>Panel</h1><button type=button id='go'>Continue</button>"
        "<script>document.getElementById('go').addEventListener('click',function(){"
        "  fetch('/web/dataset/call_kw/crm.lead/search_read',{method:'POST',"
        "        headers:{'Content-Type':'application/json'},"
        "        body:JSON.stringify({jsonrpc:'2.0',method:'call',"
        "                             params:{model:'crm.lead',method:'search_read',args:[],kwargs:{}}})});"
        "  document.querySelector('h1').textContent='done';"
        "});</script>")
    site = _RpcSite(page)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        res = await _learn_once(
            spec,
            provider=ScriptedProvider([
                {"action": "click", "role": "button", "name": "Continue", "intent": "continue"},
                {"action": "done", "intent": "done"},
            ]),
            router=None, cache=cache, verify_replay=True)
        print(f"    saves={site.saves}  calls={site.calls}  cached={bool(res.cached)}  "
              f"performed_write={res.performed_write}")
        if site.saves == 0:
            raise RuntimeError(
                "PREMISE LOST: the click never reached the server, so this cell exercises nothing.")
        assert site.saves == 1, (
            f"the server committed {site.saves} times. A body-demoted call that really writes was "
            f"re-driven -- verify-by-replay is no longer skipped once `performed_write` clears, and "
            f"that is the double-submit D7's condition 3 names. Inviolable #3.")
    finally:
        httpd.shutdown()
        httpd.server_close()
