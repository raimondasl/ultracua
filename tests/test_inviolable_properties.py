"""Inviolables #1 and #2 as PROPERTIES, not as point assertions on happy paths (S14 / H4).

Inviolable #3 has had a property file since S1. #1 and #2 had ten files asserting `llm_calls == 0`
somewhere — all on paths that were already succeeding — and nothing anywhere saying that a replay CANNOT
reach a provider, or that a failure always carries a reason. The census that found this (H4) also named
where such a gap would live: "the recovery paths (heal / suffix-replan / auth-refresh / drift ladder)".

FIRST, THE INVARIANT HAD TO BE STATED CORRECTLY, because the obvious phrasing is false. "Replay never
calls an LLM" is not what this system does: `flows.replay` takes `provider=` and `router=`, an unpinned
extraction builds a router by design, and `flow.py`'s own comment says "a pure replay makes no LLM call
and reports zeros; a HEAL or suffix-replan does". The real claim, from `replay`'s docstring, is 0-LLM
**navigation** — and the shapes it names: navigate-only reads, PINNED reads, and writes whose confirm is
selector/url/text based. A blanket "no LLM in replay" property would have gone red for correct behaviour,
which is the failure mode this file exists to avoid on other people's behalf.

WHAT THE PROVIDER STUB DOES, AND WHY IT HAS AN ALLOWLIST. `_Exploding.decide` raises, and constructing a
provider or router raises too (`build_router` / `get_provider` are patched), so an LLM is unreachable in
BOTH directions — call and construction. `.router` is allowed to read as None because `flow.py` reads it
for token accounting on every run; exploding there would fire on bookkeeping rather than on an LLM call,
and a probe that cannot tell those apart proves nothing.
"""
from __future__ import annotations

import http.server
import threading
import time
from pathlib import Path

import pytest

from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.locators import LocatorSpec

pytestmark = pytest.mark.asyncio


class LLMWasReached(AssertionError):
    """Raised from the stub, so a violation names itself instead of surfacing as a TypeError."""


class _Exploding:
    """A provider that cannot be used without saying so."""

    def __init__(self) -> None:
        self.router = None          # read by flow.py for token accounting; see the module docstring

    async def decide(self, *a, **kw):
        raise LLMWasReached("a replay reached provider.decide() — inviolable #1")

    def __getattr__(self, name):
        raise LLMWasReached(f"a replay touched provider.{name} — inviolable #1")


@pytest.fixture()
def no_llm(monkeypatch: pytest.MonkeyPatch):
    """Make an LLM unreachable by CONSTRUCTION as well as by call."""
    import ultracua.providers as providers

    def _boom(*a, **kw):
        raise LLMWasReached(f"a replay constructed an LLM client: {a!r}")

    monkeypatch.setattr(providers, "build_router", _boom, raising=False)
    monkeypatch.setattr(providers, "get_provider", _boom, raising=False)
    return _Exploding()


_PAGE = ("<h1>Panel</h1>"
         "<button id='c' type='button'>Continue</button>"
         "<a href='#x' id='l'>Daily report</a>")


def _serve(page: str = _PAGE):
    class _H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            b = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _seed(cache: FlowCache, base: str, goal: str, *, name: str, missing: bool = False) -> str:
    key = flow_key(goal, f"{base}/", "default")
    cache.put(CachedFlow(
        key=key, goal=goal, start_url=f"{base}/", created_ts=time.time(),
        steps=[CachedStep(intent=f"click {name}", action="click",
                          locator=LocatorSpec(role="button" if not missing else "button",
                                              name=name, tag="button"))]))
    return key


# ==================== inviolable #1 ====================


@pytest.mark.parametrize("mode", ["replay", "repair"])
async def test_a_navigate_only_replay_never_reaches_a_provider(
        mode: str, tmp_path: Path, no_llm) -> None:
    """#1a. The shapes the docstring calls 0-LLM must not touch a provider even when one is HANDED to
    them. `repair` is included deliberately: it is an undocumented public mode that keeps the heal
    provider, so it is exactly where an accidental call would hide."""
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        _seed(cache, base, "work the panel", name="Continue")
        report = await run_cached(url=f"{base}/", goal="work the panel", provider=no_llm,
                                  cache=cache, mode=mode, headless=True)
        assert report.llm_calls == 0, f"{mode}: a 0-LLM shape reported {report.llm_calls} LLM call(s)"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_DRIFTED_replay_fails_loud_rather_than_reaching_for_an_llm(
        tmp_path: Path, no_llm) -> None:
    """#1b. Drift is where the recovery ladder lives, and the ladder is the one part of replay that may
    legitimately use an LLM. With `mode="replay"` it may not: it must fail, loudly, with its reason."""
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        _seed(cache, base, "work the panel", name="Nonexistent", missing=True)
        report = await run_cached(url=f"{base}/", goal="work the panel", provider=no_llm,
                                  cache=cache, mode="replay", headless=True)
        assert report.success is False, "a step whose locator is absent must not report success"
        assert report.llm_calls == 0
    finally:
        httpd.shutdown()
        httpd.server_close()


