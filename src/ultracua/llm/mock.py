"""Key-less mock LLM client — returns scripted tool_use calls for tests."""

from __future__ import annotations

from typing import Optional

from .types import LLMRequest, LLMResponse, ToolUseBlock, Usage


class MockClient:
    def __init__(self, actions: Optional[list[dict]] = None, tool_name: str = "act") -> None:
        self.actions = list(actions or [])
        self.tool_name = tool_name
        self.calls = 0
        self.last_request: Optional[LLMRequest] = None

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.last_request = req
        self.calls += 1
        name = req.force_tool or self.tool_name
        inp = self.actions.pop(0) if self.actions else {"action": "done", "intent": "mock done"}
        return LLMResponse(
            blocks=[ToolUseBlock(id=f"mock-{self.calls}", name=name, input=inp)],
            model=req.model or "mock",
            stop_reason="tool_use",
            usage=Usage(input_tokens=10, output_tokens=10),
            ttft_ms=1.0,
        )
