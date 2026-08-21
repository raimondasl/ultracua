"""What every `BatchRowResult` says about the write — as a committed table, captured BEFORE 1.4b.

reshape-plan step 1.4b closes **R4.52**: `BatchRowResult.landed` was a two-state bool answering a
three-state question, and it read `False` on rows where that is not merely uninformative but
CONTRADICTED by the row's own other fields — a successfully confirmed write, and a row that crashed
after the POST.

**The field is `BatchRowResult.committed` now.** THREE fields were spelled `landed` and asked three
different questions — the exception's two-state ledger ARM, the record's "may a resume skip this row",
and this one's "did anything commit" — and this is the one that reaches `--json`. 1.4b changes its
ANSWER on four row kinds, so keeping the name would have handed every consumer a silently different
value under an unchanged key; the rename makes that a `KeyError` instead. Free exactly once, because
the field had no reader anywhere outside this file.

THIS FILE IS THE EVIDENCE, and it is committed GREEN against unmodified `src/` first. There is no
other observer: `row.committed` has **zero readers** in `src/`, `tests/`, `scripts/` and `benchmarks/`;
its only escape is `cli.py`'s `dataclasses.asdict(report)` into a `--json` file. Without this table
1.4b would change an unobserved wire contract with a green suite either side, which is the shape this
register keeps filing.

So the diff to `EXPECTED` in the same PR is the argued record of what the step changed, and every row
that moves has to be defended in the PR body.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from ultracua.flows import BatchRowResult

# (label, the kwargs `run_batch` actually passes at that site) -> what the row then reports.
#
# Transcribed from the eight construction sites by reading them, then DRIVEN through the real
# dataclass below rather than asserted from the reading — a table copied out of the source asserts the
# source against itself.
SITES = {
    # 1. pre-flight refusal: a typed `FlowReplayError` from `_preflight_row`, before any browser.
    "invalid_preflight": dict(index=0, status="invalid", ok=False, error="refused",
                              code="not_approved", retryable=False, committed=False),
    # 2. duplicate-row refusal: two rows minting the same Idempotency-Key. Carries NO code today.
    "invalid_duplicate": dict(index=1, status="invalid", ok=False, error="duplicate of row 0"),
    # 3a. dry-run preview of a row that would run.
    "planned": dict(index=2, status="planned", ok=True, idempotency_keys=["sha:a"]),
    # 3b. dry-run preview of a row the ledger says already committed.
    "resumed_dryrun": dict(index=3, status="resumed", ok=True, idempotency_keys=["sha:b"],
                           committed=True),
    # 4. halt-skip: an earlier row failed under on_row_error="stop". Never reached a browser.
    "halt_skipped": dict(index=4, status="skipped", ok=False, error="skipped — an earlier row failed"),
    # 5. live resume: the ledger says this row committed on a PRIOR run, so it was not re-fired.
    "resumed_live": dict(index=5, status="resumed", ok=True, idempotency_keys=["sha:c"],
                         committed=True,
                         error="already committed on a prior run (resume) — not re-fired"),
    # 6. the ok row. `data` is replay()'s return: for a declared write, {"status": ..., "data": ...}.
    "ok_write_confirmed": dict(index=6, status="ok", ok=True, ms=1.0, idempotency_keys=["sha:d"],
                               committed=True,
                               data={"status": "confirmed", "data": {"order": "A-1"}}),
    "ok_write_already_done": dict(index=7, status="ok", ok=True, ms=1.0, idempotency_keys=["sha:e"],
                                  data={"status": "already-done", "data": None}),
    "ok_read": dict(index=8, status="ok", ok=True, ms=1.0, data={"total": 42}),
    # 7. typed failure, with the exception's evidence copied onto the row.
    "failed_typed_unlanded": dict(index=9, status="failed", ok=False, ms=1.0, error="drift",
                                  idempotency_keys=["sha:f"], code="drift", retryable=False,
                                  committed=False),
    "failed_typed_landed": dict(index=10, status="failed", ok=False, ms=1.0, error="readback missed",
                                idempotency_keys=["sha:g"], code="write_readback", retryable=False,
                                committed=True),
    # 8. the crash row — a non-taxonomy exception. Carries NO code and NO evidence today.
    "failed_crash": dict(index=11, status="failed", ok=False, ms=1.0, code="raised", committed=None,
                         error="RuntimeError: browser died", idempotency_keys=["sha:h"]),
}

# (status, code, retryable, landed) as the row reports it. CAPTURED AT 0.111.0, BEFORE 1.4b.
#
# UPDATED BY 1.4b, and the four rows that moved were the finding:
#   * `ok_write_confirmed`     the write CONFIRMED and the row said landed=False   -> True
#   * `resumed_live` / `_dryrun`  the LEDGER caused the skip and the row denied it -> True
#   * `failed_crash`           no code at all, and a denial over a POST that may have fired
#                              -> ("raised", None)
#
# `ok_write_already_done` is UNREACHABLE from `run_batch` and so did not move: a declared write with a
# precheck is refused at pre-flight with `precheck_unsafe`, because `parameterizing = params is not
# None` and a batch always passes a dict per row. Measured; see the cell at the end of this file.
EXPECTED = {
    "invalid_preflight":     ("invalid", "not_approved", False, False),
    "invalid_duplicate":     ("invalid", "", False, False),
    "planned":               ("planned", "", False, False),
    "resumed_dryrun":        ("resumed", "", False, True),
    "halt_skipped":          ("skipped", "", False, False),
    "resumed_live":          ("resumed", "", False, True),
    "ok_write_confirmed":    ("ok", "", False, True),
    "ok_write_already_done": ("ok", "", False, False),
    "ok_read":               ("ok", "", False, False),
    "failed_typed_unlanded": ("failed", "drift", False, False),
    "failed_typed_landed":   ("failed", "write_readback", False, True),
    "failed_crash":          ("failed", "raised", False, None),
}


def _report(kwargs) -> tuple:
    row = BatchRowResult(**kwargs)
    return (row.status, row.code, row.retryable, row.committed)


def test_every_construction_site_consults_the_one_write_definition() -> None:
    """The premise, and the containment.

    READS THE MODULE UNDER TEST, not the repo path. `scripts/prove_red.py` installs a mutant by putting
    a COPY of `src/` first on `PYTHONPATH`, so a cell that opens
    `Path(__file__).parents[1] / "src" / ...` parses PRISTINE source and can never contribute a kill —
    a mutation whose only guard is such a cell is reported as a SURVIVOR, i.e. as a hole that is not
    there. `inspect.getsource` follows the import.

    And the count alone was too weak: it said nothing about what the sites PASS. Three of the eight
    took the dataclass DEFAULT rather than consulting `_row_write_evidence`, which left
    `_ROW_NEVER_RAN`'s `"skipped"` member with no production caller at all — the quiet-allowlist
    inversion, in the one place this slice added the allowlist.
    """
    import ast
    import inspect
    import textwrap

    from ultracua import flows as flows_under_test

    tree = ast.parse(textwrap.dedent(inspect.getsource(flows_under_test.run_batch)))
    sites = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "BatchRowResult"]
    planned = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_planned_row"]
    assert len(sites) + len(planned) == 8, (
        f"`run_batch` builds a row at {len(sites) + len(planned)} site(s), not 8. A site was added or "
        f"removed — add its row to SITES/EXPECTED before changing anything else.")

    missing = [n.lineno for n in sites
               if not any(kw.arg == "committed" for kw in n.keywords)]
    assert not missing, (
        f"BatchRowResult is built without `committed=` at line(s) {missing} of `run_batch`, so those rows "
        f"take the dataclass DEFAULT instead of consulting `_row_write_evidence`. One definition means "
        f"every site asks it — otherwise a class that can be raised there tomorrow is silently denied.")
    for n in sites:
        for kw in n.keywords:
            if kw.arg == "committed":
                expr = ast.unparse(kw.value)
                assert "_row_write_evidence" in expr, (
                    f"line {n.lineno} passes `committed={expr}` — a value decided somewhere other than "
                    f"the one definition")
    assert set(SITES) == set(EXPECTED), "SITES and EXPECTED have drifted apart"


@pytest.mark.parametrize("label", sorted(SITES))
def test_the_row_reports_what_the_golden_says(label) -> None:
    """THE GOLDEN. A diff here in the 1.4b PR is the point; a diff outside it is a regression."""
    got = _report(SITES[label])
    assert got == EXPECTED[label], (
        f"{label}: the row now reports {got}, the committed golden says {EXPECTED[label]}. If 1.4b "
        f"moved it, update the table IN THE SAME DIFF and argue the new value in the PR body — this "
        f"field reaches a `--json` report and has no other observer.")


def test_the_whole_table_is_printed_and_survives_the_json_round_trip() -> None:
    """The only escape `row.committed` has is `cli.py`'s `asdict(report)` into a file, so the golden is
    worth exactly what that round-trip preserves. Printed so `-s` shows the table a reviewer reads."""
    print("\n  site                     status    code             retryable  landed")
    for label in sorted(SITES):
        status, code, retryable, landed = _report(SITES[label])
        print(f"    {label:<24} {status:<9} {code or '-':<16} {retryable!s:<10} {landed!s}")
        blob = json.loads(json.dumps(dataclasses.asdict(BatchRowResult(**SITES[label]))))
        assert blob["committed"] == landed and blob["code"] == code, (
            f"{label}: the field does not survive `asdict` -> JSON, which is the ONLY way it leaves "
            f"the process")



# ===================================================================================================
# THE SAME QUESTION, DRIVEN THROUGH THE REAL `run_batch`.
#
# Everything above constructs `BatchRowResult(**kwargs)` by hand, which pins the DATACLASS. That is
# worth having — it is what proves `None` survives `asdict` into the `--json` wire — but it CANNOT SEE
# THE CONSTRUCTION SITE, and the construction site is what 1.4b changes.
#
# MEASURED, and it is why these cells exist: with `run_batch`'s ok-row mutated to pass `landed=True` —
# the exact change 1.4b makes — the fifteen cells above reported **15 passed**. A golden that cannot
# observe the edit it was written for is the third instrument in this programme to watch less than its
# docstring claimed.

import contextlib  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

import tests._fake_engine as fe  # noqa: E402
from ultracua import flows as flows_mod  # noqa: E402
from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key  # noqa: E402
from ultracua.flows import FlowSpec, MutateSpec, approve, run_batch, save_spec  # noqa: E402
from ultracua.locators import LocatorSpec  # noqa: E402


@contextlib.contextmanager
def _no_driver(monkeypatch):
    """`run_batch` holds one Playwright driver across the batch. Patched at the MODULE BINDING, which
    is the shape `tests/_fake_engine` uses and the only one that reaches `flows.py`'s own reference."""
    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr(flows_mod, "_acquire_driver", _noop)
    monkeypatch.setattr(flows_mod, "_release_driver", _noop)
    yield


