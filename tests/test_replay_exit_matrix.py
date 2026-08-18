"""`flows.replay()` driven through its whole exit set, browser-free, in milliseconds.

WHY. `replay()` + `_attempt_replay` are ~490 page-free lines, and they are where SEVEN of PR #165's ten
confirmed defects lived (R4.44-R4.53). Reaching them costs a Chromium session today, so the entire
surface has exactly TWO cells that pass `record=` — and eleven mutations of the record plumbing survive
the whole suite. Eight of those ten findings are literally "a record site that was not written", which is
a class no per-scenario browser test can catch and an exit-set matrix catches by construction.

THE EXIT SET IS DERIVED, NOT LISTED. `test_the_exit_set_has_not_grown` AST-counts every `raise` and
`return` inside `replay()` and compares it to a committed number, so an exit added tomorrow breaks that
ratchet and forces a cell rather than slipping in uncovered. An earlier draft of this file hand-listed
"at least 17 exits"; the AST says 16, which is exactly why the count is derived.

WHAT THIS DOES NOT PROVE. The fake reproduces the engine's CONTRACT, not the engine. It cannot see a
page-side timing change, so `drift_bench` and the browser write-safety matrix remain the adjudicators for
anything that runs in the page. Its fidelity to the real contract is asserted by
`tests/test_fake_engine_fidelity.py`, which runs the REAL engine and diffs what it writes into `out`.
"""

from __future__ import annotations

import ast
import pathlib
import time

import pytest

import _fake_engine as fe
from ultracua.cache import CachedFlow, CachedStep, FlowCache
from ultracua.flows import (DriftError, EscalateError, FlowReplayError, FlowSpec, LearnResult,
                            MutateSpec,
                            RunRecord, ShapeDriftError, WriteReadbackError, WriteUnverifiedError,
                            approve, flow_key, replay, save_spec, _update_meta)
from ultracua.locators import LocatorSpec

# Committed on purpose: a change here means `replay()` grew or lost an exit, and the matrix below must be
# revisited rather than silently covering less. Derived by AST, never counted by hand.
EXIT_COUNT = 16

USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
         "cache_write_tokens": 0, "cost_usd": 0.0}


def _seed(tmp_path, monkeypatch, *, name, mutating=False, mutate=None, extract=None,
          approved=True):
    """A spec plus a cached recipe, with no page anywhere."""
    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    spec = FlowSpec(name=name, goal=f"goal-{name}", start_url="http://fixture.invalid/",
                    extract=extract, mutate=mutate)
    save_spec(spec)
    cache = FlowCache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    step = CachedStep(action="click", locator=LocatorSpec(role="button", name="Go", tag="button"),
                      intent="go", mutating=mutating)
    cache.put(CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, url=spec.start_url,
                         created_ts=time.time(), steps=[step]))
    if approved:
        approve(spec, cache=cache)
    return spec, cache


