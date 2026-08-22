"""Root conftest: `benchmarks` importable from tests, plus the suite-tier wiring.

Two reasons this lives at the ROOT rather than in `tests/`, and the second was measured the hard way:

1. pytest adds the directory of the root conftest.py to `sys.path`, which is what makes the top-level
   `benchmarks` package importable from a test.
2. **`pytest_addoption` is only honoured in the rootdir conftest.** A `tests/conftest.py` copy of it is
   picked up when the command names a path (`pytest tests/ --tier fast`) and silently NOT picked up when
   it relies on `testpaths` (`pytest --tier fast`) — which is the form CI runs. Measured: the latter died
   with `unrecognized arguments: --tier` while every unit test of the mechanism passed. The mechanism
   itself is in `tests/_tiers.py`; this file is only the wiring, and it is here because that is where
   pytest looks.

    pytest --tier fast                 # no Chromium, seconds, the pre-commit signal
    pytest                             # everything (the merge gate; what CI runs)
    pytest --store-browser-marks       # regenerate tests/.browser_tests.json (needs a FULL run)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "tests"))

import _tiers  # noqa: E402 - needs the path above

# Wall-clock of the marks phase, so the manifest's re-derivation cost is MEASURED rather than
# complained about. See `scripts/manifest_cost.py` for what the number is for.
_STARTED = [0.0]


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--tier", action="store", default="all", choices=("all", "fast", "browser"),
        help="all (default, the merge gate) | fast (no Chromium) | browser (only the browser tests)",
    )
    parser.addoption(
        "--store-browser-marks", action="store_true", default=False,
        help="record per-test browser launches and rewrite tests/.browser_tests.json (needs a FULL run)",
    )
    parser.addoption(
        "--emit-marks", action="store", default=None, metavar="PATH",
        help="write this run's OBSERVATION to PATH (never the manifest). Safe on a shard, safe on CI; "
             "merge parts with `python scripts/tier_marks.py merge`",
    )
    parser.addoption(
        "--update-truth-table", action="store_true", default=False,
        help="rewrite tests/goldens/auth_retry_truth.json from the current source. A diff there is a "
             "change to what may be RE-DRIVEN after an auth refresh — never regenerate to make a "
             "test pass; regenerate only once the new cells have been read",
    )


def pytest_configure(config) -> None:
    _STARTED[0] = time.monotonic()
    _tiers.scrub_provider_keys()
    _tiers.STATE["fast"] = config.getoption("--tier") == "fast"
    if not _tiers.install_probes():  # pragma: no cover - Playwright missing entirely
        raise pytest.UsageError("no Playwright entry point could be wrapped — the tier probe is inert")


def pytest_unconfigure(config) -> None:
    _tiers.uninstall_probes()


def pytest_collection_modifyitems(config, items) -> None:
    # Recorded BEFORE any tier filtering, and on every tier including the default, so a test can check
    # that the manifest classifies what THIS run collected. Without it a stale manifest is discovered
    # only by whoever next types `--tier fast` — the merge gate never calls `partition`.
    _tiers.COLLECTED[:] = [i.nodeid for i in items]

    tier = config.getoption("--tier")
    if tier == "all":
        return
    try:
        data = _tiers.load_manifest()
        keep, _drop = _tiers.partition([i.nodeid for i in items], tier, data)
    except (_tiers.UnclassifiedTests, FileNotFoundError) as exc:
        raise pytest.UsageError(str(exc)) from exc

    keep_set = set(keep)
    dropped = [i for i in items if i.nodeid not in keep_set]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = [i for i in items if i.nodeid in keep_set]


@pytest.fixture(autouse=True)
def _browser_launch_probe(request):
    """Attribute launches to the test that caused them, for `--store-browser-marks`."""
    before = _tiers.STATE["n"]
    _tiers.CURRENT[0] = request.node.nodeid
    yield
    _tiers.CURRENT[0] = None
    _tiers.PER_TEST[request.node.nodeid] = _tiers.STATE["n"] - before


def pytest_runtest_logreport(report) -> None:
    """Every id that produced a report ACTUALLY RAN, and the WORST outcome it reached.

    Two consumers, and the second is why the value is kept rather than just the key:
    `_tiers.guard_every_selected_test_ran` reads the key set; `_tiers.merge_observations` reads the
    outcome, because a test that died in setup reports and launches nothing — indistinguishable from
    "no longer needs a browser" if you only count launches.
    """
    outcome = "failed" if report.failed else ("skipped" if report.skipped else "passed")
    prev = _tiers.REPORTED.get(report.nodeid)
    if prev is None or _tiers._OUTCOME_RANK[outcome] > _tiers._OUTCOME_RANK[prev]:
        _tiers.REPORTED[report.nodeid] = outcome


def pytest_sessionfinish(session, exitstatus) -> None:
    _tiers.write_offenders()   # only when non-empty, i.e. only beside an already-red fast run

    # EMIT. Deliberately before, and independent of, the store path: an observation is evidence, not a
    # classification, so a SHARD may emit one even though it may never write the manifest. That
    # asymmetry is the whole design — the four CI shards each emit, and one cheap merge does what a
    # 36-minute local run used to.
    emit_to = session.config.getoption("--emit-marks")
    if emit_to:
      # NEVER allowed to redden a run. Measured: `--emit-marks` at an unwritable path raised out of
      # this hook, replaced the summary line with a traceback and exited 1 while ten tests had passed.
      # This flag is on all four merge-gate shard commands, so an emit failure must cost the marks and
      # nothing else. The ledger append below is wrapped for exactly this reason ("a ledger is not
      # worth failing a 32-minute suite for"), and the CI upload step carries `continue-on-error`.
      # The LOUD channel is the merge's own coverage clause, which refuses an incomplete set and names
      # the missing shard where a human can act on it.
      try:
        selected = [i.nodeid for i in session.items]
        label = os.environ.get("ULTRACUA_MARKS_LABEL") or f"local-{sys.platform}"
        record = _tiers.observation(
            collected=_tiers.COLLECTED, selected=selected, reported=_tiers.REPORTED,
            launches=_tiers.PER_TEST, exitstatus=exitstatus,
            seconds=time.monotonic() - _STARTED[0], label=label,
        )
        out = Path(emit_to)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=1, sort_keys=True) + chr(10), encoding="utf-8")
        print(f"{chr(10)}wrote observation {out} "
              f"({len(record['launches'])} launching of {len(selected)} selected, label={label!r})")
      except Exception as exc:  # pragma: no cover - a diagnostic never fails a run
        print(f"{chr(10)}(tier marks not emitted: {exc})")

    if not session.config.getoption("--store-browser-marks"):
        return
    if session.config.getoption("--tier") != "all":
        raise pytest.UsageError("--store-browser-marks needs a FULL run; drop --tier")
    # THREE sensors, because a "whole suite observation" can fail in three independent ways and two of
    # them used to be checked while the third was not: TIERED (above), UNDER-COLLECTED, and NARROWED by
    # deselection. `session.items` here is POST-deselection — measured: 1201 full, 626 under
    # `--splits 2 --group 1`, 22 under `-k tiers` — whereas `_tiers.COLLECTED` is recorded before any
    # filtering, so the pair is an exact narrowing detector that a merely SKIPPED test does not trip.
    selected = [i.nodeid for i in session.items]
    try:
        _tiers.guard_full_collection(len(_tiers.COLLECTED))
        _tiers.guard_not_narrowed(len(_tiers.COLLECTED), len(selected))
        _tiers.guard_every_selected_test_ran(selected, _tiers.REPORTED)
    except _tiers.PartialDerivation as exc:
        raise pytest.UsageError(str(exc)) from exc
    browser, fast = _tiers.write_manifest()
    print(f"\nwrote {_tiers.MANIFEST.name}: {browser} browser, {fast} fast")

    # RECORD WHAT IT COST. A full marks run is the expensive half of a re-derivation, and the cheaper
    # design that keeps suggesting itself (merge new ids in, never rewrite) trades away DE-CLASSIFICATION
    # detection for it. That trade is settled by data in `scripts/manifest_cost.py`, so the data is
    # collected here rather than remembered. Never allowed to redden a run that has already done its
    # work: a ledger is not worth failing a 32-minute suite for.
    try:
        from datetime import datetime, timezone

        sys.path.insert(0, str(Path(__file__).parent / "scripts"))
        import manifest_cost

        manifest_cost.append({
            "phase": "marks",
            "seconds": round(time.monotonic() - _STARTED[0], 1),
            "collected": len(_tiers.COLLECTED),
            "browser": browser,
            "fast": fast,
            "commit": manifest_cost.head_sha(),
            "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        print(f"recorded the re-derivation cost in {manifest_cost.LEDGER.name}")
    except Exception as exc:  # pragma: no cover - diagnostics never redden a run
        print(f"(manifest cost not recorded: {exc})")
