"""Does a corpus read's extractor return the same SHAPE every time? (R4.140.)

    uv run --no-sync python -m benchmarks.extract_shape_probe --substrate odoo --reps 5
    uv run --no-sync python -m benchmarks.extract_shape_probe --only odoo-filter-status --reps 9

`flows._shape_of` reduces a scalar to its primitive type and a dict to its SORTED KEY SET, and
`_shape_matches` compares objects with exact key equality. So a replay whose extractor emits one key
more than the learn did refuses `ShapeDriftError` -- correctly, since a vanished key is exactly what
that gate exists to catch, but the divergence here is the model rewording its own JSON rather than
the page changing.

WHY THIS EXISTS RATHER THAN A REASONED ARGUMENT. Half of R4.140's scope is free to derive: a read
returning one primitive type is structurally immune, because `_shape_matches` calls two same-primitive
shapes equal whatever they contain (`tests/test_shape_gate_scope.py` pins that). The other half --
whether the corpus's goals actually YIELD primitives -- is a fact about what an LLM chooses to
return, which is the one thing that must never be assumed. Measured at 0.159.0: 19 of 19 scored runs
across the four non-composite Odoo reads returned a bare string, and only `odoo-filter-status`,
whose goal asks for two things at once, returns an object.

**IT SPENDS MONEY** -- one learn plus one replay per rep, ~$0.07-0.14 a run on Odoo. The committed
result is `baselines/runs/odoo_0159_extract_shapes.json`; re-run this only to re-derive it after a
goal is reworded or the extractor changes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from typing import Any, Optional

from ultracua import flows

from . import corpus, scored_run


def shape_key(data: Any) -> Optional[str]:
    """A hashable, stable rendering of `_shape_of` -- the product's own function, never a re-derivation.

    Re-implementing the shape here would measure this file rather than the gate, which is the mistake
    `web_survey` made by copying `nameOf` and reported 36% against the product's 19%.
    """
    if data is None:
        return None
    return json.dumps(flows._shape_of(data), sort_keys=True)


async def run(names: list[str], reps: int) -> dict:
    seen: dict[str, list[str]] = {}
    answers: dict[str, Any] = {}
    for name in names:
        for i in range(1, reps + 1):
            try:
                out, scored = await scored_run.score_one(name)
            except Exception as exc:  # noqa: BLE001 -- a dead row must not end the series
                print(f"{name:24} rep{i} RAISED {type(exc).__name__}: {exc}", flush=True)
                traceback.print_exc()
                continue
            key = shape_key(out.get("data"))
            outcome = scored.verdict.outcome if scored else None
            print(f"{name:24} rep{i} outcome={outcome!r:20} steps={out.get('steps')!r:5} "
                  f"shape={key!r} data={str(out.get('data'))[:60]!r}", flush=True)
            # A run that observed NOTHING is not evidence of stability -- it is no evidence at all.
            if key is None:
                continue
            seen.setdefault(name, []).append(key)
            answers.setdefault(name, out.get("data"))

    rows = {}
    for name, keys in seen.items():
        uniq = sorted(set(keys))
        rows[name] = {"runs_observed": len(keys), "shape_t": json.loads(uniq[0])["t"],
                      "distinct_shapes": len(uniq), "shapes": [json.loads(u) for u in uniq],
                      "sample_answer": answers[name]}
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--substrate", default="odoo", choices=sorted(corpus.CORPORA))
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--only", default="", help="comma-separated scenario names")
    ap.add_argument("--out", default=None, help="write the per-scenario summary here as JSON")
    a = ap.parse_args(argv)

    entries = corpus.for_substrate(a.substrate)
    names = [e.scenario.name for e in entries if not e.truth.mutating]
    if a.only:
        want = {s.strip() for s in a.only.split(",") if s.strip()}
        unknown = want - {e.scenario.name for e in entries}
        if unknown:
            raise SystemExit(f"no such scenario on {a.substrate}: {sorted(unknown)}")
        names = [n for n in names if n in want]
    if not names:
        raise SystemExit("no read scenarios selected -- a write's answer is not extracted")

    rows = asyncio.run(run(names, a.reps))
    print("\n=== distinct extraction shapes per scenario ===")
    for name, r in sorted(rows.items()):
        flag = "  <-- CAN shape-drift" if r["shape_t"] == "object" and r["distinct_shapes"] > 1 else ""
        print(f"  {name:24} {r['distinct_shapes']} distinct over {r['runs_observed']} runs "
              f"({r['shape_t']}){flag}")
    if a.out:
        doc = {"substrate": a.substrate, "reps_per_scenario": a.reps,
               "instrument": "benchmarks/extract_shape_probe.py", "scenarios": dict(sorted(rows.items()))}
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=2) + "\n")
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
