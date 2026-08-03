"""Row identity, redesigned (R3.1). The capabilities the two-implementation version could not have.

R1/R2's fix worked but was built the way this codebase keeps getting bitten: `rowIdOf` was transcribed
into BOTH `_SPECOF_JS` (capture) and `_ROW_OF_JS` (bind), and the two copies had to agree or the guard
compares a token to a differently-computed token and refuses everything. Round 3 found them already
diverging. It also found the selection rule itself too weak in two specific ways, both of which end in a
FABRICATED identity — the failure mode R2 named as worse than having none, because the guard then runs,
reports satisfied, and protects nothing:

  * only the FIRST `a[href]`/`form[action]` in the row was read. A row whose first link is a shared
    decorative one (`/help`, `#`, a sort header) hid the real per-row action sitting right behind it.
  * `input[type=hidden][name]` was not considered at all — and a hidden `order_id`/`csrf_record` pair is
    how a great many server-rendered apps carry record identity, often as the ONLY per-row token present.

The redesign extracts ONE `_ROWID_JS` snippet, included verbatim by both sites so divergence is not
expressible, and makes DISCRIMINATION the requirement applied once across every candidate — id, every
href/action, every `data-*`, every hidden input — rather than a priority order over a couple of them. A
candidate is an identity only if no OTHER row on the page carries it.

The residual is stated, not hidden, and the last test pins it: a row with nothing discriminating claims no
identity, and the guard then cannot fire. That is the pre-existing bound of the mechanism, unchanged here.
"""

from __future__ import annotations

import pytest
from playwright.async_api import async_playwright

from ultracua.locators import DESCRIBE_JS, LocatorSpec, resolve


