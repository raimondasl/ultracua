"""The tier manifest's re-derivation cost, and the trade that would make it cheaper.

Every PR that changes the test collection needs a FULL `--store-browser-marks` run (~32 min) plus the
fixed-point loop (~1 min). The obvious cheaper design — merge new ids in rather than rewrite the file —
is a ONE-WAY trade with a SILENT failure mode, so it is decided by data rather than by how annoying the
wait feels on the day. `scripts/manifest_cost.py` collects the data; this file keeps every part of it
armed, because an instrument nobody has seen fail is a claim.

Four cells here exist specifically to stop the three ways this could be theatre:

* the classifier could report a constant (cell 2 changes the input and watches the number move);
* the rule could be unable to say YES, which is as useless as one that cannot say no (cell 3 reaches
  both answers);
* the pipeline could never call the recorder at all — S14's `no_llm` stub was inert twice past its own
  review — so cell 5 drives the REAL `pytest_sessionfinish` and watches a record land, and arms the
  negative by asserting nothing lands without `--store-browser-marks`;
* and the whole analysis rests on a PREMISE — that merging would lose de-classification detection but
  NOT deletion detection, because deletion is caught by a different, cheap guard. Cell 6 arms that
  guard rather than trusting the sentence.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import _tiers as tier  # noqa: E402
import manifest_cost  # noqa: E402


@contextlib.contextmanager
def _restored(mod, per_test, collected):
    """Swap `_tiers.PER_TEST` / `COLLECTED` IN PLACE and put them back before any fixture teardown."""
    saved_per_test, saved_collected = dict(mod.PER_TEST), list(mod.COLLECTED)
    if per_test is not None:
        mod.PER_TEST.clear()
        mod.PER_TEST.update(per_test)
    mod.COLLECTED[:] = collected
    try:
        yield
    finally:
        mod.PER_TEST.clear()
        mod.PER_TEST.update(saved_per_test)
        mod.COLLECTED[:] = saved_collected


# ---------------------------------------------------------------------------------------------------
# 1. The ledger is real data in the shape the report reads.

def test_the_ledger_parses_and_every_record_is_schema_shaped() -> None:
    """Anti-vacuity first: an empty ledger means the wiring never fired, which is the failure mode."""
    records = manifest_cost.read_ledger()
    assert records, (
        f"{manifest_cost.LEDGER.name} is empty — no re-derivation has been measured, so the report's "
        f"cost half is unarmed. It fills itself: `pytest -q --store-browser-marks` then "
        f"`python scripts/derive_test_tiers.py`."
    )
    for n, r in enumerate(records, 1):
        assert r["phase"] in manifest_cost.PHASES, f"record {n}: unknown phase {r['phase']!r}"
        assert isinstance(r["seconds"], (int, float)) and r["seconds"] > 0, f"record {n}: bad seconds"
        assert isinstance(r["collected"], int) and r["collected"] > 0, f"record {n}: bad collected"
        assert isinstance(r["commit"], str) and r["commit"], f"record {n}: no commit"
        assert isinstance(r["when"], str) and r["when"], f"record {n}: no timestamp"


def test_a_corrupt_ledger_is_loud_rather_than_skipped(tmp_path: Path) -> None:
    bad = tmp_path / "ledger.jsonl"
    bad.write_text('{"phase": "marks"}\nnot json at all\n', encoding="utf-8")
    with pytest.raises(ValueError, match="is not JSON"):
        manifest_cost.read_ledger(bad)


# ---------------------------------------------------------------------------------------------------
# 2. The classifier counts; it does not return a constant.

def test_the_move_classifier_counts_every_direction_and_is_not_a_constant() -> None:
    prev_browser = {"a::x", "b::y"}
    prev_fast = {"c::z"}
    browser = {"a::x", "c::z"}     # c::z promoted fast -> browser
    fast = {"b::y", "d::w"}        # b::y DE-CLASSIFIED browser -> fast; d::w is new

    moved = manifest_cost.classify_moves(prev_browser, prev_fast, browser, fast)
    assert moved["new"] == 1, moved
    assert moved["gone"] == 0, moved
    assert moved["declassified"] == ["b::y"], moved
    assert moved["promoted"] == ["c::z"], moved

    # ARM IT: the same call with b::y left in the browser list must report ZERO de-classifications, or
    # the number above was a constant rather than a measurement.
    still = manifest_cost.classify_moves(prev_browser, prev_fast, browser | {"b::y"}, fast - {"b::y"})
    assert still["declassified"] == [], still
    assert still["new"] == 1, still

    # And a deletion is seen as a deletion, not as a move.
    deleted = manifest_cost.classify_moves(prev_browser, prev_fast, {"a::x"}, {"c::z"})
    assert deleted["gone"] == 1 and deleted["declassified"] == [], deleted


# ---------------------------------------------------------------------------------------------------
# 3. The rule can reach BOTH answers.

def _delta(declassified=()):
    d = manifest_cost.Delta(rev="x", date="d", subject="s", total=1)
    d.declassified = list(declassified)
    return d


def test_the_verdict_can_reach_both_answers() -> None:
    """A rule that can only ever say "no" is not a rule — it is the conclusion, pre-written.

    So all three corners are driven: the tax alone is not enough, a clean history alone is not enough,
    and both together DO flip it to yes.
    """
    first = manifest_cost.Delta(rev="0", date="d", subject="first", total=1)
    expensive = [{"phase": "marks", "seconds": 5 * 3600}]
    cheap = [{"phase": "marks", "seconds": 60}]

    yes = manifest_cost.verdict([first, _delta(), _delta()], expensive)
    assert yes.switch is True, yes.reasons

    dirty = manifest_cost.verdict([first, _delta(["t::a"]), _delta()], expensive)
    assert dirty.switch is False, dirty.reasons
    assert "1 de-classification" in " ".join(dirty.reasons)

    free = manifest_cost.verdict([first, _delta(), _delta()], cheap)
    assert free.switch is False, free.reasons


def test_only_the_marks_phase_argues_that_the_tax_is_real() -> None:
    """The fixed-point loop is ~1 minute and is NOT what makes re-derivation expensive; counting it
    toward the threshold would let a cheap phase argue for a one-way trade."""
    first = manifest_cost.Delta(rev="0", date="d", subject="first", total=1)
    loops_only = [{"phase": "fixed_point", "seconds": 9 * 3600}]
    assert manifest_cost.verdict([first, _delta()], loops_only).switch is False

    plus_marks = loops_only + [{"phase": "marks", "seconds": 5 * 3600}]
    assert manifest_cost.verdict([first, _delta()], plus_marks).switch is True


# ---------------------------------------------------------------------------------------------------
# 4. The deltas really come from git.

def test_the_delta_table_is_derived_from_git_and_finds_the_known_history() -> None:
    shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
    assert shallow == "false", (
        "this clone is SHALLOW, so `git log --follow` sees one manifest revision and the whole delta "
        "half of the report is silently empty. Reproduced against a real depth-1 clone: 1 revision "
        "instead of 4. CI needs `fetch-depth: 0` on its checkout; locally, `git fetch --unshallow`. "
        "Loud rather than skipped, because a skip here reads as 'the history is clean'."
    )
    deltas = manifest_cost.deltas_from_git()
    assert len(deltas) >= 4, "git should know at least the four manifest revisions to date"
    assert all(d.total > 0 for d in deltas)
    # The one event the whole trade turns on: a revision that de-classified three of the tier's own
    # arming cells. If this ever stops being findable, the analysis in the script's docstring is stale.
    assert any(d.declassified for d in deltas[1:]), (
        "no de-classification anywhere in the manifest's history — the docstring's central measured "
        "claim no longer holds and the merge-mode trade must be re-argued"
    )


# ---------------------------------------------------------------------------------------------------
# 5. The pipeline is WIRED to the recorder — driven, not asserted about.

class _FakeConfig:
    def __init__(self, store: bool):
        self._store = store

    def getoption(self, name):
        return {"--store-browser-marks": self._store, "--tier": "all"}[name]


class _FakeSession:
    def __init__(self, store: bool):
        self.config = _FakeConfig(store)


def test_a_marks_run_records_its_own_cost(tmp_path: Path, monkeypatch) -> None:
    """Drives the REAL `pytest_sessionfinish`, so this fails if the ledger call is ever dropped.

    Everything it writes is redirected into tmp_path: the cell must not append to the committed ledger
    or touch the committed manifest.
    """
    import conftest  # the root conftest — the wiring under test

    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(manifest_cost, "LEDGER", ledger)
    monkeypatch.setattr(tier, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(tier, "OFFENDERS_FILE", tmp_path / "offenders.json")
    monkeypatch.setattr(conftest, "_STARTED", [0.0])

    # PER_TEST and COLLECTED are restored IN THE BODY, in place, rather than by monkeypatch. This cell
    # runs inside the very session whose marks are being recorded, and the tier's autouse fixture writes
    # this test's own launch count into PER_TEST during teardown — so a restore that happens at fixture
    # teardown, in an order this file does not control, could drop this test from the manifest and make
    # the NEXT run refuse it as unclassified. Mutating in place and restoring here cannot lose that.
    with _restored(tier, {"m.py::a": 1, "m.py::b": 0}, ["m.py::a", "m.py::b"]):
        conftest.pytest_sessionfinish(_FakeSession(store=True), 0)

    records = manifest_cost.read_ledger(ledger)
    assert len(records) == 1, f"the marks phase recorded {len(records)} timings, expected exactly 1"
    assert records[0]["phase"] == "marks"
    assert records[0]["collected"] == 2
    assert records[0]["browser"] == 1 and records[0]["fast"] == 1
    assert records[0]["seconds"] > 0

    # ARM THE NEGATIVE: a normal run writes no manifest, so it must write no timing either. Without
    # this, a recorder that fired unconditionally would look identical above and would fill the ledger
    # with 1100 rows a day, drowning the number the rule reads.
    with _restored(tier, {"m.py::a": 1}, ["m.py::a"]):
        conftest.pytest_sessionfinish(_FakeSession(store=False), 0)
    assert len(manifest_cost.read_ledger(ledger)) == 1, "a run without --store-browser-marks recorded"


def test_the_fixed_point_loop_records_its_own_cost(tmp_path: Path, monkeypatch) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import derive_test_tiers

    ledger = tmp_path / "ledger.jsonl"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"total": 7, "browser": [], "fast": []}), encoding="utf-8")
    monkeypatch.setattr(manifest_cost, "LEDGER", ledger)
    monkeypatch.setattr(tier, "MANIFEST", manifest)

    derive_test_tiers._record(started=0.0, rounds=2, promoted=5)

    records = manifest_cost.read_ledger(ledger)
    assert len(records) == 1 and records[0]["phase"] == "fixed_point"
    assert records[0]["rounds"] == 2 and records[0]["promoted"] == 5
    assert records[0]["collected"] == 7


# ---------------------------------------------------------------------------------------------------
# 6. The PREMISE of the whole trade-off, armed rather than asserted.

def test_deletion_is_caught_by_a_different_guard_so_merging_would_only_lose_declassification(
    tmp_path: Path, monkeypatch
) -> None:
    """`scripts/manifest_cost.py`'s docstring claims merging would NOT weaken deletion detection.

    That claim is load-bearing — it is the reason the trade is priced as "loses de-classification only"
    rather than "loses two things" — and it rests on a guard in another file. So drive that guard with
    a manifest describing a test that no longer collects, and require it to fail.
    """
    from test_tiers import test_the_manifest_covers_everything_this_run_collected as guard

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "total": 2, "browser": ["gone.py::deleted_test"], "fast": ["live.py::a"],
    }), encoding="utf-8")
    monkeypatch.setattr(tier, "MANIFEST", manifest)

    with _restored(tier, None, ["live.py::a", "live.py::b"]):
        with pytest.raises(AssertionError, match="gone.py::deleted_test"):
            guard()

        # And it passes when nothing vanished, so the raise above is the guard working rather than the
        # fixture being malformed.
        tier.COLLECTED[:] = ["live.py::a", "gone.py::deleted_test"]
        guard()


# ---------------------------------------------------------------------------------------------------
# 7. The ledger accretes; it is never rewritten.

def test_the_ledger_is_append_only_against_its_committed_version() -> None:
    """A ledger that can be edited is a ledger that can be made to say the tax is smaller than it was."""
    proc = subprocess.run(["git", "show", f"HEAD:tests/{manifest_cost.LEDGER.name}"],
                          cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.skip("the ledger is not committed yet — nothing to compare against")
    committed = proc.stdout.splitlines()
    current = manifest_cost.LEDGER.read_text(encoding="utf-8").splitlines()
    assert current[:len(committed)] == committed, (
        "the committed ledger lines were edited or reordered; timings are appended, never revised"
    )


def test_the_report_runs_end_to_end() -> None:
    """The surface a human actually types. Fails on any exception in the derivation or the rule."""
    proc = subprocess.run([sys.executable, "scripts/manifest_cost.py", "report"],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERDICT:" in proc.stdout
    assert "UNMEASURED:" in proc.stdout, "the report must say how much of the history it cannot price"


def test_every_ci_job_that_runs_pytest_fetches_the_history_it_needs() -> None:
    """Derived from the workflow, not remembered: any job running pytest must have full history.

    The cell above fails loudly on a shallow clone; this one stops that failure from ever reaching CI,
    and stops a job ADDED tomorrow from reintroducing it.
    """
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    jobs, current = {}, None
    for line in text.splitlines():
        m = re.match(r"^  ([a-z][a-z0-9-]*):\s*$", line)
        if m:
            current = m.group(1)
            jobs[current] = []
        elif current:
            jobs[current].append(line)
    runners = {name: body for name, body in jobs.items() if any("pytest" in ln for ln in body)}
    assert runners, "no CI job runs pytest — this cell would pass vacuously"
    for name, body in runners.items():
        assert any("fetch-depth: 0" in ln for ln in body), (
            f"CI job {name!r} runs pytest without `fetch-depth: 0`, so `git log --follow` sees one "
            f"manifest revision and tests/test_manifest_cost.py's delta half goes red there"
        )