def _write_flow(tmp_path, monkeypatch, *, name, approved=True):
    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    # NO SLOTS. The first draft declared one and every cell below refused at pre-flight with
    # `slot_unbound` — a slot must be BOUND to a recorded type/select step, and this recipe has only a
    # click. Caught by the premise assertions rather than by review: without them all four cells would
    # have passed over rows that never reached a browser.
    spec = FlowSpec(name=name, goal=f"g-{name}", start_url="http://fixture.invalid/",
                    mutate=MutateSpec(confirm_text_contains="done"))
    save_spec(spec)
    cache = FlowCache(root=tmp_path / "c")
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, url=spec.start_url,
                         created_ts=time.time(),
                         steps=[CachedStep(action="click", intent="pay", mutating=True,
                                           locator=LocatorSpec(role="button", name="Pay",
                                                               tag="button"))]))
    if approved:
        approve(spec, cache=cache)
    return spec, cache


@pytest.mark.asyncio
async def test_a_confirmed_write_row_no_longer_denies_its_own_commit(tmp_path, monkeypatch) -> None:
    """R4.52's NAMED GOLDEN: the ok-write row's value, driven rather than constructed.

    Before 1.4b this row carried `data={"status":"confirmed"}` and `landed=False` — one object, two
    fields, opposite answers, and `landed` is the one a machine reads.
    """
    spec, cache = _write_flow(tmp_path, monkeypatch, name="okw")
    fe.FakeEngine(fe.Attempt(report=fe.report(traces=[fe.trace(0, ok=True)]),
                             out={"found": True, "write_landed": True})).install(monkeypatch)
    with _no_driver(monkeypatch):
        run = await run_batch(spec, [{}], max_rows=1, cache=cache)

    assert len(run.rows) == 1 and run.rows[0].status == "ok", (
        f"premise: one ok row, got {[(r.status, r.error) for r in run.rows]}")
    row = run.rows[0]
    assert row.data["status"] == "confirmed", "premise: the write confirmed"
    assert row.committed is True, (
        "the confirmed-write row must not deny its own commit — this is the value R4.52 names")


