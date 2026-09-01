"""The agent could not tell a whole page from the top third of one (R4.102).

`SNAPSHOT_JS` drops `r.top > innerHeight` deliberately -- that is a token-economy choice and a
reasonable one. What was missing is any SIGNAL that the drop happened: `Observation` carried `url`,
`title`, `elements`, `text`, `webmcp_tools` and `fingerprint`, and nothing said more page existed.
`scroll` was already an available action; there was simply never a reason to use it.

THE FLAGSHIP FAILURE, re-verified at 0.155.0 before this was built: `gitea-start-timer` is 0/3
permanently, and its control is a real `<button aria-label="Start Time Tracking">` at **y=859 in a
720px viewport**. The observation holds 73 elements and not that one; ONE scroll and it appears,
correctly named -- so neither hints (R4.131) nor the ARIA roles (R4.132) could ever have helped.

A COUNT, NOT THE ELEMENTS, and that is measured rather than preferred: the R4.134 survey found up to
**789** interactables below the fold on a single page, against a prompt cap of 80. Including them
would evict the visible ones.

AND IT COUNTS ELEMENTS, NOT HEIGHTS. `document.body.scrollHeight > innerHeight` is INERT on an app
that scrolls an inner container -- measured on Odoo, which keeps `docH == vpH == 720` while hiding 12
controls. R4.102 says this explicitly and it is the reason the obvious one-liner was never the fix.
"""

from __future__ import annotations

import http.server
import threading

from ultracua.browser import BrowserSession
from ultracua.providers import llm_agent

# Three buttons in the viewport, four pushed far below it, and three BELOW THE FOLD that must NOT be
# counted. The position matters and a first draft got it wrong: `display:none` collapses to a ZERO
# RECT (top=0), so it can never satisfy `top > innerHeight` wherever it sits -- the mutation that
# removes the CSS check SURVIVED against it. `visibility:hidden` and `opacity:0` keep a real layout
# box below the fold, so they are what actually exercises the check.
PAGE = """
<button id="a">Alpha</button><button id="b">Beta</button><button id="c">Gamma</button>
<div style="height:3000px"></div>
<button id="d">Delta</button><button id="e">Epsilon</button>
<button id="f">Zeta</button><button id="g">Eta</button>
<button id="invisible" style="visibility:hidden">Invisible</button>
<button id="transparent" style="opacity:0">Transparent</button>
<button id="gone" style="display:none">Gone</button>
"""

FITS = "<button id=a>Only</button><button id=b>Two</button>"


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


async def _obs(body: str, after=None):
    httpd, url = _serve(body)
    try:
        async with BrowserSession(headless=True, window_size=(1280, 720)) as sess:
            await sess.goto(url)
            if after is not None:
                await after(sess)
            return await sess.snapshot()
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_the_count_is_what_the_viewport_bound_dropped() -> None:
    obs = await _obs(PAGE)
    names = [e.name for e in obs.elements]
    print(f"    visible={names}  below_fold={obs.below_fold}")
    assert names == ["Alpha", "Beta", "Gamma"], "premise lost: the fixture's layout changed"
    assert obs.below_fold == 4, (
        f"expected the four buttons past the 3000px spacer, got {obs.below_fold}")


async def test_css_hidden_controls_are_not_counted_as_below_the_fold() -> None:
    """THE COUNT IS ABOUT WHAT A SCROLL CAN REACH. Telling the agent to scroll toward a control
    that is `visibility:hidden` is a signal it cannot act on, which is worse than none.

    THE FIXTURE HAD TO BE FIXED FOR THIS TO MEAN ANYTHING: `prove_red` reported the CSS-check
    mutation as a SURVIVOR, correctly. A `display:none` element has a zero rect, so `top > innerHeight`
    is false wherever it sits and the check is redundant for it. `visibility:hidden` and `opacity:0`
    keep their layout box, and those are the ones the check earns its keep on."""
    obs = await _obs(PAGE)
    assert obs.below_fold == 4, (
        f"below_fold={obs.below_fold}: the hidden and zero-sized buttons are being counted, so the "
        f"agent would be told to scroll for controls that do not exist on screen at any offset.")


async def test_a_page_that_fits_reports_zero_and_says_nothing() -> None:
    """MOST TURNS PAY FOR THIS LINE. The survey found 2 of 14 targets with nothing below the fold,
    so the clause must vanish when there is nothing to say rather than printing `0 more`."""
    obs = await _obs(FITS)
    print(f"    below_fold={obs.below_fold}")
    assert obs.below_fold == 0
    text = llm_agent._render(obs, "do a thing", [])
    header = [ln for ln in text.splitlines() if ln.startswith("INTERACTABLE")][0]
    print(f"    {header}")
    assert header == "INTERACTABLE ELEMENTS:", "a page that fits still paid for the clause"


