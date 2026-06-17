"""Runtime configuration, sourced from environment variables (with a .env fallback).

Phase 0 keeps this intentionally tiny — a frozen dataclass read once at import. The
multi-provider / tiering config (PLAN.md Phase 3) will grow this into a proper layered
settings object.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


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


@dataclass(frozen=True)
class Settings:
    # Which provider drives the agent: anthropic | openai | gemini | mock.
    provider: str = os.getenv("ULTRACUA_PROVIDER", "anthropic")
    # Native LLM backend used to build the router (when provider is an LLM backend).
    llm_backend: str = os.getenv("ULTRACUA_LLM_BACKEND", "anthropic")
    # STRONG-tier model (discovery / escalation). Defaults to Anthropic's most capable.
    model: str = os.getenv("ULTRACUA_MODEL", "claude-opus-4-8")
    # FAST-tier model (routine element selection); escalates to STRONG on low confidence.
    fast_model: str = os.getenv("ULTRACUA_FAST_MODEL", "claude-haiku-4-5")
    # Default tier the agent uses. Discovery (learning a novel flow) needs reasoning, so
    # default to STRONG; cached replay uses no LLM, so a fast routine tier rarely applies.
    # Set ULTRACUA_TIER=fast to drive routine steps cheaply (escalates to strong on give_up).
    tier: str = os.getenv("ULTRACUA_TIER", "strong")
    headless: bool = _flag("ULTRACUA_HEADLESS", True)
    max_steps: int = int(os.getenv("ULTRACUA_MAX_STEPS", "8"))
    # Stop a discovery run after this many consecutive no-progress steps (anti-loop): when
    # the agent keeps acting without changing the page, it's stuck (or solved-but-not-aware),
    # so bail instead of burning the full step budget.
    stuck_limit: int = int(os.getenv("ULTRACUA_STUCK_LIMIT", "4"))
    # Cap on interactable elements sent to the model — keeps the observation compact.
    max_elements: int = int(os.getenv("ULTRACUA_MAX_ELEMENTS", "80"))
    nav_timeout_ms: int = int(os.getenv("ULTRACUA_NAV_TIMEOUT_MS", "15000"))
    action_timeout_ms: int = int(os.getenv("ULTRACUA_ACTION_TIMEOUT_MS", "5000"))
    # Max flows run concurrently by run_many (as separate contexts in one browser).
    concurrency: int = int(os.getenv("ULTRACUA_CONCURRENCY", "4"))
    # Root for large/working data kept off the system drive (benchmark downloads, the
    # isolated evaluator's uv cache, scratch eval dirs). Configurable via ULTRACUA_DATA_DIR.
    data_dir: str = _default_data_dir()


settings = Settings()
