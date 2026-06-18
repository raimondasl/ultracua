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
from .parallel import run_many
from .safety import PacingGovernor, is_mutating
from .types import Action, Element, Observation, StepResult
from .verifiers import keyword_completion
from .extract import Extraction, extract
from .flows import FlowHealth, FlowReplayError, FlowSpec, LoginSpec, MutateSpec, refresh_auth
from .flows import approve as approve_flow
from .flows import health as flow_health
from .flows import learn as learn_flow
from .flows import replay as replay_flow
from .flows import unapprove as unapprove_flow
from .vision import AnthropicGrounding, MockGrounding

__version__ = "0.15.0"

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
    "run_many",
    "keyword_completion",
    "AnthropicGrounding",
    "MockGrounding",
    "FlowSpec",
    "LoginSpec",
    "MutateSpec",
    "FlowHealth",
    "FlowReplayError",
    "learn_flow",
    "replay_flow",
    "approve_flow",
    "unapprove_flow",
    "refresh_auth",
    "flow_health",
    "extract",
    "Extraction",
    "settings",
    "main",
]


def main() -> None:
    from .cli import main as _main

    _main()
