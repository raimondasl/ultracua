"""An open overlay survives the element cap. (R4.133.)

Odoo's search panel is a dropdown that OVERLAYS the list, so its items interleave with the rows
behind them in reading order and the cut at `max_elements` falls in the middle of the menu.
Measured on the opportunities list: 118 visible interactables, 16 `menuitem*` roles, and only NINE
of them in the observation -- `Archived` among the seven cut. The agent was asked to use a control
that was rendered, visible, correctly named and simply absent from what it could see. That is what
blocked R4.130's repair.

The fix is PRIORITY, not a bigger cap: R4.134 measured 0 of 14 surveyed targets saturating the cap
at rest, so raising it taxes every turn of every page for a case that only arises with a menu open.

BOTH DIRECTIONS ARE LOAD-BEARING and the inert one is the direction that would rot quietly. A change
that reordered every page's observation would still satisfy "the menu survives", and it would
invalidate nothing loudly -- it would just silently change what the agent sees first on every page
in every deployment. So the cells below pin the untouched cases at least as hard as the fixed one.
"""

from __future__ import annotations

import pytest

from ultracua.browser import BrowserSession

CAP = 12  # a small cap keeps the fixtures readable; the mechanism does not know the number


def _rows(n: int, start: int = 0) -> str:
    """Background content, one button per row, laid out top-to-bottom so reading order is by y."""
    # 10px apart so 20 rows span 190px and everything stays INSIDE the 720px viewport.
    # `isVisible` drops `r.top > innerHeight` (R4.102), so a fixture that overflows the fold
    # excludes its own subject from the candidate list and the cell passes or fails for the wrong
    # reason. The first draft parked its overlays at y=900 and went red against a working fix.
    return "".join(
        f"<div style='position:absolute;top:{(start + i) * 10}px;left:0'>"
        f"<button>row{start + i}</button></div>"
        for i in range(n)
    )


async def _snap(html: str, cap: int = CAP):
    async with BrowserSession(headless=True) as s:
        await s.page.set_content(f"<div style='position:relative;height:2000px'>{html}</div>")
        from ultracua import snapshot as snap_mod
        return await snap_mod.capture(s.page, cap)


@pytest.mark.asyncio
async def test_a_page_with_no_overlay_is_truncated_EXACTLY_as_before() -> None:
    """The inert direction, and the one that matters most.

    With no overlay the kept set must still be the first `cap` candidates in reading order -- the
    same `slice(0, MAX)` this code has always taken. A regression here reorders or drops elements on
    every ordinary page, in every deployment, and nothing else in the suite would fail for it.
    """
    obs = await _snap(_rows(30))
    assert len(obs.elements) == CAP
    assert [e.name for e in obs.elements] == [f"row{i}" for i in range(CAP)], (
        "an ordinary page's observation changed; the overlay branch must be inert when nothing is open")


@pytest.mark.asyncio
async def test_a_page_under_the_cap_keeps_everything_overlay_or_not() -> None:
    """No truncation, no decision to make -- asserted so the fast path cannot start filtering."""
    plain = await _snap(_rows(5))
    menu = await _snap(_rows(3) + "<div role='menu'><button role='menuitem'>Archived</button></div>")
    assert len(plain.elements) == 5
    assert {e.name for e in menu.elements} == {"row0", "row1", "row2", "Archived"}


@pytest.mark.asyncio
async def test_an_open_menu_survives_the_cut_even_when_it_sorts_LAST() -> None:
    """THE DEFECT, in the shape that produced it: the menu's items sit BELOW the rows that evict them.

    Without the fix `Archived` is candidate 20-of-30 by y and never reaches a 12-slot observation.
    """
    html = _rows(20) + (
        "<div role='menu' style='position:absolute;top:300px;left:0'>"
        "<button role='menuitemcheckbox'>Filters</button>"
        "<button role='menuitemcheckbox'>Archived</button>"
        "<button role='menuitem'>Add Custom Filter</button></div>")
    obs = await _snap(html)
    names = [e.name for e in obs.elements]
    assert len(obs.elements) == CAP, f"the cap moved: {len(obs.elements)}"
    for want in ("Filters", "Archived", "Add Custom Filter"):
        assert want in names, (
            f"{want!r} was cut although it is inside an open menu; this is R4.130's blocker: "
            f"got {names}")


@pytest.mark.asyncio
async def test_the_survivors_stay_in_READING_ORDER_rather_than_menu_first() -> None:
    """Concatenating menu-then-rest would pass the cell above and silently reorder every such page.

    The agent reads the list top-down, so an observation that hoists a mid-page dropdown to rank 0
    changes what "first" means on every page carrying one. The kept set is re-filtered out of the
    sorted candidates for exactly this reason.
    """
    html = _rows(20) + (
        "<div role='menu' style='position:absolute;top:300px;left:0'>"
        "<button role='menuitemcheckbox'>Archived</button></div>")
    obs = await _snap(html)
    names = [e.name for e in obs.elements]
    assert names[0] == "row0", f"the observation no longer starts at the top of the page: {names}"
    assert names[-1] == "Archived", (
        f"the menu item is at y=900, below every kept row, so reading order puts it LAST: {names}")


