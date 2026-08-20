"""Build the tier manifest from observations that already happened, instead of a 36-minute run.

THE COST THIS ATTACKS. `tests/.browser_tests.json` says which tests never launch a browser, and it is
what makes `pytest --tier fast` an 85-second signal instead of a 36-minute one. Re-deriving it needs a
full suite run — twelve entries in `tests/.manifest_cost.jsonl`, 1917-3151 s each — and the last three
slices paid it to add six, four and three browser-free cells. CI already runs that suite on every PR,
four times over (two shards x two OSes), and throws the evidence away.

    pytest -q --emit-marks .scratch/marks/local.json      # an OBSERVATION; never the manifest
    python scripts/tier_marks.py merge .scratch/marks     # parts -> candidate -> validate -> swap
    python scripts/tier_marks.py pull                     # fetch CI's parts, then merge

WHY IT IS LOCAL-FIRST, and this is the correction that a design review forced. An earlier shape made
CI the SOLE supplier of marks, and that quietly made the inner loop worse: producing marks would then
require an open PR (`ci.yml` triggers on push-to-main and pull_request, so a feature branch with no PR
runs nothing), `gh` installed and authenticated, no concurrent push, and a 16-25 minute wait — with
the same 36-minute local run as the fallback when any of that did not hold. That is not removing a
tax; it is moving it behind a network precondition. So `--emit-marks` writes an observation ANYWHERE,
`merge` takes parts from anywhere, and `pull` is an accelerator rather than the mechanism.

WHY IDENTITY IS THE COLLECTED ID SET AND NOT A COMMIT SHA. On a `pull_request` GitHub does not check
out your branch: it checks out `refs/pull/N/merge`. Verified — run 32380417760 reports headSha
5d47964 (the branch tip) while its checkout step says `HEAD is now at 2c908fc Merge 5d47964 into
4f7ac67`. So a run that is correctly *identified* by your commit collected a *different tree*. Match
by sha and you would cheerfully write a manifest listing tests you do not have — and the inflated
`total` would then DISARM the deletion detector in `tests/test_tiers.py`, which only runs when
`len(collected) >= data["total"]`. The bug would switch off its own alarm.

WHY CANDIDATE-THEN-SWAP. The fixed-point loop is the only instrument that can refute a merge, and its
failure branch says "Fix it; do not re-run this script" — a confident misdiagnosis to hand someone
over an artifact that costs 36 minutes to rebuild. So the merge writes a CANDIDATE, points the loop at
it via `ULTRACUA_TIER_MANIFEST`, and swaps only on convergence. The committed manifest is never the
draft.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import _tiers  # noqa: E402 - needs the path above

DEFAULT_PART_DIR = ROOT / ".scratch" / "marks"
ARTIFACT_PREFIX = "tier-marks-"


def collect_ids() -> list:
    """THIS tree's collection — the identity every part is checked against.

    A subprocess rather than an in-process collect: this script must not be running inside the pytest
    session whose manifest it is about to replace.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )
    ids = [ln.strip() for ln in proc.stdout.splitlines() if "::" in ln and not ln.startswith(" ")]
    if not ids:
        raise SystemExit(
            "could not collect this tree's tests, so there is nothing to check the parts against:\n"
            + (proc.stdout + proc.stderr)[-2000:]
        )
    return ids


def load_parts(where: Path) -> list:
    """Every `*.json` under `where`, in name order, as `(path, data)`.

    Name order is what makes `reconcile_attempts` see the attempts in order: CI artifact names
    carry the run attempt, so `...-1` sorts before `...-2`.
    """
    if where.is_file():
        return [(where, _tiers.load_observation(where))]
    files = sorted(p for p in where.glob("*.json"))
    if not files:
        raise SystemExit(f"no observation parts (*.json) under {where}")
    return [(p, _tiers.load_observation(p)) for p in files]


def cmd_merge(args) -> int:
    where = Path(args.parts)
    parts = load_parts(where)
    print(f"read {len(parts)} part(s) from {where}: " + ", ".join(d["label"] for _p, d in parts))

    expected = collect_ids()
    print(f"this tree collects {len(expected)} test(s)")

    try:
        browser, fast = _tiers.merge_observations(parts, expected)
    except _tiers.MergeRefused as exc:
        print(f"\nREFUSED to merge:\n{exc}", file=sys.stderr)
        return 2

    print(f"merged classification: {len(browser)} browser, {len(fast)} fast")

    # CANDIDATE. Written somewhere the committed manifest is not, validated, and only then swapped in.
    # BESIDE the manifest, not in a temp dir: `os.replace` is only atomic within a volume, and
    # `tempfile.mkdtemp()` is on another one. Measured on this host — `shutil.move` onto an existing
    # file falls back to copy2 + unlink, i.e. a truncate-then-write of the committed manifest on EVERY
    # merge, on the platform this repo is developed from.
    candidate = _tiers.MANIFEST.with_suffix(".candidate.json")
    real = _tiers.MANIFEST
    try:
        _tiers.MANIFEST = candidate
        _tiers.write_manifest_from(browser, fast)
    finally:
        _tiers.MANIFEST = real

    if args.no_validate:
        print("SKIPPING validation (--no-validate); this is not a fixed point and may redden CI")
    else:
        print("\nvalidating the candidate — the fixed-point loop, against the CANDIDATE not the "
              "committed manifest:")
        env = dict(os.environ, ULTRACUA_TIER_MANIFEST=str(candidate))
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "derive_test_tiers.py")], cwd=ROOT, env=env)
        if proc.returncode != 0:
            print(
                f"\nthe candidate did NOT converge (exit {proc.returncode}). The committed manifest is "
                f"UNTOUCHED; the candidate is at {candidate} if you want to look at it.",
                file=sys.stderr)
            return proc.returncode

    os.replace(candidate, real)          # atomic within a volume; never a truncate-then-write
    data = json.loads(real.read_text(encoding="utf-8"))
    print(f"\nswapped in {real.name}: {len(data['browser'])} browser, {len(data['fast'])} fast")
    return 0


