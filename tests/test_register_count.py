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


# ---------------------------------------------------------------------------------------------------
# Round 4 has no heading grammar to parse, so it gets an INDEX and a token scan instead.
#
# R3 works because every finding is one `### … R3.N.` heading. R4 is not like that: its 27 findings are
# declared as `## R4.N —` headings, `- **R4.N** *(sev)*` bullets, `**R4.N (filed, not fixed).**`
# paragraphs and `* **R4.N —**` bullets, and several ids also appear as bare cross-references inside
# unrelated prose. A parser over that would miscount SILENTLY, which this register rates worse than no
# parser at all — so the truth is derived from an explicit index table, and the only thing scanned out of
# the prose is the ID TOKEN, which no formatting choice can hide.
#
# Found while building it: R4.6's heading still said "— OPEN" while its own body said "Closed by plan
# slice S1". That is the R3 staleness one series over, which is the argument for the guard.
_R4_ROW = re.compile(r"^\| *(R4\.\d+) *\| *(open|fixed|parked) *\|", re.MULTILINE)
_R4_TOKEN = re.compile(r"R4\.(\d+)")
_R4_CLAIM = re.compile(r"\*\*(\d+) open\*\*, (\d+) fixed, (\d+) parked")


def r4_index() -> dict[str, str]:
    """`{'R4.5': 'open', …}` — the index table's own rows."""
    return {m.group(1): m.group(2) for m in _R4_ROW.finditer(REGISTER.read_text(encoding="utf-8"))}


def test_the_r4_index_is_not_vacuous() -> None:
    """Anti-vacuity FIRST, as above: a regex matching nothing makes every assertion below pass."""
    idx = r4_index()
    assert len(idx) >= 25, f"only {len(idx)} R4 rows parsed; the index or the parser is broken"
    assert set(idx.values()) == {"open", "fixed", "parked"}, (
        f"expected all three states to be used; got {sorted(set(idx.values()))}")


def test_every_r4_id_in_the_register_is_indexed() -> None:
    """The load-bearing one, and the reason this is a token scan. A new round-4 finding filed in ANY
    prose shape still mentions its own id, so it lands here and the suite demands a row for it."""
    text = REGISTER.read_text(encoding="utf-8")
    mentioned = {f"R4.{m.group(1)}" for m in _R4_TOKEN.finditer(text)}
    missing = sorted(mentioned - set(r4_index()), key=lambda s: int(s.split(".")[1]))
    assert not missing, (
        f"these round-4 ids are written about but have no row in the R4 STATUS INDEX: {missing}. "
        f"Add a row (open / fixed / parked) — an unindexed finding is one nobody can count.")


def test_the_r4_counts_the_register_claims_match_its_index() -> None:
    """The R3 defect, one series over: prose states a count, the rows are the truth."""
    text = REGISTER.read_text(encoding="utf-8")
    claims = _R4_CLAIM.findall(text)
    assert claims, "the register no longer states an R4 tally as `**N open**, N fixed, N parked`"
    idx = r4_index()
    actual = (sum(v == "open" for v in idx.values()),
              sum(v == "fixed" for v in idx.values()),
              sum(v == "parked" for v in idx.values()))
    for i, claim in enumerate(claims):
        assert tuple(int(x) for x in claim) == actual, (
            f"R4 tally #{i + 1} says {claim}; the index rows show {actual}. RE-COUNT rather than "
            f"editing the number to match a guess — the rows are the truth.")


# ---------------------------------------------------------------------------------------------------
# An xfail is an ACKNOWLEDGEMENT, and an acknowledgement nobody re-reads becomes wallpaper.
#
# 0.90.0 introduced the suite's first `xfail`s, for R4.5. They are the right shape — strict, so the fix
# turns them into an XPASS and the suite goes red until the marker is deleted — but nothing noticed if
# they sat for ten releases, and nothing stopped the next one being added for a defect that was never
# filed. Both directions are pinned here, in the same derive-truth-from-the-artifact shape as above:
#
#   * every strict-xfail must NAME a finding this register shows as OPEN. Close the finding and forget
#     the marker, and this goes red — which is the case the marker's own `strict` cannot cover, because
#     a test can keep failing for an unrelated reason.
#   * every xfail must BE strict. A non-strict one passes whether it fails or not, which is the
#     unfalsifiable-test shape this suite has shipped before.
import ast

