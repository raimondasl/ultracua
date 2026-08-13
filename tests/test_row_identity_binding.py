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

from ultracua.locators import DESCRIBE_JS, LocatorSpec, _ROW_OF_JS, resolve

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


# ============ S11 / R3.7 — STILL OPEN, and the obvious fix was BUILT and MEASURED WRONG ============
#
# `anchorOf` (capture) and `_ROW_OF_JS` (bind) walk the DOM twice, in two transcriptions, under a comment
# asserting they mirror each other. They do not: `anchorOf` requires a row-like container to have
# non-empty collapsed text before anchoring on it and otherwise keeps climbing, while `_ROW_OF_JS` stops
# at the first row-like container unconditionally. A nested action list with an ICON-ONLY control makes
# the two name DIFFERENT containers, so replay refuses a bind on a page that never drifted.
#
# THE FIX EVERYONE REACHES FOR — share one walk, give the bind side the same non-empty-text condition —
# WAS WRITTEN, PASSED THE PARITY MATRIX BELOW, AND TURNED A CORRECT REFUSAL INTO A SILENT WRONG-ROW BIND.
# Measured at the wire: `POST /cancel/7` for a step recorded against `/cancel/3`, `bound_by='role+name'`,
# no `row_mismatch`, nothing logged. The reason is worth stating precisely, because it is not obvious and
# three independent auditors each had to reproduce it before believing it:
#
#   Skipping a text-less row-like container makes the bind walk climb OUT of the bound element's own row
#   into an ANCESTOR that contains it. `rowIdOf` then hands that ancestor an identity borrowed from a row
#   nested inside it — deliberately, since its `taken` set skips anything the container `contains`, so a
#   row's own link is not treated as evidence the value is shared. When the recorded row is the first
#   identity-bearing row inside that ancestor, the ancestor's identity string EQUALS the recorded one and
#   the guard compares equal for two different records.
#
# So PARITY IS NECESSARY AND NOT SUFFICIENT. "Capture and bind name the same row" is satisfied by a bind
# walk that has stopped being a containment check at all. The invariant that actually matters is
# CONTAINMENT — pinned by `test_a_bind_outside_the_recorded_record_is_refused_on_every_nesting_shape`
# below, which is GREEN against main and RED against the attempted fix. It is the artifact this slice
# exists to leave behind: whatever closes R3.7 has to keep it green.

_SHELLS = {
    "tr": ('<table><tbody>\n{rows}\n</tbody></table>',
           '<tr id="order-{n}"><td>Acme {n}</td><td>{payload}</td></tr>'),
    "li": ('<ul>\n{rows}\n</ul>',
           '<li id="order-{n}"><span>Acme {n}</span>{payload}</li>'),
    "role-listitem": ('<div role="list">\n{rows}\n</div>',
                      '<div role="listitem" id="order-{n}"><span>Acme {n}</span>{payload}</div>'),
}

# How far the control sits from its row. `direct` is the shape every earlier test in this file used.
_NESTINGS = {
    "direct": "{ctrl}",
    "nested-li": '<ul class="actions"><li>{ctrl}</li></ul>',
    "nested-li-deep": '<div class="cell"><ul class="actions"><li>{ctrl}</li></ul></div>',
}

# `icon-only` is the whole point: its container's collapsed text is empty, which is what the two walks
# disagree about. `text` is the control — it must keep working exactly as it does.
_CONTROLS = {
    "text": "<button>Cancel</button>",
    "icon-only": '<button aria-label="Cancel"><svg width="8" height="8"></svg></button>',
}


def _matrix_page(shell: str, nesting: str, control: str) -> str:
    wrap, row = _SHELLS[shell]
    rows = "\n".join(
        row.format(n=n, payload=_NESTINGS[nesting].format(
            ctrl='<form method="post" action="/cancel/%d">%s</form>' % (n, _CONTROLS[control])))
        for n in (3, 7))
    return "<!doctype html><html><body>" + wrap.format(rows=rows) + "</body></html>"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "R3.7 — OPEN. Capture and bind name different rows whenever a text-less row-like container sits "
    "between the control and its row. Kept STRICT so it XPASSes the moment the walks agree — at which "
    "point the containment property below is what says whether they were made to agree the RIGHT way. "
    "Parity alone was satisfied by BOTH failed attempts."))
