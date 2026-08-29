"""Does the replay read page state BEFORE a client-rendered app has painted it? (R4.115.)

    uv run --no-sync python -m benchmarks.readiness_probe --contrast   # needs substrates
    uv run --no-sync python -m benchmarks.readiness_probe --remedy     # needs nothing
    uv run --no-sync python -m benchmarks.readiness_probe --recipes DIR  # needs nothing

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

`--recipes` is the third mode and needs no substrate, no browser and no key: it reads cached recipes
off disk and reports their SHAPE. It exists because of R4.118 -- on Odoo the learn thrashes on
navigation and caches recipes that are 100% `navigate` steps (measured: `odoo-filter-status` 20 of
20, `odoo-open-record` 8 of 8), several of them carrying MALFORMED urls with an empty `action=`.
Such a recipe replays every step "ok" and lands on the wrong app, so nothing downstream can fail for
it. A degenerate recipe is detectable from the ARTIFACT ALONE, which is the cheapest place to catch
it, and this is where that detection is written down.
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


# --------------------------------------------------- the readiness CEILING (an instrument, not a fix)

EVENTS: list = []
_REAL: dict = {}


def _unpatch() -> None:
    if not _REAL:
        return
    from ultracua import flow as flow_mod
    from ultracua.browser import BrowserSession
    L.resolve = _REAL["resolve"]
    flow_mod.resolve = _REAL["resolve"]
    flow_mod.scope_fingerprint = _REAL["fp"]
    BrowserSession.snapshot = _REAL["snap"]


def _patch_readiness() -> None:
    """Wait, at all THREE sites, before believing what the page says.

    A CEILING, NOT A PROPOSED FIX. `--remedy` above measures why: a retry keyed on
    `resolve() -> None` re-drives four deliberate safety refusals and was caught binding a wrong
    record. This exists to answer "what would be reachable if readiness were solved", and a real fix
    needs a sensor that separates 'not rendered' from 'found it and refused' (D5).

    The predicate is 'the first value that REPEATS', which is what two independent reproductions were
    obtained with. A rewrite of it -- `networkidle` plus a fixed settle -- looked strictly better and
    FAILED where this passes, on two different recipes: `networkidle` NEVER FIRES on Odoo, whose
    message bus holds a long-poll open, so every call paid a 6 s timeout for nothing."""
    from ultracua import flow as flow_mod
    from ultracua.browser import BrowserSession

    if not _REAL:
        _REAL.update(resolve=L.resolve, fp=flow_mod.scope_fingerprint,
                     snap=BrowserSession.snapshot)
    real_resolve, real_fp, real_snap = _REAL["resolve"], _REAL["fp"], _REAL["snap"]

    async def resolve_ready(page, spec, unique=False, sink=None):
        s = {} if sink is None else sink
        got = await real_resolve(page, spec, unique=unique, sink=s)
        w = 0
        while got is None and w < 5000:
            await page.wait_for_timeout(100)
            w += 100
            got = await real_resolve(page, spec, unique=unique, sink=s)
        if w:
            EVENTS.append(f"resolve waited {w}ms -> bound={got is not None}")
        if sink is not None:
            sink.update(s)
        return got

    async def fp_stable(target):
        prev = await real_fp(target)
        for i in range(50):
            try:
                await target.page.wait_for_timeout(100)
            except Exception:                                          # noqa: BLE001
                return prev
            cur = await real_fp(target)
            if cur == prev:
                if i:
                    EVENTS.append(f"scope fingerprint settled after {i * 100}ms")
                return cur
            prev = cur
        return prev

    async def snap_stable(self):
        obs = await real_snap(self)
        prev = len(getattr(obs, "elements", []) or [])
        for i in range(50):
            try:
                await self.page.wait_for_timeout(100)
            except Exception:                                          # noqa: BLE001
                return obs
            nxt = await real_snap(self)
            n = len(getattr(nxt, "elements", []) or [])
            if n == prev and n > 0:
                if i:
                    EVENTS.append(f"snapshot settled after {i * 100}ms -> {n} elements")
                return nxt
            prev, obs = n, nxt
        return obs

    L.resolve = resolve_ready
    flow_mod.resolve = resolve_ready
    flow_mod.scope_fingerprint = fp_stable
    BrowserSession.snapshot = snap_stable


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


# ------------------------------------------------------------------------------- the COMPOSITION

async def _replay_arm(entry, recipe: dict, *, storage: str, root: Path, ready: bool) -> dict:
    """One replay of `recipe`, with the readiness ceiling on or off. Resets first, so neither arm
    inherits the other's substrate state."""
    import time

    from benchmarks.mark_flip_probe import install
    from ultracua.flows import FlowCache, RunRecord, replay

    _unpatch()
    sub = SUBSTRATES[entry.scenario.substrate]()
    sub.reset()
    sub.await_ready()
    spec = spec_for(entry, sub.url, storage)     # direct at the substrate: no proxy, no port churn
    install(recipe, spec, root)
    EVENTS.clear()
    if ready:
        _patch_readiness()
    rec, t0 = RunRecord(), time.monotonic()
    try:
        got = await replay(spec, cache=FlowCache(root=root), on_drift="raise", record=rec)
        out = {"ok": True, "error": None, "result": str(got)[:160]}
    except BaseException as exc:                                       # noqa: BLE001
        out = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:170]}", "result": None}
    finally:
        _unpatch()
    steps = [{"i": getattr(t, "index", None), **{k: v for k, v in (dict(getattr(t, "meta", {}) or {})).items()
                                                 if k in ("action", "ok", "bound_by", "gate_bound_by",
                                                          "gate", "note")}}
             for t in (getattr(rec, "traces", None) or []) if getattr(t, "meta", None)]
    out.update(steps=steps, events=list(EVENTS), wall_s=round(time.monotonic() - t0, 1),
               # EVERY step bound and executed. Deliberately NOT the oracle: R4.116 -- the extractor
               # is handed the whole page and answers correctly over a wrongly-sorted list.
               every_step_ok=bool(out["ok"] and steps and all(s.get("ok") for s in steps)))
    return out