# The documented set. `flow.run_cached`'s signature comment says "auto" | "learn" | "replay"; the body
# also accepts "repair". Anything else is a caller mistake — a typo, a stale client, a wire value.
_UNRECOGNISED = ["bogus", "REPLAY", "Replay", "replay ", "", "reply", "auto2"]


@pytest.mark.parametrize("mode", _UNRECOGNISED)
async def test_an_unrecognised_mode_is_REFUSED_and_never_silently_becomes_a_learn(
        mode: str, tmp_path: Path, no_llm) -> None:
    """THE PROPERTY THAT FOUND R4.31, and it breaks all three inviolables at once.

    `run_cached`'s control flow tests `mode in ("auto", "replay", "repair")` and then
    `mode in ("replay", "repair")`; an unrecognised string matches NEITHER and falls through to the
    learn path. Measured with a provider present — the daemon's normal state, and the daemon passes
    `params.get("mode", "auto")` straight from JSON-RPC without validating it:

        mode="bogus"   -> report.mode='learn', llm_calls=2, and the cached flow's write FIRED AGAIN
        mode="REPLAY"  -> identical. A case typo re-authors the flow and re-places the order.

    So a caller asking to replay gets: an LLM call (#1), something other than what it asked for and no
    reason given (#2), and a repeated write (#3). The refusal must be positive — an unknown mode is a
    caller error, and guessing which mode they meant is how this shipped in the first place.
    """
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        _seed(cache, base, "work the panel", name="Continue")
        try:
            report = await run_cached(url=f"{base}/", goal="work the panel", provider=no_llm,
                                      cache=cache, mode=mode, headless=True)
        except (ValueError, LLMWasReached) as exc:
            if isinstance(exc, LLMWasReached):
                pytest.fail(f"mode={mode!r} reached an LLM: {exc}")
            assert mode in str(exc) or "mode" in str(exc).lower(), (
                f"a refusal must name the bad input; got {exc}")
            return                                  # refusing loudly is the correct outcome
        assert report.mode != "learn", (
            f"mode={mode!r} silently became a LEARN (llm_calls={report.llm_calls}). A caller that asked "
            f"for something unrecognised must be told, not re-authored — and for a write flow this "
            f"re-performs the commit (R4.31).")
        assert report.note, f"mode={mode!r} failed with an EMPTY note"
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== inviolable #2 ====================


async def test_every_failed_report_carries_a_REASON(tmp_path: Path, no_llm) -> None:
    """#2 on the engine surface. `FlowReport.note` is the documented reason field; a caller that checks
    it on a failure currently gets `''` while the real cause sits in `traces[-1].meta['note']`
    ("locator unresolved or ambiguous (drift)"). Loud-but-unexplained is better than silent, and worse
    than the contract this project states.
    """
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        _seed(cache, base, "work the panel", name="Nonexistent", missing=True)
        report = await run_cached(url=f"{base}/", goal="work the panel", provider=no_llm,
                                  cache=cache, mode="replay", headless=True)
        assert report.success is False, "premise: this fixture must fail, or the cell proves nothing"
        assert report.note, (
            "a failed FlowReport carries no reason. The cause exists — "
            f"traces[-1].meta={[t.meta for t in report.traces][-1:]!r} — but `report.note`, the field a "
            f"caller reads, is empty.")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_replay_with_no_cached_flow_says_so(tmp_path: Path, no_llm) -> None:
    """The control for the cell above: this failure shape already carries its reason, so a fix for the
    empty note must not be 'set a note everywhere' — it must preserve the ones that are already true."""
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        report = await run_cached(url=f"{base}/", goal="never learned", provider=no_llm,
                                  cache=cache, mode="replay", headless=True)
        assert report.success is False and report.note, "a cache miss must explain itself"
        assert "cached" in report.note.lower()
    finally:
        httpd.shutdown()
        httpd.server_close()
