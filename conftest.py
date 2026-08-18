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


def pytest_sessionfinish(session, exitstatus) -> None:
    _tiers.write_offenders()   # only when non-empty, i.e. only beside an already-red fast run
    if not session.config.getoption("--store-browser-marks"):
        return
    if session.config.getoption("--tier") != "all":
        raise pytest.UsageError("--store-browser-marks needs a FULL run; drop --tier")
    try:
        _tiers.guard_full_collection(len(_tiers.COLLECTED))
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
