"""Secrets must not reach DISK, and must not reach the MODEL (B1 / B2).

This project's stated rule is that credentials are env-resolved and "never persisted, logged or written to
disk". Two places broke it, both by capturing a value with no idea it was sensitive.

B1 — AT REST. The recorder's change listener excluded only checkbox and radio, so `input[type=password]`
was stored with `el.value`. The plaintext landed in the flow cache JSON, `flow inspect` echoed it verbatim,
`flow approve --all` reprinted it fleet-wide, and every replay re-typed the frozen password. The recorder's
own comment names this exact string "a password / token / PII" and redacts it — but only from the LLM
captioner, not from disk. There are TWO capture sites (the `change` listener and the Enter-keydown sibling
that handles a form with no submit control); fixing one leaves the other leaking.

The fix has to live in the JS: neither `domainOf` nor `specOf` records an input's `type`, so by the time
the event reaches Python the fact that this was a password field is already gone. And it REDACTS rather
than DROPS — dropping the step would silently omit a fill and replay would submit an empty credential,
which trades a privacy bug for a wrongness one.

B2 — IN THE PROMPT. `SNAPSHOT_JS` captured every input's value with no type filter, and the observation
renderer put it in the user turn. Off the 0-LLM path (it needs a heal or a suffix-replan while the secret
is still on the page), but `Observation` is documented as "sanitized" and `SlotSpec` promises a secret is
"NEVER passed in params, logged, or serialized". Neither was true. Note this needs BOTH halves: masking
password fields cannot see a `$secret_env` API token typed into a plain text input.

The guard already existed on sibling paths, which is this codebase's recurring shape: `recorder.py` redacts
a `type` step's literal before handing it to the captioner, and `verifiers.py` renders elements without
`value` at all. Neither was ever applied to the observation renderer.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

import ultracua.cli as cli
from ultracua.browser import BrowserSession
from ultracua.cache import CachedStep, FlowCache, flow_key
from ultracua.flow import _maybe_heal
from ultracua.flows import FlowSpec, MutateSpec, SlotSpec, record
from ultracua.locators import LocatorSpec
from ultracua.providers.llm_agent import _render
from ultracua.recorder import record_demo
from ultracua.timing import StepTrace
from ultracua.types import Action

SECRET = "hunter2-SUPER-SECRET-TOKEN"

_PAGES = {
    # A form WITH a submit button -> the `change` listener captures the fields.
    "/login": ("<form action='/done' method='post'>"
               "<input id='u' name='u' aria-label='username'>"
               "<input id='p' name='p' type='password' aria-label='password'>"
               "<button>Sign in</button></form>"),
    # A form with NO submit control -> Enter takes the KEYDOWN path instead. This is the sibling that
    # stays open if only the change listener is fixed.
    "/loginenter": ("<form action='/done' method='post'>"
                    "<input id='u' name='u' aria-label='username'>"
                    "<input id='p' name='p' type='password' aria-label='password'></form>"),
    "/done": "<h1>Signed in</h1>",
}


def _serve():
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            self._send(_PAGES.get(self.path.split("?")[0], "<h1>not found</h1>"))

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self._send(_PAGES["/done"])

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _no_plaintext_anywhere(root: Path) -> None:
    for p in root.rglob("*"):
        if p.is_file():
            try:
                assert SECRET not in p.read_text(encoding="utf-8", errors="ignore"), f"leaked in {p}"
            except (OSError, UnicodeDecodeError):
                pass


# ==================== B1. the password never reaches disk ====================

async def _demo_click(page) -> None:
    await page.get_by_label("username").fill("alice")
    await page.get_by_label("password").fill(SECRET)
    await page.get_by_role("button", name="Sign in").click()


async def _demo_enter(page) -> None:
    await page.get_by_label("username").fill("alice")
    await page.get_by_label("password").fill(SECRET)
    await page.get_by_label("password").press("Enter")


@pytest.mark.parametrize("path,demo", [("/login", _demo_click), ("/loginenter", _demo_enter)],
                         ids=["change-listener", "enter-keydown-sibling"])
async def test_a_demoed_password_never_reaches_the_flow_cache(tmp_path: Path, path, demo) -> None:
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        flow, *_ = await record_demo(f"{base}{path}", demo, goal="sign in", cache=cache, headless=True,
                                     mutate=True)
        _no_plaintext_anywhere(tmp_path)

        # REDACTED, not DROPPED: the step still exists, so replay still knows the field must be filled.
        types = [s for s in flow.steps if s.action == "type"]
        by_name = {(s.locator.name if s.locator else None): s for s in types}
        assert "username" in by_name and "password" in by_name, [s.locator for s in types]
        assert by_name["username"].text == "alice"      # an ordinary field is untouched
        assert by_name["username"].secret is False
        assert by_name["password"].text == ""           # ...the credential is blank
        assert by_name["password"].secret is True       # ...and marked, so "" reads as a redaction
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_record_refuses_a_flow_with_an_unbound_credential_field(tmp_path: Path) -> None:
    """The step would replay an EMPTY credential, so caching it would just move the failure somewhere
    confusing. Refuse at authoring, with the exact spec to add."""
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name="signin", start_url=f"{base}/login", goal="sign in", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Signed in"))
    try:
        res = await record(spec, demo=_demo_click, headless=True, cache=cache)
        assert res.cached is False
        assert "CREDENTIAL" in res.note and "secret_env" in res.note
        assert cache.get(flow_key(spec.goal, spec.start_url, spec.scope)) is None
        _no_plaintext_anywhere(tmp_path)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_the_declared_secret_slot_workflow_still_works(tmp_path: Path, monkeypatch) -> None:
    """The control: the SUPPORTED route must not be broken by the refusal above."""
    monkeypatch.setenv("ULTRACUA_TEST_PW", SECRET)
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name="signin-ok", start_url=f"{base}/login", goal="sign in", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Signed in"),
                    slots={"password": SlotSpec(type="string", required=True, secret=True,
                                                secret_env="ULTRACUA_TEST_PW")})
    try:
        res = await record(spec, demo=_demo_click, headless=True, cache=cache,
                           writable_slots=["password"])
        assert res.cached is True, res.note
        _no_plaintext_anywhere(tmp_path)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_record_refuses_to_launder_a_credential_into_a_plain_slot(tmp_path: Path) -> None:
    """A non-secret slot would make the value an ordinary tool argument — advertised in the MCP input
    schema, accepted in `params`, echoed by `flow inspect`. That is exactly what `secret=True` prevents."""
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name="launder", start_url=f"{base}/login", goal="sign in", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Signed in"),
                    slots={"password": SlotSpec(type="string", required=True)})   # NOT secret
    try:
        res = await record(spec, demo=_demo_click, headless=True, cache=cache,
                           writable_slots=["password"])
        assert res.cached is False
        assert "CREDENTIAL" in res.note
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_step_renderer_masks_a_credential_field() -> None:
    """Defence in depth — this renderer feeds BOTH `flow inspect` and `flow approve --all`, which reprints
    fleet-wide. It must never be the thing that echoes a secret out of an older cache file or a hand-edit."""
    spec = FlowSpec(name="x", start_url="http://x/", goal="g")
    step = CachedStep(intent="type the password", action="type", text=SECRET, secret=True,
                      locator=LocatorSpec(role="textbox", name="password", tag="input"))
    out = cli._step_line(1, step, spec)
    assert SECRET not in out
    assert 'text="***"' in out
    assert "SECRET" in out            # shown, not hidden: an approver needs to know the field is there


def test_the_step_renderer_still_shows_ordinary_values() -> None:
    """The control: an over-broad mask would blind the human the approval gate depends on."""
    spec = FlowSpec(name="x", start_url="http://x/", goal="g")
    step = CachedStep(intent="type the city", action="type", text="Vilnius",
                      locator=LocatorSpec(role="textbox", name="city", tag="input"))
    assert "'Vilnius'" in cli._step_line(1, step, spec)


# ==================== B2. the secret never reaches the model ====================

_LEAK_HTML = """<!doctype html><html><body><form method='post'>
  <input id='u' aria-label='username'>
  <input id='p' type='password' aria-label='password'>
  <input id='t' type='text' aria-label='API token'>
  <button>Continue</button>
