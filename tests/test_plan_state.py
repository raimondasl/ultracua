"""The reshape plan's status, adjudicated by the TREE rather than by typing.

WHY THIS EXISTS, measured. `docs/reshape-plan.md` §13's order table named **2.1 (B2)** as the next
step and described it as *"unblocked since day one and never started"*. B2 had merged as PR #189 on
2026-08-20, in exactly the position that table assigned it, and **every row below it had since been
marked done**. Three days and six merged slices later the row still said "never started", and the next
instruction to act on it was an instruction to build B2 a second time — 1750 lines, four modules,
three test files, all already on `main`.

Nothing could have caught it, because the status was PROSE. It was typed in four tables with three
different column shapes, and the one thing that could contradict it — the code being in the tree — was
never asked.

WHAT THIS ASSERTS. `docs/plan/state.json` is the status of record. Every step names ONE artifact whose
presence means it shipped, and the check runs BOTH WAYS:

* a `done` step must have its artifact — this catches a status that outlived its deliverable;
* a `pending` or `held` step must NOT — **this is the direction that failed**, and it is the whole
  reason the field exists.

WHAT IT DOES NOT PROVE, stated plainly. "The file exists" is not "the step was done well"; the audits
and the step's own pins are what say that. And three artifacts (0.5, 0.6, 0.7) are FORWARD-DECLARED
because the plan names no file for them — if the slice that lands one picks a different name, the
absent-direction stays quiet. The `done` direction is what closes that: flipping a status without its
artifact fails here, naming the step.

DELIBERATELY NOT BUILT: a prose scan over the plan's narrative. The stale phrases sit in hard-wrapped
paragraphs, so a per-line matcher is an accident of where the text happens to wrap, and its failure
mode is "reword an innocent sentence" — a loud channel with a nuisance false-positive rate is one that
gets switched off, which is D0 wearing a linting hat. The ONE prose shape checked below is §13's order
table, whose rows are single lines by markdown's own syntax and therefore not wrap-sensitive.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "docs" / "plan" / "state.json"
PLAN = ROOT / "docs" / "reshape-plan.md"

STATUSES = ("done", "pending", "held")
SHIPPED = "done"


def _steps() -> list:
    return json.loads(STATE.read_text(encoding="utf-8"))["steps"]


# --- the adjudicator, root-injectable so it can be armed --------------------------------------

def artifact_present(spec: str, root: Path) -> bool:
    """Is `spec` present under `root`?  Two forms, and the second is what makes a code SHAPE checkable.

    `path`            — the file must exist.
    `path::token`     — the file must exist AND contain `token`. Used where a step's deliverable is a
                        shape inside an existing file (`flows.py::_RecordSink`) rather than a new one,
                        and where a future step will add a name to a file that already exists
                        (`customer_bench.py::CORPUS`).
    """
    path, _, token = spec.partition("::")
    p = root / path
    if not p.is_file():
        return False
    if not token:
        return True
    return token in p.read_text(encoding="utf-8", errors="replace")


def disagreements(steps: list, root: Path) -> list:
    """Every row whose claim the tree refutes, each naming the step and the remedy."""
    out = []
    for s in steps:
        present = artifact_present(s["artifact"], root)
        if s["status"] == SHIPPED and not present:
            out.append(
                f"{s['id']} is marked `done` but its artifact `{s['artifact']}` is NOT in the tree. "
                f"Either it was deleted, or the status was flipped without the work."
            )
        elif s["status"] != SHIPPED and present:
            out.append(
                f"{s['id']} is marked `{s['status']}` but its artifact `{s['artifact']}` IS in the "
                f"tree — the step has shipped and the plan does not say so. This is the B2 failure "
                f"verbatim: set status to `done` in docs/plan/state.json, record where it landed, "
                f"and re-run `python scripts/render_plan_status.py --write`."
            )
    return out


# --- the two directions -------------------------------------------------------------------------

def test_every_done_step_has_its_artifact_in_the_tree() -> None:
    bad = [d for d in disagreements(_steps(), ROOT) if "is marked `done`" in d]
    assert not bad, "\n  ".join([""] + bad)


def test_no_unshipped_step_has_its_artifact_in_the_tree() -> None:
    """THE ONE THAT WOULD HAVE CAUGHT B2. A step the plan calls pending whose code is already here."""
    bad = [d for d in disagreements(_steps(), ROOT) if "is marked `done`" not in d]
    assert not bad, "\n  ".join([""] + bad)


# --- armed, because a green check over a mis-spelled path is not a check ------------------------

def test_a_vanished_artifact_under_a_done_step_is_caught(tmp_path: Path) -> None:
    """Direction 1, armed: an empty tree must refute EVERY done row, by name."""
    steps = _steps()
    done = [s for s in steps if s["status"] == SHIPPED]
    assert done, "no done rows — this arming cell would pass vacuously"
    found = disagreements(steps, tmp_path)
    for s in done:
        assert any(d.startswith(f"{s['id']} is marked `done`") for d in found), (
            f"an empty tree did not refute {s['id']}"
        )


def test_an_artifact_appearing_under_an_unshipped_step_is_caught(tmp_path: Path) -> None:
    """Direction 2, armed — and this is the B2 shape reproduced: the artifact exists, the plan says no."""
    steps = _steps()
    unshipped = [s for s in steps if s["status"] != SHIPPED]
    assert unshipped, "no pending/held rows — this arming cell would pass vacuously"
    for s in unshipped:
        path, _, token = s["artifact"].partition("::")
        f = tmp_path / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(token or "x", encoding="utf-8")
    found = disagreements(steps, tmp_path)
    for s in unshipped:
        assert any(d.startswith(f"{s['id']} is marked `{s['status']}`") for d in found), (
            f"planting {s['artifact']} did not refute {s['id']}"
        )
    # ...and the message has to be actionable, not merely red.
    assert all("docs/plan/state.json" in d for d in found if "is marked `done`" not in d)


# --- the data itself ------------------------------------------------------------------------------

def test_the_table_is_not_degenerate() -> None:
    """Both statuses present, both existence states present, and no artifact aimed at a missing directory.

    The last clause is the one that matters: a `pending` row whose artifact path is misspelled passes
    the absent-direction forever. Requiring the PARENT to exist catches a wrong directory, which is the
    half of a typo that a permanently-absent path cannot otherwise reveal.
    """
    steps = _steps()
    assert len(steps) >= 20, f"only {len(steps)} steps — the plan has more than that"
    seen = {s["status"] for s in steps}
    assert seen <= set(STATUSES), f"unknown status: {seen - set(STATUSES)}"
    assert SHIPPED in seen and (seen - {SHIPPED}), (
        "the table is all one status, so one of the two directions is untested by the real tree"
    )
    present = {artifact_present(s["artifact"], ROOT) for s in steps}
    assert present == {True, False}, (
        "every artifact resolves the same way — the check is measuring the tree, not the table"
    )
    for s in steps:
        parent = (ROOT / s["artifact"].partition("::")[0]).parent
        assert parent.is_dir(), f"{s['id']}: artifact directory {parent} does not exist (typo?)"


def test_every_id_is_unique_and_every_row_is_complete() -> None:
    steps = _steps()
    ids = [s["id"] for s in steps]
    assert len(ids) == len(set(ids)), f"duplicate step ids: {sorted({i for i in ids if ids.count(i) > 1})}"
    for s in steps:
        for field in ("id", "phase", "title", "status", "artifact"):
            assert s.get(field) not in (None, ""), f"{s.get('id')}: missing `{field}`"
        if s["status"] == SHIPPED:
            assert s.get("landed"), f"{s['id']} is done and does not say where it landed"
        if s["status"] == "held":
            assert s.get("trigger"), f"{s['id']} is held and names no trigger"
            assert isinstance(s.get("trigger_fired"), bool), (
                f"{s['id']} is held and does not say whether its trigger has fired — which is the "
                f"fact a reader needs, and 0.5's and 0.6's had both fired unremarked"
            )


def test_a_held_steps_trigger_is_a_real_step_and_its_fired_flag_is_true_iff_that_step_is_done() -> None:
    """`trigger_fired` is DERIVED, not an opinion — 0.5 said "held → 1.1" while 1.1 had landed."""
    steps = _steps()
    by_id = {s["id"]: s for s in steps}
    for s in steps:
        if s["status"] != "held":
            continue
        trig = s["trigger"]
        assert trig in by_id, f"{s['id']} is held on `{trig}`, which is not a step in this table"
        expected = by_id[trig]["status"] == SHIPPED
        assert s["trigger_fired"] is expected, (
            f"{s['id']} says trigger_fired={s['trigger_fired']} for `{trig}`, which is "
            f"`{by_id[trig]['status']}`"
        )


# --- the rendered index, and the one prose table that is not wrap-sensitive -----------------------

def test_the_status_index_matches_the_data() -> None:
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "render_plan_status.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_index_actually_renders_the_status() -> None:
    """ARMED: flipping 2.1 back to `pending` must change the rendered block.

    A generated table that renders the same text whatever the data says is a decoration, and this one
    is the first thing a reader of the plan sees."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import render_plan_status as rps
    finally:
        sys.path.pop(0)

    steps = _steps()
    real = rps.render(steps)
    flipped = [{**s, "status": "pending"} if s["id"] == "2.1" else s for s in steps]
    assert rps.render(flipped) != real, "the render ignores `status` — the index cannot go stale-proof"
    assert "| 2.1 | 2 | done |" in real and "| 2.1 | 2 | pending |" in rps.render(flipped)


