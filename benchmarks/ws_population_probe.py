"""What can a learn-path WebSocket watcher SEE? D4's first question, and it is free (0.179.0).

    python -m benchmarks.ws_population_probe --calibrate   # prove the sensor fires (offline, ~3 s)
    python -m benchmarks.ws_population_probe               # both substrates, all 14 corpus pages
    python -m benchmarks.ws_population_probe --only odoo

Correctness-plan D4 asks for "learn-path WebSocket parity with the recorder: a sent WS frame during
learn is a write-suspect; an undeclared one refuses". Before building a REFUSAL, the question that
decided D3 and D2 is the free one: **can the prescription fire at all?**

WHAT THE SENSOR IS, AND WHY THERE IS NO CHOICE OF SCOPE. `recorder.py` uses `page.on("websocket")`.
That is not a scope oversight to be corrected the way the request watchers were at 0.177.0 --
measured from Playwright's own event tables, `WebSocket` exists on `Page` and NOT on
`BrowserContext`, which offers `ServiceWorker` and nothing for shared workers or sockets. So a
websocket watcher has exactly one scope available and D4 has nothing to widen to.

CALIBRATION IS NOT OPTIONAL, AND ITS FIRST DRAFT CAUGHT ITSELF. A probe reporting "zero sockets" is
worth nothing unless the sensor is proven able to fire (R4.134). `--calibrate` drives a local page
that opens one socket from the PAGE and one from a SHARED WORKER:

    page.on('websocket') saw : ['ws://127.0.0.1:.../from-page']
    CDP target types         : ['page', 'shared_worker']
    sensor CAN fire (page realm)     : True
    sensor sees SHARED worker socket : False

The first version pointed both sockets at port 9, which is on Chrome's blocked-port list, so nothing
was created and the page-realm CONTROL read False -- a zero that described the probe. Read the
control before reading the measurement.

SO EVERY ROW REPORTS TWO THINGS: what the watcher saw, and whether a shared-worker target EXISTS on
that page. A page with a shared worker and zero observed sockets is the R4.154 realm, and it is the
difference between "this app has no websockets" and "this app's websockets are somewhere the sensor
cannot look".
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ultracua.browser import BrowserSession                              # noqa: E402

from benchmarks import corpus                                            # noqa: E402
from benchmarks import substrates as S                                   # noqa: E402

# --- the calibration fixture: one socket per realm, on a port Chrome will actually open ------------
_SHARED_JS = "const WS='%s'; new WebSocket(WS + '/from-shared');"
_PAGE_HTML = """<!doctype html><meta charset=utf-8><title>ws realms</title>
<script>
  const WS = '%s';
  new WebSocket(WS + '/from-page');
  new SharedWorker('/shared.js');
</script>"""


def _serve_realms():
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:                                        # noqa: N802
            ws = "ws://127.0.0.1:%d" % self.server.server_address[1]
            p = self.path.split("?")[0]
            body, ctype = ((_SHARED_JS % ws, "text/javascript") if p == "/shared.js"
                           else (_PAGE_HTML % ws, "text/html"))
            b = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, "http://127.0.0.1:%d" % httpd.server_address[1]


async def _shared_worker_targets(browser) -> list:
    """Ground truth for the realm: CDP sees a shared worker even when the page listener cannot."""
    cdp = await browser.new_browser_cdp_session()
    infos = (await cdp.send("Target.getTargets"))["targetInfos"]
    return [t for t in infos if t["type"] == "shared_worker"]


async def calibrate() -> int:
    httpd, base = _serve_realms()
    seen: list = []
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        try:
            page = await (await b.new_context()).new_page()
            page.on("websocket", lambda ws: seen.append(ws.url))
            await page.goto(base + "/", wait_until="domcontentloaded")
            await asyncio.sleep(1.5)
            shared = await _shared_worker_targets(b)
        finally:
            await b.close()
            httpd.shutdown()
            httpd.server_close()

    page_ok = any("from-page" in u for u in seen)
    shared_seen = any("from-shared" in u for u in seen)
    print("  page.on('websocket') saw : %s" % sorted(seen))
    print("  shared_worker targets    : %d" % len(shared))
    print("  sensor CAN fire (page realm)     : %s" % page_ok)
    print("  sensor sees SHARED worker socket : %s" % shared_seen)
    if not page_ok:
        print("\n  THE SENSOR NEVER FIRED -- any zero measured with it describes the probe.")
        return 1
    if shared_seen or not shared:
        print("\n  the shared-worker arm did not reproduce; re-read before trusting a corpus zero.")
        return 1
    print("\n  calibrated: the watcher fires on its own realm and cannot reach a shared worker's.")
    return 0


async def _auth_state(substrate: str, work: Path) -> str:
    """A logged-in storage_state through the product's own verb (the `watcher_scope_probe` shape)."""
    import os

    from ultracua.flows import refresh_auth

    from .scored_run import LOGIN, PASS_ENV, USER_ENV, spec_for

    sub = {"gitea": S.Gitea, "odoo": S.Odoo}[substrate]()
    sub.await_ready()
    cfg = LOGIN[substrate]
    os.environ[USER_ENV], os.environ[PASS_ENV] = cfg["user"], cfg["password"]
    state = str(work / ("auth-%s.json" % substrate))
    await refresh_auth(spec_for(next(iter(corpus.for_substrate(substrate))), sub.url, state),
                       headless=True)
    return state


