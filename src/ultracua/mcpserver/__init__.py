"""H2 flows-as-tools — expose approved ultracua flows to any MCP client (Claude, Cursor, VS Code, …).

Stage 1 (this module): a stdio MCP server that registers every **approved READ flow** as one typed,
zero-argument tool. A tool call dispatches to `flows.replay(require_approved=True, on_drift="raise",
check_shape=True)` — the safety-gated Flow API, NEVER the raw `daemon.server` engine (whose `run`
bypasses the approval / write / shape / health gates by documented contract). So one deterministic,
verified tool call replaces an outer agent LLM-orchestrating dozens of per-step browser primitives.

Deliberately out of scope for stage 1 (see ROADMAP H2):
- WRITE flows are **default-deny**: never registered. (Stage 2 adds opt-in exposure behind MCP
  elicitation + a completed-run ledger + a per-flow mutex — none of which exist yet.)
- learn / approve / record / unapprove are **never** tools — a calling agent must not be able to
  approve or author flows (no self-approval); the server only *runs* already-approved read flows.
- Typed inputs (slots) and per-caller credentials wait on H3 + the auth daemon; every tool is
  zero-argument (one tool per learned literal flow) and every caller rides the operator's identity.

The core (`list_flow_tools`, `call_flow_tool`) is pure and SDK-free so it imports and unit-tests
without the `mcp` package; only `serve()` needs the SDK (`uv sync --group mcp`).

Known gap inherited from `replay()` (deferred to H9 value-contracts, not fixed here): a read whose
answer is a LIST can, on a page longer than the extractor's ~12k-char window, come back
*silently short* — the extraction is flagged truncated but still reported found, and the shape check
is count-agnostic, so a tool call returns a confidently-complete-looking but incomplete list. Scalar
reads and shorter pages are unaffected; a truncated *not-found* fails loud (a DriftError) as usual.
Until H9 fails these loud on a count drop, prefer a pinned/scalar read for list-shaped answers that
feed anything downstream, and don't advertise MCP list tools as completeness-guaranteed.
"""

from .server import FlowTool, ToolOutcome, call_flow_tool, list_flow_tools, serve

__all__ = ["FlowTool", "ToolOutcome", "call_flow_tool", "list_flow_tools", "serve"]