def test_the_exit_set_has_not_grown() -> None:
    """The ratchet. Derive the truth, compare it to the claim — the shape `check_shard_coverage` uses."""
    src = pathlib.Path("src/ultracua/flows.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "replay":
            n = sum(1 for x in ast.walk(node) if isinstance(x, (ast.Raise, ast.Return)))
            assert n == EXIT_COUNT, (
                f"replay() now has {n} exits, not {EXIT_COUNT}. An exit added without a cell in this "
                f"file is an exit nothing covers — add the cell, then update EXIT_COUNT.")
            return
    pytest.fail("replay() not found — the AST walk is broken, so the ratchet asserts nothing")


# ---------------------------------------------------------------------------------------------------
# The exits. Each asserts the RECORD as well as the return, because the record is what B1 got wrong.

async def test_ok_read_exit_records_a_priced_zero(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="okread")
    eng = fe.FakeEngine(fe.Attempt(report=fe.report(usage=USAGE, llm_calls=3))).install(monkeypatch)
    rec = RunRecord()
    data = await replay(spec, cache=cache, record=rec)
    assert data is None and len(eng.calls) == 1
    assert (rec.ok, rec.attempts, rec.mode) == (True, 1, "replay")
    assert rec.llm_calls == 3, "the engine reported 3 decides and the record must carry them"
    assert rec.usage.get("cost_usd") == 0.0, "a fully observed 0-LLM replay must claim zero, not unknown"


async def test_miss_exit_fails_loud_with_a_reason(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="miss")
    fe.FakeEngine(fe.Attempt(report=fe.report(mode="miss", success=False,
                                              note="no cached flow for key"))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(FlowReplayError) as exc:
        await replay(spec, cache=cache, record=rec)
    assert "learn" in str(exc.value).lower()
    assert rec.ok is False and rec.failure_code, "a failure must name itself on the record"


async def test_escalate_exit_is_a_distinct_kind(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="escalate")
    fe.FakeEngine(fe.Attempt(report=fe.report(mode="escalate", success=False,
                                              note="interstitial"))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(FlowReplayError):
        await replay(spec, cache=cache, record=rec)
    assert rec.failure_code == "escalate", f"escalate collapsed into {rec.failure_code!r}"


async def test_drift_exit_is_not_retried_without_a_login(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="drift")
    eng = fe.FakeEngine(fe.Attempt(report=fe.report(success=False, note="page drift",
                                                    usage=USAGE))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(DriftError):
        await replay(spec, cache=cache, record=rec)
    assert len(eng.calls) == 1, "no login is configured, so nothing may re-drive the flow"
    assert rec.failure_code == "drift" and rec.landed in (None, False)


async def test_precheck_already_done_never_reaches_the_engine(tmp_path, monkeypatch) -> None:
    """The pre-attempt exit. `_mark_ok` runs here and `_attempt_replay` never does, which is why B1's
    M3 found `record.ok` stale on this path."""
    spec, cache = _seed(tmp_path, monkeypatch, name="precheck", mutating=True,
                        mutate=MutateSpec(confirm_text_contains="done"))
    eng = fe.FakeEngine(fe.Attempt(report=fe.report()), precheck=True).install(monkeypatch)
    rec = RunRecord()
    out = await replay(spec, cache=cache, record=rec)
    assert out == {"status": "already-done", "data": None}
    assert len(eng.calls) == 0, "the precheck short-circuit must not open a session"
    assert eng.precheck_calls == 1
    assert rec.ok is True, "a success return that never enters _attempt_replay must still say so"


async def test_write_unverified_exit_cannot_be_retried_or_relearned(tmp_path, monkeypatch) -> None:
    """The commit actuated and the confirm cannot report on it. Raised before `retry_ok` exists, so it
    reaches neither the auth-refresh retry nor the relearn — both would fire it again."""
    spec, cache = _seed(tmp_path, monkeypatch, name="wunver", mutating=True,
                        mutate=MutateSpec(confirm_text_contains="thanks"))
    eng = fe.FakeEngine(fe.Attempt(
        report=fe.report(usage=USAGE),
        out={"found": False, "confirm_pre_true": True, "error": "confirm never appeared"},
    )).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(WriteUnverifiedError):
        await replay(spec, cache=cache, record=rec)
    assert len(eng.calls) == 1, "a write that may have committed must not be re-driven"
    assert rec.failure_code == "write_unverified"


async def test_write_readback_exit_reports_the_write_as_landed(tmp_path, monkeypatch) -> None:
    """The write CONFIRMED and only its readback missed — the one class that must never be retried."""
    spec, cache = _seed(tmp_path, monkeypatch, name="wread", mutating=True,
                        mutate=MutateSpec(confirm_text_contains="thanks"),
                        extract={"order": "the order id"})
    eng = fe.FakeEngine(fe.Attempt(
        report=fe.report(usage=USAGE),
        out={"found": True, "write_landed": True, "extract_found": False,
             "extract_error": "not on the page"},
    )).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(WriteReadbackError) as exc:
        await replay(spec, cache=cache, record=rec)
    assert exc.value.landed is True, "this class declares the write landed; the ledger reads it"
    assert len(eng.calls) == 1
    assert rec.failure_code == "write_unreadable"


async def test_shape_drift_exit_refuses_rather_than_returning_wrong_data(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="shape", extract={"n": "a number"})
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    _update_meta(cache, key, lambda m: setattr(m, "shape", {"n": "int"}), on_unreadable="raise")
    fe.FakeEngine(fe.Attempt(report=fe.report(usage=USAGE),
                             out={"found": True, "data": {"totally": "different"}})).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(ShapeDriftError):
        await replay(spec, cache=cache, record=rec)
    assert rec.failure_code == "shape"


async def test_an_engine_raise_leaves_the_record_saying_unknown_not_no(tmp_path, monkeypatch) -> None:
    """M4's three-state guarantee: an exception between the pre-stamp and the population block loses that
    attempt's evidence, so the record must say UNKNOWN rather than deny a write that may have landed."""
    spec, cache = _seed(tmp_path, monkeypatch, name="raised", mutating=True,
                        mutate=MutateSpec(confirm_text_contains="thanks"))
    fe.FakeEngine(fe.Attempt(raises=RuntimeError("browser exploded mid-run"))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(RuntimeError):
        await replay(spec, cache=cache, record=rec)
    assert rec.mode == "raised", "the pre-stamp is what makes a raise say 'unknown'"
    assert rec.landed is None and rec.committed is None, (
        "a raise must never leave a CONFIDENT DENIAL about a write that may have committed")


async def test_a_typed_error_from_the_engine_reaches_the_caller(tmp_path, monkeypatch) -> None:
    """The `except FlowReplayError` arming point at the bottom of replay(). The engine itself never
    raises this class (`flow.py` does not import it), so this exercises the handler, not a live path."""
    spec, cache = _seed(tmp_path, monkeypatch, name="fre")
    fe.FakeEngine(fe.Attempt(raises=DriftError("propagated"))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(DriftError):
        await replay(spec, cache=cache, record=rec)
    assert rec.mode == "raised"


# ---------------------------------------------------------------------------------------------------
# The RETRY and RELEARN exits. These are the paths B1's M2/M3/F2 defects lived on — the relearn sat
# entirely outside the record, `_mark_ok` was missing on two of three success returns, and a raise
# mid-relearn lost the largest spend of the run. Nothing reached them without a browser until now.

def _with_login(spec):
    """A login makes the auth-refresh retry admissible for a READ flow."""
    from ultracua.flows import LoginSpec
    spec.login = LoginSpec(url="http://fixture.invalid/login")
    return spec


async def test_auth_refresh_retry_succeeds_and_the_record_spans_both_attempts(tmp_path, monkeypatch) -> None:
    """Exit: the post-refresh success. Two attempts, so the record must ACCUMULATE rather than
    last-attempt-wins — B1's M2."""
    spec, cache = _seed(tmp_path, monkeypatch, name="retryok")
    _with_login(spec)
    eng = fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="session expired",
                                    usage=dict(USAGE, calls=1, cost_usd=0.01))),
        fe.Attempt(report=fe.report(usage=dict(USAGE, calls=2, cost_usd=0.02))),
    ).install(monkeypatch)
    rec = RunRecord()
    await replay(spec, cache=cache, record=rec)
    assert len(eng.calls) == 2 and eng.refresh_calls == 1
    assert rec.attempts == 2, "both attempts must be counted"
    assert rec.auth_refreshed is True, "the refresh happened and the record must say so"
    assert rec.ok is True, "the retry succeeded"
    assert rec.usage.get("calls") == 3, (
        f"usage must span BOTH attempts (1 + 2); got {rec.usage.get('calls')} — last-attempt-wins is M2")


async def test_auth_refresh_then_precheck_already_done(tmp_path, monkeypatch) -> None:
    """Exit: the post-refresh precheck. `_mark_ok` runs here and `_attempt_replay` never does again."""
    spec, cache = _seed(tmp_path, monkeypatch, name="retryprecheck")
    _with_login(spec)
    eng = fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="expired", usage=USAGE)),
        precheck=[False, True],
    ).install(monkeypatch)
    rec = RunRecord()
    out = await replay(spec, cache=cache, record=rec)
    assert out == {"status": "already-done", "data": None}
    assert eng.refresh_calls == 1 and eng.precheck_calls == 2
    assert rec.auth_refreshed is True
    assert rec.ok is True, "the post-refresh precheck is a success return that must say so"
    assert rec.landed is None and rec.committed is None, (
        "the failed attempt stamped landed=False meaning 'THAT attempt did not confirm'. A success that "
        "never evaluated write evidence must downgrade it to UNKNOWN, not leave a denial standing — "
        "B1's F3/F4, and the only cell that distinguishes the two")


