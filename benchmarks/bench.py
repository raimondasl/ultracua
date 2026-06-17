"""Learn-vs-replay benchmark on the deterministic demo-shop fixtures.

    uv run python -m benchmarks.bench                 # key-less (scripted teacher)
    uv run python -m benchmarks.bench --provider anthropic   # real LLM learn run

Run 1 LEARNS the flow (with a provider) and caches it; run 2 REPLAYS from cache with no
LLM. Prints the per-step latency breakdown for both and the replay speedup. With the
scripted teacher the learn run has ~0 LLM latency, so the ratio reflects only actuation
overhead — use --provider anthropic (ANTHROPIC_API_KEY set) for the real speedup.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import FlowReport, run_cached
from ultracua.providers.scripted import ScriptedProvider

from benchmarks.shop_flow import GOAL, STEPS, SUCCESS_TEXT, index_url


def _teacher(name: str):
    if name == "scripted":
        return ScriptedProvider(list(STEPS))
    from ultracua.providers import get_provider

    return get_provider(name)


def _dump(label: str, r: FlowReport) -> None:
    print(
        f"== {label} ==  mode={r.mode} success={r.success} "
        f"llm_calls={r.llm_calls} healed={r.healed_steps}"
    )
    for t in r.traces:
        print("   " + t.render())
    print(f"   total={r.total_ms:.0f}ms  avg_step={r.avg_step_ms:.0f}ms\n")


async def main(provider_name: str) -> int:
    url = index_url()
    cache = FlowCache(root=Path(".ultracua/bench"))
    cache.delete(flow_key(GOAL, url))  # clean slate -> force a learn run

    print(f"benchmark: provider={provider_name}  goal={GOAL!r}")
    print(f"url={url}\n")

    learn = await run_cached(url, GOAL, _teacher(provider_name), cache, mode="learn")
    _dump("LEARN (run 1)", learn)

    replay = await run_cached(url, GOAL, None, cache, mode="replay")
    _dump("REPLAY (run 2, no LLM)", replay)

    correct = (
        replay.success
        and replay.llm_calls == 0
        and SUCCESS_TEXT.lower() in replay.final_text.lower()
    )
    if learn.avg_step_ms and replay.avg_step_ms:
        print(f"speedup (avg step): {learn.avg_step_ms / replay.avg_step_ms:6.1f}x")
        print(f"speedup (total):    {learn.total_ms / max(replay.total_ms, 1e-9):6.1f}x")
    print(f"replay correct:     {correct}  (reached '{SUCCESS_TEXT}', success, 0 LLM calls)")
    if provider_name == "scripted":
        print(
            "\nnote: the scripted teacher has ~0 LLM latency, so this ratio reflects only "
            "actuation overhead.\n      Run with --provider anthropic for the real "
            "learn-vs-replay speedup."
        )
    return 0 if correct else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="benchmarks.bench")
    ap.add_argument(
        "--provider", default="scripted", choices=["scripted", "anthropic", "mock"]
    )
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.provider)))