</form></body></html>"""

_PLAIN_SECRET = "tok_live_51ABCDEF"


class _PromptSpy:
    """A provider that records the EXACT prompt the renderer produced, then declines."""

    def __init__(self) -> None:
        self.prompt = ""

    async def decide(self, goal, obs, history):
        self.prompt = _render(obs, goal, history)
        return Action(action="give_up", intent="spy"), None


async def test_no_secret_reaches_the_rendered_prompt() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(_LEAK_HTML)
        await session.page.fill("#u", "alice")
        await session.page.fill("#p", SECRET)
        await session.page.fill("#t", _PLAIN_SECRET)
        # What `flows._secret_values(spec)` would produce for a spec with a `$secret_env` slot.
        session.redact = (_PLAIN_SECRET,)

        spy = _PromptSpy()
        step = CachedStep(intent="click Continue", action="click", mutating=False,
                          locator=LocatorSpec(role="link", name="Gone", tag="a"))
        await _maybe_heal(session, step, spy, StepTrace(index=0), "sign in", "drift")

        assert spy.prompt, "the spy never ran; the assertions below would prove nothing"
        assert SECRET not in spy.prompt                 # masked at the source (password type)
        assert _PLAIN_SECRET not in spy.prompt          # redacted (a token in a PLAIN text input)
        # ...and the agent can still tell the fields are filled, which is what the value is FOR.
        assert 'value="alice"' in spy.prompt            # an ordinary value survives
        assert "[REDACTED]" in spy.prompt
    finally:
        await session.close()


async def test_a_masked_password_still_reads_as_filled() -> None:
    """Emitting `null` for a password would silently regress the agent's "have I already typed this?"
    reasoning into re-typing. The mask must be non-empty when the field is."""
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(_LEAK_HTML)
        await session.page.fill("#p", SECRET)
        obs = await session.snapshot()
        pw = [e for e in obs.elements if (e.type or "") == "password"]
        assert pw, [(e.tag, e.type) for e in obs.elements]
        assert pw[0].value                              # truthy: the agent sees a filled field
        assert SECRET not in (pw[0].value or "")

        await session.page.fill("#p", "")
        obs2 = await session.snapshot()
        pw2 = [e for e in obs2.elements if (e.type or "") == "password"]
        assert pw2[0].value == ""                       # ...and an EMPTY one still reads empty
    finally:
        await session.close()


async def test_redaction_cannot_manufacture_drift() -> None:
    """The fingerprint basis is role/name/tag + url, so redacting a value must not change it. If it could,
    every secret-bearing flow would fail its mutation gate for no reason."""
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(_LEAK_HTML)
        await session.page.fill("#t", _PLAIN_SECRET)

        session.redact = ()
        plain = await session.snapshot()
        session.redact = (_PLAIN_SECRET,)
        scrubbed = await session.snapshot()

        assert plain.fingerprint == scrubbed.fingerprint
        assert _PLAIN_SECRET in (plain.elements[[e.name for e in plain.elements].index("API token")].value or "")
        assert _PLAIN_SECRET not in (
            scrubbed.elements[[e.name for e in scrubbed.elements].index("API token")].value or "")
    finally:
        await session.close()


async def test_a_secret_echoed_as_page_text_is_redacted_too() -> None:
    """`obs.text` is a second channel: browser innerText excludes input values, but a page that echoes a
    token back as visible text would ship it just the same."""
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(f"<p>Your key is {_PLAIN_SECRET} — keep it safe.</p>")
        session.redact = (_PLAIN_SECRET,)
        obs = await session.snapshot()
        assert _PLAIN_SECRET not in obs.text
        assert "[REDACTED]" in obs.text
    finally:
        await session.close()
