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
import types
import time

import pytest

import _fake_engine as fe
from ultracua import flows as flows_mod
from ultracua.cache import CachedFlow, CachedStep, FlowCache
from ultracua.flows import (DriftError, EscalateError, FlowReplayError, FlowSpec, LearnResult,
                            MutateSpec,
                            RunRecord, ShapeDriftError, WriteReadbackError, WriteUnverifiedError,
                            approve, flow_key, replay, save_spec, _update_meta)
from ultracua.locators import LocatorSpec

# Committed on purpose: a change here means `replay()` grew or lost an exit, and the matrix below must be
# revisited rather than silently covering less. Derived by AST, never counted by hand.
# 16 in `_replay_body` plus 2 in the `replay()` wrapper, which exists so `_RecordSink.finish` is
# called exactly once however the body leaves.
EXIT_COUNT = 18

# The ways a record may explain an UNKNOWN cost. A closed set on purpose: 1.5 picks which one it
# sets, but "None with no reason" is not among the options (R4.46).
COST_UNKNOWN_REASONS = ("unobserved_llm_path", "attempts_without_usage")

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
    """The ratchet. Derive the truth, compare it to the claim — the shape `check_shard_coverage` uses.

    BOTH functions, since 1.5 split them. `replay()` is now a four-line wrapper whose only job is to
    call `_RecordSink.finish` exactly once; the exits live in `_replay_body`. Counting only the
    wrapper would have read "2 exits" and quietly stopped covering anything — which is precisely what
    this ratchet fired on when the split landed.
    """
    src = pathlib.Path("src/ultracua/flows.py").read_text(encoding="utf-8")
    found = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in ("replay", "_replay_body"):
            found[node.name] = sum(1 for x in ast.walk(node)
                                   if isinstance(x, (ast.Raise, ast.Return)))
    assert set(found) == {"replay", "_replay_body"}, (
        f"the AST walk found {sorted(found)} — it must see both, or the ratchet asserts nothing")
    n = sum(found.values())
    assert n == EXIT_COUNT, (
        f"replay()+_replay_body now have {n} exits ({found}), not {EXIT_COUNT}. An exit added "
        f"without a cell in this file is an exit nothing covers — add the cell, then update "
        f"EXIT_COUNT.")


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
    assert rec.failure_code == exc.value.code == "write_readback", (
        "since 1.5 the record carries the exception's code, not the engine's internal kind "
        "(R4.49) — one vocabulary, so the two channels cannot describe one run differently")


async def test_shape_drift_exit_refuses_rather_than_returning_wrong_data(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="shape", extract={"n": "a number"})
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    _update_meta(cache, key, lambda m: setattr(m, "shape", {"n": "int"}), on_unreadable="raise")
    fe.FakeEngine(fe.Attempt(report=fe.report(usage=USAGE),
                             out={"found": True, "data": {"totally": "different"}})).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(ShapeDriftError) as exc:
        await replay(spec, cache=cache, record=rec)
    assert rec.failure_code == exc.value.code == "shape_drift", (
        "R4.49: the record's code is the exception's code since 1.5")


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
    await replay(spec, cache=cache, record=rec, router=eng.router)
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
    await replay(spec, cache=cache, on_drift="relearn", provider=eng.provider, router=eng.router, record=rec)
    assert len(eng.calls) == 2 and eng.learn_calls == 0, "the repair must be tried before a full relearn"
    assert rec.ok is True and rec.attempts == 2
    assert rec.usage.get("calls") == 4


async def test_relearn_full_success_absorbs_the_learn_spend(tmp_path, monkeypatch) -> None:
    """Exit: the full re-author success. `learn()` is the largest spend in the run and sat entirely
    outside the record before B1's M2."""
    spec, cache = _seed(tmp_path, monkeypatch, name="relearnok", approved=False)
    # The relearn's report is what carries its calls, traces, heals and duration onto the record
    # (R4.50). `report=` is a REQUIRED keyword, so a `learn()` exit that forgets it is a TypeError.
    lr = LearnResult(spec=spec, cached=True, steps=[], data={"n": 1}, found=True, note="",
                     report=fe.report(mode="learn", usage=dict(USAGE, calls=2, cost_usd=0.05),
                                      llm_calls=7, traces=[fe.trace(0, ms=30.0)]))
    eng = fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="drift", usage=dict(USAGE, calls=1))),
        fe.Attempt(report=fe.report(success=False, note="replan failed", usage=dict(USAGE, calls=1))),
        learn=lr, learn_usage=dict(USAGE, calls=2, cost_usd=0.05),
    ).install(monkeypatch)
    rec = RunRecord()
    data = await replay(spec, cache=cache, on_drift="relearn", provider=eng.provider, router=eng.router,
                        record=rec)
    assert data == {"n": 1} and eng.learn_calls == 1
    assert rec.ok is True, "the relearn success must clear the two failed attempts' verdict"
    assert rec.mode == "relearn", "the record must name the path that produced the answer"
    # M2 + R4.50. The old cell asserted `cost_usd is None` here, which was an ARTIFACT of the test
    # handing `replay()` a bare `object()` as the provider: an owner exposing no totals marks the run
    # unobserved, so the None it asserted came from the harness, not from the relearn. With a router
    # that really spends, the property is that the re-author's spend and its CALLS both land — the
    # calls being the half R4.50 says was dropped while the dollars were kept.
    assert rec.usage.get("calls") == 4, (
        f"the relearn's 2 calls must join the two attempts' 2; got {rec.usage.get('calls')}")
    assert rec.llm_calls == 7, (
        f"the re-author reported 7 decides and the record says {rec.llm_calls} — R4.50 is the case "
        f"where the dollars are kept and the calls are not")
    assert rec.total_ms == pytest.approx(30.0), "the re-author's duration must reach the record too"
    assert rec.mode == "relearn"


