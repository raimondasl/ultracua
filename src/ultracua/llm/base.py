"""LLM client protocol + fast/strong tier router."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from .types import LLMRequest, LLMResponse


class LLMClient(Protocol):
    async def complete(self, req: LLMRequest) -> LLMResponse: ...


@dataclass
class Tier:
    client: LLMClient
    model: str


@dataclass
class Router:
    """Routes a request to a fast or strong tier.

    Tier is chosen per request but, per PLAN.md, callers should keep it stable across a
    session so the prompt cache prefix stays valid — escalation to `strong` is the
    exception path, not the norm.
    """

    fast: Tier
    strong: Optional[Tier] = None

    @property
    def has_strong(self) -> bool:
        return self.strong is not None

    async def complete(self, req: LLMRequest, tier: str = "fast") -> LLMResponse:
        t = self.strong if (tier == "strong" and self.strong is not None) else self.fast
        return await t.client.complete(req.with_model(t.model))
