"""Agent providers.

`get_provider`:
  - "anthropic" | "openai" | "gemini" -> an LLMAgentProvider over a fast/strong Router on
    that native backend (multi-provider abstraction, PLAN.md §5);
  - "mock" -> the key-less heuristic provider.

The scripted/oracle teachers (benchmarks) implement the same `decide` interface directly.
"""

from __future__ import annotations

from .base import ACTION_TOOL, Provider

__all__ = ["Provider", "ACTION_TOOL", "get_provider", "build_router", "PROVIDERS"]

_LLM_BACKENDS = ("anthropic", "openai", "gemini")

# THE CLOSED SET OF PROVIDER NAMES, derived from the branches `get_provider` actually has rather
# than typed beside them. A door that wants to refuse an unknown provider BEFORE building anything
# (the daemon does) must ask this rather than keep its own copy — two lists of the permitted names
# is how one of them silently stops permitting something the other does.
PROVIDERS = frozenset(_LLM_BACKENDS) | {"mock"}


def build_router(backend: str):
    from ..config import settings
    from ..llm import build_client
    from ..llm.base import Router, Tier

    client = build_client(backend)
    return Router(
        fast=Tier(client, settings.fast_model),
        strong=Tier(client, settings.model),
    )


def get_provider(name: str) -> Provider:
    from ..config import settings

    if name == "mock":
        from .mock import MockProvider

        return MockProvider()
    if name in _LLM_BACKENDS:
        from .llm_agent import LLMAgentProvider

        return LLMAgentProvider(build_router(name), tier=settings.tier)
    raise ValueError(
        f"unknown provider: {name!r} (expected anthropic/openai/gemini or mock)"
    )