async def test_a_relearn_that_raises_still_reports_what_it_spent(tmp_path, monkeypatch) -> None:
    """Exit: the re-raise after `learn()` throws. F2's fix — a provider 500 mid-authoring used to carry
    the earlier attempts' cents against dollars actually spent."""
    spec, cache = _seed(tmp_path, monkeypatch, name="relearnraise", approved=False)
    eng = fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="drift", usage=dict(USAGE, calls=1))),
        fe.Attempt(report=fe.report(success=False, note="replan failed", usage=dict(USAGE, calls=1))),
        learn=RuntimeError("provider 500 mid-authoring"),
        learn_usage=dict(USAGE, calls=2, cost_usd=0.05),   # burned BEFORE the 500
    ).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(RuntimeError):
        await replay(spec, cache=cache, on_drift="relearn", provider=eng.provider, router=eng.router,
                     record=rec)
    assert eng.learn_calls == 1
    assert rec.mode == "raised", "a relearn that raised must say the run ended unknown"
    # F2, with the harness artifact removed as above. The re-author burned two calls and THEN the
    # provider failed; the run-scoped watch spans the raise, so the money is on the record without the
    # relearn needing a watch, an absorb, or a site of its own on this leg.
    assert rec.usage.get("calls") == 4, (
        f"the raised re-author's 2 calls were dropped from the two attempts' 2; got "
        f"{rec.usage.get('calls')}")
    assert rec.mode == "raised" and rec.failure_code == "raised"


# ---------------------------------------------------------------------------------------------------
# The B1 findings, as strict xfails against SHIPPED behaviour. Step 1.5's sink must flip these, and
# `strict` is what forces their removal rather than leaving them as wallpaper.

async def test_R4_45_miss_exit_populates_usage(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="r445")
    fe.FakeEngine(fe.Attempt(report=fe.report(mode="miss", success=False,
                                              note="no cached flow"))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(FlowReplayError):
        await replay(spec, cache=cache, record=rec)
    assert "cost_usd" in rec.usage


async def test_R4_45_escalate_exit_populates_usage(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="r445b")
    fe.FakeEngine(fe.Attempt(report=fe.report(mode="escalate", success=False,
                                              note="captcha"))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(FlowReplayError):
        await replay(spec, cache=cache, record=rec)
    assert "cost_usd" in rec.usage


async def test_R4_44_a_raised_attempt_keeps_its_spend(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="r444")
    fe.FakeEngine(fe.Attempt(raises=RuntimeError("boom"))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(RuntimeError):
        await replay(spec, cache=cache, record=rec)
    assert "cost_usd" in rec.usage, "the raise path records no usage at all"


async def test_R4_49_failure_code_matches_the_exception_code(tmp_path, monkeypatch) -> None:
    spec, cache = _seed(tmp_path, monkeypatch, name="r449")
    fe.FakeEngine(fe.Attempt(report=fe.report(mode="miss", success=False,
                                              note="no cached flow"))).install(monkeypatch)
    rec = RunRecord()
    with pytest.raises(FlowReplayError) as exc:
        await replay(spec, cache=cache, record=rec)
    assert rec.failure_code == exc.value.code


async def test_R4_51_an_unobserved_replay_is_distinguishable(tmp_path, monkeypatch) -> None:
    """R4.51's end-to-end pin: a priced zero and an unobserved run must not look the same to a caller.

    THIS CELL USED TO ASSERT THE COUNTEREXAMPLE (R4.59). It was a strict xfail demanding
    `cost_usd == 0.0` from a run whose engine reported `cost_usd=None, unobserved_llm_path=True` — i.e.
    it specified that `replay()` should overwrite an UNKNOWN with a confident zero, which is the exact
    failure B1 exists to prevent, and `strict=True` made it a standing demand that someone implement it.

    R4.51's actual complaint is a COVERAGE gap, not a behaviour defect: "an engine reporting
    unobserved on every replay would pass every cell". The property that closes it is
    DISTINGUISHABILITY — which is what the cell was always named for — so both arms run here, through
    the same path, differing only in what the engine reported. Measured before this rewrite: the two
    already differ, so the cell is green rather than xfail, and nothing in `src/` changes.
    """
    spec, cache = _seed(tmp_path, monkeypatch, name="r451")
    unobserved = dict(USAGE, cost_usd=None, unobserved_llm_path=True)
    fe.FakeEngine(fe.Attempt(report=fe.report(usage=unobserved))).install(monkeypatch)
    rec = RunRecord()
    await replay(spec, cache=cache, record=rec)
    assert rec.usage.get("cost_usd") is None, (
        "an unobserved run was priced at a confident zero — the understated bill B1 exists to prevent")
    assert rec.usage.get("unobserved_llm_path") is True, (
        "the cost is unknown and the record does not say WHY, so a caller cannot tell it from a "
        "genuinely free run")

    spec2, cache2 = _seed(tmp_path, monkeypatch, name="r451b")
    fe.FakeEngine(fe.Attempt(report=fe.report(usage=dict(USAGE)))).install(monkeypatch)
    rec2 = RunRecord()
    await replay(spec2, cache=cache2, record=rec2)
    assert rec2.usage.get("cost_usd") == 0.0, "a fully observed 0-LLM replay must CLAIM zero"
    assert not rec2.usage.get("unobserved_llm_path")

    # THE PROPERTY, stated over the pair rather than either arm: an engine that reported UNKNOWN on
    # every replay — the thing R4.51 says nothing would catch — now fails here, because these two
    # records would be equal.
    assert rec.usage.get("cost_usd") != rec2.usage.get("cost_usd"), (
        "a priced zero and an unobserved run are indistinguishable on the record")

    # AND THE MERGE PATH, which the two arms above do NOT reach. `_absorb_usage` early-returns on the
    # first attempt (`if not dst: record.usage = dict(usage)`), so a one-attempt cell only proves
    # pass-through. Measured while arming this rewrite: a mutation that summed None as 0 in the merge
    # left both arms above green. A priced attempt followed by an unobserved one is where sticky-None
    # actually lives, and it is the shape a real auth-refresh retry produces.
    spec3, cache3 = _seed(tmp_path, monkeypatch, name="r451c")
    _with_login(spec3)
    fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="session expired",
                                    usage=dict(USAGE, cost_usd=0.25))),
        fe.Attempt(report=fe.report(usage=unobserved)),
    ).install(monkeypatch)
    rec3 = RunRecord()
    await replay(spec3, cache=cache3, record=rec3)
    assert rec3.usage.get("cost_usd") is None, (
        f"a priced attempt merged with an UNOBSERVED one reported {rec3.usage.get('cost_usd')!r} — a "
        f"partial sum presented as the total is the understated bill")
    assert rec3.usage.get("unobserved_llm_path") is True


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


