"""`describe()` writes page-derived secrets into the flow cache in plaintext (R3.6).

`snapshot.capture(redact=...)` was introduced as "the one place" every snapshot -> LLM path runs through,
and it works: the heal prompt really does say `[REDACTED]`. But `locators.describe()` reads the LIVE page
through its own JS and never consults `session.redact` / `_secret_values(spec)`, so the SAME run captures
the same characters verbatim into

    LocatorSpec.name        the accessible name — "Copy sk-live-DEADBEEF..." on the standard copy button
    LocatorSpec.anchor      60 chars of the enclosing row's collapsed innerText
    LocatorSpec.anchor_id   'href:' + the row's first href/action, QUERY STRING INCLUDED

...which `_learn`/`_replay` then `cache.put` to `<flow_home>/flows/<key>.json`, where it persists. Unlike
`audit.capture` (which chmods its directory 0o700 on POSIX), `FlowCache.put` does no chmod at all, so the
file lands at the default umask.

WHAT MAKES THIS THE NASTY KIND. Every observable signal says the redaction worked — the prompt is clean,
the Observation is clean, the audit artifact is clean. The leak is on the one channel nobody looks at,
and it is the durable one. `docs/open-defects.md` records R7 (extractor page text) and R9 (name/url/title
to the MODEL) as fixed; the DISK sibling was recorded nowhere and was not in the residual list.

The project rule this violates is stated as an absolute: secrets are "never serialized, logged or written
to disk".

THE CONSTRAINT THAT MAKES THIS MORE THAN A `str.replace`, and the reason S8 had to land first: REPLAY
BINDS ON THESE STRINGS. `resolve()` matches on `name`, and disambiguates with `anchor` / `anchor_id`. A
scrub that rewrites them changes what the cached recipe resolves to — so the redaction has to leave the
locator BINDABLE, or it trades a confidentiality leak for a silent wrong-target bind, which is a worse
defect on this project's own ranking. Both directions are asserted here for that reason.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowSpec, SlotSpec, learn
from ultracua.llm.base import Router, Tier
from ultracua.llm.mock import MockClient
from ultracua.providers.scripted import ScriptedProvider
from ultracua.snapshot import REDACTED

SECRET_ENV = "ULTRACUA_TEST_APIKEY"
SECRET = "sk-live-DEADBEEF0123456789"

# The exact page shape the 0.72.0 redaction fix cited as its own motivation: a table that ECHOES the
# token, with a copy button whose accessible name contains it and a row href that carries it in a query
# string. The button is what the agent clicks, so it is what `describe()` captures.
PAGE = f"""<h1>API keys</h1>
<table><tbody>
  <tr id="row-prod">
    <td>Production key {SECRET}</td>
    <td><a href="/reveal?api_key={SECRET}">details</a></td>
    <td><button type="button" id="copy">Copy {SECRET}</button></td>
  </tr>
