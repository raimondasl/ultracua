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


def _cost(report) -> "Optional[float]":
    """This report's spend, or None for UNKNOWN. Never 0.0 standing in for an unknown (1.3).

    `or 0.0` collapsed three different things into free: a run that genuinely spent nothing, a run
    whose spend could not be PRICED, and a run that could not see one of its own LLM paths. The
    first is a measurement; the other two are `obs.py` reporting an honest unknown, and summing them
    as zero is what turns an honest instrument into a confident wrong number one function later.
    """
    usage = report.extra.get("usage") or {}
    if "cost_usd" not in usage:
        return None                      # nobody looked — the pre-B1 shrug, still possible off-path
    cost = usage["cost_usd"]
    return None if cost is None else float(cost)


def sum_cost(xs) -> "Optional[float]":
    """UNKNOWN IS ABSORBING. One rep whose cost is unknown makes the total unknown.

    The alternative — sum what is known and report it as the total — is strictly worse than saying
    nothing, because the number looks complete. This is `UsageTotals.cost_usd`'s own rule (any
    unpriced spend makes the whole bill None) applied one level up.

    PUBLIC, AND THE ONLY DEFINITION. 1.3 shipped this twice for one afternoon — here and in
    `drift_bench` — and the two copies disagreed on their first non-trivial input: `[0.1, 0.2]` gave
    `0.30000000000000004` and `0.3`, because only one of them rounded. Two derivations of one fact
    is how the fact drifts, and this one drifted before either copy had a second caller.
    """
    total = 0.0
    for x in xs:
        if x is None:
            return None
        total += float(x)
    return round(total, 6)


def _money(x) -> str:
    """`$0.0000` is a claim; `UNKNOWN` is a shrug. A formatter is where they must stay apart."""
    return "UNKNOWN" if x is None else f"${x:.4f}"


def _g(x) -> str:
    """The same rule for a bare number, and the reason it exists is a defect this slice shipped.

    1.3 made `cost_usd: None` reach the RECORD, added two `compare_records` branches whose findings
    carry `baseline`/`current` as None — and then formatted them with `f"{x:.4g}"`, which raises
    `TypeError: unsupported format string passed to NoneType.__format__`.

    `_money` was written for exactly this and applied to two of the THREE print sites. The third is
    `_gate`, the one that decides the exit code. So the loud channel the slice built was computed
    correctly and then died before printing itself: the `[FAIL] cost_usd` row, its detail and the
    `== REGRESSION ==` verdict never appeared. The inverse case is worse — a baseline whose cost is
    unknown is DESIGNED to pass, and crashed to exit 1 instead.

    A formatter that cannot render an unknown is a formatter that will meet one.
    """
    return "UNKNOWN" if x is None else f"{x:.4g}"


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
        # None survives to the record. `float(None)` would raise, which is at least loud — but a
        # caller that "fixed" it with `or 0.0` is the defect this whole step exists to remove.
        "cost_usd": None if cost_usd is None else round(float(cost_usd), 6),
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

    THE TOLERANCE ASSUMES `per_rep` HOLDS REPS OF ONE BENCHMARK, and that assumption is load-bearing
    rather than incidental. `std` is a measure of run-to-run NOISE only when each value is another
    attempt at the same thing. Hand it one value per DIFFERENT task and the sample stdev of a 0/1
    vector is a closed form of the mean — `sqrt(p(1-p)·n/(n-1))`, largest exactly where the rate is
    most interesting — so the tolerance grows with the spread it is supposed to be measuring.
    Measured on a 10-scenario corpus: a baseline of 0.700 yields std 0.483, and a current run of
    **0.300 does not regress**. B3 (`benchmarks/outcomes.py`) therefore does NOT call this function
    for its rates; it compares against the baseline's Wilson lower bound instead, which is an honest
    error bar on a proportion measured once. Its record still uses this module's record SHAPE.
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

    bc, cc = baseline.get("cost_usd"), current.get("cost_usd")
    if cc is None:
        # A run that cannot account for its own spend has not proved anything about cost, and the
        # gate's job is to be loud about that rather than to compare it against nothing.
        findings.append({"metric": "cost_usd", "gated": True, "regressed": True,
                         "baseline": bc, "current": None,
                         "detail": "this run could not account for its own spend"})
    elif bc is None:
        findings.append({"metric": "cost_usd", "gated": True, "regressed": False,
                         "baseline": None, "current": float(cc),
                         "detail": "the baseline's cost is unknown, so there is nothing to compare "
                                   "against — re-record it rather than reading this as a pass"})
    else:
        bc, cc = float(bc), float(cc)
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
async def _demo_rep(provider_name: str, root: Path, samples: int = 1, reflect: bool = False) -> dict:
    url = index_url()
    cache = FlowCache(root=root)  # fresh -> each rep re-learns (the point: discovery variance)
    learn = await run_cached(url, GOAL, get_provider(provider_name), cache, mode="learn", headless=True,
                             samples=samples, verify_replay=samples > 1, reflect=reflect)
    replay = await run_cached(url, GOAL, None, cache, mode="replay", headless=True)
    ok = bool(replay.success and replay.llm_calls == 0 and SUCCESS_TEXT.lower() in replay.final_text.lower())
    speedup = learn.total_ms / replay.total_ms if replay.total_ms else 0.0
    first_fail = first_failure_index([t.meta.get("ok", True) for t in replay.step_traces])
    return {"ok": ok, "speedup": speedup, "cost": _cost(learn),
            "learn_ms": learn.total_ms, "replay_ms": replay.total_ms, "first_fail": first_fail}