# ===================================================================================================
# THE GOLDENS FOR STEP 1.5. Frozen BEFORE the sink is written, because the plan's own rule is that the
# sink's landed/committed decision must be argued against what ships today rather than against what
# whoever writes it remembers. Each cell asserts TODAY's values and names, in its docstring, which of
# them 1.5 is EXPECTED to move and which must not move at all.
#
# A golden that goes red under the sink is not a regression by itself — it is the prompt for an argued
# diff in that PR. A golden that goes red for a field marked MUST NOT MOVE is a regression.

async def test_GOLDEN_the_first_precheck_exit(tmp_path, monkeypatch) -> None:
    """The idempotency precheck says the write was already done, before any attempt runs.

    EXPECTED TO MOVE under 1.5: `usage` (today `{}` — RunRecord's docstring says usage is "always
    populated and always carries cost_usd", and this exit contradicts it) and `mode` (today `""`,
    which is not one of the documented modes).

    MUST NOT MOVE: `ok` True, `attempts` 0 (no attempt ran), and landed/committed staying None —
    the precheck is evidence that SOMETHING committed on an earlier run, never evidence about this one.
    """
    spec, cache = _seed(tmp_path, monkeypatch, name="goldpc1", mutating=True,
                        mutate=MutateSpec(confirm_text_contains="done",
                                          precheck_text_contains="already"))
    eng = fe.FakeEngine(precheck=True).install(monkeypatch)
    rec = RunRecord()

    out = await replay(spec, cache=cache, record=rec)

    assert out == {"status": "already-done", "data": None}
    assert eng.precheck_calls == 1 and len(eng.calls) == 0, "premise: no attempt ran"
    # MUST NOT MOVE
    assert rec.ok is True
    assert rec.attempts == 0
    assert (rec.landed, rec.committed) == (None, None)
    # MOVED AT 1.5, as this cell predicted, and both moves are the ones it named:
    #   mode  ""  -> "precheck"  — `""` is not one of the documented modes, and the run's outcome was
    #                             produced by the precheck, so that is what names it.
    #   usage {}  -> a priced zero — `RunRecord` promises usage is "always populated and always
    #                             carries cost_usd", and this exit contradicted it. Nothing COULD have
    #                             been spent (the routers are not even resolved yet), so zero is not a
    #                             guess: it is the only value the evidence supports.
    assert rec.mode == "precheck"
    assert rec.usage.get("cost_usd") == 0.0 and rec.usage.get("calls") == 0


async def test_GOLDEN_the_post_refresh_precheck_exit(tmp_path, monkeypatch) -> None:
    """One attempt failed, auth was refreshed, and the precheck then says the write is done.

    MUST NOT MOVE: landed/committed None. The first attempt's write MAY have landed — this exit's own
    comment says so — so a False here would be the confident denial M4 forbids, and
    `_forget_negative_write_evidence` exists to stop exactly that.

    EXPECTED TO MOVE under 1.5: nothing required. `mode` is the first attempt's, which is defensible;
    if the sink changes it, the diff must say why.
    """
    spec, cache = _seed(tmp_path, monkeypatch, name="goldpc2")
    _with_login(spec)
    eng = fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="session expired", usage=USAGE)),
        precheck=[False, True],
    ).install(monkeypatch)
    rec = RunRecord()

    out = await replay(spec, cache=cache, record=rec)

    assert out == {"status": "already-done", "data": None}
    assert eng.refresh_calls == 1 and eng.precheck_calls == 2, "premise: the refresh + 2nd precheck ran"
    # MUST NOT MOVE
    assert (rec.landed, rec.committed) == (None, None), (
        "a post-refresh precheck must not deny a write the first attempt may have landed")
    assert rec.ok is True and rec.failure_code == ""
    assert rec.auth_refreshed is True
    assert rec.attempts == 1
    # MOVED AT 1.5, and this cell said the diff would have to be argued. `mode` was "replay" — the
    # mode of the attempt that FAILED — and is now "precheck", the thing that actually produced the
    # outcome. The sink takes the last fact, and on this path the last fact is the precheck. A field
    # naming "what produced this answer" must not name the step that did not.
    assert rec.mode == "precheck"
    assert rec.usage.get("cost_usd") == 0.0


