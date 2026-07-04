"""Eval runner CLI — manual, partial-run-configurable, cost-aware. NOT wired into CI.

    uv run python -m evals.run --list                     # inventory
    uv run python -m evals.run --estimate                 # $ table: full suite vs tiers/groups
    uv run python -m evals.run                            # run key-less scenarios ($0, default)
    uv run python -m evals.run --group h03,h07,core       # partial: only those groups
    uv run python -m evals.run --id h08                   # partial: ids containing "h08"
    uv run python -m evals.run --tag writes               # partial: by tag
    uv run python -m evals.run --include-llm --budget 2.0 # + LLM tier, hard $ cap
    uv run python -m evals.run --config my_run.json       # saved partial-run config

--config JSON shape (all keys optional; CLI flags override):
    {"groups": ["core","h03"], "ids": ["h03.slots"], "tags": [], "include_llm": true,
     "include_live": false, "budget_usd": 2.5}

Scoring: score = pass / (pass + fail + missing). `missing` = capability not built yet (the
aspirational gap); `fail` = a built capability misbehaved (a regression); `skip` is excluded.
A low score on horizon groups is EXPECTED — the suite is the target, not the gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from evals.core import REGISTRY, Ctx, Scenario, load_all_scenarios

RESULTS_DIR = Path(__file__).parent / "results"


# --- selection ---------------------------------------------------------------------------------
def _selected(args) -> list[Scenario]:
    scns = sorted(REGISTRY.values(), key=lambda s: s.id)
    if args.groups:
        want = {g.strip().lower() for g in args.groups}
        scns = [s for s in scns if s.group.lower() in want]
    if args.ids:
        scns = [s for s in scns if any(frag in s.id for frag in args.ids)]
    if args.tags:
        want = {t.strip().lower() for t in args.tags}
        scns = [s for s in scns if want & {t.lower() for t in s.tags}]
    return scns


def _runnable(scns: list[Scenario], args) -> tuple[list[Scenario], list[Scenario]]:
    """Split the selection into (run now, excluded-by-tier)."""
    run, excluded = [], []
    for s in scns:
        if s.requires == "llm" and not args.include_llm:
            excluded.append(s)
        elif s.requires == "live" and not args.include_live:
            excluded.append(s)
        else:
            run.append(s)
    return run, excluded


# --- estimate ----------------------------------------------------------------------------------
def _estimate(scns: list[Scenario]) -> None:
    by_tier: dict[str, list[Scenario]] = defaultdict(list)
    for s in scns:
        by_tier[s.requires].append(s)
    print(f"\n{len(scns)} scenario(s) selected — estimated cost of ONE full run of this selection:\n")
    total = 0.0
    for tier in ("none", "llm", "live"):
        ss = by_tier.get(tier, [])
        if not ss:
            continue
        cost = sum(s.est_cost_usd for s in ss)
        calls = sum(s.est_llm_calls for s in ss)
        total += cost
        label = {"none": "key-less (default run)", "llm": "LLM tier (--include-llm)",
                 "live": "live-site tier (--include-live)"}[tier]
        print(f"  {label:34s} {len(ss):3d} scenarios  ~{calls:4d} LLM calls  ~${cost:7.2f}")
    print(f"  {'TOTAL':34s} {len(scns):3d} scenarios  {'':11s}  ~${total:7.2f}\n")
    print("  by group (only groups with a nonzero estimate):")
    by_group: dict[str, float] = defaultdict(float)
    gcalls: dict[str, int] = defaultdict(int)
    for s in scns:
        by_group[s.group] += s.est_cost_usd
        gcalls[s.group] += s.est_llm_calls
    for g in sorted(by_group):
        if by_group[g] > 0:
            print(f"    {g:10s} ~{gcalls[g]:4d} calls  ~${by_group[g]:6.2f}")
    print("\n  Estimates are calibrated on measured baselines (see evals/README.md): a scripted")
    print("  learn ~= $0.09, demo learn+replay ~= $0.27, one extraction/caption call ~= $0.02.")
    print("  Key-less scenarios cost $0 (local fixtures + scripted providers, real Chromium).")


# --- run ---------------------------------------------------------------------------------------
def _run_one(s: Scenario) -> dict:
    """Run one scenario in its own event loop + temp dir; NEVER let it crash the suite."""
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"uceval-{s.group}-") as td:
        ctx = Ctx(tmp=Path(td))
        usage = None
        try:
            checks = asyncio.run(s.fn(ctx))
            checks = list(checks or [])
        except Exception as exc:  # noqa: BLE001 — a scenario bug is a report row, not a crash
            checks = [{"name": "scenario crashed", "status": "error", "note": f"{type(exc).__name__}: {exc}"}]
            checks = checks  # already dicts
        else:
            checks = [{"name": c.name, "status": c.status, "note": c.note} for c in checks]
        if ctx._router is not None:  # measured $ for LLM scenarios
            try:
                from ultracua.config import settings

                usage = ctx._router.totals.as_dict(settings.model)
            except Exception:  # noqa: BLE001
                usage = None
    n = defaultdict(int)
    for c in checks:
        n[c["status"]] += 1
    denom = n["pass"] + n["fail"] + n["missing"] + n["error"]
    return {
        "id": s.id, "title": s.title, "group": s.group, "requires": s.requires,
        "aspirational": s.aspirational, "checks": checks,
        "score": round(n["pass"] / denom, 3) if denom else None,
        "counts": dict(n), "seconds": round(time.perf_counter() - started, 2),
        "usage": usage, "est_cost_usd": s.est_cost_usd,
    }


def _summarize(rows: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(r["group"], defaultdict(int))
        for k, v in r["counts"].items():
            g[k] += v
    out = {}
    for gname, n in sorted(groups.items()):
        denom = n["pass"] + n["fail"] + n["missing"] + n["error"]
        out[gname] = {
            "checks": denom, "pass": n["pass"], "fail": n["fail"] + n["error"],
            "missing": n["missing"], "skip": n["skip"],
            "score": round(n["pass"] / denom, 3) if denom else None,
        }
    return out


def _harden_console() -> None:
    """On a legacy Windows console (cp1252), a single non-cp1252 char in a scenario title/note/id
    would crash a print() with UnicodeEncodeError and abort --estimate/--list/a run. Scenario text
    is authored freely across 100+ scenarios, so degrade gracefully — replace the odd char with '?'
    instead of dying. Only affects chars the console couldn't encode anyway; ASCII/cp1252 unchanged."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # keeps the encoding; only changes error handling
        except (AttributeError, ValueError):  # redirected to a non-reconfigurable stream: fine
            pass


