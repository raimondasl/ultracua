"""R3.5 — an UNDECLARED write takes the auth-refresh retry that a declared one is refused, and re-fires.

`is_write_flow(spec, cached)` exists because this predicate kept being re-derived by hand: a flow is a
WRITE if it is DECLARED one (`spec.mutate`) **or** its cached steps in fact mutate. Round 2 found a fourth
hand-derivation (`run_all`); round 3 found a fifth, inline in the funnel every replay goes through:

    retry_ok = auth_refresh and spec.login is not None and (
        not is_mutate or (spec.mutate.has_precheck() and not spec.mutate.is_multiwrite()))

with `is_mutate = spec.mutate is not None`. For an undeclared write — `spec.mutate is None`, but a cached
step carries `mutating=True` because the wire promotion saw it POST — that reads False, so `retry_ok`
becomes True. The retry re-runs the flow FROM `start_url`, re-actuating the commit. Measured on the
pre-fix source: two POSTs, byte-identical Idempotency-Keys, on an endpoint that never asked for the
header. A DECLARED write refuses that same retry in as many words ("a blind retry would double-submit").

WHAT THIS SLICE DELIBERATELY DOES **NOT** DO, because an adversarial pass measured the cost:

The obvious fix — refuse an undeclared write outright at the shared `_preflight_row` gate, which is what
`run_batch` and `run_all` already do and what the plan prescribed — was built, went green, and is wrong.
`step.mutating` OVER-counts badly. `safety.classify_mutation` falls back to an unbounded substring match,
so ordinary read navigations cache as mutating:

    click "Payment history"   -> True   ("pay")        click "Show borders"     -> True   ("order")
    click "the Sent folder"   -> True   ("send")       click "Deleted items"    -> True   ("delete")

8 of 10 sampled read navigations classified True. Those flows cache, and they replay fine today. A
flow-level refusal breaks all of them — and NEITHER remedy such a refusal can name actually works for
that population: `flow record` re-derives the same verdict through the same classifier *and deletes the
cached recipe*, and declaring `mutate` demands a write-completion confirm signal that a read cannot
produce (plus it disables the H9 read-side contract/magnitude rails and changes `replay()`'s return
type). That is the 0.74.0 over-refusal regression one population over — the one CLAUDE.md records as
having actually shipped.

So the guard is on the RETRY, not on the flow — and on `on_drift='relearn'`, which re-authors and
therefore re-performs the write. What that costs the misclassified population, stated exactly (an earlier
draft of this docstring said "nothing but a loud failure on an expired session", which the same commit
falsified): they lose the auth-refresh retry, and they lose `relearn`. What they keep is the thing they
actually run — a plain `on_drift='raise'` replay — plus a remedy that WORKS, since `learn()` does not
funnel through pre-flight. That last point is the whole difference from the rejected flow-level refusal,
whose two named remedies both failed.

Refusing the flow outright is decision D0, now BLOCKED on making the write signal precise enough to tell
the two populations apart — see `docs/correctness-plan.md`.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key
from ultracua.locators import LocatorSpec


class _Site:
    """Serves a two-control page and counts POSTs. `drift` removes the second control, so the replay's
    LAST step fails after the commit has already fired."""

    def __init__(self) -> None:
        self.posts: list[str] = []      # Idempotency-Key per POST ("" when absent)
        self.drift = False

    def page(self) -> str:
        second = "" if self.drift else "<button id='n' type='button'>Next</button>"
        return ("<h1>Panel</h1>"
                "<button id='c' type='button' "
                "onclick=\"fetch('/commit',{method:'POST',body:'x=1'})\">Place order</button>"
                + second +
                "<script>document.querySelectorAll('button').forEach(function(b){"
                "b.addEventListener('click',function(){"
                "document.querySelector('h1').textContent='clicked '+b.id;});});</script>")

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
                self._send(site.page())

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.posts.append(self.headers.get("Idempotency-Key") or "")
                self._send("<h1>committed</h1>")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _write_steps() -> list[CachedStep]:
    """Step 0 WRITES — `mutating=True` — which is what the learn path's wire promotion produces for a
    formless `fetch` POST. Step 1 is the one the drift removes."""
    return [
        CachedStep(intent="place the order", action="click", mutating=True,
                   precond_scope="", precond_fingerprint="",
                   locator=LocatorSpec(tag="button", role="button", name="Place order")),
        CachedStep(intent="go next", action="click", mutating=False,
                   precond_fingerprint="",
                   locator=LocatorSpec(tag="button", role="button", name="Next")),
    ]


def _spec_and_cache(flows_mod, base: str, tmp_path: Path, *, declared):
    """A spec + a primed cache whose first cached step MUTATES. `declared=None` -> the UNDECLARED write
    (the defect population): the wire saw a POST, the human declared nothing."""
    spec = flows_mod.FlowSpec(
        name="p", goal="work the panel", start_url=f"{base}/", headless=True,
        mutate=declared, login=flows_mod.LoginSpec(url=f"{base}/login"))
    cache = FlowCache(root=tmp_path / "c")
    cache.put(CachedFlow(key=flow_key(spec.goal, spec.start_url, spec.scope), goal=spec.goal,
                         start_url=f"{base}/", created_ts=1e9, steps=_write_steps()))
    return spec, cache


async def test_an_undeclared_write_is_not_re_actuated_by_the_auth_refresh_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE defect, end to end. The commit fires, a LATER step drifts, and on the pre-fix source the
    auth-refresh retry replays the flow from the start — firing the commit a second time under a
    byte-identical Idempotency-Key. The flow must still RUN (it is not refused); it must simply not be
    re-run after the refresh."""
    from ultracua import flows as flows_mod

    site = _Site()
    httpd, base = site.serve()
    try:
        spec, cache = _spec_and_cache(flows_mod, base, tmp_path, declared=None)
        # Approve for REAL rather than synthesising a FlowMeta: the approval is bound to the recipe's
        # steps_hash, and a hand-built `FlowMeta(approved=True)` is refused by the stale-approval gate
        # before anything actuates — which would make this test pass for the wrong reason. It did, once.
        monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
        flows_mod.approve(spec, cache=cache)

        refreshed = {"n": 0}

        async def _fake_refresh(spec, **kw):
            refreshed["n"] += 1            # cookies "refreshed"; the retry then re-runs the whole flow

        monkeypatch.setattr(flows_mod, "refresh_auth", _fake_refresh)

        site.drift = True                  # the LAST step's control is gone -> the replay drifts
        with pytest.raises(flows_mod.FlowReplayError) as excinfo:
            await flows_mod.replay(spec, cache=cache, require_approved=False)

        assert site.posts, "the fixture never committed, so this test would prove nothing"
        assert len(site.posts) == 1, (
            f"the commit fired {len(site.posts)} times (keys={site.posts}, auth refreshes="
            f"{refreshed['n']}) — an UNDECLARED write took the auth-refresh retry and re-actuated, under "
            f"a byte-identical Idempotency-Key. A declared write refuses this retry because a blind "
            f"re-run double-submits; the undeclared one has no confirm barrier at all, so replay cannot "
            f"even tell whether the first attempt landed.")
        assert refreshed["n"] == 0, (
            f"auth was refreshed {refreshed['n']} time(s) — the refresh itself is harmless, but reaching "
            f"it means the retry gate let a writing flow through, and the re-run is what double-submits")
        assert "not retrying" in str(excinfo.value), (
            f"the operator is told the replay drifted but never that a recorded step is marked as "
            f"WRITING, which is the whole reason no retry was attempted: {excinfo.value}")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_declared_write_on_the_same_fixture_still_commits_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONTROL — the "must remain learnable" half, without which the property above is satisfied by
    refusing everything (a regression that has actually shipped here). Same fixture, same mutating step;
    the only difference is that the human DECLARED the write and gave it a confirm barrier. It must still
    run, still commit, and commit exactly once — carrying a key. Expected GREEN on both sides of the fix:
    it is not a regression test, it is what gives the assertions above their meaning."""
    from ultracua import flows as flows_mod

    site = _Site()
    httpd, base = site.serve()
    try:
        spec, cache = _spec_and_cache(
            flows_mod, base, tmp_path,
            declared=flows_mod.MutateSpec(confirm_text_contains="clicked n"))
        monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
        flows_mod.approve(spec, cache=cache)

        out = await flows_mod.replay(spec, cache=cache, require_approved=False)

        assert out["status"] == "confirmed", out
        assert len(site.posts) == 1, (
            f"a DECLARED write on this fixture committed {len(site.posts)} times (keys={site.posts}) — "
            f"the retry assertions above are only meaningful if this arm still writes exactly once")
        assert all(site.posts), (
            f"the commit rode NO Idempotency-Key ({site.posts}) — this file's whole subject is key-based "
            f"double-submit safety, so the control must not accept an unkeyed write")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_relearn_cannot_re_perform_an_undeclared_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE SIBLING TWENTY LINES BELOW THE ONE R3.5 NAMES — and strictly worse than it.

    `on_drift='relearn'` re-authors the flow, which RE-RUNS it from `start_url` and therefore re-performs
    the commit. It is refused for a DECLARED write ("re-authoring would re-perform the write") and for any
    APPROVED flow. An UNAPPROVED, UNDECLARED write satisfies neither, so it fell straight through both.

    Worse than the retry arm in three ways, all measured: `replay()` returns NORMALLY rather than raising
    (the `_ok` envelope keys off the declaration, so an undeclared write returns bare data), health
    records a SUCCESS, and the cached recipe is silently rewritten by the repair. So the operator sees a
    green run, a green health record, and a commit that fired more than once. That is inviolable #2 and
    #3 in the same call.

    The register scoped this arm OUT of R3.5 on the grounds that relearn "is refused for any APPROVED flow
    regardless of declaration" — true, and the wrong half: the population that can run undeclared is the
    UNAPPROVED one."""
    from ultracua import flows as flows_mod
    from ultracua.providers.scripted import ScriptedProvider

    site = _Site()
    httpd, base = site.serve()
    try:
        spec, cache = _spec_and_cache(flows_mod, base, tmp_path, declared=None)
        monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
        site.drift = True          # deliberately NOT approved — that is the whole population

        # Both provider and router are passed so `_router()` is never consulted: this stays key-less.
        # The router is never used (spec.extract is None); it exists only to keep the relearn branch from
        # building one.
        with pytest.raises(flows_mod.FlowReplayError) as excinfo:
            await flows_mod.replay(
                spec, cache=cache, require_approved=False, on_drift="relearn",
                provider=ScriptedProvider([{"action": "done", "intent": "done"}]), router=object())

        assert site.posts == [], (
            f"the commit fired {len(site.posts)} time(s) (keys={site.posts}) — `on_drift='relearn'` "
            f"re-authored an UNDECLARED write, which re-performs it. Refusing the auth-refresh retry "
            f"while leaving this open closes the narrower of the two doors: unlike the retry, this path "
            f"returns NORMALLY and records a success, so nothing tells the operator it happened.")
        msg = str(excinfo.value)
        assert "relearn" in msg, (
            f"refused, but not as a relearn refusal — the operator must be told which option to drop: "
            f"{msg}")
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- the retry gate itself, as a property (0-LLM, no browser, no network) -----------------------------
# `_auth_retry_allowed` is the single definition. Testing it directly is what makes this a guard on the
# MECHANISM rather than one inline expression that drifts from its four siblings — the shape that produced
# this finding. It is a pure function, so the whole cross-product is free.