async def run_demo(provider_name: str, reps: int, samples: int = 1, reflect: bool = False) -> dict:
    print(f"variance: bench=demo-shop provider={provider_name} reps={reps} samples={samples} reflect={reflect}\n")
    results = []
    with tempfile.TemporaryDirectory() as td:
        for i in range(reps):
            r = await _demo_rep(provider_name, Path(td) / f"rep{i}", samples, reflect)
            results.append(r)
            print(f"  rep {i + 1}/{reps}: replay_ok={r['ok']} speedup={r['speedup']:.1f}x "
                  f"learn={r['learn_ms']:.0f}ms replay={r['replay_ms']:.0f}ms {_money(r['cost'])}")
    record = build_record(
        "demo", provider_name, reps, _now_iso(),
        {"replay_success_rate": [1.0 if r["ok"] else 0.0 for r in results],
         "speedup": [r["speedup"] for r in results if r["ok"]]},
        cost_usd=sum_cost(r["cost"] for r in results),
        first_fail=[r["first_fail"] for r in results],
    )
    record["samples"], record["reflect"] = samples, reflect
    sr, sp = record["metrics"]["replay_success_rate"], record["metrics"]["speedup"]
    print(f"\n== demo-shop, {reps} reps ==")
    print(f"replay success:  {int(sr['mean'] * reps + 0.5)}/{reps}  (rate {sr['mean']:.2f})")
    print(f"speedup:         mean {sp['mean']:.1f}x +/- {sp['std']:.1f}  (min {sp['min']:.1f}x, max {sp['max']:.1f}x)")
    print(f"total LLM cost:  ~{_money(record['cost_usd'])}")
    _print_reliability(record)
    return record


async def run_miniwob(provider_name: str, reps: int, all_tasks: bool, seed: int, samples: int = 1,
                      reflect: bool = False) -> dict:
    from benchmarks.miniwob_bench import _raw, _run_task
    from benchmarks.miniwob_env import EASY_TASKS, TASKS, StaticServer, miniwob_html_root

    tasks = TASKS if all_tasks else EASY_TASKS
    print(f"variance: bench=miniwob provider={provider_name} reps={reps} tasks={len(tasks)} "
          f"samples={samples} reflect={reflect}\n")
    fracs, costs = [], []
    server = StaticServer(miniwob_html_root())
    base = server.start()
    try:
        with tempfile.TemporaryDirectory() as td:
            for i in range(reps):
                cache = FlowCache(root=Path(td) / f"rep{i}")
                ok, per_task = 0, []
                for task in tasks:
                    _instr, learn, replay = await _run_task(base, cache, task, provider_name, seed,
                                                            samples, reflect)
                    if _raw(replay) > 0:
                        ok += 1
                    per_task.append(_cost(learn))
                # Collected then summed, rather than `cost += _cost(...)`: `+=` cannot carry an
                # unknown, so the accumulator forces a zero at exactly the moment the answer is that
                # nobody knows.
                cost = sum_cost(per_task)
                fracs.append(ok / len(tasks))
                costs.append(cost)
                print(f"  rep {i + 1}/{reps}: replay success {ok}/{len(tasks)} "
                      f"({ok / len(tasks) * 100:.0f}%)  {_money(cost)}")
    finally:
        server.stop()
    record = build_record(
        "miniwob", provider_name, reps, _now_iso(),
        {"replay_success_rate": fracs}, cost_usd=sum_cost(costs),
    )
    record["samples"], record["reflect"] = samples, reflect
    sr = record["metrics"]["replay_success_rate"]
    print(f"\n== miniwob ({len(tasks)} tasks), {reps} reps ==")
    print(f"replay success rate: mean {sr['mean'] * 100:.0f}% +/- {sr['std'] * 100:.0f}%  "
          f"(min {sr['min'] * 100:.0f}%, max {sr['max'] * 100:.0f}%)")
    print(f"total LLM cost:      ~{_money(record['cost_usd'])}")
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
        print(f"  [{tag}] {f['metric']:<20} "
              f"baseline={_g(f['baseline'])}  current={_g(f['current'])}")
        # The `detail` is the whole content of an unknown-cost verdict — "this run could not
        # account for its own spend" versus "the baseline's cost is unknown" are opposite
        # instructions, and the numbers beside them are both UNKNOWN, so printing only the numbers
        # says nothing at all.
        if f.get("detail"):
            print(f"         {f['detail']}")
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
    ap.add_argument("--reflect", action="store_true",
                    help="reflexion: feed a failed attempt's LLM-written lesson to the next sample "
                         "(only meaningful with --samples >1).")
    ap.add_argument("--json", type=Path, default=None, metavar="PATH",
                    help="write the run record as JSON (record a baseline)")
    ap.add_argument("--baseline", type=Path, default=None, metavar="PATH",
                    help="gate this run against a saved baseline; exit 1 on regression")
    args = ap.parse_args()

    if args.bench == "demo":
        record = asyncio.run(run_demo(args.provider, args.reps, args.samples, args.reflect))
    else:
        record = asyncio.run(run_miniwob(args.provider, args.reps, args.all, args.seed, args.samples, args.reflect))

    if args.json:
        _write_json(record, args.json)
    if args.baseline:
        sys.exit(0 if _gate(args.baseline, record) else 1)
