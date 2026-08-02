"""A3: Tier 1's `role+name~` was a SUBSTRING match dressed as an identity anchor.

`get_by_role(..., exact=False)` is a case-insensitive substring match on the accessible name. It sat
SECOND in Tier 1 — above placeholder, exact-text and element id — and the Tier-1 loop returns the first
unique match OUTRIGHT, so a substring hit short-circuited all three exact anchors and never reached the
Tier-2 css cross-check that exists to stop exactly this class of mis-bind.

The failure needs no drift beyond a rename: the recorded target's label changes, three intact exact
anchors still point at the correct element, and an unrelated control whose name merely CONTAINS the cached
one ("Coupon code" for "Code") single-matches and binds. As a `type` step it fills the WRONG field —
universal surface, no writes required, 0 LLM, reports success. `unique=True` cannot help: the wrong match
has count 1.

THE FIX IS A REORDER, NOT A DELETION, and the difference is measured. `role+name~` earns a Tier-1 place by
re-finding a control whose label was lightly AUGMENTED ("Proceed" -> "Proceed now") when no exact anchor
survives; deleting it loses that. Moving it last costs nothing on the drift corpus and closes the
short-circuit. The second test here is the guard that keeps a later reader from "simplifying" the demotion
into a deletion.

RESIDUAL, deliberately not hidden: when `role+name~` is the ONLY surviving Tier-1 candidate and a substring
decoy exists, it still binds outright with no corroboration. Closing that needs a css-agreement gate like
Tier 2's, which measured at the same corpus cost as full deletion.
"""

from __future__ import annotations

from playwright.async_api import async_playwright

from ultracua.locators import DESCRIBE_JS, LocatorSpec, resolve


async def _spec_from(html: str, marker: str) -> LocatorSpec:
    """Describe the element carrying `data-o=<marker>` on the PRISTINE page, exactly as learn would."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            await page.set_content(html)
            await page.evaluate(
                "(m) => document.querySelector('[data-o=' + m + ']')"
                ".setAttribute('data-ultracua-ref', 'r1')", marker)
            return LocatorSpec(**await page.evaluate(DESCRIBE_JS, "r1"))
        finally:
            await browser.close()


async def _bind(html: str, spec: LocatorSpec):
    """Resolve `spec` against `html`; returns (data-o of the bound element, the candidate that bound)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            await page.set_content(html)
            sink: dict = {}
            loc = await resolve(page, spec, unique=True, sink=sink)
            got = await loc.get_attribute("data-o") if loc is not None else None
            return got, sink.get("bound_by")
        finally:
            await browser.close()


# The decoy's accessible name CONTAINS the target's: "Coupon code" contains "Code". Both are textboxes,
# so `get_by_role("textbox", name="Code", exact=False)` matches the decoy once the target is renamed.
_COUPON = """<!doctype html><html><body><form>
  <label id="lt" for="code">{first}</label>
  <input id="code" data-o="TARGET" placeholder="Gift code">
  <label for="coupon">Coupon code</label>
  <input id="coupon" data-o="OTHER" placeholder="Coupon">
  <button type="submit">Apply</button>
</form></body></html>"""

_COUPON_PRISTINE = _COUPON.format(first="Code")
_COUPON_DRIFTED = _COUPON.format(first="Voucher")     # ONLY the label text changed


async def test_a_renamed_label_never_binds_a_substring_decoy_over_an_exact_anchor() -> None:
    spec = await _spec_from(_COUPON_PRISTINE, "TARGET")
    # The premise: three EXACT anchors survive the rename, so a correct resolver has every chance.
    assert spec.name == "Code" and spec.placeholder == "Gift code" and spec.elem_id == "code"

    got, bound_by = await _bind(_COUPON_DRIFTED, spec)
    assert got == "TARGET", f"bound the decoy via {bound_by!r}"
    # Pin the ORDER, not just the outcome: an exact identity anchor must be what carried it. Without this
    # the test would still pass if role+name~ happened to match the target for some unrelated reason.
    assert bound_by in ("placeholder", "exact-text", "elem_id"), bound_by


_AUGMENT_PRISTINE = ('<!doctype html><html><body>'
                     '<a href="/next" data-o="TARGET">Proceed</a></body></html>')
# The label is lightly AUGMENTED and there is no decoy — no id, no testid, no placeholder, and exact-text
# is dead. `role+name~` is the only thing that can still find this, which is why it stays in Tier 1.
_AUGMENT_DRIFTED = ('<!doctype html><html><body>'
                    '<a href="/next" data-o="TARGET">Proceed now</a></body></html>')


async def test_role_name_substring_still_rescues_a_lightly_augmented_label() -> None:
    """The guard that keeps the demotion from silently becoming a deletion. This passes both before and
    after the reorder — it is the unit-level stand-in for the drift-corpus rows that would go dark."""
    spec = await _spec_from(_AUGMENT_PRISTINE, "TARGET")
    assert spec.name == "Proceed" and not spec.elem_id and not spec.testid and not spec.placeholder

    got, bound_by = await _bind(_AUGMENT_DRIFTED, spec)
    assert got == "TARGET"
    assert bound_by == "role+name~", bound_by


async def test_an_exact_role_name_match_still_wins_first() -> None:
    """The other control: on an UNCHANGED page the cheapest exact anchor must still carry the bind, so the
    reorder cannot have quietly pushed ordinary traffic onto a later candidate."""
    spec = await _spec_from(_AUGMENT_PRISTINE, "TARGET")
    got, bound_by = await _bind(_AUGMENT_PRISTINE, spec)
    assert got == "TARGET"
    assert bound_by == "role+name", bound_by