async def test_GOLDEN_the_retry_raise_then_repair_path(tmp_path, monkeypatch) -> None:
    """Attempt 1 drifts; the auth-refresh retry RAISES; the suffix-replan then succeeds.

    This is the path the plan singles out, and it is the one whose golden 1.5 is expected to CHANGE.

    Today: `landed=False, committed=False` on a run in which an attempt raised at an unknown point.
    The critic's rule for the sink is "True if any attempt evidenced True; else None if any attempt is
    unknown (raised, precheck-skip, relearn-raise); else False" — under which this becomes None. That
    change is a strict improvement (a raised attempt cannot support a denial) and it is written down
    HERE, before the sink, so it lands as an argued diff rather than as an unnoticed side effect.

    Also visible and NOT frozen as correct: `ok=True` beside `failure_code='drift'`, which is R4.57 on
    a third path. Its xfail cell is the one that must flip; this cell records that the same shape
    reaches here too.

    MUST NOT MOVE: ok True, attempts 3, and the returned data.
    """
    spec, cache = _seed(tmp_path, monkeypatch, name="goldretry", extract="value", approved=False)
    _with_login(spec)
    eng = fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="drifted", usage=USAGE)),
        fe.Attempt(raises=RuntimeError("the retry attempt blew up")),
        fe.Attempt(report=fe.report(mode="replay+replan", usage=USAGE, llm_calls=2),
                   out={"found": True, "data": {"v": 1}}),
    ).install(monkeypatch)
    rec = RunRecord()

    data = await replay(spec, cache=cache, record=rec, on_drift="relearn",
                        provider=eng.provider, router=eng.router)

    assert data == {"v": 1}
    assert len(eng.calls) == 3 and eng.refresh_calls == 1, "premise: three attempts, one refresh"
    # MUST NOT MOVE
    assert rec.ok is True
    assert rec.attempts == 3
    assert rec.llm_calls == 2
    # MOVED AT 1.5, exactly as predicted and for the stated reason: an attempt RAISED at an unknown
    # point, so this run has no basis for a denial. `False` here was the confident denial M4 forbids —
    # it survived because the old success block wrote its OWN attempt's value over the run's.
    assert (rec.landed, rec.committed) == (None, None), (
        "a run in which an attempt raised must report the write question as unanswerable")
    # And R4.57's shape is gone from this path too: `ok` and `failure_code` are now decided once, at
    # the single exit, from whether `replay()` raised — so a successful run cannot carry the code of
    # an attempt that failed on the way.
    assert rec.ok is True and rec.failure_code == ""


async def test_R4_46_a_usage_less_attempt_erases_nothing(tmp_path, monkeypatch) -> None:
    """A priced attempt followed by one that reported no usage at all.

    BEFORE 1.5 this was a silent erasure: `_absorb_usage` merged each attempt's self-reported dict and
    was sticky-None on `cost_usd`, so an attempt whose report carried no usage key (R4.45's shape)
    turned a correctly-priced total into `None` with NOTHING on the record saying why —
    indistinguishable from a genuinely unpriced model, and the earlier attempt's spend gone with it.

    THE SINK FIXES IT BY CONSTRUCTION rather than by a flag. Usage comes from one run-scoped watch, so
    an attempt that reports nothing contributes nothing to the ANSWER: there is no merge, and so
    nothing to erase. This cell therefore asserts the outcome (the spend survives) and the invariant
    that has to hold either way — an unknown cost always says why — instead of the old mechanism.
    """
    spec, cache = _seed(tmp_path, monkeypatch, name="r446")
    _with_login(spec)
    eng = fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="session expired",
                                    usage=dict(USAGE, calls=1, cost_usd=0.25))),
        fe.Attempt(report=fe.report(usage=None)),      # no `extra` at all — R4.45's shape
    ).install(monkeypatch)
    rec = RunRecord()

    await replay(spec, cache=cache, record=rec, router=eng.router)

    assert rec.attempts == 2 and rec.ok is True, "premise: both attempts ran and the retry succeeded"
    assert rec.usage.get("calls") == 1, (
        f"the first attempt's call was erased by an attempt that reported no usage; got "
        f"{rec.usage.get('calls')}")
    reasons = {k: rec.usage.get(k) for k in COST_UNKNOWN_REASONS}
    assert rec.usage.get("cost_usd") is not None or any(reasons.values()), (
        f"the run is unpriceable and the record does not say why: {reasons} — a caller cannot "
        f"distinguish it from a genuinely unpriced model")