def _flow(*, mutating: bool):
    return CachedFlow(key="k", goal="g", start_url="http://x.invalid/", created_ts=1e9,
                      steps=[CachedStep(intent="do it", action="click", mutating=mutating,
                                        precond_fingerprint="",
                                        locator=LocatorSpec(tag="button", role="button", name="Go"))])


def _cases(flows_mod):
    """(label, spec-kwargs, cached-flow, may-retry). The two axes that decide it: does the flow WRITE (by
    declaration or by the wire), and can a re-run be made safe (a whole-flow precheck on a single write)."""
    M = flows_mod.MutateSpec
    login = flows_mod.LoginSpec(url="http://x.invalid/login")
    sc = flows_mod.StepConfirm
    return [
        ("read",                       dict(login=login), _flow(mutating=False), True),
        ("undeclared write",           dict(login=login), _flow(mutating=True), False),
        ("declared, no precheck",      dict(login=login, mutate=M(confirm_text_contains="ok")),
         _flow(mutating=True), False),
        ("declared + precheck",        dict(login=login, mutate=M(confirm_text_contains="ok",
                                                                  precheck_text_contains="done")),
         _flow(mutating=True), True),
        ("declared + precheck, multi", dict(login=login, mutate=M(
            confirm_text_contains="ok", precheck_text_contains="done",
            step_confirms=[sc(text_contains="a"), sc(text_contains="b")])), _flow(mutating=True), False),
        # A DECLARED write whose cached steps do not mutate is still a write — the declaration alone
        # settles it. (Such a flow is refused earlier by UnkeyedWriteError, but this predicate must not
        # depend on that: see the docstring on standalone correctness.)
        ("declared, nothing mutating", dict(login=login, mutate=M(confirm_text_contains="ok")),
         _flow(mutating=False), False),
        ("no cached flow at all",      dict(login=login), None, True),
        # TWO MUTATING CACHED STEPS, ONE declared barrier. `is_multiwrite()` asks the DECLARATION
        # (`len(step_confirms) > 1`), and `record()` explicitly permits a multi-write flow with no
        # `step_confirms` at all — so this reads as a single write and was granted the retry. The
        # whole-flow precheck probes only the LAST write's marker, so after write #1 lands and the flow
        # drifts, the precheck says "not done" and the re-run re-fires write #1. That is verbatim the harm
        # the multiwrite exclusion exists to prevent, reached by asking the declaration instead of the
        # recipe — R3.5's own conflation, one field over.
        ("declared + precheck, TWO mutating steps",
         dict(login=login, mutate=M(confirm_text_contains="ok", precheck_text_contains="done")),
         CachedFlow(key="k", goal="g", start_url="http://x.invalid/", created_ts=1e9,
                    steps=[CachedStep(intent="write one", action="click", mutating=True,
                                      precond_scope="", precond_fingerprint="",
                                      locator=LocatorSpec(tag="button", role="button", name="One")),
                           CachedStep(intent="write two", action="click", mutating=True,
                                      precond_scope="", precond_fingerprint="",
                                      locator=LocatorSpec(tag="button", role="button", name="Two"))]),
         False),
    ]


