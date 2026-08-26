"""The login guard, and the phase boundary that attributes its failures (R4.97, R4.98, R4.99).

BROWSER-FREE ON PURPOSE. `assert_login_discriminates` is driven through a fake page whose only job
is to answer `locator(sel).count()`, so every direction of the guard runs in the fast tier — the
same reason `tests/_fake_engine.py` exists one module over. What it deliberately does NOT claim is
that any particular selector matches a real Gitea: that is a fact about a served page, it was
measured against a live container when the values were chosen, and `benchmarks/oracle_liveness.py`
is the pattern for re-checking it.
"""
from __future__ import annotations

import asyncio

import pytest

from benchmarks import corpus, outcomes, substrates as S
from benchmarks import scored_run as SR


class _FakeLocator:
    def __init__(self, n: int) -> None:
        self._n = n

    async def count(self) -> int:
        return self._n


class _FakePage:
    """Answers `locator(sel).count()` from a dict. Anything unlisted matches nothing."""

    def __init__(self, counts: dict) -> None:
        self.counts = counts
        self.asked: list = []

    def locator(self, sel: str):
        self.asked.append(sel)
        return _FakeLocator(self.counts.get(sel, 0))


class _FakeSession:
    def __init__(self, page) -> None:
        self.page = page
        self.goto_urls: list = []

    async def start(self):
        return self

    async def goto(self, url: str) -> None:
        self.goto_urls.append(url)

    async def close(self) -> None:
        pass


def _drive(monkeypatch, cfg, *, anon: dict, authed: dict, authenticate=None):
    """Run the guard with a scripted anonymous page and a scripted authenticated one."""
    pages = {"anon": _FakePage(anon), "authed": _FakePage(authed)}
    seen = {"launches": 0, "storage_states": []}

    def fake_session(*, headless, storage_state):
        seen["launches"] += 1
        seen["storage_states"].append(storage_state)
        return _FakeSession(pages["anon" if storage_state is None else "authed"])

    monkeypatch.setattr(SR, "BrowserSession", fake_session)
    calls = {"auth": 0}

    async def _auth():
        calls["auth"] += 1

    asyncio.run(SR.assert_login_discriminates(
        cfg, "http://sub.test", substrate="fake",
        authenticate=authenticate or _auth, storage_state="/tmp/auth.json"))
    return seen, calls, pages


CFG = dict(path="/login", submit_selector="button.go", success_selector=".inside")


def test_the_guard_interrogates_the_anonymous_page_and_then_authenticates(monkeypatch):
    """The happy path. It observes ONE world — the anonymous one — and then hands off.

    IT USED TO OBSERVE BOTH and require the marker PRESENT on a logged-in fetch of the login path.
    That half was removed at 0.130.0: Gitea redirects `/user/login` to `/`, **Odoo does not**, and
    the false refusal blocked the entire Odoo corpus at preflight. The affirmative check already
    exists in the product — `refresh_auth` raises `LoginFailedError` when `_login_succeeded` is
    False, evaluated on the page the submit LANDED on.
    """
    seen, calls, pages = _drive(monkeypatch, CFG,
                                anon={"button.go": 1, ".inside": 0},
                                authed={"button.go": 0, ".inside": 1})
    assert calls["auth"] == 1, "the guard must still authenticate — it is the caller's only login"
    assert seen["launches"] == 1, "one observation, of the ANONYMOUS page"
    assert seen["storage_states"] == [None], (
        "the observation must carry NO session; done with one it cannot tell the worlds apart, "
        "which is the whole of R4.98")
    assert ".inside" in pages["anon"].asked


def test_a_submit_selector_matching_nothing_is_refused_before_authenticating(monkeypatch):
    """R4.97, and the ORDER matters: refusing after the login wastes the thing it protects."""
    with pytest.raises(S.SubstrateNotReady) as e:
        _drive(monkeypatch, CFG, anon={"button.go": 0, ".inside": 0},
               authed={".inside": 1})
    assert "matches NOTHING" in str(e.value)
    assert "button.go" in str(e.value), "the refusal must name the selector to fix"
    assert "R4.97" in str(e.value), "a refusal names its remedy"


def test_a_success_selector_true_while_logged_out_is_refused(monkeypatch):
    """R4.98 — the defect that shipped. `.dashboard, #navbar` matched `#navbar` on the login page."""
    with pytest.raises(S.SubstrateNotReady) as e:
        _drive(monkeypatch, CFG, anon={"button.go": 1, ".inside": 1},
               authed={".inside": 1})
    msg = str(e.value)
    assert "while LOGGED OUT" in msg
    assert "anonymously" in msg
    assert "selector LIST matches if ANY" in msg, (
        "the message must name the mechanism, because the next person will write another list")


