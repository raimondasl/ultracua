"""Drift-sandbox benchmark — quantify how well a learned flow's resilient locator survives a
DISTRIBUTION of realistic DOM drifts at 0-LLM, and prove no drift ever silently binds the WRONG element.

    uv run python -m benchmarks.drift_sandbox                       # key-less (0-LLM resilience)
    uv run python -m benchmarks.drift_sandbox --provider anthropic  # + heal recovery (real LLM)
    uv run python -m benchmarks.drift_sandbox --json out.json --baseline baselines/drift.json

Until now heal/locator resilience was anecdotal (one hand-broken `test_replan` fixture). This learns ONE
flow on a pristine page, then replays the cached flow against each of N named drifts (a banner added, an
id removed, the target wrapped/reordered, a sibling inserted, a heading renamed, an ambiguous twin added,
the target removed) and classifies the outcome:

  - SURVIVED   the resilient locator resolved the RIGHT target 0-LLM and the flow reached its goal page.
  - HEALED     0-LLM failed but an LLM re-grounded the step (only with --provider; counts an LLM call).
  - DRIFTED    the locator failed loud (no heal / no provider) — the SAFE outcome for a real change.
  - WRONG      the flow "succeeded" but landed on the wrong target — a SILENT MIS-BIND. Must be ZERO;
               it's the one outcome the whole resilient-locator design forbids.

Headline metrics: 0-LLM resilience rate over the *cosmetic* drifts (higher = fewer paid heals), and the
WRONG count (must be 0). `--baseline` fails (exit 1) if resilience regresses past the error bar or any
WRONG appears.
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from ultracua.cache import FlowCache
from ultracua.flow import run_cached
from ultracua.llm.base import Router, Tier
from ultracua.llm.mock import MockClient
from ultracua.providers.scripted import ScriptedProvider

# The pristine flow page: the target is a "Continue" link inside the Checkout section (which carries an
# h2 heading "Checkout" — the neighbor anchor). Sibling sections + chrome give realistic surroundings.
_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Shop</title></head><body>
  <header><a href="/help">Help</a> <a href="/account">Account</a></header>
  <section id="cart"><h2>Your Cart</h2><p>1 item — a widget</p></section>
  <section id="checkout"><h2>Checkout</h2>
    <p>Review your order, then continue.</p>
    <a href="/done">Continue</a>
  </section>
  <footer><p>(c) 2026 Shop</p></footer>
</body></html>"""

GOAL = "continue to the next step"
STEPS = [
    {"action": "click", "role": "link", "name": "Continue", "intent": "continue to the next step"},
    {"action": "done", "intent": "done"},
]

# Each drift mutates the pristine page (in the browser, after navigation, before replay). `kind`:
#   "cosmetic"  — the target's identity is preserved; the resilient locator SHOULD survive 0-LLM.
#   "ambiguous" — a same-name twin is added in another section; the neighbor anchor SHOULD pick the right
#                 one. If it can't, it must fail loud (DRIFTED), never bind the twin (WRONG).
#   "semantic"  — the target is gone; the locator MUST fail loud (DRIFTED), never bind something else.
_DONE = "document.querySelector('a[href=\"/done\"]')"
DRIFTS = [
    {"name": "none", "kind": "cosmetic", "js": ""},
    {"name": "banner-added", "kind": "cosmetic",
     "js": "() => { const d = document.createElement('div'); d.textContent = 'FLASH SALE'; "
           "document.body.insertBefore(d, document.body.firstChild); }"},
    {"name": "section-id-removed", "kind": "cosmetic",
     "js": "() => document.querySelector('#checkout').removeAttribute('id')"},
    {"name": "target-classed", "kind": "cosmetic",
     "js": f"() => {{ {_DONE}.className = 'btn btn-primary pulse'; }}"},
    {"name": "target-wrapped", "kind": "cosmetic",
     "js": f"() => {{ const a = {_DONE}; const s = document.createElement('span'); "
           "a.parentNode.insertBefore(s, a); s.appendChild(a); }"},
    {"name": "sibling-inserted", "kind": "cosmetic",
     "js": f"() => {{ const a = {_DONE}; const b = document.createElement('a'); b.href = '/back'; "
           "b.textContent = 'Back'; a.parentNode.insertBefore(b, a); }"},
    {"name": "section-reordered", "kind": "cosmetic",
     "js": "() => document.body.appendChild(document.querySelector('#checkout'))"},
    {"name": "heading-renamed", "kind": "cosmetic",
     "js": "() => { document.querySelector('#checkout h2').textContent = 'Payment'; }"},
    {"name": "target-renamed", "kind": "cosmetic",
     "js": f"() => {{ {_DONE}.textContent = 'Proceed'; }}"},  # role+name breaks -> css must recover it
    {"name": "ambiguous-twin", "kind": "ambiguous",
     "js": "() => { const a = document.createElement('a'); a.href = '/wrong'; a.textContent = 'Continue'; "
           "document.querySelector('#cart').appendChild(a); }"},
    {"name": "target-removed", "kind": "semantic", "js": f"() => {{ {_DONE}.remove(); }}"},
]


