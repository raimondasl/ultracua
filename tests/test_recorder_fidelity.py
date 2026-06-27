"""Recorder CAPTURE FIDELITY (Phase-I recorder) — the increments past click + same-page text:

  - NAVIGATION HANDSHAKE: a demo whose click NAVIGATES, then acts on the next page, captures EVERY step
    across the navigation boundary — nothing dropped. (The exfiltration store survives same-origin nav and
    is drained post-navigation, replacing the spike's fixed-timeout flush that could lose the last event.)
  - SELECT: a <select> choice is captured as a `select` step and replays via select_option(value), 0-LLM.
  - PRESS: an Enter-submit on a text input with no submit button (the "type then Enter" pattern) is captured
    as a `press` step that re-focuses the field and replays 0-LLM.
  - SCROLL: a human scroll is captured (debounced + coalesced) as one deterministic `scroll` step.

The "human" is a scripted sequence of REAL interactions, so the suite stays key-less + deterministic.
"""

from __future__ import annotations

import http.server
import threading

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.recorder import record_demo

PAGES = {
    # --- navigation handshake: page one links to page two; an action precedes AND follows the navigation ---
    "/nav1": "<h1>One</h1>"
             "<input type=checkbox id=a><label for=a>alpha</label>"
             "<a href='/nav2'>Go to two</a>",
    "/nav2": "<h1>Two</h1>"
             "<input type=checkbox id=b><label for=b>beta</label>",
    # --- select: a dropdown + a show button that echoes the chosen value ---
    "/select": "<select id=s aria-label='fruit'>"
               "<option value=''>--</option><option value='apple'>Apple</option>"
               "<option value='banana'>Banana</option></select>"
               "<button id=show onclick=\"document.getElementById('out').textContent="
               "document.getElementById('s').value\">show</button><div id=out></div>",
    # --- press: a JS search box (NO form, NO submit button) that updates on Enter, no navigation ---
    "/press": "<input id=q aria-label='query'><div id=out></div>"
              "<script>document.getElementById('q').addEventListener('keydown',function(e){"
              "if(e.key==='Enter'){document.getElementById('out').textContent='searched: '+e.target.value;}"
              "});</script>",
    # --- multi-select: more than one option chosen; the full set must survive (not just the first value) ---
    "/multiselect": "<select id=ms aria-label='langs' multiple>"
                    "<option value='py'>Py</option><option value='js'>JS</option>"
                    "<option value='go'>Go</option></select>",
    # --- press double-submit guard: a form WITH a submit button (Enter -> synthesized button click) ---
    "/pressform": "<form action='/results' method='get'>"
                  "<input id=q2 name=q aria-label='q2'><button>Search</button></form>",
    "/results": "<h1>results</h1>",
    # --- scroll: a tall page so a wheel actually moves the viewport ---
    "/scroll": "<div style='height:3000px'>top</div>"
               "<button id=b aria-label='bottombtn'>at bottom</button>",
    # --- neighbor anchor: two same-role+name buttons in differently-headed sections ---
    "/anchored": "<section><h2>Billing</h2><button>Save</button></section>"
                 "<section><h2>Shipping</h2><button>Save</button></section><div id=out></div>"
                 "<script>document.querySelectorAll('section').forEach(function(s){"
                 "s.querySelector('button').addEventListener('click',function(){"
                 "document.getElementById('out').textContent='saved '+s.querySelector('h2').textContent;});});"
                 "</script>",
}


