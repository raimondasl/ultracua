"""The 0.65.0 trust-state release: four ways the recorded trust state, or the report about it, could lie.

These came out of the standing defect register (docs/open-defects.md), and every one was reproduced with a
probe BEFORE being fixed. Every test here was confirmed to FAIL against the pre-fix code — a regression test
that passes both before and after proves nothing.

  * C2  — a re-learn that cached NOTHING reported `cached=True` off the PRE-EXISTING flow, then re-bound that
          flow's `read_pin` to the rejected attempt's DOM. The approval gate structurally cannot see it (the
          steps never changed), so an approved flow replays its old steps and reads the wrong element.
  * A12 — a corrupt/torn meta sidecar was read as "no meta", wiping quarantine + approval + contracts + the
          0-LLM read pin in one step, silently. A quarantined flow returned its quarantined value as a clean
          success, and the wiped pin put the LLM extractor back on a replay that was pinned 0-LLM.
  * A14 — a write flow whose confirmation READBACK failed returned `{"status": "confirmed", "data": None}`.
          A caller logging that records a null against a real order.
  * C1  — `dry_run`'s stale-approval predicate had dropped an arm the real pre-flight has, so a preview could
          be strictly MORE permissive than the run it previews.

The four share the shape that predicts the next bug here: the correct guard already existed on a sibling path.
  * C2  — the engine's own best-of-N loop reads `extra["cached"]`; the Flow API's loop read `cache.get(key)`
  * A12 — `cache.get` returns None on a corrupt flow (fail-loud); `_load_meta` returned a virgin FlowMeta
  * A14 — the READ branch of `_make_finalize` propagates `ex.found`/`error`/`truncated`; the WRITE branch did not
  * C1  — `health()` and `_preflight_row` had the three-armed predicate; `dry_run` had a two-armed copy
"""

from __future__ import annotations

import dataclasses
import http.server
import json
import os
import threading
from pathlib import Path

import pytest

import ultracua.flow as flowmod
import ultracua.flows as flowsmod
from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key
from ultracua.flows import (
    FlowMeta,
    FlowQuarantineError,
    FlowReplayError,
    FlowSpec,
    MutateSpec,
    SlotSpec,
    StaleApprovalError,
    WriteReadbackError,
    _load_meta,
    _meta_path,
    _save_meta,
    _update_meta,
    approve,
    dry_run,
    health,
    learn,
    preflight_keys,
    record,
    replay,
)
from ultracua.history import history_path, load_history, save_history
from ultracua.llm.base import Router, Tier
from ultracua.llm.mock import MockClient
from ultracua.types import Action


