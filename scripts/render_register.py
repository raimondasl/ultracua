"""The R4 status index, generated from data instead of typed by hand.

WHY. `docs/open-defects.md` is 3900 lines of narrative with the STATE of every finding embedded in it,
and the state is what drifts: the counts went stale twice in four slices, which is why
`tests/test_register_count.py` exists at all. That test derives truth from the prose table and compares
it to the prose sentences — it catches drift, but the table itself is still hand-maintained, so filing a
finding means typing a row, two tallies and a ceiling in four places and hoping.

WHAT THIS CHANGES, AND WHAT IT DELIBERATELY DOES NOT. The STATE (id, status, one-line summary) moves to
`docs/register/state.json` and the table is rendered from it. The NARRATIVE stays exactly where it is:
it does not drift, it accretes, and moving 3900 lines of evidence to gain nothing is the kind of churn
this repo's own reshape plan argues against.

Optional fields (severity, class, inviolable, attempts, blocked_by, pins, disposition) are OMITTED
rather than guessed. Only 5 of 53 rows state a severity in prose, so populating the rest would mean
hand-typing 50 facts read out of narrative — which is the transcription class this migration exists to
END, not a good way to start it. Each field gets filled in when a slice touches that finding and knows
the answer; `tests/test_register_data.py` enforces the D5 rule for any entry that HAS attempts.

    python scripts/render_register.py --check      # CI/test: rendered == what is in the doc
    python scripts/render_register.py --write      # regenerate the block after editing state.json
    python scripts/render_register.py --bootstrap  # one-shot: seed state.json FROM the current doc
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTER = ROOT / "docs" / "open-defects.md"
STATE = ROOT / "docs" / "register" / "state.json"

BEGIN = "<!-- generated:r4-index — edit docs/register/state.json, then `python scripts/render_register.py --write` -->"
END = "<!-- /generated:r4-index -->"

_ROW = re.compile(r"^\| (R4\.\d+) \| (open|fixed|parked) \| (.*?) \|$", re.M)
STATUSES = ("open", "fixed", "parked")


def load_state() -> list:
    data = json.loads(STATE.read_text(encoding="utf-8"))
    return data["findings"]


def render(findings: list) -> str:
    """The exact table body the register carries today — header included, so the block is self-contained."""
    lines = ["| id | status | what |", "|---|---|---|"]
    for f in sorted(findings, key=lambda x: int(x["id"].split(".")[1])):
        lines.append(f"| {f['id']} | {f['status']} | {f['summary']} |")
    return "\n".join(lines)


def current_block(text: str) -> "str | None":
    if BEGIN not in text or END not in text:
        return None
    return text.split(BEGIN, 1)[1].split(END, 1)[0].strip("\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--bootstrap", action="store_true")
    args = ap.parse_args()
    text = REGISTER.read_text(encoding="utf-8")

    if args.bootstrap:
        rows = _ROW.findall(text)
        if len(rows) < 25:
            print(f"only {len(rows)} rows parsed — refusing to seed from a broken read")
            return 2
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({
            "_comment": (
                "The STATE of every round-4 finding. The narrative stays in docs/open-defects.md; this "
                "is what the index table is rendered FROM, so a count cannot drift from the rows. "
                "Optional fields (severity, inviolable, class, attempts, blocked_by, "
                "next_attempt_requires, pins, disposition) are OMITTED rather than guessed — fill one "
                "in when a slice touches that finding and knows the answer. An entry that HAS attempts "
                "must satisfy the D5 rule: open + >=2 attempts requires blocked_by and "
                "next_attempt_requires."),
            "statuses": list(STATUSES),
            "findings": [{"id": i, "status": s, "summary": w} for i, s, w in rows],
        }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"seeded {STATE.relative_to(ROOT)} with {len(rows)} findings")
        return 0

    rendered = render(load_state())
    have = current_block(text)

    if args.write:
        if have is None:
            print(f"markers not found in {REGISTER.name}; add {BEGIN} / {END} around the index table")
            return 2
        REGISTER.write_text(
            text.split(BEGIN, 1)[0] + BEGIN + "\n" + rendered + "\n" + END + text.split(END, 1)[1],
            encoding="utf-8")
        print(f"wrote the index block: {len(load_state())} findings")
        return 0

    if args.check:
        if have is None:
            print("markers not found — the block is not generated")
            return 1
        if have != rendered:
            print("the rendered index and the doc DISAGREE; run --write")
            return 1
        print(f"index block matches state.json ({len(load_state())} findings)")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