def main(argv=None) -> int:
    _harden_console()
    ap = argparse.ArgumentParser(prog="evals.run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list selected scenarios and exit")
    ap.add_argument("--estimate", action="store_true", help="print the $ cost estimate and exit")
    ap.add_argument("--group", dest="groups", type=lambda v: v.split(","), default=None)
    ap.add_argument("--id", dest="ids", action="append", default=None,
                    help="substring match on scenario id (repeatable)")
    ap.add_argument("--tag", dest="tags", type=lambda v: v.split(","), default=None)
    ap.add_argument("--include-llm", action="store_true", help="run requires='llm' scenarios (real $)")
    ap.add_argument("--include-live", action="store_true", help="run requires='live' scenarios")
    ap.add_argument("--budget", dest="budget_usd", type=float, default=None,
                    help="hard $ cap: skip remaining LLM scenarios once estimated+measured spend would exceed it")
    ap.add_argument("--config", default=None, help="JSON file with saved partial-run filters")
    ap.add_argument("--out", default=None, help="report path (default evals/results/eval-<ts>.json)")
    args = ap.parse_args(argv)

    if args.config:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
        args.groups = args.groups or cfg.get("groups")
        args.ids = args.ids or cfg.get("ids")
        args.tags = args.tags or cfg.get("tags")
        args.include_llm = args.include_llm or bool(cfg.get("include_llm"))
        args.include_live = args.include_live or bool(cfg.get("include_live"))
        args.budget_usd = args.budget_usd if args.budget_usd is not None else cfg.get("budget_usd")

    load_all_scenarios()
    selected = _selected(args)
    if not selected:
        print("no scenarios match the selection", file=sys.stderr)
        return 2

    if args.list:
        for s in selected:
            asp = " [aspirational]" if s.aspirational else ""
            cost = f" ~${s.est_cost_usd:.2f}" if s.est_cost_usd else ""
            print(f"  {s.id:44s} {s.group:7s} requires={s.requires:4s}{cost}{asp}  {s.title}")
        print(f"\n{len(selected)} scenario(s).")
        return 0

    if args.estimate:
        _estimate(selected)
        return 0

    to_run, excluded = _runnable(selected, args)
    if excluded:
        tiers = sorted({s.requires for s in excluded})
        print(f"note: {len(excluded)} scenario(s) excluded (tier {', '.join(tiers)}) — "
              f"enable with --include-llm / --include-live")

    rows: list[dict] = []
    spent_est = 0.0
    for s in to_run:
        if (args.budget_usd is not None and s.requires == "llm"
                and spent_est + s.est_cost_usd > args.budget_usd):
            rows.append({"id": s.id, "title": s.title, "group": s.group, "requires": s.requires,
                         "aspirational": s.aspirational, "checks": [],
                         "score": None, "counts": {"skip": 1}, "seconds": 0.0, "usage": None,
                         "est_cost_usd": s.est_cost_usd})
            print(f"  SKIP  {s.id}  (budget: ~${spent_est:.2f} spent-est + ~${s.est_cost_usd:.2f} "
                  f"> ${args.budget_usd:.2f})")
            continue
        r = _run_one(s)
        if s.requires == "llm":
            measured = (r.get("usage") or {}).get("cost_usd")
            spent_est += measured if measured is not None else s.est_cost_usd
        rows.append(r)
        n = r["counts"]
        print(f"  {r['score'] if r['score'] is not None else '-':>5}  {s.id:44s} "
              f"pass={n.get('pass', 0)} fail={n.get('fail', 0) + n.get('error', 0)} "
              f"missing={n.get('missing', 0)} skip={n.get('skip', 0)}  ({r['seconds']}s)")

    summary = _summarize(rows)
    print("\n=== summary (score = pass / (pass+fail+missing); low horizon scores are expected) ===")
    print("    NOTE: horizon (hNN) scores include PARTIAL CREDIT for shipped building blocks —")
    print("    'missing' is the unbuilt-capability count (the roadmap gap); 'fail' is a regression.")
    for g, s in summary.items():
        print(f"  {g:8s} score={s['score'] if s['score'] is not None else '-':>5}  "
              f"pass={s['pass']:3d} fail={s['fail']:3d} missing={s['missing']:3d} skip={s['skip']:3d}")
    measured_total = sum((r.get("usage") or {}).get("cost_usd") or 0.0 for r in rows)
    if measured_total:
        print(f"\n  measured LLM spend this run: ~${measured_total:.2f}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else RESULTS_DIR / f"eval-{time.strftime('%Y%m%d-%H%M%S')}.json"
    try:
        from ultracua import __version__ as ucv
    except Exception:  # noqa: BLE001
        ucv = "unknown"
    out.write_text(json.dumps({
        "ultracua_version": ucv, "ts": time.time(),
        "filters": {"groups": args.groups, "ids": args.ids, "tags": args.tags,
                    "include_llm": args.include_llm, "include_live": args.include_live,
                    "budget_usd": args.budget_usd},
        "summary": summary, "measured_cost_usd": round(measured_total, 4), "scenarios": rows,
    }, indent=2), encoding="utf-8")
    print(f"  report: {out}")
    # Exit 0 even on low scores: aspirational misses are the expected state, not a failure.
    # Exit 1 only if a scenario ERRORED (an eval bug) so authoring mistakes surface.
    return 1 if any(r["counts"].get("error") for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
