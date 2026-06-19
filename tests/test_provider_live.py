"""Live-path tests for the OpenAI and Gemini adapters' `.complete()` glue.

The translation (`to_native` / `from_native`) is unit-tested in `test_llm.py` against hand-built
dicts. What that does NOT cover is the real `.complete()` path: building the request, calling the
actual SDK, and parsing the SDK's *response object* back through `from_native`. That gap hid two real
bugs (OpenAI `max_tokens` rejection; Gemini `from_native` reading the wrong key casing off the SDK
object). These tests exercise that path with no API key and no network:

- OpenAI: replay a canned ChatCompletion through the REAL `AsyncOpenAI` SDK via an httpx MockTransport
  (the SDK builds the request and parses the response; our adapter consumes it).
- Gemini: the google SDK doesn't take an injectable transport as cleanly, so we hand `complete()` a
  fake client whose `generate_content` returns a REAL `types.GenerateContentResponse` object — which is
  exactly what exposed the snake_case/camelCase parsing bug.

Both `importorskip` their SDK, so a contributor running `uv run pytest` without the optional
`providers` group skips them; CI installs `--group providers` so they actually run.
"""

from __future__ import annotations

import httpx
import pytest

from ultracua.llm.types import LLMRequest, Message, TextBlock, ToolDef

_TOOL = ToolDef(
    name="act", description="Emit exactly one browser action.",
    input_schema={
        "type": "object",
        "properties": {"action": {"type": "string"}, "intent": {"type": "string"}},
        "required": ["action", "intent"],
    },
    strict=False,
)


def _req(model: str) -> LLMRequest:
    return LLMRequest(
        model=model, system="You drive a web browser. Emit one action via the `act` tool.",
        tools=[_TOOL], force_tool="act", max_tokens=200,
        messages=[Message("user", [TextBlock("The page shows a 'Log in' button. Click it.")])],
    )


async def test_openai_complete_replays_recorded_tool_call() -> None:
    openai = pytest.importorskip("openai")
    from ultracua.llm.openai import OpenAIClient

    body = {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "gpt-4o",
        "choices": [{
            "index": 0, "finish_reason": "tool_calls",
            "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "act",
                                 "arguments": '{"action": "click", "intent": "click the log in button"}'},
                }],
            },
        }],
        "usage": {"prompt_tokens": 42, "completion_tokens": 12, "total_tokens": 54,
                  "prompt_tokens_details": {"cached_tokens": 8}},
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = OpenAIClient()
    client._client = openai.AsyncOpenAI(
        api_key="sk-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    resp = await client.complete(_req("gpt-4o"))  # REAL SDK request-build + parse + our adapter

    tu = resp.tool_use("act")
    assert tu is not None and tu.input["action"] == "click"   # forced tool came back + JSON-args parsed
    assert resp.stop_reason == "tool_calls"
    assert resp.usage.input_tokens == 42 and resp.usage.output_tokens == 12
    assert resp.usage.cache_read_tokens == 8                  # prompt_tokens_details.cached_tokens
    assert resp.ttft_ms is not None


async def test_gemini_complete_parses_real_sdk_response() -> None:
    gt = pytest.importorskip("google.genai.types")
    from ultracua.llm.gemini import GeminiClient

    # A REAL SDK response object (model_dump() is snake_case — the case the live path must handle).
    canned = gt.GenerateContentResponse(
        candidates=[gt.Candidate(
            content=gt.Content(role="model", parts=[
                gt.Part(function_call=gt.FunctionCall(
                    name="act", args={"action": "click", "intent": "click the log in button"}))]),
            finish_reason="STOP",
        )],
        usage_metadata=gt.GenerateContentResponseUsageMetadata(
            prompt_token_count=42, candidates_token_count=12, cached_content_token_count=8),
    )

    seen: dict = {}

    class _Models:
        async def generate_content(self, **kw):
            seen.update(kw)
            return canned

    class _Aio:
        models = _Models()

    class _FakeSdk:
        aio = _Aio()

    client = GeminiClient()
    client._client = _FakeSdk()

    resp = await client.complete(_req("gemini-2.5-flash"))

    tu = resp.tool_use("act")
    assert tu is not None and tu.input["action"] == "click"   # functionCall parsed off the REAL object
    assert resp.usage.input_tokens == 42 and resp.usage.output_tokens == 12
    assert resp.usage.cache_read_tokens == 8                  # cachedContentTokenCount
    assert resp.ttft_ms is not None
    # the translated request actually reached the SDK
    assert seen.get("model") == "gemini-2.5-flash"
    assert seen["contents"][0]["role"] == "user"
