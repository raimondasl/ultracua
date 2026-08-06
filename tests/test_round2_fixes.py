"""Round 2's eight remaining findings — mostly holes in round 1's own fixes.

The re-audit that found these was scoped to the ~1059 lines the round-1 fixes ADDED, and the result is the
pattern CLAUDE.md names, striking the changes written to prevent it. The sharpest example is R6: the
`is_write_flow` helper was extracted *specifically* so the write predicate would stop existing in
triplicate, and it left a fourth copy in `run_all` — the unattended cron driver.

  R3   the heal's wire guard read `wrote["hit"]` with a ZERO-WIDTH window, so a deferred POST walked past it
  R4   the wire promotion credited the WRONG step: `cur["i"]` was overwritten by the next act, so a
       still-in-flight write from step i landed on step i+1
  R5   `record()`'s confirm probe checks the ENTRY page; replay's baseline checks the PRE-FIRST-WRITE page
  R6   `run_all` never got `is_write_flow`, so an UNDECLARED write ran on every scheduled tick
  R7   the LLM extractor read raw `page.inner_text("body")` — `redact` never reached it
  R8   a write the code KNOWS landed was never ledgered, and the CLI then told the operator to resume it
  R9   `redact` covered 2 of the 5 page-derived fields the prompt renders
  R10  a TRANSIENT read error destroyed a healthy meta sidecar
"""

from __future__ import annotations

import asyncio
import http.server
import json
import threading
from pathlib import Path

import pytest

import ultracua.flows as flowsmod
from ultracua.browser import BrowserSession
from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key
from ultracua.flows import (
    FlowMeta,
    FlowSpec,
    MutateSpec,
    WriteReadbackError,
    _load_meta,
    _meta_path,
    _save_meta,
    approve,
    fleet_verdict,
    is_write_flow,
    record,
    run_all,
    save_spec,
)
from ultracua.locators import LocatorSpec
from ultracua.snapshot import REDACTED


# ==================== R6: run_all, the fourth transcription ====================

def _read_spec_with_a_mutating_step(tmp_path: Path, name: str):
    """A flow LEARNED AS A READ (`spec.mutate is None`) whose cached step mutates — precisely what the wire
    promotion now produces for a formless JS fetch-POST behind a bland name."""
    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name=name, start_url="http://127.0.0.1:9/panel", goal="open the panel", headless=True)
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, created_ts=1.0,
                         steps=[CachedStep(intent="open the daily panel", action="click", mutating=True,
                                           locator=LocatorSpec(role="button", name="Continue",
                                                               tag="button"))]))
    return cache, spec, key


async def test_run_all_skips_an_undeclared_write(tmp_path: Path, monkeypatch) -> None:
    """The unattended cron driver. `spec.mutate is None` made `include_writes=False` a no-op, so every
    scheduled tick actuated the commit — with no confirm barrier, because `_preflight_row` derives
    `is_mutate` from `spec.mutate` too."""
    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    cache, spec, key = _read_spec_with_a_mutating_step(tmp_path, "panel")
    save_spec(spec)
    approve(spec, cache=cache)

    runs = await run_all(cache=cache, include_writes=False)
    # NOT RUN is the invariant, and it is unchanged. The STATUS changed in 0.80.0: `failed` here means
    # "refused a run", the same call `_one`'s unreadable-recipe guard already made, because a flow enters
    # this branch with no human act and `skipped` fed neither channel cron watches (R3.9).
    assert [r.status for r in runs] == ["failed"], runs
    assert not runs[0].ok
    assert "UNDECLARED write" in (runs[0].error or "")
    assert fleet_verdict(runs).exit_code != 0, "and it must reach cron, not just the console"


async def test_run_all_skips_an_undeclared_write_even_with_include_writes(tmp_path: Path,
                                                                         monkeypatch) -> None:
    """`--include-writes` is consent to run writes that can be VERIFIED. With no `spec.mutate` there is no
    confirm barrier at all, so replay cannot tell whether the write landed — `run_batch` already refuses
    this, and the scheduled fleet must too."""
    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    cache, spec, key = _read_spec_with_a_mutating_step(tmp_path, "panel2")
    save_spec(spec)
    approve(spec, cache=cache)

    runs = await run_all(cache=cache, include_writes=True)
    assert [r.status for r in runs] == ["failed"], runs      # see the note above on the 0.80.0 status
    assert not runs[0].ok
    assert "UNDECLARED write" in (runs[0].error or "")
    assert fleet_verdict(runs).exit_code != 0


