"""Can this machine actually run a benchmark substrate? Bring one up, seed it, prove it, tear it down.

    uv run --no-sync python -m benchmarks.substrate_check --substrate gitea

WHY IT EXISTS, and it is not only for CI. The customer benchmark SPENDS MONEY — roughly $0.60 a
Gitea pass, more for Odoo, and a learn that fails spends its whole budget before returning nothing.
Every one of those dollars is wasted if the substrate was never serving properly, and the failure
modes there are quiet by nature: R4.89 is a database manager served with HTTP 200 at every URL the
readiness probe looked at, and R4.98 is a login that reported success having authenticated nobody.
So this runs the container layer FIRST, for free, and refuses to be the thing that discovers a dead
substrate after the bill.

IT IS ALSO A MEASUREMENT, and the reason for that is worth writing down. Two modules in this package
state that "Docker is present on a developer host and absent on CI" — `benchmarks/oracle_liveness.py`
in its docstring, and `CLAUDE.md` as the third axis of local-vs-CI divergence. Tracing that claim to
its evidence gives 0.121.0, where two cells reached `await_ready()` for real and failed both CI arms
with `NOT WRITABLE`. That is a container that is not RUNNING, which is trivially true of a job that
never started one — it is not evidence about the daemon. A whole plan step (2.4's weekly run) rests
on which of those two readings is right, and this repo has already paid once for a prerequisite
written on exactly that ambiguity (reshape-plan §13, corrected by step 0). So the question gets
measured rather than reasoned about, and this module is what measures it.

WHAT IT DELIBERATELY DOES NOT DO: drive a browser. `assert_login_discriminates` needs Chromium and
already runs inside every scored run, where it guards the thing that matters. This is the layer
BELOW that — compose, health, HTTP, and a real write through the substrate's own API — and keeping
it browser-free is what makes it cheap enough to run on every pull request.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import substrates as S                           # noqa: E402

SUBSTRATES = {"gitea": S.Gitea, "odoo": S.Odoo}


def docker_report() -> dict:
    """What the host offers, as facts rather than as an assumption.

    Reported BEFORE anything is attempted and printed whatever happens, because "there is no daemon"
    and "the daemon is there and the substrate is broken" want completely different next actions and
    the failure text alone has never separated them here.
    """
    out: dict = {"docker_on_path": bool(shutil.which("docker"))}
    for label, cmd in (("version", ["docker", "version", "--format", "{{.Server.Version}}"]),
                       ("compose", ["docker", "compose", "version", "--short"])):
        if not out["docker_on_path"]:
            out[label] = None
            continue
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            out[label] = (p.stdout or p.stderr).strip()[:120] if p.returncode == 0 else None
            if p.returncode != 0:
                out[f"{label}_error"] = (p.stderr or p.stdout).strip()[:200]
        except Exception as exc:                                  # noqa: BLE001 - a probe, not a run
            out[label] = None
            out[f"{label}_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return out


def check(name: str, *, seed: bool = True, keep: bool = False) -> dict:
    """Bring one substrate up, prove it serves and can be written to, and report the timings."""
    sub = SUBSTRATES[name]()
    report: dict = {"substrate": name, "images": S.substrate_report()["images"]}
    try:
        t0 = time.monotonic()
        report["up_s"] = round(sub.up(), 1)
        # `up()` already calls `await_ready`, which is health + a substantive body + writability.
        # Calling `assert_writable` again is not redundant: `up` is the composite and this names the
        # layer, so a failure here says WRITABLE rather than the generic readiness message.
        sub.assert_writable()
        report["ready_s"] = round(time.monotonic() - t0, 1)
        if seed:
            t1 = time.monotonic()
            sub.seed()
            # AND SNAPSHOT, ALWAYS, because seeding without it produces a substrate that the only
            # consumer there is cannot use. `score_one` calls `substrate.reset()` before every
            # scenario, and `reset()` restores from a snapshot `seed()` does not take — nothing in
            # `benchmarks/` ever called `snapshot()`, because on a developer host somebody ran it by
            # hand once and the world persisted between sessions.
            #
            # MEASURED, on 2.4a's first real weekly run: all seven Gitea scenarios raised
            # `SubstrateError: no seed at /data/gitea/seed.db — call snapshot() once after seeding`,
            # and the whole record was refused rather than published as 0.0 (R4.110). That is the
            # third local-vs-CI axis arriving one layer down from where 0.137.0 had just corrected
            # it: a job that assumes a provisioned substrate is assuming the developer host.
            #
            # NOT behind a flag. A `--no-snapshot` would be a way to build the broken thing on
            # purpose, and there is no caller that wants one.
            sub.snapshot()
            report["seed_s"] = round(time.monotonic() - t1, 1)
            # THE SEED IS ONLY WORTH SOMETHING IF IT LANDED. `assert_writable` proves the substrate
            # accepts a write; it says nothing about the corpus's own fixtures existing, which is
            # what every scenario's expected answer is computed from. It also proves the substrate
            # came back UP after `snapshot()` stopped it.
            sub.assert_writable()
        report["ok"] = True
    finally:
        if not keep:
            sub.down(wipe=True)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--substrate", required=True, choices=sorted(SUBSTRATES) + ["all"])
    ap.add_argument("--no-seed", action="store_true",
                    help="container + readiness only; skips the fixture load")
    ap.add_argument("--keep", action="store_true", help="leave it running (for a follow-on run)")
    args = ap.parse_args(argv)

    env = docker_report()
    print("docker: " + json.dumps(env))
    if not env.get("version"):
        print("\nNO DOCKER DAEMON REACHABLE HERE.\n"
              "  This is a FACT about the machine, not about the benchmark. Two modules in this "
              "package assert that CI has no daemon; if this message is printing on a "
              "GitHub-hosted ubuntu runner then that assertion is CONFIRMED and reshape-plan 2.4's "
              "weekly run needs a self-hosted runner. If it is printing on a developer host, start "
              "Docker Desktop.", file=sys.stderr)
        return 2

    names = sorted(SUBSTRATES) if args.substrate == "all" else [args.substrate]
    reports = []
    failed = 0
    for name in names:
        print(f"\n=== {name} ===", flush=True)
        try:
            rep = check(name, seed=not args.no_seed, keep=args.keep)
        except BaseException as exc:                              # noqa: BLE001 - report, then fail
            rep = {"substrate": name, "ok": False,
                   "error": f"{type(exc).__name__}: {exc}"[:400]}
            failed = 1
        reports.append(rep)
        print(json.dumps(rep, indent=1))

    print("\n" + json.dumps({"docker": env, "substrates": reports}))
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
