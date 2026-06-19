"""Multi-provider abstraction: native request/response translation (no API calls) and
fast->strong escalation routing."""

from __future__ import annotations

from types import SimpleNamespace as NS

from ultracua.llm import anthropic, gemini, openai
from ultracua.llm.base import Router, Tier
from ultracua.llm.mock import MockClient
from ultracua.llm.types import (
    LLMRequest,
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
)
from ultracua.providers.llm_agent import LLMAgentProvider
from ultracua.types import Observation


def _req() -> LLMRequest:
    return LLMRequest(
        model="m",
        system="S",
        tools=[ToolDef("act", "d", {"type": "object"}, strict=True)],
        force_tool="act",
        messages=[Message("user", [TextBlock("hello")])],
        cache=True,
    )


# ---- Anthropic ----

def test_anthropic_to_native() -> None:
    b = anthropic.to_native(_req())
    assert b["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert b["tools"][0]["input_schema"] == {"type": "object"}
    assert b["tools"][0]["strict"] is True
    assert b["tool_choice"] == {"type": "tool", "name": "act"}
    assert b["messages"][0]["content"][0] == {"type": "text", "text": "hello"}


def test_anthropic_from_native() -> None:
    msg = NS(
        content=[
            NS(type="text", text="hi"),
            NS(type="tool_use", id="t1", name="act", input={"action": "click", "intent": "x"}),
        ],
        usage=NS(input_tokens=5, output_tokens=3, cache_read_input_tokens=2, cache_creation_input_tokens=0),
        model="claude-x",
        stop_reason="tool_use",
    )
    resp = anthropic.from_native(msg)
    assert resp.tool_use("act").input["action"] == "click"
    assert resp.usage.cache_read_tokens == 2
    assert resp.text() == "hi"


# ---- OpenAI ----

def test_openai_to_native_shapes() -> None:
    b = openai.to_native(_req())
    assert b["tools"][0]["function"]["parameters"] == {"type": "object"}
    assert b["tool_choice"] == {"type": "function", "function": {"name": "act"}}
    assert b["messages"][0] == {"role": "system", "content": "S"}
    assert b["messages"][1] == {"role": "user", "content": "hello"}
    # Newer OpenAI models reject `max_tokens`; we must send `max_completion_tokens`.
    assert "max_completion_tokens" in b and "max_tokens" not in b


def test_openai_tool_roundtrip_messages() -> None:
    req = LLMRequest(
        messages=[
            Message("assistant", [ToolUseBlock(id="c1", name="act", input={"a": 1})]),
            Message("user", [ToolResultBlock(tool_use_id="c1", content="ok")]),
        ]
    )
    b = openai.to_native(req)
    assert b["messages"][0]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'
    assert b["messages"][1] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}


def test_openai_from_native_parses_stringified_args() -> None:
    raw = {
        "model": "gpt-x",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "act", "arguments": '{"action": "click", "intent": "x"}'}}
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "prompt_tokens_details": {"cached_tokens": 6}},
    }
    resp = openai.from_native(raw)
    assert resp.tool_use("act").input["action"] == "click"  # JSON string -> dict
    assert resp.usage.cache_read_tokens == 6


# ---- Gemini ----

def test_gemini_to_native_shapes() -> None:
    b = gemini.to_native(_req())
    assert b["tools"][0]["function_declarations"][0]["parameters"] == {"type": "object"}
    assert b["tool_config"]["function_calling_config"]["mode"] == "ANY"
    assert b["system_instruction"] == {"parts": [{"text": "S"}]}
    assert b["contents"][0] == {"role": "user", "parts": [{"text": "hello"}]}


def test_gemini_from_native() -> None:
    raw = {
        "modelVersion": "gemini-x",
        "candidates": [
            {"finishReason": "STOP",
             "content": {"parts": [{"functionCall": {"name": "act", "args": {"action": "click", "intent": "x"}}}]}}
        ],
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2},
    }
    resp = gemini.from_native(raw)
    assert resp.tool_use("act").input["action"] == "click"
    assert resp.usage.input_tokens == 7


# ---- Routing / escalation ----

def _obs() -> Observation:
    return Observation(url="u", title="t", elements=[], fingerprint="f")


async def test_agent_uses_fast_when_confident() -> None:
    fast = MockClient(actions=[{"action": "click", "intent": "ok", "ref": "e0"}])
    strong = MockClient(actions=[{"action": "click", "intent": "strong"}])
    prov = LLMAgentProvider(Router(fast=Tier(fast, "h"), strong=Tier(strong, "o")), tier="fast")
    action, _ttft = await prov.decide("g", _obs(), [])
    assert action.action == "click" and action.intent == "ok"
    assert fast.calls == 1 and strong.calls == 0


async def test_agent_escalates_to_strong_on_giveup() -> None:
    fast = MockClient(actions=[{"action": "give_up", "intent": "unsure"}])
    strong = MockClient(actions=[{"action": "click", "intent": "do it", "ref": "e1"}])
    prov = LLMAgentProvider(Router(fast=Tier(fast, "h"), strong=Tier(strong, "o")), tier="fast")
    action, _ttft = await prov.decide("g", _obs(), [])
    assert action.action == "click" and action.intent == "do it"
    assert fast.calls == 1 and strong.calls == 1