async def test_the_population_block_reaches_the_record_on_a_FAILED_run(tmp_path, monkeypatch) -> None:
    """R4.47's class, closed browser-free rather than by strengthening one browser cell.

    `tests/test_flows.py`'s FAILED-path cell asserts `ok is False` (the default), a truthy `mode` (the
    M4 pre-stamp writes one before the engine runs) and a truthy `failure_code` — so an engine that
    populated only the SUCCESS return would keep it green while dropping everything B1 exists to
    deliver. Measured at 1.5's first step: mutations removing `record.total_ms +=` and
    `record.traces.extend(...)` SURVIVED the entire exit matrix.

    So this asserts the whole population block on a run that FAILS, with values that can only come
    from the report: a duration, the traces, an idempotency key lifted out of trace meta, and the
    heal count.
    """
    spec, cache = _seed(tmp_path, monkeypatch, name="popfail")
    traces = [fe.trace(0, ms=11.0, idempotency_key="idem-1"), fe.trace(1, ms=7.5)]
    fe.FakeEngine(fe.Attempt(report=fe.report(success=False, note="drifted", usage=USAGE,
                                              llm_calls=4, healed_steps=2, traces=traces))
                  ).install(monkeypatch)
    rec = RunRecord()

    with pytest.raises(FlowReplayError):
        await replay(spec, cache=cache, record=rec)

    assert rec.ok is False and rec.failure_code, "premise: this run failed"
    assert rec.total_ms == pytest.approx(18.5), (
        f"the report's duration did not reach the record on a failure (got {rec.total_ms})")
    assert len(rec.traces) == 2, "a caller diagnosing a failure is exactly who needs the traces"
    assert rec.llm_calls == 4
    assert rec.healed_steps == 2
    assert rec.idempotency_keys == ["idem-1"], (
        "the key that would be re-used on a resume must be recorded on the failing run too")
    assert rec.usage.get("cost_usd") == 0.0


# ===================================================================================================
# The two properties the SINK owns that nothing else does. Both were found by mutants surviving the
# matrix at 1.5, which is the whole reason `tests/mutations/b1_wiring.py` is re-expressed rather than
# retired: the class it measures moved, it did not go away.

def test_the_fold_decides_landed_by_evidence_and_never_by_position() -> None:
    """STICKY, tested on the fold itself rather than through a page that cannot exist.

    The integration route was tried first and is UNREACHABLE by construction, which is worth recording:
    for a write flow, an attempt that lands makes `_auth_retry_allowed` refuse the retry (correctly — a
    second run would double-submit), so "attempt 1 landed, attempt 2 got nowhere" has no page state
    behind it. Scripting one anyway would be a cell asserting a fiction.

    The property is arithmetic over facts, so it is tested as arithmetic. Found by
    `evidence_true_does_not_win` SURVIVING the matrix.
    """
    def fold(*facts):
        rec = RunRecord()
        sink = flows_mod._RecordSink(rec)
        for f in facts:
            sink.attempt(f)
        sink.finish(None)
        return rec

    F = flows_mod._AttemptFacts
    # TRUE WINS, whatever came after it. A write evidenced as landed cannot be un-landed by a later
    # attempt that failed earlier — the ledger would otherwise skip a row that really was paid.
    assert fold(F(outcome="failed", landed=True), F(outcome="failed", landed=False)).landed is True
    assert fold(F(outcome="failed", landed=True), F(outcome="raised")).landed is True
    # UNKNOWN BEATS FALSE. A raise, a precheck skip and a relearn that raised all leave the question
    # unanswerable, and a confident denial over a write that may have committed is the one error
    # direction nothing downstream catches.
    for unknown in ("raised", "precheck", "relearn_raised"):
        got = fold(F(outcome="failed", landed=False), F(outcome=unknown)).landed
        assert got is None, f"{unknown} left the record claiming {got!r} rather than unknown"
    # FALSE only when every fact is a completed attempt that saw nothing.
    assert fold(F(outcome="failed", landed=False), F(outcome="ok", landed=False)).landed is False
    # And no facts at all is unknown, not "no".
    assert fold().landed is None


async def test_a_broken_sink_reports_itself_and_never_replaces_the_real_failure(
    tmp_path, monkeypatch
) -> None:
    """TOTAL. `finish()` runs inside `replay()`'s `except` arm, so it must not raise.

    If it did, the caller would be told about an accounting bug instead of about the drift that
    actually ended their run — a diagnostic destroying the thing it reports on. The failure goes to
    `record.note` instead, which is the only field on `RunRecord` that exists for this.

    Found by `finish_is_not_total` SURVIVING the matrix: nothing drove a sink that breaks.
    """
    spec, cache = _seed(tmp_path, monkeypatch, name="sinkboom")
    fe.FakeEngine(fe.Attempt(report=fe.report(mode="miss", success=False,
                                              note="no cached flow"))).install(monkeypatch)

    def _boom(self, field_name):
        raise ZeroDivisionError("the sink itself is broken")

    monkeypatch.setattr(flows_mod._RecordSink, "_evidence", _boom)
    rec = RunRecord()

    with pytest.raises(FlowReplayError) as exc:
        await replay(spec, cache=cache, record=rec)

    assert "learn" in str(exc.value).lower(), (
        "the caller was told about the SINK's failure instead of the replay's — the diagnostic "
        "replaced the outcome")
    assert "ZeroDivisionError" in rec.note, (
        f"a sink that could not complete the record must SAY so on the record; note={rec.note!r}")


