"""`mouse.wheel` moves the WINDOW, and some apps do not scroll the window (R4.138).

MEASURED across the Odoo corpus: on **6 of 7** start pages `window.scrollY` stays 0 after a scroll
action, the element set is unchanged, and `below_fold` does not move -- because the page scrolls an
inner container. R4.121 recorded the consequence on the learn side: the agent asks for the one action
that cannot reach the row it wants, and `odoo-open-record` cached **4 `scroll` steps and nothing
else**.

WHAT MADE IT URGENT is that R4.102 shipped one release earlier. The observation now TELLS the agent
"12 more are further down the page -- use `scroll`", so a signal it cannot act on was being handed to
it on 6 of 7 pages. A signal that cannot be acted on is worse than none -- D0's shape, arriving
through a fix rather than a refusal.

THE WINDOW STILL WINS WHEN IT CAN SCROLL, which is what keeps the common case identical: Gitea's
issue page has `docH 1512` against a 720 viewport and keeps the wheel it always had. Only a document
that CANNOT scroll reaches the container search, which is exactly the broken case.
"""

from __future__ import annotations

import http.server
import threading

from ultracua.browser import BrowserSession
from ultracua.types import Action

# The Odoo shape: body pinned to the viewport, a HEADER, and an inner pane doing the scrolling.
#
# THE HEADER IS LOAD-BEARING AND A FIRST DRAFT OMITTED IT, so `prove_red` reported the central
# mutation as a SURVIVOR -- correctly. `mouse.wheel` dispatches at the POINTER, and Playwright's
# pointer starts at (0, 0); with the pane at the top-left the wheel landed on the pane and scrolled
# it by accident, so removing the fix changed nothing. Odoo's scroller is `o_list_renderer` centred
# at (640, 413) under a control panel, and the wheel lands on the header -- whose nearest scrollable
# ancestor is the window, which cannot move. That is the real mechanism: not "inner containers do
# not scroll", but "the wheel scrolls whatever is under the pointer".
#
# 800px spacer, chosen so ONE 600px scroll brings the deep buttons from below the 720 fold into
# view. A first draft used 2000px and every assertion failed -- the scroll was working and the
# fixture simply could not be crossed in one step, which reads exactly like the feature being broken.
INNER = """
<style>
  html, body { height: 100%; margin: 0; overflow: hidden; }
  #header { height: 140px; background: #eee; }
  #pane { height: calc(100% - 140px); overflow-y: auto; }
  .spacer { height: 800px; }
</style>
<div id="header">A control panel, where the pointer starts</div>
<div id="pane">
  <button id="top">Top</button>
  <div class="spacer"></div>
  <button id="deep1">Deep one</button><button id="deep2">Deep two</button>
</div>
"""

# The ordinary shape: the document itself scrolls. It ALSO carries a small inner scroller, because
# that is what makes the window-first check testable -- a page with no container at all cannot tell
# a correct precedence from a missing search.
WINDOW = """
<style>
  #aside { height: 200px; width: 200px; overflow-y: auto; float: right; }
</style>
<div id="aside">a sidebar<div style="height:900px"></div><button id="side">Sidebar button</button></div>
<button id="top">Top</button>
<div style="height:800px"></div>
<button id="deep1">Deep one</button><button id="deep2">Deep two</button>
"""

# TWO scrollers: a big visible one, and a taller one mostly off-screen. The chosen scroller must be
# the one the agent can SEE, so "most scrollable" is the wrong rule and this is what says so.
TWO_SCROLLERS = """
<style>
  html, body { height: 100%; margin: 0; overflow: hidden; }
  #header { height: 140px; }
  #main { height: calc(100% - 140px); overflow-y: auto; }
  #drawer { position: absolute; top: 690px; left: 0; width: 300px; height: 400px;
            overflow-y: auto; }
</style>
<div id="header">header</div>
<div id="main">
  <button id="top">Top</button>
  <div style="height:800px"></div>
  <button id="deep1">Deep one</button><button id="deep2">Deep two</button>
</div>
<div id="drawer">drawer<div style="height:9000px"></div><button id="drawerbtn">Drawer</button></div>
"""

# Nothing scrolls at all.
FLAT = "<button id=a>Only</button>"


def _serve(body: str):
    payload = body.encode()

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    h = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=h.serve_forever, daemon=True).start()
    return h, f"http://127.0.0.1:{h.server_port}/"