def _extract_router(*datas) -> Router:
    mc = MockClient(actions=[{"found": True, "data": d} for d in datas], tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


class _ClickNthLink:
    """Scripted agent: click the link at index `n` once (navigating), then done."""

    def __init__(self, n: int = 0) -> None:
        self.n, self.clicked = n, False

    async def decide(self, goal, obs, history):
        if not self.clicked:
            links = [el for el in obs.elements if el.role == "link"]
            if len(links) > self.n:
                self.clicked = True
                return Action(action="click", intent="open the value page", ref=links[self.n].ref), None
        return Action(action="done", intent="done"), None


def _serve_two_pages(state: dict):
    """/page1 links to /a and /b.

      /a  — the APPROVED flow's terminal page: `#total` is the answer, `#fee` is a DIFFERENT number
      /b  — where a re-learn that clicks the SECOND link lands: only `#fee`

    The overlap is the point: `#fee` exists on both, so a pin learned on /b resolves cleanly on /a and
    returns the wrong number rather than failing loud."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            from urllib.parse import urlparse

            path = urlparse(self.path).path
            if path == "/page1":
                self._send("<html><body><h1>Home</h1>"
                           "<a href='/a'>the total</a> <a href='/b'>the fees</a></body></html>")
            elif path == "/a":
                self._send(f"<html><body><h1>Total</h1><p id='total'>{state['total']}</p>"
                           f"<p id='fee'>{state['fee']}</p></body></html>")
            elif path == "/b":
                self._send(f"<html><body><h1>Fees</h1><p id='fee'>{state['fee']}</p></body></html>")
            else:
                self._send("not found")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# ==================== C2. a re-learn that cached nothing reported success ====================

async def test_failed_relearn_is_not_reported_as_cached(tmp_path: Path, monkeypatch) -> None:
    """A re-learn whose authored recipe is REJECTED by verify-by-replay must report `cached=False` and
    leave the pre-existing flow's trust state completely alone."""
    state = {"total": "42", "fee": "7"}
    httpd, base = _serve_two_pages(state)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="c2", start_url=f"{base}/page1", goal="read the total",
                    extract="the total number", pin_read=True, headless=True)
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    try:
        first = await learn(spec, provider=_ClickNthLink(0), router=_extract_router(42), cache=cache)
        assert first.cached and first.pinned
        approve(spec, cache=cache)
        pin0 = _load_meta(cache, key).read_pin
        steps0 = [s.intent for s in cache.get(key).steps]

        async def _reject(*a, **k):     # the authored recipe does not survive verify-by-replay
            return False

        monkeypatch.setattr(flowmod, "_verify_by_replay", _reject)
        res = await learn(spec, provider=_ClickNthLink(1), router=_extract_router(7), cache=cache)

        # THIS attempt cached nothing, so it must say so — best-of-N keeps resampling and the CLI prints
        # its "no replayable flow was cached" warning off exactly this flag.
        assert res.cached is False
        assert res.steps == []
        # ...and the pre-existing flow is untouched: same recipe, same pin.
        assert cache.get(key) is not None
        assert [s.intent for s in cache.get(key).steps] == steps0
        assert _load_meta(cache, key).read_pin == pin0
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_failed_relearn_does_not_repin_an_approved_flow_onto_the_wrong_element(
    tmp_path: Path, monkeypatch
) -> None:
    """The wrongness the `cached=True` phantom actually causes: the REJECTED attempt's pin is bound onto the
    APPROVED flow, whose steps still walk to the OLD page — where that pin resolves to a different number."""
    state = {"total": "42", "fee": "7"}
    httpd, base = _serve_two_pages(state)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="c2wrong", start_url=f"{base}/page1", goal="read the total",
                    extract="the total number", pin_read=True, headless=True)
    try:
        assert (await learn(spec, provider=_ClickNthLink(0), router=_extract_router(42),
                            cache=cache)).pinned
        approve(spec, cache=cache)
        assert await replay(spec, cache=cache) == 42            # 0-LLM, via the pin, on /a

        async def _reject(*a, **k):
            return False

        monkeypatch.setattr(flowmod, "_verify_by_replay", _reject)
        # A re-learn that lands on /b and reads `#fee`. It is REJECTED — nothing may cross into the flow.
        await learn(spec, provider=_ClickNthLink(1), router=_extract_router(7), cache=cache)

        # The approval legitimately still holds: the steps never changed, so this gate cannot be the one
        # that catches a pin swap. That is precisely why the pin must not have moved.
        assert health(spec, cache=cache).approval_stale is False
        assert await replay(spec, cache=cache) == 42            # NOT 7 (`#fee`, which also exists on /a)
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== A12. a corrupt trust sidecar wiped the trust state ====================

@pytest.mark.parametrize("payload", [b"", b"\x00" * 64, b'{"approved": tr'],
                         ids=["empty", "nul-filled", "half-json"])