async def compose(scenario: str, recipe_path: Path, workdir: Path) -> dict:
    """THE 2x2 NOBODY HAD RUN: {marks kept, demoted} x {readiness off, on}.

    Every earlier experiment moved ONE lever. R4.114 demoted the wire marks with no readiness and the
    locator race killed the first click; the R4.115 blast run added readiness with the marks kept and
    the whole-page gate killed the first NAVIGATE, because a wire-marked navigate is gated on a
    fingerprint the render race makes unreproducible. Those are the two off-diagonal cells, and each
    fails for its own finding's reason. This runs all four.

    MEASURED 0.143.0 on a freshly learned `odoo-sort-list`: only the composed cell completes."""
    from benchmarks.mark_flip_probe import demote

    entry = next((e for s in corpus.CORPORA for e in corpus.for_substrate(s)
                  if e.scenario.name == scenario), None)
    if entry is None:
        raise SystemExit(f"no scenario named {scenario!r}")
    recipe = json.loads(Path(recipe_path).read_text(encoding="utf-8"))
    demoted, flipped = demote(recipe)
    if not flipped:
        raise SystemExit(
            f"{Path(recipe_path).name} has no wire-only marks to flip, so the demoted arms are the "
            f"same recipe as the kept arms and the 2x2 collapses to a 1x2 measuring nothing.")

    workdir.mkdir(parents=True, exist_ok=True)
    cfg = LOGIN[entry.scenario.substrate]
    os.environ[USER_ENV], os.environ[PASS_ENV] = cfg["user"], cfg["password"]
    sub = SUBSTRATES[entry.scenario.substrate]()
    sub.await_ready()
    storage = str(workdir / f"auth-{scenario}.json")
    await refresh_auth(spec_for(entry, sub.url, storage), headless=True)

    cells = {}
    for marks, r in (("kept", recipe), ("demoted", demoted)):
        for ready in (False, True):
            key = f"{marks}+{'ready' if ready else 'today'}"
            cells[key] = await _replay_arm(entry, r, storage=storage,
                                           root=workdir / key.replace("+", "-"), ready=ready)
    return {"scenario": scenario, "recipe": str(recipe_path), "flipped_steps": flipped,
            "cells": cells}


def _print_compose(res: dict) -> None:
    print("=" * 98)
    print(f"  {res['scenario']}   wire marks demoted on steps {res['flipped_steps']}")
    print(f"\n  {'':10} {'readiness OFF':>16} {'readiness ON':>16}")
    for marks in ("kept", "demoted"):
        row = [res["cells"][f"{marks}+{k}"]["every_step_ok"] for k in ("today", "ready")]
        print(f"  marks {marks:10} {str(row[0]):>10} {str(row[1]):>16}")
    print()
    for key, c in res["cells"].items():
        bad = [s for s in c["steps"] if not s.get("ok")]
        if c["every_step_ok"]:
            print(f"    {key:18} COMPLETED, every step bound   result={c['result']!r}")
        elif bad:
            b = bad[0]
            print(f"    {key:18} first failure: step {b.get('i')} {b.get('action')} "
                  f"bound_by={b.get('bound_by', b.get('gate_bound_by'))}"
                  f"{'  gate=' + b['gate'] if b.get('gate') else ''} :: {(b.get('note') or '')[:52]}")
        else:
            print(f"    {key:18} {(c['error'] or '')[:88]}")
    won = [k for k, c in res["cells"].items() if c["every_step_ok"]]
    print(f"\n  cells that completed: {won or 'none'}")
    if won == ["demoted+ready"]:
        print("    -> NEITHER FIX ALONE IS SUFFICIENT AND THE COMPOSITION IS. The marking fix and the")
        print("       readiness fix each remove one of two independent blockers on the same step set.")
    print("=" * 98)