def test_the_write_predicate_has_one_definition(tmp_path: Path) -> None:
    """The consolidation itself. A read flow with a mutating cached step IS a write, whatever the spec says
    — that is the whole point of `is_write_flow`, and the reason a fourth copy was a defect."""
    cache, spec, key = _read_spec_with_a_mutating_step(tmp_path, "pred")
    assert is_write_flow(spec, cache.get(key)) is True
    plain = CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, created_ts=1.0,
                       steps=[CachedStep(intent="click", action="click", mutating=False)])
    assert is_write_flow(spec, plain) is False


# ==================== R8: a write we KNOW landed must arm the ledger ====================

def test_only_a_known_landed_write_arms_the_ledger() -> None:
    """`WriteReadbackError` means the confirm PASSED and only the readback missed — certain. The others are
    a maybe, and `ledger.py`'s invariant is "never a false skip of an un-landed write", so a maybe must
    stay unrecorded and take a keyed retry instead."""
    assert WriteReadbackError.landed is True
    assert flowsmod.WriteUnverifiedError.landed is False    # the commit actuated, but we do NOT know
    assert flowsmod.DriftError.landed is False
    assert flowsmod.FlowReplayError.landed is False
    # ...and neither may ever be retried automatically.
    assert WriteReadbackError.retryable is False
    assert flowsmod.WriteUnverifiedError.retryable is False


# ==================== R5: the confirm that was already true ====================