@pytest.mark.asyncio
async def test_an_overlay_bigger_than_the_cap_is_itself_truncated_in_reading_order() -> None:
    """The cap is still a cap. An overlay may not grow the observation without bound."""
    html = _rows(5) + "<div role='menu'>" + "".join(
        f"<div style='position:absolute;top:{300 + i * 12}px'>"
        f"<button role='menuitem'>opt{i}</button></div>"
        for i in range(30)) + "</div>"
    obs = await _snap(html)
    assert len(obs.elements) == CAP
    assert [e.name for e in obs.elements] == [f"opt{i}" for i in range(CAP)], (
        "an over-large overlay must keep its FIRST cap items in reading order")


@pytest.mark.asyncio
@pytest.mark.parametrize("markup,label", [
    ("<button role='menuitem'>Archived</button>", "role=menuitem"),
    ("<button role='menuitemcheckbox'>Archived</button>", "role=menuitemcheckbox"),
    ("<button role='menuitemradio'>Archived</button>", "role=menuitemradio"),
    ("<button role='option'>Archived</button>", "role=option"),
    ("<div aria-modal='true'><button>Archived</button></div>", "aria-modal container"),
    ("<dialog open><button>Archived</button></dialog>", "<dialog open> container"),
])
async def test_every_recognised_overlay_kind_survives(markup: str, label: str) -> None:
    """A menu is not the only thing a user chooses from; a modal evicted by background rows is the
    same defect.

    ITEM ROLES and MODAL CONTAINERS are two different mechanisms and both are listed. A modal's
    contents are ordinary controls with no distinguishing role of their own, so it can only be
    recognised by its container; a dropdown's items announce themselves and MUST be recognised that
    way, because its container does not mean what it looks like -- see the cell below.
    """
    obs = await _snap(_rows(20) + markup.replace("<button", "<button style='position:absolute;top:300px'", 1))
    assert "Archived" in [e.name for e in obs.elements], f"{label} was not treated as an overlay"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["menu", "listbox", "tree"])
async def test_a_CONTAINER_role_alone_grants_nothing_which_is_the_blast_radius(role: str) -> None:
    """THE MEASUREMENT THAT REFUSED THE FIRST DESIGN, kept as a guard.

    The first draft asked `el.closest('[role=menu],[role=listbox],[role=tree],...')`. Measured
    across all 14 corpus start pages: **10 were BOTH truncated and carrying 4-11 such elements AT
    REST**, because `role=menu` names a PERSISTENT container on both benchmark substrates -- Gitea
    puts it on a CLOSED dropdown (`aria-expanded="false"`) and Odoo on its systray toolbar. Priority
    for those silently reorders most of the corpus. Re-keyed on the item's own role, the same
    measurement reports **0 overlay elements at rest on 13 of 14 pages**, and the fourteenth is not
    truncated.

    So a bare container must grant NOTHING. Its plain `<button>` child is background content.
    """
    html = _rows(20) + (f"<div role='{role}' style='position:absolute;top:300px;left:0'>"
                        f"<button>Archived</button></div>")
    obs = await _snap(html)
    assert "Archived" not in [e.name for e in obs.elements], (
        f"a bare role={role} container granted overlay priority to a plain button. On the real "
        f"substrates that fires at rest on 10 of 14 corpus pages")


@pytest.mark.asyncio
async def test_a_CLOSED_dialog_does_not_get_priority_even_when_a_page_styles_it_VISIBLE() -> None:
    """The `[open]` qualifier, tested where it is actually load-bearing.

    THE FIRST DRAFT OF THIS CELL PROVED NOTHING, and its own mutation said so. It served a plain
    `<dialog>` and asserted its button stayed out of the observation -- true, but decided by the UA
    stylesheet's `display:none` long before any overlay logic runs, so dropping `[open]` from the
    selector changed nothing and the mutation SURVIVED against a cell that looked correct. The
    arming pass caught my explanation, not my assertion; that is the third time this session.

    Where `[open]` earns its place is a page that overrides the UA rule -- `dialog { display: block }`
    is a real thing in CSS resets. Then a CLOSED dialog's contents ARE visible and DO become
    candidates, and without the qualifier they would take overlay priority and evict real controls
    on behalf of a panel nobody opened.
    """
    styled = ("<style>dialog{display:block;border:0;padding:0;margin:0}</style>"
              "<dialog><button style='position:absolute;top:300px'>Archived</button></dialog>")
    obs = await _snap(_rows(20) + styled)
    names = [e.name for e in obs.elements]
    assert "Archived" in [e.name for e in (await _snap(_rows(2) + styled)).elements], (
        "the fixture is inert: a visible closed-dialog button must be a CANDIDATE under the cap, or "
        "this cell is testing the visibility path again rather than the `[open]` qualifier")
    assert "Archived" not in names, (
        "a CLOSED dialog took overlay priority. `[open]` is what separates a panel the user opened "
        "from one the page merely rendered, and without it a hidden-by-intent dialog evicts real "
        f"controls: got {names}")
