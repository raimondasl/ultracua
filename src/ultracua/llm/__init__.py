"""Multi-provider LLM abstraction (PLAN.md §5 / Phase 3).

A provider-neutral, content-block canonical request/response, with thin in-process native
adapters (Anthropic / OpenAI / Gemini) — NOT an OpenAI-compat shim and NOT a network proxy
(both drop prompt caching / strict tool args). The canonical message is content-block based
(text | thinking | tool_use | tool_result) because that is the superset you can losslessly
down-convert to OpenAI's flatter shape, not the reverse.
"""

from __future__ import annotations

from .base import LLMClient, Router, Tier
from .types import (
    LLMRequest,
    LLMResponse,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

__all__ = [
    "LLMClient",
    "Router",
    "Tier",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "TextBlock",
    "ThinkingBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ToolDef",
    "Usage",
    "build_client",
]


def build_client(backend: str):
    """Construct a native LLM client by backend name (lazy — SDK imported on use)."""
    if backend == "anthropic":
        from .anthropic import AnthropicClient

        return AnthropicClient()
    if backend == "openai":
        from .openai import OpenAIClient

        return OpenAIClient()
    if backend == "gemini":
        from .gemini import GeminiClient

        return GeminiClient()
    if backend == "mock":
        from .mock import MockClient

        return MockClient()
    raise ValueError(f"unknown LLM backend: {backend!r}")
