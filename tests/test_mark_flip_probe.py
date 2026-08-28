"""The mark-flip probe: it must flip the right marks, and refuse to run a vacuous A/B.

WHAT THE PROBE IS FOR. `docs/reads-over-post.md` surveys eleven approaches to R4.27, and every one
of them assumes that fixing the marking improves Odoo availability. This probe is what measured that
assumption — and the answer was NO: the gate refusal becomes a locator refusal and the replay still
fails. A conclusion of that weight has to stay reproducible, so the probe ships with it (the rule
`gate_probe.py` shipped under for R4.111).

WHAT THESE CELLS GUARD. The probe's whole value is that its two arms differ ONLY in the mark. Three
ways that can silently stop being true, all of which the harness hit before it was right: a recipe
with nothing to flip (both arms identical), a demotion that flips more than the wire-sourced marks,
and a cache key that misses so neither arm replays anything. The first and third are refusals in the
probe; the second is what `demote` is.

Browser-free and container-free: `demote` is a dict transform, and the refusals are checked without
reaching a substrate.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from benchmarks import mark_flip_probe as P


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    """This host runs a live seeded substrate and CI starts with nothing — see tests/test_substrates.py."""
    def refuse(*a, **kw):
        raise AssertionError("a cell in test_mark_flip_probe.py reached subprocess.run for real")
    monkeypatch.setattr(subprocess, "run", refuse)


def _step(**kw) -> dict:
    return {"action": "click", "mutating": False, "mutating_sources": None, **kw}


# ------------------------------------------------------------------- what demote() may touch


def test_a_wire_only_mark_is_flipped() -> None:
    recipe = {"steps": [_step(mutating=True, mutating_sources=["wire"])]}
    out, flipped = P.demote(recipe)
    assert flipped == [0]
    assert out["steps"][0]["mutating"] is False
    assert out["steps"][0]["mutating_sources"] is None


def test_the_input_recipe_is_not_mutated() -> None:
    """The caller replays the ORIGINAL as its control arm; an in-place flip would demote both."""
    recipe = {"steps": [_step(mutating=True, mutating_sources=["wire"])]}
    P.demote(recipe)
    assert recipe["steps"][0]["mutating"] is True, "the control arm's recipe was mutated in place"


@pytest.mark.parametrize("sources", [
    ["keyword"],                 # a substring guess — a body classifier would not have touched it
    ["wire", "keyword"],         # co-marked: the keyword mark survives a body demotion
    ["form_method"],             # form evidence, nothing to do with the wire
    ["overgate"],                # AB-1's blanket precaution
    ["declared"],                # a human said so
])
def test_only_wire_sourced_marks_are_flipped(sources) -> None:
    """THE PROPERTY. A demotion that reached further would overstate the counterfactual — the probe
    would be measuring 'what if nothing were marked' rather than 'what if the body classifier had
    run', and the two differ exactly on the co-marked steps (`odoo-open-record` trips
    MUTATING_KEYWORDS on 'order' by design)."""
    recipe = {"steps": [_step(mutating=True, mutating_sources=sources)]}
    out, flipped = P.demote(recipe)
    assert flipped == []
    assert out["steps"][0]["mutating"] is True


def test_unmarked_steps_are_left_alone() -> None:
    recipe = {"steps": [_step(), _step(mutating=True, mutating_sources=["wire"])]}
    out, flipped = P.demote(recipe)
    assert flipped == [1] and out["steps"][0]["mutating"] is False


# ------------------------------------------------------------------------ the vacuity refusals


def test_a_recipe_with_no_marks_is_refused(tmp_path) -> None:
    """A no-op flip makes both arms identical, and two identical arms agree for the wrong reason —
    which is exactly how the first three runs of this harness reported a result that was really a
    TypeError, a StaleApprovalError and a cache miss."""
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"steps": [_step()]}), encoding="utf-8")
    import asyncio
    with pytest.raises(SystemExit, match="NO mutating steps"):
        asyncio.run(P.run("odoo-sort-list", p, tmp_path))


def test_an_unknown_scenario_is_refused(tmp_path) -> None:
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"steps": [_step(mutating=True, mutating_sources=["wire"])]}),
                 encoding="utf-8")
    import asyncio
    with pytest.raises(SystemExit, match="no scenario named"):
        asyncio.run(P.run("not-a-scenario", p, tmp_path))


def test_a_cache_miss_is_refused_not_replayed(tmp_path, monkeypatch) -> None:
    """The subtlest of the three: a key that does not hit replays NOTHING and fails cleanly, so both
    arms 'fail the same way' and the probe reports no difference — a false negative on the very
    question it exists to answer. The bench flows this probe consumes were learned through an
    ephemeral proxy port, so a stale key is the DEFAULT case, not an edge one."""
    class _Spec:
        key = "deadbeef"
        start_url = "http://localhost:8069/x"
    monkeypatch.setattr(P, "approve", lambda *a, **kw: None)
    monkeypatch.setattr(P, "FlowCache", lambda root: type("C", (), {"get": lambda s, k: None})())
    with pytest.raises(AssertionError, match="cache MISS"):
        P.install({"steps": []}, _Spec(), tmp_path / "c")


def test_the_probe_reads_the_gate_marker_the_engine_sets() -> None:
    """`meta["gate"]` is the structured fact the whole measurement turns on: the control arm has it
    and the demoted arm does not. It is set in exactly ONE place in `flow.py`, inside
    `if step.mutating:` — pinned here so a rename fails against the probe that depends on it."""
    import inspect

    from ultracua import flow as flow_mod
    src = inspect.getsource(flow_mod)
    assert src.count('tr.meta["gate"] = "drift"') == 1, (
        "the gate marker moved or multiplied; `mark_flip_probe` reads it as the one structured "
        "signal that the mutation gate spoke, and its ABSENCE in the demoted arm is the result"
    )
