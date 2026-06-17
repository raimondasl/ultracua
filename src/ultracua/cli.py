"""`ultracua` command-line entry point.

Phase 1: runs a goal through the flow cache. First run on a (goal, url) LEARNS and caches
the flow; subsequent runs REPLAY it with no LLM. Use --mode to force learn/replay and
--fresh to clear the cached flow first.
"""

from __future__ import annotations

import argparse
import asyncio

from .cache import FlowCache, flow_key
from .config import settings
from .flow import run_cached
from .providers import get_provider
from .timing import StepTrace


def _on_step(tr: StepTrace) -> None:
    print(tr.render())
    bits = []
    if "intent" in tr.meta:
        bits.append(f"intent={tr.meta['intent']!r}")
    if "action" in tr.meta:
        bits.append(f"action={tr.meta['action']}")
    if "ok" in tr.meta:
        bits.append(f"ok={tr.meta['ok']}")
    if tr.meta.get("note"):
        bits.append(f"note={tr.meta['note']}")
    if bits:
        print("         " + "  ".join(bits))


async def _amain(args: argparse.Namespace) -> None:
    cache = FlowCache()
    if args.fresh:
        if cache.delete(flow_key(args.goal, args.url, args.scope)):
            print("(cleared cached flow)")
    provider = get_provider(args.provider)
    print(
        f"ultracua: provider={args.provider} model={settings.model} "
        f"mode={args.mode} headless={settings.headless}\n"
    )
    report = await run_cached(
        args.url,
        args.goal,
        provider,
        cache=cache,
        mode=args.mode,
        scope=args.scope,
        on_step=_on_step,
    )
    print(
        f"\nmode={report.mode} success={report.success} "
        f"llm_calls={report.llm_calls} healed={report.healed_steps}"
    )
    steps = report.step_traces
    if steps:
        print(
            f"{len(steps)} step(s), avg {report.avg_step_ms:.0f} ms/step, "
            f"total {report.total_ms:.0f} ms"
        )


def main() -> None:
    p = argparse.ArgumentParser(
        prog="ultracua",
        description="ultracua — a browser CUA with a learn-once / replay-fast flow cache.",
    )
    p.add_argument("--url", required=True, help="Starting URL.")
    p.add_argument("--goal", required=True, help="Natural-language goal.")
    p.add_argument(
        "--provider",
        default=settings.provider,
        choices=["anthropic", "mock"],
        help="LLM provider for learn/heal (default from ULTRACUA_PROVIDER).",
    )
    p.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "learn", "replay"],
        help="auto: replay if cached else learn; learn: force learn; replay: cache-only.",
    )
    p.add_argument("--scope", default="default", help="Cache scope namespace.")
    p.add_argument(
        "--fresh", action="store_true", help="Delete the cached flow before running."
    )
    args = p.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