async def test_corrupt_meta_refuses_instead_of_wiping_the_trust_state(
    tmp_path: Path, monkeypatch, payload: bytes
) -> None:
    state = {"total": "42", "fee": "7"}
    httpd, base = _serve_two_pages(state)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="a12", start_url=f"{base}/page1", goal="read the total",
                    extract="the total number", pin_read=True, headless=True)
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    try:
        assert (await learn(spec, provider=_ClickNthLink(0), router=_extract_router(42),
                            cache=cache)).pinned
        approve(spec, cache=cache)
        # A REAL quarantine: the learn-seeded `positive` contract fires on a negative value.
        state["total"] = "-7"
        with pytest.raises(FlowQuarantineError):
            await replay(spec, cache=cache)
        assert health(spec, cache=cache).status == "quarantined"

        _meta_path(cache, key).write_bytes(payload)             # host crash / torn write / bad hand-edit

        # An unreadable sidecar is NOT "no sidecar". Every one of these was the opposite before the fix.
        assert _load_meta(cache, key).quarantine is not None
        assert health(spec, cache=cache).status == "quarantined"

        built: list = []
        monkeypatch.setattr(flowsmod, "build_router",
                            lambda name: built.append(name) or _extract_router(-7))
        with pytest.raises(FlowQuarantineError):
            await replay(spec, cache=cache)
        # Inviolable #1: the wiped pin used to put the LLM extractor back on a 0-LLM-pinned replay.
        assert built == []

        # The evidence survives — the meta is the HOT file, so the next run would have overwritten it.
        preserved = list(Path(cache.root).glob(f"{key}.meta.json.corrupt.*"))
        assert len(preserved) == 1
        assert preserved[0].read_bytes() == payload
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_meta_that_is_valid_json_but_not_an_object_also_refuses(tmp_path: Path) -> None:
    """`json.loads` succeeds, so the corrupt-file branch never sees it — it used to fall through to a
    virgin FlowMeta just the same."""
    cache = FlowCache(root=tmp_path / "cache")
    key = "k"
    Path(cache.root).mkdir(parents=True, exist_ok=True)
    _meta_path(cache, key).write_text("[1, 2, 3]", encoding="utf-8")
    meta = _load_meta(cache, key)
    assert meta.quarantine is not None
    assert meta.quarantine["code"] == "meta_unreadable"
    assert list(Path(cache.root).glob(f"{key}.meta.json.corrupt.*"))


async def test_an_absent_meta_is_still_a_clean_slate(tmp_path: Path) -> None:
    """The guard must not over-fire: a flow that was never learned has no trust state to lose."""
    cache = FlowCache(root=tmp_path / "cache")
    meta = _load_meta(cache, "never-seen")
    assert meta.quarantine is None and meta.approved is False and meta.runs == 0


def _fsync_order(monkeypatch, call):
    """Run `call()` recording the interleaving of os.fsync / os.replace."""
    order: list = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: order.append("fsync") or real_fsync(fd))
    monkeypatch.setattr(os, "replace", lambda a, b: order.append("replace") or real_replace(a, b))
    call()
    return order


async def test_the_trust_sidecar_is_fsynced_before_it_is_renamed_into_place(
    tmp_path: Path, monkeypatch
) -> None:
    """temp+rename alone is atomic but not DURABLE: `os.replace` can land while the bytes are still only in
    the page cache, which is how the torn sidecar above gets made in the first place."""
    cache = FlowCache(root=tmp_path / "cache")
    order = _fsync_order(monkeypatch, lambda: _save_meta(cache, "k", FlowMeta(approved=True)))
    assert order == ["fsync", "replace"]
    assert json.loads(_meta_path(cache, "k").read_text(encoding="utf-8"))["approved"] is True


async def test_the_magnitude_history_is_fsynced_before_it_is_renamed_into_place(
    tmp_path: Path, monkeypatch
) -> None:
    cache = FlowCache(root=tmp_path / "cache")
    doc = {"v": 1, "fields": {"": [1.0, 2.0]}, "anchors": {"": 1.0}}
    order = _fsync_order(monkeypatch, lambda: save_history(cache, "k", doc))
    assert order == ["fsync", "replace"]


async def test_a_corrupt_magnitude_history_preserves_the_lost_anchor(tmp_path: Path) -> None:
    """The anchor's loss is SELF-CONCEALING — `set_anchor` only writes when absent, so the next clean run
    silently re-anchors at today's already-drifted value. Losing it quietly is the whole problem."""
    cache = FlowCache(root=tmp_path / "cache")
    save_history(cache, "k", {"v": 1, "fields": {"": [10.0]}, "anchors": {"": 10.0}})
    history_path(cache, "k").write_bytes(b"\x00" * 32)

    doc = load_history(cache, "k")
    assert doc["anchors"] == {}                                  # still tolerant — never raises
    preserved = list(history_path(cache, "k").parent.glob("k.magnitude.json.corrupt.*"))
    assert len(preserved) == 1                                   # ...but no longer SILENT
    assert preserved[0].read_bytes() == b"\x00" * 32