async def test_capture_and_bind_name_the_same_row_on_every_nesting_shape() -> None:
    """The divergence itself, enumerated rather than described.

    Both halves of the report are load-bearing. A cell whose capture stops producing a row anchor stops
    exercising the guard entirely, so it is a FAILURE here rather than a skip: a matrix that quietly
    tests nothing is a shape this repo has shipped twice.
    """
    report: list[str] = []
    diverged: list[str] = []
    disabled: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            for shell in _SHELLS:
                for nesting in _NESTINGS:
                    for control in _CONTROLS:
                        await page.set_content(_matrix_page(shell, nesting, control))
                        await page.evaluate("() => document.querySelector('#order-3 button')"
                                            ".setAttribute('data-ultracua-ref','r1')")
                        spec = LocatorSpec(**await page.evaluate(DESCRIBE_JS, "r1"))
                        got = await page.locator('[data-ultracua-ref="r1"]').evaluate(_ROW_OF_JS)
                        cell = "%-14s %-15s %-10s" % (shell, nesting, control)
                        if spec.anchor_source != "row" or not spec.anchor_id:
                            disabled.append(cell)
                            report.append("%s GUARD OFF source=%r id=%r"
                                          % (cell, spec.anchor_source, spec.anchor_id))
                            continue
                        agree = got == spec.anchor_id
                        report.append("%s capture=%-18r bind=%-18r %s"
                                      % (cell, spec.anchor_id, got, "ok" if agree else "DIVERGES"))
                        if not agree:
                            diverged.append(cell)
        finally:
            await browser.close()
    printed = "\n".join(report)
    print(printed)
    assert not disabled, "these cells no longer exercise the row guard at all:\n" + printed
    assert not diverged, "capture and bind name DIFFERENT rows:\n" + printed


# ==================== THE CONTAINMENT PROPERTY — the invariant the parity one is not ====================
#
# Every control carries `data-record`; the recorded one is always 3. The page is then drifted the way
# `resolve`'s own docstring describes — the recorded row survives, its control does not — which is
# precisely what makes a sibling's control uniquely matchable at Tier 1.

_ICON = '<button aria-label="Cancel" data-record="%d"><svg width="8" height="8"></svg></button>'
_TEXT = '<button data-record="%d">Cancel</button>'
_CANCELLED = "<span>Cancelled</span>"

# A customer row containing two SUBSCRIPTION rows. The recorded subscription carries the only
# identity-bearing link, so the enclosing `<tr>` inherits that same identity string — this is the shape
# the first fix attempt bound wrongly.
_GROUPED = """<!doctype html><html><body><table><tbody>
  <tr><td>Acme</td><td><ul>
    <li><a href="/sub/3">Plan A</a><form method="post" action="/cancel/3">%s</form></li>
    <li><form method="post" action="/cancel/7">%s</form></li>
  </ul></td></tr>
  <tr><td>Globex</td><td><ul>
    <li><a href="/sub/9">Plan B</a><form method="post" action="/cancel/9">
      <button aria-label="Remove"><svg/></button></form></li>
  </ul></td></tr>
</tbody></table></body></html>"""

# A nested action list on flat rows — R3.7's own shape.
_NESTED = """<!doctype html><html><body><table><tbody>
  <tr id="order-3"><td>Acme</td><td><ul class="actions"><li>
    <form method="post" action="/cancel/3">%s</form></li></ul></td></tr>
  <tr id="order-7"><td>Globex</td><td><ul class="actions"><li>
    <form method="post" action="/cancel/7">%s</form></li></ul></td></tr>
</tbody></table></body></html>"""

# The control shape: the control sits directly in the row.
_FLAT = """<!doctype html><html><body><table><tbody>
  <tr id="order-3"><td>Acme</td><td><form method="post" action="/cancel/3">%s</form></td></tr>
  <tr id="order-7"><td>Globex</td><td><form method="post" action="/cancel/7">%s</form></td></tr>
</tbody></table></body></html>"""