async def test_the_signal_reaches_the_prompt_and_names_the_action() -> None:
    """A COUNT THE PROMPT DOES NOT RENDER IS INERT -- R4.131's mutation lesson, where the whole
    feature worked upstream and the agent saw exactly what it saw before. The clause also names
    `scroll`, because the action was always available and what was missing was the reason."""
    obs = await _obs(PAGE)
    text = llm_agent._render(obs, "find the last button", [])
    header = [ln for ln in text.splitlines() if ln.startswith("INTERACTABLE")][0]
    print(f"    {header}")
    assert "4 more" in header, f"the count did not reach the prompt: {header!r}"
    assert "scroll" in header, "the signal does not name the action that resolves it"


async def test_scrolling_resolves_it() -> None:
    """The signal must be live rather than a property of the URL: after scrolling, what was below is
    now in `elements` and the count falls."""
    async def scroll(sess):
        await sess.page.mouse.wheel(0, 4000)
        await sess.await_settled()

    obs = await _obs(PAGE, after=scroll)
    names = [e.name for e in obs.elements]
    print(f"    after scroll visible={names}  below_fold={obs.below_fold}")
    assert "Delta" in names, "the scroll did not reveal the lower buttons"
    assert obs.below_fold == 0


async def test_the_count_is_not_in_the_fingerprint() -> None:
    """A fingerprint change invalidates every cached recipe's `precond_fingerprint` and makes the
    mutation gate read drift. The basis is `sorted([role, name, tag]) + url`, so a control appearing
    BELOW the fold -- which changes the count and nothing the agent can see -- must be invisible to
    it. Injected in place, on one page and one url, for the reason
    `test_element_hints` records: two `_serve()` calls bind different ephemeral ports, and the url
    is in the basis."""
    httpd, url = _serve(FITS)
    try:
        async with BrowserSession(headless=True, window_size=(1280, 720)) as sess:
            await sess.goto(url)
            before = await sess.snapshot()
            await sess.page.evaluate(
                "() => { const d = document.createElement('div');"
                "        d.style.height = '3000px'; document.body.appendChild(d);"
                "        const b = document.createElement('button');"
                "        b.textContent = 'FarBelow'; document.body.appendChild(b); }")
            await sess.await_settled()
            after = await sess.snapshot()
    finally:
        httpd.shutdown()
        httpd.server_close()
    print(f"    below_fold {before.below_fold} -> {after.below_fold}; "
          f"fingerprint {before.fingerprint} -> {after.fingerprint}")
    assert before.below_fold == 0 and after.below_fold == 1, "premise lost: the injection did nothing"
    assert [e.name for e in before.elements] == [e.name for e in after.elements], (
        "premise lost: the injection changed the VISIBLE set, so this cell is no longer isolating "
        "the count")
    assert before.fingerprint == after.fingerprint, (
        "the below-fold count changed the observation fingerprint. Every cached recipe's stored "
        "fingerprint would be invalidated, and the mutation gate would read drift from a control "
        "nobody can see.")


#: The Odoo shape, reduced: the BODY does not scroll (`docH == vpH`) and controls are still hidden,
#: because an inner container scrolls instead. R4.102 measured exactly this -- a 720px body in a
#: 720px viewport with 12 controls outside it -- and it is why the obvious one-liner was refuted.
INNER_SCROLL = """
<style>
  html, body { height: 100%; margin: 0; overflow: hidden; }
  #pane { height: 100%; overflow-y: auto; }
  .spacer { height: 3000px; }
</style>
<div id="pane">
  <button id="a">Visible</button>
  <div class="spacer"></div>
  <button id="b">Hidden below</button><button id="c">Also hidden</button>
</div>
"""


async def test_the_derivation_counts_elements_rather_than_comparing_heights() -> None:
    """THE ONE-LINER R4.102 REFUTED, asserted as BEHAVIOUR rather than as a grep.

    A first draft checked `"scrollHeight" not in SNAPSHOT_JS` and went red on the comment explaining
    why scrollHeight is wrong -- the seventh time a scan in this repository has matched its own
    prose. The rule already written down here is to stop scanning text and assert the property, and
    the property has a decisive fixture: a page whose BODY does not scroll but whose controls are
    still off-screen. A height-comparing implementation reports 0 on it; counting elements does not.
    """
    httpd, url = _serve(INNER_SCROLL)
    try:
        async with BrowserSession(headless=True, window_size=(1280, 720)) as sess:
            await sess.goto(url)
            obs = await sess.snapshot()
            heights = await sess.page.evaluate(
                "() => ({doc: document.body.scrollHeight, vp: innerHeight})")
    finally:
        httpd.shutdown()
        httpd.server_close()
    print(f"    body.scrollHeight={heights['doc']} innerHeight={heights['vp']} "
          f"-> below_fold={obs.below_fold}")
    assert heights["doc"] <= heights["vp"], (
        "premise lost: this fixture is supposed to have a NON-scrolling body, which is what makes "
        "the height comparison inert. Rebuild it before trusting the assertion below.")
    assert obs.below_fold == 2, (
        f"below_fold={obs.below_fold} on a page where the body does not scroll. A height comparison "
        f"reports 0 here, and R4.102 measured that exact case on Odoo -- 12 controls hidden behind "
        f"docH == vpH == 720.")