_ROW = re.compile(r"^\| \d+ \| (\*\*.+?)\|", re.M)
_STEP_ID = re.compile(r"\b(\d\.\d[ab]?)\b")


OPERATIVE = "<!-- order-table:operative -->"
HISTORICAL = "<!-- order-table:historical -->"


def test_exactly_one_order_table_is_marked_operative() -> None:
    """The plan holds TWO order tables — §12's, superseded, and §13's, live. Which is which was
    something a reader had to infer from section numbers, and inferring it wrong is how a superseded
    row gets read as an instruction. Both markers are asserted so neither can be quietly dropped."""
    text = PLAN.read_text(encoding="utf-8")
    assert text.count(OPERATIVE) == 1, f"{text.count(OPERATIVE)} operative markers; expected exactly 1"
    assert text.count(HISTORICAL) >= 1, "the superseded §12 order table lost its historical marker"
    assert text.index(HISTORICAL) < text.index(OPERATIVE), "the operative table must be the later one"


def unmarked_rows(text: str, done_ids: set) -> tuple:
    """(rows checked, rows naming a landed step with no `done` marker) over the OPERATIVE table.

    Takes the document as a STRING so the cell below can drive it against a mutated copy — a scan that
    can only read the real file is a scan nobody has watched go red.
    """
    assert OPERATIVE in text, "the operative order-table marker is gone; this scan reads nothing"
    body = text[text.index(OPERATIVE):]
    rows = [(m.group(1), body[m.start():body.index("\n", m.start())]) for m in _ROW.finditer(body)]
    checked, stale = 0, []
    for label, _line in rows:
        ids = [i for i in _STEP_ID.findall(label) if i in done_ids]
        if not ids:
            continue
        checked += 1
        # The marker lives in the LABEL cell, not anywhere on the row — "done" appears innocently in
        # several reasoning columns, and a whole-line search would score those as marked.
        if "done" not in label:
            stale.append(f"the row labelled {label.strip()} names {ids}, which landed, and carries no "
                         f"`(done, …)` marker")
    return checked, stale


