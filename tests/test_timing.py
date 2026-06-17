from __future__ import annotations

from ultracua.timing import StepTrace


def test_step_trace_totals_and_render() -> None:
    tr = StepTrace(index=0)
    tr.add("snapshot", 10.0)
    tr.add("llm", 5.0)
    assert tr.total_ms == 15.0
    rendered = tr.render()
    assert "step 0" in rendered
    assert "snapshot=10ms" in rendered


def test_measure_context_records_a_span() -> None:
    tr = StepTrace(index=1)
    with tr.measure("work"):
        pass
    assert [s.name for s in tr.spans] == ["work"]
    assert tr.spans[0].ms >= 0.0
