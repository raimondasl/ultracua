"""WebMCP tier (PLAN.md Phase 4): consume a site's native agent tools where it exposes them.

WebMCP is an emerging browser-native standard (W3C draft; Chrome 146 / Edge 147, 2026) where
a page publishes structured tools an agent can call directly — skipping DOM scraping (~89%
fewer tokens, ~98% accuracy on structured calls). Real-world coverage is still near-zero, so
this detects a plausible interface (`window.webmcp` / `window.mcp` / `navigator.mcp` exposing
`listTools()` + `callTool(name, args)`) and invokes it. Where present it's the top, fastest
tier (above cached selector → DOM/AX → vision).

Detection + invocation are wired here and exercised end to end (learn → replay) via the
`webmcp_call` action; surfacing the tool list to the LLM for automatic selection is the
remaining integration, gated on real-world WebMCP adoption.
"""

from __future__ import annotations

from typing import Any, Optional

_DETECT_JS = r"""
() => {
  const api = window.webmcp || window.mcp || (window.navigator && window.navigator.mcp);
  if (!api || typeof api.listTools !== 'function') return null;
  try {
    const tools = api.listTools();
    return (Array.isArray(tools) ? tools : []).map((t) => ({
      name: String(t.name || ''),
      description: String(t.description || ''),
    }));
  } catch (e) {
    return null;
  }
}
"""

_CALL_JS = r"""
async ([name, args]) => {
  const api = window.webmcp || window.mcp || (window.navigator && window.navigator.mcp);
  if (!api || typeof api.callTool !== 'function') throw new Error('no WebMCP callTool');
  const r = await api.callTool(name, args || {});
  return r === undefined ? null : r;
}
"""


async def detect(page) -> Optional[list[dict]]:
    """Return the page's WebMCP tools ([{name, description}, ...]), or None if not exposed."""
    try:
        return await page.evaluate(_DETECT_JS)
    except Exception:
        return None


async def call(page, name: str, args: Optional[dict] = None) -> Any:
    """Invoke a WebMCP tool by name with args."""
    return await page.evaluate(_CALL_JS, [name, args or {}])
