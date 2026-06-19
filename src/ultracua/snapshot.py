"""Scoped, sanitized DOM/accessibility snapshot.

Per PLAN.md, the snapshot pipeline must keep Python light: the DOM walk, visibility
filtering, and ref assignment all run *inside the page* (V8) via a single injected JS
call, and Python only receives the compact result (~viewport interactable elements).
This is what stops the DOM build from becoming the bottleneck (full AX snapshots take
3-26s on heavy SPAs) and keeps the host language off the hot path.

Phase 1 will add dirty-region diffing and persistent ref IDs across steps; Phase 0
captures a fresh scoped snapshot each step.
"""

from __future__ import annotations

import json

import xxhash

from .types import Element, Observation

# Role + accessible-name derivation, SHARED across the snapshot, scope-fingerprint, and locator JS
# (imported by locators.py) so all three agree by construction — they MUST, since replay resolves by
# the same role+name learning saw, and Playwright's get_by_role matches the computed accessible name.
# `roleOf`/`nameOf` take one element arg (named `el` internally; callers may pass any variable).
_ROLEOF_JS = r"""
  const roleOf = (el) => {
    const ar = el.getAttribute('role');
    if (ar) return ar;
    const t = el.tagName.toLowerCase();
    if (t === 'a') return 'link';
    if (t === 'button') return 'button';
    if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      const ty = (el.getAttribute('type') || 'text').toLowerCase();
      if (['button', 'submit', 'reset', 'image'].includes(ty)) return 'button';
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      return 'textbox';
    }
    return t;
  };
"""

# Accessible name (a practical subset of W3C AccName): aria-labelledby -> aria-label -> the element's
# <label> (for= / wrapping) -> placeholder/title/alt/value -> text. Folding in the label is the key
# upgrade — it's how real forms name inputs, and it makes our captured name match get_by_role's.
_ACCNAME_JS = r"""
  const nameOf = (el) => {
    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const lb = el.getAttribute && el.getAttribute('aria-labelledby');
    if (lb) {
      const t = norm(lb.split(/\s+/).map((id) => {
        const e = document.getElementById(id);
        return e ? (e.innerText || e.textContent || '') : '';
      }).join(' '));
      if (t) return t.slice(0, 120);
    }
    const al = el.getAttribute && norm(el.getAttribute('aria-label'));
    if (al) return al.slice(0, 120);
    if (el.id && window.CSS && CSS.escape) {
      const lf = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lf) { const t = norm(lf.innerText || lf.textContent); if (t) return t.slice(0, 120); }
    }
    // A wrapping <label> names ONLY its single labelable control in real AccName — applying it to a
    // label that wraps several controls would give them all the same name, and get_by_role would then
    // resolve the wrong one with count==1 (defeating resolve()'s ambiguity guard).
    const wrap = el.closest && el.closest('label');
    if (wrap && wrap.querySelectorAll('input, select, textarea').length <= 1) {
      const t = norm(wrap.innerText || wrap.textContent);
      if (t) return t.slice(0, 120);
    }
    // A control's value / selected text is NOT its accessible name (get_by_role ignores it), so we do
    // NOT fall back to el.value; placeholder/title/alt, then text content (correct for button/link).
    const cand = (el.getAttribute && (el.getAttribute('placeholder') || el.getAttribute('title') ||
                 el.getAttribute('alt'))) || el.innerText || el.textContent || '';
    return norm(cand).slice(0, 120);
  };
"""


# Runs in the browser. Returns viewport-visible interactable elements in READING ORDER (top-to-bottom,
# left-to-right), tagging each with a `data-ultracua-ref` so Python can act on it via a stable selector.
SNAPSHOT_JS = r"""
(MAX) => {
  const isVisible = (el) => {
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) return false;
    return true;
  };
""" + _ROLEOF_JS + _ACCNAME_JS + r"""
  const sel = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]',
    '[role=checkbox]', '[role=radio]', '[role=combobox]', '[role=switch]',
    '[contenteditable=""]', '[contenteditable="true"]', '[onclick]',
  ].join(',');

  // Collect first (no refs yet), then sort into reading order, THEN assign e0..eN — so refs and the
  // structural fingerprint follow visual order, not DOM order (a visually-early span-link no longer
  // lands last after the cursor:pointer pass). `seen` dedups across the two passes.
  const seen = new Set();
  const cands = [];
  const add = (el, role, name) => {
    if (seen.has(el)) return;
    seen.add(el);
    const r = el.getBoundingClientRect();
    cands.push({ el, role, name, tag: el.tagName.toLowerCase(),
                 type: el.getAttribute('type'),
                 value: (el.value != null ? String(el.value) : null),
                 bbox: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  };

  for (const el of document.querySelectorAll(sel)) {
    if (cands.length >= MAX) break;
    if (el.disabled) continue;
    if (!isVisible(el)) continue;
    add(el, roleOf(el), nameOf(el));
  }

  // Second pass: leaf elements clickable only via JS listeners / cursor:pointer (e.g. <span> "links"
  // with no onclick attribute, as MiniWoB++ uses). The leaf + short-text guards keep this cheap.
  if (cands.length < MAX) {
    for (const el of document.querySelectorAll('*')) {
      if (cands.length >= MAX) break;
      if (seen.has(el)) continue;
      if (el.children.length > 0) continue;
      const txt = (el.innerText || el.textContent || '').trim();
      if (!txt || txt.length > 60) continue;
      if (!isVisible(el)) continue;
      if (window.getComputedStyle(el).cursor !== 'pointer') continue;
      add(el, 'link', txt.replace(/\s+/g, ' ').slice(0, 120));
    }
  }

  // Reading order: group into ~rows by y (band), then left-to-right by x; original collection index
  // breaks ties so co-located elements stay deterministic (a stable fingerprint).
  const BAND = 12;
  cands.forEach((c, idx) => { c._i = idx; });
  cands.sort((a, b) => {
    const ay = Math.round(a.bbox[1] / BAND), by = Math.round(b.bbox[1] / BAND);
    if (ay !== by) return ay - by;
    if (a.bbox[0] !== b.bbox[0]) return a.bbox[0] - b.bbox[0];
    return a._i - b._i;
  });

  const out = [];
  let i = 0;
  for (const c of cands) {
    const ref = 'e' + (i++);
    c.el.setAttribute('data-ultracua-ref', ref);
    out.push({ ref, role: c.role, name: c.name, tag: c.tag, type: c.type, value: c.value, bbox: c.bbox });
  }

  const pageText = (document.body && document.body.innerText ? document.body.innerText : '')
    .replace(/\s+/g, ' ').trim().slice(0, 1500);
  return { elements: out, text: pageText };
}
"""