async def test_relearn_repair_success_records_the_repair_attempt(tmp_path, monkeypatch) -> None:
    """Exit: the suffix-replan success under on_drift='relearn'."""
    spec, cache = _seed(tmp_path, monkeypatch, name="repairok", approved=False)
    eng = fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="drift", usage=USAGE)),
        fe.Attempt(report=fe.report(mode="replay+replan", usage=dict(USAGE, calls=4, cost_usd=0.04))),
    ).install(monkeypatch)
    rec = RunRecord()
    await replay(spec, cache=cache, on_drift="relearn", provider=object(), router=object(), record=rec)
    assert len(eng.calls) == 2 and eng.learn_calls == 0, "the repair must be tried before a full relearn"
    assert rec.ok is True and rec.attempts == 2
    assert rec.usage.get("calls") == 4


async def test_relearn_full_success_absorbs_the_learn_spend(tmp_path, monkeypatch) -> None:
    """Exit: the full re-author success. `learn()` is the largest spend in the run and sat entirely
    outside the record before B1's M2."""
    spec, cache = _seed(tmp_path, monkeypatch, name="relearnok", approved=False)
    lr = LearnResult(spec=spec, cached=True, steps=[], data={"n": 1}, found=True, note="")
    eng = fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="drift", usage=USAGE)),
        fe.Attempt(report=fe.report(success=False, note="replan failed", usage=USAGE)),
        learn=lr,
    ).install(monkeypatch)
    rec = RunRecord()
    data = await replay(spec, cache=cache, on_drift="relearn", provider=object(), router=object(),
                        record=rec)
    assert data == {"n": 1} and eng.learn_calls == 1
    assert rec.ok is True, "the relearn success must clear the two failed attempts' verdict"
    assert rec.mode == "relearn", "the record must name the path that produced the answer"
    assert rec.usage.get("cost_usd") is None, (
        "the relearn's own watch must be absorbed: it observes objects that expose no totals, so a "
        "run that re-authored can no longer claim a priced zero (B1's M2)")


