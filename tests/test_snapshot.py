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


CURSOR_HTML = """<!doctype html><html><head><style>.lk{cursor:pointer}</style></head>
<body>
  <span class="lk">Pseudo Link</span>
  <span>plain text</span>
</body></html>"""


async def test_capture_includes_cursor_pointer_leaves() -> None:
    """JS-listener clickables (no onclick attr / non-semantic tag) are caught via
    computed cursor:pointer — the Phase 2 fix that makes MiniWoB span-links visible."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await (await browser.new_context()).new_page()
        await page.set_content(CURSOR_HTML)
        obs = await capture(page, 50)
        await browser.close()

    names = {e.name for e in obs.elements}
    assert "Pseudo Link" in names      # cursor:pointer span detected
    assert "plain text" not in names   # ordinary text is not
