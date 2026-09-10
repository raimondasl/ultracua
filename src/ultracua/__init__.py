"""ultracua — a Computer Use Agent that learns a browser flow once with an LLM, then replays it
deterministically with no model in the loop.

This said *drives a browser at 5-10x human speed* until 0.182.0. Nothing in this tree measures that:
what is committed is what replay REMOVES (0 model calls on all 24 write replays in the benchmark
series, one on 43 of 51 read replays) plus `baselines/demo.json`'s FIXTURE speedup of 62-114x, which
is removed model latency against a LOCAL page and does not transfer to a real site. See `README.md`.

ACTIVE DEVELOPMENT STOPPED ON 2026-09-09. `docs/open-defects.md` carries 74 open findings and the
close-out banner explaining what that means; read it before adopting this. `PLAN.md` and `ROADMAP.md`
are kept as the record of what was intended, not as a statement of what is coming.
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
from .flows import (
    FleetRun, FlowHealth, FlowQuarantineError, FlowReplayError, FlowSpec, LoginSpec, MutateSpec, SlotSpec,
    WriteReadbackError, WriteUnverifiedError, refresh_auth,
)
from .flows import AuditFinding, AuditRun
from .flows import approve as approve_flow
from .flows import audit_flows
from .flows import health as flow_health
from .flows import learn as learn_flow
from .flows import release as release_flow
from .flows import replay as replay_flow
from .flows import run_all as run_all_flows
from .flows import unapprove as unapprove_flow
from .vision import AnthropicGrounding, MockGrounding

# Single-source the version from the installed package metadata (pyproject.toml is the source of
# truth). Falls back for an uninstalled source checkout.
try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    __version__ = _pkg_version("ultracua")
except (PackageNotFoundError, ImportError):  # pragma: no cover - source-tree fallback
    __version__ = "0.0.0+dev"

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
    "SlotSpec",
    "FlowHealth",
    "FleetRun",
    "FlowReplayError",
    "FlowQuarantineError",
    "WriteReadbackError",
    "WriteUnverifiedError",
    "learn_flow",
    "replay_flow",
    "approve_flow",
    "unapprove_flow",
    "release_flow",
    "audit_flows",
    "AuditRun",
    "AuditFinding",
    "run_all_flows",
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