def test_every_record_write_is_inside_the_sink() -> None:
    """CONTAINMENT — the invariant 1.5 actually buys, derived rather than remembered.

    The ratchet in `scripts/ratchets.py` counts `record.<field> =` statements and can only shrink; it
    went 16 -> 14, which understates the change, because the number was never the problem. The problem
    was that the sixteen were spread across `replay()` and `_attempt_replay`, on paths that each had to
    remember to write, to clear what an earlier path wrote, and to not write a denial they had no
    evidence for. Every one of B1's ten findings is a consequence of that spread.

    MATCHED BY FIELD NAME, NOT BY THE VARIABLE'S NAME. The first draft matched only targets whose base
    was literally `record`, which an audit showed is the SAME failure mode as the ratchet it replaces —
    the ratchet read ZERO because a local had been renamed `rec`. Measured then: `record.landed = True`
    was caught while `sink._record.landed = True`, `rec = sink._record; rec.landed = True` and
    `setattr(sink._record, 'landed', True)` all slipped through. It now keys on `RunRecord`'s own field
    set, whatever the base expression is, and flags `setattr` on anything that is not `self`.
    """
    fields = set(RunRecord.__dataclass_fields__)
    assert len(fields) >= 10, "RunRecord lost its fields — this guard would pass vacuously"

    tree = ast.parse(pathlib.Path("src/ultracua/flows.py").read_text(encoding="utf-8"))
    sink = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.ClassDef) and n.name == "_RecordSink"), None)
    assert sink is not None, "the sink is gone — this guard would pass vacuously"
    inside = {id(n) for n in ast.walk(sink)}

    # EXCEPTION VARIABLES ARE NOT RECORDS, and `landed` is a field on BOTH `RunRecord` and
    # `FlowReplayError`. `exc.landed = True` at the ledger-arming site (R3.3's consumer, which the plan
    # requires to stay byte-identical) is a write to the EXCEPTION. Derived from the tree — every name
    # bound by an `except ... as <name>` — rather than allowlisted, so a handler renamed tomorrow is
    # still understood.
    exception_names = {h.name for h in ast.walk(tree)
                       if isinstance(h, ast.ExceptHandler) and h.name}

    outside, n_inside = [], 0
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        flat = [e for t_ in targets for e in (t_.elts if isinstance(t_, ast.Tuple) else [t_])]
        for t_ in flat:
            if isinstance(t_, ast.Attribute) and t_.attr in fields:
                if isinstance(t_.value, ast.Name) and t_.value.id in exception_names:
                    continue
                if id(node) in inside:
                    n_inside += 1
                else:
                    outside.append(f"flows.py:{node.lineno} writes .{t_.attr} on "
                                   f"{ast.unparse(t_.value)}")
        # `setattr(x, 'landed', ...)` is the same write wearing a string. Allowed only on `self`,
        # which is how a dataclass or a helper legitimately configures itself.
        if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "setattr"
                and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in fields
                and not (isinstance(node.args[0], ast.Name)
                         and node.args[0].id in {"self"} | exception_names)
                and id(node) not in inside):
            outside.append(f"flows.py:{node.lineno} setattr(..., {node.args[1].value!r}, ...)")

    assert not outside, (
        "a RunRecord field is written outside `_RecordSink`, which re-creates the spread 1.5 removed:\n"
        + "\n  ".join(outside))
    assert n_inside >= 10, (
        f"only {n_inside} record writes found inside the sink — the walk is broken, so the assertion "
        f"above would pass however the code changed")


# ===================================================================================================
# WHAT THE TWO ADVERSARIAL AUDITS OF 1.5 FOUND. Every cell below exists because a green suite, a green
# `prove_red 16/16` and six green ratchets did NOT catch the defect it pins. They are grouped here
# rather than filed beside their neighbours so the next reader can see the shape of what an audit
# catches that a matrix does not.

async def test_AUDIT_a_relearn_success_leaves_the_write_question_UNANSWERABLE(
    tmp_path, monkeypatch
) -> None:
    """THE HIGH ONE. A relearn that SUCCEEDS used to fold as answerable-and-no.

    A relearn is a live LLM authoring run against a page that has demonstrably drifted, and discovery
    can actuate a write — `LearnResult.performed_write` exists for exactly that, and `_learn` refuses
    to cache a flow whose write it could not attribute. So a completed relearn licenses no denial.

    Before the sink this came from `_mark_ok` -> `_forget_negative_write_evidence`. Deleting those
    helpers deleted the property, and `test_relearn_full_success_absorbs_the_learn_spend` asserts
    `ok`, `mode`, `usage`, `llm_calls` and `total_ms` but NEVER `landed` — so a fourth golden moved,
    in the opposite direction to the one the slice argued, and nothing noticed. Measured against main:
    `None/None` there, `False/False` here.
    """
    spec, cache = _seed(tmp_path, monkeypatch, name="auditrelearn", approved=False)
    lr = LearnResult(spec=spec, cached=True, steps=[], data={"n": 1}, found=True, note="",
                     performed_write=True,          # discovery ACTUATED a write on the drifted page
                     report=fe.report(mode="learn", usage=dict(USAGE)))
    eng = fe.FakeEngine(
        fe.Attempt(report=fe.report(success=False, note="drift", usage=USAGE)),
        fe.Attempt(report=fe.report(success=False, note="replan failed", usage=USAGE)),
        learn=lr,
    ).install(monkeypatch)
    rec = RunRecord()

    data = await replay(spec, cache=cache, on_drift="relearn", provider=eng.provider,
                        router=eng.router, record=rec)

    assert data == {"n": 1} and eng.learn_calls == 1, "premise: the relearn ran and succeeded"
    assert (rec.landed, rec.committed) == (None, None), (
        "a relearn that may have actuated a write during discovery reported a confident denial — the "
        "one error direction nothing downstream catches")


