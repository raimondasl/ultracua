"""Drive the tier manifest to its FIXED POINT.

`pytest -q --store-browser-marks` gives per-test launch counts, and that is necessary but not
sufficient: attribution is ORDER-DEPENDENT. When a module shares browser work through a fixture,
whichever test triggers it first is charged for the launch and its siblings are charged nothing — so
they classify as "fast" while being unable to run without a browser at all. Measured on the first
derivation: all 15 non-launching tests in `tests/test_drift_bench.py` classified fast because
`test_every_absolute_invariant_holds` primes the shared bench, and every one of them launched the moment
the fast tier deselected that sibling.

So the manifest is a fixed point, not the output of one run: promote whatever the fast tier catches,
until the fast tier catches nothing. That converges because promotion only ever moves tests OUT of the
fast tier.

    uv run --no-sync pytest -q --store-browser-marks      # phase 1, full run (~31 min)
    uv run --no-sync python scripts/derive_test_tiers.py  # phase 2, this loop (~1 min per round)

The CI `fast` job is what keeps it honest afterwards: a manifest that stops being a fixed point turns
that job red and names the tests, rather than quietly skipping them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import _tiers  # noqa: E402

MAX_ROUNDS = 10


def main() -> int:
    if not _tiers.MANIFEST.exists():
        print(f"{_tiers.MANIFEST} is missing — run `pytest -q --store-browser-marks` first.")
        return 2

    total_promoted = 0
    for rnd in range(1, MAX_ROUNDS + 1):
        if _tiers.OFFENDERS_FILE.exists():
            _tiers.OFFENDERS_FILE.unlink()

        print(f"round {rnd}: running the fast tier ...", flush=True)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tier", "fast", "--tb=no",
             "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True,
        )
        tail = proc.stdout.strip().splitlines()[-1:] or ["(no output)"]
        print(f"  {tail[0]}")

        # GREEN IS THE FIXED POINT, checked BEFORE the offenders file. The old order asked about the
        # file first, so a green round that had nonetheless written one — which a green run can do, since
        # the cells that ARM the refusal trip the probe on purpose — would promote the tier's own test
        # cells out of the tier they validate, one round at a time, and report it as progress.
        if proc.returncode == 0:
            print(f"\nFIXED POINT reached after {rnd - 1} promotion round(s); "
                  f"{total_promoted} test(s) promoted in total.")
            return 0

        if not _tiers.OFFENDERS_FILE.exists():
            # Red, but nothing launched a browser: a genuine test failure, which this script must not
            # paper over by promoting anything.
            print("\nthe fast tier is RED but no test launched a browser — that is a real failure, "
                  "not a classification problem. Fix it; do not re-run this script.")
            print(proc.stdout[-3000:])
            return 1

        offenders = json.loads(_tiers.OFFENDERS_FILE.read_text(encoding="utf-8"))
        total_promoted += len(offenders)
        browser, fast = _tiers.promote(offenders)
        print(f"  promoted {len(offenders)} test(s) to the browser tier "
              f"(now {browser} browser / {fast} fast):")
        for nid in offenders[:10]:
            print(f"    {nid}")
        if len(offenders) > 10:
            print(f"    ... and {len(offenders) - 10} more")

    print(f"\nstill promoting after {MAX_ROUNDS} rounds — the loop is not converging, which it should "
          f"by construction (promotion only removes tests from the fast tier). Investigate.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
