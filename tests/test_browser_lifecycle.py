"""BrowserSession lifecycle — a failure partway through start() must not leak the driver/Chromium."""

from __future__ import annotations

import pytest

from ultracua.browser import BrowserSession


async def test_start_tears_down_on_post_launch_failure(tmp_path) -> None:
    # A missing storage_state file makes new_context() raise AFTER the browser has launched. Without
    # the cleanup, the caller's `session = await ...start()` never binds, so its `finally: close()`
    # never runs and the launched Chromium + driver leak. Assert start() tore them down and re-raised.
    session = BrowserSession(headless=True, storage_state=str(tmp_path / "does_not_exist.json"))
    closed = {"n": 0}
    orig_close = session.close

    async def spy() -> None:
        closed["n"] += 1
        await orig_close()

    session.close = spy  # type: ignore[method-assign]
    with pytest.raises(Exception):  # noqa: B017 - any launch/context error is fine; we assert cleanup
        await session.start()
    assert closed["n"] == 1  # cleanup ran exactly once
    # the launched browser was actually closed — nothing left connected to leak
    assert session.browser is None or not session.browser.is_connected()