async def _capture_and_bind(capture_html: str, bind_html: str, row_n: int = 0):
    """Capture the Cancel control in row `row_n` of `capture_html`, then resolve against `bind_html`.

    Returns (anchor_id, bound_row_label) — `bound_row_label` is the bound row's first cell, or None when
    the guard refused. Rows deliberately carry NO `id`: an id is itself a perfectly good identity and
    would win outright, hiding whether the candidate under test is ever reached.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            await page.set_content(capture_html)
            await page.evaluate(
                "(n) => document.querySelectorAll('tr')[n].querySelector('button')"
                ".setAttribute('data-ultracua-ref', 'r1')", row_n)
            spec = LocatorSpec(**await page.evaluate(DESCRIBE_JS, "r1"))

            page2 = await (await browser.new_context()).new_page()
            await page2.set_content(bind_html)
            loc = await resolve(page2, spec, unique=True, sink={})
            bound = None
            if loc is not None:
                bound = await loc.evaluate(
                    "(el) => { const r = el.closest('tr,li');"
                    " return r ? r.querySelector('td').textContent.trim() : null; }")
            return spec.anchor_id, bound
        finally:
            await browser.close()


def _page(rows: str) -> str:
    return f"<!doctype html><html><body><table><tbody>{rows}</tbody></table></body></html>"


# Both rows POST to the SAME endpoint and the record is carried in a hidden field — the shape where the
# href is useless and the hidden input is the only thing that tells the rows apart.
def _hidden_row(who: str, order: str, control: str) -> str:
    return (f'<tr><td>{who}</td>'
            f'<td><form method="post" action="/orders/cancel">'
            f'<input type="hidden" name="order" value="{order}">{control}</form></td></tr>')


_HIDDEN_PRISTINE = _page(_hidden_row("Acme", "3", "<button>Cancel</button>")
                         + _hidden_row("Globex", "7", "<button>Cancel</button>"))
# The recorded row survives but its control is gone; only Globex's Cancel remains and Tier 1 would bind it.
_HIDDEN_DRIFTED = _page(_hidden_row("Acme", "3", "<span>Cancelled</span>")
                        + _hidden_row("Globex", "7", "<button>Cancel</button>"))


# Every row opens with the same decorative help link. The real action follows it.
def _decoy_row(who: str, n: str, control: str) -> str:
    return (f'<tr><td><a href="/help/orders">?</a> {who}</td>'
            f'<td><form method="post" action="/cancel/{n}">{control}</form></td></tr>')


_DECOY_PRISTINE = _page(_decoy_row("Acme", "3", "<button>Cancel</button>")
                        + _decoy_row("Globex", "7", "<button>Cancel</button>"))
_DECOY_DRIFTED = _page(_decoy_row("Acme", "3", "<span>Cancelled</span>")
                       + _decoy_row("Globex", "7", "<button>Cancel</button>"))


async def test_a_hidden_record_field_is_a_row_identity() -> None:
    """Shared action, per-record hidden input. Before the redesign nothing here was even LOOKED at, so the
    row claimed no identity, the guard could not fire, and the cancel bound Globex's row under Acme's
    recorded Idempotency-Key — the original A1 wrong-row write, intact."""
    anchor, bound = await _capture_and_bind(_HIDDEN_PRISTINE, _HIDDEN_DRIFTED)
    assert anchor == "hidden:order=3", f"expected the hidden record key, got {anchor!r}"
    assert bound is None, "the recorded row's control is gone — binding the sibling is the wrong-row write"


async def test_a_hidden_record_field_still_binds_its_own_row() -> None:
    """The control. A guard that refuses the undrifted page is not a guard, it is an outage."""
    anchor, bound = await _capture_and_bind(_HIDDEN_PRISTINE, _HIDDEN_PRISTINE)
    assert anchor == "hidden:order=3"
    assert bound == "Acme"


async def test_a_shared_hidden_field_is_not_mistaken_for_an_identity() -> None:
    """A CSRF token stamped identically on every row is not a record key. Taking it would fabricate an
    identity that every sibling satisfies — the R2 failure, one field over."""
    # No `id` on the rows: an id is itself a fine identity and would win outright, so leaving one here
    # would make this test pass without the CSRF field ever being considered.
    rows = "".join(
        f'<tr><td>{who}</td>'
        f'<td><form method="post" action="/orders/cancel">'
        f'<input type="hidden" name="csrf" value="SAME"><button>Cancel</button></form></td></tr>'
        for who in ("Acme", "Globex"))
    anchor, _ = await _capture_and_bind(_page(rows), _page(rows))
    assert anchor != "hidden:csrf=SAME", "a token shared by every row tells them apart from nothing"
    # Nothing else discriminates either, so the honest answer is to claim NO identity at all.
    assert not anchor, f"nothing here tells the rows apart; claiming {anchor!r} would be a fabrication"


async def test_a_shared_decorative_link_does_not_hide_the_real_action() -> None:
    """Only the FIRST href was read. With a shared `/help/orders` link first in the row, the candidate was
    a token every sibling has — so the guard passed on every row and protected none of them."""
    anchor, bound = await _capture_and_bind(_DECOY_PRISTINE, _DECOY_DRIFTED)
    assert anchor == "href:/cancel/3", f"the real per-row action must win, got {anchor!r}"
    assert bound is None


async def test_the_decoy_row_still_binds_when_nothing_drifted() -> None:
    """Its control."""
    anchor, bound = await _capture_and_bind(_DECOY_PRISTINE, _DECOY_PRISTINE)
    assert anchor == "href:/cancel/3"
    assert bound == "? Acme"   # the cell also holds the decoy help link's text


async def test_a_one_row_capture_cannot_fabricate_an_identity_that_binds_a_sibling_later() -> None:
    """The wrong-row write this redesign actually closes, and the one an audit expected it to OPEN.

    On a page with a SINGLE row, nothing else exists to compare against, so any candidate is trivially
    "unique" — the discrimination test is vacuous. The worry is that a shared endpoint is then captured as
    an identity that is not one. What matters is what happens at BIND, and the two versions differ:

      0.72.0   captures `href:/orders/cancel`, and when the page later grows a second customer's row and
               the recorded row's own control is gone, it BINDS THE SIBLING — measured, the A1 wrong-row
               write, under the recorded row's Idempotency-Key.
      now      refuses, because the identity is re-derived at bind and `/orders/cancel` no longer
               discriminates once there is something to discriminate against.

    The vacuous capture is harmless precisely because the check is redone against the page in front of us
    rather than trusted from the recording. Pinning it because the direction is not obvious and the
    difference is a real write against a real stranger's record."""
    def row(who: str, control: str) -> str:
        return (f"<tr><td>{who}</td><td><form method='post' action='/orders/cancel'>"
                f"{control}</form></td></tr>")

    one_row = _page(row("Acme", "<button>Cancel</button>"))
    drifted = _page(row("Acme", "<span>Cancelled</span>") + row("Globex", "<button>Cancel</button>"))
    anchor, bound = await _capture_and_bind(one_row, drifted)
    assert anchor == "href:/orders/cancel", f"the one-row capture takes what it can, got {anchor!r}"
    assert bound is None, ("bound a sibling row's Cancel under the recorded row's key — the wrong-row "
                           "write this guard exists to stop")


