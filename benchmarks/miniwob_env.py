"""MiniWoB++ adapter: drive the public deterministic benchmark with ultracua.

Loads MiniWoB++ task HTML (bundled with the `miniwob` package) over a local static
server, seeds a deterministic instance (`Math.seedrandom(seed)` + `core.startEpisodeReal`),
exposes the instruction (`#query`) as the goal, and reads the reward (`WOB_RAW_REWARD_GLOBAL`)
as the correctness oracle. ultracua stays the driver — it just navigates to the task URL.

We target tasks whose targets are *semantic* (button/input) and snapshot-visible; MiniWoB's
`<span class="alink">` link tasks (d3 `.on('click')`, no `onclick` attr) are intentionally
excluded for Phase 1. A key-less `MiniwobOracleProvider` solves the simple click tasks so
the benchmark runs end to end without an API key.
"""

from __future__ import annotations

import functools
import http.server
import re
import threading
from pathlib import Path

from ultracua.browser import BrowserSession
from ultracua.types import Action, Observation


def miniwob_html_root() -> Path:
    import miniwob

    return Path(miniwob.__file__).parent / "html"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # silence request logging
        pass


class StaticServer:
    def __init__(self, root: Path) -> None:
        self.root = str(root)
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self.port = 0

    def start(self) -> str:
        handler = functools.partial(_QuietHandler, directory=self.root)
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()


def task_url(base: str, task: str) -> str:
    return f"{base}/miniwob/{task}.html"


_BEGIN_JS = """
(seed) => {
  Math.seedrandom(String(seed));
  core.EPISODE_MAX_TIME = 3600000;       // avoid the 10s episode timeout during slow LLM runs
  if (core.cover_div == null) core.startEpisode();
  core.startEpisodeReal();               // seed -> genProblem -> deterministic instance
}
"""


async def begin(page, seed: int) -> None:
    # window.onload calls core.startEpisode(); wait for it so a late onload can't re-show
    # the START overlay on top of the task after we begin.
    await page.wait_for_load_state("load")
    await page.evaluate(_BEGIN_JS, seed)


async def instruction(page) -> str:
    txt = await page.evaluate(
        "() => { const q = document.getElementById('query'); return q ? q.textContent : ''; }"
    )
    return " ".join((txt or "").split())


async def reward(page) -> dict:
    return await page.evaluate(
        "() => ({done: !!window.WOB_DONE_GLOBAL, raw: window.WOB_RAW_REWARD_GLOBAL, "
        "reward: window.WOB_REWARD_GLOBAL})"
    )


def make_prepare(seed: int):
    async def _prepare(session: BrowserSession) -> None:
        await begin(session.page, seed)

    return _prepare


def make_finalize():
    async def _finalize(session: BrowserSession) -> dict:
        return await reward(session.page)

    return _finalize


async def read_instruction(url: str, prepare) -> str:
    """Load the task once (throwaway session) to read its deterministic instruction,
    so the cache key can be computed before the learn/replay runs."""
    session = await BrowserSession(headless=True).start()
    try:
        await session.goto(url)
        await prepare(session)
        return await instruction(session.page)
    finally:
        await session.close()


# Oracle-solvable + snapshot-visible (semantic targets identifiable from the instruction).
EASY_TASKS = ["click-test", "click-button"]

# Broader set for LLM-driven runs (snapshot-visible button/input/checkbox targets).
TASKS = [
    "click-test",
    "click-button",
    "click-button-sequence",
    "enter-text",
    "enter-text-dynamic",
    "focus-text",
    "focus-text-2",
    "click-checkboxes",
    "click-option",
]

_CLICK_ROLES = {"button", "link", "checkbox", "radio", "tab", "menuitem", "option"}


class MiniwobOracleProvider:
    """Key-less solver for simple click tasks: parse the quoted target from the
    instruction and click the matching element (or the only button), then stop. Lets the
    MiniWoB benchmark run end to end without an API key — analogous to the scripted teacher."""

    def __init__(self) -> None:
        self.acted = False

    async def decide(self, goal: str, obs: Observation, history: list[str]):
        if self.acted:
            return Action(action="done", intent="task action issued"), None
        self.acted = True
        m = re.search(r'"([^"]+)"', goal)
        target = m.group(1).lower() if m else None
        chosen = None
        for el in obs.elements:
            if el.role not in _CLICK_ROLES:
                continue
            if target is None or target in el.name.lower():
                chosen = el
                break
        if chosen is None:
            return Action(action="give_up", intent="no clickable target found"), None
        return (
            Action(
                action="click",
                intent=f"click target {target or chosen.name.lower()!r}",
                ref=chosen.ref,
                reasoning="miniwob oracle",
            ),
            None,
        )
