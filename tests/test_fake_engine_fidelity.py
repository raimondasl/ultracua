"""Is the fake engine faithful, or is it the next inert stub?

THE QUESTION THIS FILE EXISTS TO ANSWER. `tests/test_replay_exit_matrix.py` drives `replay()` through
its exit set in two seconds by scripting what the engine returns and what `finalize` writes into `out`.
That is only worth something if the scripted contract matches the real one — and this repo's record on
exactly that is bad: S14's `no_llm` stub was inert TWICE past its own review, and a replay built 105 real
Anthropic clients while every cell passed and the corpus cell printed "0 reached an LLM".

So the matrix's premise is asserted here rather than asserted in its docstring, three ways:

  1. the `out` key SET the fake permits is derived from the engine's source, not remembered;
  2. the real `_make_finalize` / `_make_pre_write` are called against a REAL browser session on a real
     fixture, and the keys they actually write are compared with what the fake is allowed to write;
  3. the fake's own guards reject an `out` key the engine never produces, and reject an Attempt that
     scripts neither a report nor a raise.

Cell 2 is the browser test in this file and the reason it is not in the matrix: the matrix must stay
millisecond-class, and its fidelity anchor must not.
"""

from __future__ import annotations

import ast
import pathlib
import re
import threading
import http.server
import functools

import pytest

import _fake_engine as fe

FLOWS = pathlib.Path("src/ultracua/flows.py")


def _out_keys_written_by(func_name: str) -> set:
    """Every `out[...]` key the named function assigns, read out of the engine's own AST."""
    src = FLOWS.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            seg = ast.get_source_segment(src, node) or ""
            return set(re.findall(r"out\[[\"']([A-Za-z_]+)[\"']\]", seg))
    raise AssertionError(f"{func_name} not found in {FLOWS} — this scan asserts nothing")


def test_the_fakes_out_contract_is_derived_from_the_engine_not_remembered() -> None:
    """If the engine gains an `out` key, the fake must learn about it — or a cell could script a shape
    the engine can no longer produce, and the matrix would be testing a fiction."""
    finalize = _out_keys_written_by("_make_finalize")
    pre_write = _out_keys_written_by("_make_pre_write")
    assert len(finalize) >= 8, f"only {len(finalize)} keys parsed from _make_finalize; the scan is broken"
    assert pre_write, "no keys parsed from _make_pre_write; the scan is broken"

    assert finalize == set(fe.FINALIZE_KEYS), (
        "the engine's `out` keys and the fake's permitted set have diverged.\n"
        f"  engine only: {sorted(finalize - set(fe.FINALIZE_KEYS))}\n"
        f"  fake only:   {sorted(set(fe.FINALIZE_KEYS) - finalize)}\n"
        "Update tests/_fake_engine.py — a fake that permits a key the engine never writes lets a cell "
        "assert a shape that cannot occur.")
    assert pre_write == set(fe.PRE_WRITE_KEYS), (
        f"pre_write keys diverged: engine {sorted(pre_write)} vs fake {sorted(fe.PRE_WRITE_KEYS)}")


def test_the_fake_rejects_shapes_the_engine_cannot_produce() -> None:
    """The fake's own guards, armed. A fake that accepts anything is a fake that proves nothing."""
    with pytest.raises(ValueError, match="exactly one"):
        fe.Attempt()
    with pytest.raises(ValueError, match="exactly one"):
        fe.Attempt(report=fe.report(), raises=RuntimeError("both"))
    with pytest.raises(ValueError, match="never writes"):
        fe.Attempt(report=fe.report(), out={"invented_key": True})
    # and the shapes it MUST accept
    assert fe.Attempt(report=fe.report()).out == {}
    assert fe.Attempt(raises=RuntimeError("x")).report is None
    assert fe.Attempt(report=fe.report(), out={"found": True, "write_landed": True})


def test_a_report_with_no_usage_is_expressible_because_the_engine_returns_them() -> None:
    """R4.45 is a defect ABOUT a missing `extra["usage"]`, so the fake must be able to produce one —
    otherwise the strict xfails pinning it could never be written."""
    assert fe.report(usage=None).extra == {}, "the fake cannot express the exits that omit usage"
    assert "usage" in fe.report(usage={"calls": 0}).extra


# ---------------------------------------------------------------------------------------------------
# The browser-backed anchor: what the REAL finalize writes, on a real page.

_PAGE = b"<!doctype html><title>t</title><h1>Panel</h1><p id=v>41</p><div id=done>Thanks</div>"


class _H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # framed, so an HTTP/1.1 client is never left waiting (R4.56)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, *a):
        pass


async def test_the_real_finalize_writes_only_keys_the_fake_permits(tmp_path) -> None:
    """Run the REAL `_make_finalize` against a REAL session and compare the keys it produces.

    This is the cell that makes the matrix more than a mutual agreement between two things I wrote. It
    launches a browser on purpose: the anchor for a millisecond-class instrument cannot itself be
    millisecond-class.
    """
    from ultracua.browser import BrowserSession
    from ultracua.flows import FlowSpec, MutateSpec, _make_finalize, _make_pre_write

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_H))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/"

    spec = FlowSpec(name="fidelity", goal="g", start_url=base, extract=None,
                    mutate=MutateSpec(confirm_text_contains="Thanks"))
    out: dict = {}
    session = BrowserSession(headless=True)
    await session.start()
    try:
        await session.page.goto(base)
        pre = _make_pre_write(spec, out)
        assert pre is not None, "a declared confirm must produce a baseline probe"
        await pre(session)
        fin = _make_finalize(spec, None, out, pin=None, redact=())
        await fin(session)
    finally:
        await session.close()
        httpd.shutdown()

    permitted = set(fe.FINALIZE_KEYS) | set(fe.PRE_WRITE_KEYS)
    assert out, "the real finalize wrote nothing — this cell would assert nothing"
    unexpected = sorted(set(out) - permitted)
    assert not unexpected, (
        f"the real engine wrote `out` keys the fake forbids: {unexpected}. The matrix is scripting a "
        f"contract the engine no longer has.")
    # And the keys the matrix's write cells actually depend on are among the ones the engine really writes.
    for k in ("found", "write_landed", "confirm_pre_true"):
        assert k in permitted
    print(f"  real finalize wrote {sorted(out)}; all within the fake's permitted set")
