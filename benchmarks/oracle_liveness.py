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

NOT IN THE UNIT SUITE, deliberately -- but NOT for the reason this line gave until 0.137.0.
It said "Docker is present on a developer host and absent on CI". **Measured: the ubuntu
runner has Docker 28.0.4 and Compose 2.38.2, and brings Gitea up in 12.5 s** (R4.109). The
real asymmetry is one level in: a developer host runs a LIVE, SEEDED substrate between
sessions and a CI job starts with nothing running, so a
test that reaches for it passes locally and fails both CI arms — measured at 0.121.0, it shipped a red
PR. A `skipif` would be worse than an absence: a check that silently never runs reads as a check that
passes. So this is a module the nightly calls, and its absence from the suite is stated rather than
disguised.

THE FIRST RUN OF THIS PAID FOR IT, TWICE. `GiteaReadOracle` claimed "a read must change NOTHING on
the server" while probing only the issue list, so a posted comment left it reporting True — the code
was right and the claim was wider than the check, R4.86's shape one module over. And
`GiteaTimerOracle` rejected all four of its falsifications while seeing NOTHING in the real world,
because `/issues/{n}/times` stays empty until a stopwatch is STOPPED. Neither was visible to
`--arm-oracles`, and neither was a subtle bug: both were oracles pointed at the wrong surface.

WHY THE ODOO ARM WRITES THROUGH `call_kw` RATHER THAN SQL. An UPDATE would prove the probe can read
a row this module just wrote. It would prove nothing about the row the APPLICATION writes — Odoo's
ORM computes stored fields, assigns a stage and fires triggers on create — and "does the probe watch
where the app actually lands" is the entire question here.
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


def odoo(sub) -> bool:
    """Every Odoo corpus oracle, against a REAL change made through the substrate's own web client.

    THE CHANGE GOES THROUGH `call_kw`, NOT THROUGH SQL, and that is the point of the whole module.
    An UPDATE would prove the probe can read a row this function just wrote; it would prove nothing
    about whether the probe reads the row the APPLICATION writes. Odoo's ORM computes stored fields,
    assigns stages and fires triggers on create, and an oracle aimed one table over from where the
    app actually lands is exactly the blind oracle `--arm-oracles` cannot see -- it rejects every
    falsified world correctly and reports nothing about the real one. That is not hypothetical here:
    `GiteaTimerOracle` was written that way and the first run of this module caught it.
    """
    entries = corpus.for_substrate("odoo")
    reads = [(e, e.oracle(sub)) for e in entries if not e.truth.mutating]
    writes = {e.scenario.name: e.oracle(sub) for e in entries if e.truth.mutating}
    ok = True

    # 0. The corpus's own expected answers must be computable and non-empty. A read scenario whose
    #    expected answer is blank scores every answer as correct -- and each of these functions
    #    refuses rather than returns when its premise (a unique top, a unique stage count, a unique
    #    search hit) does not hold, so this also drives those refusals against the real world.
    for e, _ in reads:
        want = e.expected_answer(sub)
        ok &= _check(f"{e.scenario.name}: expected answer is a real fact", bool(str(want).strip()), True)
        print(f"          -> {want!r}")

    # 1. Untouched world: every read oracle satisfied.
    baselines = {e.scenario.name: o.premise() for e, o in reads}
    for e, o in reads:
        ok &= _check(f"{e.scenario.name}: untouched world",
                     o.adjudicate(baselines[e.scenario.name], agent_ran=True).satisfied, True)

    # 2. A REAL lead, created the way the app creates one. The write oracle must see it; every read
    #    oracle must now refuse, because a read that changed a watched surface is `incorrect_target`.
    lead = writes["odoo-create-lead"]
    before_lead = lead.premise()
    sub.rpc("crm.lead", "create", [{"name": lead.expect_name, "type": lead.expect_type}])
    v = lead.adjudicate(before_lead, agent_ran=True)
    ok &= _check("odoo-create-lead: sees the real write", v.satisfied, True)
    ok &= _check("odoo-create-lead: exactly one identity matched", len(v.matched), 1)
    for e, o in reads:
        ok &= _check(f"{e.scenario.name}: refuses a world that moved",
                     o.adjudicate(baselines[e.scenario.name], agent_ran=True).satisfied, False)

    # 3. A DOUBLE. Identical name and type, different server id -- the inviolable R4.87 made visible.
    sub.rpc("crm.lead", "create", [{"name": lead.expect_name, "type": lead.expect_type}])
    v2 = lead.adjudicate(before_lead, agent_ran=True)
    ok &= _check("odoo-create-lead: the DOUBLE is two identities", len(v2.matched), 2)
    ok &= _check("odoo-create-lead: and is not satisfied", v2.satisfied, False)

    # 4. The replay oracle is a distinct scenario with its own intended record, so the lead above
    #    must NOT satisfy it -- a write oracle that accepts a neighbouring scenario's record scores
    #    `incorrect_target` as success.
    replay = writes["odoo-idempotent-replay"]
    before_replay = replay.premise()
    ok &= _check("odoo-idempotent-replay: another scenario's lead does not satisfy it",
                 replay.adjudicate(before_replay, agent_ran=True).satisfied, False)
    sub.rpc("crm.lead", "create", [{"name": replay.expect_name, "type": replay.expect_type}])
    ok &= _check("odoo-idempotent-replay: sees its own write",
                 replay.adjudicate(before_replay, agent_ran=True).satisfied, True)

    # 5. agent_ran=False is UNSCORED, never a quiet False (B2 rule 3).
    ok &= _check("agent never ran is unscored",
                 lead.adjudicate(before_lead, agent_ran=False).satisfied, None)

    # 6. And the reset puts every watched surface back -- BOTH halves of it, since Odoo keeps state
    #    in Postgres and on disk and restoring one is a half-reset.
    sub.reset()
    for e, o in reads:
        ok &= _check(f"{e.scenario.name}: reset restores the world",
                     o.probe().identities, baselines[e.scenario.name].identities)
    return ok


CHECKS = {"gitea": gitea, "odoo": odoo}


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
