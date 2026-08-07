"""A refusal must be REMEMBERED, or it re-fires the write it refused (R3.13).

`flow._learn` refuses to cache a flow when a write fired on the wire that no step could be held
responsible for. That refusal is right — caching it would replay the commit ungated, un-keyed and
without a precondition, or with all three bolted to a step that never writes.

But it is AMNESIAC: it caches nothing and records nothing, so the world after a refusal is byte-identical
to the world before the flow was ever attempted. The next `mode="auto"` invocation finds no recipe,
learns again, drives the browser again, and the page fires the same commit again.

Re-measured against main @ 0.80.0 before writing a line of fix (the register's own rule, and R3.13
records an earlier draft that was measured on a fixture which does not refuse and reached the opposite
conclusion). Three invocations, both entry points:

    flows._learn_once            run1 POSTs=1  run2 POSTs=2  run3 POSTs=3   0 keyed
    run_cached(mode="auto")      run1 POSTs=1  run2 POSTs=2  run3 POSTs=3   0 keyed

Identical to the 0.74.0 numbers in the register. Six releases and five plan slices changed nothing here.

WHAT THE FIX MUST SATISFY, and why each direction is pinned:

  1. invocation 2 does not fire the write — the finding;
  2. it does not fire it on the ENGINE path either. `ultracua run` (cli.py) and the daemon call
     `run_cached` directly and never pass through the flows-layer trust gate, so a memory that has to be
     INJECTED by the caller protects the `flow` verbs and leaves those two re-firing. That is "a guard in
     the wrapper rather than the mechanism", which `flow.py`'s own comment calls this codebase's
     most-repeated defect shape — in a comment written when this very refusal was moved into the engine
     for that reason. So the memory must be ON BY DEFAULT for a bare `run_cached`;
  3. `flow record` — the remedy the refusal message NAMES — must stay reachable, or the fix builds a trap
     whose escape hatch it has itself removed (the D0 shape);
  4. a human clear makes the flow learnable again. Without this clause the invariant is satisfied by
     refusing everything forever, which is the 0.74.0 over-refusal regression that actually shipped.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.flows import FlowSpec, _learn_once, release
from ultracua.providers.scripted import ScriptedProvider

# The register's own shape: a benign step whose INTENT trips the keyword classifier ("submit the
# search"), with the real commit on the NEXT step — so R3.2 leaves the commit unattributed and the
# consistency rule refuses the flow.
_LATER_COMMIT = """<h1>Panel</h1>
<button id='s' type='button'>Search</button>
<button id='c' type='button' onclick="fetch('/api/commit',{method:'POST',body:'x=1'})">Continue</button>
<script>
for (const id of ['s','c']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""


class _Site:
    def __init__(self, page: str = _LATER_COMMIT) -> None:
        self.page = page
        self.posts: list[str] = []
        self.keys: list = []          # the Idempotency-Key on each POST, if any

    def serve(self):
        site = self

        class _H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def _send(self, body: str, ctype: str = "text/html") -> None:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body.encode())))
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self) -> None:  # noqa: N802
                self._send(site.page)

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.posts.append(self.path.split("?")[0])
                site.keys.append(self.headers.get("Idempotency-Key"))
                self._send("{}", "application/json")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _prov() -> ScriptedProvider:
    return ScriptedProvider([
        {"action": "click", "role": "button", "name": "Search", "intent": "submit the search"},
        {"action": "click", "role": "button", "name": "Continue", "intent": "continue"},
        {"action": "done", "intent": "done"},
    ])


# ==================== 1. the finding ====================


