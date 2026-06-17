"""ultracua — a Computer Use Agent that drives a browser at 5-10x human speed.

Phase 0 (walking skeleton) public surface. See PLAN.md for the full roadmap.
"""

from __future__ import annotations

from .browser import BrowserSession
from .config import settings
from .types import Action, Element, Observation, StepResult

__version__ = "0.1.0"

__all__ = [
    "BrowserSession",
    "Action",
    "Element",
    "Observation",
    "StepResult",
    "settings",
    "run_goal",
    "main",
]


def run_goal(*args, **kwargs):
    """Lazy re-export of the agent loop (keeps Playwright import off `import ultracua`)."""
    from .agent import run_goal as _run_goal

    return _run_goal(*args, **kwargs)


def main() -> None:
    from .cli import main as _main

    _main()
