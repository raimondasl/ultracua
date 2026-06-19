"""Google Gemini (generateContent) native adapter.

Differences vs Anthropic (normalized here):
- tools are `functionDeclarations` with `parameters`;
- roles are `user` / `model`; content is `parts[]`;
- a forced tool is `tool_config.function_calling_config.mode = "ANY"` + allowed names;
- tool calls surface as a `functionCall` part (args already parsed); tool results are a
  `functionResponse` part.
"""

from __future__ import annotations

import time
from typing import Any

from .types import LLMRequest, LLMResponse, TextBlock, ToolUseBlock, Usage


def _parts_for(msg: Any) -> list[dict]:
    parts: list[dict] = []
    for b in msg.content:
        if b.type == "text":
            parts.append({"text": b.text})
        elif b.type == "tool_use":
            parts.append({"functionCall": {"name": b.name, "args": b.input}})
        elif b.type == "tool_result":
            parts.append(
                {"functionResponse": {"name": b.tool_use_id, "response": {"content": b.content}}}
            )
    return parts


def to_native(req: LLMRequest) -> dict:
    contents = [
        {"role": ("user" if m.role == "user" else "model"), "parts": _parts_for(m)}
        for m in req.messages
    ]
    body: dict = {"contents": contents}
    if req.system:
        body["system_instruction"] = {"parts": [{"text": req.system}]}
    if req.tools:
        body["tools"] = [
            {
                "function_declarations": [
                    {"name": t.name, "description": t.description, "parameters": t.input_schema}
                    for t in req.tools
                ]
            }
        ]
    if req.force_tool:
        body["tool_config"] = {
            "function_calling_config": {
                "mode": "ANY",
                "allowed_function_names": [req.force_tool],
            }
        }
    return body


def from_native(raw: Any) -> LLMResponse:
    # A live SDK response object's `model_dump()` is snake_case (`function_call`, `usage_metadata`, …),
    # but the keys read below — and our raw-dict test fixtures — are the REST API's camelCase. `by_alias`
    # normalizes the SDK object to camelCase so both the live `.complete()` path and dict inputs parse
    # identically. (Without it, a real Gemini call returns an empty response with zero usage.)
    data = raw if isinstance(raw, dict) else raw.model_dump(by_alias=True)
    cands = data.get("candidates") or [{}]
    parts = ((cands[0].get("content") or {}).get("parts")) or []
    blocks: list = []
    for p in parts:
        if "text" in p and p["text"]:
            blocks.append(TextBlock(text=p["text"]))
        elif "functionCall" in p:
            fc = p["functionCall"]
            blocks.append(
                ToolUseBlock(id=fc.get("name", ""), name=fc["name"], input=dict(fc.get("args") or {}))
            )
    um = data.get("usageMetadata") or {}
    usage = Usage(
        input_tokens=um.get("promptTokenCount", 0) or 0,
        output_tokens=um.get("candidatesTokenCount", 0) or 0,
        cache_read_tokens=um.get("cachedContentTokenCount", 0) or 0,
    )
    return LLMResponse(
        blocks=blocks,
        model=data.get("modelVersion", ""),
        stop_reason=(cands[0].get("finishReason", "") or ""),
        usage=usage,
    )


class GeminiClient:
    def __init__(self) -> None:
        self._client = None

    def _sdk(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client()  # reads GEMINI_API_KEY / GOOGLE_API_KEY
        return self._client

    async def complete(self, req: LLMRequest) -> LLMResponse:
        body = to_native(req)
        t0 = time.perf_counter()
        resp = await self._sdk().aio.models.generate_content(
            model=req.model,
            contents=body["contents"],
            config={k: v for k, v in body.items() if k != "contents"},
        )
        out = from_native(resp)
        out.ttft_ms = (time.perf_counter() - t0) * 1000.0
        return out
