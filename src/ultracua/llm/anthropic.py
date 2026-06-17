"""Anthropic (Claude Messages API) native adapter.

- Tools use `input_schema` (+ optional `strict`); a forced tool via tool_choice.
- Prompt caching: a `cache_control` breakpoint on the stable system+tools prefix; the
  volatile observation goes in the user turn after it.
- Streams to measure TTFT (the dominant per-step latency component).
- `tool_use` blocks come back with `input` already parsed (a dict) — no JSON.parse needed.
"""

from __future__ import annotations

import time
from typing import Any

from .types import (
    LLMRequest,
    LLMResponse,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
)


def _block_to_native(b: Any) -> dict:
    if b.type == "text":
        return {"type": "text", "text": b.text}
    if b.type == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    if b.type == "tool_result":
        return {
            "type": "tool_result",
            "tool_use_id": b.tool_use_id,
            "content": b.content,
            "is_error": b.is_error,
        }
    if b.type == "thinking":
        return {"type": "thinking", "thinking": b.thinking}
    raise ValueError(f"unknown block type: {b.type}")


def to_native(req: LLMRequest) -> dict:
    system: list[dict] = []
    if req.system:
        sys_block: dict = {"type": "text", "text": req.system}
        if req.cache:
            sys_block["cache_control"] = {"type": "ephemeral"}  # caches tools + system
        system.append(sys_block)

    tools = []
    for t in req.tools:
        td: dict = {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        if t.strict:
            td["strict"] = True
        tools.append(td)

    messages = [
        {"role": m.role, "content": [_block_to_native(b) for b in m.content]}
        for m in req.messages
    ]

    body: dict = {"model": req.model, "max_tokens": req.max_tokens, "messages": messages}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = tools
    if req.force_tool:
        body["tool_choice"] = {"type": "tool", "name": req.force_tool}
    if req.thinking:
        body["thinking"] = {"type": "adaptive"}
    return body


def from_native(msg: Any) -> LLMResponse:
    blocks: list = []
    for b in msg.content:
        if b.type == "text":
            blocks.append(TextBlock(text=b.text))
        elif b.type == "tool_use":
            blocks.append(ToolUseBlock(id=b.id, name=b.name, input=dict(b.input)))
        elif b.type == "thinking":
            blocks.append(ThinkingBlock(thinking=getattr(b, "thinking", "")))
    u = msg.usage
    usage = Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )
    return LLMResponse(
        blocks=blocks,
        model=getattr(msg, "model", ""),
        stop_reason=getattr(msg, "stop_reason", "") or "",
        usage=usage,
    )


class AnthropicClient:
    def __init__(self) -> None:
        self._client = None

    def _sdk(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY
        return self._client

    async def complete(self, req: LLMRequest) -> LLMResponse:
        body = to_native(req)
        t0 = time.perf_counter()
        ttft = None
        async with self._sdk().messages.stream(**body) as stream:
            async for _event in stream:
                if ttft is None:
                    ttft = (time.perf_counter() - t0) * 1000.0
            msg = await stream.get_final_message()
        resp = from_native(msg)
        resp.ttft_ms = ttft
        return resp
