"""Cassette test for the live Anthropic adapter path.

The adapters' translation (to_native / from_native) is unit-tested elsewhere; what was NOT covered
is the real `.complete()` glue — building the request, the streaming call, `get_final_message()`, and
wiring usage/ttft back. This replays a RECORDED streaming response through the real Anthropic SDK +
adapter (no network, no key), so an SDK-API change that breaks `complete()` fails CI.

Re-record (occasionally, when the SDK/API changes) with a real key in .env:

    uv run python tests/test_llm_cassette.py --record

The cassette (tests/cassettes/anthropic_act.json) stores only the API *response* (no request, no key).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from ultracua.llm.anthropic import AnthropicClient
from ultracua.llm.types import LLMRequest, Message, TextBlock, ToolDef

_CASSETTE = Path(__file__).parent / "cassettes" / "anthropic_act.json"

# A realistic agent-style call: force the `act` tool (as the agent loop does) on Haiku (cheap).
_TOOL = ToolDef(
    name="act", description="Emit exactly one browser action.",
    input_schema={
        "type": "object",
        "properties": {"action": {"type": "string"}, "intent": {"type": "string"}},
        "required": ["action", "intent"],
    },
    strict=False,
)
_REQ = LLMRequest(
    model="claude-haiku-4-5",
    system="You drive a web browser. Emit exactly one action via the `act` tool.",
    tools=[_TOOL], force_tool="act", max_tokens=200,
    messages=[Message("user", [TextBlock("The page shows a 'Log in' button. Click it.")])],
)


def _replay_sdk(cassette: dict):
    """An AsyncAnthropic whose transport replays the recorded response (auth is never checked)."""
    from anthropic import AsyncAnthropic

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            cassette["status"],
            headers={"content-type": cassette["content_type"]},
            content=base64.b64decode(cassette["body_b64"]),
        )

    return AsyncAnthropic(
        api_key="sk-ant-cassette", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_anthropic_complete_replays_recorded_tool_call() -> None:
    if not _CASSETTE.exists():
        pytest.skip("no cassette recorded (run: python tests/test_llm_cassette.py --record)")
    client = AnthropicClient()
    client._client = _replay_sdk(json.loads(_CASSETTE.read_text(encoding="utf-8")))

    resp = await client.complete(_REQ)  # exercises the REAL SDK stream + adapter parse

    tu = resp.tool_use("act")
    assert tu is not None and "intent" in tu.input        # forced tool came back + parsed to a dict
    assert resp.stop_reason == "tool_use"
    assert resp.usage.input_tokens > 0 and resp.usage.output_tokens > 0  # usage parsed off the message
    assert resp.ttft_ms is not None                        # the streaming TTFT path ran


# --- recording (run manually with a real key) -------------------------------------------------
class _CaptureTransport(httpx.AsyncBaseTransport):
    """Passes the request through and captures the raw response body for the cassette."""

    def __init__(self, sink: dict) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self._sink = sink

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resp = await self._inner.handle_async_request(request)
        body = await resp.aread()
        self._sink.update(
            status=resp.status_code,
            content_type=resp.headers.get("content-type", ""),
            body_b64=base64.b64encode(body).decode(),
        )
        return httpx.Response(resp.status_code, headers={"content-type": self._sink["content_type"]},
                              content=body, request=request)

    async def aclose(self) -> None:
        await self._inner.aclose()


async def _record() -> None:
    from anthropic import AsyncAnthropic  # noqa: F401

    sink: dict = {}
    client = AnthropicClient()
    client._client = __import__("anthropic").AsyncAnthropic(
        http_client=httpx.AsyncClient(transport=_CaptureTransport(sink))  # real key from env/.env
    )
    resp = await client.complete(_REQ)
    assert resp.tool_use("act") is not None, "recording did not yield a tool call"
    _CASSETTE.parent.mkdir(parents=True, exist_ok=True)
    _CASSETTE.write_text(json.dumps(sink, indent=2), encoding="utf-8")
    print(f"recorded {len(sink['body_b64'])} b64 chars -> {_CASSETTE}")


if __name__ == "__main__":
    import asyncio
    import sys

    if "--record" in sys.argv:
        asyncio.run(_record())
    else:
        print("usage: python tests/test_llm_cassette.py --record   (needs ANTHROPIC_API_KEY)")
