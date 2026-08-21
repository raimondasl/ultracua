"""R3.3 — a write the code KNOWS landed does not arm the ledger, so the CLI tells you to resume it.

R8 (0.72.0) added `FlowReplayError.landed` and set it True on exactly ONE class, `WriteReadbackError`.
But `_attempt_replay` has more than one failure return downstream of the write-landed evidence:

    found=True, extract_found=False   -> "write_unreadable"   landed=True    (armed by R8)
    found=True, extract_found=True,
                shape changed         -> "shape"              landed=False   <-- R3.3

The second is STRICTLY STRONGER evidence than the first: the confirm transitioned AND the readback was
read cleanly. The shape gate is not fenced to reads either — the H9 contract/magnitude gates just below
it carry `if check_shape and spec.mutate is None`, the shape gate does not — and `meta.shape` IS seeded
for write flows, so `flow learn --extract ... --confirm-text ...` leaves an approved WRITE flow with a
non-None `meta.shape`.

So a row whose payment demonstrably committed comes back as `ShapeDriftError(landed=False)`. `run_batch`
writes no ledger line, reports the row `failed`, and `cli` prints a `--resume` command described as "the
rows that DIDN'T commit". The operator resumes and the payment is submitted again. Shape drift is
deterministic, so every subsequent resume re-fires it too.

WHY THIS FILE IS SHAPED AS A PROPERTY, NOT A SCENARIO
-----------------------------------------------------
The plan (slice S3) is explicit that "also arm it for ShapeDriftError" is the WRONG fix — that is the
per-exception-class patch shape that produced R3.3 in the first place, one class at a time. The
invariant is positional, not typological:

    once the confirm TRANSITION has been observed, every failure raised afterwards — present and
    future, whatever its class — must carry `landed=True`.

`test_every_failure_after_the_confirm_transition_is_landed` asserts exactly that, over the failure
returns that exist below the evidence point, so a NEW return added there is covered without anyone
remembering to arm it.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from ultracua.cache import CachedFlow, CachedStep, FlowCache
from ultracua.llm.base import Router, Tier
from ultracua.locators import LocatorSpec
from ultracua.llm.mock import MockClient
from ultracua.providers.scripted import ScriptedProvider


def _extract_router(*datas) -> Router:
    """A Router whose successive extraction calls return {found: True, data: <each>}."""
    mc = MockClient(actions=[{"found": True, "data": d} for d in datas], tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


def _pay_agent(*, trailing_step: bool = False):
    """Scripted agent: click Pay, optionally click a POST-COMMIT control, then done."""
    acts = [{"action": "click", "role": "button", "name": "Pay invoice", "intent": "pay the invoice"}]
    if trailing_step:
        acts.append({"action": "click", "role": "button", "name": "Print receipt",
                     "intent": "print the receipt"})
    return ScriptedProvider(acts + [{"action": "done", "intent": "done"}])


class _Site:
    """A pay button that POSTs and then, IN THE PAGE, reveals the confirm banner + the reference.

    The banner is revealed client-side on purpose: every GET must serve the PRE-commit page, so the
    whole-flow confirm is a genuine absent->present TRANSITION on each run. A server that remembered the
    commit would make the second run `confirm_pre_true`, i.e. `write_unverified` — a different finding.
    """

    def __init__(self) -> None:
        self.posts: list[str] = []
        self.drift = False      # when True the TRAILING (post-commit) control is gone

    def serve(self):
        site = self

        class _H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def _send(self, body: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body.encode())))
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self) -> None:  # noqa: N802
                # The trailing control is revealed BY the commit, so the ENTRY page — and therefore the
                # pay button's recorded precondition — is identical with and without the drift. That
                # matters: a drift that also perturbs the commit's own precondition makes the mutation
                # gate refuse the click, nothing POSTs, and the test would measure the wrong thing (it
                # did, on the first attempt: 0 payments fired).
                #
                # The reveal is SYNCHRONOUS, outside the fetch's `.then()`, and that is load-bearing.
                # Inside the callback it raced the agent's next observation during LEARN: on a slower
                # machine the recipe captured only the pay step, so at replay there was no trailing step
                # to drift and the test failed with "DID NOT RAISE". That is exactly how it failed on
                # Ubuntu CI while passing on Windows. The BANNER stays in `.then()` — it must genuinely
                # follow the POST for the confirm to be a real transition — and finalize polls for it, so
                # it has time. Only the button's presence needs to be deterministic.
                reveal = ("" if site.drift else
                          "var b=document.createElement('button');b.type='button';"
                          "b.textContent='Print receipt';document.body.appendChild(b);")
                self._send(
                    "<h1>Invoice</h1><div id='r'>pending</div>"
                    "<button id='p' type='button' onclick=\"" + reveal +
                    "fetch('/pay',{method:'POST',body:'x=1'}).then(function(){"
                    "document.getElementById('r').textContent='Payment sent REF-99';"
                    "});\">Pay invoice</button>")

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.posts.append(self.headers.get("Idempotency-Key") or "")
                self._send("ok")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


class _StaleBannerSite:
    """The entry page ALREADY carries the confirm text, and the control the FIRST step needs is gone.

    So the run fails before it ever reaches the write, and the whole-flow confirm is satisfied by a
    banner left over from a previous order."""

    def __init__(self) -> None:
        self.posts: list[str] = []

    def serve(self):
        site = self

        class _H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                body = ("<h1>Invoice</h1><div>Payment sent REF-01</div>"
                        "<button id='p' type='button' onclick=\""
                        "fetch('/pay',{method:'POST',body:'x=1'});\">Pay invoice</button>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body.encode())))
                self.end_headers()
                self.wfile.write(body.encode())

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.posts.append(self.headers.get("Idempotency-Key") or "")
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_a_run_that_never_reached_the_write_does_not_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE FALSE-ARM DIRECTION, which is worse than the bug this slice closes.

    `out["_pre_confirm"]` — the A8 baseline that makes the whole-flow confirm a TRANSITION rather than a
    presence check — is written by a hook `flow.py` calls only when the step loop REACHES a step with
    `mutating=True`. A run that fails BEFORE the write never takes that baseline, so `_pre_confirm` is
    absent, and `bool(out.get("_pre_confirm"))` collapses "never measured" into False. If a stale banner
    from a previous order happens to satisfy the confirm, `found` is True and `confirm_pre_true` is False
    — and a naive `landed = found and not confirm_pre_true` reads a run that WROTE NOTHING as a
    committed write.

    That is not a missed fix, it is a REGRESSION: on the pre-fix code this failure carried `landed=False`,
    no ledger row was written, and the resume re-ran the row and paid the invoice. Armed, `run_batch`
    writes a ledger row, every `--resume` reports "already committed — not re-fired", and **the invoice is
    never paid, silently and permanently** — against `ledger.py`'s stated invariant, "never a false skip
    of an un-landed write". The disclosure compounds it by telling the operator not to pay by hand.

    The shape is this project's own "absent vs unreadable" lesson (R3.1/R3.4): a boolean that cannot say
    "never measured" gets its third state read as the safe one. So the arming requires POSITIVE proof the
    baseline ran — `"_pre_confirm" in out`, i.e. the KEY's presence. (Not `out.get("_pre_confirm") is
    False`, which is a different and wrong predicate: it excludes the measured-and-true case, collapsing
    back to exactly the two-state read this paragraph argues against.)"""
    from ultracua import flows as flows_mod

    site = _StaleBannerSite()
    httpd, base = site.serve()
    try:
        spec = flows_mod.FlowSpec(
            name="inv", goal="pay the invoice", start_url=f"{base}/", headless=True,
            mutate=flows_mod.MutateSpec(confirm_text_contains="Payment sent"))
        cache = FlowCache(root=tmp_path / "c")
        cache.put(CachedFlow(
            key=flows_mod.flow_key(spec.goal, spec.start_url, spec.scope), goal=spec.goal,
            start_url=f"{base}/", created_ts=1e9,
            steps=[
                # Step 0 is GONE at replay, so the run stops before the write below it.
                CachedStep(intent="open the payment form", action="click", mutating=False,
                           precond_fingerprint="",
                           locator=LocatorSpec(tag="button", role="button", name="Open form")),
                CachedStep(intent="pay the invoice", action="click", mutating=True, precond_scope="",
                           precond_fingerprint="",
                           locator=LocatorSpec(tag="button", role="button", name="Pay invoice")),
            ]))
        monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
        flows_mod.approve(spec, cache=cache)

        with pytest.raises(flows_mod.FlowReplayError) as ei:
            await flows_mod.replay(spec, cache=cache)

        assert site.posts == [], (
            f"the fixture actuated the write ({site.posts}); this test must exercise a run that never "
            f"reached it, or it proves nothing")
        assert ei.value.landed is False, (
            "a run that fired ZERO writes was armed as a committed write, because the A8 baseline was "
            "never taken and a stale banner satisfied the confirm. run_batch would write a ledger row, "
            "every --resume would skip the row as 'already committed', and the invoice would never be "
            "paid — silently, permanently. This is the direction `ledger.py` forbids and it is a "
            "REGRESSION: unarmed, the resume re-runs the row and pays it.")
        assert "did commit" not in str(ei.value), (
            f"the operator is told not to re-submit a payment that never happened: {ei.value}")
    finally:
        httpd.shutdown()
        httpd.server_close()


