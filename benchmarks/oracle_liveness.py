"""Do the oracle PROBES see reality? The half `--arm-oracles` structurally cannot answer.

    uv run --no-sync python -m benchmarks.oracle_liveness --substrate gitea

TWO DIFFERENT QUESTIONS, and conflating them is how a benchmark ends up trusting a blind oracle:

  * `--arm-oracles` (offline, in CI, gates every run) asks **can the adjudication say NO?** It
    substitutes a falsified probe result, so it needs no container and costs nothing. What it cannot
    see is an oracle whose QUERY is aimed at the wrong table — that oracle rejects every falsified
    world correctly and still reports nothing about the real one.

  * this module (needs Docker, operator-run until B5's nightly) asks **does the probe move when the
    world moves?** It makes a real change through the substrate's own API and requires the probe to
    notice, then resets and requires it to go back.

NOT IN THE UNIT SUITE, deliberately. Docker is present on a developer host and absent on CI, and a
test that reaches for it passes locally and fails both CI arms — measured at 0.121.0, it shipped a red
PR. A `skipif` would be worse than an absence: a check that silently never runs reads as a check that
passes. So this is a module the nightly calls, and its absence from the suite is stated rather than
disguised.

THE FIRST RUN OF THIS PAID FOR IT. `GiteaReadOracle` claimed "a read must change NOTHING on the
server" while probing only the issue list, so a posted comment left it reporting True. The code was
right and the claim was wider than the check — R4.86's shape one module over, written an hour after
R4.86 was fixed. The probe now covers `SURFACES` and the claim is exactly that wide.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import oracles as O                # noqa: E402
from benchmarks import substrates as S             # noqa: E402

MARKER = "ucprobe liveness marker"


def _brief(v) -> str:
    """Sets are compared in FULL and reported by size — a six-line frozenset dump per row is a report
    nobody reads, and an unread report is the same as no report."""
    if isinstance(v, (frozenset, set)):
        return f"<{len(v)} identities>"
    return str(v)


def _check(label: str, got, want) -> bool:
    ok = got == want
    print(f"  {'ok ' if ok else 'FAIL'}  {label:52} got={_brief(got):18} want={_brief(want)}")
    if not ok and isinstance(got, (frozenset, set)):
        for side, rows in (("only after", got - want), ("only before", want - got)):
            for r in sorted(map(str, rows)):
                print(f"          {side}: {r}")
    return ok


def gitea(sub) -> bool:
    """A real comment through the API; every oracle must move, then move back after a reset."""
    read = O.GiteaReadOracle(sub, "gitea-read")
    baseline = read.probe()
    issues = [r for r in baseline.rows if r[0] == "issue"]
    if not issues:
        raise O.OracleError(
            "the substrate has no issues, so every probe below would compare two empty sets and "
            "pass vacuously. Seed it first: `Gitea().seed()` then `snapshot()`.")
    target = issues[0][1]
    comment = O.GiteaCommentOracle(sub, target, MARKER)
    before = comment.premise()

    ok = True
    ok &= _check("read oracle, world untouched", read.adjudicate(read.probe(), agent_ran=True).satisfied, True)
    ok &= _check("comment oracle, premise is empty", before.count, 0)

    sub._api(sub.token(), "POST",
             f"/repos/{sub.user}/{sub.repo}/issues/{target}/comments", {"body": MARKER})

    ok &= _check("comment oracle sees the REAL write", comment.adjudicate(before, agent_ran=True).satisfied, True)
    ok &= _check("read oracle refuses the same world", read.adjudicate(baseline, agent_ran=True).satisfied, False)
    ok &= _check("agent_ran=False is unscored", comment.adjudicate(before, agent_ran=False).satisfied, None)

    sub.reset()
    after = read.probe()
    ok &= _check("reset restores the watched surfaces", after.identities, baseline.identities)
    return ok


CHECKS = {"gitea": gitea}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--substrate", choices=sorted(CHECKS), default="gitea")
    args = ap.parse_args(argv)

    sub = {"gitea": S.Gitea, "odoo": S.Odoo}[args.substrate]()
    sub.await_ready(timeout_s=120)
    print(f"oracle liveness: {args.substrate}")
    try:
        ok = CHECKS[args.substrate](sub)
    except (O.OracleError, S.SubstrateError) as exc:
        print(f"\nREFUSED: {exc}", file=sys.stderr)
        return 2
    print("\nevery probe moved with the world" if ok else
          "\nA PROBE DID NOT SEE REALITY — the oracle is blind and `--arm-oracles` cannot tell",
          file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - operator surface
    raise SystemExit(main())