async def test_AUDIT_a_raise_after_the_engine_returns_still_records_the_attempt(
    tmp_path, monkeypatch
) -> None:
    """The attempt's facts must survive a raise ANYWHERE in the attempt, not just in `run_cached`.

    The first draft wrapped only the engine call, so the ~130 lines after it — the write-evidence
    block, the shape gate, the H9 contract checks, `_capture_audit` — could raise with no fact
    appended. The record then said a run that fully executed one engine attempt made ZERO attempts,
    with its traces and its Idempotency-Keys gone: R4.45's own family, one window over.

    Driven by making the shape gate raise, which is inside that window and after the engine returned.
    """
    spec, cache = _seed(tmp_path, monkeypatch, name="auditraise", extract={"n": "a number"})
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    _update_meta(cache, key, lambda m: setattr(m, "shape", {"n": "int"}), on_unreadable="raise")
    traces = [fe.trace(0, ms=9.0, idempotency_key="idem-CRITICAL")]
    eng = fe.FakeEngine(fe.Attempt(report=fe.report(usage=USAGE, llm_calls=4, traces=traces),
                                   out={"found": True, "data": {"n": 1}})).install(monkeypatch)

    def _boom(*a, **kw):
        raise ZeroDivisionError("something in the gate window")

    monkeypatch.setattr(flows_mod, "_shape_matches", _boom)
    rec = RunRecord()

    with pytest.raises(ZeroDivisionError):
        await replay(spec, cache=cache, record=rec, router=eng.router)

    assert len(eng.calls) == 1, "premise: the engine ran and returned before the raise"
    assert rec.attempts == 1, (
        f"the record says {rec.attempts} attempt(s) for a run that fully executed one — the operator "
        f"reads 'nothing ran' over a run whose write step may have POSTed")
    assert rec.idempotency_keys == ["idem-CRITICAL"], (
        "the key a resume must re-use was dropped on the raise path, which is the path that most "
        "needs it")
    assert rec.llm_calls == 4 and rec.traces, "the attempt's spend and traces went with it"
    assert (rec.landed, rec.committed) == (None, None), "and the write question stays unanswerable"


def test_AUDIT_the_cross_check_sums_the_attempts_rather_than_comparing_each(tmp_path) -> None:
    """N half-blind attempts must not report a confident, understated bill.

    Each report is ONE attempt's router delta and the watch spans the whole run, so blindness is
    `sum(reported) > watch`. The first draft compared each attempt INDIVIDUALLY against the run total,
    which detects only the single-attempt case — with two attempts spending comparably neither exceeds
    the total alone. Both audits reproduced the miss; the cell that was supposed to pin it used one
    attempt, i.e. exactly the shape where max == sum.
    """
    from ultracua.flows import RunRecord as _RR
    from ultracua.flows import _AttemptFacts, _RecordSink

    class _R:
        def __init__(self):
            from ultracua.obs import UsageTotals
            self.totals = UsageTotals()

    router = _R()
    rec = _RR()
    sink = _RecordSink(rec)
    sink.arm(None, router, "mock-1")
    # The watch sees three calls; the two attempts between them report six. Neither report exceeds
    # three on its own, so a per-attempt comparison sees nothing wrong.
    for _ in range(3):
        router.totals.add(types.SimpleNamespace(input_tokens=1, output_tokens=1,
                                                cache_read_tokens=0, cache_write_tokens=0), "mock-1")
    sink.attempt(_AttemptFacts(outcome="failed"))
    sink.cross_check_usage({"calls": 3})
    sink.attempt(_AttemptFacts(outcome="ok"))
    sink.cross_check_usage({"calls": 3})
    sink.finish(None)

    assert rec.usage["cost_usd"] is None, (
        f"six reported calls against a watch that saw three was priced at "
        f"{rec.usage['cost_usd']!r} — a confident number for half the run's spend")
    assert rec.usage.get("watch_missed_a_router") is True


def test_AUDIT_a_broken_fold_leaves_usage_UNKNOWN_not_empty() -> None:
    """`finish()`'s own failure must not re-open R4.45.

    `usage` was assigned LAST, so any earlier failure left the dataclass default `{}` — the exact
    shape R4.45 was filed for, against `RunRecord`'s promise that usage is always populated and always
    carries `cost_usd`. The existing totality cell asserts the note and the caller's exception, so a
    half-written record passed it.
    """
    from ultracua.flows import RunRecord as _RR
    from ultracua.flows import _AttemptFacts, _RecordSink

    # ARM 1 — the usage computation ITSELF fails, so nothing ever computed a cost. This is the R4.45
    # shape: without the guard the record keeps the dataclass default `{}`.
    rec = _RR()
    sink = _RecordSink(rec)
    sink.attempt(_AttemptFacts(outcome="ok"))
    sink._usage = lambda: (_ for _ in ()).throw(ZeroDivisionError("boom in _usage"))
    sink.finish(None)

    assert "ZeroDivisionError" in rec.note, "premise: the fold really did break"
    assert rec.usage.get("cost_usd") is None and rec.usage.get("unobserved_llm_path") is True, (
        f"a record whose cost was never computed must say UNKNOWN, not carry an empty dict that "
        f"reads as 'no usage'; usage={rec.usage!r}")

    # ARM 2 — a LATER step fails, after usage was computed successfully. The genuine number must
    # survive: forcing unknown here would throw away a measurement the run really made.
    rec2 = _RR()
    sink2 = _RecordSink(rec2)
    sink2.attempt(_AttemptFacts(outcome="ok"))
    sink2._evidence = lambda *_: (_ for _ in ()).throw(ZeroDivisionError("boom after usage"))
    sink2.finish(None)

    assert "ZeroDivisionError" in rec2.note
    # ATOMIC, since the audit round:  computes every value BEFORE assigning any, so a failure
    # anywhere in the fold leaves the record wholly untouched rather than half this run and half the
    # last one. On a fresh record that means defaults; on a REUSED one it is what stops an earlier
    # call's landed=True sitting beside this call's verdict, which is a false arm.
    assert rec2.usage.get("cost_usd") is None and rec2.usage.get("unobserved_llm_path") is True
    assert (rec2.attempts, rec2.mode, rec2.replay_calls) == (0, "", 0), (
        f"the fold was not atomic — some fields landed before it failed: attempts={rec2.attempts}, "
        f"mode={rec2.mode!r}, replay_calls={rec2.replay_calls}")


