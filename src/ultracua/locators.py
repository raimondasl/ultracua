"""Cross-run-stable element locators (PLAN.md §4 LEARN + self-healing foundation).

The Phase 0 snapshot tags elements with a `data-ultracua-ref` that is only valid within
one snapshot. For *replay across runs* we need resilient locators that survive a fresh
page load. At record time `describe()` extracts a ranked set of stable hints for the
chosen element (role+name, test-id, id, placeholder, text, css path); at replay time
`resolve()` tries them in priority order — role/text/test-id first, css last — mirroring
Playwright's own "prefer user-facing locators" guidance.
"""

from __future__ import annotations

from typing import Optional

from playwright.async_api import Locator, Page
from pydantic import BaseModel

from .snapshot import _ACCNAME_JS, _ROLEOF_JS

# Roles Playwright's get_by_role understands and that our snapshot emits.
KNOWN_ROLES = {
    "button",
    "link",
    "textbox",
    "checkbox",
    "radio",
    "tab",
    "menuitem",
    "combobox",
    "switch",
    "option",
}

# Landmark/section containers used to scope a neighbor-anchored disambiguation (must mirror the `LM`
# list in DESCRIBE_JS so capture and resolve agree on what counts as a "section/row").
_LANDMARKS = ("form,fieldset,section,article,aside,nav,dialog,"
              "[role=region],[role=group],[role=form],li,tr,[role=listitem]")