def test_the_auth_refresh_retry_is_refused_for_anything_that_writes() -> None:
    """The property, over the cross-product: a re-run from `start_url` re-actuates, so it is allowed only
    when re-actuating is safe — the flow does not write, or it is a DECLARED single write whose whole-flow
    precheck can detect that the commit already landed.

    The `undeclared write` cell is R3.5. The `read` and `declared + precheck` cells are the liveness half:
    without them the property is satisfied by never retrying anything, which would silently remove the
    expired-session recovery this project advertises."""
    from ultracua import flows as flows_mod

    wrong = []
    for label, kw, cached, expected in _cases(flows_mod):
        spec = flows_mod.FlowSpec(name="p", goal="g", start_url="http://x.invalid/", headless=True, **kw)
        got, why = flows_mod._auth_retry_allowed(spec, cached, auth_refresh=True, parameterizing=False, landed=False)
        if got is not expected:
            wrong.append(f"{label}: may_retry={got}, expected {expected}")
        elif got is False and not why:
            # A refusal with no reason is how the operator ends up with a bare "replay drifted". The
            # decision and its explanation come from one place precisely so this cannot drift apart.
            wrong.append(f"{label}: refused with NO reason for the operator")
    assert not wrong, (
        "the auth-refresh retry re-runs the whole flow from start_url, so allowing it for a flow that "
        "WRITES is a double-submit and refusing it for one that does not silently drops expired-session "
        "recovery:\n  " + "\n  ".join(wrong))


