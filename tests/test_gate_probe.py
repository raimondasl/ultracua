"""The gate probe must describe the gate the ENGINE has, not the one it had. (D6's instrument.)

WHY A PROBE NEEDS A TEST AT ALL. `benchmarks/gate_probe.py` exists to make a "do not change `src/`"
conclusion re-derivable — D6 was refused on its evidence, and a refusal nobody can re-check is a
claim. But the probe works by RESTATING `flow.py`'s branch condition, and a restatement drifts. A
probe that describes a condition the engine no longer has is worse than no probe: it answers
confidently and wrongly, which is the shape this register keeps filing.

So the two are pinned together structurally, by reading the engine's own source.

Nothing here launches a browser, spends anything, or needs a container: it is a dict and an AST.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from benchmarks import gate_probe as GP


def _step(**kw) -> dict:
    base = {"action": "click", "mutating": True, "precond_scope": "", "locator": None}
    base.update(kw)
    return base


# ------------------------------------------------------------------ the branch, held against src/


def test_the_probe_branches_on_exactly_what_the_engine_branches_on() -> None:
    """THE PIN. `flow.py` selects the precise gate with `step.precond_scope and step.locator is not
    None`; this probe claims to mirror it. Derived from the engine's SOURCE rather than remembered,
    so the day that condition gains or loses a term, the probe fails here instead of quietly
    misreporting which gate a step gets.

    Read via `inspect.getsource` rather than by PATH deliberately: a path-reading scan parses the
    pristine tree under `scripts/prove_red.py`'s mutant install and can never contribute a kill
    (R4.75), and this is a property worth being scoreable there.
    """
    from ultracua import flow as flow_mod

    src = inspect.getsource(flow_mod)
    # The gate's own branch, found by the comment the engine puts on it rather than by line number.
    marker = "if step.precond_scope and step.locator is not None:"
    assert src.count(marker) == 1, (
        f"the engine's gate branch is not the single line this probe mirrors (found "
        f"{src.count(marker)}). `benchmarks/gate_probe.py` reports WHICH GATE each marked step "
        f"gets; if the condition moved, the probe is now describing a gate that does not exist. "
        f"Re-derive it, and re-check D6's refutation, which rests on this branch."
    )
    tested = {"precond_scope", "locator"}
    assert set(GP.PRECISE_REQUIRES) == tested, (
        f"the probe says the precise gate needs {GP.PRECISE_REQUIRES} and the engine tests {tested}"
    )


@pytest.mark.parametrize("scope,locator,expected", [
    ("abc", {"ref": "e1"}, "precise"),
    ("", {"ref": "e1"}, "whole-page"),        # promoted click/type that captured no scope
    ("abc", None, "whole-page"),              # a scope with nothing to resolve
    ("", None, "whole-page"),                 # a navigate: neither, and never
])
def test_gate_of_matches_the_branch_in_all_four_corners(scope, locator, expected) -> None:
    """All four, because the condition is a conjunction and three of the corners are ways to lose."""
    assert GP.gate_of(_step(precond_scope=scope, locator=locator)) == expected


# --------------------------------------------------------------------------- what it reports


def test_only_mutating_steps_are_reported() -> None:
    """Only they are gated: `flow.py` opens with `if step.mutating:` and the rest is unreachable.

    Reporting a read here would inflate every count in a measurement whose whole purpose was to
    establish the ACTION-TYPE MIX of gated steps — which is how D6 came to be refused.
    """
    recipe = {"steps": [_step(mutating=False, action="scroll"),
                        _step(mutating=True, action="navigate"),
                        _step(mutating=False, action="click")]}
    rows = GP.gate_inputs(recipe)
    assert [r["action"] for r in rows] == ["navigate"]
    assert rows[0]["index"] == 1, "the reported index must be the step's position in the recipe"


def test_the_d6_population_is_reported_the_way_the_measurement_read_it() -> None:
    """The shape of the actual finding, as a regression test on the probe's own arithmetic.

    Six navigations that can only get the whole-page gate, four clicks that already get the precise
    one and refused anyway. If this ever reads differently for the same input, the number D6 was
    refused on has moved.
    """
    recipe = {"steps": (
        [_step(action="navigate", precond_scope="", locator=None)] * 6
        + [_step(action="click", precond_scope="s", locator={"ref": "e1"})] * 4
    )}
    rows = GP.gate_inputs(recipe)
    by = {}
    for r in rows:
        by[(r["action"], r["gate"])] = by.get((r["action"], r["gate"]), 0) + 1
    assert by == {("navigate", "whole-page"): 6, ("click", "precise"): 4}


# ------------------------------------------------------------------------------- loud, not empty


def test_a_directory_with_no_recipes_is_loud(tmp_path, capsys) -> None:
    """A cache with nothing in it must not print the same clean table as one where nothing is gated.

    That is the difference between "measured, and no step is gated" and "measured nothing" — the
    distinction this repo files under `unscored`, and the one a reader of a summary table cannot
    recover on their own.
    """
    (tmp_path / "notes.txt").write_text("not a recipe", encoding="utf-8")
    assert GP.main([str(tmp_path)]) == 2
    assert "NO RECIPES FOUND" in capsys.readouterr().err


def test_a_missing_path_is_refused_rather_than_reported_as_empty(tmp_path, capsys) -> None:
    assert GP.main([str(tmp_path / "nope")]) == 2
    assert "no such path" in capsys.readouterr().err


def test_a_cache_file_that_is_not_a_recipe_is_skipped_not_fatal(tmp_path, capsys) -> None:
    """A `.ultracua` cache holds meta sidecars and locks beside the flows; walking one must not die
    on the first thing that is not a recipe, or the probe cannot be pointed at a real cache."""
    (tmp_path / "a.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "b.json").write_text(
        '{"goal": "g", "steps": [{"action": "navigate", "mutating": true}]}', encoding="utf-8")
    assert GP.main([str(tmp_path)]) == 0
    out = capsys.readouterr()
    assert "navigate" in out.out and "skipping a.json" in out.err
