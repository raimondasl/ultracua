"""Cross-run-stable element locators (PLAN.md §4 LEARN + self-healing foundation).

The Phase 0 snapshot tags elements with a `data-ultracua-ref` that is only valid within
one snapshot. For *replay across runs* we need resilient locators that survive a fresh
page load. At record time `describe()` extracts a ranked set of stable hints for the
chosen element (role+name, test-id, id, placeholder, text, css path); at replay time
`resolve()` tries them in priority order — role/text/test-id first, css last — mirroring
Playwright's own "prefer user-facing locators" guidance.
"""

from __future__ import annotations

import re
from typing import Optional

from playwright.async_api import Locator, Page
from pydantic import BaseModel

from .snapshot import _ACCNAME_JS, _ROLEOF_JS

# `anchorOf` truncates every captured anchor to this many chars. A row anchor at exactly this length is
# a PREFIX of the real row text, so it cannot be compared exactly — and a prefix comparison is the
# wrong-bind this guards. Kept in sync with the `.slice(0, 60)` calls in `_SPECOF_JS` below.
_ANCHOR_MAX = 60

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
# list in `_SPECOF_JS` so capture and resolve agree on what counts as a "section/row").
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
    # A stable IDENTITY for the anchor's row, when the page offers one: the row's id / data-* value, or
    # the first identity-bearing href inside it. Row TEXT is not an identity — a price or status changing
    # does not make it a different record — so text alone cannot tell "this row was edited" from "this row
    # is gone". This can. Additive and defaulted None, so old cached flows deserialize unchanged and simply
    # fall back to the previous behaviour.
    anchor_id: Optional[str] = None


# Runs in the page. Builds a LocatorSpec from a live element `el`, reusing snapshot.py's SHARED
# role/accessible-name derivation (so the captured name matches what learning saw and what get_by_role
# resolves), a short css path, and the neighbor anchor. ASSUMES `roleOf`/`nameOf` are already defined in
# scope (i.e. `_ROLEOF_JS + _ACCNAME_JS` is concatenated before it). Factored out of DESCRIBE_JS so the
# RECORDER (recorder.py) builds a recorded step's spec with the IDENTICAL css path + neighbor anchor as the
# learn path — resolution PARITY by construction (a recorded spec resolves exactly as a learned one would),
# and recorded steps gain the drift-resilient neighbor anchor they previously lacked.
_SPECOF_JS = r"""
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
  const _ucnorm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  // A row's stable identity. Returns null when the row offers none — a very common case, and one where we
  // must NOT invent one, because a fabricated identity is worse than an absent one: the guard runs, reports
  // satisfied, and protects nothing.
  //
  // PRECEDENCE, and it is load-bearing. This used to take ANY `data-*` in attribute order and return it
  // BEFORE looking at the href — so on a design-system table stamping `data-testid="order-row"` on every
  // row, the captured "identity" was present on every sibling and the guard became a no-op, while the real
  // identity (`href:/cancel/3`) sat unread in the same row. Now:
  //   1. an explicit `id`;
  //   2. the first href/action inside the row — a URL usually EMBEDS the record key (`/cancel/3`), which is
  //      the strongest thing a row offers;
  //   3. a `data-*` ONLY if no sibling landmark carries the same attribute+value. That uniqueness test is
  //      exactly what separates a per-record `data-order-id="3"` from a shared `data-testid="order-row"`,
  //      and it is decidable here, at capture, from the page in front of us.
  // `_ROW_OF_JS` in this same file re-implements this order for the post-bind containment check; the two
  // MUST agree, which is why they are commented as a pair.
  //
  // RESIDUAL, stated rather than hidden: a POSITIONAL token (`data-index="2"`, `id="row-2"`) is unique
  // among its siblings and so passes, yet renumbers when a row is deleted — the identity survives the
  // record it named. Nothing observable in a single capture distinguishes that from a real key. Rows that
  // also carry an href are covered, because the href now wins.
  const rowIdOf = (c) => {
    try {
      if (c.id) return 'id:' + c.id;
      const link = c.querySelector('a[href], form[action]');
      if (link) {
        const v = link.getAttribute('href') || link.getAttribute('action') || '';
        if (v && v !== '#' && !/^javascript:/i.test(v)) return 'href:' + v;
      }
      for (const a of Array.from(c.attributes || [])) {
        if (a.name.indexOf('data-') !== 0 || !a.value) continue;
        // Compare ATTRIBUTES directly rather than building a CSS selector from the value. A selector
        // needs the value escaped, and getting that escaping wrong fails SILENTLY: the query throws, the
        // identity is dropped, and the guard degrades without a word. (Measured, in this very change: a
        // mis-escaped character class made the whole check throw, so every row anchor refused and the
        // drift corpus lost 8 rows of 0-LLM survival.) getAttribute has no such edge.
        let shared = 0;
        for (const other of Array.from(document.querySelectorAll(LM))) {
          if (other !== c && other.getAttribute && other.getAttribute(a.name) === a.value) shared++;
        }
        if (shared === 0) return a.name + ':' + a.value;   // unique among rows -> a real identity
      }
    } catch (e) {}
    return null;
  };
  const anchorOf = (e) => {
    let c = e.closest(LM), hops = 0;
    while (c && hops < 4) {
      const al = _ucnorm(c.getAttribute && c.getAttribute('aria-label'));
      if (al) return { text: al.slice(0, 60), source: 'label' };
      const h = c.querySelector('h1,h2,h3,h4,h5,h6,legend,caption,summary,[role=heading]');
      if (h) { const t = _ucnorm(h.innerText || h.textContent); if (t) return { text: t.slice(0, 60), source: 'heading' }; }
      const role = c.getAttribute && c.getAttribute('role');
      if (/^(li|tr)$/.test(c.tagName.toLowerCase()) || role === 'listitem') {
        const t = _ucnorm(c.innerText || c.textContent);
        if (t) return { text: t.slice(0, 60), source: 'row', id: rowIdOf(c) };
      }
      c = c.parentElement ? c.parentElement.closest(LM) : null;
      hops++;
    }
    return null;
  };
  const specOf = (el) => {
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
      anchor_id: anchor ? (anchor.id || null) : null,
    };
  };
"""

