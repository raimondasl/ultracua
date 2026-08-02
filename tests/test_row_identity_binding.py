"""R1 + R2: the row-identity guard must check WHAT WE BOUND, not what exists somewhere on the page.

The 0.64.0 fix for A1 (a write firing against the wrong row) asked a question that sounds right and is not:
"does the recorded row's identity token still exist in the DOM?" That says nothing about the element
resolution actually returns, so the original wrong-row write came straight back through two ordinary shapes:

  * the recorded row SURVIVES but its own control is gone — an already-cancelled order rendering a
    `Cancelled` badge instead of a Cancel link. The token is present, so the old gate passed; the only
    remaining `Cancel` belonged to a different customer and Tier 1 bound it uniquely, outright.
  * the recorded row is HIDDEN rather than removed (`display:none` / `[hidden]`), the ordinary SPA
    client-side delete. The asymmetry is exact: `querySelectorAll` sees hidden rows, `get_by_role` does
    not — so hiding the recorded row is PRECISELY what makes the sibling uniquely matchable.

The mutation gate cannot catch either: per-row forms are structurally identical, so `scope_fingerprint`
matches byte-for-byte and the write fires under the recorded row's Idempotency-Key.

R2 is the other half. `rowIdOf` took ANY `data-*` in attribute order and returned it BEFORE looking at the
href — so a design-system table stamping `data-testid="order-row"` on every row produced an "identity" that
was present on every sibling, while the real one (`href:/cancel/3`) sat unread in the same row. A fabricated
identity is worse than none: the guard runs, reports satisfied, and protects nothing.

THE CONTROLS AT THE BOTTOM ARE THE POINT OF THE EXERCISE. A guard that refuses everything would pass every
test above and destroy the product. The measured cost of this change on the drift corpus is ZERO — the
survival curve, `bound_by` histogram, mechanism rates and `silent_wrong` are byte-identical to
`baselines/drift_v2.json` and all invariants hold — and these two tests are the unit-level statement of why.
"""

from __future__ import annotations

import http.server
import threading

import pytest
from playwright.async_api import async_playwright

from ultracua.locators import DESCRIBE_JS, LocatorSpec, resolve

# Two pending orders. Each row carries a real identity (an id AND a form action), and both rows' controls
# share the accessible name "Cancel" — the shape the 0.64.0 fix was written for.
_ROWS = """<!doctype html><html><body><table><tbody>
  <tr id="order-3"{a3}><td>Acme Corp</td><td>#3</td>
    <td><form method="post" action="/cancel/3">{c3}</form></td></tr>
  <tr id="order-7"><td>Globex</td><td>#7</td>
    <td><form method="post" action="/cancel/7"><button>Cancel</button></form></td></tr>
</tbody></table></body></html>"""

_CANCEL = "<button>Cancel</button>"
_PRISTINE = _ROWS.format(a3="", c3=_CANCEL)


async def _spec_of(html: str, row_sel: str):
    """Capture a spec for the Cancel control inside `row_sel`, exactly as learn/record would."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            await page.set_content(html)
            await page.evaluate(
                "(sel) => document.querySelector(sel + ' button')"
                ".setAttribute('data-ultracua-ref', 'r1')", row_sel)
            return LocatorSpec(**await page.evaluate(DESCRIBE_JS, "r1"))
        finally:
            await browser.close()


async def _bind(html: str, spec: LocatorSpec):
    """Resolve against `html`; return (the bound row's id, the bound form's action, bound_by)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            await page.set_content(html)
            sink: dict = {}
            loc = await resolve(page, spec, unique=True, sink=sink)
            if loc is None:
                return None, None, sink.get("bound_by")
            info = await loc.evaluate(
                "(el) => { const tr = el.closest('tr'), f = el.closest('form');"
                " return [tr ? tr.id : null, f ? f.getAttribute('action') : null]; }")
            return info[0], info[1], sink.get("bound_by")
        finally:
            await browser.close()


# ==================== R1: containment, not existence ====================

@pytest.mark.parametrize("drifted,label", [
    (_ROWS.format(a3="", c3="<span>Cancelled</span>"), "row survives, its control is gone"),
    (_ROWS.format(a3=' style="display:none"', c3=_CANCEL), "recorded row display:none"),
    (_ROWS.format(a3=" hidden", c3=_CANCEL), "recorded row [hidden]"),
], ids=["control-gone", "display-none", "hidden-attr"])
async def test_a_bind_outside_the_recorded_row_is_refused(drifted, label) -> None:
    spec = await _spec_of(_PRISTINE, "#order-3")
    assert spec.anchor_source == "row" and spec.anchor_id, spec

    row, action, bound_by = await _bind(drifted, spec)
    assert row is None, f"{label}: bound row {row!r} (action {action!r}) via {bound_by!r}"
    assert bound_by == "none"


# ==================== R2: the captured identity must be a real one ====================

_SHARED_TESTID = """<!doctype html><html><body><table><tbody>
  <tr data-testid="order-row"><td>Acme</td>
    <td><form method="post" action="/cancel/3"><button>Cancel</button></form></td></tr>
  <tr data-testid="order-row"><td>Globex</td>
    <td><form method="post" action="/cancel/7"><button>Cancel</button></form></td></tr>
</tbody></table></body></html>"""