@pytest.mark.asyncio
async def test_an_unverifiable_write_row_says_UNKNOWN_rather_than_no(tmp_path, monkeypatch) -> None:
    """R4.61 reaching the ROW. The commit actuated and the page cannot report on it, so the row must
    not print a denial into a `--json` report an operator acts on."""
    spec, cache = _write_flow(tmp_path, monkeypatch, name="wu")
    fe.FakeEngine(fe.Attempt(report=fe.report(traces=[fe.trace(0, ok=True)]),
                             out={"found": False, "confirm_pre_true": True,
                                  "error": "the confirm was already true"})).install(monkeypatch)
    with _no_driver(monkeypatch):
        run = await run_batch(spec, [{}], max_rows=1, on_row_error="continue", cache=cache)

    row = run.rows[0]
    assert (row.status, row.code) == ("failed", "write_unverified"), (
        f"premise: this row failed as write_unverified, got {(row.status, row.code)}")
    assert row.committed is None, (
        "a commit that FIRED and cannot be confirmed must read UNKNOWN, not `false` — `false` here is "
        "a denial an operator would act on")


@pytest.mark.asyncio
async def test_a_refusal_that_never_ran_still_says_NO_rather_than_unknown(
    tmp_path, monkeypatch
) -> None:
    """THE D0 HALF, and without it every cell above is satisfied by nulling everything.

    A row refused before any browser opened has EARNED its `False`. If these read `null` too, the
    field stops carrying information — `not_approved` becomes indistinguishable from
    `write_unverified`, which is the one distinction the tri-state exists to make.
    """
    spec, cache = _write_flow(tmp_path, monkeypatch, name="na", approved=False)
    with _no_driver(monkeypatch):
        run = await run_batch(spec, [{}], max_rows=1, cache=cache)

    assert run.status == "invalid" and run.rows[0].status == "invalid", (
        f"premise: the batch refused at pre-flight, got {run.status}")
    row = run.rows[0]
    assert row.code == "not_approved", f"premise: refused for approval, got {row.code!r}"
    assert row.committed is False, (
        "a row that never reached a browser must DENY the commit, not call it unknown")


