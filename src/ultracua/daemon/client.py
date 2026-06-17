"""In-language Python client for the ultracua daemon — spawns it as a subprocess and
speaks JSON-RPC over its stdio. (Other languages use the same protocol; see clients/node/.)"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Optional


class DaemonClient:
    def __init__(self, command: Optional[list[str]] = None) -> None:
        # Default: launch the daemon with the current interpreter.
        self.command = command or [sys.executable, "-m", "ultracua.daemon"]
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._id = 0

    async def start(self) -> "DaemonClient":
        self.proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        return self

    async def call(self, method: str, params: Optional[dict] = None) -> Any:
        assert self.proc is not None and self.proc.stdin and self.proc.stdout
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        self.proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()
        line = await self.proc.stdout.readline()
        if not line:
            raise RuntimeError("daemon closed the connection")
        resp = json.loads(line.decode("utf-8"))
        if resp.get("error"):
            raise RuntimeError(resp["error"]["message"])
        return resp["result"]

    async def close(self) -> None:
        if self.proc is not None:
            if self.proc.stdin:
                self.proc.stdin.close()
            await self.proc.wait()

    async def __aenter__(self) -> "DaemonClient":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.close()
