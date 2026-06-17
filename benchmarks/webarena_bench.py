"""Offline WebArena-Verified runner — validates the evaluator wiring with zero containers.

Two key-less modes (no ANTHROPIC_API_KEY, no Docker, native Windows):

  uv run python -m benchmarks.webarena_bench --selfcheck   # producer->eval round-trip (default)
  uv run python -m benchmarks.webarena_bench --demo        # re-score bundled demo logs 107/108

``--selfcheck`` writes the gold answer for a RETRIEVE task (+ a minimal valid HAR) and scores it
(expect 1.0), plus an empty answer (expect 0.0) — proving the full isolated-CLI pipeline end to
end without depending on any checked-out fixtures.

``--demo`` re-scores the demo agent logs shipped in the cloned webarena-verified repo
(``examples/agent_logs/demo``). Point at it with ``--src`` or ``ULTRACUA_WEBARENA_SRC``
(default ``<data_dir>/webarena-verified-src``); clone with::

  git clone --depth 1 https://github.com/ServiceNow/webarena-verified.git <data_dir>/webarena-verified-src

The evaluator itself is fetched on demand via ``uv tool run`` (isolated env); first run downloads.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ultracua.config import settings

from benchmarks import webarena_env as wa


def _default_src() -> Path:
    return Path(os.getenv("ULTRACUA_WEBARENA_SRC", str(Path(settings.data_dir) / "webarena-verified-src")))


def run_selfcheck(task_id: int) -> bool:
    print(f"[selfcheck] producer->eval round-trip on RETRIEVE task {task_id} (key-less, no containers)")
    scores = wa.selfcheck_roundtrip(task_id)
    good, bad = scores["good"], scores["bad"]
    print(f"  gold answer  -> score {good}  (expect 1.0)")
    print(f"  empty answer -> score {bad}  (expect 0.0)")
    ok = good == 1.0 and bad == 0.0
    print("  RESULT:", "PASS" if ok else "FAIL")
    return ok


def run_demo(src: Path, task_ids: tuple[int, ...]) -> bool:
    demo = src / "examples" / "agent_logs" / "demo"
    if not demo.is_dir():
        print(f"[demo] demo logs not found at {demo}")
        print("       clone the repo first (see module docstring) or pass --src <repo>.")
        return False
    print(f"[demo] re-scoring bundled demo logs {list(task_ids)} from {demo}")
    scratch = Path(settings.data_dir) / "webarena" / "demo_eval"
    scores = wa.validate_demo(demo, scratch=scratch, task_ids=task_ids)
    expected = {107: 0.0, 108: 1.0}
    ok = True
    for tid in task_ids:
        exp = expected.get(tid)
        got = scores[tid]
        tag = "" if exp is None else f"  (expect {exp})"
        print(f"  task {tid} -> score {got}{tag}")
        if exp is not None and got != exp:
            ok = False
            for msg in wa.get_failure_reasons(scratch, tid):
                print("     -", msg)
    print("  RESULT:", "PASS" if ok else "FAIL")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Offline WebArena-Verified evaluator validation.")
    ap.add_argument("--selfcheck", action="store_true", help="producer->eval round-trip (default)")
    ap.add_argument("--demo", action="store_true", help="re-score bundled demo logs 107/108")
    ap.add_argument("--task-id", type=int, default=108, help="RETRIEVE task id for --selfcheck")
    ap.add_argument("--src", type=Path, default=None, help="cloned webarena-verified repo root")
    args = ap.parse_args(argv)

    if not wa.cli_available():
        print("webarena-verified CLI unavailable (need `uv` + network for first download). Aborting.")
        return 2

    src = args.src or _default_src()
    # Default to --selfcheck when neither flag is given.
    run_self = args.selfcheck or not args.demo
    run_dem = args.demo

    ok = True
    if run_self:
        ok = run_selfcheck(args.task_id) and ok
    if run_dem:
        ok = run_demo(src, (107, 108)) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