class _LateBannerSite:
    """The write step's control is RENAMED (so that step drifts and nothing POSTs), and a banner paints
    ~300ms later — inside the finalize confirm's poll window but after the instantaneous baseline."""

    def __init__(self) -> None:
        self.posts: list[str] = []

    def serve(self):
        site = self

        class _H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                body = ("<h1>Invoice</h1>"
                        "<button id='p' type='button' onclick=\""
                        "fetch('/pay',{method:'POST',body:'x=1'});\">Settle invoice</button>"
                        "<script>setTimeout(function(){var d=document.createElement('div');"
                        "d.textContent='Payment sent REF-01';document.body.appendChild(d);},300);"
                        "</script>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body.encode())))
                self.end_headers()
                self.wfile.write(body.encode())

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.posts.append(self.headers.get("Idempotency-Key") or "")
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_a_failed_write_step_does_not_arm_even_if_the_confirm_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE THIRD FALSE-ARM ENTRY, and the one that survives "positive proof the baseline ran".

    `pre_write` is called BEFORE `_replay_step` attempts the action, so merely REACHING a mutating step
    puts `_pre_confirm` in `out`. The click, the mutation gate and the POST all happen afterwards. And the
    two probes are asymmetric by construction: the baseline is a single instantaneous check
    (`timeout_ms=0`) while the finalize confirm POLLS for seconds. So on a run whose write step itself
    drifts — a renamed commit control, a mutation-gate refusal, an ambiguous target; none of which are
    healed, because mutating steps never are — anything matching the confirm that paints inside that
    window reads as an absent->present "transition" with ZERO writes.

    `flow.py` already has the missing fact and already uses it ONE GUARD OVER: the per-step commit
    barrier gates its identical transition check on `ok` (the step succeeded). The whole-flow arming did
    not. That is this project's own predictor — a guard on a sibling path never applied to the mechanism.

    The residual after this fix is stated rather than hidden: a click that SUCCEEDS but fires no request,
    with a late-painting banner, still arms. That is A8's documented residual, and it is exactly the
    residual the SUCCESS path already carries — the success path records a ledger row on the same
    `confirmed`. Parity with the success path is the claim; nothing stronger."""
    from ultracua import flows as flows_mod

    site = _LateBannerSite()
    httpd, base = site.serve()
    try:
        spec = flows_mod.FlowSpec(
            name="inv", goal="pay the invoice", start_url=f"{base}/", headless=True,
            mutate=flows_mod.MutateSpec(confirm_text_contains="Payment sent"))
        cache = FlowCache(root=tmp_path / "c")
        cache.put(CachedFlow(
            key=flows_mod.flow_key(spec.goal, spec.start_url, spec.scope), goal=spec.goal,
            start_url=f"{base}/", created_ts=1e9,
            steps=[CachedStep(intent="pay the invoice", action="click", mutating=True, precond_scope="",
                              precond_fingerprint="",
                              locator=LocatorSpec(tag="button", role="button", name="Pay invoice"))]))
        monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
        flows_mod.approve(spec, cache=cache)

        # Capture what `finalize` actually observed, so the anti-vacuity check below can tell "refused
        # for the right reason" from "the fixture never got into the interesting state".
        captured: dict = {}
        real_finalize = flows_mod._make_finalize

        def _spy(spec_, router_, out_, **kw):
            fn = real_finalize(spec_, router_, out_, **kw)

            async def _wrapped(session):
                try:
                    return await fn(session)
                finally:
                    captured.update(out_)
            return _wrapped

        monkeypatch.setattr(flows_mod, "_make_finalize", _spy)

        with pytest.raises(flows_mod.FlowReplayError) as ei:
            await flows_mod.replay(spec, cache=cache)

        assert site.posts == [], (
            f"the fixture actuated the write ({site.posts}); this test must exercise a run whose write "
            f"step FAILED, or it proves nothing")
        # ANTI-VACUITY. The banner is on a 300ms timer racing an instantaneous baseline probe. On a
        # loaded machine the baseline can land AFTER it, which makes `confirm_pre_true` True, so
        # `write_landed` is False and the assertion below passes for a reason that has nothing to do with
        # the conjunct under test — the test would then stay green with the step-success check deleted.
        # Pin the state the fixture must have reached, so a lost race fails LOUD instead of silently
        # testing nothing.
        assert captured.get("write_landed") is True, (
            f"the fixture did not reach the state this test exists to probe: finalize reported "
            f"write_landed={captured.get('write_landed')!r} (confirm_pre_true="
            f"{captured.get('confirm_pre_true')!r}). The 300ms banner timer probably lost its race with "
            f"the baseline probe, so the step-success conjunct was never the thing being tested.")
        assert ei.value.landed is False, (
            "the write step itself failed and ZERO requests fired, but the run armed because the "
            "baseline had been taken (it is probed before the action) and a banner painted inside the "
            "confirm's poll window. run_batch writes a ledger row, every --resume skips the row as "
            "'already committed', and the invoice is never paid. Unarmed, the resume re-runs it and pays "
            "it — so this is a REGRESSION, in the direction ledger.py forbids.")
    finally:
        httpd.shutdown()
        httpd.server_close()


class _TwoWriteSite:
    """Two controls the recipe marks mutating. `drift` renames the SECOND one, so it never fires.

    `first_writes=False` makes the first control inert — the shape of a classifier FALSE POSITIVE, e.g. a
    "Payment history" link that this project pins as misclassified. It reveals a prior order's banner, so
    the whole-flow confirm is satisfied by history rather than by anything this run did."""

    def __init__(self, *, first_writes: bool) -> None:
        self.posts: list[str] = []
        self.drift = False
        self.first_writes = first_writes

    def serve(self):
        site = self

        class _H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                first = ("fetch('/pay',{method:'POST',body:'x=1'});" if site.first_writes else "")
                second = "Archive it" if site.drift else "Second action"
                body = ("<h1>Invoice</h1>"
                        "<button id='a' type='button' onclick=\"" + first +
                        "var d=document.createElement('div');"
                        "d.textContent='Payment sent REF-01';document.body.appendChild(d);\">"
                        "Payment history</button>"
                        "<button id='b' type='button' onclick=\""
                        "fetch('/archive',{method:'POST',body:'y=1'});\">" + second + "</button>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body.encode())))
                self.end_headers()
                self.wfile.write(body.encode())

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.posts.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _spy_on_finalize(flows_mod, monkeypatch) -> dict:
    """Capture what `finalize` observed, so a test can prove it reached the state it claims to probe.

    Without this, `landed is False` passes for ANY reason — including the fixture never getting into the
    interesting shape — and the test stays green with the conjunct under test deleted. This file's own
    standing rule is to print what a cell exercised before believing it."""
    captured: dict = {}
    real = flows_mod._make_finalize

    def _spy(spec_, router_, out_, **kw):
        fn = real(spec_, router_, out_, **kw)

        async def _wrapped(session):
            try:
                return await fn(session)
            finally:
                captured.update(out_)
        return _wrapped

    monkeypatch.setattr(flows_mod, "_make_finalize", _spy)
    return captured


async def _two_write_flow(flows_mod, tmp_path, monkeypatch, base):
    spec = flows_mod.FlowSpec(
        name="inv", goal="pay the invoice", start_url=f"{base}/", headless=True,
        mutate=flows_mod.MutateSpec(confirm_text_contains="Payment sent"))
    cache = FlowCache(root=tmp_path / "c")
    cache.put(CachedFlow(
        key=flows_mod.flow_key(spec.goal, spec.start_url, spec.scope), goal=spec.goal,
        start_url=f"{base}/", created_ts=1e9,
        steps=[
            CachedStep(intent="open payment history", action="click", mutating=True, precond_scope="",
                       precond_fingerprint="",
                       locator=LocatorSpec(tag="button", role="button", name="Payment history")),
            CachedStep(intent="second action", action="click", mutating=True, precond_scope="",
                       precond_fingerprint="",
                       locator=LocatorSpec(tag="button", role="button", name="Second action")),
        ]))
    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    flows_mod.approve(spec, cache=cache)
    return spec, cache


async def test_a_sibling_mutating_step_cannot_arm_for_a_failed_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE QUANTIFIER. With `any(...)`, a SECOND step marked mutating arms the run off whichever one
    succeeded — even when that one is a classifier false positive that writes nothing and the real commit
    step failed.

    The exploit needs no exotic page: `classify_mutation` matches `pay` inside "Payment history", which
    `tests/test_write_classification.py` pins as a known false positive and CLAUDE.md measures at ~28% of
    ordinary read controls. So a recipe with one misclassified navigation plus one real commit is the
    common case, not a corner. Here the navigation succeeds and reveals a PRIOR order's banner, the real
    commit step has drifted, ZERO requests fire, and the run must not arm."""
    from ultracua import flows as flows_mod

    site = _TwoWriteSite(first_writes=False)
    httpd, base = site.serve()
    try:
        spec, cache = await _two_write_flow(flows_mod, tmp_path, monkeypatch, base)
        captured = _spy_on_finalize(flows_mod, monkeypatch)
        site.drift = True                       # the second (real commit) control is renamed

        with pytest.raises(flows_mod.FlowReplayError) as ei:
            await flows_mod.replay(spec, cache=cache)

        assert site.posts == [], f"the fixture wrote something ({site.posts}); it must not"
        assert captured.get('write_landed') is True, (
            f"the fixture did not reach the armed-by-write_landed state (write_landed="
            f"{captured.get('write_landed')!r}); the step-success conjunct was never under test")
        assert ei.value.landed is False, (            "a run that fired ZERO requests armed the ledger, because a SIBLING step marked mutating "
            "succeeded and a prior order's banner satisfied the confirm. run_batch records the row, "
            "every --resume skips it as 'already committed', and the invoice is never paid. `any()` is "
            "the wrong quantifier: every step the RECIPE marks mutating must have run and succeeded.")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_multiwrite_row_whose_second_write_failed_does_not_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same quantifier, on a GENUINE multi-write. Write #1 fires and its banner transitions; write #2
    drifts and never fires. Arming the row records it as committed, so a resume SUPPRESSES write #2
    permanently — inviolable #3's second clause, and a verbatim contradiction of `ledger.py`: "a
    multi-write row that died mid-flow is not recorded and re-fires all its writes on resume"."""
    from ultracua import flows as flows_mod

    site = _TwoWriteSite(first_writes=True)
    httpd, base = site.serve()
    try:
        spec, cache = await _two_write_flow(flows_mod, tmp_path, monkeypatch, base)
        captured = _spy_on_finalize(flows_mod, monkeypatch)
        site.drift = True

        with pytest.raises(flows_mod.FlowReplayError) as ei:
            await flows_mod.replay(spec, cache=cache)

        assert site.posts == ["/pay"], (
            f"expected write #1 to fire and write #2 not to; got {site.posts}")
        assert captured.get("write_landed") is True, (
            f"the fixture did not reach the armed-by-write_landed state "
            f"(write_landed={captured.get('write_landed')!r}); the quantifier is then not what this "
            f"test is measuring, and it would stay green with `all` weakened back to `any`")
        assert ei.value.landed is False, (
            "write #1 landed and write #2 never fired, but the ROW armed as committed — so a resume "
            "would skip it and write #2 would be silently suppressed forever. The ledger checkpoints at "
            "whole-flow granularity, so a row that died mid-flow must not be recorded.")
        # ...AND THE OPERATOR MUST STILL BE TOLD. Arming and disclosure answer DIFFERENT questions:
        # the ledger asks "may a resume skip this whole row" (needs every write ok), disclosure asks
        # "did anything commit" (needs one). Routing both through the ledger predicate suppresses the
        # warning in precisely this shape — and this is the shape with NO Idempotency-Key floor, because
        # the operator, seeing a bare `[FAIL] … page drift`, pays by hand through another channel. That
        # is verbatim the harm the disclosure was added to prevent.
        assert "did commit" in str(ei.value).lower(), (
            f"write #1 fired and the operator is not told: {ei.value}")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_post_commit_step_drift_still_arms_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE LARGEST POPULATION, and the one the first version of this fix MISSED.

    `finalize` runs UNCONDITIONALLY (`flow.py` calls it outside the step loop), so `out["found"]` — the
    confirm's absent->present transition — can be True on a run whose `report.success` is False because a
    LATER step drifted. A trailing "Print receipt" / "Back to list" / "Continue" step is the canonical
    shape of a write flow, so this is a bigger population than the shape gate that R3.3 was filed for.

    The first fix set `landed` at a POSITION below the `not report.success` guard, so this return carried
    `landed=False` after a demonstrated commit — and then the slice's own new retry stop, keyed off that
    False, let the auth-refresh path re-run the flow from `start_url`. Measured before the fix: TWO
    payments for one operator request.

    The lesson, recorded because it cost three criticals: the first design replaced a TYPOLOGICAL proxy
    (the exception's class) with a POSITIONAL one (where the return sits); the second replaced that with
    a collapsed boolean (`found and not confirm_pre_true`) that could not say "never measured". All three
    were proxies. `landed` is now a CONJUNCTION of two independently-sourced facts — the A8 baseline ran
    and the confirm then transitioned (from `finalize`), AND EVERY step the cached recipe marks
    `mutating` ran and succeeded (from the step loop's traces) — and every failure exit inherits it
    through `_fail`. The quantifier is `all` over the RECIPE; `any`, and counting against the traces
    instead, each shipped a critical of their own."""
    from ultracua import flows as flows_mod

    site = _Site()
    httpd, base = site.serve()
    try:
        # `retryable=True` is load-bearing: without a `login` AND a precheck, `_auth_retry_allowed`
        # refuses the retry for unrelated reasons and the `fired == 1` assertion below can never fail.
        spec, cache = await _learned_write_flow(flows_mod, tmp_path, monkeypatch, base, "REF-99",
                                                trailing_step=True, retryable=True)
        # ANTI-VACUITY. Everything below depends on the recipe having a POST-COMMIT step to drift. If the
        # learn captured only the pay step, `site.drift` changes nothing, the replay succeeds, and the
        # failure reads "DID NOT RAISE" — which says nothing about the property under test. That is how
        # this test failed on Ubuntu while passing on Windows, so the premise is now pinned.
        learned = cache.get(flows_mod.flow_key(spec.goal, spec.start_url, spec.scope))
        assert learned is not None and len(learned.steps) == 2, (
            f"the fixture did not learn a trailing post-commit step "
            f"(steps={[s.intent for s in (learned.steps if learned else [])]}) — the drift below would "
            f"then be a no-op and this test would prove nothing")
        posts_after_learn = len(site.posts)

        refreshed = {"n": 0}

        async def _fake_refresh(spec, **kw):
            refreshed["n"] += 1

        monkeypatch.setattr(flows_mod, "refresh_auth", _fake_refresh)
        site.drift = True          # the POST-COMMIT control is gone; the commit itself is untouched

        with pytest.raises(flows_mod.FlowReplayError) as ei:
            await flows_mod.replay(spec, router=_extract_router("REF-99", "REF-99"), cache=cache)

        fired = len(site.posts) - posts_after_learn
        assert fired == 1, (
            f"the payment fired {fired} times for ONE request (keys={site.posts[posts_after_learn:]}, "
            f"auth refreshes={refreshed['n']}). The confirm TRANSITIONED, so the commit is known to have "
            f"landed — but the failure came back `landed=False` because a later step drifted, and the "
            f"auth-refresh retry re-ran the whole flow from start_url.")
        assert ei.value.landed is True, (
            "a post-commit step drifted. The confirm transition was still observed, so the write is "
            "known to have committed — but this failure reports landed=False, so no ledger row is "
            "written and every `--resume` re-fires the payment. That is R3.3's harm, in the population "
            "the first version of this fix did not cover.")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def _learned_write_flow(flows_mod, tmp_path: Path, monkeypatch, base: str, learn_value,
                              *, trailing_step: bool = False, retryable: bool = False):
    """Learn a WRITE flow that also extracts, then approve it. `meta.shape` is seeded from
    `learn_value` — which is what makes the shape gate reachable on a write flow at all.

    `retryable=True` gives the spec a `login` AND a whole-flow precheck, which is what
    `_auth_retry_allowed` requires before it will grant the auth-refresh retry at all. Without both, the
    retry is structurally unreachable and any test asserting "the payment fired once" is measuring
    nothing — an earlier draft of the post-commit-drift test had exactly that hole. The precheck marker
    is deliberately a string the fixture never renders, so `_precheck_done` cannot short-circuit the run
    into `already-done` before the write."""
    spec = flows_mod.FlowSpec(
        name="inv", goal="pay the invoice", start_url=f"{base}/", headless=True,
        extract="the payment reference",
        login=flows_mod.LoginSpec(url=f"{base}/login") if retryable else None,
        mutate=flows_mod.MutateSpec(
            confirm_text_contains="Payment sent",
            precheck_text_contains="Receipt archived" if retryable else None))
    cache = FlowCache(root=tmp_path / "c")
    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    res = await flows_mod.learn(spec, provider=_pay_agent(trailing_step=trailing_step),
                                router=_extract_router(learn_value), cache=cache)
    assert res.cached, f"the fixture did not learn: {res.note}"
    flows_mod.approve(spec, cache=cache)
    meta = flows_mod._load_meta(cache, flows_mod.flow_key(spec.goal, spec.start_url, spec.scope))
    assert meta.shape is not None, (
        "meta.shape was not seeded for this WRITE flow, so the shape gate is unreachable and this test "
        "would prove nothing — the finding depends on a write flow carrying a shape")
    return spec, cache


async def test_a_shape_drift_after_a_confirmed_write_arms_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE defect. The payment POSTs, the confirm banner transitions, the reference IS read back — and
    then the shape gate fires because the vendor now renders it as a number. The write demonstrably
    landed; `ShapeDriftError.landed` is False, so no ledger line is written and the resume re-fires it."""
    from ultracua import flows as flows_mod

    site = _Site()
    httpd, base = site.serve()
    try:
        spec, cache = await _learned_write_flow(flows_mod, tmp_path, monkeypatch, base, "REF-99")
        posts_after_learn = len(site.posts)

        # Same page, same confirm transition, same successful readback — only the VALUE's shape moved.
        with pytest.raises(flows_mod.ShapeDriftError) as ei:
            await flows_mod.replay(spec, router=_extract_router(99), cache=cache)

        assert len(site.posts) == posts_after_learn + 1, (
            f"the replay did not actuate the commit ({site.posts}) — this test must exercise a write "
            f"that really fired, or `landed` is a claim about nothing")
        # DISCLOSURE. Arming the ledger fixes the machine loop; it does not by itself fix the human one.
        # Under R8 the two were coincident by accident — the single armed class was `WriteReadbackError`,
        # whose message literally says "the write WAS confirmed". Extending arming to any class raised
        # past the evidence point breaks that coincidence: `ShapeDriftError`'s message says only that the
        # data shape moved. The operator sees `[FAIL] row 7 ... data shape changed`, does the natural
        # thing for a failed payment row, and pays invoice 7 by hand — a double payment that never
        # touches the Idempotency-Key floor, because it goes through a different channel entirely.
        # `str(exc)` is what reaches all three surfaces (CLI row line, BatchRowResult.error, MCP
        # ToolOutcome.message), so the disclosure belongs there.
        assert "did commit" in str(ei.value).lower() or "committed" in str(ei.value).lower(), (
            f"the error reaching the operator does not say the write went through, so a `[FAIL]` row "
            f"reads as 'nothing happened': {ei.value}")
        assert ei.value.landed is True, (
            "the confirm TRANSITIONED and the readback SUCCEEDED — the write is known to have landed, on "
            "strictly stronger evidence than the write_unreadable case that R8 armed — yet "
            "ShapeDriftError.landed is False. run_batch writes no ledger line, the row reports `failed`, "
            "and the CLI prints a --resume command it describes as 'the rows that DIDN'T commit'. "
            "Resuming re-fires a payment that already went through, deterministically, every time.")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_every_failure_after_the_confirm_transition_is_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE INVARIANT, stated positionally so a future failure return inherits it.

    Not "ShapeDriftError is landed" — that is the per-class patch that produced this finding. The rule is
    that the evidence point is the confirm TRANSITION, and everything downstream of it is landed. This
    drives `_attempt_replay` directly and asserts the property over every failure it can return from
    below that point, so adding a new one without arming it fails here."""
    from ultracua import flows as flows_mod

    site = _Site()
    httpd, base = site.serve()
    try:
        spec, cache = await _learned_write_flow(flows_mod, tmp_path, monkeypatch, base, "REF-99")
        key = flows_mod.flow_key(spec.goal, spec.start_url, spec.scope)
        meta = flows_mod._load_meta(cache, key)

        # (label, router, what the attempt should return, why it is downstream of the evidence)
        cases = [
            ("shape drift after a clean readback", _extract_router(99), "shape"),
            # The readback MISSES: found=True (confirm transitioned), extract_found=False.
            ("readback failed after the confirm", _extract_router(), "write_unreadable"),
        ]
        wrong = []
        for label, router, expect_kind in cases:
            res = await flows_mod._attempt_replay(spec, router, cache, key, meta, True, cached_flow=cache.get(key))
            ok, _data, _reason, kind = res[0], res[1], res[2], res[3]
            landed = res[4] if len(res) > 4 else None
            if ok or kind != expect_kind:
                wrong.append(f"{label}: expected a {expect_kind!r} failure, got ok={ok} kind={kind!r}")
            elif landed is not True:
                wrong.append(
                    f"{label}: kind={kind!r} came back landed={landed!r}. It is downstream of the "
                    f"confirm TRANSITION, so the write is known to have committed and the ledger must "
                    f"be armed.")
        assert not wrong, (
            "a failure raised AFTER the write-landed evidence did not carry it. Arming one exception "
            "class at a time is what produced R3.3; the evidence point is positional:\n  "
            + "\n  ".join(wrong))
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_write_whose_confirm_was_already_true_is_NOT_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE DANGEROUS HALF, without which everything above is satisfied by `landed = True` at the top of
    the function — and that direction is worse than the bug being fixed.

    `ledger.py`'s invariant is "never a false skip of an UN-landed write". A row wrongly marked landed is
    recorded, a resume SKIPS it, and an invoice that was never paid is silently dropped. So the arming
    must be pinned from BOTH sides.

    The case that must never arm is `confirm_pre_true`: the commit actuated but the confirm signal was
    ALREADY present before it ran, so nothing on the page distinguishes a landed write from a banner that
    was already there. It sits INSIDE the `not found` block, i.e. before the evidence point, and must come
    back `landed=False`.

    (`tests/test_round2_fixes.py` and `tests/test_ledger.py` also pin this direction end-to-end; it is
    repeated here because this is the property file for the invariant, and a property file that only
    checks the safe half is how "refuse everything" and "arm everything" both pass.)"""
    from ultracua import flows as flows_mod

    site = _Site()
    httpd, base = site.serve()
    try:
        spec, cache = await _learned_write_flow(flows_mod, tmp_path, monkeypatch, base, "REF-99")
        key = flows_mod.flow_key(spec.goal, spec.start_url, spec.scope)
        meta = flows_mod._load_meta(cache, key)

        # Make the confirm text present from the very first paint: now it cannot TRANSITION, so the run
        # can never tell a landed write from a signal that was already on the page.
        monkeypatch.setattr(spec, "mutate",
                            flows_mod.MutateSpec(confirm_text_contains="Invoice"))
        res = await flows_mod._attempt_replay(spec, _extract_router("REF-99"), cache, key, meta, True, cached_flow=cache.get(key))
        ok, kind, landed = res[0], res[3], res[4]

        assert ok is False and kind == "write_unverified", (
            f"expected the already-true confirm to come back as `write_unverified`; got ok={ok} "
            f"kind={kind!r} — the rest of this assertion is then about the wrong case")
        assert landed is False, (
            "a write whose confirm was ALREADY TRUE before it ran was marked landed. Nothing on that "
            "page distinguishes a commit from a stale banner, so the ledger would record a row that may "
            "never have paid — and a resume would SKIP it. That is a silently LOST write, which is worse "
            "than the double-submit this slice exists to close.")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_landed_write_is_never_handed_the_auth_refresh_retry() -> None:
    """The SIBLING GUARD this finding's own two neighbours already have.

    `replay()` hard-stops `write_unverified` and `write_unreadable` BEFORE `retry_ok` is computed, and
    says why in as many words: "so this can enter neither the auth-refresh retry nor the relearn path —
    both would re-fire a committed write." `shape` is the THIRD landed kind, carrying strictly stronger
    evidence than `write_unreadable`, and it had no such stop — it fell straight through to
    `_auth_retry_allowed`, which for a declared single write with a precheck returns True. The retry
    re-runs the flow from `start_url` and re-fires the payment; the only thing standing in the way is the
    backend honouring `Idempotency-Key`, which is NOT this project's posture for the other two.

    So the evidence belongs IN the retry predicate: a run whose write is known to have committed is never
    re-run, whatever failure surfaced afterwards. Keyed off `landed` rather than off `kind`, so a future
    landed failure inherits the stop — the same positional discipline as the arming itself."""
    from ultracua import flows as flows_mod

    M = flows_mod.MutateSpec
    spec = flows_mod.FlowSpec(
        name="p", goal="g", start_url="http://x.invalid/", headless=True,
        login=flows_mod.LoginSpec(url="http://x.invalid/login"),
        mutate=M(confirm_text_contains="ok", precheck_text_contains="done"))
    cached = CachedFlow(key="k", goal="g", start_url="http://x.invalid/", created_ts=1e9,
                        steps=[CachedStep(intent="pay", action="click", mutating=True, precond_scope="",
                                          precond_fingerprint="",
                                          locator=LocatorSpec(tag="button", role="button", name="Pay"))])

    # The control: with no landed evidence this flow IS retryable — a declared single write whose
    # whole-flow precheck can detect that the commit already landed. Without this the assertion below is
    # satisfied by never retrying anything.
    allowed, _why = flows_mod._auth_retry_allowed(spec, cached, auth_refresh=True, parameterizing=False,
                                                  landed=False)
    assert allowed is True, "the control arm lost its expired-session recovery; the test below is empty"

    allowed, why = flows_mod._auth_retry_allowed(spec, cached, auth_refresh=True, parameterizing=False,
                                                 landed=True)
    assert allowed is False, (
        "the write is KNOWN to have committed — the confirm transition was observed — and the flow was "
        "still handed the auth-refresh retry, which re-runs it from start_url and re-fires the payment. "
        "`write_unverified` and `write_unreadable` are both hard-stopped before this point for exactly "
        "that reason; `shape` is the third landed kind and carries stronger evidence than either.")
    assert "landed" in why or "already" in why or "committed" in why, (
        f"refused, but the operator is not told the write already went through: {why}")


def test_every_failure_return_in_attempt_replay_goes_through_the_arming_helper() -> None:
    """THE STRUCTURAL GUARD, and the one that matters most.

    The behavioural test above covers the failure returns that exist TODAY. But R3.3 was never about the
    returns that existed when R8 was written — it is about the one that was ADDED below the evidence
    point afterwards and never armed. A test that enumerates today's kinds cannot fail for tomorrow's.

    So: parse `_attempt_replay` and require that every `return` in it is either the single success return
    or a call to `_fail(...)`. A raw `return False, None, …` tuple is what silently drops the evidence,
    and this makes writing one a red test rather than a round-5 finding. If you need a new failure exit,
    route it through `_fail` — that is the whole mechanism, and it costs nothing.

    MEASURED — this guard covers something the behavioural tests provably cannot. Mutation: reinstate one
    raw tuple return that STILL carries `landed` correctly. Behaviour is unchanged, both tests above stay
    green, and only this test fails. That is the case worth catching: a hand-rolled return that happens
    to be right today, and is wrong the moment it is copied to a new exit or the tuple shape changes."""
    import ast
    import pathlib

    # Parse the FILE, not `inspect.getsource` — real line numbers in the failure message, and no
    # re-indentation games.
    path = pathlib.Path(__file__).parents[1] / "src" / "ultracua" / "flows.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    # BOTH HALVES since 1.5 split them. `_attempt_replay` is now a guard whose only job is to append a
    # `raised` fact if anything inside throws; every failure return lives in `_attempt_replay_body`.
    #
    # This guard CAUGHT that split, which is the point worth recording: it is the THIRD structural
    # scan in this repo to be silently disarmed by a function body moving (the exit-set ratchet and
    # `test_boundary_truth`'s `_record_run` count were the other two, in the same slice). A scan that
    # names ONE function asserts a negative about a body that can walk away from it. Requiring both
    # names, and failing if either is missing, is what makes the rename loud instead of quiet.
    wanted = ("_attempt_replay", "_attempt_replay_body")
    fns = [n for n in ast.walk(tree)
           if isinstance(n, ast.AsyncFunctionDef) and n.name in wanted]
    assert {f.name for f in fns} == set(wanted), (
        f"expected {list(wanted)} in {path}, found {sorted(f.name for f in fns)} — this scan asserts a "
        f"NEGATIVE, so a rename or a split would make it pass while checking nothing")

    # `_fail`'s OWN return is the arming mechanism, not a bypass of it — so exclude every return that
    # belongs to a nested function rather than to `_attempt_replay` itself.
    nested_returns = {id(r)
                      for fn in fns
                      for inner in ast.walk(fn)
                      if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner not in fns
                      for r in ast.walk(inner) if isinstance(r, ast.Return)}

    offenders = []
    for node in [n for fn in fns for n in ast.walk(fn)]:
        if not isinstance(node, ast.Return) or node.value is None or id(node) in nested_returns:
            continue
        # The one legitimate non-_fail return is the success tuple, which must still carry `landed`.
        if isinstance(node.value, ast.Tuple):
            elts = node.value.elts
            is_success = (len(elts) == 6 and isinstance(elts[0], ast.Constant) and elts[0].value is True                          and [getattr(e, "id", None) for e in elts[-2:]] == ["landed", "committed"])
            if not is_success:
                offenders.append(f"line {node.lineno}: raw tuple return, not `_fail(...)`")
            continue
        if isinstance(node.value, ast.Call) and getattr(node.value.func, "id", None) == "_fail":
            continue
        # The guard's ONE return: it delegates to the body and re-raises on the way out. It carries no
        # evidence of its own because it carries the body's, unchanged.
        if (isinstance(node.value, ast.Await) and isinstance(node.value.value, ast.Call)
                and getattr(node.value.value.func, "id", None) == "_attempt_replay_body"):
            continue
        offenders.append(f"line {node.lineno}: return is neither `_fail(...)` nor the success tuple")

    assert not offenders, (
        "a return in `_attempt_replay` bypasses `_fail`, so it does not carry the write-landed evidence. "
        "That is R3.3 exactly: a failure exit added below the confirm-transition point without arming "
        "it, so a committed write reports `landed=False`, no ledger line is written, and the operator is "
        "told to resume a row that already paid:\n  " + "\n  ".join(offenders))


def test_the_two_ledger_ARMS_read_the_exception_directly_and_nothing_else() -> None:
    """1.4b's critic clause, made structural: the two arming reads stay BYTE-IDENTICAL.

    1.4b introduced `outcome_of(exc) -> Outcome` and `_row_write_evidence(...)`, and both sit within
    three lines of these guards. The report's answer is a TRI-STATE and the ledger's arm is a
    TWO-STATE token where a maybe reads as a no — but `None` is falsy, so routing the arm through the
    report would "work" until somebody wrote `is not False`, at which point a row that was never paid
    is skipped forever. `Outcome` deliberately has no `landed` attribute for the same reason; this is
    the other half, on the two statements that actually write the durable file.

    Named files, and a MISSING one is a failure rather than a smaller scan — a negative asserted about
    a body that can walk away is how three scans were disarmed at once in 1.5.
    """
    import ast
    import pathlib

    WANT = {
        "src/ultracua/flows.py": 'getattr(exc, "landed", False)',
        "src/ultracua/mcpserver/server.py": 'getattr(exc, "landed", False)',
    }
    root = pathlib.Path(__file__).parents[1]
    for rel, needle in WANT.items():
        path = root / rel
        assert path.exists(), f"{rel} is gone — this scan asserts a negative about it"
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))

        # THE ARM'S OWN CONDITION, BY AST — never a substring of the file. The first draft asserted
        # `needle in text` and a mutation that rewrote the arm to route through the report seam
        # SURVIVED, because the same text appears in a COMMENT describing the arm. That is the
        # grep-counts-prose failure `scripts/ratchets.py` was built to escape, re-made in a pin.
        guards = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            body = ast.unparse(node).replace("'", '"')
            if "ledger.record(" in body:
                guards.append(ast.unparse(node.test).replace("'", '"'))
        assert guards, f"{rel}: no `if` guards a `ledger.record(...)` call — the arming shape is gone"
        armed_by_exc = [g for g in guards if needle.replace("'", '"') in g]
        assert armed_by_exc, (
            f"{rel} no longer arms the ledger by reading `{needle}` in the guard itself. Guards found: "
            f"{guards}. If the arm reads a REPORT value, `None` is falsy and the row silently stops "
            f"being recorded — or, worse, an `is not False` records one that was never paid.")
        for g in guards:
            assert "_row_write_evidence" not in g and "outcome_of" not in g, (
                f"{rel}: a `ledger.record(...)` guard now reads 1.4b's report seam — {g}")
        arms = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "record"
                and getattr(getattr(n.func, "value", None), "id", None) == "ledger"]
        assert arms, f"{rel} no longer calls `ledger.record(...)` — the arming site is gone"
        # There are TWO shapes and they are not interchangeable: the SUCCESS path records the status
        # the page reported, the FAILURE path records the exception's own code. The first draft of this
        # cell demanded the second of BOTH and went red on the success path — which is the cell finding
        # its own over-reach rather than a defect.
        shapes = {ast.unparse(c).replace("'", '"') for c in arms}
        failure_arms = {s for s in shapes if 'getattr(exc, "code", "landed")' in s}
        assert failure_arms, (
            f"{rel}: no `ledger.record(...)` takes the exception's own code directly. Shapes found: "
            f"{sorted(shapes)}. The durable audit trail and the caller must name the same failure, and "
            f"a REPORT value here would write a tri-state into a two-state audit line.")
        for s in shapes:
            assert "_row_write_evidence" not in s and "outcome_of" not in s, (
                f"{rel}: `ledger.record(...)` now takes a value derived by 1.4b's report seam — {s}. "
                f"The arm reads the exception; the report projects the record. Two questions.")

    # The report seam must NOT have leaked a `landed` attribute for the arms to reach for.
    from ultracua.flows import Outcome
    assert not hasattr(Outcome("c", False, False), "landed"), (
        "`Outcome` grew a `landed` attribute. It exists WITHOUT one so that the tri-state report value "
        "cannot reach an arming guard even by accident; `armed` is the same fact under a name that "
        "does not invite the collapse.")
