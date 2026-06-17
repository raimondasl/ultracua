"""LLM providers. Phase 0 ships an Anthropic adapter and a key-less Mock provider.

The provider boundary is where PLAN.md's multi-provider abstraction (constraint b) will
grow: a content-block canonical message type and in-process native adapters. For Phase 0
the surface is just `Provider.decide(...) -> (Action, ttft_ms)`.
"""

from __future__ import annotations

from .base import ACTION_TOOL, Provider

__all__ = ["Provider", "ACTION_TOOL", "get_provider"]


def get_provider(name: str) -> Provider:
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if name == "mock":
        from .mock import MockProvider

        return MockProvider()
    raise ValueError(f"unknown provider: {name!r} (expected 'anthropic' or 'mock')")
