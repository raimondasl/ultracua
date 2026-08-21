"""The ledger ARM's property, over the whole taxonomy — and BROWSER-FREE, which is load-bearing.

Split out of `tests/test_landed_arms_the_ledger.py` at 1.4b, and the reason is a CI failure this file
exists to make impossible to repeat. That file holds real-browser cells; this property is driven
entirely through `tests/_fake_engine` and a patched driver. `scripts/prove_red.py` names its killer
suite explicitly and the `red-proof` CI job deliberately does NOT install Playwright — so putting the
property in the browser file made every mutant's baseline run fail on a missing Chromium
(`8 failed, 135 passed`, both OS arms), while passing locally where Chromium happens to be installed.

That is CLAUDE.md's "a local green is weaker evidence than CI, in two measured ways" on the platform
axis, caught by CI on the first push. Anything added to the killer suite must be browser-free.

WHAT IT PINS. 1.4b put a TRI-STATE report (`_row_write_evidence`) three lines from the two-state
ledger ARM, and the first pin for that was an AST scan of the guard's own text. MEASURED, that scan is
green over:

    o_arm = outcome_of(exc)
    may_have_committed = _row_write_evidence(rec, "failed", o_arm)
    if (may_have_committed is not False and getattr(exc, "landed", False) is not None
            and ledger is not None and preview_keys[i]):
        ledger.record(...)

— the needle is still literally in the guard, no banned symbol appears in it, and the collapse the
scan's own docstring warned about ("would work until somebody wrote `is not False`") has happened. For
`write_unverified` the report is `None`, `None is not False`, the arm fires, a durable commit line is
written for a row nothing confirmed was paid, and the NEXT resume skips it forever.

A scan over text is the wrong sensor class for a behaviour. This drives the behaviour, over a
population DERIVED from the taxonomy — `__init_subclass__` makes `landed` total over every subclass,
so the set grows by itself.
"""

from __future__ import annotations

import pytest

import ultracua.flows as F

def _ledger_arm_flow(tmp_path, monkeypatch, name):
    """An approved single-mutating-step write flow with a resume ledger."""
    import time as _t

    from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key
    from ultracua.locators import LocatorSpec

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    spec = F.FlowSpec(name=name, goal=f"g-{name}", start_url="http://fixture.invalid/",
                      mutate=F.MutateSpec(confirm_text_contains="done"))
    F.save_spec(spec)
    cache = FlowCache(root=tmp_path / "c")
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, url=spec.start_url,
                         created_ts=_t.time(),
                         steps=[CachedStep(action="click", intent="pay", mutating=True,
                                           locator=LocatorSpec(role="button", name="Pay",
                                                               tag="button"))]))
    F.approve(spec, cache=cache)
    return spec, cache


# The two RECORD shapes a failing row can hand the arm, and BOTH must be governed by the exception.
#
# This axis is what makes the cell able to fail. Its first draft stubbed `replay` to raise and left the
# `RunRecord` pristine — `attempts=0`, which drives `_row_write_evidence` down its never-ran clause to
# a plain `False`. Under the mutation this cell exists to catch (`may_have_committed is not False`),
# `False is not False` is False, the arm does not fire, and the cell passed. MEASURED: 38 passed with
# the defect present. The instrument suppressed the defect, which is this register's own standing
# warning applied to the guard written for it.
#
# A real failed replay that ATTEMPTED reports `attempts=1, committed=None` — and `None is not False`.
_RECORD_SHAPES = {
    # nothing ran: a refusal from replay()'s own pre-flight, before any engine attempt
    "never_attempted": (0, False),
    # an attempt ran and could not answer: `write_unverified`, and every raised attempt
    "attempted_unknown": (1, None),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", sorted(_RECORD_SHAPES))
@pytest.mark.parametrize("code", sorted(F.REGISTRY))
async def test_the_ledger_records_a_row_IFF_the_exception_says_the_write_landed(
    code, shape, tmp_path, monkeypatch
) -> None:
    """THE ARM'S PROPERTY, over every class in the taxonomy.

    `ledger.py`'s invariant is "never a false skip of an un-landed write", and the ledger is durable —
    a line written here makes every future resume of this job skip the row. So the arm must fire on
    EXACTLY the classes that positively know the write committed, which is exactly the classes
    declaring `landed = True`. Today that is one (`WriteReadbackError`); the population is derived, so
    a class added tomorrow is covered without touching this file.

    Driven through the real `run_batch` and a real `RunLedger`, with `replay` raising the class — which
    is what makes it immune to a guard that merely mentions the right words.
    """
    cls = F.REGISTRY[code]
    attempts, committed = _RECORD_SHAPES[shape]
    spec, cache = _ledger_arm_flow(tmp_path, monkeypatch,
                                   f"arm{abs(hash((code, shape))) % 99999}")

    async def _boom(*a, **kw):
        # Populate the record the way a real `replay()` would before raising, because the ARM sits one
        # statement from a projection OF that record and the whole property is that it ignores it.
        rec = kw.get("record")
        if rec is not None:
            rec.attempts, rec.committed, rec.landed = attempts, committed, committed
        raise cls(f"{code}: scripted for the arming property")

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(F, "replay", _boom)
    monkeypatch.setattr(F, "_acquire_driver", _noop)
    monkeypatch.setattr(F, "_release_driver", _noop)

    run = await F.run_batch(spec, [{}], max_rows=1, on_row_error="continue", cache=cache,
                            resume="job-arm")
    assert len(run.rows) == 1 and run.rows[0].status == "failed", (
        f"premise: one failed row, got {[(r.status, r.error) for r in run.rows]}")
    assert run.rows[0].idempotency_keys, (
        "premise: the row must have minted a key, or the arm is unreachable and this cell is vacuous")

    from ultracua.ledger import RunLedger
    ledger = RunLedger.open(cache, F.flow_key(spec.goal, spec.start_url, spec.scope), "job-arm",
                            spec.scope)
    try:
        recorded = ledger.committed()
    finally:
        ledger.close()

    if cls.landed:
        assert recorded, (
            f"{code} declares `landed = True` and the ledger recorded NOTHING. A committed write left "
            f"unrecorded is re-fired by every subsequent resume (R3.3).")
    else:
        assert not recorded, (
            f"{code}/{shape} declares `landed = False` — a MAYBE — and the ledger recorded the row "
            f"anyway: {recorded}. That is a durable false skip: every future resume of this job "
            f"passes over a "
            f"row nothing confirmed was paid. `ledger.py`'s invariant is 'never a false skip of an "
            f"un-landed write', and it is the error direction nothing downstream catches.")