def test_no_order_table_row_omits_a_done_marker_for_a_step_that_has_landed() -> None:
    """The OPERATIVE order table, whose rows are single lines and therefore safe to read.

    This is the exact artifact that went stale: row 2's label said `**2.1 — B2**` with no `(done`
    marker while rows 3–7 below it all carried one. Scoped to the operative table on purpose — §12's
    rows deliberately carry no markers, because a superseded table should not look answerable.
    """
    done_ids = {s["id"] for s in _steps() if s["status"] == SHIPPED}
    checked, stale = unmarked_rows(PLAN.read_text(encoding="utf-8"), done_ids)
    assert checked >= 5, f"only {checked} rows named a landed step — the id regex has gone stale"
    assert not stale, "\n  ".join([""] + stale)


def test_the_marker_scan_goes_red_on_the_row_that_actually_went_stale() -> None:
    """ARMED, by reconstructing row 2 exactly as it stood on `main` for three days.

    Without this the cell above is a scan that has never been seen to fail, and this repo's own rule is
    that a green property is worth what its arming is worth.
    """
    done_ids = {s["id"] for s in _steps() if s["status"] == SHIPPED}
    text = PLAN.read_text(encoding="utf-8")
    row = next(ln for ln in text.splitlines() if ln.startswith("| 2 | **2.1 — B2**"))
    stale_row = "| 2 | **2.1 — B2** | unblocked since day one and never started; it is the goal | none |"
    assert "done" not in stale_row and "done" in row  # the mutation is real in both directions

    checked, found = unmarked_rows(text.replace(row, stale_row), done_ids)
    assert checked >= 5, "the mutated copy stopped parsing — the scan would pass for the wrong reason"
    assert any("2.1" in f for f in found), (
        f"the stale row was NOT caught; the scan cannot fail for the defect it exists for: {found}"
    )


@pytest.mark.parametrize("spec,expect", [
    ("scripts/render_plan_status.py", True),
    ("scripts/render_plan_status.py::load_state", True),
    ("scripts/render_plan_status.py::this_name_is_not_in_that_file", False),
    ("scripts/no_such_script.py", False),
    ("scripts", False),  # a directory is not an artifact
])
def test_the_artifact_predicate_itself(spec: str, expect: bool) -> None:
    """The predicate every other cell leans on, driven directly — including the directory case, which
    `Path.exists()` would answer True for and `is_file()` does not."""
    assert artifact_present(spec, ROOT) is expect