</tbody></table>
<p id="out">idle</p>
<script>
document.getElementById('copy').addEventListener('click', function () {{
  document.getElementById('out').textContent = 'copied';
}});
</script>"""


def _serve(page: str):
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            body = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _spec(base: str) -> FlowSpec:
    return FlowSpec(
        name="keys", start_url=f"{base}/", goal="copy the production key", headless=True,
        slots={"key": SlotSpec(secret=True, secret_env=SECRET_ENV)},
    )


async def _learn_it(cache: FlowCache, base: str):
    """Learn a one-click flow. Scripted provider + mock router: the suite is key-less (see CLAUDE.md)."""
    # THE NAME THE AGENT ACTUALLY SEES, which is already `Copy [REDACTED]` — `_learn_once` passes
    # `redact=_secret_values(spec)` and `snapshot.capture` rewrites the accessible name before any
    # provider sees it. Scripting the RAW name matches nothing and the flow never caches, which is how
    # the first draft of this fixture failed: the Observation channel is fixed, and that is precisely
    # what makes the disk channel below easy to miss.
    provider = ScriptedProvider([
        {"action": "click", "role": "button", "name": f"Copy {REDACTED}", "intent": "copy the key"},
        {"action": "done", "intent": "done"},
    ])
    mc = MockClient(actions=[{"found": True, "data": "copied"}], tool_name="submit")
    router = Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))
    return await learn(_spec(base), provider=provider, router=router, cache=cache, verify_replay=False)


async def test_the_flow_cache_on_disk_holds_no_secret(tmp_path: Path, monkeypatch) -> None:
    """THE FINDING, asserted against the BYTES ON DISK rather than against the object graph.

    Reading the file is the point: an in-memory assertion could pass while the serializer wrote something
    else, and "written to disk" is precisely the claim. Anything the agent could later leak is already
    covered by the Observation scrub — this is the channel that outlives the run.
    """
    monkeypatch.setenv(SECRET_ENV, SECRET)
    httpd, base = _serve(PAGE)
    cache = FlowCache(root=tmp_path / "c")
    try:
        res = await _learn_it(cache, base)
        assert res.cached, f"premise: the flow must have been cached, got note={res.note!r}"

        key = flow_key(_spec(base).goal, _spec(base).start_url, _spec(base).scope)
        blob = (Path(cache.root) / f"{key}.json").read_text(encoding="utf-8")
        assert "Copy" in blob, "premise: the recipe must actually describe the clicked control"

        assert SECRET not in blob, (
            f"the resolved secret was written to the flow cache in plaintext, where it persists. "
            f"Offending fields: "
            f"{[k for k, v in json.loads(blob).get('steps', [{}])[0].get('locator', {}).items() if isinstance(v, str) and SECRET in v]}")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_every_captured_locator_field_is_scrubbed_not_just_the_name(
    tmp_path: Path, monkeypatch,
) -> None:
    """`name` is the obvious one and the least sufficient. The 0.72.0 fix's own justification was that a
    secret rides `url` and an accessible `name`; `describe()` additionally captures the row's collapsed
    TEXT (`anchor`) and its first href WITH the query string (`anchor_id`). Scrubbing one and not the
    others is the same partial-coverage mistake one layer down — R9 fixed 2 of 5 Observation fields and
    had to be reopened for the other three.
    """
    monkeypatch.setenv(SECRET_ENV, SECRET)
    httpd, base = _serve(PAGE)
    cache = FlowCache(root=tmp_path / "c")
    try:
        await _learn_it(cache, base)
        key = flow_key(_spec(base).goal, _spec(base).start_url, _spec(base).scope)
        cached = cache.get(key)
        assert cached and cached.steps, "premise: a step must have been cached"

        loc = cached.steps[0].locator
        assert loc is not None, "premise: the step must carry a locator to inspect"
        leaked = {f: getattr(loc, f) for f in ("name", "text", "anchor", "anchor_id", "css", "elem_id")
                  if isinstance(getattr(loc, f, None), str) and SECRET in getattr(loc, f)}
        assert not leaked, f"secret survives in LocatorSpec fields: {sorted(leaked)}"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_the_scrubbed_recipe_still_binds_its_target(tmp_path: Path, monkeypatch) -> None:
    """THE MUST-REMAIN-BINDABLE CLAUSE, and it is the half that makes this hard.

    Replay resolves on these very strings. A scrub that rewrites `name` into something the page does not
    contain turns a confidentiality leak into a locator that binds NOTHING — or worse, binds the wrong
    element — and on this project's ranking a silent wrong-target bind is a more severe defect than the
    leak being fixed. Redaction must be applied so the recipe still resolves against the SAME page.
    """
    monkeypatch.setenv(SECRET_ENV, SECRET)
    httpd, base = _serve(PAGE)
    cache = FlowCache(root=tmp_path / "c")
    try:
        from ultracua.flow import run_cached

        res = await _learn_it(cache, base)
        assert res.cached, "premise: nothing to re-bind if the learn did not cache"

        # `scope=` MATTERS and defaults differently on the two sides: `FlowSpec.scope` is `flow:<name>`
        # while `run_cached`'s parameter defaults to "default", so omitting it computes a DIFFERENT
        # `flow_key` and the replay reports `mode="miss"` — a bindability test that never binds anything
        # and fails for a reason unrelated to its claim. Take it from the spec.
        sp = _spec(base)
        report = await run_cached(url=sp.start_url, goal=sp.goal, scope=sp.scope, provider=None,
                                  cache=cache, mode="replay", headless=True)
        assert report.success, (
            f"the cached recipe no longer binds after redaction — mode={report.mode} note={report.note!r}. "
            f"A leak traded for a broken (or wrong) bind is not a fix.")
        assert report.llm_calls == 0, "and it must still replay at 0-LLM (inviolable #1)"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_redacted_row_identity_is_dropped_not_stored(tmp_path: Path) -> None:
    """Found by auditing the fix, not by the tests above — none of which would have caught it.

    `anchor_id` is compared by EQUALITY against the row identity recomputed from the LIVE page
    (`got == spec.anchor_id`). The live page still holds the real secret, so a stored
    `href:/reveal?api_key=[REDACTED]` can never match: the row guard would refuse the bind on every
    replay, permanently, for a reason no error message explains. Storing `None` instead is this module's
    own documented answer for "no candidate discriminates" — a fabricated identity "makes the guard claim
    protection it does not provide".

    The alternative that keeps the guard working — scrub both sides at compare time — is REJECTED here on
    purpose: it makes two DIFFERENT secrets compare equal, which trades a loud refusal for a possible
    silent wrong-row bind. Worse on this project's ranking.
    """
    from ultracua.locators import redact_spec_fields

    raw = {"role": "button", "name": f"Copy {SECRET}", "tag": "button",
           "anchor": f"Production key {SECRET} details Copy", "anchor_source": "row",
           "anchor_id": f"href:/reveal?api_key={SECRET}", "elem_id": "copy"}
    out = redact_spec_fields(raw, (SECRET,))

    assert out["anchor_id"] is None, "a redacted identity must be dropped, never stored"
    assert out["anchor"] is None and out["anchor_source"] is None, "and the anchor pair goes with it"
    assert SECRET not in out["name"] and out["elem_id"] == "copy", (
        "while the matchable tiers are still scrubbed and the untouched ones survive")

    # The control: with no secret present, nothing is dropped — the guard keeps working for every
    # ordinary flow, which is the half a "drop it all" fix would quietly destroy.
    clean = {**raw, "anchor_id": "href:/reveal?id=42", "anchor": "Production key details Copy",
             "name": "Copy key"}
    assert redact_spec_fields(clean, (SECRET,)) == clean


def test_redacted_is_never_a_bindable_name(tmp_path: Path) -> None:
    """A guard against the lazy fix. Replacing the whole `name` with `[REDACTED]` satisfies the two leak
    tests above and produces a recipe that cannot resolve — the failure mode the bindability test exists
    to catch, stated here as an explicit rule so nobody re-derives it as a surprise.
    """
    assert REDACTED not in PAGE, (
        "the fixture page must not itself contain the redaction marker, or the bindability test could "
        "pass for the wrong reason")
