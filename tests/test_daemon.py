"""The JSON-RPC daemon exposes the core cross-process: learn a flow in-process, then
REPLAY it through the daemon (separate process) with 0 LLM calls."""

from __future__ import annotations

from pathlib import Path

from ultracua.cache import FlowCache
from ultracua.daemon.client import DaemonClient
from ultracua.flow import run_cached
from ultracua.providers.scripted import ScriptedProvider

from benchmarks.shop_flow import GOAL, STEPS, SUCCESS_TEXT, index_url


async def test_daemon_health_and_cross_process_replay(tmp_path: Path) -> None:
    url = index_url()

    # Learn the flow in-process (scripted, deterministic) to populate a cache dir.
    learn = await run_cached(
        url, GOAL, ScriptedProvider(list(STEPS)), FlowCache(root=tmp_path),
        mode="learn", headless=True,
    )
    assert learn.success

    # Now drive a REPLAY through the daemon (a separate process) over JSON-RPC.
    async with DaemonClient() as client:
        health = await client.call("health")
        assert health["status"] == "ok"
        assert health["version"]

        res = await client.call("run", {
            "url": url, "goal": GOAL, "mode": "replay",
            "cache_root": str(tmp_path), "headless": True,
        })
        assert res["success"] is True
        assert res["llm_calls"] == 0
        assert SUCCESS_TEXT.lower() in res["final_text"].lower()
