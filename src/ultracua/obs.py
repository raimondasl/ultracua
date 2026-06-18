"""Observability: a library logger + LLM usage/cost accounting.

The library logs through the `ultracua` logger, which carries a NullHandler by default so
importing ultracua never spams output. The CLI/daemon call `configure_logging()` to attach a
real handler. Every log record carries the current `run_id` (a contextvar set per run_cached
call) so a scheduled job's logs can be traced end-to-end.

`UsageTotals` accumulates token usage across LLM calls (the Router feeds it) and estimates the
dollar cost — surfaced in logs and `FlowReport.extra["usage"]` so an unattended run is auditable.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from typing import Optional

# --- logging ----------------------------------------------------------------------------------
_ROOT = logging.getLogger("ultracua")
_ROOT.addHandler(logging.NullHandler())  # library default: never emit unless a handler is attached

run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("ultracua_run_id", default="-")


def new_run_id() -> str:
    """Mint + install a short run id for the current context (returns it)."""
    import uuid

    rid = uuid.uuid4().hex[:8]
    run_id_var.set(rid)
    return rid


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_var.get()
        return True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger("ultracua." + name)


def configure_logging(level: "str | int" = "INFO", *, stream=None) -> None:
    """Attach a stderr handler to the `ultracua` logger (idempotent). Safe to call repeatedly."""
    lvl = logging.getLevelName(level) if isinstance(level, str) else level
    _ROOT.setLevel(lvl)
    if any(not isinstance(h, logging.NullHandler) for h in _ROOT.handlers):
        return  # already configured
    handler = logging.StreamHandler(stream)
    handler.addFilter(_RunIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(run_id)s] %(message)s", "%H:%M:%S")
    )
    _ROOT.addHandler(handler)


# --- LLM usage + cost -------------------------------------------------------------------------
# $ per 1M tokens (input, output) for the known models; cache tokens are approximated at the
# input rate (a small over-estimate for cache reads — fine for a cost ceiling, not billing).
_PRICES = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}


def _price(model: str) -> Optional[tuple]:
    for prefix, p in _PRICES.items():
        if model and model.startswith(prefix):
            return p
    return None


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0

    def add(self, usage) -> None:
        """Accumulate one response's Usage (duck-typed; tolerant of None / missing fields)."""
        if usage is None:
            return
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_write_tokens", 0) or 0
        self.calls += 1

    def snapshot(self) -> tuple:
        return (self.input_tokens, self.output_tokens, self.cache_read_tokens,
                self.cache_write_tokens, self.calls)

    def since(self, snap: tuple) -> "UsageTotals":
        """A delta UsageTotals = self minus an earlier snapshot() (for per-run scoping)."""
        return UsageTotals(
            input_tokens=self.input_tokens - snap[0], output_tokens=self.output_tokens - snap[1],
            cache_read_tokens=self.cache_read_tokens - snap[2],
            cache_write_tokens=self.cache_write_tokens - snap[3], calls=self.calls - snap[4],
        )

    def cost_usd(self, model: str) -> Optional[float]:
        p = _price(model)
        if p is None:
            return None
        price_in, price_out = p
        billed_in = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        return (billed_in * price_in + self.output_tokens * price_out) / 1_000_000

    def as_dict(self, model: str = "") -> dict:
        d = {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }
        cost = self.cost_usd(model) if model else None
        if cost is not None:
            d["cost_usd"] = round(cost, 6)
        return d

    def summary(self, model: str = "") -> str:
        cost = self.cost_usd(model) if model else None
        tail = f", ~${cost:.4f}" if cost is not None else ""
        return (f"{self.calls} call(s), in={self.input_tokens} out={self.output_tokens} "
                f"cache_r={self.cache_read_tokens}{tail}")
