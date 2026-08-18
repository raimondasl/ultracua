"""The register's STATE is data now; these are the guards that keep the data and the prose one thing.

`tests/test_register_count.py` stays exactly as it was, and is deliberately not merged into this file:
it derives truth from the PROSE table and compares it to the prose sentences, so it is what proves the
generated block still says what a human reader sees. This file guards the layer underneath — that the
block is generated from `docs/register/state.json`, that the JSON covers every id the register mentions,
and that an entry carrying attempts obeys D5.

Both directions matter. Prose-only was how the count went stale twice; data-only would be a second place
for it to go stale unnoticed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "docs" / "register" / "state.json"
REGISTER = ROOT / "docs" / "open-defects.md"
STATUSES = ("open", "fixed", "parked")


def _findings() -> list:
    return json.loads(STATE.read_text(encoding="utf-8"))["findings"]


def test_the_state_file_is_not_vacuous_and_every_entry_is_well_formed() -> None:
    """ANTI-VACUITY FIRST, as every derive-and-compare guard in this repo does: a state file that failed
    to load, or an empty list, would make every assertion below pass by describing nothing."""
    f = _findings()
    assert len(f) >= 50, f"only {len(f)} findings loaded — the state file or the parse is broken"
    assert sum(1 for x in f if x["status"] == "open") >= 10, "no open findings — verify before believing"
    for x in f:
        assert re.fullmatch(r"R4\.\d+", x["id"]), f"bad id: {x['id']!r}"
        assert x["status"] in STATUSES, f"{x['id']}: status {x['status']!r} is not one of {STATUSES}"
        assert x["summary"].strip(), f"{x['id']}: empty summary"
    ids = [x["id"] for x in f]
    assert len(ids) == len(set(ids)), "duplicate ids in the state file"


def test_the_rendered_index_matches_the_register_byte_for_byte() -> None:
    """The load-bearing one. If the doc and the data disagree, the data is not the source of truth and
    this whole layer is decoration — so the check runs the real renderer rather than re-implementing it.
    """
    proc = subprocess.run([sys.executable, "scripts/render_register.py", "--check"],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"the generated index block and docs/register/state.json disagree — run "
        f"`python scripts/render_register.py --write`.\n{proc.stdout}{proc.stderr}")


def test_every_r4_id_the_register_mentions_has_an_entry() -> None:
    """The token scan `test_register_count.py` applies to the table, applied to the data.

    A finding written up in any prose shape still names its own id, so it lands here and this demands a
    row for it. Without this, a new entry could be filed in narrative and never reach the state file —
    the same silent gap the R4 STATUS INDEX was created to close, one layer down.
    """
    mentioned = {f"R4.{n}" for n in re.findall(r"R4\.(\d+)", REGISTER.read_text(encoding="utf-8"))}
    assert len(mentioned) >= 50, "the token scan found almost nothing; it is broken"
    missing = sorted(mentioned - {x["id"] for x in _findings()},
                     key=lambda s: int(s.split(".")[1]))
    assert not missing, (
        f"these ids are written about in the register but have no entry in state.json: {missing}. "
        f"Add one — an unindexed finding is one nobody can count.")


def test_the_prose_tally_matches_the_data() -> None:
    """The drift this whole layer exists for, now checked against the data rather than against itself."""
    f = _findings()
    counts = tuple(sum(1 for x in f if x["status"] == s) for s in STATUSES)
    claims = re.findall(r"\*\*(\d+) open\*\*, (\d+) fixed, (\d+) parked",
                        REGISTER.read_text(encoding="utf-8"))
    assert claims, "the register no longer states an R4 tally as `**N open**, N fixed, N parked`"
    for i, c in enumerate(claims):
        assert tuple(int(x) for x in c) == counts, (
            f"tally #{i + 1} says {c}; state.json holds {counts}. RE-COUNT rather than editing the "
            f"number to match a guess — the data is the truth.")


def test_D5_is_a_schema_rule_for_any_entry_that_records_its_attempts() -> None:
    """`D5` — two strikes, then change the SENSOR CLASS — is a paragraph everyone must remember. For any
    entry that records `attempts`, it becomes a property instead.

    Scoped to entries that HAVE the field on purpose: the migration omitted optional fields rather than
    guessing them (only 5 of 53 summaries state even a severity), so requiring `attempts` everywhere
    would force 50 hand-typed facts — the transcription class this layer exists to end. An entry gains
    the field when a slice touches it and knows the answer, and gains this guard at the same moment.
    """
    recorded = [x for x in _findings() if x.get("attempts") is not None]
    assert recorded, (
        "no entry records its attempts, so this rule currently guards NOTHING. At least the findings D5 "
        "already binds must carry theirs, or the cell is decoration — the vacuity this repo has shipped "
        "in an instrument twice.")

    offenders = []
    for x in _findings():
        attempts = x.get("attempts")
        if attempts is None or x["status"] != "open":
            continue
        assert isinstance(attempts, list), f"{x['id']}: attempts must be a list"
        if len(attempts) >= 2 and not (x.get("blocked_by") and x.get("next_attempt_requires")):
            offenders.append(x["id"])
    assert not offenders, (
        "D5 binds these: two fix shapes have been built and measured wrong, so the next attempt must "
        "change what the decision is made FROM. Record `blocked_by` and `next_attempt_requires` before "
        f"a third is attempted: {offenders}")
