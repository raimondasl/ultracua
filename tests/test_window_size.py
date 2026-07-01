"""window_size option: BrowserSession sizes the viewport/window, and ULTRACUA_WINDOW_SIZE parses safely.

Key-less (no ANTHROPIC_API_KEY) — launches a headless Chromium only.
"""

from __future__ import annotations

import pytest

from ultracua.browser import BrowserSession
from ultracua.config import _window_size


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1600x1000", (1600, 1000)),
        ("1600,1000", (1600, 1000)),
        ("1600X1000", (1600, 1000)),
        (" 1440 x 900 ", (1440, 900)),
        ("bad", None),
        ("1600", None),   # single value is not a size
        ("0x100", None),  # non-positive rejected (falls back to Playwright default)
        ("-5x100", None),
        ("", None),
        (None, None),
    ],
)
def test_window_size_env_parse(monkeypatch, raw, expected) -> None:
    if raw is None:
        monkeypatch.delenv("ULTRACUA_WINDOW_SIZE", raising=False)
    else:
        monkeypatch.setenv("ULTRACUA_WINDOW_SIZE", raw)
    assert _window_size() == expected


async def test_window_size_sets_headless_viewport() -> None:
    # Explicit window_size renders the (headless) page at exactly that size — the mechanism the demo
    # driver uses so the browser fills the recorded frame.
    session = await BrowserSession(headless=True, window_size=(1440, 900)).start()
    try:
        assert session.page.viewport_size == {"width": 1440, "height": 900}
        assert await session.page.evaluate("[innerWidth, innerHeight]") == [1440, 900]
    finally:
        await session.close()


async def test_no_window_size_keeps_default_viewport() -> None:
    # Regression guard: with no window_size (and no ULTRACUA_WINDOW_SIZE), the context keeps
    # Playwright's default viewport — we must not silently switch to no_viewport / a custom size.
    session = await BrowserSession(headless=True).start()
    try:
        assert session.page.viewport_size == {"width": 1280, "height": 720}
    finally:
        await session.close()