def test_a_declined_retry_names_the_reason_that_actually_applies() -> None:
    """The reason must match the ARM that fired, not a plausible-sounding neighbour.

    This is the defect the tuple return exists to prevent, and it shipped once: when the gate grew the
    recipe-count arm, the caller's hand-derived `elif` chain did not, so a flow refused for having TWO
    mutating steps was told it was "without an idempotency precheck" — which it had — and handed
    `add mutate.precheck_*`, a no-op. The only signal the operator gets, pointing in a circle."""
    from ultracua import flows as flows_mod

    M, login = flows_mod.MutateSpec, flows_mod.LoginSpec(url="http://x.invalid/login")
    two_writes = CachedFlow(
        key="k", goal="g", start_url="http://x.invalid/", created_ts=1e9,
        steps=[CachedStep(intent=f"write {n}", action="click", mutating=True, precond_scope="",
                          precond_fingerprint="",
                          locator=LocatorSpec(tag="button", role="button", name=n))
               for n in ("One", "Two")])
    spec = flows_mod.FlowSpec(
        name="p", goal="g", start_url="http://x.invalid/", headless=True, login=login,
        mutate=M(confirm_text_contains="ok", precheck_text_contains="done"))

    allowed, why = flows_mod._auth_retry_allowed(spec, two_writes, auth_refresh=True, parameterizing=False, landed=False)
    assert allowed is False
    assert "2 recorded steps are marked as WRITING" in why, (
        f"the flow HAS a precheck and was refused for having two mutating steps, so the reason must name "
        f"THAT — blaming the missing precheck prescribes `add mutate.precheck_*`, which this flow already "
        f"has: a false cause with a no-op remedy, pointing the operator in a circle: {why}")

    # ...and a PARAMETERIZED write must keep its own reason even on the same two-step recipe, because the
    # count message explains it in terms of a precheck it is FORBIDDEN to have (pre-flight refuses the
    # combination as row-blind) and drops the guidance that does apply — the row-keyed Idempotency-Key.
    # Mutation-checked: with `parameterizing` hard-coded to False, this is the assertion that fails.
    allowed, why = flows_mod._auth_retry_allowed(spec, two_writes, auth_refresh=True, parameterizing=True, landed=False)
    assert allowed is False
    assert "row-keyed" in why and "Idempotency-Key dedupes" in why, (
        f"a parameterized write was refused with a reason that does not mention the one thing that makes "
        f"its manual re-run safe. If `parameterizing` stopped being threaded from `replay()`, this is "
        f"what breaks — and nothing else in the suite would notice: {why}")