async def test_a_refused_learn_does_not_re_fire_the_write_next_time(tmp_path: Path) -> None:
    """THE FINDING, on the flows-layer path. Three invocations of a flow that refuses placed three
    orders on the vendor's system while reporting failure, with a clear reason, all three times.

    Note what is NOT claimed: run 1's write is unavoidable. The commit fires during discovery, on the
    live page, before anything can know it was unattributable — that is HOW we find out. What is
    available to prevent is doing it again.
    """
    site = _Site()
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        for _ in range(3):
            res = await _learn_once(spec, provider=_prov(), router=None, cache=cache,
                                    verify_replay=False)
            assert res.cached is False, "premise: this fixture must refuse, or the test proves nothing"

        assert site.posts == ["/api/commit"], (
            f"a refusal that is not remembered re-fires the write it refused: {len(site.posts)} POSTs "
            f"from 3 invocations")
        assert not any(site.keys), "and none of them carried an Idempotency-Key"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_the_engine_path_remembers_it_too(tmp_path: Path) -> None:
    """The half that decides the DESIGN, not just the fix. `ultracua run` (cli.py:52) and the daemon
    (daemon/server.py:53) call `run_cached` directly — they never reach the flows-layer trust gate where
    `FlowMeta.quarantine` is enforced. A memory the CALLER has to inject would protect the `flow` verbs
    and leave these two firing a write per invocation, forever.

    So this test calls `run_cached` exactly as they do — bare, with no policy argument — and requires the
    refusal to be remembered anyway. It fails against any design where protection is opt-in.
    """
    site = _Site()
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        for _ in range(3):
            rep = await run_cached(url=f"{base}/", goal="work the panel", provider=_prov(),
                                   cache=cache, mode="auto", headless=True)
            assert rep.success is False, "premise: this fixture must refuse"

        assert site.posts == ["/api/commit"], (
            f"the engine path re-learns and re-fires on every invocation: {len(site.posts)} POSTs from 3 "
            f"runs. `ultracua run` and the daemon both reach the write this way.")
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== 2. the clauses that keep it from being an over-refusal ====================