async def probe(substrate: str, dwell: float) -> list:
    work = ROOT / ".scratch" / "ws_population"
    work.mkdir(parents=True, exist_ok=True)
    sub = {"gitea": S.Gitea, "odoo": S.Odoo}[substrate]()
    sub.await_ready()
    state = await _auth_state(substrate, work)

    rows: list = []
    async with BrowserSession(headless=True, storage_state=state) as sess:
        page = sess.page
        for entry in corpus.for_substrate(substrate):
            seen: list = []
            on_ws = lambda ws: seen.append(ws.url)                       # noqa: E731
            page.on("websocket", on_ws)
            try:
                # `about:blank` FIRST: Odoo routes on the hash, so a goto between two `#action=` urls
                # is a SAME-DOCUMENT navigation that measures the view it just left (R4.141).
                await page.goto("about:blank")
                await sess.goto("%s%s" % (sub.url, entry.scenario.url_path))
                await sess.await_settled()
                await asyncio.sleep(dwell)
                obs = await sess.snapshot()
                shared = await _shared_worker_targets(page.context.browser)
            finally:
                page.remove_listener("websocket", on_ws)

            rows.append({
                "scenario": entry.scenario.name,
                "elements": len(obs.elements),
                "sockets": len(seen),
                "shared_workers": len(shared),
                "urls": sorted({u.split("?")[0] for u in seen})[:3],
                "worker_urls": sorted({t.get("url", "").split("?")[0] for t in shared})[:2],
            })
    return rows


async def main() -> int:
    ap = argparse.ArgumentParser(prog="benchmarks.ws_population_probe")
    ap.add_argument("--calibrate", action="store_true",
                    help="prove the sensor fires, offline, with no substrate")
    ap.add_argument("--only", choices=["gitea", "odoo"], default=None)
    ap.add_argument("--dwell", type=float, default=6.0)
    args = ap.parse_args()

    if args.calibrate:
        return await calibrate()

    total_sockets = total_shared = 0
    for name in ([args.only] if args.only else ["gitea", "odoo"]):
        print("\n=== %s ===" % name)
        print("%-24s %5s %9s %9s" % ("scenario", "els", "sockets", "sh.workers"))
        for r in await probe(name, args.dwell):
            flag = "" if r["elements"] else "   <-- EMPTY PAGE: this row measures nothing"
            print("%-24s %5d %9d %9d%s"
                  % (r["scenario"], r["elements"], r["sockets"], r["shared_workers"], flag))
            for u in r["urls"]:
                print("%-24s   socket: %s" % ("", u[:70]))
            for u in r["worker_urls"]:
                print("%-24s   worker: %s" % ("", u[:70]))
            total_sockets += r["sockets"]
            total_shared += r["shared_workers"]

    print("\n  `page.on(\"websocket\")` observed %d socket(s) across the corpus;" % total_sockets)
    print("  %d shared-worker target(s) existed on those same pages." % total_shared)
    print()
    if total_sockets == 0:
        print("  D4's watcher has NO POPULATION on this corpus: a frame-based write-suspect rule")
        print("  cannot fire, so it can neither help nor over-refuse here. Run --calibrate before")
        print("  believing that zero.")
    if total_shared:
        print("  And the shared-worker realm is PRESENT, which is R4.154's blast radius arriving as")
        print("  a by-product: those sockets are unreachable from any Playwright event.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
