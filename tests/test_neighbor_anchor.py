"""Neighbor-anchor disambiguation: `describe` captures a distinguishing text from an element's enclosing
landmark (section heading / row text), and `resolve` tries it FIRST — so an ambiguous role+name resolves
to the right element by its section/row context (robust) instead of falling through to the brittle
positional css path. resolve only ever returns a count==1 match, so it never binds the wrong element; when
even css goes ambiguous (drift adds a sibling), `unique=True` fails loud rather than guessing. Completes
the silent-wrong-bind closure (#57 did the write target) for the read/click path.
"""

from __future__ import annotations

from ultracua.browser import BrowserSession
from ultracua.locators import describe, resolve


async def _tag_nth(page, sel: str, idx: int) -> None:
    await page.evaluate(
        f"() => document.querySelectorAll({sel!r})[{idx}].setAttribute('data-ultracua-ref', 'e0')"
    )


async def test_describe_captures_section_heading_anchor() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(
            '<section><h2>Billing</h2><button data-ultracua-ref="e0">Save</button></section>'
        )
        spec = await describe(session.page, "e0")
        assert spec is not None and spec.anchor == "Billing"
    finally:
        await session.close()


async def test_describe_captures_row_text_anchor() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(
            '<table><tbody><tr><td>Widget A</td>'
            '<td><button data-ultracua-ref="e0">Edit</button></td></tr></tbody></table>'
        )
        spec = await describe(session.page, "e0")
        assert spec is not None and spec.anchor and "Widget A" in spec.anchor
    finally:
        await session.close()


async def test_describe_anchor_none_without_a_landmark() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content('<button data-ultracua-ref="e0">Click me</button>')
        spec = await describe(session.page, "e0")
        assert spec is not None and spec.anchor is None  # no enclosing section/row
        loc = await resolve(session.page, spec)  # still resolves via role+name (backward compatible)
        assert loc is not None and (await loc.inner_text()) == "Click me"
    finally:
        await session.close()


async def test_resolve_anchor_disambiguates_when_css_is_ambiguous() -> None:
    # The win: a button unique at learn (css `section > button`, no nth, no id) — then drift adds ANOTHER
    # section with its own "Save", so role+name AND the captured css both go ambiguous (count 2). The
    # anchor, tried last, breaks the tie by section instead of failing loud or first-matching.
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(
            '<section><h2>Billing</h2><button data-which="billing">Save</button></section>'
        )
        await _tag_nth(session.page, "button", 0)
        spec = await describe(session.page, "e0")
        assert spec.anchor == "Billing" and spec.elem_id is None
        await session.page.evaluate(
            "() => { const s = document.createElement('section'); "
            "s.innerHTML = '<h2>Shipping</h2><button data-which=shipping>Save</button>'; "
            "document.body.appendChild(s); }"
        )
        loc = await resolve(session.page, spec, unique=True)
        assert loc is not None and (await loc.get_attribute("data-which")) == "billing"
    finally:
        await session.close()


async def test_anchor_does_not_override_a_unique_css() -> None:
    # REGRESSION GUARD: the anchor is a whole-subtree substring match, so an UNRELATED section that merely
    # contains the anchor word could single-match it. The anchor must never override a css that still
    # resolves the RIGHT element uniquely (else it binds confidently-wrong where the old code was correct).
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(
            '<section id="pay"><h2>Billing</h2><button data-which="real">Save</button></section>'
        )
        await _tag_nth(session.page, '[data-which="real"]', 0)
        spec = await describe(session.page, "e0")
        assert spec.anchor == "Billing"  # captured css is `#pay > button` (id-anchored, stays unique)
        # DRIFT: rebrand the real section's heading ("Billing"->"Payment") AND add a DIFFERENT section whose
        # BODY text contains "Billing" + its own Save. has_text("Billing") now single-matches the WRONG
        # section — but the css still uniquely resolves the real button, so resolve must return the real one.
        await session.page.evaluate(
            "() => { document.querySelector('#pay h2').textContent = 'Payment'; "
            "const s = document.createElement('section'); "
            "s.innerHTML = '<h2>Notes</h2><p>Billing questions? contact us</p>"
            "<button data-which=other>Save</button>'; document.body.appendChild(s); }"
        )
        loc = await resolve(session.page, spec, unique=True)
        assert loc is not None and (await loc.get_attribute("data-which")) == "real"
    finally:
        await session.close()


async def test_resolve_fails_loud_when_drift_makes_every_locator_ambiguous() -> None:
    # A button unique at learn (css `section > button`, no nth) — then drift adds an IDENTICAL sibling Save
    # into the same section. role+name, the anchor (same heading), text, AND the css path all now match 2.
    # With no unique signal left, unique=True fails loud (-> heal) rather than binding the wrong one.
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(
            '<section><h2>Billing</h2><button data-which="a">Save</button></section>'
        )
        await _tag_nth(session.page, "button", 0)
        spec = await describe(session.page, "e0")
        assert spec.anchor == "Billing" and spec.elem_id is None
        await session.page.evaluate(
            "() => { const b = document.createElement('button'); b.setAttribute('data-which','b'); "
            "b.textContent = 'Save'; document.querySelector('section').appendChild(b); }"
        )
        assert await resolve(session.page, spec, unique=True) is None  # genuinely ambiguous -> fail loud
        assert await resolve(session.page, spec, unique=False) is not None  # non-critical: first-match
    finally:
        await session.close()
