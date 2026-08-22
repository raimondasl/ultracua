"""The full truth table of the two write-safety predicates step 1.6 rewrote.

WHY THIS EXISTS, AND WHY IT IS COMMITTED. `_auth_retry_allowed` decides whether a drifted replay may
be re-run from `start_url` after refreshing auth. For a read that is free; for a write it is a
double-submit. Step 1.6 replaced three of its four questions with named ones, and the evidence that
the rewrite changed nothing was a DIFFERENTIAL against `main` — 1530 cells, 0 differing. That
evidence dies the moment this branch merges, because there is no longer an old tree to diff against.
So the table itself is committed here: the same cross-product, re-derived on every run.

A DIFF IN THE GOLDEN IS A CHANGE TO WHAT MAY BE RE-DRIVEN AFTER AN AUTH REFRESH. That is a
write-safety decision (inviolable #3), not a formatting detail, and it should be reviewed cell by
cell. This is the dimension CLAUDE.md asks for when a slice touches write safety — a property over a
cross-product rather than a bespoke test beside the change.

    uv run --no-sync python -m pytest tests/test_auth_retry_truth_table.py --update-truth-table

The inputs are every shape that can change an answer: 15 `MutateSpec` shapes (including the two that
a reader reliably guesses wrong — `precheck_url` alone, which declares NO precheck, and an EMPTY
`step_confirms` list, which declares no barriers), a spec with and without `login`, six recipes from
"nothing learned" to "three steps, two of which commit", and the three keyword facts.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from ultracua.cache import CachedFlow, CachedStep, StepConfirm
from ultracua.flows import (FlowSpec, LoginSpec, MutateSpec, _auth_retry_allowed, is_write_flow)

GOLDEN = pathlib.Path(__file__).resolve().parent / "goldens" / "auth_retry_truth.json"


def _sc(i: int) -> StepConfirm:
    return StepConfirm(confirm_selector="#ok", expects_intent=f"i{i}")


MUTATES = {
    "none": None,
    "bare": MutateSpec(),
    "confirm_sel": MutateSpec(confirm_selector="#done"),
    "confirm_txt": MutateSpec(confirm_text_contains="thanks"),
    "confirm_url": MutateSpec(confirm_url_contains="/receipt"),
    "pre_sel": MutateSpec(precheck_selector="#already"),
    "pre_txt": MutateSpec(precheck_text_contains="already"),
    "pre_urlc": MutateSpec(precheck_url_contains="/done"),
    # NOT a precheck: `has_precheck()` reads only the three below it, never `precheck_url`.
    "pre_url_only": MutateSpec(precheck_url="http://x/c"),
    "barriers0": MutateSpec(step_confirms=[]),
    "barriers1": MutateSpec(step_confirms=[_sc(0)]),
    "barriers2": MutateSpec(step_confirms=[_sc(0), _sc(1)]),
    "barriers3": MutateSpec(step_confirms=[_sc(0), _sc(1), _sc(2)]),
    "confirm+pre": MutateSpec(confirm_selector="#d", precheck_selector="#a"),
    "confirm+pre+b2": MutateSpec(confirm_selector="#d", precheck_selector="#a",
                                 step_confirms=[_sc(0), _sc(1)]),
}

RECIPES = {
    "no_recipe": None,          # nothing learned yet — ordinary, and it reached the old inline sum
    "r0": (),
    "r1": (True,),
    "r_read_only": (False, False),
    "r2": (True, True),         # the shape that separates the DECLARATION from the RECIPE
    "r3_mixed": (True, False, True),
}


def _flow(marks):
    if marks is None:
        return None
    return CachedFlow(key="k", goal="g", start_url="u", created_ts=0.0,
                      steps=[CachedStep(intent=f"s{i}", action="click", mutating=m)
                             for i, m in enumerate(marks)])


def derive() -> dict:
    """{cell key: (allowed, reason)} plus the `is_write_flow` half, over the whole cross-product."""
    retry, iswf = {}, {}
    for mname, mutate in MUTATES.items():
        for has_login in (False, True):
            spec = FlowSpec(
                name="f", start_url="http://h/", goal="g", mutate=mutate,
                login=LoginSpec(url="http://h/login", username_selector="#u",
                                password_selector="#p", submit_selector="#s",
                                username_env="U", password_env="P") if has_login else None)
            for rname, marks in RECIPES.items():
                cached = _flow(marks)
                iswf[f"is_write_flow|{mname}|{rname}"] = is_write_flow(spec, cached)
                for ar in (False, True):
                    for par in (False, True):
                        for landed in (False, True):
                            allowed, reason = _auth_retry_allowed(
                                spec, cached, auth_refresh=ar, parameterizing=par, landed=landed)
                            retry[f"retry|{mname}|login={has_login}|{rname}|ar={ar}|par={par}"
                                  f"|landed={landed}"] = (allowed, reason)
    return {"retry": retry, "is_write_flow": iswf}


def _as_golden(derived: dict) -> dict:
    answers = sorted({v for v in derived["retry"].values()}, key=lambda t: (not t[0], t[1]))
    idx = {a: i for i, a in enumerate(answers)}
    return {"answers": [list(a) for a in answers],
            "retry": {k: idx[v] for k, v in sorted(derived["retry"].items())},
            "is_write_flow": dict(sorted(derived["is_write_flow"].items()))}


def test_the_truth_table_is_what_the_golden_says(request) -> None:
    derived = _as_golden(derive())
    if request.config.getoption("--update-truth-table"):
        stored = json.loads(GOLDEN.read_text(encoding="utf-8"))
        stored.update(derived)
        GOLDEN.write_text(json.dumps(stored, indent=1), encoding="utf-8")
        pytest.skip("golden rewritten")

    stored = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert stored["answers"] == derived["answers"], (
        "the SET of possible answers moved — an arm was added, removed or reworded.\n"
        + "\n".join(f"  {w}: {a}" for w, a in
                    [("stored", stored["answers"]), ("actual", derived["answers"])]))
    moved = {k: (stored["retry"].get(k), v) for k, v in derived["retry"].items()
             if stored["retry"].get(k) != v}
    moved.update({k: (v, None) for k, v in stored["retry"].items() if k not in derived["retry"]})
    assert not moved, (
        f"{len(moved)} cell(s) of the auth-retry decision changed. This is what may be RE-DRIVEN "
        f"after an auth refresh — a write-safety decision, not a detail:\n"
        + "\n".join(f"  {k}: {a} -> {b}" for k, (a, b) in sorted(moved.items())[:12]))
    assert stored["is_write_flow"] == derived["is_write_flow"], "is_write_flow moved"


def test_the_table_is_not_degenerate() -> None:
    """ANTI-VACUITY, and it is not decoration: a table where every cell answers the same way is
    satisfied by `return False, ""` — which is the D0 over-refusal shape, and would pass a golden
    regenerated from it. Every arm must be REACHED by some cell, and both verdicts must occur."""
    derived = derive()
    verdicts = {v[0] for v in derived["retry"].values()}
    assert verdicts == {True, False}, f"the retry gate only ever answers {verdicts}"
    reasons = {v[1] for v in derived["retry"].values()}
    assert len(reasons) == 7, (
        f"{len(reasons)} distinct reasons reached, expected 7 — an arm became unreachable, or a new "
        f"one was added without a row here.")

    # COUNTING CANNOT FINISH THIS JOB, and the first draft of this cell did not notice. The function
    # has nine arms and only EIGHT distinct (allowed, reason) pairs, because two of them return
    # `(True, "")`: "a read is idempotent, re-running it is free" and the fall-through "a declared,
    # single-commit write WITH a precheck". Those are opposite populations reaching one answer, so a
    # count is satisfied while either is unreachable. Named cells are the only way to tell them apart.
    assert derived["retry"]["retry|none|login=True|r0|ar=True|par=False|landed=False"] == (True, ""), (
        "the READ arm is unreachable — a plain read is no longer granted its free retry")
    assert derived["retry"]["retry|pre_sel|login=True|r1|ar=True|par=False|landed=False"] == (True, ""), (
        "the fall-through arm is unreachable — a declared single-commit write WITH an idempotency "
        "precheck is the one write shape that may be retried, and nothing reaches it any more. That "
        "is the D0 over-refusal direction and a golden regenerated from it would look fine.")
    assert set(derived["is_write_flow"].values()) == {True, False}
    print(f"{len(derived['retry'])} retry cells, {len(reasons)} distinct answers; "
          f"{len(derived['is_write_flow'])} is_write_flow cells")
    for r in sorted(reasons):
        n = sum(1 for v in derived["retry"].values() if v[1] == r)
        print(f"  {n:5}x  {r[:96] or '(no reason — not an auth-refresh path)'}")


def test_the_undeclared_write_is_refused_its_retry_in_the_table() -> None:
    """R3.5 itself, as a row rather than as prose. Nothing declared, a step that in fact commits,
    an auth-refresh path available: the answer must be NO, and for the recipe's reason."""
    stored = json.loads(GOLDEN.read_text(encoding="utf-8"))
    key = "retry|none|login=True|r1|ar=True|par=False|landed=False"
    allowed, reason = stored["answers"][stored["retry"][key]]
    assert allowed is False and "declares no write" in reason, (allowed, reason)
