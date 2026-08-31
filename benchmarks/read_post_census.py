"""What POSTs does a JSON-RPC app actually make, and what would a body classifier do with them?

    uv run --no-sync python -m benchmarks.read_post_census --reads     # needs substrates, $0
    uv run --no-sync python -m benchmarks.read_post_census --write     # needs odoo, $0, MUTATES

D7's lead candidate (`docs/reads-over-post.md` B) demotes a POST to read only when the route matches
`call_kw`, the envelope parses as JSON-RPC `method:"call"`, and `params.method` is on a COMMITTED
read allowlist -- everything else stays a write. Its FIRST attached condition is "measure the demoted
population offline first, over the existing capture artifacts", and its second is "trim the allowlist
to methods actually observed": five drafted entries were never live-probed, and `onchange` in
particular is a documented write-in-a-read-shaped-call hazard.

This is that measurement. It costs nothing -- no LLM, no learn -- and it answers three questions the
survey leaves open:

  * measurement #3: what fraction of Odoo's POSTs would the rule demote, with and without the
    route-exact half?
  * measurement #4: the NEGATIVE CONTROL. A real create must produce POSTs the rule refuses to
    demote, and Gitea must be untouched entirely.
  * condition 2: which methods are actually live, so the allowlist can be the observed set rather
    than a drafted one.

NOTHING HERE CHANGES `src/`. The rule is implemented locally, as a proposal under measurement, so
that a bad answer costs a file nobody imports rather than a revert on the write rail.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import corpus                                          # noqa: E402
from benchmarks import substrates as S                                 # noqa: E402
from benchmarks.scored_run import LOGIN, PASS_ENV, USER_ENV, spec_for  # noqa: E402
from ultracua.flows import refresh_auth                                # noqa: E402
from ultracua.config import settings                                   # noqa: E402
from ultracua.safety import body_says_read, is_write_request           # noqa: E402

WORK = Path("D:/ultracua-data/postcensus")

# The DRAFTED read allowlist from the survey. Deliberately kept as a draft here: condition 2 says to
# trim it to what is observed, and this probe is how that set gets decided. `onchange` is EXCLUDED on
# purpose -- it is the documented write-in-a-read-shaped-call hazard, and a census that quietly
# included it would be assuming the answer to the question it exists to ask.
DRAFT_READ_METHODS = frozenset({
    "search_read", "read", "search", "search_count", "read_group", "name_search", "fields_get",
    "web_search_read", "web_read", "web_read_group", "load_views", "get_views",
})

# The four named page-load read routes -- the route-EXACT half. Exact, never prefix: a prefix match
# is how a denylist becomes a hole.
DRAFT_READ_ROUTES = frozenset({
    "/web/webclient/load_menus", "/web/dataset/call_kw", "/web/action/load", "/web/webclient/translations",
})


def classify(url: str, body: str | None) -> dict:
    """The candidate rule, failing CLOSED. Returns the verdict plus WHY, so a census can show its work."""
    path = url.split("?", 1)[0].split("#", 1)[0]
    for scheme in ("http://", "https://"):
        if path.startswith(scheme):
            path = "/" + path.split("/", 3)[3] if path.count("/") >= 3 else "/"
    out = {"path": path, "rpc_method": None, "demote": False, "why": ""}

    if not body:
        out["why"] = "no body -> stays a write"
        return out
    try:
        env = json.loads(body)
    except Exception:                                                  # noqa: BLE001
        out["why"] = "unparseable body -> stays a write"
        return out
    if isinstance(env, list):
        out["why"] = "batch array -> stays a write"
        return out
    if not isinstance(env, dict) or env.get("method") != "call":
        out["why"] = f"envelope method {env.get('method') if isinstance(env, dict) else '?'!r} != 'call'"
        return out

    params = env.get("params") or {}
    m = params.get("method")
    out["rpc_method"] = m

    # ODOO'S ROUTE IS `/web/dataset/call_kw/<model>/<method>`, NOT something ending in `/call_kw`.
    # A first draft of this rule used `endswith("/call_kw")` and matched NOTHING -- 18 of 22 observed
    # POSTs fell through as "neither", which is the census earning its keep before a line of `src/`
    # was touched. It also answers the survey's measurement #5: the ORM method is carried in the URL
    # PATH, so suffix and body are two independent readings of the same fact.
    CALL_KW = "/web/dataset/call_kw"
    if path.startswith(CALL_KW + "/"):
        tail = path[len(CALL_KW) + 1:].split("/")
        out["url_method"] = tail[1] if len(tail) > 1 else None
        # BOTH READINGS MUST AGREE when both are present. A disagreement is not a puzzle to resolve,
        # it is a request whose operation is ambiguous -- and an ambiguous operation stays a write.
        if m is not None and out["url_method"] is not None and m != out["url_method"]:
            out["why"] = f"suffix {out['url_method']!r} != body {m!r} -> stays a write"
            return out
        eff = m or out["url_method"]
        if eff is None:
            out["why"] = "call_kw with no method in body or suffix -> stays a write"
            return out
        if eff in DRAFT_READ_METHODS:
            out["demote"], out["why"] = True, f"call_kw + read method {eff!r}"
            return out
        out["why"] = f"call_kw + method {eff!r} NOT on the allowlist -> stays a write"
        return out

    if path == CALL_KW:
        # The BARE form, no suffix: the body is the only reading, so there is nothing to cross-check.
        if m in DRAFT_READ_METHODS:
            out["demote"], out["why"] = True, f"bare call_kw + read method {m!r}"
            return out
        out["why"] = f"bare call_kw + method {m!r} not on the allowlist -> stays a write"
        return out

    if path in DRAFT_READ_ROUTES:
        out["demote"], out["why"] = True, "route-exact read route"
        return out
    out["why"] = "route is neither call_kw nor a named read route"
    return out


async def _drive(name: str, urls: list, interact=None) -> list:
    """Open each url with a real session and record every POST it makes."""
    sub = {"gitea": S.Gitea, "odoo": S.Odoo}[name]()
    sub.await_ready()
    WORK.mkdir(parents=True, exist_ok=True)
    cfg = LOGIN[name]
    os.environ[USER_ENV], os.environ[PASS_ENV] = cfg["user"], cfg["password"]
    storage = str(WORK / f"auth-{name}.json")
    entries = list(corpus.for_substrate(name))
    await refresh_auth(spec_for(entries[0], sub.url, storage), headless=True)

    seen: list = []
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        br = await p.chromium.launch(headless=True)
        ctx = await br.new_context(storage_state=storage, viewport={"width": 1280, "height": 720})
        page = await ctx.new_page()

        def on_request(req):
            try:
                if req.method.upper() != "POST":
                    return
                seen.append({"substrate": name, "url": req.url, "method": req.method,
                             "body": req.post_data, "today_is_write": is_write_request(req.method, req.url)})
            except Exception:                                          # noqa: BLE001
                pass

        page.on("request", on_request)
        for u in urls:
            await page.goto(u, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
        if interact is not None:
            await interact(page)
        await br.close()
    return seen


def _report(rows: list, label: str) -> dict:
    print("=" * 96)
    print(f"  {label}: {len(rows)} POST(s) observed")
    if not rows:
        print("  (none -- a substrate that makes no POSTs is untouched by any body classifier)")
        print("=" * 96)
        return {"n": 0, "demoted": 0}

    methods: collections.Counter = collections.Counter()
    paths: collections.Counter = collections.Counter()
    demoted = 0
    reasons: collections.Counter = collections.Counter()
    for r in rows:
        v = classify(r["url"], r["body"])
        r["verdict"] = v
        methods[v["rpc_method"]] += 1
        paths[v["path"]] += 1
        reasons[v["why"]] += 1
        demoted += bool(v["demote"])

    print(f"\n  today `is_write_request` says WRITE for: "
          f"{sum(1 for r in rows if r['today_is_write'])}/{len(rows)}")
    print(f"  the candidate rule would DEMOTE:          {demoted}/{len(rows)} "
          f"({demoted / len(rows):.0%})")
    print(f"\n  {'ORM method':28} count")
    for m, c in methods.most_common():
        mark = "  <- on the draft allowlist" if m in DRAFT_READ_METHODS else ""
        print(f"  {str(m):28} {c:>5}{mark}")
    print(f"\n  {'route':44} count")
    for pth, c in paths.most_common(12):
        print(f"  {pth[:44]:44} {c:>5}")
    print(f"\n  verdict reasons:")
    for why, c in reasons.most_common():
        print(f"    {c:>4}  {why}")
    print("=" * 96)
    return {"n": len(rows), "demoted": demoted, "methods": dict(methods)}


async def reads() -> dict:
    out = {}
    for name in ("odoo", "gitea"):
        sub = {"gitea": S.Gitea, "odoo": S.Odoo}[name]()
        sub.await_ready()
        storage = str(WORK / f"auth-{name}.json")
        urls = [spec_for(e, sub.url, storage).start_url for e in corpus.for_substrate(name)]
        rows = await _drive(name, urls)
        out[name] = _report(rows, f"{name.upper()} read-only page loads")
        (WORK / f"{name}-reads.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return out


async def write() -> dict:
    """THE NEGATIVE CONTROL. A real create must produce POSTs the rule REFUSES to demote."""
    sub = S.Odoo()
    sub.reset()
    sub.await_ready()
    storage = str(WORK / "auth-odoo.json")
    entry = next(e for e in corpus.for_substrate("odoo") if e.scenario.name == "odoo-create-lead")
    url = spec_for(entry, sub.url, storage).start_url

    async def make_a_lead(page):
        """Issue REAL write requests from the page's own session.

        A first version drove the UI -- New, type, Ctrl+S -- and captured NO write at all, which the
        control correctly reported as proving nothing rather than as a pass. Driving Odoo's inline
        create reliably is its own project, and it is not what measurement #4 asks: the question is
        whether the classifier REFUSES a write request, so the honest control is to put real write
        requests in front of it. These go over the page's own origin and session, so the body and
        route are exactly what the wire watcher would see.
        """
        js = """async (calls) => {
          const out = [];
          for (const c of calls) {
            try {
              const r = await fetch('/web/dataset/call_kw/' + c.model + '/' + c.method, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: {
                  model: c.model, method: c.method, args: c.args, kwargs: c.kwargs || {}}}),
              });
              out.push(c.method + ':' + r.status);
            } catch (e) { out.push(c.method + ':ERR'); }
          }
          return out;
        }"""
        calls = [
            {"model": "crm.lead", "method": "create", "args": [{"name": "census probe lead"}]},
            {"model": "crm.lead", "method": "web_save",
             "args": [[], {"name": "census probe lead 2"}], "kwargs": {"specification": {}}},
        ]
        try:
            res = await page.evaluate(js, calls)
            print(f"  drove real writes: {res}")
        except Exception as exc:                                       # noqa: BLE001
            print(f"  !! could not drive the writes: {type(exc).__name__}: {str(exc)[:110]}")
        await page.wait_for_timeout(1500)

    rows = await _drive("odoo", [url], interact=make_a_lead)
    res = _report(rows, "ODOO create-lead (NEGATIVE CONTROL)")
    (WORK / "odoo-write.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")

    writes = [r for r in rows if r["verdict"]["rpc_method"] in ("create", "web_save", "write", "unlink")]
    print(f"\n  write-shaped ORM calls seen: {len(writes)}")
    bad = [r for r in writes if r["verdict"]["demote"]]
    if writes and not bad:
        print("  *** NEGATIVE CONTROL HOLDS: every write-shaped call stays a WRITE. ***")
    elif not writes:
        print("  !! NO write-shaped call was captured -- the control did not exercise its subject,")
        print("     so it proves nothing. Fix the driving before believing the read numbers.")
    else:
        print(f"  *** CONTROL FAILED: {len(bad)} write-shaped call(s) would be DEMOTED ***")
        for r in bad:
            print(f"      {r['verdict']['rpc_method']}  {r['verdict']['why']}")
    res["writes_seen"] = len(writes)
    res["writes_demoted"] = len(bad)
    sub.reset()          # leave the substrate as it was found
    return res


async def step() -> dict:
    """THE NUMBER THAT ACTUALLY DECIDES D7: would a read STEP stop being wire-marked?

    Per-POST clearance is not the question. `flow._author_steps` marks a step when a write-classified
    request fires inside its ACT WINDOW, so a step is only demoted when EVERY post in that window
    demotes -- one un-cleared background poll re-marks it. Odoo's mail-bus routes carry no ORM method
    and a body classifier structurally cannot clear them, so the whole fix turns on whether they land
    in act windows or only at page load.

    So: load, let the page settle, START RECORDING, then perform a real interaction and look only at
    what that interaction caused.
    """
    sub = S.Odoo()
    sub.await_ready()
    WORK.mkdir(parents=True, exist_ok=True)
    cfg = LOGIN["odoo"]
    os.environ[USER_ENV], os.environ[PASS_ENV] = cfg["user"], cfg["password"]
    storage = str(WORK / "auth-odoo.json")
    entry = next(e for e in corpus.for_substrate("odoo") if e.scenario.name == "odoo-sort-list")
    url = spec_for(entry, sub.url, storage).start_url

    windows: list = []
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        br = await p.chromium.launch(headless=True)
        ctx = await br.new_context(storage_state=storage, viewport={"width": 1280, "height": 720})
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(6000)      # let the load-time chrome finish

        recording: list = []
        page.on("request", lambda r: recording.append(
            {"url": r.url, "method": r.method, "body": r.post_data})
            if r.method.upper() == "POST" else None)

        for i, label in enumerate(("sort click 1", "sort click 2")):
            recording.clear()
            try:
                await page.get_by_text("Expected Revenue", exact=True).first.click(timeout=8000)
            except Exception as exc:                                   # noqa: BLE001
                print(f"  !! {label}: {type(exc).__name__}: {str(exc)[:90]}")
                break
            await page.wait_for_timeout(2500)          # the act window, generously
            windows.append({"label": label, "posts": list(recording)})
        await br.close()

    print("=" * 96)
    print("  PER-STEP: only the POSTs an ACTION caused (page-load chrome excluded)")
    all_clear = True
    for w in windows:
        verdicts = [(r, classify(r["url"], r["body"])) for r in w["posts"]]
        blockers = [(r, v) for r, v in verdicts if not v["demote"]]
        clear = bool(verdicts) and not blockers
        all_clear &= clear
        print(f"\n  {w['label']}: {len(verdicts)} POST(s) in the window")
        for r, v in verdicts:
            flag = "DEMOTE" if v["demote"] else "WRITE "
            print(f"     [{flag}] {str(v['rpc_method']):26} {v['path'][:52]}")
        print(f"     -> the step would be {'DEMOTED' if clear else 'STILL MARKED AS A WRITE'}")
        if blockers:
            for _, v in blockers:
                print(f"        blocked by: {v['why']}")
    print("\n" + "=" * 96)
    if windows and all_clear:
        print("  *** EVERY measured read step clears: the mail-bus routes do NOT land in act windows.")
        print("      Per-POST clearance understated the fix -- the number that matters is 100%.")
    elif windows:
        print("  NOT every read step clears. A body classifier alone does not un-mark these steps,")
        print("      and the blocking routes above are what a v1 would have to account for.")
    (WORK / "odoo-steps.json").write_text(json.dumps(windows, indent=1), encoding="utf-8")
    return {"windows": len(windows), "all_clear": all_clear}


def would_demote(posts: "list") -> dict:
    """THE DEMOTION QUESTION, as `flow._author_steps` asks it: does EVERY write-classified POST in
    this window clear? One un-cleared post re-marks the whole step.

    Pure, so the arithmetic can be tested with no browser and no substrate. `body_says_read` is the
    REAL one -- the question is what SHIPPED does, not what a local draft would do.
    """
    writes = [r for r in posts if is_write_request(r["method"], r["url"])]
    cleared = [r for r in writes if body_says_read(r["url"], r.get("body"))]
    stuck = [r for r in writes if not body_says_read(r["url"], r.get("body"))]
    return {"posts": len(posts), "writes": len(writes), "cleared": len(cleared),
            "stuck": [r["url"] for r in stuck],
            # NO POSTS AT ALL IS NOT A DEMOTION. Such a step was never marked, so reporting it as
            # demoted would inflate the rate with steps the fix never touched -- and on a
            # server-rendered substrate that is every step.
            "demoted": bool(writes) and not stuck}


async def navstep() -> dict:
    """THE OTHER HALF OF `--step`, AND IT ANSWERS THE OPPOSITE WAY.

    `step()` above measures CLICK act windows, and does so after a deliberate 6 s wait so the
    load-time chrome is finished before recording starts. That is the right shape for its question
    and it reported 2/2 demoted. But a `navigate` step's act window IS the page load, so the very
    traffic that wait excludes is the traffic that decides it -- and this function's own sibling
    docstring names the risk: "the whole fix turns on whether they land in act windows".

    R4.117 measured Odoo's blocking step as step 0, a NAVIGATE, refused by the gate. So this is the
    window that matters and it was never measured. Costs nothing: no LLM, no learn.
    """
    sub = S.Odoo()
    sub.await_ready()
    WORK.mkdir(parents=True, exist_ok=True)
    cfg = LOGIN["odoo"]
    os.environ[USER_ENV], os.environ[PASS_ENV] = cfg["user"], cfg["password"]
    storage = str(WORK / "auth-odoo.json")
    entries = list(corpus.for_substrate("odoo"))
    await refresh_auth(spec_for(entries[0], sub.url, storage), headless=True)

    from playwright.async_api import async_playwright
    rows: list = []
    print()
    print("=" * 96)
    print(f"  NAVIGATE act windows (grace = write_window_ms = {settings.write_window_ms} ms)")
    print()
    async with async_playwright() as p:
        br = await p.chromium.launch(headless=True)
        for entry in entries:
            name = entry.scenario.name
            url = spec_for(entry, sub.url, storage).start_url
            ctx = await br.new_context(storage_state=storage,
                                       viewport={"width": 1280, "height": 720})
            page = await ctx.new_page()
            rec: list = []
            page.on("request", lambda r: rec.append(
                {"url": r.url, "method": r.method, "body": r.post_data})
                if r.method.upper() == "POST" else None)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:                                   # noqa: BLE001
                print(f"  {name:24} goto failed: {type(exc).__name__}: {exc}")
                await ctx.close()
                continue
            # THE ACT WINDOW, and nothing is excluded from it -- that exclusion is the whole point.
            await page.wait_for_timeout(settings.write_window_ms)
            v = would_demote(rec)
            v["scenario"] = name
            rows.append(v)
            print(f"  {name:24} writes={v['writes']:3} cleared={v['cleared']:3} "
                  f"stuck={len(v['stuck']):3}  demoted={v['demoted']}")
            for u in v["stuck"][:4]:
                print(f"  {'':24}   stuck: {u.split(sub.url)[-1][:62]}")
            await ctx.close()
        await br.close()

    dem = [r for r in rows if r["demoted"]]
    print()
    print("=" * 96)
    print(f"  {len(dem)}/{len(rows)} NAVIGATE steps would be demoted "
          f"(`--step` measured 2/2 for CLICK steps).")
    if rows and not dem:
        print("  *** D7 DEMOTES NO NAVIGATE STEP. The blocking step R4.117 measured is a navigate,")
        print("      so the fix does not reach it. Latent only while learns cache no navigate step.")
    (WORK / "odoo-navsteps.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return {"rows": len(rows), "demoted": len(dem)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reads", action="store_true", help="census read-only page loads (both substrates)")
    ap.add_argument("--write", action="store_true", help="the negative control (MUTATES odoo, then resets)")
    ap.add_argument("--step", action="store_true", help="per-STEP clearance: only what an action caused")
    ap.add_argument("--navstep", action="store_true",
                    help="per-NAVIGATE-step clearance: the page-load window `--step` excludes")
    a = ap.parse_args(argv)
    if not (a.reads or a.write or a.step or a.navstep):
        ap.error("pick --reads, --write, --step and/or --navstep")
    if a.reads:
        asyncio.run(reads())
    if a.write:
        asyncio.run(write())
    if a.step:
        asyncio.run(step())
    if a.navstep:
        asyncio.run(navstep())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
