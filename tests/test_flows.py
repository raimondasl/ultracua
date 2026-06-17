"""Flow spec + reusable extraction: define a task once, learn it, replay it returning data.

Uses a local two-page fixture + a scripted agent provider + a MockClient extraction router — no
live LLM, no network. See ROADMAP.md / src/ultracua/flows.py.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

from ultracua.cache import FlowCache
from ultracua.extract import extract
from ultracua.flows import FlowReplayError, FlowSpec, approve, learn, replay
from ultracua.llm.base import Router, Tier
from ultracua.llm.mock import MockClient
from ultracua.types import Action


def _extract_router(*datas) -> Router:
    """A Router whose successive extraction calls return {found: True, data: <each>}."""
    mc = MockClient(actions=[{"found": True, "data": d} for d in datas], tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


class _ClickFirstLink:
    """Scripted agent: click the first link once (navigating), then done."""

    def __init__(self) -> None:
        self.clicked = False

    async def decide(self, goal, obs, history):
        if not self.clicked:
            for el in obs.elements:
                if el.role == "link":
                    self.clicked = True
                    return Action(action="click", intent="open the answer page", ref=el.ref), None
        return Action(action="done", intent="done"), None


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a) -> None:
        pass


def _serve(directory: Path):
    handler = functools.partial(_QuietHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# --- extraction (pure, MockClient) ------------------------------------------------------------
async def test_extract_returns_structured_data() -> None:
    ex = await extract(_extract_router([{"month": "Jan", "count": 12}]), "monthly counts", "Jan: 12")
    assert ex.found and ex.data == [{"month": "Jan", "count": 12}]


async def test_extract_unwraps_spurious_nesting() -> None:
    ex = await extract(_extract_router([["000000299"]]), "the order id", "order 000000299")
    assert ex.data == ["000000299"]


async def test_extract_empty_page_is_not_found() -> None:
    ex = await extract(_extract_router("x"), "anything", "")
    assert not ex.found and ex.error


# --- flow learn -> replay (browser + scripted provider) ---------------------------------------
def _write_fixture(d: Path) -> None:
    (d / "page1.html").write_text(
        "<html><body><h1>Home</h1><a href='page2.html'>see the answer</a></body></html>", encoding="utf-8"
    )
    (d / "page2.html").write_text(
        "<html><body><h1>Answer</h1><p>The answer is 42.</p></body></html>", encoding="utf-8"
    )


async def test_flow_learn_then_replay_returns_data(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="answer", start_url=f"{base}/page1.html",
                    goal="open the answer page", extract="the answer number", headless=True)
    try:
        res = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        assert res.cached and res.found and res.data == 42 and res.steps  # learned a replayable flow

        # replay reproduces the navigation at 0 LLM and returns the extracted data
        data = await replay(spec, router=_extract_router(42), cache=cache)
        assert data == 42
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_replay_without_learned_flow_raises(tmp_path: Path) -> None:
    spec = FlowSpec(name="missing", start_url="http://127.0.0.1:1/x", goal="g", extract="d", headless=True)
    with pytest.raises(FlowReplayError):
        await replay(spec, router=_extract_router(1), cache=FlowCache(root=tmp_path / "empty"))


# --- Phase B: trust unattended ----------------------------------------------------------------
def _fixture_spec(tmp_path: Path, name: str):
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name=name, start_url=f"{base}/page1.html",
                    goal="open the answer page", extract="the answer", headless=True)
    return httpd, cache, spec


async def test_require_approved_gate(tmp_path: Path) -> None:
    httpd, cache, spec = _fixture_spec(tmp_path, "gate")
    try:
        res = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        assert res.cached and not res.approved              # learned but unapproved
        with pytest.raises(FlowReplayError):                 # gate blocks an unapproved flow
            await replay(spec, require_approved=True, router=_extract_router(42), cache=cache)
        approve(spec, cache=cache)
        assert await replay(spec, require_approved=True, router=_extract_router(42), cache=cache) == 42
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_shape_change_is_flagged_as_drift(tmp_path: Path) -> None:
    httpd, cache, spec = _fixture_spec(tmp_path, "shape")
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)  # shape: number
        with pytest.raises(FlowReplayError):  # replay now extracts a list -> structure changed
            await replay(spec, router=_extract_router(["a", "b"]), cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_on_drift_relearn_reauthors(tmp_path: Path) -> None:
    httpd, cache, spec = _fixture_spec(tmp_path, "relearn")
    try:
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        # replay extracts a differently-shaped value (drift); on_drift=relearn re-authors and
        # returns the fresh data. Router needs two responses: the drifting attempt + the relearn.
        data = await replay(spec, on_drift="relearn", provider=_ClickFirstLink(),
                            router=_extract_router(["x"], ["x"]), cache=cache)
        assert data == ["x"]
    finally:
        httpd.shutdown()
        httpd.server_close()
