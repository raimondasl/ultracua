"""MiniWoB++ learn-vs-replay benchmark.

    uv run --group bench python -m benchmarks.miniwob_bench                  # key-less oracle
    uv run --group bench python -m benchmarks.miniwob_bench --provider anthropic --all

Per task: read the deterministic instruction, LEARN (with a provider) caching the flow,
then REPLAY from cache with no LLM. Reports MiniWoB reward (success), wall-clock, and the
replay speedup. The oracle teacher has ~0 LLM latency, so the real speedup needs
--provider anthropic.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import FlowReport, run_cached

from benchmarks.miniwob_env import (
    EASY_TASKS,
    TASKS,
    MiniwobOracleProvider,
    StaticServer,
    make_finalize,
    make_prepare,
    miniwob_html_root,
    read_instruction,
    task_url,
)


def _teacher(name: str):
    if name == "oracle":
        return MiniwobOracleProvider()
    from ultracua.providers import get_provider

    return get_provider(name)


def _raw(r: FlowReport) -> float:
    return float((r.extra.get("finalize") or {}).get("raw") or 0.0)


async def _run_task(base: str, cache: FlowCache, task: str, provider_name: str, seed: int):
    url = task_url(base, task)
    prep, fin = make_prepare(seed), make_finalize()
    instr = await read_instruction(url, prep)
    cache.delete(flow_key(instr, url))  # clean slate -> force a learn
    learn = await run_cached(
        url, instr, _teacher(provider_name), cache, mode="learn",
        prepare=prep, finalize=fin, headless=True, max_steps=12,
    )
    replay = await run_cached(
        url, instr, None, cache, mode="replay", prepare=prep, finalize=fin, headless=True
    )
    return instr, learn, replay


async def main(provider_name: str, tasks: list[str], seed: int) -> int:
    server = StaticServer(miniwob_html_root())
    base = server.start()
    cache = FlowCache(root=Path(".ultracua/miniwob"))
    rows: list[tuple] = []
    try:
        for task in tasks:
            instr, learn, replay = await _run_task(base, cache, task, provider_name, seed)
            lr, rr = _raw(learn), _raw(replay)
            rows.append((task, replay, lr, rr))
            print(f"[{task}] {instr!r}")
            print(
                f"   learn:  reward={lr:+.2f} total={learn.total_ms:5.0f}ms llm={learn.llm_calls}"
            )
            print(
                f"   replay: reward={rr:+.2f} total={replay.total_ms:5.0f}ms "
                f"llm={replay.llm_calls} healed={replay.healed_steps} "
                f"avg_step={replay.avg_step_ms:.0f}ms"
            )
            if learn.total_ms and replay.total_ms:
                print(f"   speedup(total): {learn.total_ms / replay.total_ms:.1f}x")
            print()
    finally:
        server.stop()

    n = len(rows)
    replay_ok = sum(1 for _, _, _, rr in rows if rr > 0)
    zero_llm = sum(1 for _, rep, _, _ in rows if rep.llm_calls == 0)
    print(f"== summary ({provider_name}, {n} task(s)) ==")
    print(f"replay success: {replay_ok}/{n}   replay 0-LLM: {zero_llm}/{n}")
    if provider_name == "oracle":
        print(
            "note: the oracle teacher has ~0 LLM latency; run --provider anthropic for the "
            "real learn-vs-replay speedup."
        )
    return 0 if (n > 0 and replay_ok == n) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="benchmarks.miniwob_bench")
    ap.add_argument("--provider", default="oracle", choices=["oracle", "anthropic", "mock"])
    ap.add_argument("--all", action="store_true", help="run the broader TASKS set (LLM)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    task_set = TASKS if args.all else EASY_TASKS
    raise SystemExit(asyncio.run(main(args.provider, task_set, args.seed)))
