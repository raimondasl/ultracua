"""Would an IN-FLIGHT REQUEST sensor replace the readiness poll's stall guard? (R4.146.)

    uv run --no-sync python -m benchmarks.inflight_probe --bench          # free
    uv run --no-sync python -m benchmarks.inflight_probe --scenario odoo-create-lead --reps 3

R4.146 names one candidate for replacing "six consecutive quiet looks": ask whether the browser is
WAITING ON SOMETHING. The premise is that a network-gated render has requests pending while it
pauses, and a page whose element is simply GONE has none. This probe measures that premise on both
populations, because D5's bar is a sensor measured against the existing artifacts rather than a
better-sounding inference -- and because both previous cost readings for this mechanism were wrong,
each taken on a population it does not fire in.

WHAT IT FOUND, so the next reader does not re-buy it:

  * `--bench` (free): of 126 retry-path entries, the **36 that STALL have ZERO requests pending at
    entry and zero at any point**, and they cost **23.4 s**. A sensor that trusted a zero AT ENTRY
    would give up on all 36 at once.
  * `--scenario odoo-create-lead` (paid): the render gap has **1-2 pending continuously from entry**,
    so the entry reading separates the two populations cleanly.
  * **BUT THE SIMPLE VERSION IS REFUTED.** Sampling every 25 ms through a whole poll, the pending
    count returns to ZERO mid-render -- busy spans 16-688 ms and zeros span 516-891 ms on the same
    run, with the element only appearing near 907 ms. Odoo's last stage renders the assets it
    fetched, which is CPU work with nothing in flight. So "nothing pending" does NOT mean "nothing
    is coming", and a sensor keyed on the live count needs the same consecutive-count crutch the
    stall guard already has.

**SPENDS MONEY** in `--scenario` mode: one learn plus one replay per rep, ~$0.09 each on Odoo.
`--bench` spends nothing and needs no substrate.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
import time

from ultracua import flow as flow_mod


def hook(page) -> None:
    """Maintain a live in-flight count on the page object.

    Counted from Playwright's own request lifecycle rather than from a `networkidle` wait, which is
    separately refuted here: Odoo's message bus holds a connection open, so `networkidle` never
    fires at all. "How many are outstanding right now" is a strictly weaker question and it does
    have an answer.
    """
    if getattr(page, "_inflight_hooked", False):
        return
    page._inflight_hooked = True
    page._inflight = 0

    def started(_r):
        page._inflight += 1

    def done(_r):
        page._inflight = max(0, page._inflight - 1)

    page.on("request", started)
    page.on("requestfinished", done)
    page.on("requestfailed", done)


def install(rows: list, *, sample_ms: int = 0):
    """Wrap `_retry_if_unpainted` so every entry records what the network was doing.

    Patches the binding in `flow.py`, not the definition: `from .locators import ...` binds the
    OBJECT, and this module's whole value is that it observes the code the replay actually runs.
    """
    real = flow_mod._retry_if_unpainted

    async def spy(session, page, spec, tr, loc, *, sink=None, tag: str = ""):
        hook(page)
        if loc is not None:                       # the happy path never reaches the poll
            return await real(session, page, spec, tr, loc, sink=sink, tag=tag)
        at_entry = page._inflight
        t0 = time.monotonic()
        samples: list = []
        stop = False

        async def sampler():
            while not stop:
                samples.append((round((time.monotonic() - t0) * 1000), page._inflight))
                await asyncio.sleep(sample_ms / 1000.0)

        task = asyncio.create_task(sampler()) if sample_ms else None
        try:
            out = await real(session, page, spec, tr, loc, sink=sink, tag=tag)
        finally:
            stop = True
            if task is not None:
                await asyncio.sleep(0)
                task.cancel()
        rows.append({"verdict": tr.meta.get(tag + "readiness_retry") or "?",
                     "ms": round((time.monotonic() - t0) * 1000),
                     "inflight_at_entry": at_entry, "bound": out is not None,
                     "samples": samples})
        return out

    flow_mod._retry_if_unpainted = spy
    return real


def summarise(rows: list) -> dict:
    by: dict = collections.defaultdict(list)
    for r in rows:
        v = r["verdict"]
        kind = v.split(":", 1)[1].split(":")[0] if ":" in v else v
        by[kind].append(r)
    out = {}
    for kind, rs in by.items():
        out[kind] = {"n": len(rs), "total_ms": sum(x["ms"] for x in rs),
                     "busy_at_entry": sum(1 for x in rs if x["inflight_at_entry"] > 0),
                     "max_at_entry": max(x["inflight_at_entry"] for x in rs)}
    return out


def _render(title: str, rows: list) -> None:
    print(f"\n=== {title} ===")
    for kind, s in sorted(summarise(rows).items(), key=lambda kv: -kv[1]["total_ms"]):
        print(f"  {kind:16} n={s['n']:4}  total={s['total_ms']/1000:6.1f}s  "
              f"busy AT ENTRY {s['busy_at_entry']}/{s['n']}  max {s['max_at_entry']}")


async def _scenario(name: str, reps: int, sample_ms: int) -> list:
    from . import scored_run

    all_rows: list = []
    for i in range(1, reps + 1):
        rows: list = []
        install(rows, sample_ms=sample_ms)
        out, scored = await scored_run.score_one(name)
        print(f"  rep{i}: {(scored.verdict.outcome if scored else None)!r}", flush=True)
        for r in rows:
            busy = [v for _t, v in r["samples"] if v > 0]
            zeros = [t for t, v in r["samples"] if v == 0]
            print(f"    {r['verdict']!r:26} {r['ms']:5} ms  at entry={r['inflight_at_entry']}"
                  + (f"  busy {len(busy)}/{len(r['samples'])} samples" if r["samples"] else ""),
                  flush=True)
            if zeros and busy:
                bt = [t for t, v in r["samples"] if v > 0]
                print(f"      busy spans {min(bt)}..{max(bt)} ms; zeros span "
                      f"{min(zeros)}..{max(zeros)} ms -- OVERLAPPING means a live count cannot be "
                      f"trusted mid-render", flush=True)
        all_rows += rows
    return all_rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bench", action="store_true", help="drift_bench, free")
    ap.add_argument("--scenario", default=None, help="a corpus scenario -- SPENDS MONEY")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--sample-ms", type=int, default=25,
                    help="live-count sampling interval; 0 records only the entry reading")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if not a.bench and not a.scenario:
        raise SystemExit("nothing to do: pass --bench (free) or --scenario NAME (spends money)")

    rows: list = []
    if a.bench:
        from . import drift_bench

        install(rows, sample_ms=0)     # the bench is 370 rows; per-look sampling would dominate it
        asyncio.run(drift_bench.measure())
        _render("drift_bench: the retry path, by verdict", rows)
    if a.scenario:
        srows = asyncio.run(_scenario(a.scenario, a.reps, a.sample_ms))
        _render(f"{a.scenario}: the retry path", srows)
        rows += srows

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"summary": summarise(rows),
                                 "rows": [{k: v for k, v in r.items() if k != "samples"}
                                          for r in rows]}, indent=2) + "\n")
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
