"""H3 typed templates, slice 1 — parameterized READ replay (key-less, local fixtures).

Records a read flow (types a value into an echoing input), marks that step as a slot, then replays
with params={...} and asserts the SUBSTITUTED value reached the live page — plus the pre-flight
validation, the read-only guard on write flows, the idempotency slot channel, and slot serialization.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from ultracua import flows
from ultracua.cache import FlowCache, flow_key
from ultracua.flows import (
    FlowReplayError,
    FlowSpec,
    MutateSpec,
    SlotSpec,
    validate_params,
)
from ultracua.safety import idempotency_key


class _EchoSite:
    """Serves one page whose text input echoes each value to the server via a SYNCHRONOUS GET
    (no async race with session close), so `.gets` is the oracle for what value reached the DOM."""

    def __init__(self) -> None:
        self.gets: list[str] = []

    def serve(self):
        site = self
        body = (
            "<!doctype html><html><body>"
            "<label for='q'>code</label><input id='q'>"
            "<script>document.getElementById('q').addEventListener('input', (e) => {"
            " const x = new XMLHttpRequest();"
            " x.open('GET', '/typed-' + encodeURIComponent(e.target.value), false); x.send();"
            "});</script></body></html>"
        )

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_GET(self) -> None:
                site.gets.append(self.path)
                b = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _record_slotted_read(site, base, cache, *, slot_name="code", enum=None):
    """Record a read flow that types 'alpha-7', mark its type step as `slot_name`, approve it."""
    spec = FlowSpec(name="tracking", start_url=base + "/", goal="enter the tracking code", headless=True,
                    slots={slot_name: SlotSpec(type="string", enum=enum)})

    async def _demo(pg) -> None:
        await pg.fill("#q", "alpha-7")
        await pg.locator("#q").blur()   # change fires on blur -> the `type` step is captured

    res = await flows.record(spec, demo=_demo, headless=True, cache=cache)
    assert res.cached, f"record didn't cache: {res.note!r}"
    # Mark the recorded type step as the slot site (slice 1's creation path is manual; slice 1b mines it).
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    flow = cache.get(key)
    typed = next(s for s in flow.steps if s.action == "type")
    typed.slot = slot_name
    cache.put(flow)
    flows.approve(spec, cache=cache)
    return spec


async def test_replay_substitutes_validated_param(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()
    httpd, base = site.serve()
    try:
        spec = await _record_slotted_read(site, base, cache, enum=["alpha-7", "beta-9"])

        # No params -> the FROZEN literal replays (backward compatible).
        site.gets.clear()
        await flows.replay(spec, params=None, cache=cache)
        assert "/typed-alpha-7" in site.gets and "/typed-beta-9" not in site.gets

        # params -> the SUBSTITUTED value reaches the live page (0-LLM), the frozen one does not.
        site.gets.clear()
        await flows.replay(spec, params={"code": "beta-9"}, cache=cache)
        assert "/typed-beta-9" in site.gets, f"substitution didn't reach the page: {site.gets}"
        assert "/typed-alpha-7" not in site.gets, "replayed the frozen literal instead of the param"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_preflight_rejects_out_of_domain_before_browser(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()
    httpd, base = site.serve()
    try:
        spec = await _record_slotted_read(site, base, cache, enum=["alpha-7", "beta-9"])
        site.gets.clear()
        # An out-of-enum value must fail loud BEFORE any page action (0-LLM pre-flight).
        with pytest.raises(FlowReplayError, match="one of"):
            await flows.replay(spec, params={"code": "gamma"}, cache=cache)
        assert site.gets == [], "pre-flight didn't refuse before touching the browser"
        # An unknown param name is refused too.
        with pytest.raises(FlowReplayError, match="unknown param"):
            await flows.replay(spec, params={"typo": "x"}, cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_parameterized_write_is_refused(tmp_path, monkeypatch) -> None:
    # Read-only slice: passing params to a WRITE flow must fail loud (write templates are the next slice),
    # and the refusal must precede any browser work (start_url is never dialed).
    monkeypatch.chdir(tmp_path)
    spec = FlowSpec(name="w", start_url="http://127.0.0.1:9/", goal="submit it",
                    mutate=MutateSpec(confirm_text_contains="Thanks"),
                    slots={"amount": SlotSpec(type="string")})
    with pytest.raises(FlowReplayError, match="WRITE flows aren't supported"):
        await flows.replay(spec, params={"amount": "5"}, cache=FlowCache())


def test_idempotency_key_slot_channel() -> None:
    base = idempotency_key("flow:w", 3, "submit")
    # Same base with no slots -> unchanged (existing single-write flows keep their keys).
    assert idempotency_key("flow:w", 3, "submit", slot_values=None) == base
    # Distinct rows -> distinct keys; same row on retry -> same key; key order doesn't matter.
    r1 = idempotency_key("flow:w", 3, "submit", slot_values={"amt": "10", "who": "a"})
    r2 = idempotency_key("flow:w", 3, "submit", slot_values={"who": "a", "amt": "10"})  # reordered
    r3 = idempotency_key("flow:w", 3, "submit", slot_values={"amt": "20", "who": "a"})
    assert r1 == r2 and r1 != r3 and r1 != base


def test_slot_spec_round_trips(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    spec = FlowSpec(name="rt", start_url="http://x/", goal="g",
                    slots={"q": SlotSpec(type="string", enum=["a", "b"], max_length=5),
                           "n": SlotSpec(type="integer", min=1, max=9, required=False)})
    flows.save_spec(spec)
    loaded = flows.load_spec("rt")
    assert isinstance(loaded.slots["q"], SlotSpec) and loaded.slots["q"].enum == ["a", "b"]
    assert loaded.slots["n"].type == "integer" and loaded.slots["n"].min == 1 and loaded.slots["n"].required is False


def test_validate_params_secret_from_env(monkeypatch) -> None:
    spec = FlowSpec(name="s", start_url="http://x/", goal="g",
                    slots={"token": SlotSpec(secret=True, secret_env="MY_TOKEN")})
    # A secret slot resolves from the env, and must NOT be passed in params.
    monkeypatch.setenv("MY_TOKEN", "s3cr3t")
    assert validate_params(spec, {}) == {"token": "s3cr3t"}
    with pytest.raises(FlowReplayError, match="must not be passed in params"):
        validate_params(spec, {"token": "x"})
    monkeypatch.delenv("MY_TOKEN")
    with pytest.raises(FlowReplayError, match="needs env var"):
        validate_params(spec, {})
