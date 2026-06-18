"""Observability + LLM-call resilience + versioning + atomic storage.

Key-less: a flaky in-memory LLM client exercises the Router's retry/usage paths; no network.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import ultracua
from ultracua.cache import FlowCache, flow_key
from ultracua.flows import _load_meta, _meta_path, _record_run
from ultracua.llm.base import Router, Tier, _is_transient
from ultracua.llm.types import LLMRequest, LLMResponse, ToolUseBlock, Usage
from ultracua.obs import UsageTotals, configure_logging, get_logger, new_run_id


# --- versioning ------------------------------------------------------------------------------
def test_version_is_single_sourced_from_pyproject() -> None:
    data = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert ultracua.__version__ == data["project"]["version"]  # no more 0.1.0 vs 0.15.0 drift


# --- usage / cost ----------------------------------------------------------------------------
def test_usage_totals_accumulate_and_cost() -> None:
    t = UsageTotals()
    t.add(Usage(input_tokens=1000, output_tokens=500))
    t.add(Usage(input_tokens=2000, output_tokens=1000, cache_read_tokens=500))
    assert t.calls == 2 and t.input_tokens == 3000 and t.output_tokens == 1500
    cost = t.cost_usd("claude-opus-4-8")  # $5/Mtok in (+cache), $25/Mtok out
    assert abs(cost - ((3000 + 500) * 5 + 1500 * 25) / 1_000_000) < 1e-9
    assert t.cost_usd("some-unpriced-model") is None  # unknown price -> None, never a crash
    assert "cost_usd" in t.as_dict("claude-opus-4-8") and "cost_usd" not in t.as_dict("unknown")


def test_usage_totals_add_tolerates_none() -> None:
    t = UsageTotals()
    t.add(None)
    assert t.calls == 0


def test_usage_since_delta() -> None:
    t = UsageTotals()
    t.add(Usage(input_tokens=10, output_tokens=5))
    snap = t.snapshot()
    t.add(Usage(input_tokens=40, output_tokens=20))
    d = t.since(snap)
    assert d.input_tokens == 40 and d.output_tokens == 20 and d.calls == 1


# --- Router resilience -----------------------------------------------------------------------
class _Flaky:
    """Raises `exc` for the first `fail_times` calls, then returns a tool_use response."""

    def __init__(self, fail_times: int, exc: Exception) -> None:
        self.fail_times, self.exc, self.calls = fail_times, exc, 0

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return LLMResponse(
            blocks=[ToolUseBlock(id="x", name="act", input={"ok": 1})],
            model=req.model or "m", stop_reason="tool_use",
            usage=Usage(input_tokens=3, output_tokens=2),
        )


async def test_router_retries_transient_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("ultracua.safety.backoff_delay", lambda *a, **k: 0.0)  # no real sleeping
    client = _Flaky(2, TimeoutError("request timed out"))  # 2 transient failures then success
    r = Router(fast=Tier(client, "m"))
    resp = await r.complete(LLMRequest())
    assert resp.tool_use("act") is not None and client.calls == 3
    assert r.totals.calls == 1 and r.totals.input_tokens == 3  # only the success is accounted


async def test_router_does_not_retry_nontransient() -> None:
    class _Boom:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, req: LLMRequest) -> LLMResponse:
            self.calls += 1
            raise ValueError("invalid request schema")  # not transient -> no retry

    c = _Boom()
    with pytest.raises(ValueError):
        await Router(fast=Tier(c, "m")).complete(LLMRequest())
    assert c.calls == 1


async def test_router_gives_up_after_max_retries(monkeypatch) -> None:
    monkeypatch.setattr("ultracua.safety.backoff_delay", lambda *a, **k: 0.0)
    client = _Flaky(99, TimeoutError("always times out"))  # never recovers
    with pytest.raises(TimeoutError):
        await Router(fast=Tier(client, "m")).complete(LLMRequest())
    assert client.calls == 1 + ultracua.settings.llm_max_retries  # initial try + N retries


def test_is_transient_classifies() -> None:
    assert _is_transient(TimeoutError("x"))
    assert _is_transient(Exception("429 Too Many Requests"))
    assert _is_transient(Exception("overloaded_error: please retry"))
    assert _is_transient(ConnectionError("reset"))
    assert not _is_transient(ValueError("invalid tool schema"))


# --- logging ---------------------------------------------------------------------------------
def test_configure_logging_emits_with_run_id(caplog) -> None:
    configure_logging("INFO")
    new_run_id()
    with caplog.at_level("INFO", logger="ultracua"):
        get_logger("test").info("hello run")
    assert any("hello run" in r.message for r in caplog.records)


# --- atomic storage --------------------------------------------------------------------------
def test_record_run_accumulates_and_save_meta_is_atomic(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path / "cache")
    key = flow_key("g", "http://x/", "flow:c")
    for _ in range(5):
        _record_run(cache, key, ok=False, error="boom")
    _record_run(cache, key, ok=True)
    m = _load_meta(cache, key)
    assert m.runs == 6 and m.successes == 1 and m.consecutive_failures == 0  # last run reset it
    # the atomic temp file must not linger next to the meta json
    leftovers = list((tmp_path / "cache").glob("*.tmp"))
    assert leftovers == [] and _meta_path(cache, key).exists()