# A GROUP list item wrapping the record items, with one shared endpoint and a per-record hidden field —
# `rowIdOf`'s own comment calls this "the very common" server-rendered shape.
_GROUP_HIDDEN = """<!doctype html><html><body><ul>
  <li>Pending<ul>
    <li><form method="post" action="/cancel">
        <input type="hidden" name="rec" value="3">%s</form></li>
    <li><form method="post" action="/cancel">
        <input type="hidden" name="rec" value="7">%s</form></li>
  </ul></li>
</ul></body></html>"""

# R4.34's shape: the nested per-row action container carries a design-system `aria-label`, identical on
# every row. Capture's anchor TEXT therefore comes from that label (`anchor_source='label'`), which used
# to mean no `anchor_id` was captured at all and the guard silently did not run on a per-record control.
# This shape is the population the fix newly ARMS, and `drift_bench` cannot see it — every heading-
# anchored corpus scenario has no enclosing row at all — so it is measured here instead.
_LABELLED_NEST = """<!doctype html><html><body><table><tbody>
  <tr id="order-3"><td>Acme</td><td><ul class="actions"><li aria-label="Row actions">
    <form method="post" action="/cancel/3">%s</form></li></ul></td></tr>
  <tr id="order-7"><td>Globex</td><td><ul class="actions"><li aria-label="Row actions">
    <form method="post" action="/cancel/7">%s</form></li></ul></td></tr>
</tbody></table></body></html>"""

# R4.37's shape, and the one that caught R3.7's second attempt. The action wrapper owns NOTHING — no
# `form[action]`, no `a[href]`, no hidden input, no `data-*` — while the record key sits one landmark up
# on the `<tr>`. Every other shape in this file and in the drift corpus puts an identity INSIDE the
# wrapper, which is why two fix attempts and a 185-row corpus were all blind to it.
_BARE_NEST = """<!doctype html><html><body><table><tbody>
  <tr data-order-id="3"><td>Acme Corp</td><td><ul class="actions"><li>%s</li></ul></td></tr>
  <tr data-order-id="7"><td>Globex</td><td><ul class="actions"><li>%s</li></ul></td></tr>
</tbody></table></body></html>"""

_CONTAINMENT_SHAPES = [
    ("bare-nest/icon", _BARE_NEST, _ICON),
    ("grouped-rows/icon", _GROUPED, _ICON),
    ("grouped-rows/text", _GROUPED, _TEXT),
    ("nested-actions/icon", _NESTED, _ICON),
    ("nested-actions/text", _NESTED, _TEXT),
    ("flat-rows/icon", _FLAT, _ICON),
    ("flat-rows/text", _FLAT, _TEXT),
    ("group-hidden/icon", _GROUP_HIDDEN, _ICON),
]


async def test_a_bind_outside_the_recorded_record_is_refused_on_every_nesting_shape() -> None:
    """THE property, and the one artifact of this slice that must outlive it.

    For every shape: with the recorded control gone, `resolve` must refuse or bind a control belonging to
    the SAME record. Binding a different record's control is the wrong-row write the guard exists to stop,
    and it fires under the recorded row's Idempotency-Key, so the mutation gate cannot object — per-row
    forms are structurally identical and the scope fingerprint matches byte-for-byte.

    The pristine column is not decoration. A guard that refuses everything satisfies the safety half
    completely, and that regression has actually shipped here before — so a shape count is asserted on it.
    """
    report: list[str] = []
    wrong: list[str] = []
    bound_pristine = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            for name, tpl, control in _CONTAINMENT_SHAPES:
                await page.set_content(tpl % (control % 3, control % 7))
                await page.evaluate("() => document.querySelector('[data-record=\"3\"]')"
                                    ".setAttribute('data-ultracua-ref','r1')")
                spec = LocatorSpec(**await page.evaluate(DESCRIBE_JS, "r1"))

                pristine = await resolve(page, spec, unique=True, sink={})
                pristine_rec = None if pristine is None else await pristine.evaluate(
                    "(el) => el.getAttribute('data-record')")
                if pristine_rec == "3":
                    bound_pristine += 1

                await page.set_content(tpl % (_CANCELLED, control % 7))
                sink: dict = {}
                loc = await resolve(page, spec, unique=True, sink=sink)
                got = "REFUSED" if loc is None else "record " + str(
                    await loc.evaluate("(el) => el.getAttribute('data-record')"))
                ok = loc is None or got == "record 3"
                if not ok:
                    wrong.append(f"{name} -> {got}")
                report.append("%-20s id=%-18r pristine=%-8s gone=%-10s by=%-12r %s"
                              % (name, spec.anchor_id, pristine_rec, got,
                                 sink.get("bound_by"), "ok" if ok else "*** WRONG RECORD ***"))
        finally:
            await browser.close()
    printed = "\n".join(report)
    print(printed)
    assert not wrong, "bound a DIFFERENT record's control:\n" + printed
    assert bound_pristine >= 3, (
        f"only {bound_pristine} shapes bind the recorded control on an UNTOUCHED page, so the refusals "
        f"above prove nothing — a guard that refuses everything passes the safety half:\n{printed}")


