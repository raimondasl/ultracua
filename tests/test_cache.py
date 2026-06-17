from __future__ import annotations

from pathlib import Path

import time

from ultracua.cache import SCHEMA_VERSION, CachedFlow, CachedStep, FlowCache, flow_key
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


def _flow(key: str, **kw) -> CachedFlow:
    base = dict(
        key=key,
        goal="g",
        start_url="https://e.com",
        created_ts=time.time(),
        steps=[CachedStep(intent="i", action="click")],
    )
    base.update(kw)
    return CachedFlow(**base)


def test_expired_entry_is_a_miss(tmp_path) -> None:
    cache = FlowCache(root=tmp_path, ttl_seconds=0.0)
    key = flow_key("g", "https://e.com")
    cache.put(_flow(key, created_ts=time.time() - 10))
    assert cache.get(key) is None  # aged past ttl=0 -> miss


def test_incompatible_schema_is_a_miss(tmp_path) -> None:
    cache = FlowCache(root=tmp_path)
    key = flow_key("g", "https://e.com")
    cache.put(_flow(key, schema_version=SCHEMA_VERSION - 1))
    assert cache.get(key) is None
    cache.put(_flow(key))  # current schema
    assert cache.get(key) is not None