def _serve():
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            body = _PAGE if path == "/" else f"<!doctype html><title>{path}</title><h1>{path}</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _mock_router() -> Router:
    mc = MockClient(actions=[{"found": True, "data": None}], tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


def _prepare(js: str):
    async def prepare(session) -> None:
        if js:
            await session.page.evaluate(js)
    return prepare


async def _finalize(session):
    # The click navigates asynchronously; wait for the page to actually LEAVE the start page before
    # reading where we landed, so a fast/slow navigation race can't misreport the outcome. (A drift that
    # never resolves never navigates -> this times out and we stay on "/", which classifies as drifted.)
    from urllib.parse import urlparse
    try:
        await session.page.wait_for_url(lambda u: urlparse(u).path not in ("", "/"), timeout=3000)
    except Exception:  # noqa: BLE001
        pass
    return {"url": session.page.url}


def _classify(drift: dict, report) -> tuple[str, int]:
    """-> (outcome, llm_calls). outcome in {survived, healed, drifted, wrong}."""
    fin = (report.extra or {}).get("finalize") or {}
    landed_done = str(fin.get("url", "")).endswith("/done")
    landed_wrong = str(fin.get("url", "")).endswith("/wrong")
    if report.success and landed_done:
        return ("healed" if report.healed_steps else "survived"), report.llm_calls
    if report.success and landed_wrong:
        return "wrong", report.llm_calls          # silent mis-bind — the forbidden outcome
    return "drifted", report.llm_calls            # failed loud (target gone / ambiguous & unanchorable)


async def measure(provider_name: str = "scripted") -> dict:
    """Learn the flow once, replay it against every drift, classify, and return the run record (no
    printing) — so a CI test can assert the metrics directly."""
    httpd, base = _serve()
    rows: list[dict] = []
    try:
        with TemporaryDirectory() as td:
            cache = FlowCache(root=Path(td) / "c")
            # LEARN once on the pristine page (scripted teacher — key-less).
            learn = await run_cached(f"{base}/", GOAL, ScriptedProvider(list(STEPS)), cache,
                                     mode="learn", headless=True)
            if not learn.success:
                raise RuntimeError("failed to learn the baseline drift-sandbox flow")
            # REPLAY the cached flow against each drift. With a provider, a 0-LLM miss may self-heal.
            for d in DRIFTS:
                kw: dict = {}
                if provider_name != "scripted":
                    kw["provider_name"] = provider_name  # enables heal/replan on drift
                report = await run_cached(
                    f"{base}/", GOAL, None, cache,
                    mode="replay" if provider_name == "scripted" else "auto",
                    headless=True, prepare=_prepare(d["js"]), finalize=_finalize, **kw,
                )
                outcome, llm = _classify(d, report)
                rows.append({"drift": d["name"], "kind": d["kind"], "outcome": outcome, "llm": llm})
    finally:
        httpd.shutdown()
        httpd.server_close()

    cosmetic = [r for r in rows if r["kind"] == "cosmetic"]
    survived_0llm = sum(1 for r in cosmetic if r["outcome"] == "survived")
    survived = sum(1 for r in cosmetic if r["outcome"] in ("survived", "healed"))
    return {
        "provider": provider_name,
        "cosmetic_total": len(cosmetic),
        "cosmetic_survived_0llm": survived_0llm,
        "cosmetic_survived_incl_heal": survived,
        "resilience_0llm": round(survived_0llm / len(cosmetic), 4) if cosmetic else 0.0,
        "wrong_binds": sum(1 for r in rows if r["outcome"] == "wrong"),
        "ambiguous_disambiguated": all(r["outcome"] in ("survived", "healed")
                                       for r in rows if r["kind"] == "ambiguous"),
        "semantic_failed_loud": all(r["outcome"] == "drifted" for r in rows if r["kind"] == "semantic"),
        "rows": rows,
    }


async def run(provider_name: str, json_path: str | None, baseline_path: str | None) -> int:
    print(f"drift-sandbox: provider={provider_name}  drifts={len(DRIFTS)}\n")
    record = await measure(provider_name)
    rows = record["rows"]
    for r in rows:
        mark = {"survived": "OK  ", "healed": "HEAL", "drifted": "DRIFT", "wrong": "WRONG"}[r["outcome"]]
        print(f"  [{mark:<5}] {r['drift']:<20} ({r['kind']}){'  +1 LLM' if r['llm'] else ''}")

    rate = record["resilience_0llm"]
    wrong = record["wrong_binds"]
    semantic_failloud = record["semantic_failed_loud"]
    print(f"\n== 0-LLM resilience {record['cosmetic_survived_0llm']}/{record['cosmetic_total']} "
          f"({rate:.0%}) cosmetic drifts; wrong-binds={wrong}; "
          f"ambiguous-disambiguated={record['ambiguous_disambiguated']}; "
          f"semantic-fail-loud={semantic_failloud} ==")

    if json_path:
        Path(json_path).write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"wrote {json_path}")

    failed = wrong > 0 or not semantic_failloud
    if baseline_path:
        base_rec = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
        if rate + 1e-9 < base_rec.get("resilience_0llm", 0):
            print(f"REGRESSION: resilience {rate:.0%} < baseline {base_rec['resilience_0llm']:.0%}")
            failed = True
        if wrong > base_rec.get("wrong_binds", 0):
            print(f"REGRESSION: wrong-binds {wrong} > baseline {base_rec.get('wrong_binds', 0)}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="benchmarks.drift_sandbox")
    ap.add_argument("--provider", default="scripted", choices=["scripted", "anthropic", "openai", "gemini"])
    ap.add_argument("--json", dest="json_path", default=None, help="write the run record to this path")
    ap.add_argument("--baseline", dest="baseline_path", default=None,
                    help="fail (exit 1) if resilience regresses or any wrong-bind appears vs this record")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.provider, args.json_path, args.baseline_path)))
