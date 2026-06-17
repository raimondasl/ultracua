"""JSON-RPC server over stdio.

Protocol: one JSON object per line.
  request:  {"jsonrpc":"2.0","id":N,"method":"...","params":{...}}
  response: {"jsonrpc":"2.0","id":N,"result":{...}}  or  {... ,"error":{"code":...,"message":...}}

Methods:
  - "health"        -> {status, version}
  - "run"           -> drive run_cached (learn/replay/auto); returns the FlowReport summary
  - "cache.delete"  -> delete a cached flow by (goal, url, scope)

Requests are handled sequentially (the browser is the bottleneck); stdin is read in a
worker thread so the asyncio loop (which Playwright needs) stays free.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from .. import __version__
from ..cache import FlowCache, flow_key
from ..flow import run_cached
from ..providers import get_provider


def _cache(params: dict) -> FlowCache:
    root = params.get("cache_root")
    return FlowCache(root=root) if root else FlowCache()


async def _run(params: dict) -> dict:
    provider = get_provider(params["provider"]) if params.get("provider") else None
    grounding = None
    if params.get("grounding") == "anthropic":
        from ..vision import AnthropicGrounding

        grounding = AnthropicGrounding()
    report = await run_cached(
        params["url"],
        params["goal"],
        provider,
        _cache(params),
        mode=params.get("mode", "auto"),
        scope=params.get("scope", "default"),
        headless=params.get("headless", True),
        max_steps=params.get("max_steps"),
        grounding=grounding,
    )
    return {
        "mode": report.mode,
        "success": report.success,
        "llm_calls": report.llm_calls,
        "healed_steps": report.healed_steps,
        "total_ms": round(report.total_ms, 1),
        "avg_step_ms": round(report.avg_step_ms, 1),
        "final_text": report.final_text,
        "note": report.note,
    }


async def _dispatch(method: str, params: dict) -> Any:
    if method == "health":
        return {"status": "ok", "version": __version__}
    if method == "run":
        return await _run(params)
    if method == "cache.delete":
        key = flow_key(params["goal"], params["url"], params.get("scope", "default"))
        return {"deleted": _cache(params).delete(key)}
    raise ValueError(f"unknown method: {method!r}")


async def serve() -> None:
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if line == "":  # EOF -> client closed stdin
            break
        line = line.strip()
        if not line:
            continue
        rid: Any = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            result = await _dispatch(req["method"], req.get("params") or {})
            resp = {"jsonrpc": "2.0", "id": rid, "result": result}
        except Exception as exc:  # noqa: BLE001 - report any failure as a JSON-RPC error
            resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(exc)}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


def main() -> None:
    asyncio.run(serve())