def test_the_product_owns_the_affirmative_check(monkeypatch):
    """WHAT REPLACED the removed half, asserted rather than assumed.

    The guard no longer re-fetches the login page with the session. That is only safe because
    `refresh_auth` raises when `_login_succeeded` is False — so this pins that the guard's job ends
    at "the selector discriminates" and the LOGIN is what proves it stuck. If `authenticate` raises,
    the guard must not swallow it.
    """
    async def boom():
        raise RuntimeError("LoginFailedError stands in for the product's own refusal")

    with pytest.raises(RuntimeError, match="LoginFailedError"):
        _drive(monkeypatch, CFG, anon={"button.go": 1, ".inside": 0},
               authed={".inside": 1}, authenticate=boom)


def test_the_guard_cannot_be_satisfied_by_refusing_everything(monkeypatch):
    """Both directions are load-bearing, so a guard that always raises is not a passing guard.

    The write-safety matrix's "must remain learnable" clause, one instrument over: without this the
    three refusal cells above are satisfied by an implementation that raises unconditionally.
    """
    seen, calls, _ = _drive(monkeypatch, CFG,
                            anon={"button.go": 3, ".inside": 0},
                            authed={".inside": 2})
    assert calls["auth"] == 1 and seen["launches"] == 1


@pytest.mark.parametrize("substrate", sorted(SR.LOGIN))
def test_every_declared_login_names_the_selectors_the_guard_reads(substrate):
    """DERIVED from the LOGIN table, so a substrate added tomorrow is covered rather than forgotten."""
    cfg = SR.LOGIN[substrate]
    for key in ("path", "submit_selector", "success_selector"):
        assert cfg.get(key), f"{substrate}: {key} is what the guard interrogates; it cannot be blank"


@pytest.mark.parametrize("substrate", sorted(SR.LOGIN))
def test_no_success_selector_is_a_list(substrate):
    """R4.98's MECHANISM, pinned rather than its two values.

    A CSS selector list matches if ANY branch does, so one always-true branch disarms the whole
    check — and it reads as a helpful fallback, which is why it survived review. The live
    differential above is the real guard; this one fails at edit time, before a browser is involved,
    and names why.
    """
    sel = SR.LOGIN[substrate]["success_selector"]
    assert "," not in sel, (
        f"{substrate}: success_selector {sel!r} is a selector LIST. It matches if ANY branch does, "
        f"so a single always-true branch (`#navbar`, on Gitea's logged-OUT page) makes the login "
        f"report success having authenticated nobody (R4.98). Use one selector that is absent "
        f"before login.")


# --- R4.99: the phase boundary --------------------------------------------------------------------

def _classify(rec, *, mutating=False):
    entry = next(e for e in corpus.CORPORA["gitea"]
                 if e.truth.mutating is mutating)
    return outcomes.classify(entry.truth, rec,
                             outcomes.Oracle(available=True, data_correct=None))


def test_a_harness_fault_is_not_scored_against_the_product():
    """R4.99. A broken login used to publish `not_authored` — loud, scored, and blaming the product
    for the bench's own misdeclaration. Reproduced against the real `classify` before fixing."""
    rec = SR._Record(False, "", None, code="", authored=None,
                     harness_error="SubstrateNotReady: gitea: submit_selector matches NOTHING")
    for mutating in (False, True):
        v = _classify(rec, mutating=mutating)
        assert v.outcome == "unscored", (
            f"mutating={mutating}: a harness fault must leave every denominator, not be scored")


def test_a_genuine_discovery_failure_is_still_scored():
    """The other direction, and the reason it is a separate cell: a fix that routed everything to
    `harness` would satisfy the cell above while silently reverting R4.96 — which is the "patch on a
    patch" this register keeps filing. Measured live: `gitea-start-timer` fails here with the
    harness perfectly healthy, at 40 turns and $0.58."""
    rec = SR._Record(True, "", None, code="", authored=False, harness_error="")
    for mutating in (False, True):
        assert _classify(rec, mutating=mutating).outcome == "not_authored"


def test_the_record_carries_the_harness_field_the_classifier_reads():
    """It was hard-coded `""` for a whole release, so the family was unreachable from this runner."""
    rec = SR._Record(True, "boom", None, code="", authored=False, harness_error="the reset failed")
    assert rec.harness_error == "the reset failed"
    assert "harness" in outcomes.UNSCORED_FAMILIES


