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
from math import comb
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


def wilson_ci(c: int, n: int, z: float = 1.96) -> "tuple[float, float]":
    """Wilson score interval for a success rate c/n — honest error bars at small n (and at 0/n)."""
    if n == 0:
        return (0.0, 0.0)
    p = c / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * (((p * (1 - p) + z2 / (4 * n)) / n) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def pass_hat_k(c: int, n: int, k: int) -> float:
    """pass^k: the probability that k reps drawn from the n run reps are ALL successes (c succeeded).

    This is the reliability metric production cares about — a 70%-per-run agent is ~34% at pass^3, not
    70%. Reporting only the mean rate hides that. Unbiased over the n reps: C(c,k)/C(n,k).
    """
    if k <= 0 or k > n or c < k:
        return 0.0
    return comb(c, k) / comb(n, k)


def pass_k_curve(successes, kmax: "Optional[int]" = None) -> dict:
    """{k: pass^k} for k=1..kmax over a per-rep boolean success list."""
    succ = [bool(s) for s in successes]
    n, c = len(succ), sum(succ)
    kmax = n if kmax is None else min(kmax, n)
    return {str(k): pass_hat_k(c, n, k) for k in range(1, max(1, kmax) + 1)}


def first_failure_index(step_oks) -> "Optional[int]":
    """Index of the first step whose ok flag is False; None if every step passed."""
    for i, ok in enumerate(step_oks):
        if not ok:
            return i
    return None


def hazard_curve(first_fail_indices) -> dict:
    """Histogram of WHERE flows first fail: {step_index: count}. `None` entries (no failure) skipped.

    Aggregated over reps, this points at exactly which step the author/replay is unreliable on — the
    precise signal suffix-replan and the authoring fixes need.
    """
    out: dict = {}
    for idx in first_fail_indices:
        if idx is not None:
            out[str(idx)] = out.get(str(idx), 0) + 1
    return out


def build_record(bench: str, provider: str, reps: int, timestamp: str,
                 per_rep: dict, cost_usd: float, *, success_key: str = "replay_success_rate",
                 first_fail: "Optional[list]" = None) -> dict:
    """Build a machine-readable run record. `per_rep` maps a metric name -> its per-rep values.

    Adds reliability views over `per_rep[success_key]` (treated as per-rep 0/1 or fraction==1.0): a
    pass^k curve, a Wilson CI on the fully-passed rate, and (if `first_fail` step indices are given)
    a per-step hazard histogram.
    """
    rec = {
        "bench": bench,
        "provider": provider,
        "reps": reps,
        "timestamp": timestamp,
        "cost_usd": round(float(cost_usd), 6),
        "metrics": {name: aggregate(vals) for name, vals in per_rep.items()},
    }
    rates = per_rep.get(success_key, [])
    passed = [float(x) >= 1.0 for x in rates]  # a rep "passes" iff it fully succeeded
    rec["pass_k"] = pass_k_curve(passed)
    lo, hi = wilson_ci(sum(passed), len(passed))
    rec["pass_rate_wilson95"] = {"lo": lo, "hi": hi, "passes": sum(passed), "n": len(passed)}
    if first_fail is not None:
        rec["hazard"] = hazard_curve(first_fail)
    return rec


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
async def _demo_rep(provider_name: str, root: Path, samples: int = 1) -> dict:
    url = index_url()
    cache = FlowCache(root=root)  # fresh -> each rep re-learns (the point: discovery variance)
    learn = await run_cached(url, GOAL, get_provider(provider_name), cache, mode="learn", headless=True,
                             samples=samples, verify_replay=samples > 1)
    replay = await run_cached(url, GOAL, None, cache, mode="replay", headless=True)
    ok = bool(replay.success and replay.llm_calls == 0 and SUCCESS_TEXT.lower() in replay.final_text.lower())
    speedup = learn.total_ms / replay.total_ms if replay.total_ms else 0.0
    first_fail = first_failure_index([t.meta.get("ok", True) for t in replay.step_traces])
    return {"ok": ok, "speedup": speedup, "cost": _cost(learn),
            "learn_ms": learn.total_ms, "replay_ms": replay.total_ms, "first_fail": first_fail}


async def run_demo(provider_name: str, reps: int, samples: int = 1) -> dict:
    print(f"variance: bench=demo-shop provider={provider_name} reps={reps} samples={samples}\n")
    results = []
    with tempfile.TemporaryDirectory() as td:
        for i in range(reps):
            r = await _demo_rep(provider_name, Path(td) / f"rep{i}", samples)
            results.append(r)
            print(f"  rep {i + 1}/{reps}: replay_ok={r['ok']} speedup={r['speedup']:.1f}x "
                  f"learn={r['learn_ms']:.0f}ms replay={r['replay_ms']:.0f}ms ${r['cost']:.4f}")
    record = build_record(
        "demo", provider_name, reps, _now_iso(),
        {"replay_success_rate": [1.0 if r["ok"] else 0.0 for r in results],
         "speedup": [r["speedup"] for r in results if r["ok"]]},
        cost_usd=sum(r["cost"] for r in results),
        first_fail=[r["first_fail"] for r in results],
    )
    record["samples"] = samples
    sr, sp = record["metrics"]["replay_success_rate"], record["metrics"]["speedup"]
    print(f"\n== demo-shop, {reps} reps ==")
    print(f"replay success:  {int(sr['mean'] * reps + 0.5)}/{reps}  (rate {sr['mean']:.2f})")
    print(f"speedup:         mean {sp['mean']:.1f}x +/- {sp['std']:.1f}  (min {sp['min']:.1f}x, max {sp['max']:.1f}x)")
    print(f"total LLM cost:  ~${record['cost_usd']:.4f}")
    _print_reliability(record)
    return record


async def run_miniwob(provider_name: str, reps: int, all_tasks: bool, seed: int, samples: int = 1) -> dict:
    from benchmarks.miniwob_bench import _raw, _run_task
    from benchmarks.miniwob_env import EASY_TASKS, TASKS, StaticServer, miniwob_html_root

    tasks = TASKS if all_tasks else EASY_TASKS
    print(f"variance: bench=miniwob provider={provider_name} reps={reps} tasks={len(tasks)} samples={samples}\n")
    fracs, costs = [], []
    server = StaticServer(miniwob_html_root())
    base = server.start()
    try:
        with tempfile.TemporaryDirectory() as td:
            for i in range(reps):
                cache = FlowCache(root=Path(td) / f"rep{i}")
                ok, cost = 0, 0.0
                for task in tasks:
                    _instr, learn, replay = await _run_task(base, cache, task, provider_name, seed, samples)
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
    record["samples"] = samples
    sr = record["metrics"]["replay_success_rate"]
    print(f"\n== miniwob ({len(tasks)} tasks), {reps} reps ==")
    print(f"replay success rate: mean {sr['mean'] * 100:.0f}% +/- {sr['std'] * 100:.0f}%  "
          f"(min {sr['min'] * 100:.0f}%, max {sr['max'] * 100:.0f}%)")
    print(f"total LLM cost:      ~${record['cost_usd']:.4f}")
    _print_reliability(record)
    return record


def _print_reliability(record: dict) -> None:
    """pass^k (all-k-succeed) + Wilson CI on the fully-passed rate + where flows first fail."""
    pk = record.get("pass_k") or {}
    w = record.get("pass_rate_wilson95") or {}
    if pk:
        print("pass^k:          " + "  ".join(f"k={k}:{v:.2f}" for k, v in pk.items()))
    if w:
        print(f"fully-passed:    {w.get('passes')}/{w.get('n')}  "
              f"(95% CI {w.get('lo', 0):.2f}-{w.get('hi', 0):.2f})")
    if record.get("hazard"):
        haz = ", ".join(f"step{k}:{v}" for k, v in sorted(record["hazard"].items(), key=lambda kv: int(kv[0])))
        print(f"first-fail step: {haz}")


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
    ap.add_argument("--samples", type=int, default=1,
                    help="best-of-N authoring: re-author up to N times, keep the first verified sample "
                         "(N>1 enables the verify-by-replay oracle). Default 1 = the N=1 baseline.")
    ap.add_argument("--json", type=Path, default=None, metavar="PATH",
                    help="write the run record as JSON (record a baseline)")
    ap.add_argument("--baseline", type=Path, default=None, metavar="PATH",
                    help="gate this run against a saved baseline; exit 1 on regression")
    args = ap.parse_args()

    if args.bench == "demo":
        record = asyncio.run(run_demo(args.provider, args.reps, args.samples))
    else:
        record = asyncio.run(run_miniwob(args.provider, args.reps, args.all, args.seed, args.samples))

    if args.json:
        _write_json(record, args.json)
    if args.baseline:
        sys.exit(0 if _gate(args.baseline, record) else 1)
