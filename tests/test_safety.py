from __future__ import annotations

import asyncio

from ultracua.safety import (
    PacingGovernor,
    backoff_delay,
    idempotency_key,
    is_mutating,
    looks_like_interstitial,
    origin_of,
)


def test_is_mutating() -> None:
    assert is_mutating("click", intent="submit the order")
    assert is_mutating("click", name="Pay now")
    assert is_mutating("press", intent="press enter to submit")
    assert not is_mutating("click", intent="open the widget")
    assert not is_mutating("type", intent="submit")  # typing isn't itself mutating
    assert not is_mutating("scroll")


def test_idempotency_key_stable_and_scoped() -> None:
    a = idempotency_key("s", 2, "pay")
    assert a == idempotency_key("s", 2, "pay")
    assert a != idempotency_key("s", 3, "pay")
    assert a.startswith("uca-")


def test_origin_of() -> None:
    assert origin_of("https://Example.com/a/b?x=1#f") == "https://example.com"


def test_interstitial_detection() -> None:
    assert looks_like_interstitial(
        "https://x.com", "Just a moment...", "Checking your browser before accessing"
    )
    assert looks_like_interstitial("", "", "Please complete the reCAPTCHA")
    assert not looks_like_interstitial("https://x.com/cart", "Cart", "Added to cart")


def test_backoff_monotonic_and_capped() -> None:
    assert backoff_delay(0) < backoff_delay(3)
    assert backoff_delay(100) <= 30.0 + 0.5


async def test_pacing_governor_caps_concurrency() -> None:
    gov = PacingGovernor(per_origin_concurrency=2)
    active = 0
    peak = 0

    async def worker() -> None:
        nonlocal active, peak
        async with gov.gate("https://e.com"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*[worker() for _ in range(6)])
    assert peak <= 2
