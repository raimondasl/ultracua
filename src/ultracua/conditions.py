"""Page-condition presence check — shared by the Flow API's mutate confirm/precheck (`flows.py`) and the
replay engine's per-write commit barrier (`flow.py`).

Lives in its own module because `flows.py` imports `flow.py` (so `flow.py` can't import back), and both need
the same ANY-of "did the page reach this state?" predicate.
"""

from __future__ import annotations

import asyncio
from typing import Optional


async def condition_present(
    page, *, selector: Optional[str] = None, text_contains: Optional[str] = None,
    url_contains: Optional[str] = None, timeout_ms: Optional[int] = None,
) -> bool:
    """ANY-of presence check: True if any set condition (URL substring / body-text substring / selector)
    holds. Polls up to `timeout_ms` (default 5000) so a confirmation that renders a beat late isn't missed;
    pass `timeout_ms=0` for a single immediate check (a precheck / resume probe wants a fast decision)."""
    budget = 5000 if timeout_ms is None else timeout_ms
    interval = 200
    waited = 0
    while True:
        if url_contains and url_contains in page.url:
            return True
        if text_contains:
            try:
                body = await page.inner_text("body")
            except Exception:  # noqa: BLE001
                body = ""
            if text_contains.lower() in body.lower():
                return True
        if selector:
            try:
                await page.wait_for_selector(selector, timeout=interval)  # this consumes ~interval
                return True
            except Exception:  # noqa: BLE001
                pass
        waited += interval
        if waited >= budget:
            return False
        if not selector:  # selector branch already waited; otherwise pace the poll
            await asyncio.sleep(interval / 1000.0)
