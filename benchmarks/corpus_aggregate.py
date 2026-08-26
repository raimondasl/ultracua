"""Fold N corpus passes into ONE record, and name the scenarios that are not stable. (B5.)

    uv run --no-sync python -m benchmarks.corpus_aggregate runs/gitea.jsonl runs/odoo.jsonl \\
        --out runs/aggregate.json

WHY THIS EXISTS. `corpus_run --reps N` writes N records and nothing joins them, so a series could be
paid for and then read one pass at a time -- which is exactly the reading that cannot tell a
regression from a flake. Measured, and it is not hypothetical: `odoo-create-lead` learned in **6
steps** in one run and captured **0** in another, same corpus and same budget.

WHAT REPS ACTUALLY BUY, stated plainly because it is easy to overclaim. They are a flake DETECTOR,
not a stability certificate: a row that passes 3/3 could still be 80% reliable, which shows 3/3 about
half the time. What they do give is the list of rows whose numbers should not be trusted, BEFORE a
baseline is written from them.

AND ONE THING THEY FIX FOR FREE. `variance.build_record`'s `per_rep` means "one value per REP", and
B3 had to hand it one value per SCENARIO -- which is why `pass_k` was renamed `subset_all_pass` and
why `gate_bench_record` gates on a Wilson bound rather than through `compare_records`, whose
`max(rate_floor, baseline_std)` tolerance is noise-awareness only when the values really are reps.
With N passes the values ARE reps, so `std` here is a genuine across-run noise estimate and means
what its name says for the first time.

IT DOES NOT WRITE A BASELINE. Promoting a record into `baselines/` stays a reviewed human act, for
the same reason `corpus_run` refuses to: the aggregate tells you which rows are unstable, and what to
do about them is a judgement.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.outcomes import QUIET_OUTCOMES, UNSCORED  # noqa: E402


class AggregateError(RuntimeError):
    """The series cannot be folded, and folding it anyway would compare different things."""


def _passed(outcome: str) -> bool:
    """Did this row do what the scenario asked? `Verdict.quiet`'s set, read off the record.

    Deliberately the QUIET set and not "not a failure": `unscored` is neither a pass nor a failure,
    and counting it either way is the confident wrong number this module is arranged against. It is
    excluded from a scenario's denominator below instead.
    """
    return outcome in QUIET_OUTCOMES


def fold(records: "list[dict]") -> dict:
    """One record per pass -> the aggregate, with the unstable rows named.

    REFUSES rather than folds in two cases, and both are the same rule: an aggregate must be over
    the SAME question every time, or its mean is an average of different things.
    """
    if len(records) < 2:
        raise AggregateError(
            f"an aggregate needs at least 2 passes; got {len(records)}. One pass has no spread, and "
            f"publishing it through this module would attach a `std` of 0.0 to a number that has "
            f"never been repeated -- which reads as 'perfectly stable'.")

    sets = [frozenset(r.get("scenarios", {})) for r in records]
    if len(set(sets)) != 1:
        missing = sorted(set().union(*sets) - set.intersection(*[set(s) for s in sets]))
        raise AggregateError(
            f"the passes do not cover the same scenarios (differing: {missing}). Folding them "
            f"would average a rate over one corpus with a rate over another, and nothing in the "
            f"result would say so.")

    names = sorted(sets[0])
    stability, unstable, varies = {}, [], []
    for name in names:
        seen = [r["scenarios"][name]["outcome"] for r in records]
        scored = [o for o in seen if o != UNSCORED]
        passes = sum(1 for o in scored if _passed(o))
        stability[name] = {
            "passes": passes, "scored_reps": len(scored), "reps": len(seen),
            # THE OUTCOMES THEMSELVES, not just a count. A row that alternates `ok`/`over_gated` and
            # one that alternates `ok`/`not_authored` are both "1 of 2" and want different fixes.
            "outcomes": sorted(set(seen)),
        }
        if scored and 0 < passes < len(scored):
            unstable.append({"scenario": name, "passes": passes, "of": len(scored),
                             "outcomes": sorted(set(seen))})
        elif len(set(seen)) > 1:
            # A WEAKER SIGNAL, AND THE FIRST REAL SERIES IS WHY IT EXISTS. `unstable` catches
            # PASS/FAIL flips, which is what a rate depends on -- but `odoo-menu-nav` came back 0/3
            # having given `over_gated` in some passes and `refused` in others, and a row that fails
            # two different ways is not the same as one that fails the same way every time. It
            # showed as stable because neither outcome is a pass. The information was already in the
            # record and nothing pointed at it.
            #
            # Kept SEPARATE from `unstable` rather than merged: a rate built on this row is
            # reproducible, so a baseline is not endangered by it. What it endangers is the
            # DIAGNOSIS -- two failure modes behind one number.
            varies.append({"scenario": name, "passes": passes, "of": len(scored),
                           "outcomes": sorted(set(seen))})

    # PER REP, which is what `variance.aggregate` has always meant by the word. Each value is one
    # pass's availability over the corpus, so the spread across them is a real run-to-run noise
    # estimate rather than a closed form of the mean (B3's objection, which only applied because it
    # was handed scenarios).
    per_rep_availability = []
    for r in records:
        m = (r.get("metrics") or {}).get("availability_rate") or {}
        if "mean" in m:
            per_rep_availability.append(float(m["mean"]))
    if not per_rep_availability:
        raise AggregateError("no pass published an `availability_rate`; there is nothing to fold")

    costs = [r.get("cost_usd") for r in records]
    total_cost = None if any(c is None for c in costs) else round(sum(costs), 6)

    return {
        "kind": "corpus-aggregate",
        "bench": records[0].get("bench", "?"),
        "reps": len(records),
        "scenarios": len(names),
        "availability_rate": {
            "mean": round(statistics.fmean(per_rep_availability), 6),
            # `pstdev` when n < 2 would be 0.0 and read as "no spread"; n >= 2 is enforced above, so
            # the sample stdev is defined and is the honest one.
            "std": round(statistics.stdev(per_rep_availability), 6),
            "min": min(per_rep_availability), "max": max(per_rep_availability),
            "n": len(per_rep_availability),
            "per_rep": per_rep_availability,
        },
        # THE DELIVERABLE. Everything above is arithmetic; this is the list a human acts on.
        "unstable": unstable,
        #: Rows whose VERDICT changed while their pass/fail did not -- see the note in `fold`.
        "varies": varies,
        "stability": stability,
        # UNKNOWN IS ABSORBING (1.3). One unpriceable pass makes the series total unknown rather than
        # silently reporting the sum of the passes that happened to be priceable.
        "cost_usd": total_cost,
    }


def _print(agg: dict) -> None:
    a = agg["availability_rate"]
    print("=" * 78)
    print(f"  {agg['bench']}  reps={agg['reps']}  scenarios={agg['scenarios']}")
    print(f"  availability  mean {a['mean']:.3f}  std {a['std']:.3f}  "
          f"range {a['min']:.3f}-{a['max']:.3f}   per rep {a['per_rep']}")
    print(f"  cost          {agg['cost_usd']}")
    print()
    if not agg["unstable"]:
        print("  NO UNSTABLE ROWS: every scenario gave the same verdict in every pass.")
        print("  That is not proof of stability — a row passing k/k can still be unreliable; it is")
        print("  the absence of evidence of instability at this rep count.")
    else:
        print(f"  UNSTABLE ROWS ({len(agg['unstable'])}) — these are what a baseline must not trust:")
        for u in agg["unstable"]:
            print(f"    {u['scenario']:26} passed {u['passes']}/{u['of']}   saw {u['outcomes']}")
    if agg.get("varies"):
        print()
        print(f"  SAME VERDICT? NO ({len(agg['varies'])}) — the pass/fail did not move, but the")
        print("  REASON did. A rate over these rows is reproducible; a diagnosis is not:")
        for v in agg["varies"]:
            print(f"    {v['scenario']:26} passed {v['passes']}/{v['of']}   saw {v['outcomes']}")
    print("=" * 78)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("records", nargs="+",
                    help="jsonl files written by `corpus_run --out` (one record per line)")
    ap.add_argument("--out", default=None, help="write the aggregate here")
    args = ap.parse_args(argv)

    by_bench: dict = {}
    for path in args.records:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                by_bench.setdefault(rec.get("bench", "?"), []).append(rec)

    aggs = []
    for bench in sorted(by_bench):
        agg = fold(by_bench[bench])
        _print(agg)
        aggs.append(agg)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(aggs if len(aggs) > 1 else aggs[0], fh, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
