"""MiniWoB++ integration: learn a simple click task with the key-less oracle, then replay
it from cache with no LLM and confirm both runs earn the task reward.

Requires the `bench` dependency group (miniwob). Run: `uv run --group bench pytest`.
Skipped automatically when miniwob isn't installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("miniwob")

from benchmarks.miniwob_env import (  # noqa: E402
    MiniwobOracleProvider,
    StaticServer,
    make_finalize,
    make_prepare,
    miniwob_html_root,
    read_instruction,
    task_url,
)
from ultracua.cache import FlowCache  # noqa: E402
from ultracua.flow import run_cached  # noqa: E402


def _raw(report) -> float:
    return float((report.extra.get("finalize") or {}).get("raw") or 0.0)


async def test_miniwob_click_button_learn_then_replay(tmp_path: Path) -> None:
    server = StaticServer(miniwob_html_root())
    base = server.start()
    try:
        url = task_url(base, "click-button")
        prep, fin = make_prepare(42), make_finalize()

        instr = await read_instruction(url, prep)
        assert instr  # deterministic instruction was read

        cache = FlowCache(root=tmp_path)
        learn = await run_cached(
            url, instr, MiniwobOracleProvider(), cache, mode="learn",
            prepare=prep, finalize=fin, headless=True,
        )
        assert _raw(learn) > 0  # the oracle solved the task

        replay = await run_cached(
            url, instr, None, cache, mode="replay", prepare=prep, finalize=fin, headless=True
        )
        assert replay.llm_calls == 0      # replayed with no LLM
        assert _raw(replay) > 0           # ...and still earned the reward
    finally:
        server.stop()