def test_the_retry_is_refused_outright_without_auth_refresh_or_a_login() -> None:
    """The two preconditions, kept in the same predicate so no caller can forget one."""
    from ultracua import flows as flows_mod

    read = _flow(mutating=False)
    with_login = flows_mod.FlowSpec(name="p", goal="g", start_url="http://x.invalid/", headless=True,
                                    login=flows_mod.LoginSpec(url="http://x.invalid/login"))
    no_login = flows_mod.FlowSpec(name="p", goal="g", start_url="http://x.invalid/", headless=True)
    assert flows_mod._auth_retry_allowed(with_login, read, auth_refresh=True, parameterizing=False, landed=False)[0] is True
    assert flows_mod._auth_retry_allowed(with_login, read, auth_refresh=False, parameterizing=False, landed=False)[0] is False
    assert flows_mod._auth_retry_allowed(no_login, read, auth_refresh=True, parameterizing=False, landed=False)[0] is False


def test_a_misclassified_read_is_not_refused_anywhere(tmp_path: Path) -> None:
    """THE OVER-REFUSAL CELL — the population a flow-level refusal would have broken, and the reason
    decision D0 is blocked rather than shipped.

    `safety.classify_mutation` falls back to an unbounded substring match, so ordinary read navigations
    cache with `mutating=True` ("Payment history" contains "pay"; "Show borders" contains "order"). Those
    flows work today, and an ORDINARY replay of them must keep working. It asserts the premise too, so the
    day the classifier is tightened this test tells you the population has changed rather than passing
    vacuously.

    The name is scoped deliberately: "not refused" means the DEFAULT `on_drift='raise'` replay, which is
    the path these flows actually run on. It is NOT a claim that nothing refuses them — `on_drift=
    'relearn'` does, and the last assertion here pins that cost rather than leaving the docstring to
    overclaim, which an earlier draft of it did."""
    from ultracua import flows as flows_mod
    from ultracua.safety import classify_mutation

    misread = [("click Payment history", "Payment history"), ("show borders", "Show borders"),
               ("open the Sent folder", "Sender")]
    assert all(classify_mutation("click", i, n, {}) for i, n in misread), (
        "the premise no longer holds: these ordinary READ navigations no longer classify as mutating. "
        "That is good news for the classifier, but it means this cell is no longer guarding the "
        "population it was written for — re-derive it from the current keyword list.")

    spec = flows_mod.FlowSpec(name="p", goal="g", start_url="http://x.invalid/", headless=True)
    cached = CachedFlow(key="k", goal="g", start_url="http://x.invalid/", created_ts=1e9,
                        steps=[CachedStep(intent=i, action="click", mutating=True, precond_scope="",
                                          precond_fingerprint="",
                                          locator=LocatorSpec(tag="a", role="link", name=n))
                               for i, n in misread])
    # Unapproved and undeclared, which is the ordinary state of a read flow: it must pre-flight clean.
    assert flows_mod._preflight_row(spec, None, meta=flows_mod.FlowMeta(), cached_flow=cached) == {}
    # ...and it keeps its expired-session recovery: the retry gate only declines a flow the wire or the
    # declaration says WRITES — which, for this population, is exactly what we are choosing not to trust
    # at flow level. It IS declined here, and that is the accepted cost, stated rather than hidden.
    assert flows_mod._auth_retry_allowed(spec, cached, auth_refresh=True, parameterizing=False, landed=False)[0] is False, (
        "expected the misclassified read to lose its auth-refresh retry — that is the accepted, bounded "
        "cost of keying the retry gate off `is_write_flow`. If this changed, the trade changed too.")
    # ...and the OTHER half of that cost, which the docstring above used to omit: the same over-count
    # drives the relearn gate, so this flow is refused `on_drift='relearn'` outright. Asserted here so
    # the price of decision D0's deferral is written down where it is paid, not only in the register.
    with pytest.raises(flows_mod.FlowReplayError) as ei:
        flows_mod._preflight_row(spec, None, meta=flows_mod.FlowMeta(), cached_flow=cached,
                                 on_drift="relearn")
    assert "relearn" in str(ei.value)


