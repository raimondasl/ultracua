"""Apply each registered mutation to a SCRATCH COPY of `src/` and report which tests kill it.

A test that has never been seen red is a claim, not a guard. This repo's record on that is explicit:
S14's `no_llm` stub was inert twice past its own review, three of its cells reached no mechanism at all,
and R4.48 measured ELEVEN mutations of the record plumbing surviving the entire 1100-test suite.

    uv run --no-sync python scripts/prove_red.py tests/mutations/b1_wiring.py
    uv run --no-sync python scripts/prove_red.py tests/mutations/b1_wiring.py --tests tests/test_replay_exit_matrix.py

WHAT THIS HARNESS CANNOT SCORE, and it matters because the report says "SURVIVOR" either way.
The mutant is installed by putting a COPY of `src/` first on `PYTHONPATH`. A cell that reaches the
code by IMPORT sees the mutant; a cell that reads the source by PATH (`Path(__file__).parents[1] /
"src" / ...`) sees the pristine repo file and can never contribute a kill. Several structural pins are
written that way deliberately -- they are a different sensor class, like `scripts/ratchets.py` -- but a
mutation whose ONLY guard is such a cell will be reported as an unregistered survivor, which reads as a
hole in the matrix rather than as a limit of the instrument. Prefer `inspect.getsource(module)` in a
cell you want scored here.

The repo is never modified: `src/` is copied to a temp tree, the mutation is applied there, and pytest
runs with that tree first on `PYTHONPATH`. A mutation that does not apply cleanly is an ERROR, not a
survivor — otherwise a stale `find` string would quietly report the suite as strong.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(path: Path):
    spec = importlib.util.spec_from_file_location("mutants", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_BASELINE_CHECKED: dict = {}


def _require_a_live_killer_suite(tests: "list[str]") -> None:
    """Fail loudly if the killer suite does not collect and pass on the UNMUTATED tree.

    THE HOLE THIS CLOSES. A mutant is judged "killed" by a NON-ZERO pytest exit code, so a `--tests`
    path that does not collect — a typo, or a path that has been renamed — makes pytest exit
    non-zero for every mutant and this script report a PERFECT score. An audit hit exactly that and
    read 17/17 where the honest number was 16/17.

    It is the same failure shape the file already guards on the other side ("a stale find-text is an
    ERROR, not a survivor"): an instrument reporting the suite as stronger than it is. Checked once per
    process, against the real tree with no mutation applied, so it costs one suite run.
    """
    key = tuple(tests)
    if _BASELINE_CHECKED.get(key):
        return
    env = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        env[k] = ""
    # PER PATH, not just the aggregate. When `--tests` became a list the guard kept checking one exit
    # code, and pytest reports 0 for "one path collected nothing, the others passed" — so a golden
    # emptied by a rename would be reported as "a hole in the matrix" for every mutant it should have
    # killed, which is the misdiagnosis this guard exists to prevent. Measured: `pytest <registry> -q`
    # alone exits 5 (no tests collected); paired with the exit matrix it exits 0.
    for one in tests:
        solo = subprocess.run(
            [sys.executable, "-m", "pytest", one, "-q", "--collect-only", "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True, env=env)
        if solo.returncode != 0:
            raise SystemExit(
                f"killer-suite path {one!r} collects NOTHING (pytest exit {solo.returncode}). Every "
                f"mutant would run against a suite missing this leg, and a mutant only it kills would "
                f"be reported as a SURVIVOR — a hole in the matrix — rather than as a dead path.\n"
                f"{solo.stdout[-2000:]}")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *tests, "-q", "--tb=no", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise SystemExit(
            f"the killer suite {tests!r} does not pass on the UNMUTATED tree (exit "
            f"{proc.returncode}), so every mutant below would be scored 'killed' by a suite that is "
            f"broken or collects nothing. Fix the suite or the --tests path first.\n"
            + (proc.stdout or "")[-2000:] + (proc.stderr or "")[-2000:])
    _BASELINE_CHECKED[key] = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("registry", type=Path)
    # A LIST, because the properties these mutants attack no longer live in one file. 1.4b's write
    # answer is decided in `_replay_body` (the exit matrix's territory) and PROJECTED onto a batch row
    # (which the exit matrix cannot see), so a single-path killer suite reported four honest cells as
    # SURVIVORS. Splatted into argv rather than joined, which is the failure the guard below names.
    ap.add_argument("--tests", nargs="+",
                    default=["tests/test_replay_exit_matrix.py",
                             "tests/test_batch_row_evidence_golden.py",
                             "tests/test_write_question_golden.py",
                             "tests/test_landed_arms_the_ledger.py"],
                    help="what to run against each mutant (default: the exit-set matrix + the two "
                         "1.4b evidence goldens)")
    args = ap.parse_args()

    mod = _load(args.registry)
    mutants, known = mod.MUTANTS, dict(getattr(mod, "KNOWN_SURVIVORS", {}))
    print(f"{len(mutants)} mutant(s) from {args.registry.name}; killer suite: {args.tests}\n")

    # BEFORE any mutation, against the UNMUTATED tree. A `--tests` path that does not collect makes
    # pytest exit non-zero for every mutant and this script report a perfect score.
    _require_a_live_killer_suite(args.tests)

    killed, survived, broken = [], [], []
    for mid, rel, find, repl, why in mutants:
        with tempfile.TemporaryDirectory(prefix=f"mut-{mid}-") as tmp:
            scratch = Path(tmp) / "src"
            shutil.copytree(ROOT / "src", scratch)
            target = scratch / "ultracua" / rel
            text = target.read_text(encoding="utf-8")
            if find not in text:
                broken.append((mid, "the `find` text no longer appears — the mutation is STALE"))
                print(f"  ERROR   {mid}: find-text not found (stale mutation, not a survivor)")
                continue
            if text.count(find) != 1:
                broken.append((mid, f"`find` matches {text.count(find)} times — ambiguous"))
                print(f"  ERROR   {mid}: find-text matches {text.count(find)}x")
                continue
            target.write_text(text.replace(find, repl, 1), encoding="utf-8")

            env = dict(os.environ)
            env["PYTHONPATH"] = str(scratch) + os.pathsep + env.get("PYTHONPATH", "")
            for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
                env[k] = ""
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", *args.tests, "-q", "-x", "--tb=no",
                 "-p", "no:cacheprovider"],
                cwd=ROOT, capture_output=True, text=True, env=env)
            if proc.returncode != 0:
                killed.append(mid)
                print(f"  killed  {mid}")
            else:
                survived.append(mid)
                print(f"  SURVIVED {mid}  — {why}")

    print(f"\n{len(killed)} killed, {len(survived)} survived, {len(broken)} broken "
          f"of {len(mutants)}")

    if broken:
        print("\nBROKEN mutations (fix the registry — a stale one silently reports the suite as strong):")
        for mid, why in broken:
            print(f"  {mid}: {why}")
        return 2

    unexpected = [m for m in survived if m not in known]
    if unexpected:
        print("\nUNREGISTERED SURVIVORS — each is a hole in the matrix, not a bug in the mutation.")
        print("Add a cell that kills it, or list it in KNOWN_SURVIVORS with a reason and a register id:")
        for m in unexpected:
            print(f"  {m}")
        return 1

    stale = [m for m in known if m not in survived]
    if stale:
        print("\nKNOWN_SURVIVORS lists mutants that are now KILLED — remove them so the list only shrinks:")
        for m in stale:
            print(f"  {m}")
        return 1

    print("\nevery registered mutation is accounted for")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