async def test_capture_and_bind_compute_the_row_id_from_one_shared_snippet() -> None:
    """The structural half of the redesign, and the reason the two bugs above were possible at all.

    `rowIdOf` existed TWICE, in `_SPECOF_JS` and `_ROW_OF_JS`. The two must agree exactly — the guard
    compares a token computed by one to a token computed by the other — and round 3 found them already
    drifting. A behavioural test cannot see a divergence that has not happened yet, so this pins the
    property that makes it inexpressible: one definition, included verbatim by both.
    """
    from ultracua.locators import _ROW_OF_JS, _ROWID_JS, _SPECOF_JS

    assert _ROWID_JS.strip(), "the shared snippet must not be empty, or this test passes vacuously"
    assert _ROWID_JS in _SPECOF_JS, "capture must include the shared snippet, not its own copy"
    assert _ROWID_JS in _ROW_OF_JS, "bind must include the shared snippet, not its own copy"
    # Two copies would mean two `const rowIdOf` definitions in the same source text.
    for name, src in (("_SPECOF_JS", _SPECOF_JS), ("_ROW_OF_JS", _ROW_OF_JS)):
        assert src.count("const rowIdOf") == 1, f"{name} defines rowIdOf more than once"


async def test_a_row_with_nothing_discriminating_claims_no_identity() -> None:
    """The documented residual, pinned so it stays documented.

    Rows carrying no id, no per-row href, no `data-*` and no hidden field are genuinely indistinguishable
    in the DOM. The honest answer is to claim NO identity — inventing one from a shared attribute is the
    R2 fabrication. The guard therefore cannot fire on such a page, and the wrong-row write remains
    reachable there. Closing it needs a different signal (row text, ordinal position), which is a separate
    trade with its own false-refusal cost; it is NOT closed by this change and must not be assumed to be.
    """
    rows = ("<tr><td>Acme</td><td><form method='post' action='/cancel'>"
            "<span>Cancelled</span></form></td></tr>"
            "<tr><td>Globex</td><td><form method='post' action='/cancel'>"
            "<button>Cancel</button></form></td></tr>")
    pristine = rows.replace("<span>Cancelled</span>", "<button>Cancel</button>")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            await page.set_content(_page(pristine))
            await page.evaluate("() => document.querySelector('tr button')"
                                ".setAttribute('data-ultracua-ref', 'r1')")
            spec = LocatorSpec(**await page.evaluate(DESCRIBE_JS, "r1"))
        finally:
            await browser.close()
    assert not spec.anchor_id, (
        f"a row with no discriminating token must claim none, got {spec.anchor_id!r} — a fabricated "
        f"identity is worse than no identity")


@pytest.mark.parametrize("href", ["#", "javascript:void(0)", ""], ids=["hash", "js", "empty"])
async def test_placeholder_hrefs_are_never_identities(href: str) -> None:
    """`#` and `javascript:` links are on every row of every table on the web. Reading one as a record
    key is the same fabrication in its most common form."""
    rows = "".join(
        f'<tr id="order-{n}"><td><a href="{href}">{who}</a></td>'
        f'<td><form method="post" action="/cancel/{n}"><button>Cancel</button></form></td></tr>'
        for n, who in (("3", "Acme"), ("7", "Globex")))
    anchor, _ = await _capture_and_bind(_page(rows), _page(rows))
    assert anchor is not None and not anchor.startswith(("href:#", "href:javascript:", "href:'")), anchor
