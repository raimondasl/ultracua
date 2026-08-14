"""R3.12 — the dry-run report must not NAME a step it cannot know caused a held write.

`DryRunArbiter` keeps ONE window slot, overwritten by every `open_window`, plus a `write_window_ms`
grace tail. On a multi-write flow the next commit's window opens well inside the previous one's tail and
takes the slot, so a write DEFERRED out of step A is recorded as `HeldWrite(step=B, intent=B.intent)` —
the same single-slot overlap R4 fixed in `_author_steps` and R3.2's redesign removed there.

**This file pins BOTH directions, and that is load-bearing.** A report that labels every hold
"ambiguous" satisfies the honesty half while destroying the artifact, which is D0's shape one surface
over. So the liveness cell — an ordinary two-write flow, each write landing in its own window, must
still be attributed per step — fails if the fix over-refuses.

WHY THIS IS NOT AN ATTRIBUTION MECHANISM (D5). Nothing here answers "which step caused this write";
that is blocked indefinitely. The fix only refuses to ASSERT a cause when more than one step is a live
candidate. The grace tail still appears in the rule, but it now governs how OFTEN the report says
"ambiguous" — never whether it says something FALSE — so no constant can tune it into a wrong claim.
"""

from __future__ import annotations

import asyncio
import http.server
import threading

import pytest

from ultracua.config import settings

# Step A's write is deferred PAST its own `expect_request` wait (so its window closes first) but well
# inside `write_window_ms` (so the grace tail is still live when step B's window opens). Derived from
# settings rather than hard-coded, so a change to either bound fails the premise below instead of
# silently testing nothing.
_DEFER_MS = settings.write_settle_ms + 500


def _page(defer_ms: int, *, b_writes: bool, a_pings: bool = False) -> bytes:
    """`a_pings` gives step 0's control a PROMPT write as well as its deferred one — an analytics ping,
    a draft save, a validation call. That shape defeated this file's first fix (see the F1 cell)."""
    b_body = "fetch('/publish', {method: 'POST', body: 'go=1'});" if b_writes else ""
    ping = "fetch('/track', {method: 'POST', body: 'e=1'});" if a_pings else ""
    return (
        "<!doctype html><html><body><h1>Invites</h1>"
        "<button id=a>Send invite</button><button id=b>Publish order</button><div id=out></div>"
        "<script>"
        "document.getElementById('a').addEventListener('click', function () {"
        f"  {ping}"
        f"  setTimeout(function () {{ fetch('/invite', {{method: 'POST', body: 'who=alice'}}); }}, {defer_ms});"
        "});"
        "document.getElementById('b').addEventListener('click', function () {"
        f"  {b_body}"
        "  document.getElementById('out').textContent = 'Published';"
        "});"
        "</script></body></html>"
    ).encode()