# Runs in the page on ONE element: fingerprints the interactable controls in the target's
# enclosing form/section. The mutation gate compares this (not the whole page) so unrelated
# churn — a banner, a cart badge, an A/B nav item — doesn't false-flag a valid write as drift.
SCOPE_JS = r"""
(el) => {
""" + _ROLEOF_JS + _ACCNAME_JS + r"""
  const sel = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]',
    '[role=checkbox]', '[role=radio]', '[role=combobox]', '[role=switch]',
  ].join(',');
  const scope = el.closest('form, dialog, [role=dialog], fieldset, [role=form], section, main, [role=main], article') || document.body;
  const out = [];
  for (const e of scope.querySelectorAll(sel)) out.push([roleOf(e), nameOf(e), e.tagName.toLowerCase()]);
  return out;
}
"""


async def scope_fingerprint(locator) -> str:
    """Fingerprint the interactables in the target locator's enclosing form/section (the precise
    mutation-gate precondition). Returns "" if it can't be computed (caller falls back)."""
    try:
        out = await locator.evaluate(SCOPE_JS)
    except Exception:  # noqa: BLE001 - target gone / detached -> caller treats as no scope
        return ""
    if not out:
        return ""
    return xxhash.xxh64(json.dumps(out, ensure_ascii=False).encode("utf-8")).hexdigest()


# Structural write-signal for a CLICK target: does activating it submit a form, and with what method?
# (`<button>` defaults to type=submit inside a form; `<input type=submit|image>` submits.) The mutation
# classifier judges a real form submit by its method — GET=read, POST/PUT/DELETE/PATCH=write.
_MUTATION_CTX_JS = r"""
(el) => {
  const tag = (el.tagName || '').toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  const isSubmit =
    type === 'submit' ||
    (tag === 'button' && (type === '' || type === 'submit')) ||
    (tag === 'input' && (type === 'submit' || type === 'image'));
  const form = el.closest('form');
  const method = form ? (form.getAttribute('method') || 'get').toLowerCase() : '';
  return { submit: !!isSubmit, form_method: method };
}
"""


async def mutation_context(locator) -> dict:
    """Probe a click target for its write-signal: `{submit, form_method}`. Returns {} on failure (the
    caller then falls back to the keyword heuristic). See `safety.classify_mutation`."""
    try:
        return await locator.evaluate(_MUTATION_CTX_JS)
    except Exception:  # noqa: BLE001 - target gone / detached -> no structural signal
        return {}


async def capture(page, max_elements: int) -> Observation:
    """Capture a scoped snapshot of the given Playwright page."""
    raw = await page.evaluate(SNAPSHOT_JS, max_elements)
    elements = [Element(**e) for e in raw["elements"]]
    text = raw.get("text", "")
    url = page.url
    title = await page.title()
    # Fingerprint over the structural signal (role/name/tag + url), NOT coordinates or page text.
    # Sort the triples to an order-INVARIANT multiset before hashing: `elements` is in reading order
    # (for the agent + ref assignment), but a layout-only reshuffle — the sort reacting to a 1px nudge
    # — must NOT masquerade as drift. Real structural change (an element added/removed/renamed) still
    # changes the sorted multiset.
    basis = json.dumps(sorted([e.role, e.name, e.tag] for e in elements), ensure_ascii=False)
    fingerprint = xxhash.xxh64((url + "\n" + basis).encode("utf-8")).hexdigest()
    return Observation(url=url, title=title, elements=elements, text=text, fingerprint=fingerprint)