@pytest.mark.asyncio
async def test_a_crashed_row_carries_a_code_and_an_unknown(tmp_path, monkeypatch) -> None:
    """The crash row reported `code=""` — indistinguishable in `--json` from the duplicate-row
    refusal, a different event entirely — and `landed=False` over a POST that may have fired."""
    spec, cache = _write_flow(tmp_path, monkeypatch, name="crash")
    fe.FakeEngine(fe.Attempt(raises=RuntimeError("the browser died"))).install(monkeypatch)
    with _no_driver(monkeypatch):
        run = await run_batch(spec, [{}], max_rows=1, cache=cache)

    row = run.rows[0]
    assert row.status == "failed" and "RuntimeError" in (row.error or ""), (
        f"premise: this row crashed, got {(row.status, row.error)}")
    assert row.code == "raised", (
        "a crash must name itself with the SAME word `RunRecord.failure_code` uses for it")
    assert row.committed is None, "a crash after the POST may have fired denies nothing"


# ---------------------------------------------------------------------------------------------------
# `_row_write_evidence`'s DECISION TABLE, as a unit.
#
# The four driven cells above are integration: they prove the sites call this, and they caught two
# mutants. They also SURVIVED two — the `attempts == 0` lowering clause and the `armed` raising clause
# — because neither is reachable from the four scenarios they happen to drive. A clause no cell can
# fail for is a clause that will rot, so the function's own table is asserted here, once, over every
# input combination it can be handed.

