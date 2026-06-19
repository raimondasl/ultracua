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

# Runs in the browser. Returns viewport-visible interactable elements, tagging each
# with a `data-ultracua-ref` attribute so Python can act on it via a stable selector.
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
  const nameOf = (el) => {
    const cand =
      el.getAttribute('aria-label') ||
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      el.getAttribute('alt') ||
      (el.value ? String(el.value) : '') ||
      el.innerText ||
      el.textContent ||
      '';
    return cand.replace(/\s+/g, ' ').trim().slice(0, 120);
  };
  const sel = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]',
    '[role=checkbox]', '[role=radio]', '[role=combobox]', '[role=switch]',
    '[contenteditable=""]', '[contenteditable="true"]', '[onclick]',
  ].join(',');
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= MAX) break;
    if (el.disabled) continue;
    if (!isVisible(el)) continue;
    const ref = 'e' + i++;
    el.setAttribute('data-ultracua-ref', ref);
    const r = el.getBoundingClientRect();
    out.push({
      ref,
      role: roleOf(el),
      name: nameOf(el),
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type'),
      value: (el.value != null ? String(el.value) : null),
      bbox: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    });
  }

  // Second pass: leaf elements clickable only via JS listeners / cursor:pointer
  // (e.g. <span> "links" with no onclick attribute, as MiniWoB++ uses). Skip already-
  // collected nodes; the leaf + short-text guards keep this cheap and avoid huge containers.
  if (out.length < MAX) {
    for (const el of document.querySelectorAll('*')) {
      if (out.length >= MAX) break;
      if (el.hasAttribute('data-ultracua-ref')) continue;
      if (el.children.length > 0) continue;
      const txt = (el.innerText || el.textContent || '').trim();
      if (!txt || txt.length > 60) continue;
      if (!isVisible(el)) continue;
      if (window.getComputedStyle(el).cursor !== 'pointer') continue;
      const ref = 'e' + i++;
      el.setAttribute('data-ultracua-ref', ref);
      const r = el.getBoundingClientRect();
      out.push({
        ref,
        role: 'link',
        name: txt.replace(/\s+/g, ' ').slice(0, 120),
        tag: el.tagName.toLowerCase(),
        type: null,
        value: null,
        bbox: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      });
    }
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
  const roleOf = (e) => {
    const ar = e.getAttribute('role');
    if (ar) return ar;
    const t = e.tagName.toLowerCase();
    if (t === 'a') return 'link';
    if (t === 'button') return 'button';
    if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      const ty = (e.getAttribute('type') || 'text').toLowerCase();
      if (['button', 'submit', 'reset', 'image'].includes(ty)) return 'button';
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      return 'textbox';
    }
    return t;
  };
  const nameOf = (e) => {
    const cand =
      e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.getAttribute('title') ||
      e.getAttribute('alt') || e.innerText || e.textContent || '';
    return cand.replace(/\s+/g, ' ').trim().slice(0, 120);
  };
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
    # Fingerprint over structural signal (role/name/tag + url), NOT coordinates or page text
    # — bboxes/text drift but structure is the thing we want to detect change against.
    basis = json.dumps([[e.role, e.name, e.tag] for e in elements], ensure_ascii=False)
    fingerprint = xxhash.xxh64((url + "\n" + basis).encode("utf-8")).hexdigest()
    return Observation(url=url, title=title, elements=elements, text=text, fingerprint=fingerprint)