def _attr_eq(attr: str, value: str) -> str:
    """A CSS `[attr="value"]` selector with the value safely quoted — anchor text can contain quotes
    or backslashes that would otherwise break the selector or change its meaning."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'[{attr}="{escaped}"]'


class LocatorSpec(BaseModel):
    """Ranked, resilient identification of one element, captured at record time."""

    role: str
    name: str
    tag: str
    elem_id: Optional[str] = None
    testid: Optional[str] = None
    placeholder: Optional[str] = None
    text: Optional[str] = None
    css: Optional[str] = None
    # Neighbor anchor: a distinguishing text from the element's enclosing landmark (section heading /
    # aria-label / row text). Used at replay to disambiguate two same-role+name elements that sit in
    # different sections/rows — so an ambiguous role+name resolves to the RIGHT one instead of guessing.
    anchor: Optional[str] = None
    # Where `anchor` came from, so resolve() can pick a PRECISE matcher instead of a loose substring:
    # "label" (landmark aria-label), "heading" (its heading/legend/caption/summary), or "row" (a li/tr's
    # own collapsed text). "label"/"heading" anchors carry a clean signal -> match them exactly; only "row"
    # (and old specs with no recorded source) fall back to the loose whole-subtree has_text substring.
    anchor_source: Optional[str] = None


# Runs in the page. Reuses snapshot.py's SHARED role/accessible-name derivation (so the captured name
# matches what learning saw and what get_by_role resolves) and adds a short css path.
DESCRIBE_JS = r"""
(ref) => {
  const el = document.querySelector('[data-ultracua-ref="' + ref + '"]');
  if (!el) return null;
""" + _ROLEOF_JS + _ACCNAME_JS + r"""
  const cssPath = (e) => {
    const parts = [];
    while (e && e.nodeType === 1 && parts.length < 5) {
      if (e.id) { parts.unshift('#' + CSS.escape(e.id)); break; }
      let part = e.tagName.toLowerCase();
      const parent = e.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter((c) => c.tagName === e.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(e) + 1) + ')';
      }
      parts.unshift(part);
      e = e.parentElement;
    }
    return parts.join(' > ');
  };
  // Neighbor anchor: a short distinguishing text from the nearest enclosing landmark — its aria-label or
  // heading/legend/caption, or (for a row-like container with neither) its own collapsed text. Two
  // same-role+name controls in different sections/rows get different anchors -> replay disambiguates.
  // The SOURCE travels with the text so resolve() can match cleanly anchors (label/heading) PRECISELY and
  // reserve the loose whole-subtree substring for row text (which has no cleaner signal).
  const LM = 'form,fieldset,section,article,aside,nav,dialog,[role=region],[role=group],[role=form],li,tr,[role=listitem]';
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const anchorOf = (e) => {
    let c = e.closest(LM), hops = 0;
    while (c && hops < 4) {
      const al = norm(c.getAttribute && c.getAttribute('aria-label'));
      if (al) return { text: al.slice(0, 60), source: 'label' };
      const h = c.querySelector('h1,h2,h3,h4,h5,h6,legend,caption,summary,[role=heading]');
      if (h) { const t = norm(h.innerText || h.textContent); if (t) return { text: t.slice(0, 60), source: 'heading' }; }
      const role = c.getAttribute && c.getAttribute('role');
      if (/^(li|tr)$/.test(c.tagName.toLowerCase()) || role === 'listitem') {
        const t = norm(c.innerText || c.textContent); if (t) return { text: t.slice(0, 60), source: 'row' };
      }
      c = c.parentElement ? c.parentElement.closest(LM) : null;
      hops++;
    }
    return null;
  };
  const anchor = anchorOf(el);
  return {
    role: roleOf(el),
    name: nameOf(el),
    tag: el.tagName.toLowerCase(),
    elem_id: el.id || null,
    testid: el.getAttribute('data-testid'),
    placeholder: el.getAttribute('placeholder'),
    text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
    css: cssPath(el),
    anchor: anchor ? anchor.text : null,
    anchor_source: anchor ? anchor.source : null,
  };
}
"""


async def describe(page: Page, ref: str) -> Optional[LocatorSpec]:
    """Capture a resilient LocatorSpec for the element currently tagged with `ref`."""
    raw = await page.evaluate(DESCRIBE_JS, ref)
    if not raw:
        return None
    return LocatorSpec(**raw)


# Returns the focused element's ref ONLY if it uniquely + correctly resolves back to it. The snapshot
# re-tags survivors `e0..eN` each step and never clears old tags, so a focused field that was EVICTED
# from this step's snapshot can carry a STALE ref that now ALSO tags a different survivor; describing
# such a ref (DOM-order-first querySelector) would silently capture the WRONG element. Bail (null) on
# any such ambiguity so the caller fails closed to the coarser whole-page gate instead of a wrong locator.
_FOCUSED_REF_JS = r"""
() => {
  const a = document.activeElement;
  if (!a || a === document.body || a === document.documentElement) return null;
  const ref = a.getAttribute('data-ultracua-ref');
  if (!ref) return null;
  const m = document.querySelectorAll('[data-ultracua-ref="' + ref + '"]');
  return (m.length === 1 && m[0] === a) ? ref : null;
}
"""


async def focused_ref(page: Page) -> Optional[str]:
    """The `data-ultracua-ref` of the focused element, but only when that ref unambiguously identifies
    it (see `_FOCUSED_REF_JS`). Returns None otherwise — used to pin a refless submit's focused field by
    identity, failing closed rather than trusting a stale/duplicated ref."""
    try:
        return await page.evaluate(_FOCUSED_REF_JS)
    except Exception:  # noqa: BLE001 - page navigating / detached -> no trustworthy ref
        return None


async def _same_element(a: Locator, b: Locator) -> bool:
    """True iff locators `a` and `b` resolve to the SAME live DOM element. Used to decide whether the two
    independent 'guess' strategies (fuzzy text vs css path) agree. Any error (detached node, navigation)
    -> False, i.e. treated as a DISAGREEMENT so the caller fails loud — the safe direction for a
    trust-relevant resolve. Each locator is already known to be count==1 when this is called."""
    try:
        handle = await b.element_handle()
        if handle is None:
            return False
        try:
            return bool(await a.evaluate("(el, other) => el === other", handle))
        finally:
            await handle.dispose()
    except Exception:  # noqa: BLE001
        return False


async def resolve(page: Page, spec: LocatorSpec, unique: bool = False) -> Optional[Locator]:
    """Resolve a spec to a visible Playwright Locator, trying resilient strategies before brittle
    ones. Returns None on drift (nothing resolves). With `unique=True`, an ambiguous candidate
    (count != 1) is never accepted — used by clicks/pinned reads/the mutation gate, where picking the
    wrong `.first` element would silently actuate/return the wrong target, so ambiguity must fail loud.

    Resolution runs in three tiers:
      1. CONFIDENT locators that are anchored to a stable identity (test-id, role+name, placeholder,
         exact whole-text, id). The first that resolves uniquely wins — these can't drift onto an
         unrelated element the way a fuzzy match can.
      2. Two GUESS strategies for an element whose confident locators all broke: the cached text as a
         tag-scoped SUBSTRING (re-finds a lightly-augmented label of the SAME element kind), and the
         recorded css path. Each can mis-resolve alone — a same-tag sibling that merely shares the cached
         substring; a positional css now pointing at a moved-in neighbor. css is structural, so a unique
         css match is trusted UNLESS the substring guess uniquely contradicts it (then neither is
         trustworthy -> fail loud). The substring guess is NEVER trusted on its own: with the target's own
         text changed it may have landed on a decoy, and there's nothing to corroborate it, so a lone
         substring match fails loud (unique) rather than silently binding a maybe-wrong element.
      3. The NEIGHBOR ANCHOR, a careful last-resort tiebreaker (only narrows; never overrides a confident
         match).
    """
    # --- Tier 1: confident, identity-anchored locators (first unique match wins) ---
    confident: list[Locator] = []
    if spec.testid:
        confident.append(page.get_by_test_id(spec.testid))
    if spec.role in KNOWN_ROLES and spec.name:
        confident.append(page.get_by_role(spec.role, name=spec.name, exact=True))  # type: ignore[arg-type]
        confident.append(page.get_by_role(spec.role, name=spec.name, exact=False))  # type: ignore[arg-type]
    if spec.placeholder:
        confident.append(page.get_by_placeholder(spec.placeholder, exact=True))
    if spec.text:
        # Exact WHOLE-text match — anchored to the element's own text, so it can't leak into a container.
        # Also tag-scoped (like the Tier-2 substring): exact whole-text still matches ACROSS tags, so a
        # removed roleless <span> "Save" whose exact text reappears as a <p> "Save" would otherwise bind
        # that prose. Scoping to `spec.tag` makes it bind only the SAME kind of element it captured (and
        # falls back to the un-scoped form for legacy specs with no recorded tag).
        exact_text = page.get_by_text(spec.text, exact=True)
        if spec.tag:
            exact_text = exact_text.and_(page.locator(spec.tag))
        confident.append(exact_text)
    if spec.elem_id:
        confident.append(page.locator(f'[id="{spec.elem_id}"]'))

    # --- Tier 2: the two independent "guess" locators (cross-checked against each other) ---
    # Fuzzy substring text, SCOPED to the element's own tag. A bare get_by_text(exact=False) matches the
    # smallest element whose subtree merely CONTAINS the cached text, which sweeps into surrounding PROSE
    # (a renamed "Continue" link let an unrelated <p> "…then continue." single-match and silently mis-bind).
    # Constraining it to `spec.tag` keeps its real value — re-finding a link whose label was lightly
    # AUGMENTED ("Proceed"->"Proceed now") where exact-text fails — while making it physically unable to
    # bind a different KIND of element than the one captured. (tag is always present — a required field;
    # role is not, since a roleless span/div "link" has role ∉ KNOWN_ROLES, so tag is the right scope key.)
    fuzzy_text = (page.get_by_text(spec.text, exact=False).and_(page.locator(spec.tag))
                  if spec.text and spec.tag else None)
    css_loc: Optional[Locator] = None
    if spec.css:
        try:
            css_loc = page.locator(spec.css)
        except Exception:  # noqa: BLE001
            css_loc = None

    # --- Tier 3: neighbor-anchor tiebreaker (LAST resort; only narrows, never overrides) ---
    anchor_loc: Optional[Locator] = None
    if spec.anchor and spec.role in KNOWN_ROLES and spec.name:
        # Scope the role+name to the landmark (section/row) carrying the captured anchor. HOW we match the
        # landmark depends on where the anchor came from:
        #   - "heading": the landmark holds a heading/legend/caption/summary whose EXACT text is the anchor.
        #     Match precisely (has= an exact-text descendant) — a loose whole-subtree has_text would let an
        #     unrelated section whose BODY merely *contains* the anchor word ("Billing questions?" vs a
        #     "Billing" heading) confidently single-match the WRONG section.
        #   - "label": the landmark's own aria-label IS the anchor — match that attribute exactly.
        #   - "row"/unknown (old specs): no cleaner signal than the row's collapsed text, so keep the loose
        #     has_text substring. Still only a tiebreaker among already-ambiguous matches; it only narrows,
        #     and a wrong/duplicate landmark still yields count!=1 -> fail loud.
        landmark = page.locator(_LANDMARKS)
        if spec.anchor_source == "heading":
            scoped = landmark.filter(has=page.get_by_text(spec.anchor, exact=True))
        elif spec.anchor_source == "label":
            scoped = landmark.and_(page.locator(_attr_eq("aria-label", spec.anchor)))
        else:
            scoped = landmark.filter(has_text=spec.anchor)
        anchor_loc = scoped.get_by_role(spec.role, name=spec.name, exact=True)  # type: ignore[arg-type]

    ambiguous: Optional[Locator] = None

    async def classify(loc: Locator) -> tuple[str, Optional[Locator]]:
        """-> ("unique"|"ambiguous"|"none", first-visible-match)."""
        try:
            n = await loc.count()
            if n == 0:
                return "none", None
            first = loc.first
            if not await first.is_visible():
                return "none", None
            return ("unique" if n == 1 else "ambiguous"), first
        except Exception:  # noqa: BLE001
            return "none", None

    # Tier 1: a confident unique match wins outright; record the first ambiguous for the lenient fallback.
    for loc in confident:
        kind, first = await classify(loc)
        if kind == "unique":
            return first
        if kind == "ambiguous" and not unique and ambiguous is None:
            ambiguous = first

    # Tier 2: reconcile the two guesses.
    fu = cu = None
    if fuzzy_text is not None:
        kind, first = await classify(fuzzy_text)
        if kind == "unique":
            fu = first
        elif kind == "ambiguous" and not unique and ambiguous is None:
            ambiguous = first
    if css_loc is not None:
        kind, first = await classify(css_loc)
        if kind == "unique":
            cu = first
        elif kind == "ambiguous" and not unique and ambiguous is None:
            ambiguous = first
    if cu is not None and (fu is None or await _same_element(fu, cu)):
        # css resolves uniquely and the fuzzy guess does NOT contradict it (absent / ambiguous / agrees).
        # css is a structural locator, so trust it — this is what recovers a renamed target
        # (`target-renamed`, `span-renamed`) where the tag-scoped substring rightly finds nothing.
        return cu
    if cu is not None and fu is not None:
        # Both resolve uniquely but to DIFFERENT elements (a same-tag sibling that shares the cached
        # substring vs a drifted positional css pointing at a moved-in neighbor). Neither is trustworthy
        # -> fail loud (unique); lenient keeps css as a best-effort structural guess.
        if not unique and ambiguous is None:
            ambiguous = cu
    elif fu is not None:
        # Only the FUZZY substring resolved (css is gone or itself ambiguous). On its own it may have
        # matched a same-tag DECOY that merely shares the cached substring (the target's own text changed),
        # with nothing to corroborate it — so it is NOT trusted for a critical bind and fails loud. Lenient
        # callers keep it as a last-ditch guess.
        if not unique and ambiguous is None:
            ambiguous = fu

    # Tier 3: neighbor anchor.
    if anchor_loc is not None:
        kind, first = await classify(anchor_loc)
        if kind == "unique":
            return first
        if kind == "ambiguous" and not unique and ambiguous is None:
            ambiguous = first

    return None if unique else ambiguous
