"""LLM-facing tier integration: the agent's action schema exposes webmcp_call/need_vision,
the provider parses WebMCP args + renders WebMCP tools, and (via a MockClient) the agent
drives the WebMCP and vision tiers end to end."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.llm.base import Router, Tier
from ultracua.llm.mock import MockClient
from ultracua.providers.base import ACTION_TOOL
from ultracua.providers.llm_agent import LLMAgentProvider, _parse, _render
from ultracua.types import Observation
from ultracua.vision import MockGrounding

_FIX = Path(__file__).parents[1] / "benchmarks" / "fixtures"


def test_action_schema_exposes_new_tiers() -> None:
    props = ACTION_TOOL["input_schema"]["properties"]
    enum = props["action"]["enum"]
    assert "webmcp_call" in enum and "need_vision" in enum
    assert "tool" in props and "args" in props


def test_parse_decodes_webmcp_args_string() -> None:
    tu = NS(name="act", type="tool_use", input={
        "action": "webmcp_call", "intent": "x", "tool": "add_to_cart", "args": '{"sku": "WIDGET"}',
    })
    resp = NS(tool_use=lambda name=None: tu)
    action = _parse(resp)
    assert action is not None
    assert action.action == "webmcp_call" and action.tool == "add_to_cart"
    assert action.args == {"sku": "WIDGET"}  # JSON string decoded to a dict


def test_render_lists_webmcp_tools() -> None:
    obs = Observation(url="u", title="t", elements=[], text="",
                      webmcp_tools=[{"name": "add_to_cart", "description": "add"}], fingerprint="f")
    out = _render(obs, "buy", [])
    assert "WEBMCP TOOLS" in out and "add_to_cart" in out


async def test_agent_drives_webmcp_call(tmp_path: Path) -> None:
    url = (_FIX / "webmcp.html").resolve().as_uri()
    fast = MockClient(actions=[
        {"action": "webmcp_call", "intent": "add via webmcp", "tool": "add_to_cart",
         "args": '{"sku": "WIDGET"}'},
        {"action": "done", "intent": "done"},
    ])
    prov = LLMAgentProvider(Router(fast=Tier(fast, "h")), tier="fast")
    cache = FlowCache(root=tmp_path)

    learn = await run_cached(url, "add the widget", prov, cache, mode="learn", headless=True)
    assert learn.success
    flow = cache.get(flow_key("add the widget", url))
    assert any(
        s.action == "webmcp_call" and s.tool == "add_to_cart" and s.args == {"sku": "WIDGET"}
        for s in flow.steps
    )

    replay = await run_cached(url, "add the widget", None, cache, mode="replay", headless=True)
    assert replay.success and replay.llm_calls == 0
    assert "Added WIDGET to cart" in replay.final_text


async def test_agent_requests_vision(tmp_path: Path) -> None:
    url = (_FIX / "vision_mixed.html").resolve().as_uri()  # has a DOM element -> no auto-vision
    fast = MockClient(actions=[
        {"action": "need_vision", "intent": "the target is on the canvas"},
        {"action": "done", "intent": "done"},
    ])
    grounding = MockGrounding([
        {"action": "click_xy", "intent": "click the green box", "coords": [100, 100]},
        {"action": "done", "intent": "hit"},
    ])
    prov = LLMAgentProvider(Router(fast=Tier(fast, "h")), tier="fast")
    cache = FlowCache(root=tmp_path)

    learn = await run_cached(
        url, "click the green box", prov, cache, mode="learn", headless=True, grounding=grounding
    )
    assert learn.success
    assert "target hit" in learn.final_text.lower()  # need_vision routed to the vision tier
