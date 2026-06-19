"""Variance harness + standing regression gate: run a benchmark N times, report mean +/- spread +
$ cost, and (optionally) gate the result against a saved baseline.

Discovery (the learn run) is LLM-nondeterministic, so a single benchmark run is noisy — one run said
6/10, a saved run said 8/10. This reps it and reports the spread, turning "8/10 once" into
"7.4/10 +/- 1.1 over 10 reps", plus the total LLM cost (read from FlowReport.extra["usage"]).

To make it *standing*: record a baseline once, then gate later runs against it. A drop within the
baseline's own error bars is treated as noise, NOT a regression — that's the whole point of the spread.

    # record a baseline (commit baselines/<name>.json if you want to gate on it later)
    uv run python -m benchmarks.variance --bench demo --reps 5 --json baselines/demo.json

    # later: re-run and FAIL (exit 1) if replay-success or cost regressed beyond the error bars
    uv run python -m benchmarks.variance --bench demo --reps 5 --baseline baselines/demo.json

    uv run --group bench python -m benchmarks.variance --bench miniwob --reps 5 --all --json base.json

MANUAL / LOCAL only — it uses a real LLM (key from .env) and is deliberately NOT wired into CI. (The
pure record/compare logic below is unit-tested key-lessly in tests/test_variance.py.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ultracua.cache import FlowCache
from ultracua.flow import run_cached
from ultracua.providers import get_provider

from benchmarks.shop_flow import GOAL, STEPS, SUCCESS_TEXT, index_url  # noqa: F401 (STEPS documents the flow)

# Metrics where higher is better and that are machine-INDEPENDENT enough to gate on. `speedup` is an
# in-process micro-timing (machine-dependent), so it's recorded and reported but never gates.
_GATED_RATES = ("replay_success_rate",)


def _cost(report) -> float:
    return float((report.extra.get("usage") or {}).get("cost_usd") or 0.0)


# --- pure, testable aggregation / record / compare --------------------------------------------
def aggregate(xs) -> dict:
    """mean / std (sample) / min / max / n over a list of numbers."""
    xs = [float(x) for x in xs]
    if not xs:
        return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    return {
        "mean": statistics.mean(xs),
        "std": statistics.stdev(xs) if len(xs) > 1 else 0.0,
        "min": min(xs),
        "max": max(xs),
        "n": len(xs),
    }


def build_record(bench: str, provider: str, reps: int, timestamp: str,
                 per_rep: dict, cost_usd: float) -> dict:
    """Build a machine-readable run record. `per_rep` maps a metric name -> its per-rep values."""
    return {
        "bench": bench,
        "provider": provider,
        "reps": reps,
        "timestamp": timestamp,
        "cost_usd": round(float(cost_usd), 6),
        "metrics": {name: aggregate(vals) for name, vals in per_rep.items()},
    }


def compare_records(baseline: dict, current: dict, *, rate_floor: float = 0.05,
                    cost_rel: float = 0.25) -> dict:
    """Compare a current run against a baseline. Returns {ok, findings}.

    A success-rate metric regresses only if its mean dropped below the baseline mean by more than the
    larger of `rate_floor` and the baseline's own stdev — i.e. a drop *within the error bars* is noise,
    not a regression. Cost regresses if it rose more than `cost_rel` over the baseline. `speedup` is
    reported but never gated (machine-dependent micro-timing).
    """
    findings: list[dict] = []
    bm, cm = baseline.get("metrics", {}), current.get("metrics", {})

    for name in _GATED_RATES:
        if name in bm and name in cm:
            b, c = bm[name], cm[name]
            tol = max(rate_floor, float(b.get("std", 0.0)))
            findings.append({
                "metric": name, "gated": True,
                "regressed": c["mean"] < b["mean"] - tol,
                "baseline": b["mean"], "current": c["mean"], "tolerance": tol,
            })

    bc, cc = float(baseline.get("cost_usd", 0.0)), float(current.get("cost_usd", 0.0))
    findings.append({
        "metric": "cost_usd", "gated": True,
        "regressed": bc > 0 and cc > bc * (1 + cost_rel),
        "baseline": bc, "current": cc, "tolerance": bc * cost_rel,
    })

    if "speedup" in bm and "speedup" in cm:  # informational only
        findings.append({
            "metric": "speedup", "gated": False, "regressed": False,
            "baseline": bm["speedup"]["mean"], "current": cm["speedup"]["mean"],
        })

    return {"ok": not any(f["regressed"] for f in findings), "findings": findings}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- benchmark runners (each returns a record) ------------------------------------------------
async def _demo_rep(provider_name: str, root: Path) -> dict:
    url = index_url()
    cache = FlowCache(root=root)  # fresh -> each rep re-learns (the point: discovery variance)
    learn = await run_cached(url, GOAL, get_provider(provider_name), cache, mode="learn", headless=True)
    replay = await run_cached(url, GOAL, None, cache, mode="replay", headless=True)
    ok = bool(replay.success and replay.llm_calls == 0 and SUCCESS_TEXT.lower() in replay.final_text.lower())
    speedup = learn.total_ms / replay.total_ms if replay.total_ms else 0.0
    return {"ok": ok, "speedup": speedup, "cost": _cost(learn),
            "learn_ms": learn.total_ms, "replay_ms": replay.total_ms}


async def run_demo(provider_name: str, reps: int) -> dict:
    print(f"variance: bench=demo-shop provider={provider_name} reps={reps}\n")
    results = []
    with tempfile.TemporaryDirectory() as td:
        for i in range(reps):
            r = await _demo_rep(provider_name, Path(td) / f"rep{i}")
            results.append(r)
            print(f"  rep {i + 1}/{reps}: replay_ok={r['ok']} speedup={r['speedup']:.1f}x "
                  f"learn={r['learn_ms']:.0f}ms replay={r['replay_ms']:.0f}ms ${r['cost']:.4f}")
    record = build_record(
        "demo", provider_name, reps, _now_iso(),
        {"replay_success_rate": [1.0 if r["ok"] else 0.0 for r in results],
         "speedup": [r["speedup"] for r in results if r["ok"]]},
        cost_usd=sum(r["cost"] for r in results),
    )
    sr, sp = record["metrics"]["replay_success_rate"], record["metrics"]["speedup"]
    print(f"\n== demo-shop, {reps} reps ==")
    print(f"replay success:  {int(sr['mean'] * reps + 0.5)}/{reps}  (rate {sr['mean']:.2f})")
    print(f"speedup:         mean {sp['mean']:.1f}x +/- {sp['std']:.1f}  (min {sp['min']:.1f}x, max {sp['max']:.1f}x)")
    print(f"total LLM cost:  ~${record['cost_usd']:.4f}")
    return record


async def run_miniwob(provider_name: str, reps: int, all_tasks: bool, seed: int) -> dict:
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
    record = build_record(
        "miniwob", provider_name, reps, _now_iso(),
        {"replay_success_rate": fracs}, cost_usd=sum(costs),
    )
    sr = record["metrics"]["replay_success_rate"]
    print(f"\n== miniwob ({len(tasks)} tasks), {reps} reps ==")
    print(f"replay success rate: mean {sr['mean'] * 100:.0f}% +/- {sr['std'] * 100:.0f}%  "
          f"(min {sr['min'] * 100:.0f}%, max {sr['max'] * 100:.0f}%)")
    print(f"total LLM cost:      ~${record['cost_usd']:.4f}")
    return record


# --- output / gate ----------------------------------------------------------------------------
def _write_json(record: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\nwrote run record -> {path}")


def _gate(baseline_path: Path, current: dict) -> bool:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    result = compare_records(baseline, current)
    print(f"\n== regression gate vs {baseline_path.name} "
          f"(baseline {baseline.get('reps')} reps @ {baseline.get('timestamp')}) ==")
    for f in result["findings"]:
        tag = "FAIL" if f["regressed"] else ("ok  " if f["gated"] else "info")
        print(f"  [{tag}] {f['metric']:<20} baseline={f['baseline']:.4g}  current={f['current']:.4g}")
    print(f"== {'PASS' if result['ok'] else 'REGRESSION'} ==")
    return result["ok"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="benchmarks.variance")
    ap.add_argument("--bench", choices=["demo", "miniwob"], default="demo")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--all", action="store_true", help="(miniwob) run the broader task set")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--json", type=Path, default=None, metavar="PATH",
                    help="write the run record as JSON (record a baseline)")
    ap.add_argument("--baseline", type=Path, default=None, metavar="PATH",
                    help="gate this run against a saved baseline; exit 1 on regression")
    args = ap.parse_args()

    if args.bench == "demo":
        record = asyncio.run(run_demo(args.provider, args.reps))
    else:
        record = asyncio.run(run_miniwob(args.provider, args.reps, args.all, args.seed))

    if args.json:
        _write_json(record, args.json)
    if args.baseline:
        sys.exit(0 if _gate(args.baseline, record) else 1)
