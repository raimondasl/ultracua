"""Do this project's observation numbers hold outside its own two substrates?

    uv run --no-sync python -m benchmarks.web_survey            # every reachable target
    uv run --no-sync python -m benchmarks.web_survey --local    # only the containers
    uv run --no-sync python -m benchmarks.web_survey --only saucedemo,conduit

THE QUESTION THIS EXISTS TO ANSWER. Between 0.146.0 and 0.153.0 the product gained a readiness
settle, a body-evidence read classifier, element hints and eight ARIA roles -- and EVERY one of them
was found by an Odoo scenario failing. Each was argued to be a CLASS (client-rendered SPAs,
reads-over-POST, unnamed controls) rather than an Odoo quirk, but the corpus holds two substrates
that differ on ONE axis, server-rendered vs SPA, so every such claim was an extrapolation from a
single SPA. The honest position was that we could not tell.

This is free: no LLM, no login, no writes, no form submission -- one page load per target and a
handful of `page.evaluate` reads. It measures the same quantities the findings were argued from, so
the arguments become a table:

  * UNNAMED       -- R4.131. Interactables the agent sees with no accessible name.
  * HINTED        -- how many of those the shipped `hint` sources recover, and from where.
  * ARIA          -- R4.132. Which of the roles that were missing until 0.153.0 the app actually uses.
  * CAP           -- R4.133. Viewport-visible candidates against `settings.max_elements`.
  * FOLD          -- R4.102. Interactables below the fold, invisible to the agent with no signal.
  * SETTLE        -- R4.115/R4.120. Is the page complete at `domcontentloaded`, or does it paint
                     later? The Gitea-vs-Odoo contrast that motivated the settle was 7/7 vs 0/7.

THE TARGETS ARE PART OF THE INSTRUMENT. They are chosen for RENDERING-FAMILY diversity, not for
popularity, and they are public demo or documentation surfaces -- several exist expressly for
browser automation. Nothing here logs in, submits, or writes. A target that moves or dies is
reported as unreachable rather than silently dropped, because a survey that quietly shrinks is a
survey whose numbers drift for reasons nobody can see.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ultracua.browser import BrowserSession                        # noqa: E402
from ultracua.config import settings                               # noqa: E402

OUT = ROOT / "baselines" / "web_survey.json"


@dataclass(frozen=True)
class Target:
    name: str
    url: str
    #: The rendering family, which is the axis the corpus could not vary.
    family: str
    #: Why this target is in the set. A target with no reason is a target nobody can re-justify.
    why: str
    local: bool = False
    #: Substrate whose session this target needs. THE FIRST RUN GOT THIS WRONG IN BOTH LOCAL ROWS
    #: and the tell was that they disagreed with a measurement taken an hour earlier: the corpus has
    #: Odoo at 80 interactables, the survey said 6. Unauthenticated, `/web` redirects to
    #: `/web/login` (a login FORM, 8 candidates) and the Gitea path was a 404. A calibration row
    #: that silently measures an error page makes every comparison against it meaningless.
    auth: str = ""


# EIGHT RENDERING FAMILIES, not eight websites. The corpus had two (Go templates, OWL) and the whole
# limitation was that "SPA" meant "Odoo". Each URL points at REAL APPLICATION UI -- a list, a table,
# a toolbar -- rather than a marketing page, because that is what the corpus scenarios exercise and
# what the numbers must be comparable to.
TARGETS: tuple = (
    # ---- the two the findings were derived from, for calibration ------------------------------
    Target("gitea", "http://localhost:3000/bench/acme/issues?state=all", "server-rendered (Go)",
           "The corpus's server-rendered arm, on the SAME page `gitea-sort-list` uses so the "
           "numbers are comparable to the findings. 0-4% unnamed, complete at domcontentloaded.",
           local=True, auth="gitea"),
    Target("odoo", "http://localhost:8069/web#action=crm.crm_lead_opportunities&view_type=list",
           "SPA (OWL)",
           "The corpus's SPA arm and the source of every finding under test here -- the same page "
           "the 19-22% unnamed rate was measured on.", local=True, auth="odoo"),
    # ---- a third local family: server-rendered PHP, a real e-commerce app ----------------------
    Target("magento", "http://localhost:7770/", "server-rendered (PHP/Knockout)",
           "WebArena's shopping substrate. Server-rendered like Gitea but a far denser commercial "
           "page, so it separates 'server-rendered' from 'small'.", local=True),
    # ---- React ---------------------------------------------------------------------------------
    Target("conduit", "https://demo.realworld.show/", "SPA (React)",
           "The RealWorld reference app -- the canonical 'real' React SPA, built to a spec by many "
           "framework teams, so it is a rendering family rather than one team's habits."),
    Target("saucedemo", "https://www.saucedemo.com/", "SPA (React)",
           "Sauce Labs' own automation demo. Exists expressly for browser automation."),
    Target("mui-table", "https://mui.com/material-ui/react-table/", "SPA (React/MUI)",
           "Material UI's table docs: a dense React component surface with real data grids."),
    Target("ant-table", "https://ant.design/components/table", "SPA (React/Ant)",
           "Ant Design's table docs. A different React design system from MUI, so 'React' is not "
           "represented by one library's conventions."),
    # ---- Vue / Angular -------------------------------------------------------------------------
    Target("vuetify", "https://vuetifyjs.com/en/components/data-tables/basics/", "SPA (Vue/Vuetify)",
           "Vue's most used component library. Vue was entirely absent from the corpus."),
    Target("angular-material", "https://material.angular.dev/components/table/examples",
           "SPA (Angular Material)",
           "Angular was entirely absent from the corpus, and its CDK emits different ARIA than "
           "React's."),
    # ---- plain / legacy server-rendered --------------------------------------------------------
    Target("the-internet", "https://the-internet.herokuapp.com/", "server-rendered (plain HTML)",
           "Selenium's classic playground: hand-written HTML with no framework at all, which is "
           "the floor case for every selector and naming question here."),
    Target("automationexercise", "https://automationexercise.com/", "server-rendered (PHP)",
           "A conventional server-rendered shop built for automation practice."),
    Target("demoqa", "https://demoqa.com/elements", "SPA (React)",
           "An automation practice site whose whole subject is unusual widgets -- a deliberate "
           "worst case for the selector list."),
    # ---- large public content, as the far end of the density axis --------------------------------
    Target("wikipedia", "https://en.wikipedia.org/wiki/Comparison_of_web_frameworks",
           "server-rendered (MediaWiki)",
           "A very dense server-rendered page: the cap question (R4.133) without any SPA involved."),
    Target("mdn", "https://developer.mozilla.org/en-US/docs/Web/API/Element",
           "server-rendered + hydration",
           "Documentation with client-side enhancement -- the middle of the rendering axis."),
)

# Everything the shipped `hint` derivation can reach, asked of the page directly so the survey does
# not depend on the product's own rendering to count its own coverage.
# WHAT THE OBSERVATION CANNOT ANSWER. Everything about names and hints is read off the REAL
# `sess.snapshot()` below -- a survey that reimplements `nameOf` to measure `nameOf` measures its own
# copy, and the first draft did exactly that: it counted Odoo at 36% unnamed where the product's own
# observation says 19%, because it took its 80 in DOM order while `SNAPSHOT_JS` sorts into READING
# order and runs a second pass. This JS is only for facts outside the Observation's contract.
PROBE = r"""
() => {
  const vis = (el) => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) return false;
    return true;
  };
  const SEL = "a[href],button,input,select,textarea,[role=button],[role=link],[role=tab]," +
    "[role=menuitem],[role=checkbox],[role=radio],[role=combobox],[role=switch]," +
    "[role=menuitemcheckbox],[role=menuitemradio],[role=option],[role=treeitem]," +
    "[role=slider],[role=spinbutton],[role=textbox],[role=searchbox]," +
    "[contenteditable=''],[contenteditable='true'],[onclick]";
  const all = [...document.querySelectorAll(SEL)];
  const visible = all.filter(vis);
  const below = all.filter(e => !vis(e) && e.getBoundingClientRect().top > innerHeight);

  // The ARIA roles that were missing from the selector until 0.153.0 (R4.132). Counted over the
  // WHOLE document, not just the viewport: they live inside menus that are closed at rest, so a
  // viewport-only count would report 0 everywhere and say nothing.
  const ADDED = ['menuitemcheckbox','menuitemradio','option','treeitem','slider','spinbutton',
                 'textbox','searchbox'];
  const aria = {};
  for (const r of ADDED) {
    const n = document.querySelectorAll('[role=' + r + ']').length;
    if (n) aria[r] = n;
  }
  return {candidates: all.length, visible: visible.length, below_fold: below.length, aria};
}
"""

COUNT_JS = ("() => document.querySelectorAll('a[href],button,input,select,textarea,[role=button],"
            "[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=radio],[role=combobox],"
            "[role=switch],[role=menuitemcheckbox],[role=menuitemradio],[role=option],"
            "[role=treeitem],[role=slider],[role=spinbutton],[role=textbox],[role=searchbox]').length")


async def survey_one(sess, t: Target) -> dict:
    """One target. Loads, measures readiness, then measures the observation."""
    row: dict = {"name": t.name, "family": t.family, "url": t.url, "local": t.local}
    t0 = time.monotonic()
    await sess.page.goto(t.url, wait_until="domcontentloaded", timeout=45000)
    # READINESS, the R4.115 question: is the page complete at domcontentloaded, or does it paint
    # later? Counted in ELEMENTS rather than by comparing heights, because R4.102 measured that
    # `scrollHeight > innerHeight` is inert on an app that scrolls an inner container.
    at_dcl = await sess.page.evaluate(COUNT_JS)
    verdict = await sess.await_settled()
    settled = await sess.page.evaluate(COUNT_JS)
    row["settle_ms"] = round((time.monotonic() - t0) * 1000)
    row["settle_verdict"] = verdict
    row["elements_at_dcl"] = at_dcl
    row["elements_settled"] = settled
    # SIGNED, AND BOTH DIRECTIONS ARE REAL. `ant-table` measured 912 interactables at
    # domcontentloaded and 407 after settling -- it REMOVES 505, it does not paint them. Calling
    # that "painted late" reported a negative number under a positive name; what matters for R4.115
    # is that the page is not the page yet, in either direction.
    row["painted_late"] = settled - at_dcl
    row["changed_after_dcl"] = abs(settled - at_dcl)
    row.update(await sess.page.evaluate(PROBE))

    # NAMES AND HINTS COME FROM THE PRODUCT'S OWN OBSERVATION, which is the thing under study.
    obs = await sess.snapshot()
    els = list(obs.elements or ())
    unnamed = [e for e in els if not (e.name or "").strip()]
    sources: dict = {"tooltip": 0, "labelled": 0, "icon": 0}
    leftover: dict = {}
    for e in unnamed:
        if e.hint:
            sources[e.hint.split(":", 1)[0]] = sources.get(e.hint.split(":", 1)[0], 0) + 1
        else:
            k = f"{e.tag}[{e.type}]" if e.type else e.tag
            leftover[k] = leftover.get(k, 0) + 1
    row.update({"considered": len(els), "unnamed": len(unnamed),
                "sources": sources, "leftover": leftover})
    return row


#: 1280x720, PINNED. The findings this survey checks were all measured at that size, and the
#: fold/cap questions are both functions of viewport -- a survey at another size answers a different
#: question while looking like the same table.
VIEWPORT = (1280, 720)


async def _auth_state(substrate: str, work: Path) -> str:
    """A logged-in storage_state for a local substrate, via the product's own verb."""
    from benchmarks import corpus
    from benchmarks import substrates as S
    from benchmarks.scored_run import LOGIN, PASS_ENV, USER_ENV, spec_for
    from ultracua.flows import refresh_auth
    import os

    sub = {"gitea": S.Gitea, "odoo": S.Odoo}[substrate]()
    sub.await_ready()
    cfg = LOGIN[substrate]
    os.environ[USER_ENV], os.environ[PASS_ENV] = cfg["user"], cfg["password"]
    state = str(work / f"auth-{substrate}.json")
    await refresh_auth(spec_for(next(iter(corpus.for_substrate(substrate))), sub.url, state),
                       headless=True)
    return state