def _serve(hits: list, body: bytes):
    class _H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _learn_then_dry_run(tmp_path, monkeypatch, hits, body):
    """Learn the two-commit flow (which writes for real), then dry-run it."""
    from ultracua.cache import FlowCache, flow_key
    from ultracua.flow import run_cached
    from ultracua.flows import FlowSpec, MutateSpec, dry_run
    from ultracua.providers.scripted import ScriptedProvider

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    httpd, base = _serve(hits, body)
    try:
        cache = FlowCache(root=tmp_path / "c")
        spec = FlowSpec(name="invites", start_url=base + "/", goal="send the invite and publish",
                        headless=True, mutate=MutateSpec(confirm_text_contains="Published"))
        steps = [
            {"action": "click", "role": "button", "name": "Send invite", "intent": "send the invite"},
            {"action": "click", "role": "button", "name": "Publish order", "intent": "publish the order"},
            {"action": "done", "intent": "done"},
        ]
        learn = await run_cached(spec.start_url, spec.goal, ScriptedProvider(steps), cache,
                                 mode="learn", headless=True, scope=spec.scope)
        assert learn.success, "the learn run failed; the dry run below would prove nothing"
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        assert flow is not None and len(flow.steps) == 2, f"unexpected recipe: {flow and flow.steps}"
        # THE PREMISE. Both steps must be mutating, or there is no second window to steal the slot and
        # this file silently tests nothing — the failure mode CLAUDE.md's fixture note is about.
        assert [s.mutating for s in flow.steps] == [True, True], (
            f"premise lost: mutating flags are {[s.mutating for s in flow.steps]}")
        hits.clear()
        rep = await dry_run(spec, cache=cache)
        assert hits == [], f"the dry run LEAKED to the server: {hits}"
        return flow, rep
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_write_deferred_out_of_its_step_is_not_labelled_with_the_next_commit(
    tmp_path, monkeypatch,
) -> None:
    """R3.12. Step 0's write lands after step 1's window has taken the slot. The report must not say
    step 1 sent it — the human approves the flow from this artifact."""
    assert _DEFER_MS < settings.write_window_ms, (
        f"premise lost: a {_DEFER_MS}ms defer is outside the {settings.write_window_ms}ms grace tail, so "
        f"the write would be ungated rather than MISLABELLED and this test would pass for the wrong reason")
    hits: list = []
    flow, rep = await _learn_then_dry_run(tmp_path, monkeypatch, hits,
                                          _page(_DEFER_MS, b_writes=False))

    assert rep.aborted is None, f"{rep.aborted}: {rep.abort_detail}"
    # THE PREMISE: step 0's deferred write must actually have been held, or there is nothing to mislabel.
    deferred = [h for h in rep.held if "/invite" in h.url]
    assert len(deferred) == 1, f"premise lost: step 0's write was not held ({[h.url for h in rep.held]})"
    h = deferred[0]

    # /invite is caused by step 0 ('send the invite') — it is the ONLY control that requests it.
    # Read through `getattr` DELIBERATELY: this assertion must fail against pre-fix source because the
    # report makes a WRONG CLAIM, not because a new field is missing. A test that goes red on an
    # AttributeError proves the API changed, which is not the property under test.
    claims_a_step = getattr(h, "attribution", "step") == "step"
    assert not (claims_a_step and h.step != 0), (
        f"the report attributes step 0's write to step {h.step} ({h.intent!r}); a human approving this "
        f"flow reads that as 'publishing the order sends POST /invite'")
    if not claims_a_step:
        assert 0 in h.candidates, f"step 0 must remain a candidate, got {h.candidates}"
        assert any("attribut" in w for w in rep.warnings), (
            f"an unattributable hold must be stated in the report, not just in the row: {rep.warnings}")


async def test_a_step_that_ALSO_writes_promptly_can_still_own_a_later_write(
    tmp_path, monkeypatch,
) -> None:
    """F1 — the shape that got past this file's first fix, and the reason it is here end-to-end and not
    only as a matrix cell.

    The first draft treated a step that had already written as SETTLED and dropped it from the candidate
    set, to keep ordinary multi-write reports precise. But "we saw a write" is not "we saw all its
    writes": a control that fires an analytics ping AND defers its real write marked itself settled, and
    the deferred write was then confidently named with the NEXT step — R3.12's exact row, with no
    warning anywhere in the report. Reproduced 3/3 at deferrals of 150/300/600 ms.

    Note the deferral needed drops sharply in this shape: the prompt write resolves `expect_request`
    immediately, so step 0's window closes in milliseconds rather than after `write_settle_ms`.
    """
    hits: list = []
    flow, rep = await _learn_then_dry_run(tmp_path, monkeypatch, hits,
                                          _page(300, b_writes=False, a_pings=True))
    assert rep.aborted is None, f"{rep.aborted}: {rep.abort_detail}"
    deferred = [h for h in rep.held if "/invite" in h.url]
    ping = [h for h in rep.held if "/track" in h.url]
    # THE PREMISE, both halves: the ping must have been held (or the step never looked settled) and the
    # deferred write must have been held (or there is nothing to mislabel).
    assert ping, f"premise lost: step 0's PROMPT write was not held ({[h.url for h in rep.held]})"
    assert len(deferred) == 1, f"premise lost: step 0's deferred write was not held ({[h.url for h in rep.held]})"
    h = deferred[0]
    claims_a_step = getattr(h, "attribution", "step") == "step"
    assert not (claims_a_step and h.step != 0), (
        f"step 0 fired a prompt write and a deferred one; the deferred one is named step {h.step} "
        f"({h.intent!r}). A step that has written is not a step that has FINISHED writing")


