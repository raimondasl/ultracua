"""OpenAI (Chat Completions) native adapter.

Concentrated differences vs Anthropic (normalized here):
- tools nest params under `function.parameters` (not `input_schema`);
- a forced tool is `tool_choice: {type:"function", function:{name}}`;
- tool calls surface on the assistant message as `tool_calls[]` with arguments as a JSON
  STRING (we json.loads them back to a dict at the boundary);
- tool results are their own `{role:"tool", tool_call_id, content}` messages.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .types import LLMRequest, LLMResponse, TextBlock, ToolUseBlock, Usage


def _messages_to_native(req: LLMRequest) -> list[dict]:
    out: list[dict] = []
    if req.system:
        out.append({"role": "system", "content": req.system})
    for m in req.messages:
        if m.role == "assistant":
            text = "".join(b.text for b in m.content if b.type == "text")
            tool_calls = [
                {
                    "id": b.id,
                    "type": "function",
                    "function": {"name": b.name, "arguments": json.dumps(b.input)},
                }
                for b in m.content
                if b.type == "tool_use"
            ]
            msg: dict = {"role": "assistant", "content": text or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        else:  # user
            results = [b for b in m.content if b.type == "tool_result"]
            if results:
                for b in results:  # each tool result is its own role:"tool" message
                    out.append(
                        {"role": "tool", "tool_call_id": b.tool_use_id, "content": b.content}
                    )
            text = "".join(b.text for b in m.content if b.type == "text")
            if text:
                out.append({"role": "user", "content": text})
    return out


def to_native(req: LLMRequest) -> dict:
    tools = []
    for t in req.tools:
        fn: dict = {"name": t.name, "description": t.description, "parameters": t.input_schema}
        if t.strict:
            fn["strict"] = True
        tools.append({"type": "function", "function": fn})

    body: dict = {
        "model": req.model,
        # Newer OpenAI models (o-series, GPT-4.1+/5, …) reject `max_tokens` and require
        # `max_completion_tokens`; it's accepted by all current chat models, so send it.
        "max_completion_tokens": req.max_tokens,
        "messages": _messages_to_native(req),
    }
    if req.temperature is not None:
        body["temperature"] = req.temperature
    if tools:
        body["tools"] = tools
    if req.force_tool:
        body["tool_choice"] = {"type": "function", "function": {"name": req.force_tool}}
    return body


def from_native(raw: Any) -> LLMResponse:
    """Parse an OpenAI ChatCompletion (dict or SDK object) into canonical form."""
    data = raw if isinstance(raw, dict) else raw.model_dump()
    choice = data["choices"][0]
    msg = choice["message"]
    blocks: list = []
    if msg.get("content"):
        blocks.append(TextBlock(text=msg["content"]))
    for tc in msg.get("tool_calls") or []:
        fn = tc["function"]
        args = fn.get("arguments") or "{}"
        blocks.append(
            ToolUseBlock(id=tc.get("id", ""), name=fn["name"], input=json.loads(args))
        )
    u = data.get("usage") or {}
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    usage = Usage(
        input_tokens=u.get("prompt_tokens", 0) or 0,
        output_tokens=u.get("completion_tokens", 0) or 0,
        cache_read_tokens=cached,
    )
    return LLMResponse(
        blocks=blocks,
        model=data.get("model", ""),
        stop_reason=choice.get("finish_reason", "") or "",
        usage=usage,
    )


class OpenAIClient:
    def __init__(self) -> None:
        self._client = None

    def _sdk(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI()  # reads OPENAI_API_KEY
        return self._client

    async def complete(self, req: LLMRequest) -> LLMResponse:
        body = to_native(req)
        t0 = time.perf_counter()
        resp = await self._sdk().chat.completions.create(**body)
        out = from_native(resp)
        out.ttft_ms = (time.perf_counter() - t0) * 1000.0
        return out