async def run(targets) -> list:
    """One SESSION PER AUTH CONTEXT. The public targets share an anonymous one; each local substrate
    needs its own, because a storage_state is per-origin and per-app."""
    work = ROOT / ".scratch" / "web_survey"
    work.mkdir(parents=True, exist_ok=True)
    groups: dict = {}
    for t in targets:
        groups.setdefault(t.auth, []).append(t)

    rows: list = []
    for auth, group in groups.items():
        state = None
        if auth:
            try:
                state = await _auth_state(auth, work)
            except Exception as exc:                                   # noqa: BLE001
                for t in group:
                    rows.append({"name": t.name, "family": t.family, "url": t.url,
                                 "local": t.local,
                                 "error": f"auth failed: {type(exc).__name__}: "
                                          f"{str(exc).splitlines()[0][:70]}"})
                    print(f"  UNREACHABLE {t.name}: auth failed", flush=True)
                continue
        async with BrowserSession(headless=True, storage_state=state,
                                  window_size=VIEWPORT) as sess:
            for t in group:
                try:
                    rows.append(await survey_one(sess, t))
                    print(f"  surveyed {t.name}", flush=True)
                except Exception as exc:                               # noqa: BLE001
                    # UNREACHABLE IS A ROW, NOT A GAP. A survey that quietly shrinks is one whose
                    # aggregate drifts for reasons nobody can see.
                    rows.append({"name": t.name, "family": t.family, "url": t.url,
                                 "local": t.local,
                                 "error": f"{type(exc).__name__}: "
                                          f"{str(exc).splitlines()[0][:90]}"})
                    print(f"  UNREACHABLE {t.name}: {type(exc).__name__}", flush=True)
    # Preserve the declared order regardless of how the groups ran, so two runs are diffable.
    order = {t.name: i for i, t in enumerate(targets)}
    return sorted(rows, key=lambda r: order.get(r["name"], 999))


