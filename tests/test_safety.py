from __future__ import annotations

import asyncio

from ultracua.safety import (
    PacingGovernor,
    backoff_delay,
    idempotency_key,
    is_mutating,
    is_telemetry_host,
    is_write_request,
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


def test_is_telemetry_host() -> None:
    # Known beacon vendors (and their subdomains) are telemetry...
    assert is_telemetry_host("https://www.google-analytics.com/g/collect?v=2")
    assert is_telemetry_host("https://region1.google-analytics.com/g/collect")
    assert is_telemetry_host("https://api.segment.io/v1/batch")
    assert is_telemetry_host("https://o123.ingest.sentry.io/api/456/envelope/")
    assert is_telemetry_host("https://bam.nr-data.net/events")
    assert is_telemetry_host("https://stats.g.doubleclick.net/j/collect")
    # ...real (write-capable) hosts are NOT, and the suffix match is dot-boundaried.
    assert not is_telemetry_host("https://api.stripe.com/v1/charges")
    assert not is_telemetry_host("https://shop.example.com/cart/add")
    assert not is_telemetry_host("https://notgoogle-analytics.com/collect")  # not a real subdomain


def test_is_write_request() -> None:
    # Non-idempotent method to a non-telemetry host (any origin) = a write...
    assert is_write_request("POST", "https://shop.example.com/checkout")
    assert is_write_request("POST", "https://api.stripe.com/v1/charges")   # cross-origin write counts
    assert is_write_request("delete", "https://api.example.com/orders/7")  # method case-insensitive
    assert is_write_request("POST", "https://shop.example.com/collect")    # PATH is never denylisted
    # ...but idempotent reads and beacons are not.
    assert not is_write_request("GET", "https://shop.example.com/products")
    assert not is_write_request("POST", "https://www.google-analytics.com/g/collect")
    assert not is_write_request("POST", "https://api.segment.io/v1/batch")


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
