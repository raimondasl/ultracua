"""The scope probe must describe the scope the ENGINE computes, not the one it used to. (R4.148.)

WHY A PROBE NEEDS A TEST AT ALL, the same argument `test_gate_probe.py` makes one instrument over:
`benchmarks/scope_probe.py` is the evidence for R4.148 -- that the PRECISE mutation gate degenerates
to a whole-page gate wherever `el.closest(...)` finds no container -- and a probe that restates a
piece of `src/` drifts away from it silently. A probe describing a scope the engine no longer
computes answers confidently and wrongly, which is the shape this register keeps filing.

THREE THINGS ARE PINNED, and each has already been wrong once somewhere in this repo:

  * WHO CALLS IT. The probe labels every `scope_fingerprint` call `record` or `gate` from the calling
    frame's name. That is exact while `flow.py` has exactly those two callers and a silent
    misattribution the moment it grows a third, so the caller set is derived from the engine's AST
    and asserted BOTH ways.
  * WHAT `closest()` SELECTS. `_CONTAINER_JS` restates `SCOPE_JS`'s container selector so it can
    report which element was chosen -- a fact `SCOPE_JS` computes and throws away. The two strings
    are pinned equal, because a probe measuring a DIFFERENT container than the gate uses is the
    R4.75 failure wearing a JS hat.
  * THAT THE DIFF CAN SHOW A COUNT CHANGE. This one is not hypothetical: the probe's first draft
    diffed with set differences only, so on the single comparison the gate actually made it printed
    two empty lists beside `identical=False` -- and the entire answer was a count, x23 -> x24. The
    finding was finished by hand. A cell for that is a cell for the instrument's own blind spot.

Nothing here launches a browser, spends anything, or needs a container: it is an AST and a dict.
"""

from __future__ import annotations

import ast
import inspect

from benchmarks import scope_probe as SP


def _callers_of(func_name: str, module) -> set:
    """Every function in `module` whose body calls `func_name`, derived from the live source.

    `inspect.getsource` rather than reading `src/` BY PATH: a path-reading scan parses the pristine
    tree under `scripts/prove_red.py`'s mutant install and can never contribute a kill (R4.75).
    """
    src = inspect.getsource(module)
    tree = ast.parse(src)
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id == func_name):
                out.add(node.name)
    return out


# ------------------------------------------------------------------- who calls scope_fingerprint


def test_the_probes_phase_map_names_exactly_the_engines_callers() -> None:
    """BOTH DIRECTIONS. A caller the map is missing gets filed `unknown:<name>` -- loud, but only if
    somebody reads it; a caller the map INVENTS is a phase that can never occur, which would make a
    `record`/`gate` pair silently unpairable and the diff list empty. Neither may pass quietly.
    """
    from ultracua import flow as flow_mod

    live = _callers_of("scope_fingerprint", flow_mod)
    declared = set(SP._CALLERS)
    assert live == declared, (
        f"`benchmarks/scope_probe.py` labels a scope_fingerprint call by its CALLING FRAME, and the "
        f"engine's callers have moved. flow.py calls it from {sorted(live)}; the probe declares "
        f"{sorted(declared)}. Missing {sorted(live - declared)} would be filed as `unknown:<name>` "
        f"and drop out of the record/gate diff entirely; extra {sorted(declared - live)} declares a "
        f"phase that can never occur. Update `_CALLERS` and say what the new call MEANS."
    )


def test_the_two_phases_mean_record_and_gate() -> None:
    """The VALUES matter as much as the keys: `probe()` pairs `record` against `gate` by these exact
    strings, so a renamed value produces an empty diff list and a probe that reports nothing wrong.
    """
    assert set(SP._CALLERS.values()) == {"record", "gate"}, (
        f"`probe()` pairs calls by the literal strings 'record' and 'gate'; `_CALLERS` now yields "
        f"{sorted(set(SP._CALLERS.values()))}. A renamed value empties the diff list, so the probe "
        f"reports a clean run on a gate that refused."
    )
    assert SP._phase("_author_steps") == "record"
    assert SP._phase("_replay_step") == "gate"


def test_an_unknown_caller_is_loud_rather_than_guessed() -> None:
    assert SP._phase("some_new_helper") == "unknown:some_new_helper"


# --------------------------------------------------------------- what container the gate scopes to


