"""`ultracua` command-line entry point — runs the Phase 0 walking skeleton against a URL."""

from __future__ import annotations

import argparse
import asyncio
from typing import Optional

from .agent import run_goal
from .config import settings
from .providers import get_provider
from .timing import StepTrace
from .types import Action, Observation, StepResult


def _on_step(
    tr: StepTrace,
    _obs: Observation,
    action: Action,
    result: Optional[StepResult],
) -> None:
    print(tr.render())
    print(f"         action={action.model_dump(exclude_none=True)}")
    if result is not None:
        flag = "ok" if result.ok else "FAIL"
        print(
            f"         -> {flag} changed={result.state_changed}"
            + (f" {result.note}" if result.note else "")
        )


async def _amain(args: argparse.Namespace) -> None:
    provider = get_provider(args.provider)
    print(
        f"ultracua: provider={args.provider} model={settings.model} "
        f"headless={settings.headless}\n"
    )
    traces = await run_goal(
        args.url,
        args.goal,
        provider,
        max_steps=args.max_steps,
        on_step=_on_step,
    )
    steps = [t for t in traces if t.index >= 0]
    if steps:
        avg = sum(t.total_ms for t in steps) / len(steps)
        print(f"\n{len(steps)} step(s), avg {avg:.0f} ms/step (LLM in the loop).")


def main() -> None:
    p = argparse.ArgumentParser(
        prog="ultracua",
        description="ultracua Phase 0 — warm-browser CUA walking skeleton.",
    )
    p.add_argument("--url", required=True, help="Starting URL.")
    p.add_argument("--goal", required=True, help="Natural-language goal.")
    p.add_argument(
        "--provider",
        default=settings.provider,
        choices=["anthropic", "mock"],
        help="LLM provider driving the loop (default from ULTRACUA_PROVIDER).",
    )
    p.add_argument("--max-steps", type=int, default=settings.max_steps)
    args = p.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