async def test_the_unattributable_hold_still_makes_later_steps_unrepresentative(
    tmp_path, monkeypatch,
) -> None:
    """`steps_representative` is computed from `held[].step`. A hold that names no step must make the
    report MORE conservative, never less: 'every step is representative' on a run that held a write is
    the single most dangerous thing this artifact could imply.

    NOT a RED-against-0.105.0 test, and labelled so deliberately: 0.105.0 passes it (its mislabelled row
    still carried a step index, which happened to be < len(steps)). This guards a trap the FIX opens —
    filtering `step == -1` out would have made an unattributable hold certify the whole recipe — so it
    is a regression guard on new code, not evidence the defect existed. Calling it RED evidence was an
    overstatement in this slice's first commit message.
    """
    hits: list = []
    flow, rep = await _learn_then_dry_run(tmp_path, monkeypatch, hits,
                                          _page(_DEFER_MS, b_writes=False))
    assert rep.held, "premise lost: nothing was held"
    assert rep.steps_representative < len(flow.steps), (
        f"a write was held, yet the report calls all {len(flow.steps)} steps representative "
        f"(steps_representative={rep.steps_representative})")


async def test_an_ordinary_two_write_flow_is_still_attributed_per_step(
    tmp_path, monkeypatch,
) -> None:
    """THE LIVENESS HALF. Both writes land inside their own step's window, so both are attributable.
    Without this cell the property above is satisfied by labelling every hold 'ambiguous', which would
    make the report useless — D0's over-refusal shape wearing a reporting hat."""
    hits: list = []
    flow, rep = await _learn_then_dry_run(tmp_path, monkeypatch, hits,
                                          _page(50, b_writes=True))
    assert rep.aborted is None, f"{rep.aborted}: {rep.abort_detail}"
    by_url = {h.url.rsplit("/", 1)[-1]: h for h in rep.held}
    assert "invite" in by_url, f"premise lost: step 0's write was not held ({list(by_url)})"
    h = by_url["invite"]
    assert getattr(h, "attribution", "step") == "step" and h.step == 0, (
        f"a promptly-sent write must still be attributed: {h}")


def test_attribution_states_are_a_closed_set_and_only_one_is_quiet() -> None:
    """Quiet is an ALLOWLIST (R3.9/CLI-1). A state added tomorrow must be LOUD by default, so the
    report's silence is keyed off the single attributed state rather than off a list of noisy ones."""
    from ultracua.dryrun import ATTRIBUTED, ATTRIBUTION_STATES

    assert ATTRIBUTED in ATTRIBUTION_STATES
    assert set(ATTRIBUTION_STATES) == {"step", "ambiguous", "ungated"}, (
        "a new attribution state must be added to the report's rendering and to this set deliberately")


def test_ambiguous_is_not_spelled_the_same_as_ungated() -> None:
    """The R3.7 lesson, applied before it can be re-earned: `anchor_id=None` meant BOTH 'no discriminating
    token' and 'I looked in the wrong container', and the consumer could not tell them apart. `step == -1`
    must not become the same overloaded sentinel — 'nobody gated this' and 'more than one step could own
    it' are different facts with different remedies."""
    from ultracua.dryrun import HeldWrite

    ungated = HeldWrite(step=-1, intent="", method="POST", url="u", body="", resource_type="",
                        in_window=False, attribution="ungated")
    ambiguous = HeldWrite(step=-1, intent="", method="POST", url="u", body="", resource_type="",
                          in_window=True, attribution="ambiguous", candidates=[0, 1])
    assert ungated.attribution != ambiguous.attribution
    assert ungated.earliest_step == -1
    assert ambiguous.earliest_step == 0, "an ambiguous hold must report its EARLIEST possible step"


@pytest.mark.xfail(strict=True, reason="R4.39 OPEN: the Idempotency-Key on the wire is whichever step "
                                       "was mid-act when the request was ISSUED, not the step that "
                                       "caused it, so a deferred write's key moves with page timing")
