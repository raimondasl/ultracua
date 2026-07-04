"""Reusable structured extraction: read the final page and return the data a goal asks for.

One LLM call turns the page text into structured data — the read counterpart to the agent loop.
Lifted to core (from the WebArena benchmark runner) so any Flow can extract, not just the
benchmark. Multi-provider via the llm Router.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .llm.base import Router
from .llm.types import LLMRequest, Message, TextBlock, ToolDef
from .obs import get_logger

_log = get_logger("extract")

_SYSTEM = (
    "You extract structured data from a web page. Return exactly what the INSTRUCTION asks for "
    "via the `submit` tool, in the shape it specifies (units, fields, ordering). Put the value in "
    "`data` as a scalar or a FLAT list — never a nested array. If the data is not present on the "
    "page, set found=false with a short `error`. Never invent values."
)


@dataclass
class Extraction:
    found: bool
    data: Any = None
    error: Optional[str] = None
    # The page text was longer than `max_chars` and was cut before the LLM saw it, so the answer (or
    # tail list items) past the cut were invisible to the extractor. A truncated extraction is NOT a
    # trustworthy result — a caller must treat it as suspect (fail loud on a false "not found", flag a
    # possibly-short list), never as a clean read. Set here so truncation is reported, never silent.
    truncated: bool = False


def _input_schema(data_schema: Optional[dict]) -> dict:
    return {
        "type": "object",
        "properties": {
            "found": {"type": "boolean", "description": "true iff the requested data is on the page"},
            "data": data_schema or {"description": "the requested data; null if not found"},
            "error": {"type": ["string", "null"]},
        },
        "required": ["found"],
        "additionalProperties": False,
    }


async def tool_extract(
    router: Router, *, system: str, tool: ToolDef, user_text: str,
    tier: str = "strong", max_tokens: int = 1500,
) -> Optional[dict]:
    """Shared extraction mechanism: one LLM call that FORCES `tool` and returns its input dict
    (or None if the model returned no tool call). The building block for both the generic
    `extract` below and any task-specific extractor (e.g. the WebArena runner)."""
    req = LLMRequest(
        system=system, tools=[tool], force_tool=tool.name,
        messages=[Message("user", [TextBlock(user_text)])], max_tokens=max_tokens,
    )
    resp = await router.complete(req, tier=tier)
    tu = resp.tool_use(tool.name)
    return dict(tu.input) if tu is not None else None


async def extract(
    router: Router,
    instruction: str,
    page_text: str,
    *,
    schema: Optional[dict] = None,
    tier: str = "strong",
    max_chars: int = 12000,
) -> Extraction:
    """Extract the data `instruction` asks for from `page_text` via one LLM call.

    `schema` (optional JSON schema for the `data` field) constrains the output shape. Returns an
    `Extraction(found, data, error)` — `found=False` when the data isn't on the page.
    """
    joined = " ".join((page_text or "").split())
    text = joined[:max_chars]
    truncated = len(joined) > max_chars
    if not text:
        return Extraction(found=False, error="empty page")
    if truncated:
        # Not fatal here (a scalar answer near the top is still fine) — but never silent: surface it so
        # the caller can fail loud / flag an incomplete list. See flows.py's finalize.
        _log.warning(
            "page text is %d chars (> max_chars=%d) — TRUNCATED before extraction; any answer or list "
            "items past the cut are invisible to the extractor", len(joined), max_chars,
        )
    tool = ToolDef(
        name="submit",
        description="Return the data extracted from the page.",
        input_schema=_input_schema(schema),
        strict=False,
    )
    d = await tool_extract(
        router, system=_SYSTEM, tool=tool,
        user_text=f"INSTRUCTION: {instruction}\n\nPAGE TEXT:\n{text}", tier=tier,
    )
    if d is None:
        return Extraction(found=False, error="extractor returned no tool call", truncated=truncated)
    data = d.get("data")
    # Unwrap a spurious extra nesting level ([["x"]] -> ["x"]).
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], list):
        data = data[0]
    found = bool(d.get("found")) if "found" in d else (data not in (None, [], ""))
    return Extraction(found=found, data=data, error=d.get("error"), truncated=truncated)