async def _scroll_once(body: str):
    """Load, snapshot, perform ONE agent-style scroll, snapshot again."""
    httpd, url = _serve(body)
    try:
        async with BrowserSession(headless=True, window_size=(1280, 720)) as sess:
            await sess.goto(url)
            await sess.await_settled()
            before = await sess.snapshot()
            await sess.act(Action(action="scroll", intent="reveal more"))
            await sess.await_settled()
            after = await sess.snapshot()
            wy = await sess.page.evaluate("() => Math.round(window.scrollY)")
            inner = await sess.page.evaluate(
                "() => { const p = document.getElementById('pane');"
                "        return p ? Math.round(p.scrollTop) : null; }")
            return before, after, wy, inner
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_an_inner_scrolling_page_actually_scrolls() -> None:
    """THE DEFECT. Before this, `window.scrollY` stayed 0 and the observation never changed."""
    before, after, wy, inner = await _scroll_once(INNER)
    print(f"    below_fold {before.below_fold} -> {after.below_fold}; "
          f"window.scrollY={wy} pane.scrollTop={inner}")
    assert before.below_fold > 0, "premise lost: nothing was below the fold to begin with"
    assert inner and inner > 0, (
        f"the inner pane did not scroll (scrollTop={inner}). `mouse.wheel` moves the WINDOW, which "
        f"on this shape has nowhere to go -- so the agent is told to scroll and nothing happens.")
    assert after.below_fold < before.below_fold, (
        "the scroll moved something but the observation did not change, so the agent still cannot "
        "see the result of acting on the below-fold signal")


async def test_the_deeper_controls_actually_arrive() -> None:
    """Moving a scrollbar is not the point; the controls have to reach the observation."""
    before, after, _wy, _inner = await _scroll_once(INNER)
    assert "Deep one" not in [e.name for e in before.elements]
    names = [e.name for e in after.elements]
    print(f"    after: {names}")
    assert "Deep one" in names, "the buttons past the spacer never entered the observation"


async def test_a_window_scrolling_page_is_untouched() -> None:
    """THE REGRESSION DIRECTION, and the reason the window is checked FIRST. Gitea scrolls the
    document; if the container search took precedence it could pick some inner pane and leave the
    page where it was."""
    before, after, wy, _inner = await _scroll_once(WINDOW)
    print(f"    window.scrollY={wy}  below_fold {before.below_fold} -> {after.below_fold}")
    assert wy > 0, f"the window did not scroll (scrollY={wy}) on a document that scrolls"
    assert "Deep one" in [e.name for e in after.elements]


async def test_a_page_with_nothing_to_scroll_is_harmless() -> None:
    """The floor: no window scroll, no container, no crash, and nothing claimed."""
    before, after, wy, _inner = await _scroll_once(FLAT)
    print(f"    below_fold {before.below_fold} -> {after.below_fold}, scrollY={wy}")
    assert before.below_fold == 0 and after.below_fold == 0
    assert [e.name for e in before.elements] == [e.name for e in after.elements]


async def test_the_helper_reports_whether_it_moved_anything() -> None:
    """`_scroll_inner_container` is the fallback switch: False must mean 'the caller should wheel',
    so it may not claim success on a page where nothing inner scrolls."""
    httpd, url = _serve(WINDOW)
    try:
        async with BrowserSession(headless=True, window_size=(1280, 720)) as sess:
            await sess.goto(url)
            moved_window_page = await sess._scroll_inner_container(600)
    finally:
        httpd.shutdown()
        httpd.server_close()
    httpd, url = _serve(INNER)
    try:
        async with BrowserSession(headless=True, window_size=(1280, 720)) as sess:
            await sess.goto(url)
            moved_inner_page = await sess._scroll_inner_container(600)
    finally:
        httpd.shutdown()
        httpd.server_close()
    print(f"    window-scrolling page -> {moved_window_page}; inner-scrolling page -> "
          f"{moved_inner_page}")
    assert moved_window_page is False, (
        "the helper claimed a scroll on a page whose WINDOW scrolls, so the caller would skip the "
        "wheel and the page would never move")
    assert moved_inner_page is True


async def test_the_scroller_chosen_is_the_one_on_screen() -> None:
    """BIGGEST BY VISIBLE AREA, NOT BY SCROLL ROOM. A mostly off-screen drawer with 9000px of
    content has far more scroll room than the list the agent is looking at, and scrolling it moves
    nothing the agent can see."""
    before, after, _wy, _inner = await _scroll_once(TWO_SCROLLERS)
    names = [e.name for e in after.elements]
    print(f"    after: {names}")
    assert "Deep one" not in [e.name for e in before.elements], "premise lost"
    assert "Deep one" in names, (
        "the deep buttons in the MAIN pane never arrived -- the drawer was scrolled instead, which "
        "has more scroll room and almost no visible area")


async def test_a_page_error_falls_back_to_the_wheel_rather_than_claiming_success() -> None:
    """FAIL-OPEN MEANS FALLING BACK, NOT REPORTING SUCCESS.

    The return value is the caller's switch: True skips the wheel. So an evaluate that throws --
    a page navigating out from under it is the ordinary way -- must return False, or the scroll is
    silently dropped and the worst case is WORSE than the behaviour that shipped before this existed.

    Driven with a stub rather than a browser: the property is about the `except` arm, and arranging a
    real mid-evaluate navigation would be a flakier test of a smaller thing.
    """
    class _Page:
        @staticmethod
        async def evaluate(*_a, **_k):
            raise RuntimeError("Execution context was destroyed, most likely because of navigation")

    class _Session:
        page = _Page()

    got = await BrowserSession._scroll_inner_container(_Session(), 600)
    print(f"    evaluate raised -> helper returned {got!r}")
    assert got is False, (
        "the helper reported success after the page threw, so `act` skips the wheel and the scroll "
        "is silently dropped")
