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


async def test_a_renumbered_positional_row_still_binds_the_stranger() -> None:
    """D3's RESIDUAL, pinned at the resolver so it does not depend on a 187-row bench run.

    A real list RE-RENDERS after a delete and the survivors renumber, so the recorded `#row-3` names
    what used to be row 4. With the row's own name gone, the positional css path is what decides, and
    it decides wrongly — the bound element is a different RECORD, opened silently.

    THE CORPUS COULD NOT PRODUCE THIS UNTIL 0.174.0 (R4.150). `row-positional` carried `data-index`
    and `id="row-N"` on all 12 rows and returned ZERO wrong binds across all 14 mutations, because
    `sibling_removed` is `sibs[0].remove()` against STATIC html and nothing renumbered. Counted now in
    drift-bench's `KNOWN_WRONG_BINDS` as `row-positional/positional-row-renumber`.

    If this starts failing the hole has been closed, and the allowlist entry goes with it.
    """
    html = """<!doctype html><html><body><table><tbody>
      <tr id="row-1" data-index="1" data-testid="cart-row"><td>Widget 2</td>
        <td><a href="/done" data-oracle="row2">Details</a></td></tr>
      <tr id="row-2" data-index="2" data-testid="cart-row"><td>Widget 3</td>
        <td><a href="/done" data-oracle="go">Details</a></td></tr>
      <tr id="row-3" data-index="3" data-testid="cart-row"><td>Widget 4</td>
        <td><a href="/wrong" data-oracle="row4">Details</a></td></tr>
    </tbody></table></body></html>"""
    # Recorded against the PRISTINE page, where the target sat in `#row-3`. Its own accessible name is
    # gone (the re-render dropped the per-row aria-label), so the positional path is all that is left.
    spec = LocatorSpec(role="link", name="Details for widget 3", tag="a", text="Details",
                       css="#row-3 > td:nth-of-type(2) > a", anchor="Widget 3", anchor_source="row")
    bound, _sink = await _resolve_on(html, spec)
    assert bound == "row4", (
        f"the renumbered positional row now resolves to {bound!r} rather than the stranger — the hole "
        f"D3 exists to close may have shut, so re-measure and update KNOWN_WRONG_BINDS")


async def test_a_sole_surviving_fuzzy_candidate_is_refused_when_css_contradicts_it() -> None:
    """D2, DECIDED AND CLOSED at 0.178.0 (R4.156). This cell used to assert the HOLE — that the sole
    surviving `role+name~` candidate bound the decoy outright — and it is inverted here rather than
    deleted, because the direction that matters now is the regression.

    THE MEASURED SHAPE, from `locators.py`'s own example: a redesign renames the primary control and
    the old wording lands on a neighbour, so the recorded name stops matching the target and starts
    matching the DECOY as a substring. The recorded css path is UNTOUCHED by that rename, so it still
    resolves uniquely to the target — the two disagree, and a disagreement is what Tier 2 has always
    treated as *neither is trustworthy -> fail loud*. The bind is refused.

    THE TARGET'S TEXT MATTERS AND IS WHY THE FIRST DRAFT OF THE BENCH ROW MEASURED NOTHING: leaving it
    intact lets `exact-text` — which sits ABOVE the demoted fuzzy tier — bind correctly, so the fuzzy
    candidate is never reached at all. Both names have to go for this cell to reach its subject, and
    `fuzzy_contradicted` is asserted so a refusal arriving from some OTHER branch cannot be read as
    this one working.
    """
    html = """<!doctype html><html><body>
      <div><a href="/wrong" data-oracle="wrong-decoy"
             aria-label="Save the document as a draft">Save draft</a></div>
      <div><a href="/done" data-oracle="go" aria-label="Publish">Publish</a></div>
    </body></html>"""
    spec = LocatorSpec(role="link", name="Save the document", tag="a", text="Save",
                       css="body > div:nth-of-type(2) > a", anchor=None, anchor_source=None)
    bound, sink = await _resolve_on(html, spec)
    assert bound is None, (
        f"the sole-surviving fuzzy candidate bound {bound!r}; D2's refusal has regressed and "
        f"`fuzzy-decoy/fuzzy-decoy-wins` is a silent wrong-target click again")
    assert sink.get("fuzzy_contradicted"), (
        "the bind was refused, but NOT by D2's contradiction check — this cell would pass for the "
        "wrong reason if some other refusal moved in front of it")