# The end-to-end consequence of R3.7, on the exact shape it names. Per-row-unique control names so Tier 1
# binds outright — the point is that resolution SUCCEEDS and the guard then throws the correct bind away.
_NESTED_ICON = """<!doctype html><html><body><table><tbody>
  <tr id="order-3"><td>Acme Corp</td><td><ul class="actions"><li>
    <form method="post" action="/cancel/3">{c3}</form></li></ul></td></tr>
  <tr id="order-7"><td>Globex</td><td><ul class="actions"><li>
    <form method="post" action="/cancel/7">{c7}</form></li></ul></td></tr>
</tbody></table></body></html>"""

_UNIQUE_ICON = '<button aria-label="Cancel order %d"><svg width="8" height="8"></svg></button>'
_NESTED_PRISTINE = _NESTED_ICON.format(c3=_UNIQUE_ICON % 3, c7=_UNIQUE_ICON % 7)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "R3.7 — OPEN. Learn succeeds and caches; every replay afterwards refuses at the bind with "
    "`row_mismatch`, accusing a pristine page of a drift that did not happen and routing the step into "
    "the heal LLM."))
async def test_a_nested_icon_only_control_binds_on_a_page_that_never_drifted() -> None:
    spec = await _spec_of(_NESTED_PRISTINE, "#order-3")
    assert (spec.anchor_source, spec.anchor_id) == ("row", "id:order-3"), spec

    row, action, bound_by = await _bind(_NESTED_PRISTINE, spec)
    assert (row, action) == ("order-3", "/cancel/3"), (row, action, bound_by)


# Both controls share one accessible name, so removing the recorded row's control is exactly what makes
# the sibling's uniquely matchable — R1's shape, on R3.7's markup.
_SHARED_ICON = '<button aria-label="Cancel"><svg width="8" height="8"></svg></button>'
_NESTED_SHARED = _NESTED_ICON.format(c3=_SHARED_ICON, c7=_SHARED_ICON)
_NESTED_SHARED_GONE = _NESTED_ICON.format(c3="<span>Cancelled</span>", c7=_SHARED_ICON)


async def test_a_nested_icon_only_bind_outside_the_recorded_row_is_still_refused() -> None:
    """The direction that must NOT move, on R3.7's own markup — whatever eventually closes R3.7."""
    spec = await _spec_of(_NESTED_SHARED, "#order-3")
    assert spec.anchor_source == "row" and spec.anchor_id, spec

    row, action, bound_by = await _bind(_NESTED_SHARED_GONE, spec)
    assert row is None, f"bound row {row!r} (action {action!r}) via {bound_by!r}"
    assert bound_by == "none"


