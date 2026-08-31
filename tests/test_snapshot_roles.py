"""`[role=menuitem]` was admitted and `[role=menuitemcheckbox]` was not (R4.132).

Odoo's Filters menu renders every option as `<span role="menuitemcheckbox">` -- 15 of its 25 items,
including `Archived`. So a menu the agent had just been taught to OPEN (R4.131 named its toggler)
arrived with its contents invisible: measured after the click, 76 visible candidates against a cap of
80, and ZERO elements whose text said Archived. Not the fold, not the cap, not the name -- the
selector list simply did not admit the role, while admitting its sibling.

WHY THIS IS SAFE TO ADD, measured before adding it: these roles contribute **0** elements across all
14 corpus start pages, because they live inside menus that are closed until something opens them. A
new candidate would otherwise enter `sorted([role, name, tag])` and shift that page's fingerprint,
which is every cached recipe's `precond_fingerprint`.

WHAT IT DOES NOT CLOSE: `SCOPE_JS` keeps its own, narrower list. Widening that changes the mutation
gate's recorded scope hash for every cached step, which is a write-safety-adjacent migration and
belongs in its own slice. The divergence is deliberate and is pinned below so it cannot become
accidental.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from ultracua import snapshot as snap
from ultracua.browser import BrowserSession

# The eight roles added, and the sibling that was already there.
ADDED = ("menuitemcheckbox", "menuitemradio", "option", "treeitem",
         "slider", "spinbutton", "textbox", "searchbox")

MENU = """
<h1>Filters</h1>
<button id="toggle">Open</button>
<div role="menu">
  <span role="menuitem">Plain item</span>
  <span role="menuitemcheckbox" class="dropdown-item">Archived</span>
  <span role="menuitemradio">Sort ascending</span>
</div>
<ul role="listbox"><li role="option">An option</li></ul>
<div role="treeitem">A tree item</div>
<div role="textbox" contenteditable="false">A textbox</div>
"""


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


async def _observe(body: str):
    httpd, url = _serve(body)
    try:
        async with BrowserSession(headless=True) as sess:
            await sess.goto(url)
            return await sess.snapshot()
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_menu_checkbox_item_reaches_the_observation() -> None:
    """THE EXACT SHAPE THAT BLOCKED `odoo-search`: `<span role="menuitemcheckbox">Archived</span>`."""
    obs = await _observe(MENU)
    names = [(e.role, e.name) for e in obs.elements]
    print(f"    {names}")
    assert any(n == "Archived" for _, n in names), (
        "a `menuitemcheckbox` did not reach the observation. Its sibling `menuitem` did, so a menu "
        "the agent can open still arrives with its options invisible.")


@pytest.mark.parametrize("role", ADDED)
async def test_every_added_role_is_admitted(role: str) -> None:
    """Derived over the list rather than one cell per role, so adding a ninth is one line."""
    obs = await _observe(f'<div role="{role}" style="width:80px;height:20px">Target {role}</div>')
    assert any(f"Target {role}" == (e.name or "") for e in obs.elements), (
        f"`[role={role}]` is in the selector list but produced no element")


async def test_the_sibling_that_was_always_admitted_still_is() -> None:
    """A regression direction: the fix must widen the list, never rewrite it."""
    obs = await _observe(MENU)
    assert any((e.name or "") == "Plain item" for e in obs.elements)


def test_the_selector_list_admits_the_aria_interactive_roles() -> None:
    """Structural, so a later edit that drops one fails here rather than on a live Odoo menu."""
    for role in ADDED + ("menuitem", "button", "link", "tab", "checkbox", "radio", "combobox",
                         "switch"):
        assert f"[role={role}]" in snap.SNAPSHOT_JS, f"`[role={role}]` left the snapshot selector"


def test_the_scope_fingerprint_list_is_deliberately_narrower() -> None:
    """THE DIVERGENCE IS A DECISION, NOT AN OVERSIGHT, and this pins it both ways.

    `SCOPE_JS` feeds the mutation gate's recorded scope hash. Widening it would change that hash for
    every cached step -- a write-safety-adjacent migration that needs its own slice and its own
    measurement. If someone widens it later, this cell fails and forces that argument to be made
    rather than absorbed; if someone narrows SNAPSHOT_JS to match, the cell above fails.
    """
    missing = [r for r in ADDED if f"[role={r}]" not in snap.SCOPE_JS]
    print(f"    roles in SNAPSHOT_JS but not SCOPE_JS: {missing}")
    assert missing == list(ADDED), (
        "SCOPE_JS gained snapshot-only roles. That changes the mutation gate's scope hash for every "
        "cached step, so it is a migration: argue it in its own slice, with the recipes measured.")