TESTS_DIR = Path("tests")


def _xfail_markers() -> list[tuple[str, str, dict]]:
    """`[(file, test_name, {'reason': …, 'strict': …}), …]` for every xfail decorator in the suite."""
    out: list[tuple[str, str, dict]] = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                call = dec if isinstance(dec, ast.Call) else None
                target = call.func if call else dec
                if not (isinstance(target, ast.Attribute) and target.attr == "xfail"):
                    continue
                kw = {}
                for k in (call.keywords if call else []):
                    if isinstance(k.value, ast.Constant):
                        kw[k.arg] = k.value.value
                out.append((path.name, node.name, kw))
    return out


def test_every_xfail_is_strict_so_it_cannot_pass_both_ways() -> None:
    loose = [(f, n) for f, n, kw in _xfail_markers() if kw.get("strict") is not True]
    assert not loose, (
        f"these xfails are not strict, so they pass whether the test fails OR succeeds and can never "
        f"force their own removal: {loose}")


def test_every_xfail_names_a_finding_the_register_still_shows_as_OPEN() -> None:
    """The anti-wallpaper rule. An xfail exists because a defect is open; when the defect closes, the
    marker must go, and this is what notices."""
    still_open = set(open_findings()) | {i for i, s in r4_index().items() if s == "open"}
    assert still_open, "no open findings at all — verify that before believing this test"
    orphans = []
    for fname, test, kw in _xfail_markers():
        reason = kw.get("reason") or ""
        if not any(f in reason for f in still_open):
            orphans.append((fname, test, reason[:70]))
    assert not orphans, (
        f"these xfails name no OPEN register finding — either the finding was closed and the marker "
        f"should be deleted, or the defect was never filed and should be: {orphans}")


# ---------------------------------------------------------------------------------------------------
# A finding id CITED IN CODE must exist in the register.
#
# Added after the S14 audit caught the slice shipping five references to "R4.31" — in `flow.py`'s
# comments and in a test docstring — while `docs/open-defects.md` topped out at R4.30 and its header
# asserted that ceiling. Nothing went red: every guard above derives truth from the register's own text
# and never looks at `src/` or `tests/`, so a finding filed in CODE and nowhere else is invisible.
#
# That is the second dangling documentation reference in one working session (the first shipped a literal
# `TESTCOUNT` placeholder into `STATUS.md` on main), which is what turns "fix the instance" into "close
# the class". CLAUDE.md's first line points a reader at this register; a comment citing an id it does not
# contain sends them looking for a record that was never written.

_ID_IN_CODE = re.compile(r"\bR([34])\.(\d+)\b")


def _cited_ids() -> "dict[str, list[str]]":
    """Every `R3.N` / `R4.N` mentioned anywhere in the shipped package or the suite -> where."""
    out: dict[str, list[str]] = {}
    for base in (Path("src"), Path("tests")):
        for py in sorted(base.rglob("*.py")):
            for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                for m in _ID_IN_CODE.finditer(line):
                    out.setdefault(f"R{m.group(1)}.{m.group(2)}", []).append(
                        f"{py}:{n}")
    return out


def test_every_finding_id_cited_in_code_exists_in_the_register() -> None:
    """The anti-vacuity clause first: this scan must actually find ids, or it asserts nothing. The suite
    cites dozens (every write-safety file names the finding it pins), so a zero here means the regex or
    the walk broke, not that the codebase went quiet."""
    cited = _cited_ids()
    assert len(cited) >= 15, (
        f"only {len(cited)} finding id(s) found across src/ and tests/ — the scan is broken, so the "
        f"assertion below would pass vacuously. Found: {sorted(cited)}")

    text = REGISTER.read_text(encoding="utf-8")
    known = set(_ID_IN_CODE.findall(text))
    known = {f"R{a}.{b}" for a, b in known}
    dangling = {i: where for i, where in cited.items() if i not in known}
    assert not dangling, (
        "these finding ids are cited in code but appear NOWHERE in docs/open-defects.md:\n  " +
        "\n  ".join(f"{i} — cited at {', '.join(w[:4])}" for i, w in sorted(dangling.items())) +
        "\n\nFile the finding in the register in the SAME change that names it. Every id is a promise "
        "that a reader can look it up; this project's first instruction is to read that file.")
