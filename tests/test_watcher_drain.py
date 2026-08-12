"""A write that leaves INSIDE the act window must never be missed because the observer left first (R4.29).

THE HOLE. `_author_steps` breaks its loop and immediately runs `page.remove_listener("request",
_watch_request)` with no drain. A request the page has not dispatched YET is invisible from that instant.
`write_window_ms` cannot help: it bounds the attribution of requests that were OBSERVED, not the lifetime
of the observer. So every guard downstream of the watcher fails at once, and all of them quietly:

  * `wrote["hit"]` is never set, so `_learn_once`'s "a write fired on the wire during discovery but no
    step could be credited" refusal — the guard that exists for exactly this — never arms;
  * no wire promotion runs, so the committing step is never marked `mutating`;
  * the flow caches as an ordinary READ: no drift gate, no `precond_scope`, no Idempotency-Key, and
    heal- and suffix-replan-eligible.

Replay then re-fires that commit on every run, ungated and un-keyed. Inviolable #3, and #2 with it.

WHY THIS FIXTURE LOOKS ELABORATE, AND WHY THE OBVIOUS ONE WAS WRONG. The first reproduction chained bare
`setTimeout`s and measured 5/6 escapes; re-run on a quieter host the same chain measured 0/8, and at 32
tasks the commit never leaves at all because the page is torn down first. Fishing for the race produced
two contradictory rates, which is this repo's own warning that a rare bug is a harness problem, not a
patience problem (R4.26). So the race is REMOVED instead: the commit waits on a response the test server
HOLDS, and the hold is released from inside `flows._make_finalize`'s callable — which `run_cached` awaits
after `_author_steps` has returned (so after `remove_listener`) and before the session closes (so the
page can still send). Nothing depends on host speed; the ORDER is fixed by the call graph. Measured
10/10 against pre-fix source.

THE PROPERTY IS DISJUNCTIVE, deliberately, and it is the same shape as the AB-1 tests: a commit that left
the browser during discovery must not end up in a recipe that gates nothing. REFUSING the flow satisfies
it. Attributing and gating the commit satisfies it. Caching a clean read flow does not.
"""
from __future__ import annotations

import asyncio
import http.server
import threading
from pathlib import Path

import pytest

import ultracua.flows as _flows
from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowSpec, _learn_once
from ultracua.providers.scripted import ScriptedProvider

pytestmark = pytest.mark.asyncio