# Names whose semantics ARE the declaration, and which may therefore hold this predicate. `is_write_flow`
# is the wire-OR-declaration predicate and is deliberately absent — nothing may re-derive it by hand.
_ALLOWED_DECLARATION_NAMES = {"declares_write", "declared_write"}


def _mutate_predicate_bindings(source: str, filename: str = "<test>") -> list[str]:
    """Every place `source` binds a `<x>.mutate` PRESENCE TEST to a name outside the allowlist.

    ONE matcher, used by both the scan over src/ and its positive control — an earlier draft had the
    control re-implement it, which is how a control silently stops matching the thing it certifies. It
    matches the predicate, not a spelling: `is`/`is not`/`==`/`!=` against None, `bool(...)`, `not`
    wrapping any of them (which the first draft missed, and the control caught on its first run), and the
    predicate buried inside a larger boolean expression — which is the form R3.5 ACTUALLY had:

        retry_ok = auth_refresh and spec.login is not None and (not is_mutate or ...)

    KNOWN LIMIT, stated rather than papered over: this sees BINDINGS only. The same predicate used inline
    in a `return`, a dict literal or a conditional expression is not caught, because `spec.mutate is not
    None` is legitimate in ~20 places where the question really is about the declaration, and banning it
    outright would need an allowlist longer than the rule. So this narrows the blast radius of the next
    transcription; it does not make the raw predicate inexpressible. Do not describe it as doing so."""
    import ast

    def presence_test(node) -> bool:
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return presence_test(node.operand)          # `not spec.mutate is None`
        if isinstance(node, ast.BoolOp):                # `a and (spec.mutate is None or b)` — the R3.5 form
            return any(presence_test(v) for v in node.values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, op, right = node.left, node.ops[0], node.comparators[0]
            return (isinstance(left, ast.Attribute) and left.attr == "mutate"
                    and isinstance(right, ast.Constant) and right.value is None
                    and isinstance(op, (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)))
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "bool":
            return bool(node.args) and isinstance(node.args[0], ast.Attribute) \
                and node.args[0].attr == "mutate"
        return False

    out = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            continue
        raw = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in raw if isinstance(t, ast.Name)]
        if not presence_test(node.value):
            continue
        out += [f"{filename}:{node.lineno}: {n} = <spec.mutate presence test>"
                for n in names if n not in _ALLOWED_DECLARATION_NAMES]
    return out


