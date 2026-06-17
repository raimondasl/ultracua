"""ultracua — a Computer Use Agent that drives a browser at 5-10x human speed.

See PLAN.md for the full roadmap. Phase 1 adds the learn-once / replay-fast flow cache.
"""

from __future__ import annotations

from .agent import run_goal
from .browser import BrowserSession
from .cache import CachedFlow, CachedStep, FlowCache, flow_key
from .config import settings
from .flow import FlowReport, run_cached
from .locators import LocatorSpec
from .safety import PacingGovernor, is_mutating
from .types import Action, Element, Observation, StepResult

__version__ = "0.4.0"

__all__ = [
    "BrowserSession",
    "Action",
    "Element",
    "Observation",
    "StepResult",
    "LocatorSpec",
    "CachedFlow",
    "CachedStep",
    "FlowCache",
    "flow_key",
    "FlowReport",
    "PacingGovernor",
    "is_mutating",
    "run_goal",
    "run_cached",
    "settings",
    "main",
]


def main() -> None:
    from .cli import main as _main

    _main()