async def test_a_fuzzy_bind_survives_when_the_css_path_is_gone() -> None:
    """D2's COST CONTROL, and the reason the shipped rule refuses on CONTRADICTION rather than
    requiring AGREEMENT. Without this cell the refusal above is satisfied by refusing every fuzzy
    bind, which measured 0-LLM survivals 84 -> 79 and k50 6 -> 2 on the drift corpus.

    A lightly AUGMENTED label ("Proceed" -> "Proceed now") is the case `role+name~` exists for, and
    the bench's five `rename_augment+wrap` rows pair it with a structural change that breaks the
    recorded css path. An absent css cannot contradict anything, so the bind must still stand.
    """
    html = """<!doctype html><html><body>
      <div><span><a href="/done" data-oracle="go">Proceed now</a></span></div>
    </body></html>"""
    spec = LocatorSpec(role="link", name="Proceed", tag="a", text="Proceed",
                       css="body > div > a", anchor=None, anchor_source=None)   # path broken by the wrap
    bound, sink = await _resolve_on(html, spec)
    assert bound == "go", (
        f"the augmented label no longer binds (got {bound!r}) — D2's check has become an AGREEMENT "
        f"gate, which is the variant measured to cost five 0-LLM rows")
    assert sink.get("bound_by") == "role+name~", (
        f"bound by {sink.get('bound_by')!r} rather than the fuzzy candidate, so this cell no longer "
        f"exercises the candidate whose cost it exists to pin")


async def test_when_the_css_path_is_the_drifted_one_the_refusal_is_still_LOUD() -> None:
    """D2's RESIDUAL, pinned in the only direction that matters (R4.156, 0.178.0).

    The contradiction check treats a unique css match as evidence against the fuzzy name match. It
    cannot tell WHICH of the two drifted — so when the css path is the one that moved and the fuzzy
    match is CORRECT, a 0-LLM bind is lost. Measured at zero cost on the drift corpus, but the corpus
    contains no row of this shape, which is a gap in the instrument rather than evidence of absence.

    MEASURED, AND IT IS A REAL LOSS: on this exact page the PRE-D2 resolver binds the CORRECT target
    (`go`, via the fuzzy candidate) and the shipped one refuses. So the residual is not hypothetical --
    it costs a 0-LLM bind wherever this shape occurs, and the corpus's zero is the absence of the shape
    rather than the absence of the cost.

    WHAT THIS CELL GUARANTEES IS THE DIRECTION, NOT THE COST: the outcome is a LOUD refusal and never
    a wrong bind. That is the property a future attempt to widen this check must not trade away — the
    tempting "trust css, it is structural" would bind the stranger here, silently, at 0-LLM.

    The page: the recorded path `body > div:nth-of-type(2) > a` now lands on an UNRELATED control
    because a div was inserted above, while the target keeps a name the recorded one is a substring
    of. Both resolve uniquely, to different elements.
    """
    html = """<!doctype html><html><body>
      <div><a href="/x" data-oracle="inserted">Unrelated</a></div>
      <div><a href="/y" data-oracle="stranger">Archive</a></div>
      <div><a href="/done" data-oracle="go">Proceed now</a></div>
    </body></html>"""
    spec = LocatorSpec(role="link", name="Proceed", tag="a", text="Proceed",
                       css="body > div:nth-of-type(2) > a", anchor=None, anchor_source=None)
    bound, sink = await _resolve_on(html, spec)
    assert bound != "stranger", (
        "the drifted css path was BOUND — a silent wrong-target click at 0-LLM, which is the one "
        "outcome this check must never produce")
    assert bound is None, (
        f"expected a loud refusal, got {bound!r}; if this now binds the correct target the residual "
        f"has been closed and this cell should record how")
    assert sink.get("fuzzy_contradicted"), (
        "refused, but not by D2's check — the residual this cell documents is no longer reached here")
