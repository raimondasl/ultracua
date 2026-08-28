"""Does the replay read page state BEFORE a client-rendered app has painted it? (R4.115.)

    uv run --no-sync python -m benchmarks.readiness_probe --contrast   # needs substrates
    uv run --no-sync python -m benchmarks.readiness_probe --remedy     # needs nothing

WHAT IT ANSWERS. `docs/reads-over-post.md` and R4.114 both concluded that Odoo's replay blocker was
the LOCATOR. It is not. On a RENDERED page the failing spec resolves uniquely on the first candidate
(`bound_by=exact-text`, n=1, no ambiguity anywhere in the ladder). What the replay is looking at is a
page that has not rendered: measured at the real failing call, `thead th` 0 and `body_chars` 3.

THREE SITES read page state, and every one is reached with no settle:

  1. `flow.py`'s `resolve(page, step.locator, unique=True)`  -> "locator unresolved or ambiguous"
  2. the mutation gate's `scope_fingerprint(target)`         -> "form/section drift"
  3. the mutation gate's whole-page `session.snapshot()`     -> "page drift"

`--contrast` measures sites 1 and 3 across a corpus without replaying anything: for each scenario it
navigates and records what is present AT domcontentloaded versus after settling, and how many
distinct whole-page fingerprints the page takes. Measured 0.142.0 -- Gitea 7/7 complete at
domcontentloaded with ONE fingerprint each; Odoo 0/7, every scenario exactly 5 elements and 1
character, taking 3-4 fingerprints and settling at 0.62-1.08 s.

`--remedy` is the other half, and it is why this file does not propose a fix. The obvious remedy --
"retry `resolve` while it returns None" -- is REFUTED, because `None` is an overloaded sensor. It
means at least five things and FOUR of them are deliberate safety refusals (ambiguity under
`unique=True`, the Tier-2 cross-check conflict, an identity contradiction, the row-containment
guard). A poll cannot tell "the SPA has not rendered" from "I found it and it is the WRONG record",
so it re-asks a refusal until the DOM reaches a state where it no longer fires -- which, for a
refusal keyed on a COMPETING candidate, means waiting for the competitor to disappear. Measured:
two per-row `Cancel` links, the recorded row hidden at t=400ms (the ordinary SPA client-side delete
`locators.resolve` documents), today refuses LOUD and the polled version binds `/cancel/30` at
0.41 s via `role+name` -- a Tier-1 confident candidate, so nothing cross-checks it -- where
`/cancel/3` was recorded. That is D5's overloaded-`None` fault one level up from `anchor_id`, and
R3.7 has already defeated two attempts at exactly this shape.

TWO TRAPS THIS PROBE EXISTS TO STOP THE NEXT PERSON REPEATING. Both reported a GREEN result for this
very finding before they were caught, one release apart from each other:

  * `RunRecord.llm_calls` is the DECIDE counter and its own comment says so ("API calls are
    usage['calls'] -- they differ"). An extracting read runs `extract()` inside the finalize closure,
    which that counter structurally cannot see. A replay reporting `llm_calls: 0` had made one real
    strong-tier call. Read `usage["calls"]`, which is what `scored_run` already does.
  * `odoo-sort-list`'s oracle compares an ANSWER STRING. The extractor is handed the whole page body
    with the goal as its prompt, so it ranks the rows itself and answers correctly over an ASCENDING
    list. `RESULT == EXPECTED` is therefore not evidence that the replay did the task (R4.116).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import corpus                                          # noqa: E402
from benchmarks import substrates as S                                 # noqa: E402
from benchmarks.scored_run import LOGIN, PASS_ENV, USER_ENV, spec_for  # noqa: E402
from ultracua import locators as L                                     # noqa: E402
from ultracua.config import settings                                   # noqa: E402
from ultracua.flows import refresh_auth                                # noqa: E402
from ultracua.snapshot import capture                                  # noqa: E402

SUBSTRATES = {"gitea": S.Gitea, "odoo": S.Odoo}

# Interactable content, counted the same way at both instants.
_COUNT_JS = ("() => ({els: document.querySelectorAll("
             "'a,button,input,select,textarea,[role=button],[onclick],th,td').length,"
             " chars: (document.body.innerText||'').length})")


# --------------------------------------------------------------------------- the remedy refutation

# Two per-row controls sharing a name, and the recorded row is HIDDEN rather than removed at 400ms --
# `locators.resolve`'s own docstring records this exact shape as what produced a wrong-row write.
_TWO_ROWS = """
<body><table><tbody>
  <tr id="r3"><td>Acme Corp #3</td><td><a href="/cancel/3">Cancel</a></td></tr>
  <tr id="r30"><td>Beta LLC #30</td><td><a href="/cancel/30">Cancel</a></td></tr>