# The bait is `Show details`: an ordinary read control carrying no `MUTATING_KEYWORDS` term. That is
# load-bearing — a keyword-named bait would be gated by the classifier and would hide the hole behind a
# gate that arrived for an unrelated reason.
_PAGE = """<h1>Panel</h1>
<button id='c' type='button'>Continue</button>
<button id='a' type='button'>Show details</button>
<script>
document.getElementById('c').addEventListener('click', function(){ window.__armed = 1; });
window.addEventListener('click', function(ev){
  if (window.__armed && ev.target && ev.target.id === 'a') {
    window.__armed = 0;
    fetch('/gate').then(function(){
      fetch('/api/commit', {method: 'POST', body: 'x=1'});
    });
  }
}, true);
for (const id of ['c','a']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""


class _HeldSite:
    """A fixture server that can HOLD one response — until the test says go, or for a fixed delay.

    The two modes are the two contracts, and keeping them apart is the whole point of this file:

      `release_after_ms`   the commit dispatches a fixed delay after the bait click, INSIDE
                           `write_window_ms`. The observer is supposed to still be watching. R4.29.
      released by the hook the commit dispatches after the post-loop hook runs, i.e. BEYOND the window.
                           Nothing claims to catch that today; it is the residual, R4.30.
    """

    def __init__(self, release_after_ms: int = 0) -> None:
        self.posts: list[str] = []
        self.release = threading.Event()
        self.gate_hits = 0
        self.release_after_ms = release_after_ms

    def serve(self):
        site = self

        class _H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a) -> None:
                pass

            def _send(self, body: bytes, ctype: str) -> None:
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:      # the page can be torn down mid-response; not this test's concern
                    pass

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?")[0]
                if path == "/gate":
                    site.gate_hits += 1
                    if site.release_after_ms:
                        # SERVER-SIDE delay: the dispatch time is a fixed offset from the bait click,
                        # independent of the agent's call graph. That is what makes the R4.29 cell
                        # deterministic in BOTH directions — pre-fix the listener is already gone when it
                        # fires, post-fix the drain is still running.
                        site.release.wait(timeout=site.release_after_ms / 1000.0)
                    else:
                        site.release.wait(timeout=25)
                    self._send(b"{}", "application/json")
                    return
                self._send(_PAGE.encode() if path == "/" else b"{}",
                           "text/html" if path == "/" else "application/json")

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.posts.append(self.path.split("?")[0])
                self._send(b"{}", "application/json")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _drive(site: _HeldSite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                 *, release_in_hook: bool, keep_alive_s: float):
    """Learn the fixture, holding the page open past the commit's dispatch, and return (res, cache, spec).

    `keep_alive_s` matters and is not padding: the post-loop hook is the only thing standing between the
    loop ending and the session closing, so without it a commit scheduled for +400 ms can find the page
    already gone — which reads as "no POST" and would make the cell prove nothing in either direction.
    """
    httpd, base = site.serve()
    real_make = _flows._make_finalize
    hook_ran: list[str] = []

    def _make(*a, **kw):
        inner = real_make(*a, **kw)

        async def _wrapped(session):
            # `run_cached` awaits this AFTER `_author_steps` returned — i.e. after the request listener
            # was removed (and, post-fix, after the drain) — and BEFORE the session closes.
            hook_ran.append("after-removal")
            if release_in_hook:
                site.release.set()
            await asyncio.sleep(keep_alive_s)
            return await inner(session)

        return _wrapped

    monkeypatch.setattr(_flows, "_make_finalize", _make)
    spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
    res = await _learn_once(
        spec,
        provider=ScriptedProvider([
            {"action": "click", "role": "button", "name": "Continue", "intent": "continue"},
            {"action": "click", "role": "button", "name": "Show details",
             "intent": "show the details"},
            {"action": "done", "intent": "done"},
        ]),
        router=None, cache=FlowCache(root=tmp_path / "c"), verify_replay=False)
    await asyncio.sleep(0.2)                   # let a late POST reach the SERVER before we count
    return res, FlowCache(root=tmp_path / "c"), spec, httpd, hook_ran


def _assert_the_commit_was_not_lost(res, cache: FlowCache, spec: FlowSpec, site: _HeldSite,
                                    hook_ran: list) -> None:
    """The property, disjunctive: a commit that LEFT THE BROWSER during discovery must not end up in a
    recipe that gates nothing. Refusing satisfies it; attributing and gating satisfies it."""
    if not hook_ran:
        raise RuntimeError("PREMISE LOST: the post-loop hook never ran, so the page was not held open "
                           "and this cell is not exercising the finding")
    if site.posts != ["/api/commit"]:
        raise RuntimeError(
            f"PREMISE LOST: the commit did not leave the browser exactly once (posts={site.posts}, "
            f"gate_hits={site.gate_hits}). Nothing committed, so there is nothing to have missed")
    flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
    if flow is None:
        assert res.cached is False and res.note, "a refusal must be loud and carry its reason"
        return
    gated = [i for i, s in enumerate(flow.steps) if s.mutating]
    assert gated, (
        f"a commit LEFT THE BROWSER during discovery and the cached recipe gates nothing. "
        f"`wrote['hit']` was never set, so the unattributable-write refusal never armed; no wire "
        f"promotion ran; and replay will re-fire this commit ungated, un-keyed and with no precondition "
        f"on every run. steps={[(s.intent, s.mutating, s.mutating_sources) for s in flow.steps]}")
    for i in gated:
        assert flow.steps[i].precond_scope or flow.steps[i].precond_fingerprint, (
            f"step {i} is marked mutating but has no precondition, so its mutation gate is a no-op")


async def test_a_commit_dispatched_after_the_loop_but_INSIDE_the_window_is_still_seen(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R4.29 proper: the commit fires 400 ms after the bait click — well inside `write_window_ms` (2 s),
    and well after the loop has broken. The observer is supposed to still be watching, and was not.

    Deterministic in BOTH directions, which is what a regression test has to be: pre-fix the listener is
    removed the instant the loop breaks (~100 ms in), so a commit at +400 ms is invisible; post-fix the
    drain is still running at +400 ms, so it is counted exactly as it would have been mid-loop.
    """
    site = _HeldSite(release_after_ms=400)
    res, cache, spec, httpd, hook_ran = await _drive(
        site, tmp_path, monkeypatch, release_in_hook=False, keep_alive_s=0.9)
    try:
        _assert_the_commit_was_not_lost(res, cache, spec, site, hook_ran)
    finally:
        site.release.set()
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "R4.30 — OPEN. A commit deferred beyond `write_window_ms` is still unobserved. The drain restores "
    "the observer's promised lifetime; it does not extend it, and that bound is the window's own "
    "stated meaning rather than a cost artefact. Closing it means watching until the session closes, which "
    "changes what counts as a CAUSED write and needs an over-refusal measurement first (D0 / R4.27's "
    "shape). Measured 10/10 against 0.96.0."))