def test_no_raw_write_predicate_stands_in_for_is_write_flow() -> None:
    """Make the SIXTH transcription a red test instead of a round-5 finding — spelling-independently.

    An earlier draft of this test was a regex over three literal variable names. An adversarial pass
    showed it was theatre: it missed `writes = spec.mutate is not None`, `bool(spec.mutate)`,
    `spec.mutate != None`, an annotated assignment, a walrus, a tuple unpack and every inline form —
    including the inline `retry_ok = … not is_mutate …` expression that WAS R3.5. Worse, the fix's own
    rename made `declares_write`/`writes` the house style, i.e. precisely the spellings it was blind to.

    This walks the AST instead, matching the PREDICATE (`spec.mutate is not None` / `is None` / `!=` /
    `not ...` / `bool(...)`, including buried inside a larger boolean — R3.5's actual form) wherever it is
    bound to a name, and allows it only where the question really is "did the human DECLARE a write". The
    allowlist is by NAME, so a new binding must be justified by what it is called rather than by being
    spelled unusually.

    It catches BINDINGS, not every inline use — see `_mutate_predicate_bindings` for why, and do not
    describe it as making the predicate inexpressible. It narrows the next transcription's blast radius;
    it does not eliminate it."""
    import pathlib

    src = pathlib.Path(__file__).parents[1] / "src" / "ultracua"
    files = sorted(src.rglob("*.py"))
    assert files, f"scanned ZERO files under {src} — the scan path is wrong and this test proves nothing"

    offenders = []
    for path in files:
        offenders += _mutate_predicate_bindings(path.read_text(encoding="utf-8"),
                                                str(path.relative_to(src)))
    assert not offenders, (
        f"a raw write-predicate is bound to a name that stands in for `is_write_flow(spec, cached)`, "
        f"which MISSES an undeclared write (spec.mutate is None but a cached step mutates). Either use "
        f"`is_write_flow`, or name it for the declaration and add it to the allowlist "
        f"({sorted(_ALLOWED_DECLARATION_NAMES)}):\n  " + "\n  ".join(offenders))


def test_the_predicate_scanner_can_actually_fail() -> None:
    """The positive control the previous draft lacked. The scan above asserts a NEGATIVE over a directory,
    so without this it stays green through any typo in the matcher, the walk, or the allowlist — and
    through the far likelier failure of simply not matching a new spelling. It earned its place
    immediately: it caught `not spec.mutate is None` slipping past the first version of the matcher,
    which parses as a `UnaryOp` wrapping the compare rather than a compare.

    It calls the SAME `_mutate_predicate_bindings` the scan uses. A control with its own copy of the
    logic certifies the copy, not the thing."""
    for evasion in ("is_mutate = spec.mutate is not None",
                    "writes = spec.mutate is not None",           # the fix's own house style
                    "is_mutate = not spec.mutate is None",
                    "is_mutate = spec.mutate != None",
                    "is_mutate = bool(spec.mutate)",
                    "is_mutate: bool = spec.mutate is not None",
                    "if (is_mutate := spec.mutate is not None): pass",
                    "is_mutate = self.spec.mutate is not None",
                    # R3.5's actual form: the predicate inlined into a larger boolean, never named.
                    "retry_ok = auth and spec.login is not None and (spec.mutate is None or ok)"):
        assert _mutate_predicate_bindings(evasion), f"the scanner does not catch: {evasion!r}"
    for legitimate in ("declares_write = spec.mutate is not None",
                       "declared_write = spec.mutate is not None",
                       "if spec.mutate is not None: pass",        # a bare guard, not a stand-in binding
                       "x = spec.extract is not None"):
        assert not _mutate_predicate_bindings(legitimate), f"the scanner FALSELY flags: {legitimate!r}"
