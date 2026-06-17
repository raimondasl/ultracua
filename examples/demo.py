"""Minimal programmatic use of the Phase 0 skeleton.

    uv run python examples/demo.py

Uses the key-less mock provider so it runs with no ANTHROPIC_API_KEY. Swap in the
Anthropic provider once a key is set:

    from ultracua.providers import get_provider
    provider = get_provider("anthropic")
"""

from __future__ import annotations

import asyncio

from ultracua import run_goal
from ultracua.providers import get_provider
from ultracua.timing import StepTrace


def _print(tr: StepTrace, *_rest) -> None:
    print(tr.render())


async def main() -> None:
    provider = get_provider("mock")
    await run_goal(
        url="https://example.com",
        goal="click the more information link",
        provider=provider,
        on_step=_print,
    )


if __name__ == "__main__":
    asyncio.run(main())