async def test_an_absent_magnitude_history_is_not_treated_as_corrupt(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path / "cache")
    assert load_history(cache, "never-seen") == {"v": 1, "fields": {}, "anchors": {}}
    assert not list((Path(cache.root) / "history").glob("*.corrupt.*"))


# ==================== A14. a write whose readback failed reported success ====================

def _serve_write_lazy_confirm(counter: dict, lazy: bool):
    """POST /save places the order and returns the confirm signal. With `lazy=True` the confirmation NUMBER
    renders asynchronously, so it is absent when the readback runs — the write LANDED, the readback missed."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, body: str, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] == "/":
                self._send("<h1>Cart</h1><form action='/save' method='post'>"
                           "<button>Place order</button></form>")
            else:
                self._send("not found", 404)

        def do_POST(self) -> None:  # noqa: N802
            counter["orders"] = counter.get("orders", 0) + 1     # the irreversible side effect
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            num = "<p id='num'>loading…</p>" if lazy else "<p id='num'>#999</p>"
            self._send(f"<h1>Order placed</h1>{num}")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _place_order(page) -> None:
    await page.get_by_role("button", name="Place order").click()


def _miss_router() -> Router:
    """An extraction router whose reply is a clean `found=False` — a soft miss, not a crash."""
    mc = MockClient(actions=[{"found": False, "data": None, "error": "number not on the page"}],
                    tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


async def test_a_write_whose_readback_misses_fails_loud_and_is_not_retryable(tmp_path: Path) -> None:
    counter: dict = {}
    httpd, base = _serve_write_lazy_confirm(counter, lazy=True)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="ordread", start_url=f"{base}/", goal="place the order",
                    extract="the confirmation number", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Order placed"))
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    try:
        res = await record(spec, demo=_place_order, headless=True, cache=cache)
        assert res.cached and res.is_write
        assert counter["orders"] == 1                            # the demonstration placed it once
        # WHY the shape gate cannot save this flow: `record()` leaves `shape` unseeded, and approval never
        # re-seeds it. Pinning this keeps the test honest if that ever changes.
        assert _load_meta(cache, key).shape is None
        approve(spec, cache=cache)

        with pytest.raises(WriteReadbackError) as ei:
            await replay(spec, cache=cache, router=_miss_router())

        assert ei.value.retryable is False
        assert ei.value.code == "write_readback"
        assert "must NOT be retried" in str(ei.value)
        # Write safety: the write fired EXACTLY once more. The raise must not have re-entered auth-refresh
        # or relearn, both of which would re-fire a committed write.
        assert counter["orders"] == 2
        # The run is booked as a SUCCESS — the write landed, and a failure streak would push an operator
        # toward the one action that must not be taken.
        meta = _load_meta(cache, key)
        assert meta.consecutive_failures == 0 and meta.successes == 1
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_write_whose_readback_lands_still_returns_the_value(tmp_path: Path) -> None:
    """The control arm: the new gate must not fire when the readback works."""
    counter: dict = {}
    httpd, base = _serve_write_lazy_confirm(counter, lazy=False)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="ordread-ok", start_url=f"{base}/", goal="place the order",
                    extract="the confirmation number", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Order placed"))
    try:
        assert (await record(spec, demo=_place_order, headless=True, cache=cache)).cached
        approve(spec, cache=cache)
        assert await replay(spec, cache=cache, router=_extract_router("999")) == {
            "status": "confirmed", "data": "999"}
        assert counter["orders"] == 2
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== C1. the dry run previewed a gate the real run enforces ====================

def _serve_dry():
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            body = b"<h1>Cart</h1><form action='/save' method='post'><button>Place order</button></form>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            body = b"<h1>Order placed</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def _recorded_write(tmp_path: Path, name: str, **spec_kw):
    httpd, base = _serve_dry()
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name=name, start_url=f"{base}/", goal="place the order", headless=True,
                    mutate=MutateSpec(confirm_text_contains="Order placed"), **spec_kw)
    await record(spec, demo=_place_order, headless=True, cache=cache)
    approve(spec, cache=cache)
    return httpd, cache, spec, flow_key(spec.goal, spec.start_url, spec.scope)


async def test_dry_run_names_a_migration_stale_approval(tmp_path: Path) -> None:
    """A flow approved by a version that predates the steps-binding. The real pre-flight refuses it; the
    preview used to be byte-identical to a fully-blessed flow's."""
    httpd, cache, spec, key = await _recorded_write(tmp_path, "migr")
    try:
        _update_meta(cache, key, lambda m: setattr(m, "steps_hash", None))   # a pre-0.60 approval on disk

        # Pin that the REAL gate refuses, so the next assertion is about a genuine divergence.
        with pytest.raises(StaleApprovalError):
            preflight_keys(spec, None, cache=cache, require_approved=True)

        rep = await dry_run(spec, cache=cache)
        assert any("stale_approval" in g for g in rep.approval_gates_skipped), rep.approval_gates_skipped
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_dry_run_names_a_dropped_slot_table(tmp_path: Path) -> None:
    """The A13 short-circuit, in the preview instead of the gate: `spec.slots and ...` meant dropping ONE
    slot was reported while dropping the WHOLE TABLE was not."""
    httpd, cache, spec, _key = await _recorded_write(
        tmp_path, "slots", slots={"qty": SlotSpec(type="int")})
    try:
        bare = dataclasses.replace(spec, slots={})           # the WHOLE table dropped, not one entry
        rep = await dry_run(bare, cache=cache)
        assert any("slot schema" in g for g in rep.approval_gates_skipped), rep.approval_gates_skipped
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_dry_run_reports_an_uncomputable_digest_instead_of_tracebacking(tmp_path: Path) -> None:
    """A hand-edited / foreign cache file whose `args` hold something `json.dumps(sort_keys=True)` rejects.
    `health()` calls it stale and pre-flight raises StaleApprovalError; `dry_run` let a bare TypeError
    escape the FlowReplayError taxonomy its callers catch."""
    httpd, base = _serve_dry()
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="baddigest", start_url=f"{base}/", goal="place the order",
                    headless=True, mutate=MutateSpec(confirm_text_contains="Order placed"))
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    bad = CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, created_ts=0.0,
                     steps=[CachedStep(action="click", intent="place the order", mutating=True)])
    bad.steps[0].args = {1: "a", "b": 2}                      # mixed-type keys -> sort_keys raises TypeError

    class _HandEdited(FlowCache):
        def get(self, k: str):
            return bad if k == key else super().get(k)

    try:
        c = _HandEdited(root=cache.root)
        _update_meta(c, key, lambda m: (setattr(m, "approved", True),
                                        setattr(m, "steps_hash", "deadbeefdeadbeef")))

        assert health(spec, cache=c).approval_stale is True     # the sibling predicate already says stale
        with pytest.raises(StaleApprovalError):                 # ...and the real gate refuses in-taxonomy
            preflight_keys(spec, None, cache=c, require_approved=True)

        rep = await dry_run(spec, cache=c)                      # must NOT traceback
        assert any("stale_approval" in g for g in rep.approval_gates_skipped), rep.approval_gates_skipped
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_dry_run_of_a_fully_blessed_flow_still_reports_no_skipped_gates(tmp_path: Path) -> None:
    """The control arm: the three new report lines must not fire on a flow whose approval genuinely binds."""
    httpd, cache, spec, _key = await _recorded_write(tmp_path, "blessed")
    try:
        rep = await dry_run(spec, cache=cache)
        assert rep.approval_gates_skipped == [], rep.approval_gates_skipped
    finally:
        httpd.shutdown()
        httpd.server_close()
