"""LLM client protocol + fast/strong tier router (with retry + usage accounting)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Protocol

from ..obs import UsageTotals, get_logger
from .types import LLMRequest, LLMResponse

_log = get_logger("llm")

# Substrings marking a likely-transient failure worth retrying (rate limit / overload / 5xx /
# connection blip). Matched against the exception type name + message, case-insensitive.
_TRANSIENT = (
    "rate limit", "ratelimit", "overloaded", "overload", "timeout", "timed out", "connection",
    "temporarily", "temporar", "unavailable", "429", "500", "502", "503", "529",
)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError)):
        return True
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(k in blob for k in _TRANSIENT)


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

    Every call is bounded by a timeout and retried on a transient failure (capped exponential
    backoff), and its token usage is accumulated into `totals` for cost reporting.
    """

    fast: Tier
    strong: Optional[Tier] = None
    totals: UsageTotals = field(default_factory=UsageTotals)

    @property
    def has_strong(self) -> bool:
        return self.strong is not None

    async def complete(self, req: LLMRequest, tier: str = "fast") -> LLMResponse:
        from ..config import settings
        from ..safety import backoff_delay

        t = self.strong if (tier == "strong" and self.strong is not None) else self.fast
        model_req = req.with_model(t.model)
        last: Optional[BaseException] = None
        for attempt in range(settings.llm_max_retries + 1):
            try:
                resp = await asyncio.wait_for(
                    t.client.complete(model_req), timeout=settings.llm_timeout_s
                )
                self.totals.add(resp.usage)
                return resp
            except Exception as exc:  # noqa: BLE001 - classify, then retry transient or re-raise
                last = exc
                if attempt >= settings.llm_max_retries or not _is_transient(exc):
                    raise
                delay = backoff_delay(attempt)
                _log.warning(
                    "LLM call failed (%s); retry %d/%d in %.1fs",
                    type(exc).__name__, attempt + 1, settings.llm_max_retries, delay,
                )
                await asyncio.sleep(delay)
        raise last  # pragma: no cover - loop always returns or raises above