async def test_a_commit_deferred_BEYOND_the_window_is_still_lost(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """THE RESIDUAL, pinned rather than described — and it is the reason this file has two cells.

    Here the commit is released from the post-loop hook itself, so by construction it dispatches after
    the drain has finished. The system's own definition says a request outside every act window is not
    caused by our actions; a debounced commit at 3 s says that definition is a convention, not a fact.
    The harm is identical to R4.29's: the flow caches as a clean read and replay re-fires the commit.

    Kept STRICT so the day someone extends the observer to session close, this XPASSes and the suite goes
    red until the marker and R4.30 are dealt with together.
    """
    site = _HeldSite()                          # released only by the hook == beyond the window
    res, cache, spec, httpd, hook_ran = await _drive(
        site, tmp_path, monkeypatch, release_in_hook=True, keep_alive_s=0.5)
    try:
        _assert_the_commit_was_not_lost(res, cache, spec, site, hook_ran)
    finally:
        site.release.set()
        httpd.shutdown()
        httpd.server_close()



async def test_an_ordinary_read_flow_still_learns_and_is_not_gated(tmp_path: Path) -> None:
    """THE OTHER HALF, and it is what stops the fix from being an over-refusal.

    Whatever the fix does about late writes, a page that commits NOTHING must still learn, cache, and
    stay un-gated. A drain that refuses on suspicion — or that waits so long every read flow stalls — is
    the D0 shape wearing a timing hat, and this register has already paid for that once.
    """
    page = """<h1>Panel</h1>
<button id='c' type='button'>Continue</button>
<button id='a' type='button'>Show details</button>
<script>
for (const id of ['c','a']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""
    site = _HeldSite()
    site.release.set()                          # nothing is held; this page never fetches anyway

    class _Plain(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            body = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Plain)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="r", goal="read the panel", start_url=f"{base}/", headless=True)
        res = await _learn_once(
            spec,
            provider=ScriptedProvider([
                {"action": "click", "role": "button", "name": "Continue", "intent": "continue"},
                {"action": "click", "role": "button", "name": "Show details",
                 "intent": "show the details"},
                {"action": "done", "intent": "done"},
            ]),
            router=None, cache=cache, verify_replay=False)
        assert res.cached, f"an ordinary read flow must still learn; note={getattr(res, 'note', None)!r}"
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        assert flow is not None
        assert not [i for i, s in enumerate(flow.steps) if s.mutating], (
            f"a page that committed nothing must not acquire a write gate: "
            f"{[(s.intent, s.mutating, s.mutating_sources) for s in flow.steps]}")
    finally:
        httpd.shutdown()
        httpd.server_close()
