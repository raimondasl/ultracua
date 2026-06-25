"""Phase-I recorder SPIKE: a human DEMONSTRATION of a grounding-hard selection (garbled-label checkboxes
— the MiniWoB ceiling failure mode) is captured into an ordinary CachedFlow and replays 0-LLM, reproducing
the exact selection. The "human" is a scripted sequence of real interactions so the spike stays key-less +
deterministic; the point is that the recorder reads the nodes the demonstrator touched, with no LLM grounding.
"""

from __future__ import annotations

from pathlib import Path

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.recorder import record_demo, recorded_steps_summary

_FIX = Path(__file__).parents[1] / "benchmarks" / "fixtures" / "recorder_checkboxes.html"
URL = _FIX.resolve().as_uri()
GOAL = "select qux and foo"


async def _demo(page) -> None:
    # what a human would do: tick the two RIGHT boxes (despite the garbled labels), type a note, show it.
    # Click by accessible name — the checkboxes have no id/test-id, so the recorder must capture them by
    # role+name+css, like a real page.
    await page.get_by_role("checkbox", name="qux").click()
    await page.get_by_role("checkbox", name="foo").click()
    await page.fill("#note", "abc123")
    await page.locator("#note").blur()  # commit the edit -> a `change` -> captured as a `type` step
    await page.click("#show")


async def test_recorder_captures_a_demo_and_replays_it_0llm(tmp_path) -> None:
    cache = FlowCache(root=tmp_path)
    flow, wrote = await record_demo(URL, _demo, goal=GOAL, cache=cache, headless=True)
    assert wrote is False  # a read/selection demo fires no write on the wire

    # The demonstration was captured as clicks + a type, each pinned to a RESILIENT locator (role+name+css),
    # NOT a positional guess — the exact nodes the human touched.
    assert [s.action for s in flow.steps] == ["click", "click", "type", "click"]
    names = [s.locator.name for s in flow.steps if s.locator]
    assert "qux" in names and "foo" in names                      # the two garbled-label checkboxes
    boxes = [s for s in flow.steps if s.locator and s.locator.role == "checkbox"]
    assert len(boxes) == 2 and all(b.locator.elem_id is None and b.locator.css for b in boxes)
    typed = [s for s in flow.steps if s.action == "type"]
    assert len(typed) == 1 and typed[0].text == "abc123" and typed[0].locator.role == "textbox"
    assert recorded_steps_summary(flow)                            # inspectable

    # The recorded flow is an ordinary CachedFlow under the standard key -> the engine replays it as-is.
    assert cache.get(flow_key(GOAL, URL, "default")) is not None

    async def _finalize(session):
        return {"result": await session.page.inner_text("#result")}

    report = await run_cached(URL, GOAL, None, cache, mode="replay", headless=True, finalize=_finalize)
    assert report.success                                          # every recorded step resolved + replayed
    # the exact selection AND the typed note, reproduced 0-LLM
    assert report.extra["finalize"]["result"] == "selected: qux,foo | note: abc123"


async def test_recorder_replay_uses_no_llm(tmp_path) -> None:
    # the whole point: a recorded flow replays with ZERO model calls (no grounding, no heal)
    cache = FlowCache(root=tmp_path)
    await record_demo(URL, _demo, goal=GOAL, cache=cache, headless=True)  # (flow, performed_write)
    report = await run_cached(URL, GOAL, None, cache, mode="replay", headless=True)
    assert report.success and report.llm_calls == 0 and report.healed_steps == 0