def test_the_probe_asks_for_the_same_container_the_gate_does() -> None:
    """`SCOPE_JS` computes `el.closest(SEL) || document.body` and returns only the resulting ARRAY,
    so which element it picked is unrecoverable from its output -- which is why the probe restates
    the selector to report it. Restating is the risk, so the strings are pinned equal here.

    Asserted on the SELECTOR TEXT rather than on a shared constant deliberately: importing one into
    the other would make them agree by construction and measure nothing, and the point of the probe
    is to describe the product's choice independently.
    """
    from ultracua import snapshot as snap

    # Anchored on the SCOPE ASSIGNMENT, not on `el.closest(` alone: `SCOPE_JS` is spliced together
    # from `_ROLEOF_JS + _ACCNAME_JS + ...` and the accessible-name helper does its own `closest()`
    # for label lookup. Matching the bare call found two and this cell went red on the wrong one --
    # which is the cell working, one layer earlier than intended.
    marker = "const scope = el.closest("
    assert snap.SCOPE_JS.count(marker) == 1, (
        "`SCOPE_JS` no longer computes its container with a single `const scope = el.closest(...)`. "
        "`benchmarks/scope_probe.py` reports WHICH container the gate scoped to, and R4.148 rests "
        "on that number being the gate's own."
    )
    start = snap.SCOPE_JS.index(marker) + len(marker)
    engine_sel = snap.SCOPE_JS[start:snap.SCOPE_JS.index(")", start)].strip().strip("'\"")

    probe_sel = SP._CONTAINER_JS[SP._CONTAINER_JS.index("SEL = ") + 6:]
    probe_sel = probe_sel[:probe_sel.index(";")].strip()
    # The probe wraps its selector across two source lines; rejoin before comparing.
    probe_sel = "".join(part.strip().strip("'\"") for part in probe_sel.split("+"))

    assert probe_sel == engine_sel, (
        f"the scope probe measures a DIFFERENT container than the mutation gate scopes to.\n"
        f"  engine (snapshot.SCOPE_JS): {engine_sel!r}\n"
        f"  probe  (_CONTAINER_JS):     {probe_sel!r}\n"
        f"R4.148's whole claim is 'this selector matches nothing on 5 of 14 surveyed targets, so the "
        f"precise gate fingerprints the whole page'. Measured against a different selector, that "
        f"number is about nothing."
    )


# ------------------------------------------------------------------------ the diff's own blind spot


def test_the_diff_reports_a_pure_COUNT_change() -> None:
    """THE INSTRUMENT'S OWN DEFECT, armed. R4.148's answer was `['checkbox','','input']` x23 -> x24 --
    one extra list row, added by the flow's OWN write. The sets are IDENTICAL, so the first draft's
    set-difference output was two empty lists beside `identical=False`: a diff that says "these
    differ" and shows nothing at all.
    """
    row = ["checkbox", "", "input"]
    d = SP._diff([row] * 23, [row] * 24)

    assert not d["only_recorded"] and not d["only_replayed"], (
        "this cell's premise is that the SETS are identical; if they are not, it is no longer "
        "exercising the blind spot it exists for"
    )
    assert d["identical"] is False and d["same_multiset"] is False
    assert d["count_changes"] == [{"triple": row, "recorded": 23, "replayed": 24}], (
        "the scope diff has lost its count-change reporting, which is the ONLY field that showed "
        "R4.148's cause. Without it the probe reports a difference it cannot describe."
    )


def test_an_identical_scope_reports_no_change_at_all() -> None:
    """The quiet direction, pinned as hard as the loud one: a gate that PASSES must diff clean, or
    every future reading of this probe starts by discounting phantom rows.
    """
    scope = [["button", "Save", "button"], ["textbox", "Name", "input"]]
    d = SP._diff(scope, list(scope))
    assert d["identical"] is True and d["same_multiset"] is True
    assert d["count_changes"] == [] and not d["only_recorded"] and not d["only_replayed"]


def test_an_ORDER_only_change_is_distinguished_from_a_content_change() -> None:
    """A re-render that re-sorts the same controls is a different world from one that adds a row, and
    the gate refuses both (it compares a hash of the ORDERED array). Keeping them apart is what lets
    a future reader tell "the page changed" from "the page was rebuilt".
    """
    a = [["button", "Save", "button"], ["textbox", "Name", "input"]]
    d = SP._diff(a, list(reversed(a)))
    assert d["identical"] is False, "an order change must still register as a difference"
    assert d["same_multiset"] is True, "...but not as a change of CONTENT"
    assert d["count_changes"] == []
