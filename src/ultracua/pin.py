"""Pinned 0-LLM reads (Phase H slice).

A data flow's replay normally makes one LLM extraction call to read the answer off the final page.
For a SCALAR answer that is exactly some element's text, we can instead pin a resilient locator to
that element at learn time and read it deterministically on replay — **truly 0-LLM** (no model call,
no API key), sub-second, and free.

Best-effort + opt-in: a pin is recorded only when the value maps to **exactly one** element (verified
by reading it back); otherwise the flow keeps using the LLM extractor. A pin that no longer resolves
on replay fails loud (the caller re-learns), so it never returns a wrong value.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .locators import LocatorSpec, resolve

# Runs in the page. Given a target string, returns a resilient locator for the UNIQUE deepest element
# whose collapsed text equals it (or null if there are zero or multiple such elements).
_PIN_JS = r"""
(target) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const t = norm(target);
  if (!t) return null;
  const matches = [];
  for (const el of document.querySelectorAll('body, body *')) {
    if (norm(el.innerText || el.textContent) !== t) continue;
    let deeper = false;
    for (const c of el.children) {
      if (norm(c.innerText || c.textContent) === t) { deeper = true; break; }
    }
    if (!deeper) matches.push(el);   // a "leaf-most" element holding exactly the value
  }
  if (matches.length !== 1) return null;   // 0 or ambiguous -> don't pin
  const el = matches[0];
  const cssPath = (e) => {
    const parts = [];
    while (e && e.nodeType === 1 && parts.length < 6) {
      if (e.id) { parts.unshift('#' + CSS.escape(e.id)); break; }
      let part = e.tagName.toLowerCase();
      const p = e.parentElement;
      if (p) {
        const sibs = Array.from(p.children).filter((c) => c.tagName === e.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(e) + 1) + ')';
      }
      parts.unshift(part);
      e = e.parentElement;
    }
    return parts.join(' > ');
  };
  return { tag: el.tagName.toLowerCase(), elem_id: el.id || null,
           testid: el.getAttribute('data-testid'), css: cssPath(el) };
}
"""


def _parse(text: str, value_type: str) -> Optional[Any]:
    """Parse the live element text to the value's type. STRICT: the text must contain exactly ONE
    well-formed numeric token (optionally thousands-grouped), else return None — so a format drift
    (a second number, scientific notation, a range/date, a locale change) fails loud rather than
    fabricating a wrong value. Returns the stripped string for str pins."""
    s = " ".join((text or "").split()).strip()
    if value_type not in ("int", "float"):
        return s
    num_re = (r"-?\d{1,3}(?:,\d{3})+|-?\d+" if value_type == "int"
              else r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?|-?\.\d+")
    nums = re.findall(num_re, s)
    if len(nums) != 1:  # zero or several numeric tokens -> not a clean scalar -> fail loud
        return None
    try:
        raw = nums[0].replace(",", "")
        return int(raw) if value_type == "int" else float(raw)
    except ValueError:  # belt-and-suspenders; the regex already constrains the shape
        return None


async def find_pin(page, value: Any) -> Optional[dict]:
    """If `value` (a scalar) maps to exactly one element's text, return a verified pin
    `{locator, value_type}`; else None (the caller keeps using the LLM extractor)."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None  # only scalar str/int/float are pinnable in this slice
    raw = await page.evaluate(_PIN_JS, str(value))
    if not raw:
        return None
    if not (raw.get("elem_id") or raw.get("testid")):
        # Refuse a purely POSITIONAL anchor (css nth-of-type only): after a layout shift it would
        # resolve to a *different* element and return a wrong value. Pin only on a content-stable
        # id / data-testid; otherwise the flow keeps using the LLM extractor.
        return None
    pin = {"locator": raw, "value_type": type(value).__name__}
    if await read_pin(page, pin) != value:  # verify the pin round-trips to the learned value
        return None
    return pin


async def read_pin(page, pin: dict) -> Optional[Any]:
    """Resolve the pinned locator and read its current text as the value's type (None if unresolved)."""
    loc = pin.get("locator") or {}
    tag = loc.get("tag", "") or ""
    spec = LocatorSpec(role=tag, name="", tag=tag, elem_id=loc.get("elem_id"),
                       testid=loc.get("testid"), css=loc.get("css"), text=None)  # never anchor on the value
    resolved = await resolve(page, spec, unique=True)  # ambiguity must fail loud, never pick .first
    if resolved is None:
        return None
    try:
        text = await resolved.inner_text()
    except Exception:  # noqa: BLE001
        return None
    return _parse(text, pin.get("value_type", "str"))