async def test_a_human_clear_makes_the_flow_learnable_again(tmp_path: Path) -> None:
    """The must-remain-learnable clause, and it is load-bearing: "never re-fires" is satisfied trivially
    by refusing everything forever, and that exact over-refusal regression shipped once already in
    0.74.0. A remembered refusal is TERMINAL until a human acts — not permanent.

    The page is fixed between the two halves (the operator corrected the flow, or the site changed), so
    the second learn is a legitimately different attempt and must be allowed to proceed.
    """
    site = _Site()
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        await _learn_once(spec, provider=_prov(), router=None, cache=cache, verify_replay=False)
        blocked = await _learn_once(spec, provider=_prov(), router=None, cache=cache,
                                    verify_replay=False)
        assert blocked.cached is False and len(site.posts) == 1, "premise: the refusal is remembered"

        release(spec, cache=cache)                      # the human act

        after = await _learn_once(spec, provider=_prov(), router=None, cache=cache,
                                  verify_replay=False)
        assert len(site.posts) == 2, (
            "after a human clear the flow must be learnable again — a refusal that cannot be cleared is "
            "the over-refusal regression, not a fix")
        assert after.cached is False, "and it refuses again on the same evidence, which is correct"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_the_memory_is_deliberately_blind_to_the_page_getting_better(tmp_path: Path) -> None:
    """The direction-of-error decision, made explicit so nobody "fixes" it later by accident.

    The refusal CAN be transient: attribution runs on a time window, so a deferred commit that lands just
    outside it is unattributable on one run and attributable on the next. Under this design one such
    flake blocks re-authoring until a human runs `flow release` — the memory does not re-check whether
    the page would behave now, because re-checking means driving the browser, which means firing the
    write, which is the entire thing being prevented.

    That asymmetry is the argument, and it is the same one `landed` is built on: a stale refusal costs
    one command from a human who is being told exactly what to run; a forgotten one costs a duplicate
    un-keyed POST on someone's payment endpoint, and nothing catches that.

    Here the page is REPLACED with one that learns cleanly, which is a stronger version of the transient
    case, and the flow stays refused anyway.
    """
    site = _Site()
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        await _learn_once(spec, provider=_prov(), router=None, cache=cache, verify_replay=False)
        assert len(site.posts) == 1, "premise: the first attempt refused after firing once"

        site.page = """<h1>Panel</h1>
<button id='s' type='button'>Search</button>
<button id='c' type='button'>Continue</button>
<script>
for (const id of ['s','c']) document.getElementById(id).addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'clicked ' + id; });
</script>"""      # writes nothing at all now

        still = await _learn_once(spec, provider=_prov(), router=None, cache=cache, verify_replay=False)
        assert still.cached is False, (
            "the memory must not re-check by driving the browser — that is the write it exists to stop")
        assert len(site.posts) == 1

        release(spec, cache=cache)
        ok = await _learn_once(spec, provider=_prov(), router=None, cache=cache, verify_replay=False)
        assert ok.cached is True, "and after the human act the now-clean page learns normally"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_flow_status_says_refused_rather_than_not_learned(tmp_path: Path) -> None:
    """A refused flow was never cached, so the health ladder called it `not-learned` — indistinguishable
    from one nobody has got round to yet, when in fact it fired a write nothing could account for and
    needs a human. That is a state nobody chose reported as a routine one, which is the rule S7a applied
    to the fleet's skips, one surface over.

    The reason travels with it: a refused flow has no run history, so `last_error` would otherwise be
    empty and the operator would get a new status with nothing beside it.
    """
    from ultracua.flows import health

    site = _Site()
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        await _learn_once(spec, provider=_prov(), router=None, cache=cache, verify_replay=False)

        h = health(spec, cache=cache)
        assert h.status == "refused", f"a refused flow reported as {h.status!r}"
        assert "attributed" in (h.last_error or ""), "and the reason must travel with the status"

        release(spec, cache=cache)
        assert health(spec, cache=cache).status == "not-learned", "cleared, it is ordinary again"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_failed_release_does_not_half_clear_the_flow(tmp_path: Path) -> None:
    """Found by re-reading the diff, not by any test above. `release` clears two things, and the first
    draft cleared the refusal BEFORE an `_update_meta(..., on_unreadable="raise")` that can fail — so on
    an unreadable sidecar the operator got a raise, the refusal gone, and the quarantine they were told
    to investigate still on disk. A re-learn could then fire the write again.

    Neither clear may happen where something after it can still fail.
    """
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name="p", goal="work the panel", start_url="http://127.0.0.1:1/", headless=True)
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.remember_refusal(key, "write_unattributed", "a write fired that no step could be attributed to")
    flows_mod._save_meta(cache, key, flows_mod.FlowMeta(
        quarantine={"code": "value_out_of_contract", "reason": "returned 0 rows", "ts": 1.0}))

    def _boom(*a, **kw):
        raise flows_mod.MetaUnreadableError("the sidecar could not be read")

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(flows_mod, "_update_meta", _boom)
        with _pytest.raises(flows_mod.MetaUnreadableError):
            flows_mod.release(spec, cache=cache)

    assert cache.refusal(key) is not None, (
        "the refusal was cleared by a release that then failed — the flow can now be re-authored (and "
        "re-fire the write) while its quarantine is still on disk")


async def test_the_remedy_the_refusal_names_stays_reachable(tmp_path: Path) -> None:
    """`flow record` is what the refusal message tells the operator to do: "Record it with `flow
    record`." If remembering the refusal also blocks recording, the fix removes its own escape hatch and
    the operator is trapped — which is the D0 shape, and the reason D0 is blocked indefinitely.

    `record()` is not gated by `_preflight_row`'s quarantine today (checked: its only gate is a comment
    mentioning it), and this test exists to keep that true rather than to assert it by luck.
    """
    from ultracua.flows import record

    site = _Site()
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        await _learn_once(spec, provider=_prov(), router=None, cache=cache, verify_replay=False)

        async def _demo(page) -> None:                  # the human demonstrates the read half only
            await page.click("#s")

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        assert res is not None, "a remembered refusal must not make `flow record` unreachable"
    finally:
        httpd.shutdown()
        httpd.server_close()
