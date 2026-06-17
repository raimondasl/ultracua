"""WebMCP tier: detect a site's tools and invoke them; learn a webmcp_call step and replay
it with no LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.types import Action, Observation
from ultracua.webmcp import call, detect

_FIX = Path(__file__).parents[1] / "benchmarks" / "fixtures" / "webmcp.html"
URL = _FIX.resolve().as_uri()


async def test_webmcp_detect_and_call() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await (await browser.new_context()).new_page()
        await page.goto(URL)
        tools = await detect(page)
        assert tools and tools[0]["name"] == "add_to_cart"
        res = await call(page, "add_to_cart", {"sku": "WIDGET"})
        assert res.get("ok") is True
        assert "Added WIDGET to cart" in await page.inner_text("body")
        await browser.close()


class _WebmcpProvider:
    def __init__(self) -> None:
        self.n = 0

    async def decide(
        self, goal: str, obs: Observation, history: list[str]
    ) -> tuple[Action, Optional[float]]:
        self.n += 1
        if self.n == 1:
            return Action(action="webmcp_call", intent="add the widget via WebMCP",
                          tool="add_to_cart", args={"sku": "WIDGET"}), None
        return Action(action="done", intent="done"), None


async def test_webmcp_learn_then_replay(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path)
    goal = "add the widget to the cart"

    learn = await run_cached(URL, goal, _WebmcpProvider(), cache, mode="learn", headless=True)
    assert learn.success
    flow = cache.get(flow_key(goal, URL))
    assert flow is not None
    assert any(
        s.action == "webmcp_call" and s.tool == "add_to_cart" and s.args == {"sku": "WIDGET"}
        for s in flow.steps
    )

    replay = await run_cached(URL, goal, None, cache, mode="replay", headless=True)
    assert replay.success
    assert replay.llm_calls == 0
    assert "Added WIDGET to cart" in replay.final_text
