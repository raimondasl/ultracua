"""Warm browser session — the long-lived process that holds a persistent
Playwright/CDP connection (PLAN.md component 1).

Keeping this hot across steps is the single biggest structural win: it avoids the
150-400ms per-step reconnect tax. Phase 0 uses Playwright's high-level actions, which
give us actionability checks + auto-waiting for free (the reliability win); Phase 1+
will reach for the raw CDPSession on the cached fast-path.
"""

from __future__ import annotations

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from .config import settings
from .snapshot import capture
from .types import Action, Observation


class BrowserSession:
    def __init__(self, headless: bool | None = None) -> None:
        self.headless = settings.headless if headless is None else headless
        self._pw: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def start(self) -> "BrowserSession":
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context()
        self.context.set_default_timeout(settings.action_timeout_ms)
        self.context.set_default_navigation_timeout(settings.nav_timeout_ms)
        self.page = await self.context.new_page()
        return self

    async def goto(self, url: str) -> None:
        assert self.page is not None
        await self.page.goto(url, wait_until="domcontentloaded")

    async def snapshot(self) -> Observation:
        assert self.page is not None
        return await capture(self.page, settings.max_elements)

    async def act(self, action: Action) -> None:
        """Execute a canonical action. Playwright's built-in actionability checks
        (visible/stable/enabled/receives-events) gate clicks and fills automatically."""
        assert self.page is not None
        page = self.page
        a = action.action
        if a == "click":
            await page.click(self._sel(action.ref))
        elif a == "type":
            await page.fill(self._sel(action.ref), action.text or "")
        elif a == "press":
            await page.keyboard.press(action.text or "Enter")
        elif a == "scroll":
            await page.mouse.wheel(0, 600)
        elif a == "navigate":
            await self.goto(action.text or "about:blank")
        # done / give_up are terminal no-ops handled by the agent loop.

    @staticmethod
    def _sel(ref: str | None) -> str:
        if not ref:
            raise ValueError("action requires an element ref")
        return f'[data-ultracua-ref="{ref}"]'

    async def close(self) -> None:
        try:
            if self.context is not None:
                await self.context.close()
            if self.browser is not None:
                await self.browser.close()
        finally:
            if self._pw is not None:
                await self._pw.stop()

    async def __aenter__(self) -> "BrowserSession":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.close()