async def test_a_relearn_that_raises_still_reports_what_it_spent(tmp_path, monkeypatch) -> None:
    """Exit: the re-raise after `learn()` throws. F2's fix — a provider 500 mid-authoring used to carry
    the earlier attempts' cents against dollars actually spent."""
    spec, cache = _seed(tmp_path, monkeypatch, name="relearnraise", approved=False)
    eng = fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="drift", usage=USAGE)),
        fe.Attempt(report=fe.report(success=False, note="replan failed", usage=USAGE)),
        learn=RuntimeError("provider 500 mid-authoring"),
    ).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(RuntimeError):
        await replay(spec, cache=cache, on_drift="relearn", provider=object(), router=object(),
                     record=rec)
    assert eng.learn_calls == 1
    assert rec.mode == "raised", "a relearn that raised must say the run ended unknown"
    assert rec.usage.get("cost_usd") is None, (
        "a relearn that RAISED must still report what it spent — F2's fix, and the leg the same "
        "defect was found on one over")


# ---------------------------------------------------------------------------------------------------
# The B1 findings, as strict xfails against SHIPPED behaviour. Step 1.5's sink must flip these, and
# `strict` is what forces their removal rather than leaving them as wallpaper.

@pytest.mark.xfail(strict=True, reason="R4.45: the miss exit returns a FlowReport with no `extra`, so "
                                      "record.usage is {} against RunRecord's 'always populated'")