# --------------------------------------------------------------------------- the recipe-shape census

# An `action=` with nothing after it. Odoo cannot resolve it, so it serves the default app (Discuss)
# and every later step runs against the wrong page while reporting ok. Measured in three of the
# recipes the Odoo learns produced.
_EMPTY_ACTION = "action=&"


def recipe_shape(recipe: dict) -> dict:
    """What KIND of recipe is this? Derived from the artifact -- no substrate, no browser, no key."""
    steps = recipe.get("steps") or []
    kinds: dict = {}
    for s in steps:
        kinds[s.get("action")] = kinds.get(s.get("action"), 0) + 1
    navs = [s for s in steps if s.get("action") == "navigate"]
    malformed = [s.get("text") for s in navs
                 if isinstance(s.get("text"), str)
                 and (_EMPTY_ACTION in s["text"] or s["text"].rstrip().endswith("action="))]
    # DISTINCT navigate targets: a healthy recipe navigates somewhere ONCE. Re-navigating to the same
    # url repeatedly is the signature of an agent that cannot tell whether the page arrived.
    targets = [s.get("text") for s in navs]
    repeats = len(targets) - len(set(targets))
    n = len(steps)
    return {
        "steps": n,
        "kinds": kinds,
        "navigate_fraction": (len(navs) / n) if n else 0.0,
        "repeated_navigations": repeats,
        "malformed_urls": malformed,
        # DEGENERATE: nothing but navigation. Such a recipe cannot perform the task -- it only moves
        # the browser -- yet every step replays "ok", so the failure surfaces as a data/verify error
        # far from its cause, or not at all.
        "degenerate_navigate_only": bool(n and len(navs) == n),
    }


def census_recipes(root: Path) -> list:
    rows = []
    for p in sorted(Path(root).rglob("*.json")):
        if p.name.endswith(".meta.json"):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                              # noqa: BLE001
            continue
        if not isinstance(d, dict) or "steps" not in d:
            continue
        rows.append({"path": str(p), "name": p.parent.parent.name or p.stem,
                     **recipe_shape(d)})
    return rows


def _print_recipes(rows: list) -> None:
    print("=" * 98)
    print(f"{'recipe':26} {'steps':>6} {'nav%':>6} {'re-nav':>7} {'malformed':>10}  kinds")
    for r in rows:
        flag = "  <== DEGENERATE (navigate-only)" if r["degenerate_navigate_only"] else ""
        print(f"{r['name'][:26]:26} {r['steps']:>6} {r['navigate_fraction']:>5.0%} "
              f"{r['repeated_navigations']:>7} {len(r['malformed_urls']):>10}  "
              f"{r['kinds']}{flag}")
    bad = [r for r in rows if r["degenerate_navigate_only"]]
    mal = [r for r in rows if r["malformed_urls"]]
    print(f"\n  navigate-only recipes: {len(bad)}/{len(rows)}  {[r['name'] for r in bad]}")
    print(f"  recipes with a malformed url: {len(mal)}/{len(rows)}  {[r['name'] for r in mal]}")
    if bad:
        print("\n  A navigate-only recipe replays every step ok and performs no task. It is a LEARN")
        print("  failure that caches as a success (R4.118); nothing downstream can fail for it.")
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
    ap.add_argument("--recipes", default=None, metavar="DIR",
                    help="census cached recipe SHAPES under DIR (needs nothing)")
    ap.add_argument("--compose", action="store_true",
                    help="the marks x readiness 2x2 (needs a substrate, --scenario and --recipe)")
    ap.add_argument("--scenario", default=None)
    ap.add_argument("--recipe", default=None, type=Path)
    ap.add_argument("--workdir", type=Path, default=Path("D:/ultracua-data/compose"))
    ap.add_argument("--substrates", default="gitea,odoo")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if not (a.contrast or a.remedy or a.recipes or a.compose):
        ap.error("pick at least one of --contrast / --remedy / --recipes / --compose")
    if a.compose and not (a.scenario and a.recipe):
        ap.error("--compose needs --scenario and --recipe")

    out: dict = {}
    if a.recipes:
        rows = census_recipes(Path(a.recipes))
        _print_recipes(rows)
        out["recipes"] = rows
    if a.contrast:
        rows = asyncio.run(contrast(a.substrates.split(",")))
        _print_contrast(rows)
        out["contrast"] = rows
    if a.remedy:
        res = asyncio.run(remedy())
        _print_remedy(res)
        out["remedy"] = res
    if a.compose:
        res = asyncio.run(compose(a.scenario, a.recipe, a.workdir))
        _print_compose(res)
        out["compose"] = res
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