def _print(rows: list) -> None:
    print("\n" + "=" * 118)
    print(f"  {'target':20} {'family':30} {'els':>4} {'d-dcl':>6} {'unnamed':>9} "
          f"{'hinted':>7} {'fold':>5} {'cap':>4}  aria")
    print("  " + "-" * 114)
    for r in rows:
        if "error" in r:
            print(f"  {r['name']:20} {r['family']:30} UNREACHABLE  {r['error'][:44]}")
            continue
        un, con = r["unnamed"], max(1, r["considered"])
        hinted = sum(r["sources"].values())
        cap = "FULL" if r["visible"] > settings.max_elements else "-"
        aria = ",".join(f"{k}:{v}" for k, v in sorted(r["aria"].items())) or "-"
        print(f"  {r['name']:20} {r['family']:30} {r['considered']:>4} "
              f"{r['painted_late']:>+6} {un:>4} ({100*un/con:>3.0f}%) "
              f"{hinted:>7} {r['below_fold']:>5} {cap:>4}  {aria[:34]}")

    live = [r for r in rows if "error" not in r]
    if not live:
        print("\n  every target was unreachable; nothing to aggregate")
        return
    tot_con = sum(r["considered"] for r in live)
    tot_un = sum(r["unnamed"] for r in live)
    tot_hint = sum(sum(r["sources"].values()) for r in live)
    src = {k: sum(r["sources"][k] for r in live) for k in ("tooltip", "labelled", "icon")}
    late = [r for r in live if r["changed_after_dcl"] > 0]
    capped = [r for r in live if r["visible"] > settings.max_elements]
    fold = [r for r in live if r["below_fold"] > 0]
    aria_users = [r for r in live if r["aria"]]
    print("\n" + "=" * 118)
    print(f"  {len(live)}/{len(rows)} targets reachable, {tot_con} interactables considered")
    print(f"  UNNAMED   {tot_un} ({100*tot_un/max(1,tot_con):.0f}%)   "
          f"HINTED {tot_hint} ({100*tot_hint/max(1,tot_un):.0f}% of unnamed)  by source {src}")
    print(f"  ARIA      {len(aria_users)}/{len(live)} targets use a role that was missing "
          f"before 0.153.0")
    print(f"  CAP       {len(capped)}/{len(live)} targets saturate max_elements={settings.max_elements}")
    print(f"  FOLD      {len(fold)}/{len(live)} targets hide interactables below the fold "
          f"(max {max((r['below_fold'] for r in live), default=0)})")
    print(f"  SETTLE    {len(late)}/{len(live)} targets CHANGE their interactable set after "
          f"domcontentloaded (max delta {max((r['changed_after_dcl'] for r in live), default=0)})")
    print("=" * 118)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--local", action="store_true", help="only the local containers")
    ap.add_argument("--only", default="", help="comma-separated target names")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    picked = [t for t in TARGETS
              if (not a.local or t.local)
              and (not a.only or t.name in {s.strip() for s in a.only.split(",")})]
    if not picked:
        ap.error("no targets selected")
    rows = asyncio.run(run(picked))
    _print(rows)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"max_elements": settings.max_elements, "rows": rows},
                                      indent=1), encoding="utf-8")
    print(f"\n  wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
