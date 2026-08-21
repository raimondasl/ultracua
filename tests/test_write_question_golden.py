"""What the RECORD says about the write, per failure kind — committed BEFORE 1.4b.

reshape-plan step 1.4b closes **R4.61**: `write_unverified` records `committed=False` while its own
message says the commit ACTUATED. The mechanism is that `_fail` appends `outcome="failed"` for EVERY
failure kind, and `"failed"` is not in `_UNKNOWN_OUTCOMES` — so `_RecordSink._evidence` answers the
write question "no" for the one kind whose entire meaning is *"it fired and cannot be confirmed"*.

Unanswerability is keyed on the outcome STRING, and every failure exit shares one string. That is why
no per-kind test could have caught this: there is nothing per-kind to look at.

THIS FILE IS THE EVIDENCE. Every kind is driven through the REAL `_replay_body` via
`tests/_fake_engine.py` — not by constructing `_AttemptFacts` by hand, which would assert my reading of
`_fail` against itself. Committed GREEN against unmodified `src/`, so the diff to `EXPECTED` in the
1.4b PR is the argued record of exactly which kinds changed answer.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import tests._fake_engine as fe
from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key
from ultracua.flows import FlowReplayError, FlowSpec, MutateSpec, RunRecord, approve, replay, save_spec
from ultracua.locators import LocatorSpec

pytestmark = pytest.mark.asyncio

# `test_the_two_outcome_allowlists...` is sync; the module-level asyncio mark warns on it. Marked
# individually below instead of loosening the module mark, which every async cell here needs.

USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
         "cache_write_tokens": 0, "cost_usd": 0.0}


def _seed(tmp_path, monkeypatch, *, name, mutating=False, mutate=None, extract=None, shape=None):
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
    approve(spec, cache=cache)
    if shape is not None:
        # The shape gate compares against `meta.shape`, which only a LEARN sets. Without it the gate is
        # skipped entirely and the run SUCCEEDS — which the first draft of the `shape` row discovered
        # as "DID NOT RAISE" rather than as a silent pass, because the cell demands the raise.
        from ultracua import flows as _F
        _F._update_meta(cache, key, lambda m: setattr(m, "shape", shape), on_unreadable="raise")
    return spec, cache


# label -> (seed kwargs, the Attempt that produces that failure kind)
def _ok_trace():
    """A trace saying the recipe's step 0 RAN AND SUCCEEDED.

    Load-bearing, and its absence is what made the first draft of this golden wrong. `landed` is
    `out["write_landed"] and all_writes_ok`, where `all_writes_ok` joins the recipe's mutating step
    indices against the traces that reported `ok`. A report with NO traces therefore denies every
    landing — so a scripted write with no trace records `landed=False` while its own exception
    declares `landed=True`, which is a fiction of the harness rather than a fact about `src/`.
    (`test_replay_exit_matrix`'s own write-readback cell scripts no trace either; it asserts only
    `exc.landed`, so it never had to notice.)
    """
    return fe.trace(0, ok=True)


def _cases():
    confirm = MutateSpec(confirm_text_contains="done")
    return {
        # `report.mode == "miss"` — no learned flow. The FIRST thing `_attempt_replay_body` checks.
        "miss": (dict(name="k_miss"),
                 lambda: fe.Attempt(report=fe.report(mode="miss", success=False, note="no flow",
                                                     usage=USAGE))),
        # an interstitial / CAPTCHA wall.
        "escalate": (dict(name="k_esc"),
                     lambda: fe.Attempt(report=fe.report(mode="escalate", success=False,
                                                         note="captcha", usage=USAGE))),
        # ordinary locator/page drift — the do-not-retry default.
        "drift": (dict(name="k_drift"),
                  lambda: fe.Attempt(report=fe.report(success=False, note="page drift", usage=USAGE))),
        # THE ONE R4.61 IS ABOUT. A declared write whose commit ACTUATED and whose confirm was already
        # true before it ran, so nothing on the page can distinguish landed from pre-existing.
        "write_unverified": (dict(name="k_wu", mutating=True, mutate=confirm),
                             lambda: fe.Attempt(report=fe.report(usage=USAGE, traces=[_ok_trace()]),
                                                out={"found": False, "confirm_pre_true": True,
                                                     "error": "confirm was already true"})),
        # the write CONFIRMED and only its readback missed — the side effect is CERTAIN.
        "write_unreadable": (dict(name="k_wr", mutating=True, mutate=confirm,
                                  extract={"ref": "the reference"}),
                             lambda: fe.Attempt(report=fe.report(usage=USAGE, traces=[_ok_trace()]),
                                                out={"found": True, "write_landed": True,
                                                     "extract_found": False,
                                                     "extract_error": "not on the page",
                                                     "data": None})),
        # a READ whose extracted structure moved.
        "shape": (dict(name="k_shape", extract={"total": "the total"},
                       shape={"t": "object", "keys": ["total"]}),
                  lambda: fe.Attempt(report=fe.report(usage=USAGE),
                                     out={"found": True, "data": [1, 2]})),
    }


# (record.ok, record.landed, record.committed, record.attempts, record.failure_code, exc.landed)
#
# CAPTURED AT 0.111.0, BEFORE 1.4b. Read the `committed` column against the kind's meaning:
# `write_unverified` means "the commit fired and cannot be confirmed", and the record says False —
# a DENIAL, not an "unknown". That single cell is R4.61.
EXPECTED = {
    "miss":             (False, False, False, 1, "not_learned",      False),
    "escalate":         (False, False, False, 1, "escalate",         False),
    "drift":            (False, False, False, 1, "drift",            False),
    # 1.4b: `committed` False -> None. The commit ACTUATED and the page cannot report on it, so the
    # record says UNKNOWN rather than denying it. `landed` and `exc.landed` stay False, deliberately:
    # `landed` asks "may a resume SKIP this row" and the arming token asks "may the ledger record it",
    # and `ledger.py`'s invariant is "never a false skip of an un-landed write".
    "write_unverified": (False, False, None,  1, "write_unverified", False),
    "write_unreadable": (False, True,  True,  1, "write_readback",   True),
    "shape":            (False, False, False, 1, "shape_drift",      False),
}


@pytest.mark.parametrize("label", sorted(_cases()))
async def test_the_record_answers_the_write_question_as_the_golden_says(
    label, tmp_path, monkeypatch
) -> None:
    """THE GOLDEN, driven through the real `_replay_body`."""
    seed_kw, attempt = _cases()[label]
    spec, cache = _seed(tmp_path, monkeypatch, **seed_kw)
    eng = fe.FakeEngine(attempt()).install(monkeypatch)

    rec = RunRecord()
    with pytest.raises(FlowReplayError) as ei:
        await replay(spec, cache=cache, record=rec)

    # PREMISE FIRST: the engine really ran, and the kind really is the one this row names. Without
    # this, a case that refused at pre-flight would produce a row of Falses and read as a pass.
    assert len(eng.calls) == 1, (
        f"{label}: the engine ran {len(eng.calls)} time(s) — this row is not exercising its kind")

    got = (rec.ok, rec.landed, rec.committed, rec.attempts, rec.failure_code, ei.value.landed)
    assert got == EXPECTED[label], (
        f"{label}: the record now says {got}, the committed golden says {EXPECTED[label]}.\n"
        f"  columns: (ok, landed, committed, attempts, failure_code, exc.landed)\n"
        f"If 1.4b moved it, update the table IN THE SAME DIFF and state the direction — a False "
        f"becoming None is a WIDENING (safe); a None becoming False is a NARROWING and is the error "
        f"direction nothing downstream catches.")


async def test_the_write_unverified_row_no_longer_denies_a_commit_it_asserts(
    tmp_path, monkeypatch
) -> None:
    """R4.61, CLOSED — and this cell replaces the one that held the defect still.

    The exception says the commit actuated; the record now agrees that it cannot say otherwise. The
    two write fields answer DIFFERENT questions and keep different values, which is the whole shape:

      `record.committed`  did ANYTHING commit          -> None, because it may have
      `record.landed`     may a resume SKIP this row   -> False, because not every write is evidenced
      `exc.landed`        may the LEDGER record it     -> False; a maybe is a no
    """
    seed_kw, attempt = _cases()["write_unverified"]
    spec, cache = _seed(tmp_path, monkeypatch, **seed_kw)
    fe.FakeEngine(attempt()).install(monkeypatch)

    rec = RunRecord()
    with pytest.raises(FlowReplayError) as ei:
        await replay(spec, cache=cache, record=rec)

    assert ei.value.code == "write_unverified", "premise: this is the kind under test"
    assert "confirm" in str(ei.value).lower(), "premise: the message is about the confirm, not a drift"
    assert rec.committed is None, (
        "the record must not DENY a commit its own exception says actuated")
    assert rec.landed is False and ei.value.landed is False, (
        "...and the two ARMING questions must keep their conservative answers — widening either one "
        "would let a resume skip a row that may never have been paid")


@pytest.mark.asyncio(loop_scope=None)
async def test_the_write_question_is_no_longer_keyed_on_the_OUTCOME_STRING() -> None:
    """THE MECHANISM OF R4.61, DISPOSED OF — pinned so the string-keyed shape cannot come back.

    `_UNKNOWN_OUTCOMES` listed the outcomes after which the write question was unanswerable, and
    `_evidence` consulted it. But EVERY failure exit appends the same string, `"failed"`, so one
    string had to answer for a `write_unverified` and an ordinary drift alike. No per-kind test could
    have caught that — there was nothing per-kind to look at.

    `_evidence` now keys on the FIELD, and `_AttemptFacts` defaults both write fields to `None`, so an
    append site that states nothing is UNKNOWN rather than a denial. That is the safe direction by
    construction rather than by remembering to add a string to a set.
    """
    import ast
    import inspect

    from ultracua import flows as F

    assert not hasattr(F, "_UNKNOWN_OUTCOMES"), (
        "`_UNKNOWN_OUTCOMES` is back. If the write question is keyed on the outcome string again, "
        "R4.61 is back with it — every failure exit shares one string.")
    assert F._ENGINE_OUTCOMES, "the attempts allowlist is gone; `record.attempts` counts nothing"

    # `inspect.getsource` on a METHOD returns it still indented, which `ast.parse` refuses. Dedent it.
    src = inspect.getsource(F._RecordSink._evidence)
    read = {n.attr for n in ast.walk(ast.parse(__import__("textwrap").dedent(src)))
            if isinstance(n, ast.Attribute)}
    assert "outcome" not in read, (
        "`_evidence` reads `f.outcome` again — the write question must key on the FIELD, so a fact "
        "that does not know says so itself instead of being classified by which exit produced it")

    # ...and the safe DEFAULT is what makes a silent append site unknown rather than denied.
    fields = {f.name: f for f in __import__("dataclasses").fields(F._AttemptFacts)}
    for name in ("landed", "committed"):
        assert fields[name].default is None, (
            f"`_AttemptFacts.{name}` no longer defaults to None. An append site that states nothing "
            f"would then DENY the write rather than leave it unknown — the narrowing direction.")
