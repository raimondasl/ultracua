"""Apply each registered mutation to a SCRATCH COPY of `src/` and report which tests kill it.

A test that has never been seen red is a claim, not a guard. This repo's record on that is explicit:
S14's `no_llm` stub was inert twice past its own review, three of its cells reached no mechanism at all,
and R4.48 measured ELEVEN mutations of the record plumbing surviving the entire 1100-test suite.

    uv run --no-sync python scripts/prove_red.py tests/mutations/b1_wiring.py
    uv run --no-sync python scripts/prove_red.py tests/mutations/b1_wiring.py --tests tests/test_replay_exit_matrix.py

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("registry", type=Path)
    ap.add_argument("--tests", default="tests/test_replay_exit_matrix.py",
                    help="what to run against each mutant (default: the exit-set matrix)")
    args = ap.parse_args()

    mod = _load(args.registry)
    mutants, known = mod.MUTANTS, dict(getattr(mod, "KNOWN_SURVIVORS", {}))
    print(f"{len(mutants)} mutant(s) from {args.registry.name}; killer suite: {args.tests}\n")

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
                [sys.executable, "-m", "pytest", args.tests, "-q", "-x", "--tb=no",
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
