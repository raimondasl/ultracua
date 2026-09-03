"""Runtime configuration, sourced from environment variables (with a .env fallback).

Phase 0 keeps this intentionally tiny — a frozen dataclass read once at import. The
multi-provider / tiering config (PLAN.md Phase 3) will grow this into a proper layered
settings object.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _window_size() -> Optional[tuple[int, int]]:
    """Parse ULTRACUA_WINDOW_SIZE ("1600x1000" or "1600,1000") into (width, height); None if unset,
    blank, or malformed. A machine-level default for the browser window/viewport size that
    BrowserSession uses when created without an explicit window_size — handy for making a headed /
    demo run fill more of the screen. A bad value is ignored (falls back to Playwright's default)
    rather than raising, so a typo in the env can't break every run."""
    raw = os.getenv("ULTRACUA_WINDOW_SIZE")
    if not raw or not raw.strip():
        return None
    parts = raw.strip().replace("X", "x").replace("x", ",").split(",")
    if len(parts) < 2:
        return None
    try:
        w, h = int(parts[0].strip()), int(parts[1].strip())
    except ValueError:
        return None
    return (w, h) if w > 0 and h > 0 else None


def _default_data_dir() -> str:
    """Where ultracua stows large/working data: benchmark downloads, the isolated
    evaluator's package cache, scratch eval dirs. Kept OFF the system drive by default.

    Resolution order: ULTRACUA_DATA_DIR -> a roomy D:\\ data drive (Windows) -> ~/.ultracua/data.
    Always overridable via the env var so the location stays configurable per machine.
    """
    env = os.getenv("ULTRACUA_DATA_DIR")
    if env:
        return env
    if os.name == "nt" and os.path.isdir("D:\\"):
        return r"D:\ultracua-data"
    return str(Path.home() / ".ultracua" / "data")


HOME_DIR_NAME = ".ultracua"


def flow_home() -> Path:
    """Where a flow's SPECS + CACHE live — resolved once, the same way for every surface.

    Resolution order:
      1. `$ULTRACUA_HOME` (`~` expanded) — set this for a machine-global store, and in an MCP client's `env`
         block (an MCP server is launched with an arbitrary cwd). **Use an ABSOLUTE path**: a relative value
         is still resolved against the current directory, which is exactly the cwd-dependence this exists to
         escape.
      2. The nearest ancestor directory containing a `.ultracua/` — git-style, so running from a
         subdirectory of your project finds the project's flows instead of silently finding nothing.
      3. `./.ultracua` — today's behavior when nothing else applies.

    There is deliberately **NO `~/.ultracua` fallback**: a scheduled job started in the wrong directory must
    fail loudly (see the empty-store refusals in the fleet verbs), never quietly run a *different* fleet.
    Not memoized on purpose — a value cached across a `chdir` is exactly the bug class this fixes.
    """
    env = os.getenv("ULTRACUA_HOME")
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd()
    for d in (cwd, *cwd.parents):
        if (d / HOME_DIR_NAME).is_dir():
            return d / HOME_DIR_NAME
    return cwd / HOME_DIR_NAME


@dataclass(frozen=True)
class Settings:
    # Which provider drives the agent: anthropic | openai | gemini | mock.
    provider: str = os.getenv("ULTRACUA_PROVIDER", "anthropic")
    # Native LLM backend used to build the router (when provider is an LLM backend).
    llm_backend: str = os.getenv("ULTRACUA_LLM_BACKEND", "anthropic")
    # STRONG-tier model (discovery / escalation), and the model `vision.py` inherits for grounding.
    # `tier` below defaults to STRONG, so this is the model a learn actually spends at.
    # It THINKS BY DEFAULT — omitting `thinking` runs adaptive here, where Opus 4.8 ran none. That is
    # a capability change and, measured, NOT a cost one: 4x the output tokens on a trivial prompt
    # (12 vs 3, a three-token baseline) but **+2% on a real agent turn**, inside the noise. No
    # `output_config.effort` is sent as a result. See docs/realistic-benchmark-plan.md §6a.
    # Whatever is set here must have a `_PRICES` entry; a test derives that rather than trusting it.
    model: str = os.getenv("ULTRACUA_MODEL", "claude-opus-5")
    # FAST-tier model (routine element selection); escalates to STRONG on low confidence.
    fast_model: str = os.getenv("ULTRACUA_FAST_MODEL", "claude-haiku-4-5")
    # Default tier the agent uses. Discovery (learning a novel flow) needs reasoning, so
    # default to STRONG; cached replay uses no LLM, so a fast routine tier rarely applies.
    # Set ULTRACUA_TIER=fast to drive routine steps cheaply (escalates to strong on give_up).
    tier: str = os.getenv("ULTRACUA_TIER", "strong")
    # Sampling temperature for the agent's decisions. >0 is what makes best-of-N actually RESAMPLE
    # diverse attempts (the provider default isn't guaranteed non-zero across backends/proxies).
    authoring_temperature: float = float(os.getenv("ULTRACUA_TEMPERATURE", "1.0"))
    headless: bool = _flag("ULTRACUA_HEADLESS", True)
    # Optional browser window/viewport size as (width, height); None -> Playwright's default. Read when
    # a BrowserSession is created without an explicit window_size. Headed: sizes the OS window and lets
    # the page fill it; headless: renders the page at this size. Env: ULTRACUA_WINDOW_SIZE="1600x1000".
    window_size: Optional[tuple[int, int]] = _window_size()
    max_steps: int = int(os.getenv("ULTRACUA_MAX_STEPS", "8"))
    # Stop a discovery run after this many consecutive no-progress steps (anti-loop): when
    # the agent keeps acting without changing the page, it's stuck (or solved-but-not-aware),
    # so bail instead of burning the full step budget.
    stuck_limit: int = int(os.getenv("ULTRACUA_STUCK_LIMIT", "4"))
    # Cap on interactable elements sent to the model — keeps the observation compact.
    max_elements: int = int(os.getenv("ULTRACUA_MAX_ELEMENTS", "80"))
    nav_timeout_ms: int = int(os.getenv("ULTRACUA_NAV_TIMEOUT_MS", "15000"))
    action_timeout_ms: int = int(os.getenv("ULTRACUA_ACTION_TIMEOUT_MS", "5000"))
    # LEARN-SIDE SETTLE. How long the DOM must be free of mutations before an observation is believed.
    # 200 ms is MEASURED, not chosen: `readiness_probe --settle` scored candidates against a ground
    # truth over 60 page-reps (R4.120) and `mut-quiet-200` was the cheapest that was NEVER premature.
    # The alternatives are worse in the direction that matters -- acting at `domcontentloaded` (what
    # the learn did before this) was premature 28 times, `document.readyState` 28, and "two equal
    # element counts 100 ms apart" 17, that last one by locking onto a PLATEAU mid-render.
    settle_quiet_ms: int = int(os.getenv("ULTRACUA_SETTLE_QUIET_MS", "200"))
    # ...AND THE CAP, because a page that never stops mutating (an animation, a live ticker) would
    # otherwise wait forever. No such page is in the measured corpus, so the residual is real and
    # stated; 2000 ms clears the largest observed firing (1485 ms) with margin. On the cap the settle
    # gives up and the caller proceeds, which is EXACTLY today's behaviour, just later -- so the
    # failure direction of this whole mechanism is "no better than before", never "worse".
    settle_cap_ms: int = int(os.getenv("ULTRACUA_SETTLE_CAP_MS", "2000"))
    # The beat between looks when the replay is waiting for a page to paint (R4.144). Reached ONLY
    # after a settle that genuinely waited, on a target the resolver has already reported ABSENT --
    # so it is never on the happy path, and measured 0 firings across three Gitea scenarios. 50 ms
    # because the next `await_settled()` usually returns `already-quiet` instantly (the page is
    # between network-gated stages), so without a beat the loop spins the resolver ladder for the
    # whole budget; the measured shape binds at look 5 of ~5, well inside `settle_cap_ms`.
    settle_poll_ms: int = int(os.getenv("ULTRACUA_SETTLE_POLL_MS", "50"))
    # How many CONSECUTIVE `already-quiet` looks mean the page has stopped rather than paused
    # (R4.144). A render waiting on the network has quiet GAPS punctuated by bursts, and every burst
    # makes the next settle wait rather than answer instantly; an element that is simply GONE is
    # quiet forever. Measured: the Odoo shape needed THREE consecutive already-quiets before its
    # next stage landed, so 6 is twice the observed need -- and `drift_bench` priced the alternative
    # exactly, 36 genuinely-drifted rows paying 2251 ms each because polling to the cap cannot tell
    # those two pages apart. The failure direction is today's behaviour: a loud miss, never a bind.
    settle_stall_looks: int = int(os.getenv("ULTRACUA_SETTLE_STALL_LOOKS", "6"))
    # Write-detection act window: how long AFTER a step's verify snapshot a non-idempotent network
    # request is still attributed to THAT action (a write's POST can race a post-act navigation and
    # land just after verify returns). Generous on purpose — a missed write means a double-submit on
    # re-author, far worse than a wasted best-of-N re-sample. See `flow._author_steps`.
    write_window_ms: int = int(os.getenv("ULTRACUA_WRITE_WINDOW_MS", "2000"))
    # Replay write-settle bound: how long the mutation gate holds the Idempotency-Key on the context AFTER a
    # mutating actuation, awaiting the in-flight write (page.expect_request) before the `finally` clears it.
    # A click/select/press-triggered write fires near-immediately (synchronous / microtask / short timer), so
    # this is kept SHORT: a mutating step that fires NO write (a preventDefault'd submit, a client-only button)
    # then waits only this long, NOT the full action_timeout_ms (a multi-second stall on every no-write
    # mutating step). The replay code waits min(action_timeout_ms, write_settle_ms), so this never exceeds the
    # action timeout. Raise it if a flow's write is dispatched on a longer timer/debounce. CLAMPED to >=1ms:
    # Playwright treats an expect_request timeout of exactly 0 as "wait forever", so 0/negative (a tuner trying
    # to "disable" the wait) would HANG a no-write mutating step — the floor degrades that to ~immediate instead.
    write_settle_ms: int = max(1, int(os.getenv("ULTRACUA_WRITE_SETTLE_MS", "1000")))
    # Max flows run concurrently by run_many (as separate contexts in one browser).
    concurrency: int = int(os.getenv("ULTRACUA_CONCURRENCY", "4"))
    # Root for large/working data kept off the system drive (benchmark downloads, the
    # isolated evaluator's uv cache, scratch eval dirs). Configurable via ULTRACUA_DATA_DIR.
    data_dir: str = _default_data_dir()
    # Observability: log level for the `ultracua` logger (WARNING keeps library imports quiet;
    # the CLI bumps this to INFO so a scheduled job's run is traceable).
    log_level: str = os.getenv("ULTRACUA_LOG_LEVEL", "WARNING")
    # LLM-call resilience: retry a transient failure (rate limit / timeout / 5xx) with capped
    # exponential backoff, and bound each call so a hung request can't stall a run forever.
    llm_max_retries: int = int(os.getenv("ULTRACUA_LLM_MAX_RETRIES", "3"))
    llm_timeout_s: float = float(os.getenv("ULTRACUA_LLM_TIMEOUT_S", "60"))


settings = Settings()