async def test_a_shared_data_attribute_is_not_treated_as_a_row_identity() -> None:
    """`data-testid="order-row"` is on every row, so it identifies nothing. The real identity — the form
    action — is in the same row and must be what gets captured."""
    spec = await _spec_of(_SHARED_TESTID, "tr:nth-child(1)")
    assert spec.anchor_id == "href:/cancel/3", spec.anchor_id


async def test_the_href_identity_wins_over_a_per_row_data_attribute() -> None:
    """Even a UNIQUE data-* loses to the href: a URL embeds the record key, whereas a `data-index` is
    unique per row yet renumbers when a row is deleted."""
    html = """<!doctype html><html><body><table><tbody>
      <tr data-index="0"><td>Acme</td>
        <td><form method="post" action="/cancel/3"><button>Cancel</button></form></td></tr>
      <tr data-index="1"><td>Globex</td>
        <td><form method="post" action="/cancel/7"><button>Cancel</button></form></td></tr>
    </tbody></table></body></html>"""
    spec = await _spec_of(html, "tr:nth-child(1)")
    assert spec.anchor_id == "href:/cancel/3", spec.anchor_id


async def test_a_unique_data_attribute_is_still_accepted_when_it_is_all_there_is() -> None:
    """The uniqueness test must not throw away a genuine identity — a row with a per-record data-* and no
    href still gets one, or rows in JS-driven tables would lose the protection entirely."""
    html = """<!doctype html><html><body><ul>
      <li data-order-id="3"><span>Acme</span><button>Cancel</button></li>
      <li data-order-id="7"><span>Globex</span><button>Cancel</button></li>
    </ul></body></html>"""
    spec = await _spec_of(html, "li:nth-child(1)")
    assert spec.anchor_id == "data-order-id:3", spec.anchor_id


async def test_a_shared_data_attribute_with_no_other_identity_captures_none() -> None:
    """Nothing distinguishes these rows, so NO identity must be invented. `anchor_id=None` means the guard
    does not run at all — the documented token-less residual, which is honest; a fabricated identity would
    make the guard report satisfied while protecting nothing."""
    html = """<!doctype html><html><body><ul>
      <li data-testid="row"><span>Acme</span><button>Cancel</button></li>
      <li data-testid="row"><span>Globex</span><button>Cancel</button></li>
    </ul></body></html>"""
    spec = await _spec_of(html, "li:nth-child(1)")
    assert spec.anchor_id is None, spec.anchor_id


# ==================== the availability controls ====================

async def test_the_recorded_row_still_binds_when_nothing_drifted() -> None:
    """A guard that refuses everything would satisfy every test above. This is the floor."""
    spec = await _spec_of(_PRISTINE, "#order-3")
    row, action, bound_by = await _bind(_PRISTINE, spec)
    assert (row, action) == ("order-3", "/cancel/3"), (row, action, bound_by)


async def test_an_edited_row_still_binds() -> None:
    """Row TEXT is deliberately not the identity: a price or status changing does not make it a different
    record. A text-keyed version of this check was measured to cost 4 rows of 0-LLM survival."""
    spec = await _spec_of(_PRISTINE, "#order-3")
    edited = _PRISTINE.replace("<td>Acme Corp</td>", "<td>Acme Corporation GmbH</td>")
    row, action, _ = await _bind(edited, spec)
    assert (row, action) == ("order-3", "/cancel/3")


async def test_a_row_that_moved_still_binds() -> None:
    """Reordering rows is cosmetic. The identity travels with the row, so the bind must follow it."""
    spec = await _spec_of(_PRISTINE, "#order-3")
    reordered = """<!doctype html><html><body><table><tbody>
      <tr id="order-7"><td>Globex</td><td>#7</td>
        <td><form method="post" action="/cancel/7"><button>Cancel</button></form></td></tr>
      <tr id="order-3"><td>Acme Corp</td><td>#3</td>
        <td><form method="post" action="/cancel/3"><button>Cancel</button></form></td></tr>
    </tbody></table></body></html>"""
    row, action, _ = await _bind(reordered, spec)
    assert (row, action) == ("order-3", "/cancel/3")


async def test_a_non_row_anchor_is_untouched_by_the_guard() -> None:
    """The guard is scoped to `anchor_source == "row"`. A heading-anchored control must resolve exactly as
    before — a renamed section heading is a COSMETIC drift the resolver is required to survive."""
    html = """<!doctype html><html><body>
      <section><h2>Billing</h2><button>Save</button></section>
    </body></html>"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            await page.set_content(html)
            await page.evaluate("() => document.querySelector('button')"
                                ".setAttribute('data-ultracua-ref','r1')")
            spec = LocatorSpec(**await page.evaluate(DESCRIBE_JS, "r1"))
            assert spec.anchor_source == "heading"
            sink: dict = {}
            assert await resolve(page, spec, unique=True, sink=sink) is not None
            assert sink.get("row_mismatch") is None      # the guard never ran
        finally:
            await browser.close()
