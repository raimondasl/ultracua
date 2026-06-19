"""Best-of-N authoring (Tier-2 #5): re-author up to N times, keep the FIRST sample the verify-by-replay
oracle confirms — converting discovery variance into a higher first-run success rate.

READ-ONLY by design: a sample that PERFORMED a write is never re-authored (that would re-submit), and a
declared write flow is never multi-sampled. These tests cover both the engine loop (`_learn_n`) and the
Flow API guard, key-lessly.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import ultracua.flows as flows_mod
from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.flows import FlowSpec, LearnResult, MutateSpec, learn
from ultracua.providers.scripted import ScriptedProvider
from ultracua.types import Action

from benchmarks.shop_flow import GOAL, index_url

# A pure READ flow (benign intents -> no mutating step -> verify-by-replay runs).
_READ_STEPS = [
    {"action": "type", "role": "textbox", "name": "search", "text": "widget", "intent": "enter the query"},
    {"action": "click", "role": "button", "name": "search", "intent": "run the search"},
    {"action": "click", "role": "link", "name": "open widget x", "intent": "open the detail page"},
    {"action": "click", "role": "button", "name": "add to cart", "intent": "click the cart button"},
    {"action": "done", "intent": "done"},
]


class _ImprovesOnRetry:
    """Gives up on the first authoring attempt, then walks the good steps — exercises best-of-N retry.

    A fresh page with empty history marks the start of a new attempt (each `_learn` re-navigates)."""

    def __init__(self, good_steps):
        self.good = good_steps
        self.attempt = 0
        self.sp = None

    async def decide(self, goal, obs, history):
        if not history:  # start of a new authoring attempt
            self.attempt += 1
            self.sp = ScriptedProvider(list(self.good)) if self.attempt >= 2 else None
        if self.attempt < 2:
            return Action(action="give_up", intent="first attempt bails"), None
        return await self.sp.decide(goal, obs, history)


class _AlwaysGivesUp:
    async def decide(self, goal, obs, history):
        return Action(action="give_up", intent="never works"), None


# --- engine best-of-N (_learn_n) --------------------------------------------------------------
async def test_best_of_n_keeps_first_verified_sample(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    report = await run_cached(url, GOAL, _ImprovesOnRetry(_READ_STEPS), cache, mode="learn",
                              headless=True, samples=3, verify_replay=True)
    assert report.success
    assert report.extra.get("samples_used") == 2          # attempt 1 bailed; attempt 2 verified
    assert cache.get(flow_key(GOAL, url)) is not None


async def test_best_of_n_never_retries_after_a_write(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    # Actuates a mutating click ("submit the order"), then bails — the write HAPPENED but didn't cache.
    provider = ScriptedProvider([
        {"action": "type", "role": "textbox", "name": "search", "text": "x", "intent": "enter query"},
        {"action": "click", "role": "button", "name": "search", "intent": "submit the order"},
        {"action": "give_up", "intent": "bail after the write"},
    ])
    report = await run_cached(url, GOAL, provider, cache, mode="learn", headless=True,
                              samples=3, verify_replay=True)
    assert report.success is False
    assert report.extra.get("performed_write") is True
    assert report.extra.get("samples_used") == 1          # a write fired -> NEVER re-author
    assert cache.get(flow_key(GOAL, url)) is None


async def test_best_of_n_exhausts_all_samples(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    report = await run_cached(url, GOAL, _AlwaysGivesUp(), cache, mode="learn", headless=True,
                              samples=3, verify_replay=True)
    assert report.success is False
    assert report.extra.get("samples_used") == 3          # tried all N (no write ever performed)
    assert cache.get(flow_key(GOAL, url)) is None


# --- write-ON-THE-WIRE detection (the classifier can't see formless POSTs / Enter-submits) -----
class _PostHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a) -> None:
        pass

    def do_POST(self) -> None:  # accept the fetch POST (it's caught by the request listener regardless)
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")  # let a cross-origin fetch resolve cleanly
        self.send_header("Content-Length", "0")
        self.end_headers()


def _serve(directory: Path):
    handler = functools.partial(_PostHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_best_of_n_stops_on_a_formless_post_the_classifier_misses(tmp_path: Path) -> None:
    # A plain <button> that fires a same-origin fetch('POST') — no <form>, bland intent — so
    # classify_mutation says NOT mutating. Only the network signal reveals the write; best-of-N must
    # still refuse to re-author it (no double-submit).
    (tmp_path / "page.html").write_text(
        "<!doctype html><html><body>"
        "<button id=\"go\" onclick=\"fetch('/api', {method:'POST'})\">Go</button>"
        "</body></html>", encoding="utf-8")
    httpd, base = _serve(tmp_path)
    try:
        cache = FlowCache(root=tmp_path / "cache")
        url = f"{base}/page.html"
        provider = ScriptedProvider([
            {"action": "click", "role": "button", "name": "go", "intent": "click the go button"},
            {"action": "done", "intent": "done"},
        ])
        report = await run_cached(url, "click go", provider, cache, mode="learn", headless=True,
                                  samples=3, verify_replay=True)
        assert report.extra.get("performed_write") is True   # caught by the act-window POST listener
        assert report.extra.get("samples_used") == 1         # NOT re-authored -> no double-submit
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_best_of_n_flags_a_cross_origin_write(tmp_path: Path) -> None:
    # The cross-origin gap: a button that fires a fetch('POST') to a DIFFERENT origin (an SPA checkout
    # that POSTs to a 3rd-party payment/API host). A same-origin-only watcher misses it, so best-of-N
    # could re-author and DOUBLE-SUBMIT to that API. The origin-independent, beacon-aware watcher catches
    # it: a non-idempotent request to a non-telemetry host, fired in the act window, is a write.
    api_httpd, api_base = _serve(tmp_path)           # the "3rd-party API" origin (different port => diff origin)
    (tmp_path / "page.html").write_text(
        "<!doctype html><html><body>"
        f"<button id=\"pay\" onclick=\"fetch('{api_base}/api', {{method:'POST'}})\">Pay</button>"
        "</body></html>", encoding="utf-8")
    page_httpd, page_base = _serve(tmp_path)
    try:
        cache = FlowCache(root=tmp_path / "cache")
        url = f"{page_base}/page.html"
        provider = ScriptedProvider([
            {"action": "click", "role": "button", "name": "pay", "intent": "click the pay button"},
            {"action": "done", "intent": "done"},
        ])
        report = await run_cached(url, "pay", provider, cache, mode="learn", headless=True,
                                  samples=3, verify_replay=True)
        assert report.extra.get("performed_write") is True   # caught despite firing to a DIFFERENT origin
        assert report.extra.get("samples_used") == 1         # NOT re-authored -> no double-submit
    finally:
        for h in (page_httpd, api_httpd):
            h.shutdown()
            h.server_close()


# --- Flow API write-safety (no double-submit) -------------------------------------------------
def _stub_router(_name):
    return object(), object()


async def test_flow_api_never_multisamples_a_declared_write(tmp_path: Path, monkeypatch) -> None:
    calls = {"n": 0}

    async def _counting(spec, *, provider, router, cache, verify_replay=True):
        calls["n"] += 1
        return LearnResult(spec=spec, cached=False, steps=[], found=False)

    monkeypatch.setattr(flows_mod, "_learn_once", _counting)
    monkeypatch.setattr(flows_mod, "_router", _stub_router)
    spec = FlowSpec(name="w", start_url="http://x/", goal="g",
                    mutate=MutateSpec(confirm_text_contains="ok"))
    await learn(spec, samples=3, cache=FlowCache(root=tmp_path / "c"))
    assert calls["n"] == 1   # a DECLARED write flow is forced to a single attempt


async def test_flow_api_stops_resampling_after_an_undeclared_write(tmp_path: Path, monkeypatch) -> None:
    calls = {"n": 0}

    async def _wrote(spec, *, provider, router, cache, verify_replay=True):
        calls["n"] += 1
        return LearnResult(spec=spec, cached=False, steps=[], found=False, performed_write=True)

    monkeypatch.setattr(flows_mod, "_learn_once", _wrote)
    monkeypatch.setattr(flows_mod, "_router", _stub_router)
    spec = FlowSpec(name="r", start_url="http://x/", goal="g", extract="d")  # a READ spec
    res = await learn(spec, samples=3, cache=FlowCache(root=tmp_path / "c"))
    assert calls["n"] == 1 and res.performed_write   # an actuated write stops re-sampling
