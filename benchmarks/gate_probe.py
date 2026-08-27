"""Which gate will each marked step actually get? Read a cached recipe and say. (D6's instrument.)

    uv run --no-sync python -m benchmarks.gate_probe D:/some/cache
    uv run --no-sync python -m benchmarks.gate_probe .ultracua/bench/abc123.json

WHY THIS EXISTS RATHER THAN A PARAGRAPH. D6 proposed routing wire-promoted steps to the PRECISE
form-scope gate instead of the whole-page one, and its own text made the fix conditional on measuring
"which action types Odoo's failing steps actually are" first. The measurement REFUTED the fix, and a
conclusion of the form "do not change `src/`" is worth exactly as much as its reproducibility: the
next person to doubt it should be able to re-derive it in a minute rather than re-buy four learns.

WHAT IT SHOWED (0.139.0, $0.4634 across four Odoo learns). Ten wire-promoted steps in the three
scenarios that actually refused:

    navigate  6   no locator, no scope  ->  whole-page fallback, and the precise gate is
                                            STRUCTURALLY unreachable: there is no element to scope
    click     4   scope AND locator     ->  the precise gate ALREADY, and it refused anyway

`odoo-sort-list` refused with `mutation gate: target missing/ambiguous` -- the PRECISE branch's own
first failure mode -- and `odoo-menu-nav` with `mutation gate: page drift`, the fallback's. So both
branches refuse, for different reasons, and D6's change addresses neither.

AND THE MEASUREMENT CORRECTED ITSELF TWICE, which is the argument for the tool. From the first
scenario alone the conclusion was "the precise gate is structurally unreachable for the failing
steps" -- true of navigations and FALSE as a generalisation, since 40% of gated steps already reach
it. The second scenario produced no gated step at all. It took the third and fourth.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# THE TWO FIELDS `flow.py`'s gate branches on, named here so a test can hold them against the real
# condition rather than this file paraphrasing it. `tests/test_gate_probe.py` reads the engine's own
# source and fails if the branch stops testing exactly these -- because a probe that describes a
# condition the code no longer has is worse than no probe: it answers confidently and wrongly.
PRECISE_REQUIRES = ("precond_scope", "locator")


def gate_of(step: dict) -> str:
    """`precise` / `whole-page` for a step the gate will judge. Mirrors `flow.py`'s own branch."""
    return "precise" if (step.get("precond_scope") and step.get("locator") is not None) \
        else "whole-page"


def gate_inputs(recipe: dict) -> "list[dict]":
    """One row per MUTATING step: what the gate has to work with, and which branch that selects.

    Only mutating steps, because only they are gated -- `flow.py` opens with `if step.mutating:` and
    everything below it is unreachable otherwise.
    """
    out = []
    for i, s in enumerate(recipe.get("steps", [])):
        if not s.get("mutating"):
            continue
        out.append({
            "index": i,
            "action": s.get("action"),
            "sources": tuple(s.get("mutating_sources") or ()),
            "has_scope": bool(s.get("precond_scope")),
            "has_locator": s.get("locator") is not None,
            "gate": gate_of(s),
            "intent": str(s.get("intent") or "")[:60],
        })
    return out


def recipes_under(path: Path) -> "list[tuple[Path, dict]]":
    """Every cached flow at `path` — a single file, or every non-meta JSON in a directory."""
    files = ([path] if path.is_file()
             else [p for p in sorted(path.glob("*.json")) if not p.name.endswith(".meta.json")])
    out = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:      # a cache is not always a recipe
            print(f"  (skipping {f.name}: {type(exc).__name__})", file=sys.stderr)
            continue
        if isinstance(data, dict) and "steps" in data:
            out.append((f, data))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", type=Path, nargs="+",
                    help="a cached flow, or a cache directory holding some")
    args = ap.parse_args(argv)

    tally: Counter = Counter()
    total_recipes = 0
    for p in args.path:
        if not p.exists():
            print(f"no such path: {p}", file=sys.stderr)
            return 2
        for f, recipe in recipes_under(p):
            total_recipes += 1
            rows = gate_inputs(recipe)
            print(f"\n=== {f.name}  —  {str(recipe.get('goal') or '')[:66]}")
            if not rows:
                print("    no mutating steps: nothing is gated in this recipe")
                continue
            print(f"    {'step':5}{'action':11}{'sources':12}{'scope':7}{'locator':9}gate")
            for r in rows:
                print(f"    {r['index']:<5}{r['action'] or '?':11}"
                      f"{','.join(r['sources']) or '-':12}"
                      f"{str(r['has_scope']):7}{str(r['has_locator']):9}{r['gate']}")
                tally[(r["action"], r["gate"])] += 1

    if not total_recipes:
        print("\nNO RECIPES FOUND. A cache directory with no flows in it reports the same clean "
              "table as one where nothing is gated, so this is loud rather than empty.",
              file=sys.stderr)
        return 2

    print(f"\nMUTATING STEPS BY ACTION AND GATE, over {total_recipes} recipe(s):")
    for (action, gate), n in sorted(tally.items()):
        note = "  <- the precise gate is unreachable: no element to scope" \
            if gate == "whole-page" and action in ("navigate", "scroll") else ""
        print(f"  {action or '?':11} {gate:12} {n}{note}")
    if not tally:
        print("  (none — no recipe here has a mutating step)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