def _serve():
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            body = PAGES.get(self.path.split("?")[0], "<h1>not found</h1>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_navigation_handshake_drops_no_step_across_a_page_boundary(tmp_path) -> None:
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        goal = "tick alpha, cross to two, tick beta"

        async def _demo(page) -> None:
            await page.get_by_role("checkbox", name="alpha").click()      # BEFORE the navigation
            await page.get_by_role("link", name="Go to two").click()      # the NAVIGATING click itself
            await page.get_by_role("checkbox", name="beta").wait_for()
            await page.get_by_role("checkbox", name="beta").click()       # AFTER the navigation

        flow, wrote, crossed, _ = await record_demo(f"{base}/nav1", _demo, goal=goal, cache=cache, headless=True)
        assert wrote is False and crossed is False     # same-origin nav: nothing crossed, nothing dropped
        # all THREE steps survived — the pre-nav toggle, the navigating click, and the post-nav toggle.
        assert [s.action for s in flow.steps] == ["click", "click", "click"]
        names = [s.locator.name for s in flow.steps if s.locator]
        assert names == ["alpha", "Go to two", "beta"]

        async def _finalize(session):
            return {"url": session.page.url, "beta": await session.page.is_checked("#b")}

        report = await run_cached(f"{base}/nav1", goal, None, cache, mode="replay",
                                  headless=True, finalize=_finalize)
        assert report.success and report.llm_calls == 0
        assert report.extra["finalize"]["url"].endswith("/nav2")   # the navigation replayed
        assert report.extra["finalize"]["beta"] is True            # the post-nav action replayed
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_select_dropdown_is_captured_and_replays(tmp_path) -> None:
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        goal = "choose banana"

        async def _demo(page) -> None:
            await page.select_option("#s", "banana")  # a <select> change -> a `select` step
            await page.click("#show")

        flow, _, _, _ = await record_demo(f"{base}/select", _demo, goal=goal, cache=cache, headless=True)
        assert [s.action for s in flow.steps] == ["select", "click"]
        sel = flow.steps[0]
        assert sel.action == "select" and sel.text == "banana"
        assert sel.locator and sel.locator.role == "combobox" and sel.locator.name == "fruit"

        async def _finalize(session):
            return {"out": await session.page.inner_text("#out")}

        report = await run_cached(f"{base}/select", goal, None, cache, mode="replay",
                                  headless=True, finalize=_finalize)
        assert report.success and report.llm_calls == 0
        assert report.extra["finalize"]["out"] == "banana"   # the recorded option was re-selected 0-LLM
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_multi_select_captures_the_full_set(tmp_path) -> None:
    # A <select multiple>'s `.value` is only the FIRST selected option — capturing that would silently drop
    # the rest. Assert the full selected set is captured (as a JSON array) and re-selected on replay.
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        goal = "pick py and go"

        async def _demo(page) -> None:
            await page.select_option("#ms", ["py", "go"])   # two options

        flow, _, _, _ = await record_demo(f"{base}/multiselect", _demo, goal=goal, cache=cache, headless=True)
        assert [s.action for s in flow.steps] == ["select"]
        assert flow.steps[0].text == '["py","go"]'          # the full set, JSON-encoded — not just "py"

        async def _finalize(session):
            return {"sel": await session.page.eval_on_selector(
                "#ms", "el => Array.from(el.selectedOptions).map(o => o.value)")}

        report = await run_cached(f"{base}/multiselect", goal, None, cache, mode="replay",
                                  headless=True, finalize=_finalize)
        assert report.success and report.llm_calls == 0
        assert report.extra["finalize"]["sel"] == ["py", "go"]   # both re-selected 0-LLM
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_enter_submit_is_captured_as_a_press_and_replays(tmp_path) -> None:
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        goal = "type a query and press enter"

        async def _demo(page) -> None:
            # The REALISTIC "type then Enter" pattern: NO blur. The keydown handler captures the field's
            # value as a `type` step BEFORE the `press`, so replay fills before pressing (regression for the
            # [press, type] mis-order / empty-formless-field bug).
            await page.fill("#q", "hello")            # sets the value (input only; no change without blur)
            await page.locator("#q").press("Enter")   # keydown Enter -> captures type("hello") THEN press

        flow, wrote, _, _ = await record_demo(f"{base}/press", _demo, goal=goal, cache=cache, headless=True)
        assert wrote is False
        assert [s.action for s in flow.steps] == ["type", "press"]   # type recorded BEFORE press, no duplicate
        typed, pressed = flow.steps
        assert typed.action == "type" and typed.text == "hello"
        assert pressed.action == "press" and pressed.text == "Enter"
        assert pressed.locator and pressed.locator.name == "query"  # re-focuses the right field on replay
        assert pressed.mutating is False                            # a read demo (no form, no wire write)

        async def _finalize(session):
            return {"out": await session.page.inner_text("#out")}

        report = await run_cached(f"{base}/press", goal, None, cache, mode="replay",
                                  headless=True, finalize=_finalize)
        assert report.success and report.llm_calls == 0
        assert report.extra["finalize"]["out"] == "searched: hello"  # Enter re-fired from the right field
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_enter_in_a_form_with_a_submit_button_is_not_double_recorded(tmp_path) -> None:
    # SAFETY: when a form HAS a submit button, Enter in a field triggers a synthesized CLICK on that button
    # (captured by the click listener). Recording a `press` TOO would submit the form twice on replay. Assert
    # we record ONLY the click — never press + click for the same submit.
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        goal = "search via enter"

        async def _demo(page) -> None:
            await page.fill("#q2", "hi")
            await page.locator("#q2").blur()          # a `type` step
            await page.locator("#q2").press("Enter")  # browser fires a click on "Search" — NOT a press

        flow, _, _, _ = await record_demo(f"{base}/pressform", _demo, goal=goal, cache=cache, headless=True)
        assert [s.action for s in flow.steps] == ["type", "click"]   # exactly one submit, recorded as a click
        assert "press" not in [s.action for s in flow.steps]         # no double-submit on replay
        click = flow.steps[1]
        assert click.action == "click" and click.locator and click.locator.name == "Search"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_scroll_is_captured_debounced_and_coalesced(tmp_path) -> None:
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        goal = "scroll down the page"

        async def _demo(page) -> None:
            await page.mouse.wheel(0, 1500)
            await page.wait_for_timeout(200)   # > the 100ms in-page debounce -> a first settled scroll event
            await page.mouse.wheel(0, 800)
            await page.wait_for_timeout(200)   # -> a second; the two coalesce to ONE step (final Y)

        flow, _, _, _ = await record_demo(f"{base}/scroll", _demo, goal=goal, cache=cache, headless=True)
        scrolls = [s for s in flow.steps if s.action == "scroll"]
        assert len(scrolls) == 1                       # consecutive scrolls coalesced to one
        assert int(scrolls[0].text) > 0                # captured the absolute Y it settled at

        report = await run_cached(f"{base}/scroll", goal, None, cache, mode="replay", headless=True)
        assert report.success and report.llm_calls == 0   # the scroll step replays without error
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_recorded_step_carries_the_neighbor_anchor(tmp_path) -> None:
    # describe()-reuse payoff: a recorded step now gets the SAME neighbor anchor the learn path captures
    # (recorded specs used to set anchor=null). Two same-role+name "Save" buttons differ only by section
    # heading -> the captured anchor disambiguates them on replay.
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        goal = "save shipping"

        async def _demo(page) -> None:
            await (page.locator("section").filter(has_text="Shipping")
                   .get_by_role("button", name="Save").click())   # the SHIPPING Save (ambiguous by role+name)

        flow, _, _, _ = await record_demo(f"{base}/anchored", _demo, goal=goal, cache=cache, headless=True)
        assert len(flow.steps) == 1
        loc = flow.steps[0].locator
        assert loc and loc.role == "button" and loc.name == "Save"
        assert loc.anchor == "Shipping" and loc.anchor_source == "heading"   # captured, not null

        async def _finalize(session):
            return {"out": await session.page.inner_text("#out")}

        report = await run_cached(f"{base}/anchored", goal, None, cache, mode="replay",
                                  headless=True, finalize=_finalize)
        assert report.success and report.llm_calls == 0
        assert report.extra["finalize"]["out"] == "saved Shipping"   # the anchor resolved the RIGHT button
    finally:
        httpd.shutdown()
        httpd.server_close()
