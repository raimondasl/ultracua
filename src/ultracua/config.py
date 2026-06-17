"""Runtime configuration, sourced from environment variables (with a .env fallback).

Phase 0 keeps this intentionally tiny — a frozen dataclass read once at import. The
multi-provider / tiering config (PLAN.md Phase 3) will grow this into a proper layered
settings object.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


@dataclass(frozen=True)
class Settings:
    # Which LLM provider drives discovery. "anthropic" or "mock" in Phase 0.
    provider: str = os.getenv("ULTRACUA_PROVIDER", "anthropic")
    # Discovery (strong-tier) model. Defaults to Anthropic's most capable model.
    model: str = os.getenv("ULTRACUA_MODEL", "claude-opus-4-8")
    headless: bool = _flag("ULTRACUA_HEADLESS", True)
    max_steps: int = int(os.getenv("ULTRACUA_MAX_STEPS", "8"))
    # Cap on interactable elements sent to the model — keeps the observation compact.
    max_elements: int = int(os.getenv("ULTRACUA_MAX_ELEMENTS", "80"))
    nav_timeout_ms: int = int(os.getenv("ULTRACUA_NAV_TIMEOUT_MS", "15000"))
    action_timeout_ms: int = int(os.getenv("ULTRACUA_ACTION_TIMEOUT_MS", "5000"))


settings = Settings()
