"""The scoped snapshot pipeline finds interactable elements and tags them with refs."""

from __future__ import annotations

from playwright.async_api import async_playwright

from ultracua.snapshot import capture

HTML = """<!doctype html><html><body>
  <button id="b">Click me</button>
  <input type="text" placeholder="Search the site">
  <a href="#x">A link</a>
  <button style="display:none">Hidden</button>
</body></html>"""


async def test_capture_finds_visible_interactables() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await (await browser.new_context()).new_page()
        await page.set_content(HTML)
        obs = await capture(page, 50)
        await browser.close()

    roles = {e.role for e in obs.elements}
    names = {e.name for e in obs.elements}
    assert {"button", "textbox", "link"} <= roles
    assert "Click me" in names
    assert "Hidden" not in names  # display:none is filtered in-page
    assert all(e.ref.startswith("e") for e in obs.elements)
    assert obs.fingerprint