def _serve_pre_true(hits: list):
    """A checkout whose confirm signal (`/review` in the URL) is already true on the page where the commit
    happens — the multi-page shape `record()`'s ENTRY-page probe structurally cannot see."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/":
                self._send("<h1>Cart</h1><a href='/review'>Review</a>")
            else:
                self._send("<h1>Review</h1><form method='post' action='/review/place'>"
                           "<button>Place the order</button></form>")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            hits.append(self.path)
            self._send("<h1>Review</h1><p>Done</p>")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_a_commit_whose_confirm_was_already_true_is_not_retryable(tmp_path: Path) -> None:
    """The commit actuated and the confirm cannot report on it. That is NOT drift — drift means the run
    never got where it meant to. It must be its own non-retryable kind, so neither auth-refresh nor
    relearn can fire it a second time, and the message must say the write may have landed."""
    hits: list = []
    httpd, base = _serve_pre_true(hits)
    cache = FlowCache(root=tmp_path / "c")
    spec = FlowSpec(name="prtrue", start_url=f"{base}/", goal="place the order", headless=True,
                    mutate=MutateSpec(confirm_url_contains="/review"))

    async def _demo(page) -> None:
        await page.get_by_role("link", name="Review").click()
        await page.get_by_role("button", name="Place the order").click()

    try:
        res = await record(spec, demo=_demo, headless=True, cache=cache)
        # The entry page is `/`, where `/review` is NOT in the URL — so the authoring probe passes, which
        # is exactly the gap. Pinning it keeps the test honest if the probe is ever extended.
        assert res.cached is True, res.note
        approve(spec, cache=cache)
        del hits[:]

        with pytest.raises(flowsmod.WriteUnverifiedError) as ei:
            await flowsmod.replay(spec, cache=cache)
        assert ei.value.retryable is False
        assert ei.value.landed is False              # a maybe -> the ledger must NOT be armed
        assert "ACTUATED" in str(ei.value)
        assert "DO NOT simply re-run" in str(ei.value)
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== R7 + R9: every channel that reaches the model ====================

_TOKEN = "tok_live_51ABCDEFsecret"


async def test_the_llm_extractor_never_receives_a_resolved_secret(tmp_path: Path) -> None:
    """The extractor is a page-text -> LLM path that did NOT run through `snapshot.capture(redact=...)`,
    and unlike the heal it needs no drift: every replay of an unpinned read flow makes this call. The same
    channel was already scrubbed before it hit DISK by `_capture_audit` — never before the MODEL."""
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(f"<body><p>Your key is {_TOKEN} — keep it safe.</p></body>")
        text = await flowsmod._redacted_body_text(session, (_TOKEN,))
        assert _TOKEN not in text
        assert REDACTED in text
    finally:
        await session.close()


async def test_every_rendered_field_is_scrubbed_and_the_fingerprint_is_stable() -> None:
    """`_render` puts FIVE page-derived fields in the user turn. Scrubbing two left three open — and an
    accessible NAME carries the plaintext on the standard "Copy sk-live-…" button, while a token in a
    query string rides `url`, which is rendered in EVERY prompt.

    The fingerprint must not move: `name` and `url` are both in its basis, so a naive fix would
    manufacture drift on every secret-bearing page and fail the mutation gate for no reason."""
    session = await BrowserSession(headless=True).start()
    try:
        html = (f"<title>key {_TOKEN}</title><body>"
                f"<button>Copy {_TOKEN}</button>"
                f"<input aria-label='t' value='{_TOKEN}'>"
                f"<p>echoed {_TOKEN}</p></body>")
        await session.page.goto(f"data:text/html,<html>{html}</html>")

        session.redact = ()
        plain = await session.snapshot()
        session.redact = (_TOKEN,)
        clean = await session.snapshot()

        blob = json.dumps([clean.url, clean.title, clean.text,
                           [[e.name, e.value] for e in clean.elements]])
        assert _TOKEN not in blob, blob[:400]
        assert any(REDACTED in (e.name or "") for e in clean.elements)   # the accessible-name channel
        assert plain.fingerprint == clean.fingerprint                    # ...and no phantom drift
    finally:
        await session.close()


# ==================== R10: a transient read error must not destroy a healthy sidecar ====================

async def test_a_transient_read_error_leaves_the_sidecar_intact(tmp_path: Path, monkeypatch) -> None:
    """On Windows an AV/indexer sharing violation moments after `os.replace` is indistinguishable from a
    torn file at the `except` — and treating it as permanent DESTROYED a healthy sidecar on the first
    occurrence, silently dropping its contracts, shape and 0-LLM read pin."""
    cache = FlowCache(root=tmp_path / "c")
    Path(cache.root).mkdir(parents=True, exist_ok=True)
    _save_meta(cache, "k", FlowMeta(approved=True, shape={"t": "number"}, read_pin={"sel": "#x"}))

    calls = {"n": 0}
    real = Path.read_text

    def _flaky(self, *a, **kw):
        if self.name.endswith("k.meta.json"):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(32, "used by another process")   # transient — the retry must win
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _flaky)
    meta = _load_meta(cache, "k")

    assert calls["n"] >= 2, "it did not retry"
    assert meta.quarantine is None                 # ...and did not poison a healthy flow
    assert meta.approved is True and meta.shape == {"t": "number"} and meta.read_pin == {"sel": "#x"}
    assert not list(Path(cache.root).glob("k.meta.json.corrupt.*"))   # nothing was renamed aside


async def test_a_persistently_unreadable_sidecar_still_refuses_but_is_not_destroyed(
    tmp_path: Path, monkeypatch
) -> None:
    """We never saw its bytes, so we cannot claim corruption. Refuse the run, leave the file for the next
    one — or a human — to read."""
    cache = FlowCache(root=tmp_path / "c")
    Path(cache.root).mkdir(parents=True, exist_ok=True)
    _save_meta(cache, "k", FlowMeta(approved=True))
    before = _meta_path(cache, "k").read_bytes()

    real = Path.read_text

    def _always(self, *a, **kw):
        if self.name.endswith("k.meta.json"):
            raise OSError(32, "used by another process")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _always)
    meta = _load_meta(cache, "k")

    assert meta.quarantine is not None             # this run refuses, loudly
    monkeypatch.undo()
    assert _meta_path(cache, "k").read_bytes() == before      # ...and the file is untouched
    assert not list(Path(cache.root).glob("k.meta.json.corrupt.*"))


async def test_a_genuinely_corrupt_sidecar_is_still_quarantined(tmp_path: Path) -> None:
    """The control: real corruption must NOT be softened by the retry. The bytes are there and they are
    not JSON, so a re-read would return the same thing — quarantine immediately, preserving the evidence."""
    cache = FlowCache(root=tmp_path / "c")
    Path(cache.root).mkdir(parents=True, exist_ok=True)
    _save_meta(cache, "k", FlowMeta(approved=True))
    _meta_path(cache, "k").write_bytes(b"\x00" * 32)

    meta = _load_meta(cache, "k")
    assert meta.quarantine is not None and meta.quarantine["code"] == "meta_unreadable"
    assert len(list(Path(cache.root).glob("k.meta.json.corrupt.*"))) == 1


# ==================== R3 + R4: the write signal's TIMING ====================

def _serve_deferred(hits: list, defer_ms: int, second_button: bool = False):
    """`#go` fires its POST after `defer_ms` — a debounce, an autosave tick, an awaited round-trip.
    `#benign` (optional) does nothing at all and exists only to be the NEXT step."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            extra = "<button id='benign' type='button'>Next thing</button>" if second_button else ""
            body = (f"<h1>Panel</h1><button id='go' type='button'>Continue</button>{extra}"
                    "<p id='out'></p><script>"
                    "document.getElementById('go').addEventListener('click', function () {"
                    f"  setTimeout(function () {{ fetch('/save', {{method:'POST'}}); }}, {defer_ms});"
                    "  document.getElementById('out').textContent = 'clicked';"
                    "});</script>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_the_heal_waits_for_a_deferred_write_before_judging(tmp_path: Path) -> None:
    """R3. Reading `wrote["hit"]` the instant `act` returns is a zero-width window. Both siblings this
    guard mirrors DO wait — the learn watcher through a `write_window_ms` grace tail, `_replay_step` via
    `expect_request(timeout=write_settle_ms)`. Without the wait, a 25 ms debounce walked straight past and
    the write control was persisted as a READ, to be re-fired ungated on every later 0-LLM replay."""
    from ultracua.flow import _maybe_heal
    from ultracua.providers.scripted import ScriptedProvider
    from ultracua.timing import StepTrace

    hits: list = []
    httpd, base = _serve_deferred(hits, defer_ms=25)
    session = await BrowserSession(headless=True).start()
    try:
        await session.goto(base + "/")
        step = CachedStep(intent="open the daily report", action="click", mutating=False,
                          locator=LocatorSpec(role="link", name="Daily report", tag="a"))
        ok, note, _did = await _maybe_heal(
            session, step, ScriptedProvider([{"action": "click", "role": "button", "name": "Continue",
                                              "intent": "open the daily report"}]),
            StepTrace(index=0), "open the daily report", "drift")

        assert hits, "the fixture did not POST; this test would prove nothing"
        assert ok is False
        assert "WRITE on the wire" in note
        assert step.locator.name == "Daily report"       # NOT re-pointed at the write control
    finally:
        await session.close()
        httpd.shutdown()
        httpd.server_close()


def test_a_deferred_write_is_never_credited_to_the_following_step() -> None:
    """R4, tested as the RULE rather than as a race.

    `cur["i"]` was overwritten by the next act, so a write still in flight from step i was credited to
    step i+1 — gating a benign neighbour while the real commit cached as a read, AND disarming
    `_learn_once`'s "wrote but nothing attributed" refusal because `any(s.mutating)` had become true.
    MEASURED end to end before the fix, with a 50 ms debounce: `[('Continue', False), ('Next thing', True)]`
    — the gate on the wrong step. After: `[('Continue', False), ('Next thing', False)]`, i.e. nobody, which
    makes the flow fail loud.

    That end-to-end reproduction is inherently timing-dependent (at 150 ms the session closes before the
    POST even fires), so a browser-level test of it would prove nothing on a fast or loaded machine. The
    decision itself is pure, and this pins it exactly."""
    from ultracua.flow import _write_owner

    assert _write_owner(True, 3, set()) == 3            # acting, nothing else live -> its own write
    assert _write_owner(True, 3, {3}) == 3              # its OWN tail is not a second candidate
    assert _write_owner(False, -1, {3}) == 3            # settled into its own tail after the act
    # THE FIX: step 4 is acting while step 3's tail is still live. Two candidates, no way to choose.
    assert _write_owner(True, 4, {3}) == -1
    assert _write_owner(False, -1, {3, 4}) == -1        # ...and likewise once both have closed
    assert _write_owner(False, -1, set()) == -1         # nothing live -> background noise, not ours
