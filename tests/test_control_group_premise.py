"""The in-substrate control groups must actually be easy (R4.104).

A control group exists to answer one question: *if this row fails, is the harness broken?* It can
only answer it while the row is genuinely easy — and `odoo-menu-nav` was not. Its goal asked for the
stages "configured", which points at CRM -> Configuration -> Stages, and `Configuration` is not in
the agent's observation when it deep-links to a CRM action. The row failed at 21 actions, and by its
own comment's rule that made the entire Odoo column unreadable.

The answer was two clicks away the whole time: `/web` lands on Discuss, `Home Menu` is offered as an
element, and CRM opens on the pipeline BOARD. The row asks for the last stage's NAME rather than a
count, because the board renders four titled stage groups PLUS an `o_column_quick_create` placeholder
— so "how many" has two defensible readings, and the live run gave the other one.

WHAT IS ASSERTED HERE IS OFFLINE. The live facts (the board's column count, `Home Menu` being
offered) were measured against a real container when the wording was chosen and are recorded in
`corpus.py`; `benchmarks/fold_probe.py` and the scored runner are what re-check them.
"""
from __future__ import annotations

import pytest

from benchmarks import corpus
from ultracua.safety import MUTATING_KEYWORDS

CONTROL_GROUPS = ("odoo-menu-nav", "gitea-menu-nav")


def _entry(name: str):
    for rows in corpus.CORPORA.values():
        for e in rows:
            if e.scenario.name == name:
                return e
    raise AssertionError(f"{name} is not in the corpus")


@pytest.mark.parametrize("name", CONTROL_GROUPS)
def test_a_control_group_is_a_read(name: str) -> None:
    """A write cannot be a control group: it can fail for write-gate reasons that say nothing about
    whether the harness is sound, which is the one question this row exists to answer."""
    assert not _entry(name).truth.mutating


@pytest.mark.parametrize("name", CONTROL_GROUPS)
def test_a_control_group_does_not_trip_the_keyword_classifier(name: str) -> None:
    """A read goal carrying a `MUTATING_KEYWORDS` token manufactures over-gating. On the row whose
    job is to be trivially easy, that would be self-defeating."""
    e = _entry(name)
    hits = sorted(k for k in MUTATING_KEYWORDS if k in e.scenario.goal.lower())
    assert not hits or e.keyword_read, (
        f"{name}: the goal trips {hits} and does not declare `keyword_read`")


def test_the_odoo_control_group_does_not_send_the_agent_to_a_menu_it_cannot_see() -> None:
    """R4.104's defect, pinned on the GOAL — which is corpus data, not prose about it.

    "configured" / "configuration" points at CRM -> Configuration -> Stages. Measured: landing on a
    CRM action gives 80 elements and **zero** matching "Configuration", and `.o_menu_sections` is 0.
    The board answers the same question in two clicks, so the goal must name the board.
    """
    goal = _entry("odoo-menu-nav").scenario.goal.lower()
    assert "configur" not in goal, (
        "the goal points the agent at Odoo's Configuration menu, which is absent from its "
        "observation on a deep-linked CRM action — that is what cost 21 actions and made the whole "
        "Odoo column unreadable (R4.104)")
    assert "board" in goal, (
        "the goal must name the pipeline BOARD, which is where the answer actually is")
    assert "how many" not in goal, (
        "the board's COUNT is ambiguous — four titled stage groups plus an `o_column_quick_create` "
        "placeholder that renders as a column, measured — so a control group must not ask for one")


def test_the_odoo_control_group_still_starts_where_the_navigation_is() -> None:
    """`/web` lands on Discuss, and that IS the task — the pair is about menu navigation, and
    starting the agent already inside CRM would delete the thing being measured. Verified live that
    the route exists: `Home Menu` is offered as an element and opens CRM."""
    assert _entry("odoo-menu-nav").scenario.url_path == "/web"


@pytest.mark.parametrize("name", CONTROL_GROUPS)
def test_a_control_group_declares_a_checkable_answer(name: str) -> None:
    """Renamed from `..._expects_a_small_number`, which stopped being true in this slice: the Odoo
    row now answers with a stage NAME. What both still owe is an answer the oracle can compute from
    server truth — without one the row scores on the agent's say-so, which is the thing this whole
    benchmark exists not to do."""
    e = _entry(name)
    assert e.expected_answer is not None, f"{name} is a read and must declare an expected answer"


def test_the_pair_is_read_together_and_that_is_recorded_not_asserted() -> None:
    """WHY THERE IS NO CELL HERE ASSERTING THE TWO GOALS ARE "THE SAME KIND OF QUESTION".

    There was one, written earlier in this slice, and it asserted both goals contained "how many".
    The live run refuted its premise — Odoo's board renders four titled stage groups plus an
    `o_column_quick_create` placeholder, so a COUNT there has two readings — and the Odoo row moved
    to asking for a NAME. Relaxed to "both contain the word report", the cell then PASSED against
    the mutation written to kill it, and the arming harness said so.

    What was left was `expected_answer is not None`, which `CorpusEntry.__post_init__` already
    refuses to construct without. A cell asserting something the constructor enforces cannot fail,
    and this project's own rule is that such a cell is worse than none. The pairing is a REVIEW
    property, recorded in `corpus.py` beside both rows, not a test.
    """
    for name in CONTROL_GROUPS:
        assert _entry(name) is not None
