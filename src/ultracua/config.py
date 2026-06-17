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
    # Which provider drives the agent: anthropic | openai | gemini | mock.
    provider: str = os.getenv("ULTRACUA_PROVIDER", "anthropic")
    # Native LLM backend used to build the router (when provider is an LLM backend).
    llm_backend: str = os.getenv("ULTRACUA_LLM_BACKEND", "anthropic")
    # STRONG-tier model (discovery / escalation). Defaults to Anthropic's most capable.
    model: str = os.getenv("ULTRACUA_MODEL", "claude-opus-4-8")
    # FAST-tier model (routine element selection); escalates to STRONG on low confidence.
    fast_model: str = os.getenv("ULTRACUA_FAST_MODEL", "claude-haiku-4-5")
    # Default tier the agent uses for routine steps: fast | strong.
    tier: str = os.getenv("ULTRACUA_TIER", "fast")
    headless: bool = _flag("ULTRACUA_HEADLESS", True)
    max_steps: int = int(os.getenv("ULTRACUA_MAX_STEPS", "8"))
    # Cap on interactable elements sent to the model — keeps the observation compact.
    max_elements: int = int(os.getenv("ULTRACUA_MAX_ELEMENTS", "80"))
    nav_timeout_ms: int = int(os.getenv("ULTRACUA_NAV_TIMEOUT_MS", "15000"))
    action_timeout_ms: int = int(os.getenv("ULTRACUA_ACTION_TIMEOUT_MS", "5000"))


settings = Settings()