def test_AUDIT_a_stale_note_does_not_survive_onto_a_healthy_record() -> None:
    """`note` was the one field `_write` never wrote, so it had to be remembered — the shape the sink
    exists to remove, one field over from the sixteen it removed."""
    from ultracua.flows import RunRecord as _RR
    from ultracua.flows import _AttemptFacts, _RecordSink

    rec = _RR()
    rec.note = "the run record could not be completed: from an earlier, broken fold"
    sink = _RecordSink(rec)
    sink.attempt(_AttemptFacts(outcome="ok", mode="replay"))
    sink.finish(None)

    assert rec.ok is True and rec.note == "", (
        f"a healthy run carries a stale failure note: {rec.note!r}")


def test_AUDIT_finish_runs_exactly_once() -> None:
    """The slice's headline claim — "a wrapper whose only job is calling `finish()` exactly once" — had
    no cell, and the mutation removing the `_finished` latch SURVIVED both test files.

    Defensive today (the wrapper cannot re-enter), which is precisely why it needs a cell: nothing
    else would notice the guarantee disappearing.
    """
    from ultracua.flows import DriftError
    from ultracua.flows import RunRecord as _RR
    from ultracua.flows import _AttemptFacts, _RecordSink

    rec = _RR()
    sink = _RecordSink(rec)
    sink.attempt(_AttemptFacts(outcome="ok", mode="replay"))
    sink.finish(None)
    assert rec.ok is True and rec.failure_code == ""

    sink.finish(DriftError("a second verdict, from a second call"))
    assert rec.ok is True and rec.failure_code == "", (
        "a second `finish()` overwrote the first with a different exception's verdict")


async def test_AUDIT_reuse_replaces_and_says_how_many_calls_wrote_the_record(
    tmp_path, monkeypatch
) -> None:
    """The cell `docs/reshape-plan.md` lists in 1.5's acceptance column and the slice did not deliver.

    ONE RECORD DESCRIBES ONE CALL. The pre-1.5 code accumulated across calls as a side effect of
    `_absorb_usage`, whose own comment scopes accumulation to the up-to-three ATTEMPTS inside one call;
    cross-call summing was a leak of that mechanism, never a contract.

    Replacement is chosen over accumulation and over a refusal for the failure DIRECTION, not for
    tidiness — see the fold-level cell below, which drives the shape that decides it. Here: the record
    describes the LAST call, and `replay_calls` says how many wrote it, which is the detection that
    makes a refusal unnecessary and is what B3's `record_disagrees` bucket keys on.
    """
    spec, cache = _seed(tmp_path, monkeypatch, name="reuse1")
    fe.FakeEngine(fe.Attempt(report=fe.report(usage=USAGE, llm_calls=5))).install(monkeypatch)
    rec = RunRecord()
    await replay(spec, cache=cache, record=rec)
    assert (rec.ok, rec.llm_calls, rec.replay_calls) == (True, 5, 1), "premise: call #1 succeeded"

    spec2, cache2 = _seed(tmp_path, monkeypatch, name="reuse2")
    fe.FakeEngine(fe.Attempt(raises=RuntimeError("the second run blew up"))).install(monkeypatch)
    with pytest.raises(RuntimeError):
        await replay(spec2, cache=cache2, record=rec)

    assert rec.ok is False and rec.failure_code == "raised", "the record must describe the LAST call"
    assert rec.llm_calls == 0 and rec.attempts == 1, (
        f"the counters are summed across calls ({rec.llm_calls} decides, {rec.attempts} attempts) — "
        f"accumulation was an attempt-scoped mechanism leaking across calls")
    assert rec.replay_calls == 2, (
        f"the record does not say it was written twice ({rec.replay_calls}), so a caller reading only "
        f"the last call's facts has no way to notice")


def test_AUDIT_a_reused_record_never_keeps_an_earlier_calls_landed_TRUE() -> None:
    """THE shape that decided replacement over both alternatives, driven at the fold.

    It cannot be driven through `replay()`: reaching a record-level `landed=True` needs the recipe's
    mutating steps to be evidenced as run-ok, which the fake does not model — `WriteReadbackError`'s
    `landed=True` is a CLASS default, not this run's evidence. So the arithmetic is tested as
    arithmetic, as with the sticky-evidence rule.

    Call #1 evidenced a landed write. Call #2 raises. Accumulating — and equally a "refuse" that leaves
    call #1's facts in place — reports `landed=True` for a run that raised: a FALSE ARM, where a resume
    skips a row it cannot know was paid, and CLAUDE.md is explicit that nothing downstream catches it.
    Replacement reaches `landed=None`, which is the direction this codebase survives.
    """
    from ultracua.flows import _AttemptFacts, _RecordSink

    rec = RunRecord()
    first = _RecordSink(rec)
    first.attempt(_AttemptFacts(outcome="failed", landed=True, committed=True))
    first.finish(None)
    assert rec.landed is True and rec.replay_calls == 1, "premise: call #1 evidenced a landed write"

    second = _RecordSink(rec)
    second.attempt(_AttemptFacts(outcome="raised", mode="raised"))
    second.finish(RuntimeError("boom"))

    assert rec.landed is None and rec.committed is None, (
        f"an earlier call's landed={True} survived onto a run that RAISED — the false arm that makes "
        f"a resume skip a row it cannot know was paid. Got landed={rec.landed!r}")
    assert rec.replay_calls == 2