</tbody></table>
<script>setTimeout(function(){document.getElementById('r3').style.display='none';},400);</script>
</body>
"""

# The css must ALSO be ambiguous or Tier 2 binds at t=0 and there is no refusal to re-drive -- which
# is what a first draft of this probe got wrong, reporting "not reproduced" while testing nothing.
# A per-row control captured by a structural path carrying no row index is the ordinary case.
REMEDY_SPEC = dict(role="link", name="Cancel", tag="a", text="Cancel",
                   css="tbody > tr > td > a",
                   anchor="Acme Corp #3 Cancel", anchor_source="row", anchor_id=None)
RECORDED_HREF = "/cancel/3"


async def _poll_resolve(page, spec, budget_ms: int = 5000):
    """The naive readiness remedy: retry while `resolve` returns None."""
    sink: dict = {}
    got = await L.resolve(page, spec, unique=True, sink=sink)
    waited = 0
    while got is None and waited < budget_ms:
        await page.wait_for_timeout(100)
        waited += 100
        sink = {}
        got = await L.resolve(page, spec, unique=True, sink=sink)
    return got, sink, waited


async def remedy() -> dict:
    """Show that polling on `resolve() -> None` re-drives a LOUD refusal into a wrong-record bind."""
    spec = L.LocatorSpec(**REMEDY_SPEC)
    from playwright.async_api import async_playwright

    async def href(loc):
        if loc is None:
            return None
        try:
            return await loc.evaluate("e => e.getAttribute('href')")
        except Exception:                                              # noqa: BLE001
            return "<detached>"

    async with async_playwright() as p:
        br = await p.chromium.launch(headless=True)
        page = await (await br.new_context()).new_page()

        await page.set_content(_TWO_ROWS)
        t_sink: dict = {}
        today = await L.resolve(page, spec, unique=True, sink=t_sink)
        today_href = await href(today)

        await page.set_content(_TWO_ROWS)
        polled, p_sink, waited = await _poll_resolve(page, spec)
        polled_href = await href(polled)
        await br.close()

    out = {"today": {"bound": today is not None, "sink": t_sink, "href": today_href},
           "polled": {"bound": polled is not None, "sink": p_sink, "href": polled_href,
                      "waited_ms": waited},
           "recorded_href": RECORDED_HREF}
    out["wrong_record_bind"] = bool(polled is not None and polled_href != RECORDED_HREF)
    return out


# ------------------------------------------------------------------------------ the readiness census


async def _trace(page, url: str) -> dict:
    """What is present AT domcontentloaded, versus after the page stops changing."""
    t0 = time.monotonic()
    await page.goto(url, wait_until="domcontentloaded")
    at_dcl = await page.evaluate(_COUNT_JS)
    fps: list = []
    prev = None
    settled_at = None
    for _ in range(30):
        obs = await capture(page, settings.max_elements, ())
        if not fps or fps[-1][1] != obs.fingerprint:
            fps.append((round(time.monotonic() - t0, 2), obs.fingerprint, len(obs.elements)))
        cur = await page.evaluate(_COUNT_JS)
        if prev is not None and cur["els"] == prev["els"] and cur["els"] > 0 and settled_at is None:
            settled_at = round(time.monotonic() - t0, 2)
        prev = cur
        await page.wait_for_timeout(100)
    return {"url": url, "at_domcontentloaded": at_dcl, "settled": prev or at_dcl,
            "settled_at_s": settled_at, "distinct_fingerprints": len(fps),
            "fingerprints": fps,
            "complete_at_dcl": at_dcl["els"] > 0 and at_dcl["els"] >= (prev or at_dcl)["els"]}


async def contrast(names: list) -> list:
    rows: list = []
    from playwright.async_api import async_playwright
    work = Path("D:/ultracua-data/readiness")
    work.mkdir(parents=True, exist_ok=True)
    for name in names:
        sub = SUBSTRATES[name]()
        sub.await_ready()
        cfg = LOGIN[name]
        os.environ[USER_ENV], os.environ[PASS_ENV] = cfg["user"], cfg["password"]
        entries = list(corpus.for_substrate(name))
        storage = str(work / f"auth-{name}.json")
        await refresh_auth(spec_for(entries[0], sub.url, storage), headless=True)
        async with async_playwright() as p:
            br = await p.chromium.launch(headless=True)
            for e in entries:
                url = spec_for(e, sub.url, storage).start_url
                # A FRESH CONTEXT PER SCENARIO. Reusing one page conflates the measurement: an SPA
                # hash navigation is SAME-DOCUMENT, so `domcontentloaded` fires against the PREVIOUS
                # view's DOM and the row reads "complete" while showing the wrong page. Measured --
                # it made odoo-open-record report 255 elements settling DOWN to 205.
                ctx = await br.new_context(storage_state=storage,
                                           viewport={"width": 1280, "height": 720})
                page = await ctx.new_page()
                try:
                    r = await _trace(page, url)
                except Exception as exc:                               # noqa: BLE001
                    r = {"url": url, "error": f"{type(exc).__name__}: {exc}"[:140]}
                r["label"] = f"{name}/{e.scenario.name}"
                rows.append(r)
                await ctx.close()
            await br.close()
    return rows


def _print_contrast(rows: list) -> None:
    print("=" * 98)
    print(f"{'scenario':32} {'@DCL els':>8} {'@DCL ch':>8} {'settled':>8} {'settled@':>9} "
          f"{'fps':>4}  complete@DCL")
    for r in rows:
        if r.get("error"):
            print(f"{r['label']:32} ERROR {r['error']}")
            continue
        d, s = r["at_domcontentloaded"], r["settled"]
        print(f"{r['label']:32} {d['els']:>8} {d['chars']:>8} {s['els']:>8} "
              f"{str(r['settled_at_s']):>9} {r['distinct_fingerprints']:>4}  "
              f"{'YES' if r['complete_at_dcl'] else 'NO'}")
    ok = [r for r in rows if not r.get("error")]
    done = [r for r in ok if r["complete_at_dcl"]]
    racy = [r for r in ok if r["distinct_fingerprints"] > 1]
    print(f"\n  complete at domcontentloaded:            {len(done)}/{len(ok)}")
    print(f"  whole-page fingerprint MOVES after DCL:  {len(racy)}/{len(ok)}")
    print("=" * 98)


def _print_remedy(res: dict) -> None:
    print("=" * 98)
    print("  THE NAIVE READINESS REMEDY, on a page where today REFUSES for safety")
    for arm in ("today", "polled"):
        a = res[arm]
        w = f"  waited={a['waited_ms']}ms" if "waited_ms" in a else ""
        print(f"    {arm.upper():7} bound={a['bound']!s:5} "
              f"bound_by={a['sink'].get('bound_by')!r:14} href={a['href']!r}{w}")
    print(f"\n    recorded row was {res['recorded_href']!r}")
    if res["wrong_record_bind"]:
        print("    *** the retry turned a LOUD refusal into a bind on a DIFFERENT record ***")
        print("        via a Tier-1 confident candidate, so nothing cross-checks it.")
        print("        `resolve() -> None` is an overloaded sensor: 1 of its 5 meanings is 'not")
        print("        rendered' and 4 are deliberate safety refusals. See D5, R3.7.")
    else:
        print("    the polled arm did NOT produce a wrong-record bind")
    print("=" * 98)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--contrast", action="store_true", help="readiness census (needs substrates)")
    ap.add_argument("--remedy", action="store_true", help="refute the naive fix (needs nothing)")
    ap.add_argument("--substrates", default="gitea,odoo")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if not (a.contrast or a.remedy):
        ap.error("pick at least one of --contrast / --remedy")

    out: dict = {}
    if a.contrast:
        rows = asyncio.run(contrast(a.substrates.split(",")))
        _print_contrast(rows)
        out["contrast"] = rows
    if a.remedy:
        res = asyncio.run(remedy())
        _print_remedy(res)
        out["remedy"] = res
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
