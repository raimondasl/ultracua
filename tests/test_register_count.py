"""CLAUDE.md's open-finding count must match the register's headings.

This exists because the number went stale TWICE in four slices. It is the first thing anyone reads, it is
what `CLAUDE.md` points at, and prose does not update itself when a heading gains a ✅. Fixing the number
a third time would have been the third fix of the same defect — so instead the invariant is enforced
once, which is this register's own standing rule about how to close something.

The same shape as `scripts/check_shard_coverage.py`: derive the truth from the artifact, compare it to
what the document claims, and fail loudly on drift rather than trusting a human to re-count.
"""

from __future__ import annotations

import re
from pathlib import Path

REGISTER = Path("docs/open-defects.md")
NOTES = Path("CLAUDE.md")

# Round-3 headings look like `### R3.7. …`, and a fixed one is prefixed `### ✅ FIXED in 0.79.0 — R3.8. …`
# or (for the one reverted redesign) `### ⚠️ STILL OPEN — … R3.2. …`. So "open" is "the heading does not
# say FIXED", which is exactly how a human scanning the file would judge it.
_HEADING = re.compile(r"^### (?P<body>.*?R3\.(?P<num>\d+)\.)", re.MULTILINE)


def open_findings() -> list[str]:
    """`['R3.2', 'R3.6', ...]` — every round-3 heading whose text does not claim it is fixed."""
    out = []
    for m in _HEADING.finditer(REGISTER.read_text(encoding="utf-8")):
        if "FIXED" not in m.group("body"):
            out.append(f"R3.{m.group('num')}")
    return sorted(set(out), key=lambda s: int(s.split(".")[1]))


def test_the_register_headings_are_the_source_of_truth() -> None:
    """Anti-vacuity first: a regex that matches nothing would make every assertion below pass trivially,
    which is the failure mode the shard-coverage checker actually shipped with."""
    text = REGISTER.read_text(encoding="utf-8")
    all_headings = _HEADING.findall(text)
    assert len(all_headings) >= 13, (
        f"found only {len(all_headings)} round-3 headings; the parser is broken, so nothing below means "
        f"anything")
    assert open_findings(), "every round-3 finding reads as fixed — verify that before believing it"


def test_claude_md_states_the_open_count_the_register_shows() -> None:
    """The drift this file exists for. If it fails, RE-COUNT rather than editing the number to match a
    guess: the register's headings are the truth and `CLAUDE.md` is the copy."""
    openf = open_findings()
    notes = NOTES.read_text(encoding="utf-8")
    m = re.search(r"\*\*(\d+) open\*\*", notes)
    assert m, "CLAUDE.md no longer states an open-finding count in the form `**N open**`"
    assert int(m.group(1)) == len(openf), (
        f"CLAUDE.md says {m.group(1)} open; the register's headings show {len(openf)}: {openf}")


def test_claude_md_names_them_so_a_wrong_count_is_obvious() -> None:
    """A bare number can be wrong quietly; a list cannot. Every finding the register shows as open must
    be named in the notes, so a stale line is visible on sight rather than only under this test."""
    notes = NOTES.read_text(encoding="utf-8")
    missing = [f for f in open_findings() if f not in notes]
    assert not missing, f"CLAUDE.md does not name these open findings: {missing}"
