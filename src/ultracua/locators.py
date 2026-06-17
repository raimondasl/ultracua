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


# Runs in the page. Mirrors snapshot.py's role/name derivation (kept in sync by hand —
# the two must agree for replay to find what learning saw) and adds a short css path.
DESCRIBE_JS = r"""
(ref) => {
  const el = document.querySelector('[data-ultracua-ref="' + ref + '"]');
  if (!el) return null;
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
      e.getAttribute('aria-label') ||
      e.getAttribute('placeholder') ||
      e.getAttribute('title') ||
      e.getAttribute('alt') ||
      (e.value ? String(e.value) : '') ||
      e.innerText ||
      e.textContent ||
      '';
    return cand.replace(/\s+/g, ' ').trim().slice(0, 120);
  };
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
  return {
    role: roleOf(el),
    name: nameOf(el),
    tag: el.tagName.toLowerCase(),
    elem_id: el.id || null,
    testid: el.getAttribute('data-testid'),
    placeholder: el.getAttribute('placeholder'),
    text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
    css: cssPath(el),
  };
}
"""


async def describe(page: Page, ref: str) -> Optional[LocatorSpec]:
    """Capture a resilient LocatorSpec for the element currently tagged with `ref`."""
    raw = await page.evaluate(DESCRIBE_JS, ref)
    if not raw:
        return None
    return LocatorSpec(**raw)


async def resolve(page: Page, spec: LocatorSpec) -> Optional[Locator]:
    """Resolve a spec to a unique, visible Playwright Locator, trying resilient
    strategies before brittle ones. Returns None on drift (nothing resolves)."""
    candidates: list[Locator] = []
    if spec.testid:
        candidates.append(page.get_by_test_id(spec.testid))
    if spec.role in KNOWN_ROLES and spec.name:
        candidates.append(page.get_by_role(spec.role, name=spec.name, exact=True))  # type: ignore[arg-type]
        candidates.append(page.get_by_role(spec.role, name=spec.name, exact=False))  # type: ignore[arg-type]
    if spec.placeholder:
        candidates.append(page.get_by_placeholder(spec.placeholder, exact=True))
    if spec.text:
        candidates.append(page.get_by_text(spec.text, exact=False))
    if spec.elem_id:
        candidates.append(page.locator(f'[id="{spec.elem_id}"]'))
    if spec.css:
        try:
            candidates.append(page.locator(spec.css))
        except Exception:
            pass

    for loc in candidates:
        try:
            if await loc.count() == 0:
                continue
            first = loc.first
            if await first.is_visible():
                return first
        except Exception:
            continue
    return None