# Runs in the page. Looks up the `data-ultracua-ref`-tagged element and returns its shared `specOf`.
DESCRIBE_JS = r"""
(ref) => {
  const el = document.querySelector('[data-ultracua-ref="' + ref + '"]');
  if (!el) return null;
""" + _ROLEOF_JS + _ACCNAME_JS + _SPECOF_JS + r"""
  return specOf(el);
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


async def _testid_contradicted(loc: Locator, testid: Optional[str]) -> bool:
    """True iff the element `loc` bound POSITIVELY FALSIFIES the recorded `data-testid`.

    The Tier-2 css-trust is a RENAME HYPOTHESIS: it exists so a target whose visible label changed is still
    found by its structural path. A rename cannot change a `data-testid` — that is a developer token, not
    user-visible copy — so a recorded testid the bound element does not carry is direct evidence the path
    landed on a DIFFERENT element, and the hypothesis the trust was granted under does not hold.

    Deliberately a CONTRADICTION test, never a corroboration one. Both renames the drift corpus must keep
    recovering (`target-renamed`, `span-renamed`) record NO identity token at all, so any "require N-of-M
    positive corroboration" rule would refuse them — measured: such a rule drops v1 parity from 12/12 to
    10/12. Absence of evidence must not become evidence of absence.

    Scope, stated honestly: this closes the retarget only for a target that HAD a testid. 8 of the 10
    Tier-2 css binds in the corpus are token-less, and for those a positional retarget remains
    undetectable with the fields `describe()` records — see the token-less row in
    `benchmarks/drift_bench.KNOWN_WRONG_BINDS`.

    `placeholder` is excluded on purpose (user-visible copy, the same class as text/name, which the corpus
    proves non-discriminating). `elem_id` is excluded because it cannot fire: `cssPath` returns `#<id>` and
    stops, so a css match on an id-bearing spec already carries that id.

    Any exception -> True (contradicted), mirroring `_same_element`'s fail-loud convention.
    """
    if not testid:
        return False
    try:
        return bool(await loc.evaluate("(el, want) => el.getAttribute('data-testid') !== want", testid))
    except Exception:  # noqa: BLE001 — detached / navigating -> treat as a contradiction
        return True


# The row that a bound element actually sits in — asked of the ELEMENT, not of the document. Returns the
# same identity string `rowIdOf` produces at capture, or "" when the element is in no row / the row has no
# identity. `_ROW_OF_JS` and `rowIdOf` MUST agree on precedence; they are deliberately adjacent so a change
# to one is impossible to make without seeing the other.
_ROW_OF_JS = r"""
(el) => {
  const LM = '""" + _LANDMARKS + r"""';
  // MIRROR `anchorOf`'s walk exactly. Capture does NOT use the nearest landmark — it climbs (max 4 hops)
  // until it reaches a ROW-LIKE container (li / tr / [role=listitem]) and takes THAT one's identity. The
  // nearest landmark is very often a per-row <form> nested inside the <tr>, whose identity is a different
  // string entirely; using it here made every cart-row drift refuse and cost 8 rows of 0-LLM survival on
  // the drift corpus. (`anchor_source` is only 'row' when the walk got past any aria-label/heading
  // landmark, so this function does not need to re-check those.)
  let c = el.closest && el.closest(LM), hops = 0, row = null;
  while (c && hops < 4) {
    const role = c.getAttribute && c.getAttribute('role');
    if (/^(li|tr)$/.test(c.tagName.toLowerCase()) || role === 'listitem') { row = c; break; }
    c = c.parentElement ? c.parentElement.closest(LM) : null;
    hops++;
  }
  if (!row) return '';
  try {
    if (row.id) return 'id:' + row.id;
    const l = row.querySelector('a[href], form[action]');
    if (l) {
      const v = l.getAttribute('href') || l.getAttribute('action') || '';
      if (v && v !== '#' && !/^javascript:/i.test(v)) return 'href:' + v;
    }
    for (const a of Array.from(row.attributes || [])) {
      if (a.name.indexOf('data-') !== 0 || !a.value) continue;
      let shared = 0;
      for (const other of Array.from(document.querySelectorAll(LM))) {
        if (other !== row && other.getAttribute && other.getAttribute(a.name) === a.value) shared++;
      }
      if (shared === 0) return a.name + ':' + a.value;
    }
  } catch (e) {}
  return '';
}
"""

async def _bound_row_id(loc: Locator) -> str:
    """The identity of the row the BOUND element is in. "" on any failure -> the caller fails closed."""
    try:
        return await loc.evaluate(_ROW_OF_JS)
    except Exception:  # noqa: BLE001 — detached/navigating: treat as unknown, which refuses
        return ""


async def resolve(page: Page, spec: LocatorSpec, unique: bool = False,
                  sink: Optional[dict] = None) -> Optional[Locator]:
    """Resolve, then REFUSE if the element we bound is not in the recorded row (see `_resolve`).

    THE ROW GUARD IS A CONTAINMENT CHECK, NOT AN EXISTENCE CHECK, and that distinction is the whole point.
    Asking "does this token still exist somewhere in the DOM?" — which is what 0.64.0 asked — says nothing
    about the element resolution actually returns. Two ordinary shapes made that a wrong-row WRITE:

      * the recorded row SURVIVES but its own control is gone (an already-cancelled order renders a
        `Cancelled` badge instead of a Cancel link). The token is present, so the old gate passed; the only
        remaining `Cancel` belongs to a different customer, so Tier 1 bound it uniquely and outright.
      * the recorded row is HIDDEN rather than removed (`display:none` / `[hidden]` — the ordinary SPA
        client-side delete). The asymmetry is exact: `querySelectorAll` sees hidden rows, `get_by_role` does
        not, so hiding the recorded row is PRECISELY what makes the sibling uniquely matchable.

    In both, the mutation gate then compared `scope_fingerprint` of the element it had just bound — and
    per-row forms are structurally identical, so it matched byte-for-byte and the write fired against
    another record under the recorded row's Idempotency-Key. Measured end to end: `POST /cancel/7` where
    `/cancel/3` was recorded, `_replay_step` returning ok=True.

    Applied HERE, at the one place every tier's result funnels through, rather than at each `return` — the
    recurring defect in this codebase is a guard added to one path and not its siblings, and `_resolve` has
    five returns.
    """
    loc = await _resolve(page, spec, unique=unique, sink=sink)
    if loc is None or not (spec.anchor_source == "row" and spec.anchor_id):
        return loc
    got = await _bound_row_id(loc)
    if got == spec.anchor_id:
        return loc
    # The bind is real and unique — it is simply in the WRONG record. There is nothing to fall back to:
    # any other candidate would be equally unrelated to the row that was recorded.
    if sink is not None:
        sink["bound_by"] = "none"
        sink["row_mismatch"] = f"{spec.anchor_id!r} -> {got or 'no identified row'}"
    return None


async def _resolve(page: Page, spec: LocatorSpec, unique: bool = False,
                   sink: Optional[dict] = None) -> Optional[Locator]:
    """Resolve a spec to a visible Playwright Locator, trying resilient strategies before brittle
    ones. Returns None on drift (nothing resolves). With `unique=True`, an ambiguous candidate
    (count != 1) is never accepted — used by clicks/pinned reads/the mutation gate, where picking the
    wrong `.first` element would silently actuate/return the wrong target, so ambiguity must fail loud.

    `sink` (optional, observability only — zero behaviour change): a dict that receives
    `sink["bound_by"] = <label>` naming WHICH candidate actually bound, one of `testid | role+name |
    role+name~ | placeholder | exact-text | elem_id | css | anchor | ambiguous | none`. Without it, a
    resilience RATE cannot distinguish "still 100%, carried by role+name" from "still 100%, now carried by
    the brittle positional css" — the same number with a much worse safety margin. `drift_bench` gates the
    histogram for exactly that reason.

    Resolution runs in three tiers:
      1. CONFIDENT locators anchored to a stable IDENTITY (test-id, role+name, placeholder, exact
         whole-text, id) — these can't drift onto an unrelated element the way a fuzzy match can — and
         then, strictly LAST, the one fuzzy candidate in this tier: `role+name~`, a case-insensitive
         SUBSTRING match on the accessible name. It earns its place in Tier 1 by re-finding a control
         whose label was lightly augmented, but it must never outrank an exact identity: as the second
         candidate it used to short-circuit placeholder / exact-text / id and bind a decoy whose name
         merely CONTAINED the cached one. The first candidate that resolves uniquely wins.
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
    def _bound(label: str):
        if sink is not None:
            sink["bound_by"] = label

    # --- Tier 1: confident, identity-anchored locators (first unique match wins) ---
    confident: list[tuple[str, Locator]] = []
    if spec.testid:
        confident.append(("testid", page.get_by_test_id(spec.testid)))
    if spec.role in KNOWN_ROLES and spec.name:
        confident.append(("role+name", page.get_by_role(spec.role, name=spec.name, exact=True)))  # type: ignore[arg-type]
    if spec.placeholder:
        confident.append(("placeholder", page.get_by_placeholder(spec.placeholder, exact=True)))
    if spec.text:
        # Exact WHOLE-text match — anchored to the element's own text, so it can't leak into a container.
        # Also tag-scoped (like the Tier-2 substring): exact whole-text still matches ACROSS tags, so a
        # removed roleless <span> "Save" whose exact text reappears as a <p> "Save" would otherwise bind
        # that prose. Scoping to `spec.tag` makes it bind only the SAME kind of element it captured (and
        # falls back to the un-scoped form for legacy specs with no recorded tag).
        exact_text = page.get_by_text(spec.text, exact=True)
        if spec.tag:
            exact_text = exact_text.and_(page.locator(spec.tag))
        confident.append(("exact-text", exact_text))
    if spec.elem_id:
        # `_attr_eq`, never an f-string: `elem_id` is PAGE-CONTROLLED (`el.id`), and an id containing a
        # quote or a backslash otherwise changes what the selector MEANS. Measured, all three shapes:
        #   id='a"b'              -> raw selector is invalid; the exception is swallowed by `classify`, so
        #                            the Tier-1 id candidate is silently LOST (fail-safe, but a lost anchor)
        #   id='a\\b'             -> `\\b` is a CSS escape, so the raw form matches `ab`, a DIFFERENT element
        #   id='zzz"],[id="other' -> the raw form becomes a selector LIST and binds `#other`: count==1, so
        #                            Tier 1 returns it OUTRIGHT. A confident, cross-check-free wrong bind.
        confident.append(("elem_id", page.locator(_attr_eq("id", spec.elem_id))))
    if spec.role in KNOWN_ROLES and spec.name:
        # LAST in Tier 1, deliberately. `get_by_role(exact=False)` is a case-insensitive SUBSTRING match on
        # the accessible name — a fuzzy matcher wearing an identity anchor's clothes. Sitting ABOVE
        # placeholder / exact-text / elem_id, it short-circuited all three (the loop returns the first
        # unique match OUTRIGHT), and being Tier 1 it never reached the css cross-check built to stop
        # exactly this. Measured failure: rename the recorded target's label, and an unrelated control
        # whose name merely CONTAINS the cached one ("Coupon code" for "Code") single-matches and binds —
        # while three intact exact anchors still pointed at the right element. As a `type` step that fills
        # the WRONG field, 0-LLM, reporting success.
        #
        # Demoting rather than deleting keeps what it is actually good for: re-finding a control whose
        # label was lightly AUGMENTED ("Proceed" -> "Proceed now") when no exact anchor survives. Measured
        # at zero cost on the drift corpus — deletion loses a 0-LLM row, this reorder loses none.
        #
        # RESIDUAL, worth stating: when role+name~ is the ONLY surviving Tier-1 candidate and a substring
        # decoy exists, it still binds outright with no corroboration. Closing that needs a css-agreement
        # gate like Tier 2's, which measured at the same cost as full deletion. See HEALING.md.
        confident.append(("role+name~", page.get_by_role(spec.role, name=spec.name, exact=False)))  # type: ignore[arg-type]

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
        elif len(spec.anchor) >= _ANCHOR_MAX:
            # TRUNCATED row text (`anchorOf` slices to 60 chars). We only hold a PREFIX, so no exact
            # comparison is possible and a prefix match is exactly the hole below. An anchor we cannot
            # compare is an anchor we must not use.
            scoped = None
        else:
            # EXACT, not `has_text`. `has_text` is a SUBSTRING match, so a recorded row
            # "Acme Corp #3 Cancel" also matches "Acme Corp #30 Cancel" — and when the recorded row is
            # DELETED, the sibling matches uniquely and binds outright. Measured: it cancelled order #30.
            # The mutation gate cannot catch it either, because per-row forms are structurally identical
            # so the scope fingerprint matches byte-for-byte. `heading` and `label` were hardened against
            # precisely this; `row` — the only source that guards a PER-ROW WRITE — was left loose.
            scoped = landmark.filter(has_text=re.compile(
                r"^\s*" + re.escape(spec.anchor).replace(r"\ ", r"\s+") + r"\s*$"))
        if scoped is not None:
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

    # THE RECORDED ROW MUST STILL EXIST — checked BEFORE Tier 1, and only when we hold a real identity.
    #
    # The anchor was only ever consulted at Tier 3, i.e. when role+name was AMBIGUOUS. But deleting the
    # recorded row is exactly what makes role+name UNIQUE: with "Acme Corp #3" gone, the one remaining
    # "Cancel" link matches count==1 and Tier 1 returns it outright. Measured — it bound a different
    # customer's Cancel and reported success. The anchor that would have objected never ran, and the
    # mutation gate cannot help: per-row forms are structurally identical, so the scope fingerprint matches
    # byte-for-byte.
    #
    # Keyed on `anchor_id`, NOT on row text. Text is not an identity: a price or status changing does not
    # make it a different record, so a text comparison cannot tell "this row was edited" from "this row is
    # gone". Measured — a text-keyed version of this check cost 4 rows of 0-LLM survival at k1-k3 on the
    # drift corpus by refusing rows that had merely been edited.
    #
    # NO identity captured (an old cached flow, or a row offering no id/data-*/href) -> NO refusal. We
    # cannot distinguish the two cases, and failing loud on every token-less row would trade a targeted
    # safety fix for a broad availability regression. That half stays open and is stated in HEALING.md.
    # Deliberately not applied to `heading`/`label` either: a renamed section heading is a COSMETIC drift
    # the resolver is required to survive (v1's `heading-renamed`), whereas a row vanishing is semantic.
    # (The row guard itself lives in `resolve`, which wraps this function — it must run on the element
    # actually BOUND, and this function has five returns. See `resolve`'s docstring for why the previous
    # pre-flight EXISTENCE query here was not a guard at all.)

    # Tier 1: a confident unique match wins outright; record the first ambiguous for the lenient fallback.
    ambiguous_label = ""
    for label, loc in confident:
        kind, first = await classify(loc)
        if kind == "unique":
            _bound(label)
            return first
        if kind == "ambiguous" and not unique and ambiguous is None:
            ambiguous, ambiguous_label = first, label

    # Tier 2: reconcile the two guesses.
    fu = cu = None
    if fuzzy_text is not None:
        kind, first = await classify(fuzzy_text)
        if kind == "unique":
            fu = first
        elif kind == "ambiguous" and not unique and ambiguous is None:
            ambiguous, ambiguous_label = first, "substring"
    if css_loc is not None:
        kind, first = await classify(css_loc)
        if kind == "unique":
            cu = first
        elif kind == "ambiguous" and not unique and ambiguous is None:
            ambiguous, ambiguous_label = first, "css"
    if cu is not None and (fu is None or await _same_element(fu, cu)):
        # css resolves uniquely and the fuzzy guess does NOT contradict it (absent / ambiguous / agrees).
        # css is a structural locator, so trust it — this is what recovers a renamed target
        # (`target-renamed`, `span-renamed`) where the tag-scoped substring rightly finds nothing.
        if not await _testid_contradicted(cu, spec.testid):
            _bound("css")
            return cu
        # ...UNLESS the element it bound positively FALSIFIES the recorded identity. A positional path
        # whose target was REMOVED re-matches whatever same-tag sibling slid into the slot, and if that
        # element happens to reach a plausible page the run reports success — a silently wrong action.
        # Fall through instead of returning: a critical caller (`unique=True`, which is every production
        # call site) fails loud, a lenient one keeps css as a best-effort guess.
        if sink is not None:
            sink["identity_contradiction"] = True   # distinct from `conflict`: identity, not cross-check
        if not unique and ambiguous is None:
            ambiguous, ambiguous_label = cu, "css"
    # `elif` from here on, and it is load-bearing: the branch above no longer always returns, so without
    # the chain a testid-contradicted css would ALSO fall into the cross-check branch and set
    # `sink["conflict"]`, conflating an identity refusal with a two-guesses-disagreed refusal.
    elif cu is not None and fu is not None:
        # Both resolve uniquely but to DIFFERENT elements (a same-tag sibling that shares the cached
        # substring vs a drifted positional css pointing at a moved-in neighbor). Neither is trustworthy
        # -> fail loud (unique); lenient keeps css as a best-effort structural guess.
        if sink is not None:
            sink["conflict"] = True   # the two Tier-2 guesses disagreed (why a `none` was a fail-loud)
        if not unique and ambiguous is None:
            ambiguous, ambiguous_label = cu, "css"
    elif fu is not None:
        # Only the FUZZY substring resolved (css is gone or itself ambiguous). On its own it may have
        # matched a same-tag DECOY that merely shares the cached substring (the target's own text changed),
        # with nothing to corroborate it — so it is NOT trusted for a critical bind and fails loud. Lenient
        # callers keep it as a last-ditch guess.
        if not unique and ambiguous is None:
            ambiguous, ambiguous_label = fu, "substring"

    # Tier 3: neighbor anchor.
    if anchor_loc is not None:
        kind, first = await classify(anchor_loc)
        if kind == "unique":
            _bound("anchor")
            return first
        if kind == "ambiguous" and not unique and ambiguous is None:
            ambiguous, ambiguous_label = first, "anchor"

    if unique or ambiguous is None:
        _bound("none")
        return None
    _bound(f"ambiguous:{ambiguous_label}")
    return ambiguous
