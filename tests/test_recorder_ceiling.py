"""Recorder ceiling validation (key-less CI gate): the Phase-I recorder cracks the garbled-label grounding
tasks the LLM-authoring discovery loop can't (`click-checkboxes` / `-large` / `click-option`). For each
seeded MiniWoB++ instance, a "human" demo-oracle reads the instruction's named targets, the recorder
captures it, and the recorded flow REPLAYS 0-LLM to a positive `WOB_RAW_REWARD`. This is the measurement
that turns the spike's *asserted* lever into a *measured* one (baselines/recorder_ceiling.json).
"""

from __future__ import annotations

from benchmarks.recorder_ceiling import measure


async def test_recorder_cracks_the_garbled_label_ceiling_0llm() -> None:
    rec = await measure()
    assert rec["instances"] >= 9                          # 3 garbled-label tasks x 3 seeds
    assert rec["recorder_solved"] == rec["instances"]     # every ceiling instance solved by the demo+replay
    assert rec["recorder_rate"] == 1.0
    assert rec["all_replays_0llm"] is True                # ...and each replay used ZERO model calls
    # ...re-grounding by role+name+css (the id/test-id are stripped from the recorded specs), so the
    # measurement exercises the SAME grounding surface the LLM mis-grounds, not a convenient id hook:
    assert rec["all_id_free"] is True
    assert rec["multi_target_instances"] >= 3             # not all trivial single-/zero-target instances
