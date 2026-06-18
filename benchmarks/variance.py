"""Variance harness (#1): run a benchmark N times and report mean +/- spread + $ cost.

Discovery (the learn run) is LLM-nondeterministic, so a single benchmark run is noisy — one run said
6/10, a saved run said 8/10. This reps it and reports the spread, turning "8/10 once" into
"7.4/10 +/- 1.1 over 10 reps", plus the total LLM cost (read from FlowReport.extra["usage"]).

MANUAL / LOCAL only — it uses a real LLM (key from .env) and is deliberately NOT wired into CI.

    uv run python -m benchmarks.variance --bench demo --reps 5
    uv run --group bench python -m benchmarks.variance --bench miniwob --reps 5 [--all]
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import tempfile
from pathlib import Path

from ultracua.cache import FlowCache
from ultracua.flow import run_cached
from ultracua.providers import get_provider

from benchmarks.shop_flow import GOAL, STEPS, SUCCESS_TEXT, index_url  # noqa: F401 (STEPS documents the flow)


def _cost(report) -> float:
    return float((report.extra.get("usage") or {}).get("cost_usd") or 0.0)


def _stat(xs):
    xs = list(xs)
    mean = statistics.mean(xs) if xs else 0.0
    std = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return mean, std, (min(xs) if xs else 0.0), (max(xs) if xs else 0.0)


async def _demo_rep(provider_name: str, root: Path) -> dict:
    url = index_url()
    cache = FlowCache(root=root)  # fresh -> each rep re-learns (the point: discovery variance)
    learn = await run_cached(url, GOAL, get_provider(provider_name), cache, mode="learn", headless=True)
    replay = await run_cached(url, GOAL, None, cache, mode="replay", headless=True)
    ok = bool(replay.success and replay.llm_calls == 0 and SUCCESS_TEXT.lower() in replay.final_text.lower())
    speedup = learn.total_ms / replay.total_ms if replay.total_ms else 0.0
    return {"ok": ok, "speedup": speedup, "cost": _cost(learn),
            "learn_ms": learn.total_ms, "replay_ms": replay.total_ms}


async def run_demo(provider_name: str, reps: int) -> None:
    print(f"variance: bench=demo-shop provider={provider_name} reps={reps}\n")
    results = []
    with tempfile.TemporaryDirectory() as td:
        for i in range(reps):
            r = await _demo_rep(provider_name, Path(td) / f"rep{i}")
            results.append(r)
            print(f"  rep {i + 1}/{reps}: replay_ok={r['ok']} speedup={r['speedup']:.1f}x "
                  f"learn={r['learn_ms']:.0f}ms replay={r['replay_ms']:.0f}ms ${r['cost']:.4f}")
    ok = sum(1 for r in results if r["ok"])
    sm, ss, smin, smax = _stat(r["speedup"] for r in results if r["ok"])
    print(f"\n== demo-shop, {reps} reps ==")
    print(f"replay success:  {ok}/{reps}")
    print(f"speedup:         mean {sm:.1f}x +/- {ss:.1f}  (min {smin:.1f}x, max {smax:.1f}x)")
    print(f"total LLM cost:  ~${sum(r['cost'] for r in results):.4f}")


async def run_miniwob(provider_name: str, reps: int, all_tasks: bool, seed: int) -> None:
    from benchmarks.miniwob_bench import _raw, _run_task
    from benchmarks.miniwob_env import EASY_TASKS, TASKS, StaticServer, miniwob_html_root

    tasks = TASKS if all_tasks else EASY_TASKS
    print(f"variance: bench=miniwob provider={provider_name} reps={reps} tasks={len(tasks)}\n")
    fracs, costs = [], []
    server = StaticServer(miniwob_html_root())
    base = server.start()
    try:
        with tempfile.TemporaryDirectory() as td:
            for i in range(reps):
                cache = FlowCache(root=Path(td) / f"rep{i}")
                ok, cost = 0, 0.0
                for task in tasks:
                    _instr, learn, replay = await _run_task(base, cache, task, provider_name, seed)
                    if _raw(replay) > 0:
                        ok += 1
                    cost += _cost(learn)
                fracs.append(ok / len(tasks))
                costs.append(cost)
                print(f"  rep {i + 1}/{reps}: replay success {ok}/{len(tasks)} "
                      f"({ok / len(tasks) * 100:.0f}%)  ${cost:.4f}")
    finally:
        server.stop()
    fm, fs, fmin, fmax = _stat(fracs)
    print(f"\n== miniwob ({len(tasks)} tasks), {reps} reps ==")
    print(f"replay success rate: mean {fm * 100:.0f}% +/- {fs * 100:.0f}%  "
          f"(min {fmin * 100:.0f}%, max {fmax * 100:.0f}%)")
    print(f"total LLM cost:      ~${sum(costs):.4f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="benchmarks.variance")
    ap.add_argument("--bench", choices=["demo", "miniwob"], default="demo")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--all", action="store_true", help="(miniwob) run the broader task set")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    if args.bench == "demo":
        asyncio.run(run_demo(args.provider, args.reps))
    else:
        asyncio.run(run_miniwob(args.provider, args.reps, args.all, args.seed))
