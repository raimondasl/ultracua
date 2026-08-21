"""What every `BatchRowResult` says about the write — as a committed table, captured BEFORE 1.4b.

reshape-plan step 1.4b closes **R4.52**: `BatchRowResult.landed` is a two-state bool answering a
three-state question, and it reads `False` on rows where that is not merely uninformative but
CONTRADICTED by the row's own other fields — a successfully confirmed write, and a row that crashed
after the POST.

THIS FILE IS THE EVIDENCE, and it is committed GREEN against unmodified `src/` first. There is no
other observer: `row.landed` has **zero readers** in `src/`, `tests/`, `scripts/` and `benchmarks/`;
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
                              code="not_approved", retryable=False, landed=False),
    # 2. duplicate-row refusal: two rows minting the same Idempotency-Key. Carries NO code today.
    "invalid_duplicate": dict(index=1, status="invalid", ok=False, error="duplicate of row 0"),
    # 3a. dry-run preview of a row that would run.
    "planned": dict(index=2, status="planned", ok=True, idempotency_keys=["sha:a"]),
    # 3b. dry-run preview of a row the ledger says already committed.
    "resumed_dryrun": dict(index=3, status="resumed", ok=True, idempotency_keys=["sha:b"]),
    # 4. halt-skip: an earlier row failed under on_row_error="stop". Never reached a browser.
    "halt_skipped": dict(index=4, status="skipped", ok=False, error="skipped — an earlier row failed"),
    # 5. live resume: the ledger says this row committed on a PRIOR run, so it was not re-fired.
    "resumed_live": dict(index=5, status="resumed", ok=True, idempotency_keys=["sha:c"],
                         error="already committed on a prior run (resume) — not re-fired"),
    # 6. the ok row. `data` is replay()'s return: for a declared write, {"status": ..., "data": ...}.
    "ok_write_confirmed": dict(index=6, status="ok", ok=True, ms=1.0, idempotency_keys=["sha:d"],
                               data={"status": "confirmed", "data": {"order": "A-1"}}),
    "ok_write_already_done": dict(index=7, status="ok", ok=True, ms=1.0, idempotency_keys=["sha:e"],
                                  data={"status": "already-done", "data": None}),
    "ok_read": dict(index=8, status="ok", ok=True, ms=1.0, data={"total": 42}),
    # 7. typed failure, with the exception's evidence copied onto the row.
    "failed_typed_unlanded": dict(index=9, status="failed", ok=False, ms=1.0, error="drift",
                                  idempotency_keys=["sha:f"], code="drift", retryable=False,
                                  landed=False),
    "failed_typed_landed": dict(index=10, status="failed", ok=False, ms=1.0, error="readback missed",
                                idempotency_keys=["sha:g"], code="write_readback", retryable=False,
                                landed=True),
    # 8. the crash row — a non-taxonomy exception. Carries NO code and NO evidence today.
    "failed_crash": dict(index=11, status="failed", ok=False, ms=1.0,
                         error="RuntimeError: browser died", idempotency_keys=["sha:h"]),
}

# (status, code, retryable, landed) as the row reports it. CAPTURED AT 0.111.0, BEFORE 1.4b.
#
# Read the `landed` column against the `status`/`data` column beside it. Four rows are the finding:
#   * `ok_write_confirmed`     the write CONFIRMED and the row says landed=False
#   * `ok_write_already_done`  an idempotency precheck skipped it; False is a denial, not "unknown"
#   * `resumed_live`           the LEDGER caused this skip and the row says the write did not land
#   * `failed_crash`           it crashed after the POST may have fired and the row says False
EXPECTED = {
    "invalid_preflight":     ("invalid", "not_approved", False, False),
    "invalid_duplicate":     ("invalid", "", False, False),
    "planned":               ("planned", "", False, False),
    "resumed_dryrun":        ("resumed", "", False, False),
    "halt_skipped":          ("skipped", "", False, False),
    "resumed_live":          ("resumed", "", False, False),
    "ok_write_confirmed":    ("ok", "", False, False),
    "ok_write_already_done": ("ok", "", False, False),
    "ok_read":               ("ok", "", False, False),
    "failed_typed_unlanded": ("failed", "drift", False, False),
    "failed_typed_landed":   ("failed", "write_readback", False, True),
    "failed_crash":          ("failed", "", False, False),
}


def _report(kwargs) -> tuple:
    row = BatchRowResult(**kwargs)
    return (row.status, row.code, row.retryable, row.landed)


def test_the_table_covers_every_construction_site() -> None:
    """The premise. A golden that has quietly stopped covering a site is worse than none — it reads as
    coverage while the site it lost is exactly where a value moved."""
    import ast

    src = Path(__file__).parents[1] / "src" / "ultracua" / "flows.py"
    tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
    sites = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "BatchRowResult"]
    assert len(sites) == 8, (
        f"`run_batch` constructs BatchRowResult at {len(sites)} site(s), not 8: {sites}. A site was "
        f"added or removed — add its row to SITES/EXPECTED before changing anything else.")
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
    """The only escape `row.landed` has is `cli.py`'s `asdict(report)` into a file, so the golden is
    worth exactly what that round-trip preserves. Printed so `-s` shows the table a reviewer reads."""
    print("\n  site                     status    code             retryable  landed")
    for label in sorted(SITES):
        status, code, retryable, landed = _report(SITES[label])
        print(f"    {label:<24} {status:<9} {code or '-':<16} {retryable!s:<10} {landed!s}")
        blob = json.loads(json.dumps(dataclasses.asdict(BatchRowResult(**SITES[label]))))
        assert blob["landed"] == landed and blob["code"] == code, (
            f"{label}: the field does not survive `asdict` -> JSON, which is the ONLY way it leaves "
            f"the process")


def test_the_ok_write_row_contradicts_itself_today() -> None:
    """R4.52 IN ONE CELL, and it is the reason this file exists.

    The row's `data` says the write was CONFIRMED. The row's `landed` says it did not land. One object,
    two fields, opposite answers — and `landed` is the one a machine reads.

    This cell asserts the CONTRADICTION, deliberately, so it goes red the moment 1.4b fixes it. That is
    the opposite of the usual mistake (a cell asserting a counterexample as correct behaviour): here
    the docstring says plainly that the assertion is a bug being recorded, not a property being kept.
    """
    row = BatchRowResult(**SITES["ok_write_confirmed"])
    assert row.data["status"] == "confirmed", "premise: this row is a confirmed write"
    assert row.landed is False, (
        "the ok-write row no longer contradicts itself — if 1.4b did that, DELETE this cell and say so; "
        "it exists only to hold the defect still while the fix is written")
