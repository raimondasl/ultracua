"""Resilient-locator fallback: a spec whose brittle anchors (id/test-id/css) no longer
match still resolves via role+name — the Phase-1 self-healing-lite that survives DOM
drift with no LLM."""

from __future__ import annotations

from playwright.async_api import async_playwright

from ultracua.locators import LocatorSpec, resolve

# The page changed since record time: the button lost its id/test-id and its css path
# moved, but its role and accessible name are unchanged.
DRIFTED_HTML = """<!doctype html><html><body>
  <div><section>
    <button class="brand-new-class">Add to cart</button>
  </section></div>
</body></html>"""


async def test_resolve_survives_id_and_css_drift() -> None:
    spec = LocatorSpec(
        role="button",
        name="Add to cart",
        tag="button",
        elem_id="old-add-id",          # gone
        testid="old-add-testid",       # gone
        css="body > button",           # no longer the real path
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await (await browser.new_context()).new_page()
        await page.set_content(DRIFTED_HTML)
        loc = await resolve(page, spec)
        assert loc is not None
        assert (await loc.inner_text()).strip() == "Add to cart"
        await browser.close()


# Two controls share role+name ("Submit"); only the id/css disambiguates them.
AMBIGUOUS_HTML = """<!doctype html><html><body>
  <form id="a"><button id="btn-a">Submit</button></form>
  <form id="b"><button id="btn-b">Submit</button></form>
</body></html>"""


async def test_resolve_prefers_unique_candidate_over_ambiguous_first() -> None:
    # role+name matches BOTH buttons (ambiguous); the unique id must win, not a blind `.first`.
    spec = LocatorSpec(role="button", name="Submit", tag="button",
                       elem_id="btn-b", css="#b > button")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await (await browser.new_context()).new_page()
        await page.set_content(AMBIGUOUS_HTML)
        loc = await resolve(page, spec)
        assert loc is not None
        assert await loc.evaluate("el => el.id") == "btn-b"  # not the first 'Submit' (btn-a)
        await browser.close()


async def test_resolve_unique_fails_loud_on_fully_ambiguous_target() -> None:
    # NOTHING disambiguates: role+name AND css both match both 'Submit' buttons, and there's no
    # test-id/id to break the tie. resolve() lenient binds a blind `.first`; resolve(unique=True) must
    # FAIL LOUD (None) — the contract the mutation gate leans on to refuse re-driving a write into the
    # wrong-but-identical form (two structurally-identical forms).
    spec = LocatorSpec(role="button", name="Submit", tag="button", css="form > button")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await (await browser.new_context()).new_page()
        await page.set_content(AMBIGUOUS_HTML)
        assert await resolve(page, spec) is not None           # lenient: a blind `.first`
        assert await resolve(page, spec, unique=True) is None  # strict: ambiguous -> fail loud
        await browser.close()


# ============================================================================================
# The positional-CSS retarget (0.62.0). These four cases ARE the specification of the fix, and
# they are drawn from the four drift-bench rows that adjudicate it.
#
# Tier-2 trusts a unique positional css so that a RENAMED target still resolves. The failure that
# buys: when the target is REMOVED and a same-tag sibling slides into its css slot, the cached path
# re-matches the neighbour and replay actuates the wrong element. `_testid_contradicted` withdraws
# that trust when the bound element positively falsifies a recorded `data-testid` — a rename cannot
# change a developer token, so its absence is evidence the path landed somewhere else.
#
# The measured constraint these pin: text similarity CANNOT discriminate (both renames change the
# text exactly as the retarget does), and a corroboration-shaped rule refuses both renames (they
# record no identity token at all). The rule must be contradiction-shaped and testid-only.
# ============================================================================================

async def _resolve_on(html: str, spec: LocatorSpec, *, unique: bool = True):
    """-> (bound element's data-oracle or None, sink)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            await page.set_content(html)
            sink: dict = {}
            loc = await resolve(page, spec, unique=unique, sink=sink)
            if loc is None:
                return None, sink
            return await loc.get_attribute("data-oracle"), sink
        finally:
            await browser.close()


# The target (`data-testid="next-link"`) was REMOVED; a same-tag sibling now occupies `#order > a`.
_RETARGETED = """<!doctype html><html><body>
  <section id="order"><h2>Your order</h2>
    <a href="/wrong" data-oracle="decoy">Skip</a>
  </section>
</body></html>"""

# The SAME element, merely renamed. No identity token was ever recorded for it.
_RENAMED = """<!doctype html><html><body>
  <section id="checkout"><h2>Checkout</h2>
    <p>Review your order, then continue.</p>
    <a href="/done" data-oracle="go">Proceed</a>
  </section>
</body></html>"""


async def test_a_positional_css_that_retargets_is_refused_when_it_falsifies_a_recorded_testid() -> None:
    """THE FIX. Recorded testid `next-link`; the element the cached path binds carries none, so the
    rename hypothesis the css-trust rests on is falsified — fail loud rather than click a stranger."""
    spec = LocatorSpec(role="link", name="Next", tag="a", testid="next-link",
                       text="Next", css="#order > a", anchor="Your order", anchor_source="heading")
    bound, sink = await _resolve_on(_RETARGETED, spec)
    assert bound is None, f"bound {bound!r} — a positional css retargeted onto a different element"
    assert sink.get("identity_contradiction") is True
    assert sink.get("conflict") is None, "an identity refusal must not be reported as a cross-check one"
    assert sink.get("bound_by") == "none"


async def test_a_renamed_target_with_no_recorded_testid_still_resolves_via_css() -> None:
    """THE COST CONTROL. This is what the css-trust exists for, and the fix must not touch it: the
    element is the same one, only its label changed, and it recorded no identity token to falsify."""
    spec = LocatorSpec(role="link", name="Continue", tag="a", text="Continue",
                       css="#checkout > a", anchor="Checkout", anchor_source="heading")
    bound, sink = await _resolve_on(_RENAMED, spec)
    assert bound == "go", "the renamed-target recovery regressed — this is the trade the fix must not make"
    assert sink.get("bound_by") == "css"
    assert sink.get("identity_contradiction") is None


async def test_a_matching_testid_on_the_css_bind_is_not_a_contradiction() -> None:
    """The rule is CONTRADICTION-shaped: a recorded token that the bound element still carries is
    corroboration, not falsification, so the bind stands."""
    html = """<!doctype html><html><body>
      <section id="order"><h2>Your order</h2>
        <a href="/done" data-oracle="go" data-testid="next-link">Renamed</a>
      </section></body></html>"""
    # `get_by_test_id` would normally win at Tier 1; force the Tier-2 path by recording a name/text
    # that no longer match, and note the testid still agrees.
    spec = LocatorSpec(role="link", name="Next", tag="a", testid="next-link",
                       text="Next", css="#order > a")
    bound, sink = await _resolve_on(html, spec)
    assert bound == "go"
    assert sink.get("identity_contradiction") is None


async def test_a_tokenless_positional_retarget_is_still_undetectable() -> None:
    """THE PUBLISHED RESIDUAL, pinned so it cannot be quietly assumed closed. With no recorded
    identity token this is INDISTINGUISHABLE from the renamed-target case above — same spec shape,
    same bind — so the resolver still binds the stranger. It is counted in drift-bench's
    `KNOWN_WRONG_BINDS` as `anchor-link/positional-css-retarget-tokenless`.

    If this test ever starts failing, the hole has been closed and the allowlist entry (and this
    docstring) should go with it."""
    html = """<!doctype html><html><body>
      <section id="checkout"><h2>Checkout</h2>
        <a href="/wrong" data-oracle="decoy">Elsewhere</a>
      </section></body></html>"""
    spec = LocatorSpec(role="link", name="Continue", tag="a", text="Continue",
                       css="#checkout > a", anchor="Checkout", anchor_source="heading")
    bound, _sink = await _resolve_on(html, spec)
    assert bound == "decoy", "the token-less retarget now resolves differently — update KNOWN_WRONG_BINDS"
