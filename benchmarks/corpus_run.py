"""Run the CORPUS, mint one bench record, and gate it. (2.4 / B5, the batch.)

    uv run --no-sync python -m benchmarks.corpus_run --substrate gitea --out runs/gitea.json

**THIS SPENDS MONEY**, roughly `scenarios x $0.04-0.10` per pass plus whatever the failures burn --
a learn that fails spends its whole budget and returns nothing, and `gitea-start-timer` alone cost
$0.58 at a 40-turn ceiling.

WHAT IT ADDS OVER `scored_run`, which drives one scenario: the LOOP, the RECORD and the GATE.
`build_bench_record` needs one `Scored` per scenario and `gate_bench_record` needs the whole record,
so neither could be reached from a runner that returns a dict about a single row.

IT DOES NOT WRITE A BASELINE, and that is deliberate rather than unfinished. `baselines/customer_v1
.json` is 2.4's artifact and one pass cannot be it: B3 already refuses to gate a single flipped
scenario (`FLIP_IS_GATED = False`) precisely because one pass per scenario cannot separate "this flow
stopped working" from "this flow is flaky". `--reps` runs the corpus N times and writes N records so
that question can be ASKED; promoting a record to `baselines/` stays a human act, with the stability
across those reps in front of you.

A ROW THAT RAISES IS NOT A ROW THAT VANISHES. `score_one` already attributes its own failures
(harness vs agent, R4.99), but it can still raise outright -- a container that dies mid-run, a
`BenchRecordError` from an unpriceable model. Those are caught here and recorded as HARNESS rows, so
the corpus size in the record always equals the corpus size on disk. A run that silently scored
thirteen of fourteen would publish a rate over the survivors, which is R4.96 one level up.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import corpus                                  # noqa: E402
from benchmarks import outcomes                                # noqa: E402
from benchmarks.customer_bench import ScenarioRun              # noqa: E402
from benchmarks.scored_run import score_one                    # noqa: E402


def _harness_row(entry, exc: BaseException):
    """A scenario whose RUN blew up, recorded rather than dropped.

    `harness_error` is what puts it in the HARNESS family, which `UNSCORED_FAMILIES` removes from
    every rate -- correct, because nothing about the product was measured -- and which channel 0
    then fails the run for unless a human signs for the pair. Dropping the row instead would shrink
    the denominator silently, and the mean would go UP.
    """
    run = ScenarioRun(scenario=entry.scenario.name, substrate=entry.scenario.substrate,
                      harness_error=f"{type(exc).__name__}: {exc}"[:300])
    return outcomes.adjudicate(entry.truth, run,
                               outcomes.Oracle(available=False,
                                               unavailable_reason="the run did not complete"))


async def run_corpus(substrate: str, *, reset: bool = True, headless: bool = True,
                     only: "tuple" = (), max_steps: "int | None" = None) -> tuple:
    """Drive every scenario of one substrate. Returns `(scored_rows, per_scenario_dicts)`."""
    entries = [e for e in corpus.CORPORA[substrate]
               if not only or e.scenario.name in only]
    scored, rows = [], []
    for i, e in enumerate(entries, 1):
        name = e.scenario.name
        print(f"[{i}/{len(entries)}] {name} ...", flush=True)
        try:
            out, one = await score_one(name, reset=reset, headless=headless, max_steps=max_steps)
        except BaseException as exc:                # noqa: BLE001 - a dead row is still a row
            print(f"    RAISED: {type(exc).__name__}: {exc}"[:160], flush=True)
            scored.append(_harness_row(e, exc))
            rows.append({"scenario": name, "raised": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        rows.append(out)
        # `score_one` returns None when nothing could be adjudicated at all; that is a harness row
        # for the same reason as a raise, and inventing a verdict for it is what B3 forbids.
        scored.append(one if one is not None
                      else _harness_row(e, RuntimeError("no verdict was minted")))
        print(f"    -> {out.get('outcome', '?')}  "
              f"steps={out.get('steps', '?')}  ${out.get('cost_usd') or 0:.4f}", flush=True)
    return scored, rows


def _print_report(rec: dict, verdict: dict) -> None:
    print()
    print("=" * 78)
    for name, row in sorted(rec["scenarios"].items()):
        print(f"  {name:24} {row['outcome']:18} {row.get('code', '') or ''}")
    print()
    for metric, m in sorted(rec.get("metrics", {}).items()):
        if isinstance(m, dict) and "mean" in m:
            print(f"  {metric:28} {m['mean']:.3f}  (n={m.get('n', '?')})")
    print(f"  {'cost_usd':28} {rec.get('cost_usd')}")
    print(f"  {'outcomes':28} "
          f"{ {k: v for k, v in rec['outcomes'].items() if v} }")
    if rec.get("no_recipe"):
        print(f"  {'no_recipe':28} {[r['scenario'] for r in rec['no_recipe']]}")
    if rec.get("unscored"):
        print(f"  {'unscored':28} {[r['scenario'] for r in rec['unscored']]}")
    print()
    # THE GATE'S OWN WORDS, worst-first, including the rows it did NOT fail on: an acknowledged
    # finding is still reported, and a reader who only sees failures cannot tell a clean run from
    # one whose every complaint was signed for.
    for f in verdict["findings"]:
        mark = "FAIL" if f.get("regressed") else ("ack " if f.get("acknowledged") else "note")
        print(f"  [{mark}] {f['channel']:11} {f.get('scenario', f.get('metric', ''))}: "
              f"{str(f.get('reason') or f.get('detail') or '')[:96]}")
    print(f"\n  GATE: {'PASS' if verdict['ok'] else 'FAIL'}")
    print("=" * 78)


def _require_a_comparable_baseline(baseline: dict, substrate: str) -> None:
    """Refuse a baseline that is not about THIS corpus, before anything is paid for.

    THE FAILURE THIS PREVENTS IS SILENT AND FLATTERING. `_rate_findings` compares metric NAMES and
    `_flip_findings` compares scenario NAMES; hand it the Gitea baseline for an Odoo run and the
    scenario sets are disjoint, so `_flip_findings` finds nothing to flip and `_rate_findings`
    compares `availability_rate` across two different corpora as though they were the same
    population. The verdict is a confident PASS built on a comparison nobody made — which is this
    register's recurring shape (a bucket that absorbs what nobody classified) wearing a gate.

    `bench` is `customer-<substrate>`, minted by `build_bench_record` and carried by `as_baseline`,
    so the check needs no new field. The scenario-set equality is the belt: a corpus that GREW since
    the baseline was cut is a real event and the operator should re-cut rather than be told the two
    are comparable.
    """
    want = f"customer-{substrate}"
    got = baseline.get("bench")
    if got != want:
        raise SystemExit(
            f"the baseline is {got!r} but this run is {want!r}. Comparing them would gate a rate "
            f"against a DIFFERENT corpus — and it would look like a pass, because the scenario sets "
            f"are disjoint and the flip channel finds nothing to report.")
    if baseline.get("kind") != "corpus-baseline":
        raise SystemExit(
            f"{got!r} has kind {baseline.get('kind')!r}, not 'corpus-baseline'. Only "
            f"`corpus_aggregate.as_baseline` output carries the (mean, n) the Wilson bound needs; a "
            f"single pass promoted by hand would gate against n=7 and read as far more evidence "
            f"than it is.")
    live = {e.scenario.name for e in corpus.CORPORA[substrate]}
    missing = sorted(set(baseline.get("scenarios", {})) - live)
    added = sorted(live - set(baseline.get("scenarios", {})))
    if missing or added:
        raise SystemExit(
            f"the corpus has moved since this baseline was cut: {len(added)} scenario(s) added "
            f"{added}, {len(missing)} gone {missing}. Re-cut the baseline — a rate over a changed "
            f"corpus is not comparable to one over the old, which is `baselines/README.md`'s "
            f"standing rule.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--substrate", required=True, choices=sorted(corpus.CORPORA))
    ap.add_argument("--reps", type=int, default=1,
                    help="run the corpus N times, writing N records (see the module docstring)")
    ap.add_argument("--only", default="", help="comma-separated scenario names, for a partial pass")
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--max-steps", type=int, default=None, dest="max_steps")
    ap.add_argument("--out", default=None, help="append each rep's record here, one JSON per line")
    ap.add_argument("--acknowledge", default=None,
                    help="JSON file holding [[scenario, reason], ...] pairs the gate may pass")
    ap.add_argument("--baseline", default=None,
                    help="a committed baseline to compare against (baselines/customer_v1_*.json). "
                         "Without it the gate is ABSOLUTE ONLY — the inviolables, coverage, and "
                         "nothing about regression.")
    args = ap.parse_args(argv)

    ack = tuple(tuple(x) for x in json.loads(Path(args.acknowledge).read_text(encoding="utf-8"))) \
        if args.acknowledge else ()
    # THE THREE COMPARATIVE CHANNELS ARE OFF UNTIL A BASELINE IS NAMED, and that asymmetry is the
    # whole reason this flag exists rather than a default path. `gate_bench_record` runs cost, rate
    # and flip only `if baseline is not None`, so a scheduled run that quietly failed to find its
    # baseline would still print a GATE: PASS having compared against nothing — the absolute
    # channels alone. Loading it HERE means a missing or malformed file raises before a single
    # scenario is paid for, which is the same phase-ordering rule R4.99 put on the login.
    baseline = None
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        _require_a_comparable_baseline(baseline, args.substrate)
    only = tuple(s for s in args.only.split(",") if s)

    worst = 0
    for rep in range(1, args.reps + 1):
        if args.reps > 1:
            print(f"\n===== REP {rep}/{args.reps} =====", flush=True)
        started = time.time()
        scored, rows = asyncio.run(run_corpus(
            args.substrate, reset=not args.no_reset, headless=not args.headed,
            only=only, max_steps=args.max_steps))
        # A TIMESTAMP THE CALLER SUPPLIES, because `variance.build_record` is handed one rather than
        # reading the clock -- the same rule the workflow scripts follow, so two records are
        # comparable and a re-run is reproducible.
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
        rec = outcomes.build_bench_record(scored, bench=f"customer-{args.substrate}",
                                          provider="anthropic", timestamp=stamp)
        rec["scenario_rows"] = rows
        verdict = outcomes.gate_bench_record(rec, baseline=baseline, acknowledged=ack)
        _print_report(rec, verdict)
        if args.out:
            with open(args.out, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, default=str) + chr(10))
        worst = worst or (0 if verdict["ok"] else 1)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