@pytest.mark.parametrize("label,record,status,outcome,ledger,want", [
    # the two RAISING clauses, which must beat everything below them
    ("ledger armed it",        None, "resumed", None, True, True),
    ("the exception armed it", None, "failed",
     flows_mod.Outcome(code="write_readback", retryable=False, armed=True), False, True),
    # ...and `armed` beats even a record that denies, because the ledger has already acted on it
    ("armed beats a denial",   "committed=False,attempts=1", "failed",
     flows_mod.Outcome(code="write_readback", retryable=False, armed=True), False, True),
    # the STATUS allowlist, for rows that never called replay()
    ("never ran: invalid",     None, "invalid", None, False, False),
    ("never ran: planned",     None, "planned", None, False, False),
    ("never ran: skipped",     None, "skipped", None, False, False),
    # QUIET IS AN ALLOWLIST — a status nobody has argued into it is UNKNOWN, not denied
    ("an unargued status",     None, "some_new_status", None, False, None),
    # the RECORD answers when this row ran
    ("record says yes",        "committed=True,attempts=1",  "ok",     None, False, True),
    ("record says no",         "committed=False,attempts=1", "ok",     None, False, False),
    ("record cannot say",      "committed=None,attempts=1",  "failed", None, False, None),
    # THE LOWERING CLAUSE: an unknown from a run where NO engine attempt happened is earned as False
    ("unknown, nothing ran",   "committed=None,attempts=0",  "failed", None, False, False),
])
def test_the_write_evidence_decision_table(label, record, status, outcome, ledger, want) -> None:
    """Every clause, driven. Its arming is the table itself — delete any clause and a row goes red."""
    rec = None
    if record is not None:
        rec = flows_mod.RunRecord()
        parts = dict(kv.split("=") for kv in record.split(","))
        rec.committed = {"True": True, "False": False, "None": None}[parts["committed"]]
        rec.attempts = int(parts["attempts"])
    got = flows_mod._row_write_evidence(rec, status, outcome, ledger_says_committed=ledger)
    assert got is want, f"{label}: got {got!r}, want {want!r}"


