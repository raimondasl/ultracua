"""Vision tier: a canvas-only page (empty DOM snapshot) is solved by grounding a click at
pixel coords, then replayed deterministically (click_xy) with no LLM."""

from __future__ import annotations

from pathlib import Path

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.vision import MockGrounding

_FIX = Path(__file__).parents[1] / "benchmarks" / "fixtures" / "vision_canvas.html"
URL = _FIX.resolve().as_uri()
GOAL = "click the green box"


async def test_vision_learn_then_replay(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path)
    grounding = MockGrounding([
        {"action": "click_xy", "intent": "click the green box", "coords": [100, 100]},
        {"action": "done", "intent": "target hit"},
    ])

    learn = await run_cached(URL, GOAL, None, cache, mode="learn", headless=True, grounding=grounding)
    assert learn.success
    flow = cache.get(flow_key(GOAL, URL))
    assert flow is not None
    assert any(s.action == "click_xy" and s.coords == [100, 100] for s in flow.steps)

    # Replay needs neither a provider nor grounding — the coords are cached.
    replay = await run_cached(URL, GOAL, None, cache, mode="replay", headless=True)
    assert replay.success
    assert replay.llm_calls == 0
    assert "target hit" in replay.final_text.lower()