# ==================== R4.34, OPEN: a labelled nested container turns the guard OFF ====================
#
# Found by the S11 matrix, and it is R3.7's root cause pointed the other way. `anchorOf` checks
# aria-label BEFORE row-ness at every hop, so a nested per-row action container carrying a
# design-system label — `<li aria-label="Row actions">`, `"Item actions"`, `"More options"` — makes the
# capture return `source='label'` at the INNER container and never reach the row. The guard is scoped to
# `anchor_source == "row"`, so it silently does not run, and `anchor_id` is not even captured.
#
# The exemption itself is deliberate and documented in `_resolve`: a renamed SECTION heading is cosmetic
# drift the resolver must survive. What was never considered is that the same exemption reaches ROWS
# through a nested container — the sibling-guard shape this codebase keeps re-finding.
#
# This is strictly worse than R3.7: R3.7 fails LOUD (a false refusal), this one fails WRONG. Measured
# against 0.99.0 and 0.100.0 — `POST /cancel/7` where `/cancel/3` was recorded, `bound_by='role+name'`,
# no `row_mismatch`, nothing logged. On a write flow that is inviolable #3 under the recorded row's
# Idempotency-Key, and the mutation gate cannot object because per-row forms are structurally identical.
#
# NOT fixed here on purpose. The candidate remedy — capture `anchor_id` from the enclosing row whatever
# the anchor source, and scope the guard on `anchor_id` alone — changes what capture MEANS, invalidates
# no cached spec but re-arms the guard on a population that has never had it, and is exactly the kind of
# resolver trade `drift_bench` exists to adjudicate. S11 fixes one walk; this gets its own slice and its
# own audit.
_LABELLED_NEST = """<!doctype html><html><body><table><tbody>
  <tr id="order-3"><td>Acme Corp</td><td><ul class="actions"><li aria-label="Row actions">
    <form method="post" action="/cancel/3">{c3}</form></li></ul></td></tr>
  <tr id="order-7"><td>Globex</td><td><ul class="actions"><li aria-label="Row actions">
    <form method="post" action="/cancel/7"><button>Cancel</button></form></li></ul></td></tr>
</tbody></table></body></html>"""


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "R4.37 — OPEN. A control whose nested wrapper owns no identity captures `anchor_id=None`, so the "
    "row guard never runs even though the RECORD key is one landmark up on the `<tr>`. Silent wrong-row "
    "bind, inviolable #3, live on main and independent of R3.7's fix attempts."))
async def test_a_control_in_an_identityless_wrapper_is_still_guarded_by_its_row() -> None:
    """The text-control half of `bare-nest`. Its icon-only twin is in the containment matrix above and
    is GREEN — capture climbs past the text-less wrapper to the `<tr>` and arms the guard. Here the
    wrapper has text (the control's own label), so capture stops at it, finds no identity, and the guard
    silently does not run. The two halves differ only in whether the control has visible text."""
    spec = await _spec_of(_BARE_NEST % (_TEXT % 3, _TEXT % 7), "tr:nth-child(1)")
    assert spec.anchor_id, f"no identity captured, so the guard cannot run: {spec!r}"

    row, action, bound_by = await _bind(_BARE_NEST % ("<span>Cancelled</span>", _TEXT % 7), spec)
    assert row is None, f"bound row {row!r} (action {action!r}) via {bound_by!r}"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "R4.34 — OPEN. A shared aria-label on the nested per-row action container makes capture anchor on it "
    "with source='label', so no `anchor_id` is captured, the row guard never runs, and a wrong-row bind "
    "goes through silently. Inviolable #3 on a write flow. Two fixes have now been measured wrong here; "
    "see R3.7 and D5 before attempting a third."))
async def test_a_labelled_nested_container_must_not_disable_the_row_guard() -> None:
    """Kept STRICT so whoever re-arms the guard on this population finds it XPASSing."""
    spec = await _spec_of(_LABELLED_NEST.format(c3="<button>Cancel</button>"), "#order-3")
    assert spec.anchor_source == "label", spec        # the premise: capture never reached the row

    row, action, bound_by = await _bind(
        _LABELLED_NEST.format(c3="<span>Cancelled</span>"), spec)
    assert row is None, (
        f"bound row {row!r} (action {action!r}) via {bound_by!r} — the recorded row was order-3, "
        f"and nothing refused")
