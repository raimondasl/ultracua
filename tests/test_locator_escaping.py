"""A page-controlled `id` must never change what a selector MEANS (0.62.1).

`resolve()`'s Tier-1 id candidate was built as an f-string, `[id="{spec.elem_id}"]`, while `_attr_eq` had
existed alongside it for exactly this purpose. `elem_id` comes from `el.id` — arbitrary page content — and
HTML places no restriction on quotes or backslashes in an id.

Three distinct failure shapes, all measured, in ascending severity:

  * `a"b`               the selector is INVALID. `classify()` swallows the exception, so the id candidate is
                        silently lost. Fail-safe, but a resilient anchor disappears without a trace.
  * `a\\b`              `\\b` is a CSS escape sequence, so the raw form matches `ab` — a DIFFERENT element.
  * `zzz"],[id="other`  the value closes the attribute and opens a second selector, so the raw form is a
                        selector LIST. It matches one element, `count == 1`, and Tier 1 returns it
                        OUTRIGHT — a confident wrong bind that never reaches the Tier-2 cross-check or the
                        neighbour anchor. That is inviolable #2 at the most-trusted tier.

Note the asymmetry the third case exposes: every other wrong-bind this project has found lives in Tier 2,
where two independent guesses are cross-checked. A Tier-1 candidate has no such check by design — it is
supposed to be anchored to a stable identity — so a selector that can be re-pointed by page content is
strictly worse there than anywhere else.

All key-less; real Chromium, static HTML, no server.
"""

from __future__ import annotations

from playwright.async_api import async_playwright

from ultracua.locators import LocatorSpec, _attr_eq, resolve


async def _bind(html: str, spec: LocatorSpec):
    """-> the `data-o` marker of whatever `resolve(unique=True)` binds, or None."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            await page.set_content("<!doctype html><html><body>" + html + "</body></html>")
            loc = await resolve(page, spec, unique=True)
            return await loc.get_attribute("data-o") if loc is not None else None
        finally:
            await browser.close()


async def test_an_id_containing_a_quote_still_resolves() -> None:
    """Previously the selector was invalid and the id candidate vanished; the element was only found if
    some OTHER anchor happened to survive."""
    html = ('<p id=\'a"b\' data-o="target">T</p>'
            '<p id="other" data-o="other">O</p>')
    # role/name/text deliberately absent, so `elem_id` is the ONLY candidate that can bind.
    spec = LocatorSpec(role="", name="", tag="p", elem_id='a"b')
    assert await _bind(html, spec) == "target"


async def test_an_id_containing_a_backslash_binds_the_right_element() -> None:
    r"""`\b` is a CSS escape, so the unescaped selector `[id="a\b"]` matched the element with id `ab`."""
    html = ('<p id="a\\b" data-o="target">T</p>'
            '<p id="ab" data-o="decoy">D</p>')
    spec = LocatorSpec(role="", name="", tag="p", elem_id="a\\b")
    assert await _bind(html, spec) == "target", "the backslash id bound the wrong element"


async def test_an_id_cannot_inject_a_second_selector() -> None:
    """THE SERIOUS ONE. The value closes the attribute and opens another selector, so the raw form became
    `[id="zzz"],[id="other"]` — a selector list matching a completely unrelated element, uniquely, at
    Tier 1. `resolve()` returns a confident match outright, so nothing downstream could have caught it."""
    html = '<p id="other" data-o="other">O</p>'
    spec = LocatorSpec(role="", name="", tag="p", elem_id='zzz"],[id="other')
    bound = await _bind(html, spec)
    assert bound is None, f"an id injected a selector and bound {bound!r} — a confident wrong bind"


async def test_a_plain_id_is_unaffected() -> None:
    """The escaping must be transparent for the overwhelmingly common case."""
    html = ('<p id="normal-id" data-o="target">T</p>'
            '<p id="other" data-o="other">O</p>')
    spec = LocatorSpec(role="", name="", tag="p", elem_id="normal-id")
    assert await _bind(html, spec) == "target"


def test_attr_eq_escapes_both_metacharacters() -> None:
    """Pure unit check on the helper, so a future edit to it fails here rather than in a browser test."""
    assert _attr_eq("id", "plain") == '[id="plain"]'
    assert _attr_eq("id", 'a"b') == '[id="a\\"b"]'
    assert _attr_eq("id", "a\\b") == '[id="a\\\\b"]'
    # backslash first, then quote — escaping in the other order would double-escape the inserted backslash
    assert _attr_eq("id", '\\"') == '[id="\\\\\\""]'


def test_no_page_controlled_value_is_interpolated_into_a_selector() -> None:
    """The regression guard. `_attr_eq` existed for ~200 lines above the offending call and was simply not
    used, so a rule that only fixes the one site invites the next one. Every raw `[attr="..."]` f-string in
    `locators.py` must go through the helper.

    `data-ultracua-ref` interpolations elsewhere are deliberately out of scope: those refs are generated by
    the snapshot (`e0`, `e1`, ...), not read from the page, so they are not attacker- or site-controlled."""
    from pathlib import Path

    src = Path("src/ultracua/locators.py").read_text(encoding="utf-8")
    offenders = [ln.strip() for ln in src.splitlines()
                 if 'f\'[' in ln.replace('f"[', "f'[") and "return f'[{attr}=" not in ln]
    assert not offenders, f"raw selector interpolation reintroduced: {offenders}"
