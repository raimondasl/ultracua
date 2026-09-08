"""What does CONTEXT scope see that PAGE scope does not, on a real app? ($0.00, no LLM)

    python -m benchmarks.watcher_scope_probe            # both substrates
    python -m benchmarks.watcher_scope_probe --only odoo

The learn and heal watchers moved from `page.on("request")` to `page.context.on("request")`, because
a Service Worker or other cross-realm fetch is surfaced at the CONTEXT and never at the page -- so a
page-scoped watcher misses such a write entirely, `performed_write` stays False, verify-by-replay
re-drives the flow, and it caches as a read. `recorder.py:745` had already chosen context scope and
said so; two paths never got it.

THIS PROBE MEASURES THE OTHER DIRECTION, which is the one that gets a change like this refused.
Context is a superset of the page for dedicated workers, service workers and popups -- though NOT
for a SHARED worker, which escapes both scopes (R4.154). Over the realms it does add, the risk is
not blindness but NOISE: a background request from
another realm landing inside a step's act window, classified as a write, and marking a step mutating
that never wrote. That is D0's over-refusal shape -- *a `mutating` mark is a GUESS; be conservative
because of one, never refuse a flow for one* -- and on this codebase over-gating costs 0-LLM replay.

It reports, per corpus start page:
  * every request each scope observed, as a MULTISET. A set difference reports 0 where the truth is
    1 whenever the extra request repeats a url the page also fetched -- R4.148's instrument lesson,
    and the first draft of this probe made exactly that mistake.
  * how many of the EXTRA requests `_watch_request` would classify as a write, using the product's
    own `is_write_request` + `body_says_read` rather than a reimplementation of them.

CALIBRATION IS NOT OPTIONAL. A row that silently measured a login page would report a reassuring
zero. Each row asserts the page actually rendered (an interactable count) and says so, because a
survey whose control is broken makes every comparison against it meaningless (R4.134).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ultracua.browser import BrowserSession                              # noqa: E402
from ultracua.safety import body_says_read, is_write_request             # noqa: E402

from benchmarks import corpus                                            # noqa: E402
from benchmarks import substrates as S                                   # noqa: E402


async def _auth_state(substrate: str, work: Path) -> str:
    """A logged-in storage_state through the product's own verb, the shape `pin_viability` uses."""
    import os

    from ultracua.flows import refresh_auth

    from .scored_run import LOGIN, PASS_ENV, USER_ENV, spec_for

    sub = {"gitea": S.Gitea, "odoo": S.Odoo}[substrate]()
    sub.await_ready()
    cfg = LOGIN[substrate]
    os.environ[USER_ENV], os.environ[PASS_ENV] = cfg["user"], cfg["password"]
    state = str(work / f"auth-{substrate}.json")
    await refresh_auth(spec_for(next(iter(corpus.for_substrate(substrate))), sub.url, state),
                       headless=True)
    return state


def _classify(rec: dict) -> bool:
    """EXACTLY what `_watch_request` asks, in the same order: method says write, body does not say
    read. Reimplementing either half would measure the probe rather than the product (R4.134)."""
    if not is_write_request(rec["method"], rec["url"]):
        return False
    return not body_says_read(rec["url"], rec["body"])


async def probe(substrate: str, dwell: float) -> list[dict]:
    work = ROOT / ".scratch" / "watcher_scope"
    work.mkdir(parents=True, exist_ok=True)
    sub = {"gitea": S.Gitea, "odoo": S.Odoo}[substrate]()
    sub.await_ready()
    state = await _auth_state(substrate, work)

    rows: list[dict] = []
    async with BrowserSession(headless=True, storage_state=state) as sess:
        page, ctx = sess.page, sess.page.context
        for entry in corpus.for_substrate(substrate):
            seen_page: list = []
            seen_ctx: list = []

            def _rec(sink):
                def on(req):
                    try:
                        sink.append({"method": req.method, "url": req.url,
                                     "body": req.post_data})
                    except Exception:                                     # noqa: BLE001
                        pass
                return on

            on_page, on_ctx = _rec(seen_page), _rec(seen_ctx)
            page.on("request", on_page)
            ctx.on("request", on_ctx)
            try:
                # `about:blank` FIRST. Odoo routes on the hash, so a goto between two `#action=` urls
                # is a SAME-DOCUMENT navigation that resolves instantly and measures the view it just
                # left (R4.141). Without this hop the whole run is quietly wrong.
                await page.goto("about:blank")
                await sess.goto(f"{sub.url}{entry.scenario.url_path}")
                await sess.await_settled()
                await asyncio.sleep(dwell)
                obs = await sess.snapshot()
            finally:
                page.remove_listener("request", on_page)
                ctx.remove_listener("request", on_ctx)

            # A MULTISET, not a set: the extra request is often a url the page also fetched, and a
            # set difference reports zero for it.
            pk = sorted(f"{r['method']} {r['url']}" for r in seen_page)
            extra: list = []
            remaining = list(pk)
            for r in seen_ctx:
                key = f"{r['method']} {r['url']}"
                if key in remaining:
                    remaining.remove(key)
                else:
                    extra.append(r)

            rows.append({
                "scenario": entry.scenario.name,
                "elements": len(obs.elements),
                "page": len(seen_page),
                "ctx": len(seen_ctx),
                "extra": len(extra),
                "extra_writes": sum(1 for r in extra if _classify(r)),
                "examples": [f"{r['method']} {r['url'][:64]}" for r in extra[:3]],
            })
    return rows


async def main() -> None:
    ap = argparse.ArgumentParser(prog="benchmarks.watcher_scope_probe")
    ap.add_argument("--only", choices=["gitea", "odoo"], default=None)
    ap.add_argument("--dwell", type=float, default=6.0,
                    help="seconds to keep listening after the page settles")
    args = ap.parse_args()

    subs = [args.only] if args.only else ["gitea", "odoo"]
    total_extra = total_writes = 0
    for sub in subs:
        print(f"\n=== {sub} ===")
        print(f"{'scenario':24} {'els':>4} {'page':>5} {'ctx':>5} {'extra':>6} {'extra WRITES':>13}")
        for r in await probe(sub, args.dwell):
            flag = "" if r["elements"] else "   <-- EMPTY PAGE: this row measures nothing"
            print(f"{r['scenario']:24} {r['elements']:4} {r['page']:5} {r['ctx']:5} "
                  f"{r['extra']:6} {r['extra_writes']:13}{flag}")
            for ex in r["examples"]:
                print(f"{'':24}   extra: {ex}")
            total_extra += r["extra"]
            total_writes += r["extra_writes"]

    print(f"\n  context scope saw {total_extra} request(s) page scope did not; "
          f"{total_writes} of them would be classified as a WRITE.")
    print("  Any non-zero WRITE count is the over-refusal risk and must be read before shipping;")
    print("  extra non-write traffic costs nothing, because `_watch_request` returns on it first.")


if __name__ == "__main__":
    asyncio.run(main())