def test_the_lowering_clause_reads_a_MEASUREMENT_not_a_DECLARATION() -> None:
    """WHY `attempts == 0` AND NOT `can_follow_actuation`, pinned so the swap is not made back.

    Lowering an unknown to a confident denial is the NARROWING direction — the one nothing downstream
    catches. The obvious source for it is the exception's own `can_follow_actuation`, which declares
    whether the class can escape post-actuation. **R4.74 records that this axis has no sensor, and that
    two of its twenty-eight declarations were measurably wrong when 1.4a's audit checked them by hand.**

    So the clause reads a fact about THIS run instead. Measured, the two agree on today's population;
    where they would disagree the count is the safer one, because a class wrongly declared
    `can_follow_actuation=False` and raised after an attempt gets a confident `False` from the
    declaration and an honest `None` from the count.

    Structural, because the hazard is a future edit that "simplifies" this back to the attribute.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(flows_mod._row_write_evidence))
    read = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "attempts" in read, "the lowering clause no longer reads `record.attempts`"
    assert "can_follow_actuation" not in read, (
        "`_row_write_evidence` now reads `can_follow_actuation` — an axis R4.74 records as having no "
        "sensor and two known-wrong declarations. It must not decide a write DENIAL.")

    # ...and the measurement really does distinguish the population, or the clause is decoration.
    rec_ran, rec_didnt = flows_mod.RunRecord(), flows_mod.RunRecord()
    rec_ran.committed, rec_ran.attempts = None, 1
    rec_didnt.committed, rec_didnt.attempts = None, 0
    assert flows_mod._row_write_evidence(rec_ran, "failed") is None
    assert flows_mod._row_write_evidence(rec_didnt, "failed") is False


@pytest.mark.asyncio
async def test_a_row_whose_commit_landed_but_whose_tail_drifted_still_says_yes(
    tmp_path, monkeypatch
) -> None:
    """THE CELL THAT DECIDES WHICH RECORD FIELD THE ROW PROJECTS, and the only one where the two
    differ. Its absence let `row_projects_the_arming_token` survive the whole matrix.

    `RunRecord` answers two write questions and they are NOT the same:

      `committed`  did ANYTHING commit?             — the FIRST recipe write ran ok
      `landed`     may a resume SKIP this whole row? — EVERY recipe write ran ok

    A flow with two mutating steps whose first commits and whose second does not gives
    `committed=True, landed=False`. The row's published question is the first one, so it must read
    True. Projecting `landed` would print `false` on a row whose write demonstrably went through —
    and that row reaches an operator through `--json` beside `cli.py`'s advice to resume the rows that
    did not commit.
    """
    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    spec = FlowSpec(name="tail", goal="g-tail", start_url="http://fixture.invalid/",
                    mutate=MutateSpec(confirm_text_contains="done"))
    save_spec(spec)
    cache = FlowCache(root=tmp_path / "c")
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    loc = LocatorSpec(role="button", name="Pay", tag="button")
    cache.put(CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, url=spec.start_url,
                         created_ts=time.time(),
                         steps=[CachedStep(action="click", intent="pay", mutating=True, locator=loc),
                                CachedStep(action="click", intent="print receipt", mutating=True,
                                           locator=loc)]))
    approve(spec, cache=cache)

    # Step 0 commits and reports ok; step 1 FAILS. `write_landed` is still true — the confirm
    # transitioned — so `committed` is True while `landed` is False.
    #
    # `success=False` IS THE REACHABLE SHAPE, and the first draft of this cell scripted `success=True`
    # beside a failing trace. The real engine breaks on the first failing step and reports failure, so
    # that report cannot occur — and on a genuine `ok` row every step ran, which makes the two record
    # fields COINCIDE and the distinction this cell exists for vacuous. Found by 1.4b's audit; the fake
    # now refuses the impossible shape outright, so the mistake is not repeatable.
    def _attempt():
        return fe.Attempt(report=fe.report(success=False, note="the receipt step drifted",
                                           traces=[fe.trace(0, ok=True), fe.trace(1, ok=False)]),
                          out={"found": True, "write_landed": True})

    fe.FakeEngine(_attempt()).install(monkeypatch)
    with _no_driver(monkeypatch):
        run = await run_batch(spec, [{}], max_rows=1, on_row_error="continue", cache=cache)

    row = run.rows[0]
    assert (row.status, row.code) == ("failed", "drift"), (
        f"premise: the tail drifted after the commit, got {(row.status, row.code, row.error)}")

    # PREMISE, AND IT IS THE WHOLE POINT: the two record fields must actually disagree here, or this
    # cell is asserting a distinction it never created.
    probe = flows_mod.RunRecord()
    fe.FakeEngine(_attempt()).install(monkeypatch)
    with pytest.raises(flows_mod.FlowReplayError):
        await flows_mod.replay(spec, cache=cache, record=probe)
    assert (probe.committed, probe.landed) == (True, False), (
        f"premise: this scenario must make the two record fields DISAGREE, got "
        f"committed={probe.committed!r} landed={probe.landed!r} — without that the assertion below "
        f"cannot tell which one the row projected")

    assert row.committed is True, (
        "the row must project `committed` (did anything commit), not `landed` (may a resume skip the "
        "whole row). This write went through and only the tail drifted; printing `false` for it is the "
        "denial R4.52 is about, and it would sit beside `cli.py`'s advice to resume the rows that did "
        "not commit")
