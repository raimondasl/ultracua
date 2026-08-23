"""The reshape plan's STATUS INDEX, generated from data instead of typed into four tables.

WHY. `docs/reshape-plan.md` carries each step's status inside its narrative — a Status column in the
Phase-0 table, another in the Phase-1 table, none at all in the Phase-2 table, and a `*(done, X)*`
marker in §13's order table. Four places, hand-maintained, and the status is the part that drifts.

Measured: **2.1 (B2) merged as PR #189 on 2026-08-20** and §13's row naming it as the next step still
read *"unblocked since day one and never started"* three days and six merged slices later. Every row
BELOW it had been marked. The next instruction to act on that table was an instruction to build B2 a
second time.

WHAT THIS CHANGES, AND WHAT IT DELIBERATELY DOES NOT. The STATE (id, phase, title, status, where it
landed, and the one artifact that proves it) moves to `docs/plan/state.json`, and a compact index is
rendered from it at the top of the plan. The NARRATIVE — the four tables, their reasoning columns, the
re-price, the refused proposals — stays exactly where it is. It does not drift; it accretes. Moving it
would be the churn the plan itself argues against.

The index is not the sensor. `tests/test_plan_state.py` is: it makes the TREE adjudicate every row, in
both directions, so a status claim cannot be merely typed.

    python scripts/render_plan_status.py --check   # CI/test: rendered == what is in the doc
    python scripts/render_plan_status.py --write   # regenerate the block after editing state.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "docs" / "reshape-plan.md"
STATE = ROOT / "docs" / "plan" / "state.json"

BEGIN = "<!-- generated:plan-status — edit docs/plan/state.json, then `python scripts/render_plan_status.py --write` -->"
END = "<!-- /generated:plan-status -->"

STATUSES = ("done", "pending", "held")


def load_state(state: Path = STATE) -> list:
    return json.loads(state.read_text(encoding="utf-8"))["steps"]


def _where(step: dict) -> str:
    """The `landed` cell for a done step; for a held one, its trigger and whether that has fired."""
    if step["status"] == "done":
        return step.get("landed", "—")
    if step["status"] == "held":
        fired = "**fired**" if step.get("trigger_fired") else "not fired"
        return f"held → {step.get('trigger', '?')} ({fired})"
    return "—"


def render(steps: list) -> str:
    """Self-contained: header included, so the block can be dropped anywhere in the document."""
    lines = ["| step | phase | status | landed / trigger | what |", "|---|---|---|---|---|"]
    for s in steps:
        lines.append(f"| {s['id']} | {s['phase']} | {s['status']} | {_where(s)} | {s['title']} |")
    done = sum(1 for s in steps if s["status"] == "done")
    lines.append("")
    lines.append(
        f"**{done} of {len(steps)} steps done.** Every row is adjudicated against the tree by "
        f"`tests/test_plan_state.py` — a `done` step must have its artifact and a `pending`/`held` "
        f"step must not."
    )
    return "\n".join(lines)


def current_block(text: str) -> "str | None":
    if BEGIN not in text or END not in text:
        return None
    return text.split(BEGIN, 1)[1].split(END, 1)[0].strip("\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    text = PLAN.read_text(encoding="utf-8")
    steps = load_state()
    rendered = render(steps)
    have = current_block(text)

    if args.write:
        if have is None:
            print(f"markers not found in {PLAN.name}; add {BEGIN} / {END} around the index table")
            return 2
        PLAN.write_text(
            text.split(BEGIN, 1)[0] + BEGIN + "\n" + rendered + "\n" + END + text.split(END, 1)[1],
            encoding="utf-8")
        print(f"wrote the status block: {len(steps)} steps")
        return 0

    if args.check:
        if have is None:
            print("markers not found — the block is not generated")
            return 1
        if have != rendered:
            print("the rendered status index and the doc DISAGREE; run --write")
            return 1
        print(f"status block matches state.json ({len(steps)} steps)")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
