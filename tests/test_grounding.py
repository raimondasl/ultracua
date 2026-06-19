"""Grounding hygiene (Tier-1): reading-order snapshot + real accessible-name.

A cleaner observation makes the LLM ground each authoring step correctly, and a real accessible name
(label / aria-labelledby) makes the captured `name` match what `get_by_role` resolves — both LEARN-side
wins that also sturdy the cached locators. Key-less (real headless Chromium against `set_content`).
"""

from __future__ import annotations

from ultracua.browser import BrowserSession
from ultracua.locators import describe
from ultracua.snapshot import capture

# DOM order (A then B) is the REVERSE of visual order (B is positioned above A).
_ORDER_HTML = """<!doctype html><html><body>
  <button id="a" style="position:absolute;top:90px;left:10px">Alpha</button>
  <button id="b" style="position:absolute;top:20px;left:10px">Bravo</button>
  <button id="c" style="position:absolute;top:20px;left:200px">Charlie</button>
</body></html>"""

# Inputs named only via aria-labelledby / <label for> / a wrapping <label>.
_NAMES_HTML = """<!doctype html><html><body>
  <span id="lbl">Email address</span>
  <input id="em" aria-labelledby="lbl">
  <label for="pw">Password</label><input id="pw" type="text">
  <label><input type="checkbox"> Accept terms</label>
</body></html>"""


async def test_snapshot_is_in_reading_order() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(_ORDER_HTML)
        obs = await capture(session.page, 50)
        names = [e.name for e in obs.elements]
        # Top row (Bravo, Charlie) before the lower Alpha, despite Alpha being first in the DOM;
        # within the top row, left-to-right (Bravo at x=10 before Charlie at x=200).
        assert names.index("Bravo") < names.index("Alpha")
        assert names.index("Charlie") < names.index("Alpha")
        assert names.index("Bravo") < names.index("Charlie")
    finally:
        await session.close()


async def test_accessible_name_from_label_aria_labelledby_and_wrapping_label() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(_NAMES_HTML)
        obs = await capture(session.page, 50)
        names = {e.name for e in obs.elements}
        assert "Email address" in names   # aria-labelledby -> referenced text
        assert "Password" in names        # <label for=id>
        assert "Accept terms" in names    # wrapping <label>

        # describe() (the locator path) must capture the SAME name the snapshot showed — they share
        # the accessible-name helper, so resolve()'s get_by_role(name=…) matches what learning saw.
        by_name = {e.name: e.ref for e in obs.elements}
        spec = await describe(session.page, by_name["Email address"])
        assert spec is not None and spec.name == "Email address"
    finally:
        await session.close()


_TRICKY_HTML = """<!doctype html><html><body>
  <input id="v" type="text" value="John Doe">
  <label>Pick <input id="a" type="radio" name="g"> Alpha <input id="b" type="radio" name="g"> Bravo</label>
</body></html>"""


async def test_value_and_multicontrol_label_are_not_accessible_names() -> None:
    # Regression (adversarial review): a control's value is NOT its accessible name (get_by_role ignores
    # it), and a <label> wrapping MULTIPLE controls must not name them all the same — that would resolve
    # the wrong control with count==1, defeating resolve()'s ambiguity guard.
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(_TRICKY_HTML)
        obs = await capture(session.page, 50)
        names = {e.name for e in obs.elements}
        assert "John Doe" not in names           # value is not a name
        assert "Pick Alpha Bravo" not in names   # multi-control wrapping label not applied to each radio
    finally:
        await session.close()


_SWAP_A = ('<!doctype html><html><body><button style="position:absolute;top:10px">One</button>'
           '<button style="position:absolute;top:50px">Two</button></body></html>')
_SWAP_B = ('<!doctype html><html><body><button style="position:absolute;top:50px">One</button>'
           '<button style="position:absolute;top:10px">Two</button></body></html>')


async def test_fingerprint_is_order_invariant() -> None:
    # The reading-order sort feeds the agent-facing list, but the fingerprint must be POSITION-invariant
    # so a layout-only reshuffle (a 1px nudge flipping sort order) can't masquerade as drift.
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(_SWAP_A)
        f1 = (await capture(session.page, 50)).fingerprint
        await session.page.set_content(_SWAP_B)
        f2 = (await capture(session.page, 50)).fingerprint
        assert f1 == f2 and f1   # same elements, swapped positions -> same fingerprint
    finally:
        await session.close()


async def test_fingerprint_is_stable_across_captures() -> None:
    # The reading-order sort feeds the structural fingerprint, so it must be deterministic for an
    # unchanged page — otherwise replay would see false drift.
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(_ORDER_HTML)
        a = await capture(session.page, 50)
        b = await capture(session.page, 50)
        assert a.fingerprint == b.fingerprint and a.fingerprint
    finally:
        await session.close()


# More visible buttons than MAX. The "Winner" (visually FIRST, top:0) is deliberately LAST in source
# order; every "Filler" is DOM-earlier but positioned lower. Building the page in Python keeps it key-less.
_DENSE_MAX = 5
_DENSE_HTML = (
    "<!doctype html><html><body>"
    + "".join(
        f'<button style="position:absolute;left:10px;top:{30 + i * 30}px">Filler{i}</button>'
        for i in range(12)
    )
    + '<button style="position:absolute;left:10px;top:0px">Winner</button>'
    + "</body></html>"
)


async def test_over_dense_page_truncates_visually_last_not_dom_last() -> None:
    # F4 regression: candidates are collected up to a ceiling, sorted into reading order, and ONLY THEN
    # truncated to MAX — so an over-dense page sheds its visually-LAST elements, not whichever fell late
    # in the DOM walk. "Winner" is visually first but DOM-last; capping before the sort (the old bug)
    # would have evicted it during the pass-1 walk. It must survive — and lead the reading order.
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(_DENSE_HTML)
        obs = await capture(session.page, _DENSE_MAX)
        names = [e.name for e in obs.elements]
        assert len(obs.elements) == _DENSE_MAX   # 13 visible buttons, truncated to the cap
        assert "Winner" in names                 # ...but the visually-first (DOM-last) one survived
        assert names[0] == "Winner"              # and it leads reading order (top:0)
    finally:
        await session.close()