async def test_R4_45_miss_exit_populates_usage(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="r445")
    fe.FakeEngine(fe.Attempt(report=fe.report(mode="miss", success=False,
                                              note="no cached flow"))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(FlowReplayError):
        await replay(spec, cache=cache, record=rec)
    assert "cost_usd" in rec.usage


@pytest.mark.xfail(strict=True, reason="R4.45: the escalate exit omits `extra` too — the same shape as "
                                      "the miss exit, one branch over")
async def test_R4_45_escalate_exit_populates_usage(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="r445b")
    fe.FakeEngine(fe.Attempt(report=fe.report(mode="escalate", success=False,
                                              note="captcha"))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(FlowReplayError):
        await replay(spec, cache=cache, record=rec)
    assert "cost_usd" in rec.usage


@pytest.mark.xfail(strict=True, reason="R4.44: an attempt whose run_cached RAISES drops that attempt's "
                                       "spend — the watch and the population block both sit after it")
async def test_R4_44_a_raised_attempt_keeps_its_spend(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="r444")
    fe.FakeEngine(fe.Attempt(raises=RuntimeError("boom"))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(RuntimeError):
        await replay(spec, cache=cache, record=rec)
    assert "cost_usd" in rec.usage, "the raise path records no usage at all"


@pytest.mark.xfail(strict=True, reason="R4.49: record.failure_code carries the internal `kind`, not "
                                       "FlowReplayError.code, so two channels describe one run "
                                       "differently")
async def test_R4_49_failure_code_matches_the_exception_code(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="r449")
    fe.FakeEngine(fe.Attempt(report=fe.report(mode="miss", success=False,
                                              note="no cached flow"))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(FlowReplayError) as exc:
        await replay(spec, cache=cache, record=rec)
    assert rec.failure_code == exc.value.code


@pytest.mark.xfail(strict=True, reason="R4.51: nothing pins the headline end to end — an engine "
                                       "reporting UNKNOWN on every replay passes every cell")
async def test_R4_51_an_unobserved_replay_is_distinguishable(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="r451")
    unobserved = dict(USAGE, cost_usd=None, unobserved_llm_path=True)
    fe.FakeEngine(fe.Attempt(report=fe.report(usage=unobserved))).install(monkeypatch)
    rec = RunRecord()
    await replay(spec, cache=cache, record=rec)
    assert rec.usage.get("cost_usd") == 0.0, (
        "the engine reported UNKNOWN and replay() passed it through; nothing in the suite objects")


@pytest.mark.xfail(strict=True, reason="R4.57: a SUCCESSFUL auth-refresh retry leaves the failed "
                                       "attempt's failure_code on the record — `_attempt_replay`'s "
                                       "success block sets ok=True but never clears it, and `_mark_ok` "
                                       "(which does) is only called on the precheck and relearn exits")
async def test_R4_57_a_successful_retry_clears_the_failed_attempts_code(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="r457")
    _with_login(spec)
    fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="session expired", usage=USAGE)),
        fe.Attempt(report=fe.report(usage=USAGE)),
    ).install(monkeypatch)
    rec = RunRecord()
    await replay(spec, cache=cache, record=rec)
    assert rec.ok is True
    assert rec.failure_code == "", (
        f"a successful run reports failure_code={rec.failure_code!r} from the attempt that failed")