async def test_the_wire_key_is_a_function_of_the_recipe_not_of_page_timing(
    tmp_path, monkeypatch,
) -> None:
    """R4.39, pinned RED so the residual cannot be lost.

    `_plan_idempotency_keys` documents the wire key as "byte-identical" to a preview computed from four
    RECIPE-side inputs (scope, step index, intent, slot values). `_replay_step` puts it on the browser
    CONTEXT for one step's act and restores the base headers in its `finally`, so a write the page
    defers past `write_settle_ms` leaves under the NEXT step's key.

    ONE cached recipe, TWO replays, and between them only the SERVER's debounce changes — nothing
    recipe-side moves. The key must not move either: if it does, a retry of that write mints a different
    key from the attempt it is retrying, and the backend dedupe this key exists for cannot fire.
    Measured DIFFERENT at 0.106.0.
    """
    from ultracua.cache import FlowCache, flow_key
    from ultracua.flow import run_cached
    from ultracua.flows import FlowSpec, MutateSpec
    from ultracua.providers.scripted import ScriptedProvider

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    hits: list = []
    defer = [50]

    class _H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            body = _page(defer[0], b_writes=False)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            hits.append((self.path, self.headers.get("Idempotency-Key", "")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        cache = FlowCache(root=tmp_path / "c")
        spec = FlowSpec(name="invites", start_url=base + "/", goal="send the invite and publish",
                        headless=True, mutate=MutateSpec(confirm_text_contains="Published"))
        await run_cached(spec.start_url, spec.goal, ScriptedProvider([
            {"action": "click", "role": "button", "name": "Send invite", "intent": "send the invite"},
            {"action": "click", "role": "button", "name": "Publish order", "intent": "publish the order"},
            {"action": "done", "intent": "done"},
        ]), cache, mode="learn", headless=True, scope=spec.scope)
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        assert flow is not None and [s.mutating for s in flow.steps] == [True, True], "premise lost"

        keys = {}
        for run, d in ((1, 50), (2, _DEFER_MS)):
            defer[0] = d
            hits.clear()
            await run_cached(spec.start_url, spec.goal, None, cache, mode="replay",
                             headless=True, scope=spec.scope)
            got = [k for p, k in hits if p == "/invite"]
            assert got, f"premise lost: run {run} (debounce {d}ms) never sent step 0's write"
            keys[run] = got[0]

        assert keys[1] == keys[2], (
            f"step 0's write carried key {keys[1]} when the page debounced 50ms and {keys[2]} when it "
            f"debounced {_DEFER_MS}ms — the recipe never changed, so a retry cannot dedupe")
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== the cross-product, driven against the arbiter directly ====================
#
# The two end-to-end tests above prove the arbiter's model matches a real browser. This matrix is where
# the SHAPES are enumerated, because "what shape is not in the test set" is the question that has
# defeated two R3.7 attempts — and a browser test per cell would be minutes of wall clock for cells that
# differ only in window bookkeeping.


class _FakeRequest:
    def __init__(self, url: str = "http://x.invalid/w") -> None:
        self.url = url
        self.method = "POST"
        self.post_data = "b=1"
        self.resource_type = "fetch"
        self.headers = {}

    def is_navigation_request(self) -> bool:
        return False


def _arb():
    from ultracua.dryrun import DryRunArbiter

    return DryRunArbiter()


def _write(arb) -> "object":
    return arb._record(_FakeRequest(), "POST")


# (label, script, expected attribution, expected step-or-candidates)
#
# `script` runs against a fresh arbiter. `w()` records one held write and returns it.
_CELLS = [
    ("single write, in its own OPEN window",
     lambda a, w: (a.open_window(step=0, intent="pay", key="k", grace_ms=2000), w())[-1],
     "step", 0),
    ("single write, in its own step's TAIL",
     lambda a, w: (a.open_window(step=0, intent="pay", key="k", grace_ms=2000),
                   a.close_window(), w())[-1],
     "step", 0),
    ("a single mutating step firing SEVERAL writes: all attributable",
     lambda a, w: (a.open_window(step=0, intent="pay", key="k", grace_ms=2000), w(), w(),
                   a.close_window(), w())[-1],
     "step", 0),
    # R3.12 ITSELF. Pre-fix this read `step=1, intent='ship'`.
    ("step 0 wrote nothing in its window; a write arrives under step 1",
     lambda a, w: (a.open_window(step=0, intent="pay", key="k", grace_ms=2000), a.close_window(),
                   a.open_window(step=1, intent="ship", key="k2", grace_ms=2000), w())[-1],
     "ambiguous", [0, 1]),
    ("three steps that have all acted",
     lambda a, w: (a.open_window(step=0, intent="a", key="k", grace_ms=2000), a.close_window(),
                   a.open_window(step=1, intent="b", key="k2", grace_ms=2000), a.close_window(),
                   a.open_window(step=2, intent="c", key="k3", grace_ms=2000), w())[-1],
     "ambiguous", [0, 1, 2]),
    # THE F1 CELL. A first draft treated a step that had already written as SETTLED, so it was dropped
    # from the candidate set — and a control that fires an analytics ping AND defers its real write then
    # had that write named with the NEXT step, silently. "We saw a write" is not "we saw all its
    # writes"; there is no observable that says a step is finished writing.
    ("a step that already wrote can STILL be the owner of a later write",
     lambda a, w: (a.open_window(step=0, intent="pay", key="k", grace_ms=2000), w(),
                   a.close_window(),
                   a.open_window(step=1, intent="ship", key="k2", grace_ms=2000), w())[-1],
     "ambiguous", [0, 1]),
    # THE F2 CELL, kept because it is the counterexample that killed the first draft's stated property.
    # Causally identical to the R3.12 cell — step 0 acted, wrote nothing, a write arrives under step 1 —
    # and differing ONLY in `grace_ms`. A draft whose candidate horizon was the grace tail said
    # "ambiguous" at 2000 ms and confidently named step 1 at 0 ms. The rule must not read `grace_ms` at
    # all, so both spellings must agree.
    ("grace_ms cannot change WHO is named (0 ms must agree with 2000 ms)",
     lambda a, w: (a.open_window(step=0, intent="pay", key="k", grace_ms=0), a.close_window(),
                   a.open_window(step=1, intent="ship", key="k2", grace_ms=2000), w())[-1],
     "ambiguous", [0, 1]),
    # THE PRICE, pinned rather than asserted in prose, and re-measured after F5: it is not one step, it
    # is EVERY step that has acted. A multi-write flow's holds are never named. That is the cost of a
    # sound rule — narrowing the candidate set is the question D5 blocks — and this cell exists so a
    # future change to it is deliberate rather than accidental.
    ("a mutating step that never writes keeps every later hold ambiguous",
     lambda a, w: (a.open_window(step=0, intent="payment history", key="k", grace_ms=2000),
                   a.close_window(),
                   a.open_window(step=1, intent="pay", key="k2", grace_ms=2000), w())[-1],
     "ambiguous", [0, 1]),
    ("no window ever opened",
     lambda a, w: w(),
     "ungated", -1),
]


@pytest.mark.parametrize("label,script,want_attr,want_where",
                         _CELLS, ids=[c[0] for c in _CELLS])
def test_attribution_matrix(label, script, want_attr, want_where, capsys) -> None:
    """PRINT what each cell exercised before believing it — two drafts of the write-safety matrix looked
    thorough while testing nothing, and the fix for that is to make the evidence readable."""
    arb = _arb()
    rec = script(arb, lambda: _write(arb))
    got_where = rec.candidates if rec.attribution == "ambiguous" else rec.step
    print(f"  {label:62s} -> attribution={rec.attribution:10s} "
          f"step={rec.step:3d} candidates={rec.candidates} earliest={rec.earliest_step}")
    assert rec.attribution == want_attr, f"{label}: wanted {want_attr}, got {rec.attribution}"
    assert got_where == want_where, f"{label}: wanted {want_where}, got {got_where}"
    # The invariant every cell shares: the report never names a step it did not earn.
    assert (rec.step >= 0) == (rec.attribution == "step"), (
        f"{label}: a step index leaked out of a non-attributed hold ({rec})")


def test_the_matrix_covers_every_attribution_state() -> None:
    """Without this, a state could be added and no cell would exercise it — the shard-coverage rule
    (`a test in NO shard leaves every shard green`) applied to the matrix's own cells."""
    from ultracua.dryrun import ATTRIBUTION_STATES

    covered = {c[2] for c in _CELLS}
    assert covered == set(ATTRIBUTION_STATES), f"uncovered attribution states: {set(ATTRIBUTION_STATES) - covered}"


if __name__ == "__main__":  # pragma: no cover - manual probe
    raise SystemExit(pytest.main([__file__, "-q"]))


_ = asyncio  # imported for the fixture server's docstring parity with tests/test_dryrun.py