def test_the_runner_threads_the_harness_error_into_the_record_it_builds():
    """R4.99's defect in its CURRENT shape.

    It used to be `self.harness_error = ""` hard-coded in a private dataclass. Since 0.130.0 the
    runner builds a real `ScenarioRun`, so the same defect is now the construction site simply not
    passing the field — the classifier would then read the dataclass default, which is `""`, and the
    harness family would be unreachable again with nothing looking different.
    """
    import ast
    import inspect
    import textwrap

    fn = ast.parse(textwrap.dedent(inspect.getsource(SR.score_one))).body[0]
    builds = [n for n in ast.walk(fn)
              if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "ScenarioRun"]
    assert builds, "score_one no longer builds a ScenarioRun; this guard needs re-deriving"
    for call in builds:
        passed = {k.arg: ast.unparse(k.value) for k in call.keywords}
        assert passed.get("harness_error") == "harness_error", (
            f"the ScenarioRun is built with harness_error={passed.get('harness_error')!r}; the "
            f"preflight's finding must reach the classifier or the harness family is unreachable "
            f"and a broken login is scored against the product (R4.99)")


def test_the_preflight_sentinel_is_not_mistaken_for_an_agent_failure():
    """`_PreflightFailed` travels through the learn's `try`; if the broad handler caught it first,
    the harness's fault would be re-attributed to the agent — the very defect, one line lower.

    ASSERTED AS A PROPERTY, NOT SCANNED AS TEXT, and the first draft is why. It compared the offsets
    of two source phrases and went red on the COMMENT that explains this fix — the fourth time in
    this project that a scan has matched its own prose. Handler order is a fact about the AST, so
    that is where it is read from; no wording change can disarm or fake it.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(SR.score_one)))

    def names(handler):
        t = handler.type
        if t is None:
            return {"<bare>"}
        if isinstance(t, ast.Name):
            return {t.id}
        if isinstance(t, ast.Tuple):
            return {n.id for n in t.elts if isinstance(n, ast.Name)}
        return set()

    guarded = [n for n in ast.walk(tree) if isinstance(n, ast.Try)
               and any("_PreflightFailed" in names(h) for h in n.handlers)]
    assert guarded, "the learn's try must catch the preflight skip BY NAME"
    for node in guarded:
        order = [names(h) for h in node.handlers]
        sentinel = next(i for i, s in enumerate(order) if "_PreflightFailed" in s)
        broad = [i for i, s in enumerate(order) if {"Exception", "BaseException", "<bare>"} & s]
        assert all(sentinel < i for i in broad), (
            f"handler order {order} lets a broad handler swallow the skip, which re-attributes the "
            f"harness's fault to the agent (R4.99)")


def test_agent_ran_is_bound_even_when_the_learn_is_skipped():
    """Both of the learn's original exits assigned it, so the missing initialiser was invisible
    until the skip added a third — a NameError on the first harness failure, i.e. the exact run this
    slice exists to make work."""
    import inspect

    src = inspect.getsource(SR.score_one)
    assert 'agent_ran, agent_error = False, ""' in src
    assert src.index('agent_ran, agent_error = False, ""') < src.index("with BoundaryLedger()")


def test_the_inter_phase_reset_is_attributed_to_the_harness_too():
    """Found by this slice's OWN adversarial pass, which mapped every call to its enclosing `try`.

    The mid-run reset and re-auth sat inside none, so a failure there propagated out of `score_one`
    and killed the process — after the learn had already been PAID FOR, discarding a record that
    cost money. Asserted structurally because the alternative is standing up a substrate.
    """
    import ast
    import inspect
    import textwrap

    fn = ast.parse(textwrap.dedent(inspect.getsource(SR.score_one))).body[0]
    tries = [(n.lineno, max(getattr(x, "lineno", n.lineno) for x in ast.walk(n)))
             for n in ast.walk(fn) if isinstance(n, ast.Try)]

    def covered(line):
        return any(a <= line <= b for a, b in tries)

    unguarded = sorted({
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) or getattr(n.func, "attr", None)) == "refresh_auth"
        and not covered(n.lineno)})
    assert not unguarded, (
        f"refresh_auth at line(s) {unguarded} of score_one is inside no try, so its failure "
        f"escapes as a crash instead of being recorded as a harness_error (R4.99)")


def test_a_failed_inter_phase_reset_skips_the_replay():
    """The replay must not be scored against a world the harness could not restore — otherwise the
    premise is taken from the LEARN's leftovers and the oracle adjudicates the wrong phase."""
    import inspect

    src = inspect.getsource(SR.score_one)
    assert 'if out.get("learned") and not harness_error:' in src, (
        "the replay gate must read harness_error; without it a failed reset still replays")


def test_the_step_ceiling_is_measured_in_turns_not_captured_steps():
    """R4.100. `len(res.steps)` is the RECIPE length; an agent can burn the whole budget and record
    nothing, which is exactly the case the sensor exists for. Measured: llm_calls 20, budget 20,
    steps 0, and the old sensor said `false`."""
    import inspect

    src = inspect.getsource(SR.score_one)
    assert 'out["hit_step_ceiling"] = usage.calls >= budget' in src
    assert 'out["hit_step_ceiling"] = len(res.steps' not in src, (
        "the captured-steps form is R4.100 and must not come back")
