"""ultracua daemon — the language-agnostic surface (PLAN.md core+bindings / Phase 4).

The Python core is exposed over newline-delimited JSON-RPC on stdio, so any language can
drive it (a Node/JS client lives in `clients/node/`). The process stays warm across calls
(provider + cache reused); the browser is the per-call bottleneck.
"""

from __future__ import annotations

from .server import main, serve

__all__ = ["main", "serve"]
