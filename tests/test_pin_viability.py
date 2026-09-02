"""The pin-viability probe's own arithmetic. (R4.141.)

`benchmarks/pin_viability.py` produced the numbers R4.141 rests on -- 0 of 770 Odoo leaf text
holders carry an `id` or `data-testid`, so no corpus read can be pinned to a 0-LLM replay. A census
that miscounts its population would make that finding fiction, and the failure would be silent: a
plausible-looking number with nothing to contradict it.

`web_survey` is the precedent. It reimplemented `nameOf` inside the probe and reported Odoo at 36%
unnamed against the product's own 19%, and only a CALIBRATION row caught it. So two things are
asserted here: the census counts what it claims to count, and the probe asks the PRODUCT for a pin
rather than deriving one.

The live half needs containers and is deliberately not here -- `tests/test_substrates.py` makes a
cell that reaches Docker fail loudly. These use `set_content`, so they run anywhere a browser does.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from benchmarks import pin_viability as PV
from ultracua.browser import BrowserSession


@pytest.mark.asyncio
async def test_the_census_counts_LEAF_text_holders_and_their_identity() -> None:
    """The population is leaf-most text holders -- the same set `_PIN_JS` picks its match from.

    An ancestor whose collapsed text equals its child's is NOT a leaf and must not be counted; that
    is what stops a deeply-nested value inflating the denominator once per wrapper. Here the outer
    `<div id=wrap>` holds exactly the same text as its `<span>`, so a census counting every element
    with text would report 2 leaves and 1 with-id, and the pinnable fraction would look twice as
    healthy as it is.
    """
    async with BrowserSession(headless=True) as s:
        await s.page.set_content("""
            <div id='wrap'><span>Gemini Furniture</span></div>
            <p id='named'>Won</p>
            <p data-testid='tid'>Need 20 Desks</p>
            <p>anonymous</p>
            <div><em>nested leaf</em></div>
        """)
        d = await s.page.evaluate(PV._DENSITY_JS)

    # leaves: the span (not its id'd wrapper), #named, the testid p, the anonymous p, the em.
    assert d["leaves"] == 5, f"the census counted {d['leaves']} leaves, expected 5: {d}"
    assert d["withId"] == 1, f"only #named is a leaf WITH an id; the wrapper is not a leaf: {d}"
    assert d["withTestid"] == 1, d


@pytest.mark.asyncio
async def test_a_page_whose_values_carry_no_identity_reports_ZERO_which_is_the_odoo_result() -> None:
    """The measured shape, as a control: text everywhere, identity nowhere.

    Without this the census could satisfy the cell above and still never return 0 -- and 0 is the
    entire finding. Odoo measured exactly this over 770 leaves across five pages.
    """
    async with BrowserSession(headless=True) as s:
        await s.page.set_content("<table><tr><td>Won</td><td>Lost</td></tr></table>")
        d = await s.page.evaluate(PV._DENSITY_JS)
    assert d["leaves"] >= 2 and d["withId"] == 0 and d["withTestid"] == 0, d


def test_the_probe_asks_the_PRODUCT_for_a_pin_rather_than_deriving_one() -> None:
    """`web_survey`'s lesson, asserted structurally rather than by comparing numbers.

    The probe must call `find_pin`/`_PIN_JS` from `ultracua.pin`. A local re-implementation would
    drift from the refusal rules that decide the answer -- above all the positional-anchor refusal,
    which is the single reason every row came back False.
    """
    src = inspect.getsource(PV)
    assert "from ultracua.pin import _PIN_JS, find_pin" in src, (
        "the probe stopped importing the product's pin machinery; a re-derivation here measures the "
        "probe rather than the product, which is exactly how web_survey reported 36% against 19%")
    assert "find_pin(" in src and "_PIN_JS" in src
    for forbidden in ("def find_pin", "def _pin_js", "matches.length"):
        assert forbidden not in src, f"the probe appears to re-implement pin logic ({forbidden!r})"


def test_a_write_scenario_is_skipped_because_its_answer_is_not_extracted() -> None:
    """Derived from the corpus, so a write added tomorrow is skipped without anyone editing a list."""
    src = inspect.getsource(PV.probe)
    assert "entry.truth.mutating" in src and "continue" in src, (
        "the probe stopped skipping write scenarios; a write sets `extract=None`, so there is no "
        "extraction to pin and `expected_answer` is not its subject")


def test_every_scenario_is_measured_on_its_OWN_view() -> None:
    """The bug this probe shipped with, kept as a guard.

    Odoo routes on the hash, so `goto` between two `#action=` urls in one session is a same-document
    navigation: it resolves at once and `await_settled` can find the PREVIOUS view already quiet.
    Three rows reported 10-22 leaves where the rendered list has 203 -- the probe was reading the
    page it had just left. The `about:blank` hop is what forces a fresh document, and it must run
    BEFORE the target navigation or it does nothing.
    """
    src = inspect.getsource(PV.probe)
    # READ THE ORDER OUT OF THE AST, not out of the text. The first draft of this cell compared
    # `src.index(...)` offsets and went RED on its own docstring, which names `await_settled` while
    # explaining the bug -- the EIGHTH time a scan in this repository has matched the prose written
    # to explain it. The remedy is the one already recorded: assert the property.
    tree = ast.parse(textwrap.dedent(src))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "goto":
            arg = node.args[0] if node.args else None
            blank = isinstance(arg, ast.Constant) and arg.value == "about:blank"
            calls.append((node.lineno, "goto:blank" if blank else "goto:target"))
        elif isinstance(f, ast.Attribute) and f.attr == "await_settled":
            calls.append((node.lineno, "settle"))
    order = [name for _, name in sorted(calls)]
    assert order[:3] == ["goto:blank", "goto:target", "settle"], (
        f"the navigation sequence is {order[:3]}, expected blank -> target -> settle. A hash-routed "
        f"row re-reads the PREVIOUS view without the blank hop, and an unsettled read is R4.115")
