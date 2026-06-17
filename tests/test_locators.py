"""Resilient-locator fallback: a spec whose brittle anchors (id/test-id/css) no longer
match still resolves via role+name — the Phase-1 self-healing-lite that survives DOM
drift with no LLM."""

from __future__ import annotations

from playwright.async_api import async_playwright

from ultracua.locators import LocatorSpec, resolve

# The page changed since record time: the button lost its id/test-id and its css path
# moved, but its role and accessible name are unchanged.
DRIFTED_HTML = """<!doctype html><html><body>
  <div><section>
    <button class="brand-new-class">Add to cart</button>
  </section></div>
</body></html>"""


async def test_resolve_survives_id_and_css_drift() -> None:
    spec = LocatorSpec(
        role="button",
        name="Add to cart",
        tag="button",
        elem_id="old-add-id",          # gone
        testid="old-add-testid",       # gone
        css="body > button",           # no longer the real path
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await (await browser.new_context()).new_page()
        await page.set_content(DRIFTED_HTML)
        loc = await resolve(page, spec)
        assert loc is not None
        assert (await loc.inner_text()).strip() == "Add to cart"
        await browser.close()