def _gh(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], cwd=ROOT, capture_output=True, text=True)


def cmd_pull(args) -> int:
    """Download CI's parts for the CURRENT branch, then merge them.

    Resolves the run by BRANCH and then checks its identity by id set, rather than trusting a sha —
    see the module docstring for why a sha is the wrong key here.
    """
    if not shutil.which("gh"):
        print("`gh` is not installed. This is the accelerator, not the mechanism — you can still\n"
              "  pytest -q --emit-marks .scratch/marks/local.json && python scripts/tier_marks.py merge",
              file=sys.stderr)
        return 3

    branch = args.branch or subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT,
        capture_output=True, text=True).stdout.strip()
    listing = _gh("run", "list", "--branch", branch, "--workflow", "CI", "--limit", "1",
                  "--json", "databaseId,status,conclusion,headSha")
    if listing.returncode != 0:
        print(f"gh could not list runs for {branch!r}: {listing.stderr}", file=sys.stderr)
        return 3
    runs = json.loads(listing.stdout or "[]")
    if not runs:
        print(f"no CI run for branch {branch!r}. CI triggers on pull_request and on pushes to main, "
              f"so a branch with no open PR produces none — emit a part locally instead.",
              file=sys.stderr)
        return 3

    run = runs[0]
    # `status` is NOT the outcome: a CANCELLED run also reports status "completed". This repo has
    # already paid once for reading an overloaded CI word (see docs/ci-provisioning.md), so the gate is
    # an ALLOWLIST on `conclusion`.
    if run["status"] != "completed":
        print(f"run {run['databaseId']} is {run['status']}; artifacts are only complete once it "
              f"finishes. Measured: `gh run download` on an in-progress run returns SOME artifacts "
              f"with exit 0, which is why this refuses rather than proceeding.", file=sys.stderr)
        return 3
    if run["conclusion"] not in ("success", "failure"):
        print(f"run {run['databaseId']} concluded {run['conclusion']!r} — not a completed observation. "
              f"A cancelled run (concurrency, or a job wall) has status 'completed' too.",
              file=sys.stderr)
        return 3

    # Clear only what a PREVIOUS PULL left, by prefix. The first draft did an unconditional
    # `shutil.rmtree(dest)` — and `dest` defaults to `.scratch/marks`, the very directory this module's
    # own docstring tells you to emit `local.json` into. So the documented "emit locally, then try
    # pull" sequence destroyed a ~36-minute observation, and `pull --parts .` from the repo root was an
    # unconfirmed rmtree of the working tree. In the one slice whose subject is not destroying an
    # expensive artifact.
    dest = Path(args.parts)
    dest.mkdir(parents=True, exist_ok=True)
    for stale in sorted(dest.glob(f"{ARTIFACT_PREFIX}*")):
        (shutil.rmtree(stale) if stale.is_dir() else stale.unlink())
    dl = _gh("run", "download", str(run["databaseId"]), "-p", f"{ARTIFACT_PREFIX}*", "-D", str(dest))
    if dl.returncode != 0:
        print(f"gh run download failed: {dl.stderr}", file=sys.stderr)
        return 3

    # `gh run download` makes one directory per artifact; flatten, keeping the artifact name so the
    # attempt suffix still orders them.
    for sub in sorted(p for p in dest.iterdir() if p.is_dir()):
        for f in sorted(sub.glob("*.json")):
            f.rename(dest / f"{sub.name}.json")
        shutil.rmtree(sub, ignore_errors=True)

    print(f"pulled from run {run['databaseId']} ({run['conclusion']}) into {dest}")
    args.parts = str(dest)
    return cmd_merge(args)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("merge", help="merge observation parts into the manifest")
    m.add_argument("parts", nargs="?", default=str(DEFAULT_PART_DIR),
                   help=f"a part file or a directory of them (default {DEFAULT_PART_DIR})")
    m.add_argument("--no-validate", action="store_true",
                   help="skip the fixed-point loop (for tests; leaves a manifest that may not converge)")
    m.set_defaults(func=cmd_merge)

    p = sub.add_parser("pull", help="download this branch's CI parts, then merge")
    p.add_argument("--branch", default=None)
    p.add_argument("--parts", default=str(DEFAULT_PART_DIR))
    p.add_argument("--no-validate", action="store_true")
    p.set_defaults(func=cmd_pull)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
