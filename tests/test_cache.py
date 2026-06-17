from __future__ import annotations

from pathlib import Path

from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key
from ultracua.locators import LocatorSpec


def test_flow_key_normalizes_goal_and_url() -> None:
    a = flow_key("Add to Cart", "https://Example.com/path/")
    b = flow_key("  add   to cart ", "https://example.com/path")
    assert a == b
    assert flow_key("x", "https://e.com") != flow_key("y", "https://e.com")
    assert flow_key("x", "https://e.com", scope="s1") != flow_key("x", "https://e.com", scope="s2")


def test_cache_round_trip(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path)
    key = flow_key("g", "https://e.com")
    assert cache.get(key) is None

    flow = CachedFlow(
        key=key,
        goal="g",
        start_url="https://e.com",
        created_ts=1.0,
        steps=[
            CachedStep(
                intent="click submit",
                action="click",
                locator=LocatorSpec(role="button", name="Submit", tag="button"),
            )
        ],
    )
    cache.put(flow)

    got = cache.get(key)
    assert got is not None
    assert got.steps[0].locator is not None
    assert got.steps[0].locator.name == "Submit"

    assert cache.delete(key) is True
    assert cache.get(key) is None
