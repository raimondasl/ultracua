"""A real, read-only recurring data-pull demo (no login, no writes).

Every run after the first replays a *learned* navigation at 0 LLM and returns fresh structured
data. Point it at a real, scrape-tolerant site — here, Hacker News: open the current top story's
discussion and pull its title / points / comment count.

    uv run python examples/hn_digest.py                 # headless
    uv run python examples/hn_digest.py --headed        # watch the browser (for a demo reel)
    uv run python examples/hn_digest.py --fresh         # forget the learned flow and re-learn

Needs ANTHROPIC_API_KEY (loaded from .env) for the one-time learn + the per-run extraction call.
First run LEARNS (the agent reasons out the navigation, ~15-20s); every run after REPLAYS the
cached path with 0 navigation LLM calls (~3-4s, dominated by the single extraction call + network).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from ultracua import FlowSpec, approve_flow, learn_flow, replay_flow
from ultracua.cache import FlowCache, flow_key


def _spec(headed: bool) -> FlowSpec:
    return FlowSpec(
        name="hn-top-discussion",
        start_url="https://news.ycombinator.com",
        goal="open the comments/discussion page of the current top story on the front page",
        extract="the story title, its score in points, and the number of comments",
        extract_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "points": {"type": "integer"},
                "comments": {"type": "integer"},
            },
            "required": ["title"],
        },
        headless=(False if headed else None),
    )


async def main(headed: bool, fresh: bool) -> None:
    spec = _spec(headed)
    if fresh:
        FlowCache().delete(flow_key(spec.goal, spec.start_url, spec.scope))

    cached = FlowCache().get(flow_key(spec.goal, spec.start_url, spec.scope)) is not None
    if not cached:
        print("LEARN  (one-time: the agent reasons out the navigation)...")
        t0 = time.perf_counter()
        res = await learn_flow(spec)
        print(f"  learned in {time.perf_counter() - t0:.1f}s — {len(res.steps)} step(s); "
              f"data: {json.dumps(res.data, ensure_ascii=False)}")
        approve_flow(spec)  # a human verifies once, then trusts the replay
        print("  approved.\n")

    print("REPLAY (0-LLM navigation; one cheap extraction reads the answer)...")
    t1 = time.perf_counter()
    data = await replay_flow(spec)
    print(f"  replayed in {time.perf_counter() - t1:.1f}s")
    print("  data: " + json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="examples/hn_digest.py")
    ap.add_argument("--headed", action="store_true", help="show the browser window.")
    ap.add_argument("--fresh", action="store_true", help="forget the learned flow and re-learn.")
    args = ap.parse_args()
    asyncio.run(main(args.headed, args.fresh))
