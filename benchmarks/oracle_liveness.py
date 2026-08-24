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

from benchmarks import corpus                     # noqa: E402
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
    """Every corpus oracle, against a REAL change made through the substrate's own API.

    Drives the CORPUS set rather than a hand-picked pair, so an oracle added to the corpus is covered
    here without editing this function — the same reason `oracles_for` derives from the corpus.
    """
    entries = corpus.for_substrate("gitea")
    reads = [(e, e.oracle(sub)) for e in entries if not e.truth.mutating]
    writes = {e.scenario.name: e.oracle(sub) for e in entries if e.truth.mutating}
    ok = True

    # 0. The corpus's own expected answers must be computable and non-empty. A read scenario whose
    #    expected answer is blank scores every answer as correct.
    for e, _ in reads:
        want = e.expected_answer(sub)
        ok &= _check(f"{e.scenario.name}: expected answer is a real fact", bool(str(want).strip()), True)

    # 1. Untouched world: every read oracle satisfied.
    baselines = {e.scenario.name: o.premise() for e, o in reads}
    for e, o in reads:
        ok &= _check(f"{e.scenario.name}: untouched world",
                     o.adjudicate(baselines[e.scenario.name], agent_ran=True).satisfied, True)

    # 2. A REAL comment. The write oracle must see it; every read oracle must now refuse, because a
    #    read that changed a watched surface is `incorrect_target` — an inviolable.
    target = 1
    comment = writes["gitea-comment"]
    before_comment = comment.premise()
    sub._api(sub.token(), "POST", f"/repos/{sub.user}/{sub.repo}/issues/{target}/comments",
             {"body": "looks right to me"})
    v = comment.adjudicate(before_comment, agent_ran=True)
    ok &= _check("gitea-comment: sees the real write", v.satisfied, True)
    ok &= _check("gitea-comment: exactly one identity matched", len(v.matched), 1)
    for e, o in reads:
        ok &= _check(f"{e.scenario.name}: refuses a world that moved",
                     o.adjudicate(baselines[e.scenario.name], agent_ran=True).satisfied, False)

    # 3. A DOUBLE, which is the inviolable R4.87 made visible. Identical body, different server id.
    sub._api(sub.token(), "POST", f"/repos/{sub.user}/{sub.repo}/issues/{target}/comments",
             {"body": "looks right to me"})
    v2 = comment.adjudicate(before_comment, agent_ran=True)
    ok &= _check("gitea-comment: the DOUBLE is two identities", len(v2.matched), 2)
    ok &= _check("gitea-comment: and is not satisfied", v2.satisfied, False)

    # 4. The timer, whose identity comes from the times endpoint rather than the id-less stopwatch.
    timer = writes["gitea-start-timer"]
    before_timer = timer.premise()
    sub._api(sub.token(), "POST", f"/repos/{sub.user}/{sub.repo}/issues/{target}/stopwatch/start", None)
    ok &= _check("gitea-start-timer: sees the real write",
                 timer.adjudicate(before_timer, agent_ran=True).satisfied, True)

    # 5. agent_ran=False is UNSCORED, never a quiet False (B2 rule 3).
    ok &= _check("agent never ran is unscored",
                 comment.adjudicate(before_comment, agent_ran=False).satisfied, None)

    # 6. And the reset puts every watched surface back.
    sub.reset()
    for e, o in reads:
        ok &= _check(f"{e.scenario.name}: reset restores the world",
                     o.probe().identities, baselines[e.scenario.name].identities)
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
