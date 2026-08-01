"""The write SIGNAL: detect it, persist it onto the step, and refuse to run without it (A9 / A5 / A10).

Three findings from docs/open-defects.md that turn out to be one root cause and one consequence.

THE ROOT CAUSE (A9 + A5). The learn loop already watches the network and knows perfectly well that a write
fired — `_author_steps` sets `wrote["hit"]` from a real `is_write_request` match inside the act window. It
then threw that evidence away, using it only pass-globally (skip verify-by-replay, stop best-of-N) and never
writing it back onto the step that caused it. So two extremely ordinary shapes cached as reads:

  * `<form onSubmit={...}>` with NO method attribute — the ubiquitous React shape. `_MUTATION_CTX_JS`
    reports `form_method="get"`, and `classify_mutation`'s GET branch returns False *without even reaching*
    the keyword fallback — so an intent of literally "place the order" still classified non-mutating.
  * a formless `<button type=button onclick="fetch(..., {method:'POST'})">` with a bland name.

Everything downstream keys off `step.mutating`: the drift gate, the Idempotency-Key, the never-LLM-heal
rule, the no-suffix-replan rule, MCP's `readOnlyHint`, `run_batch`'s write check, `flow approve --all`'s
skip. All of them silently no-opped on a real write.

THE CONSEQUENCE (A10). A flow DECLARED a write whose commit no step is marked mutating for plans ZERO
Idempotency-Keys — so the key never reaches the wire, no ledger file is ever created, `run_batch(resume=)`
re-fires the commit on every resume with no human in the loop, and `dry_run` (which releases idempotent
methods unheld) FIRES a GET-link commit at the real server while reporting `writes_planned=0`.

The recorder has had the equivalent guard, test-pinned, since Phase I. `learn()` never did — the pattern
this codebase keeps producing.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.flows import (
    FlowSpec,
    MutateSpec,
    UnkeyedWriteError,
    approve,
    dry_run,
    is_write_flow,
    learn,
    preflight_keys,
    record,
    run_batch,
)
from ultracua.mcpserver.server import list_flow_tools
from ultracua.providers.scripted import ScriptedProvider


class _Site:
    """One server, several page shapes. Records every non-GET request as (method, path, idem, body), and
    also records GETs to the commit path so a GET-link commit is visible."""

    def __init__(self, page: str) -> None:
        self.page = page
        self.writes: list[tuple[str, str, str, str]] = []
        self.commits: list[tuple[str, str]] = []       # (path, Idempotency-Key) for GET-link commits

    def serve(self):
        site = self

        class _H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def _send(self, body: str, code: int = 200) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?")[0]
                if path == "/go":                      # a GET-link commit (A10's shape)
                    site.commits.append((path, self.headers.get("Idempotency-Key") or ""))
                    self._send("<h1>All set</h1>")
                elif path == "/":
                    self._send(site.page)
                else:
                    self._send("<h1>ok</h1>")

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode() if length else ""
                site.writes.append(("POST", self.path.split("?")[0],
                                    self.headers.get("Idempotency-Key") or "", body))
                self._send("<h1>Order placed</h1>")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# A method-less <form onSubmit> that POSTs via fetch — `classify_mutation` reads form_method as "get".
_GET_FORM = """<h1>Cart</h1>
<form id='f' action='/order'>
  <input name='qty' value='1'>
  <button type='submit'>Place order</button>
</form>
<script>
document.getElementById('f').addEventListener('submit', function (e) {
  e.preventDefault();
  fetch('/api/order', {method: 'POST', body: 'qty=' + document.querySelector('[name=qty]').value});
});
</script>"""

# The same form, but its listener fires NOTHING. The control: no wire evidence -> must stay a read.
_GET_FORM_INERT = """<h1>Cart</h1>
<form id='f' action='/order'>
  <input name='qty' value='1'>
  <button type='submit'>Place order</button>
