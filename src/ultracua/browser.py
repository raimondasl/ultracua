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
    def __init__(
        self,
        headless: bool | None = None,
        browser: Browser | None = None,
        record_har_path: str | None = None,
        storage_state: str | None = None,
    ) -> None:
        self.headless = settings.headless if headless is None else headless
        self._pw: Playwright | None = None
        # If a browser is provided, run as a fresh CONTEXT inside it (parallelism) and don't
        # own its lifecycle; otherwise launch (and later close) our own browser.
        self._shared_browser = browser
        self._owns_browser = browser is None
        # When set, record a Playwright HAR of all network activity to this path (flushed on
        # context close). This is the trace WebArena-Verified scores against (component:
        # benchmarks/webarena_env.py) — captured via the native record_har_* context options.
        self._record_har_path = record_har_path
        # Path to a Playwright storage_state JSON (cookies + localStorage) to seed the context —
        # cookie-based auth for a recurring Flow, so replay starts already logged in.
        self._storage_state = storage_state
        self.browser: Browser | None = browser
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def start(self) -> "BrowserSession":
        if self._shared_browser is None:
            self._pw = await async_playwright().start()
            self.browser = await self._pw.chromium.launch(headless=self.headless)
        context_kwargs: dict = {}
        if self._record_har_path:
            context_kwargs["record_har_path"] = self._record_har_path
            context_kwargs["record_har_content"] = "embed"
        if self._storage_state:
            context_kwargs["storage_state"] = self._storage_state
        self.context = await self.browser.new_context(**context_kwargs)
        self.context.set_default_timeout(settings.action_timeout_ms)
        self.context.set_default_navigation_timeout(settings.nav_timeout_ms)
        self.page = await self.context.new_page()
        return self

    async def goto(self, url: str) -> None:
        assert self.page is not None
        await self.page.goto(url, wait_until="domcontentloaded")

    async def set_extra_http_headers(self, headers: dict[str, str]) -> None:
        """Set (or clear, with {}) extra HTTP headers on the context — used to inject an
        Idempotency-Key around mutating actions so a retry can't duplicate a side effect."""
        assert self.context is not None
        await self.context.set_extra_http_headers(headers)

    async def screenshot(self) -> bytes:
        """Viewport screenshot (PNG bytes) — input for the vision fallback tier."""
        assert self.page is not None
        return await self.page.screenshot()

    async def snapshot(self) -> Observation:
        assert self.page is not None
        try:
            return await capture(self.page, settings.max_elements)
        except Exception as exc:  # noqa: BLE001
            # A snapshot can race a navigation triggered by the previous action — Playwright's
            # in-page evaluate then fails with "Execution context was destroyed". Wait for the
            # page to settle and retry once before giving up.
            if "context was destroyed" not in str(exc) and "navigation" not in str(exc).lower():
                raise
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=settings.nav_timeout_ms)
            except Exception:  # noqa: BLE001
                pass
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
        elif a == "click_xy":  # vision tier: click pixel coordinates
            x, y = (action.coords or [0, 0])[:2]
            await page.mouse.click(x, y)
        elif a == "webmcp_call":  # WebMCP tier: invoke a site-exposed tool
            from .webmcp import call as webmcp_call

            await webmcp_call(page, action.tool or "", action.args or {})
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
            if self._owns_browser and self.browser is not None:
                await self.browser.close()
        finally:
            if self._owns_browser and self._pw is not None:
                await self._pw.stop()

    async def __aenter__(self) -> "BrowserSession":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.close()