</form>
<script>
document.getElementById('f').addEventListener('submit', function (e) { e.preventDefault(); });
</script>"""

# A formless JS POST behind a bland name — no form context at all, and no mutating keyword.
_JS_BUTTON = """<h1>Panel</h1>
<button id='go' type='button' onclick="fetch('/api/sync', {method: 'POST'})">Continue</button>"""

# An ordinary GET-only read. The control for the whole slice: nothing here may become a write.
_READ_ONLY = "<h1>Home</h1><a href='/next'>Continue</a>"

# A DECLARED write whose commit is a bare GET link — nothing can classify it, so it plans zero keys.
_LINK_COMMIT = "<h1>Opt out</h1><a href='/go'>Proceed</a>"


def _click(name: str, intent: str):
    return ScriptedProvider([{"action": "click", "role": "button", "name": name, "intent": intent},
                             {"action": "done", "intent": "done"}])


def _click_link(name: str, intent: str):
    return ScriptedProvider([{"action": "click", "role": "link", "name": name, "intent": intent},
                             {"action": "done", "intent": "done"}])


# ==================== A9/A5: the wire evidence is written back onto the step ====================

async def test_a_method_less_form_commit_that_posts_is_cached_as_a_write(tmp_path: Path) -> None:
    """The React shape. `classify_mutation` says read (GET branch, keyword fallback never reached);
    the wire says write. The wire wins."""
    site = _Site(_GET_FORM)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "place the order"
    try:
        report = await run_cached(f"{base}/", goal, _click("Place order", "place the order"), cache,
                                  mode="learn", headless=True)
        assert report.success
        assert site.writes, "the fixture did not POST; the assertions below would prove nothing"

        flow = cache.get(flow_key(goal, f"{base}/"))
        commit = flow.steps[0]
        assert commit.mutating is True
        # ...and it carries the PRECISE precondition, captured pre-act while the element was live. Without
        # it the gate would silently degrade to the whole-page fingerprint.
        assert commit.precond_scope != ""
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_promoted_write_is_then_gated_by_the_mutation_gate_on_replay(tmp_path: Path) -> None:
    """The point of the flag: with it, form drift makes replay REFUSE instead of committing a wrong
    payload. This is the assertion that shows the promotion is load-bearing, not cosmetic."""
    site = _Site(_GET_FORM)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "place the order"
    try:
        assert (await run_cached(f"{base}/", goal, _click("Place order", "place the order"), cache,
                                 mode="learn", headless=True)).success
        # The form drifts: an extra field appears INSIDE it and the quantity default changes.
        site.page = _GET_FORM.replace("<input name='qty' value='1'>",
                                      "<input name='qty' value='99'><input name='express' value='yes'>")
        site.writes.clear()

        report = await run_cached(f"{base}/", goal, None, cache, mode="replay", headless=True)
        assert report.success is False              # fail LOUD
        assert site.writes == []                    # ...and, crucially, nothing was committed
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_formless_js_post_behind_a_bland_name_is_cached_as_a_write(tmp_path: Path) -> None:
    """No form context and no mutating keyword — the classifier has nothing to go on. The wire does."""
    site = _Site(_JS_BUTTON)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "open the panel"
    try:
        assert (await run_cached(f"{base}/", goal, _click("Continue", "open the daily panel"), cache,
                                 mode="learn", headless=True)).success
        assert site.writes
        assert cache.get(flow_key(goal, f"{base}/")).steps[0].mutating is True
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_form_whose_listener_writes_nothing_stays_a_read(tmp_path: Path) -> None:
    """The control that keeps this from becoming "blanket-gate every GET form". Promotion must ride on
    REAL wire evidence — without it, a search form would start demanding approval."""
    site = _Site(_GET_FORM_INERT)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "run the search"
    try:
        assert (await run_cached(f"{base}/", goal, _click("Place order", "place the order"), cache,
                                 mode="learn", headless=True)).success
        assert site.writes == []
        assert cache.get(flow_key(goal, f"{base}/")).steps[0].mutating is False
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_an_ordinary_read_flow_is_untouched(tmp_path: Path) -> None:
    """The other control: a GET-only flow must keep every step non-mutating and stay a read."""
    site = _Site(_READ_ONLY)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    goal = "open the next page"
    try:
        assert (await run_cached(f"{base}/", goal, _click_link("Continue", "open the next page"), cache,
                                 mode="learn", headless=True)).success
        flow = cache.get(flow_key(goal, f"{base}/"))
        assert all(s.mutating is False for s in flow.steps)
        spec = FlowSpec(name="r", start_url=f"{base}/", goal=goal, headless=True)
        assert is_write_flow(spec, flow) is False
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_an_undeclared_wire_write_is_never_advertised_over_mcp(tmp_path: Path, monkeypatch) -> None:
    """The A5 surface. A bland-named button whose handler POSTs was learned as a read, could be
    bulk-approved by `flow approve --all`, and was served with readOnlyHint=True — an untrusted outer agent
    firing an irreversible POST with no write prefix, no confirm, no key, no mutex and no ledger."""
    monkeypatch.chdir(tmp_path)
    site = _Site(_JS_BUTTON)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        from ultracua.flows import save_spec
        spec = FlowSpec(name="daily_panel", start_url=f"{base}/", goal="open the panel", headless=True)
        save_spec(spec)
        res = await learn(spec, provider=_click("Continue", "open the daily panel"),
                          router=None, cache=cache)
        assert res.performed_write is True

        cached = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        assert cached is not None and cached.steps[0].mutating is True   # (1) the signal is PERSISTED
        assert is_write_flow(spec, cached) is True

        approve(spec, cache=cache)
        # (2) an UNDECLARED write is never advertised — with the flag or without it. `--expose-writes`
        # only ever covers a DECLARED write with a confirm check.
        assert list_flow_tools(cache, expose_writes=False) == []
        assert list_flow_tools(cache, expose_writes=True) == []
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== A10: a declared write that plans zero keys is refused ====================

async def _recorded_link_commit(tmp_path: Path, site: _Site, base: str, name: str):
    """Record + approve a DECLARED write whose commit is a bare GET link.

    The name and the intent deliberately avoid `safety.MUTATING_KEYWORDS` — with a mutating keyword the
    step would classify as a write and the case would evaporate."""
    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name=name, start_url=f"{base}/", goal="finish the opt out", headless=True,
                    mutate=MutateSpec(confirm_text_contains="All set"))

    async def _demo(page) -> None:
        await page.get_by_role("link", name="Proceed").click()
        await page.wait_for_selector("text=All set")

    res = await record(spec, demo=_demo, headless=True, cache=cache)
    assert res.cached, res.note
    approve(spec, cache=cache)
    cached = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
    # The precondition for this whole section: nothing marked the commit as a write, so zero keys are
    # planned. If a future change starts classifying it, these tests must be re-thought, not deleted.
    assert not any(s.mutating for s in cached.steps)
    site.commits.clear()
    return cache, spec


async def test_a_declared_write_planning_zero_keys_is_refused_at_preflight(tmp_path: Path) -> None:
    site = _Site(_LINK_COMMIT)
    httpd, base = site.serve()
    try:
        cache, spec = await _recorded_link_commit(tmp_path, site, base, "optout")
        with pytest.raises(UnkeyedWriteError) as ei:
            preflight_keys(spec, None, cache=cache)
        assert ei.value.retryable is False and ei.value.code == "unkeyed_write"
        assert site.commits == []            # refused pre-browser: nothing actuated
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_the_resume_ledger_cannot_be_bypassed_by_an_unkeyed_write(tmp_path: Path) -> None:
    """`ledger.py` promises "never a false skip of an un-landed write". With zero keys the ledger
    short-circuits entirely — no file is ever created — so a resumed batch silently re-fires the commit,
    with no human elicit anywhere on that path."""
    site = _Site(_LINK_COMMIT)
    httpd, base = site.serve()
    try:
        cache, spec = await _recorded_link_commit(tmp_path, site, base, "optout_batch")
        out = await run_batch(spec, [{}], max_rows=1, resume="job1", cache=cache)
        assert out.rows[0].status == "invalid"
        assert site.commits == []
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_dry_run_refuses_instead_of_firing_the_commit_for_real(tmp_path: Path) -> None:
    """The worst of the three. `DryRunArbiter` releases idempotent methods unheld, so the GET commit
    ACTUALLY REACHED THE SERVER during a run whose whole promise is that no write leaves the browser —
    and the artifact reported `writes_planned=0, held=0, warnings=[]`, certifying itself clean."""
    site = _Site(_LINK_COMMIT)
    httpd, base = site.serve()
    try:
        cache, spec = await _recorded_link_commit(tmp_path, site, base, "optout_dry")
        rep = await dry_run(spec, cache=cache)
        assert rep.aborted == "unkeyed_write", f"{rep.aborted}: {rep.abort_detail}"
        assert site.commits == [], f"the dry run LEAKED a real commit: {site.commits}"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_an_ordinary_post_form_write_is_unaffected(tmp_path: Path) -> None:
    """The control for A10: a real POST-form write has a mutating step, plans a key, and still runs."""
    site = _Site("<h1>Cart</h1><form action='/save' method='post'><button>Place order</button></form>")
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name="ord", start_url=f"{base}/", goal="place the order", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Order placed"))

    async def _demo(page) -> None:
        await page.get_by_role("button", name="Place order").click()

    try:
        assert (await record(spec, demo=_demo, headless=True, cache=cache)).cached
        approve(spec, cache=cache)
        _resolved, keys = preflight_keys(spec, None, cache=cache)
        assert len(keys) == 1 and keys[0].startswith("uca-")
        site.writes.clear()
        assert await __import__("ultracua.flows", fromlist=["replay"]).replay(spec, cache=cache) == {
            "status": "confirmed", "data": None}
        assert len(site.writes) == 1 and site.writes[0][2] == keys[0]   # the previewed key IS the wire key
    finally:
        httpd.shutdown()
        httpd.server_close()
