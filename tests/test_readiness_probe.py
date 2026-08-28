"""The readiness probe's claims, pinned against the engine's own source.

WHY THIS FILE EXISTS. `benchmarks/readiness_probe.py` asserts three things ABOUT `src/`: that the
replay reaches page state at three named sites with no settle, that `RunRecord.llm_calls` cannot see
an extraction call, and that `resolve() -> None` is an overloaded sensor whose meanings include
deliberate safety refusals. A probe that describes a mechanism the code no longer has answers
confidently and wrongly -- R4.111's lesson, and the reason `tests/test_gate_probe.py` exists. So each
claim is derived from real source here rather than restated in prose.

`scripts/prove_red.py` CANNOT reach `benchmarks/` (R4.77: it installs a mutant by putting a copy of
`src/` first on PYTHONPATH, and pytest puts the repo ROOT at sys.path[0]), so these cells read `src/`
through `inspect.getsource` -- which is the half a mutation CAN reach -- and the probe's own
constants directly.

Browser-free: every cell reads source or a module constant. The probe's live half is
`--remedy`/`--contrast`, which are operator surfaces.
"""

from __future__ import annotations

import inspect

import pytest

from benchmarks import readiness_probe as P
from ultracua import flow as flow_mod
from ultracua import flows as flows_mod
from ultracua import locators as L


# --------------------------------------------------------------- the three sites the probe names


def test_the_three_page_state_sites_are_still_where_the_probe_says() -> None:
    """THE PROBE'S SUBJECT. If any of these moves, the docstring's site list is stale and the
    measurement no longer describes this engine."""
    src = inspect.getsource(flow_mod)
    assert "resolve(page, step.locator, unique=True, sink=tr.meta)" in src, (
        "site 1 moved: the replay's locator resolve")
    assert "current = await scope_fingerprint(target)" in src, (
        "site 2 moved: the mutation gate's precise form/section fingerprint")
    assert "obs = await session.snapshot()" in src and "mutation gate: page drift" in src, (
        "site 3 moved: the mutation gate's whole-page fallback")


def test_no_settle_stands_between_arriving_and_resolving() -> None:
    """THE DEFECT, as a property. `goto` waits only for `domcontentloaded`, which on a
    client-rendered app fires against an unpainted page -- measured, Odoo serves 5 elements and 1
    character there. Nothing between that and the resolve waits for anything.

    Asserted on `BrowserSession.goto` rather than on the absence of a wait somewhere in `flow.py`:
    a negative over a 2000-line module is satisfied by deleting the wrong thing, and this is the
    line that decides what "arrived" means."""
    from ultracua.browser import BrowserSession
    goto = inspect.getsource(BrowserSession.goto)
    assert 'wait_until="domcontentloaded"' in goto, (
        "`goto`'s readiness contract changed -- re-measure the contrast before trusting the probe")
    assert "networkidle" not in goto and "wait_for_selector" not in goto, (
        "`goto` grew a settle; R4.115's premise needs re-measuring")


# ------------------------------------------------------------------ trap 1: llm_calls is blind


def test_llm_calls_is_a_decide_counter_and_says_so() -> None:
    """TRAP 1, and it reported a green for this very finding. An extracting read spends a real
    strong-tier call that this counter structurally cannot see."""
    src = inspect.getsource(flows_mod)
    assert 'llm_calls: int = 0                  # DECIDE calls; API calls are usage["calls"]' in src, (
        "the `llm_calls` comment moved -- it is the only thing that documents the trap the probe warns "
        "about, and a reader who believes the NAME will report a spending replay as 0-LLM")


def test_an_unpinned_read_reaches_the_extractor() -> None:
    """The other half of trap 1: this is the branch that spends, and it is taken whenever a read
    flow has no `read_pin` -- which is the default, and was true of the flow that reported 0."""
    src = inspect.getsource(flows_mod)
    assert "if pin is not None:" in src and "await extract(router, spec.extract, text" in src, (
        "the pinned/unpinned read split moved; the probe's claim that an unpinned read spends a "
        "call is what makes `llm_calls: 0` misleading")


# ------------------------------------------- trap 2 / the refutation: `None` is an overloaded sensor


@pytest.mark.parametrize("needle, meaning", [
    ('sink["bound_by"] = "none"', "the row-containment refusal"),
    ('sink["conflict"] = True', "the Tier-2 cross-check disagreed"),
    ('sink["identity_contradiction"] = True', "the bind falsifies the recorded identity"),
    ("if unique or ambiguous is None:", "ambiguity under unique=True"),
])
def test_resolve_returns_none_for_a_safety_reason_not_only_for_absence(needle, meaning) -> None:
    """THE REFUTATION, as a property rather than a claim. Each of these is a DELIBERATE refusal that
    also surfaces as `None`, so a remedy keyed on the return value re-drives it. Measured end to end
    by `readiness_probe --remedy`: the retry binds a different record via `role+name`.

    This is D5's overloaded-`None` one level up from `anchor_id`, and it is why the probe refutes the
    obvious fix instead of proposing it."""
    src = inspect.getsource(L)
    assert needle in src, (
        f"{meaning}: this refusal path moved or changed shape. The probe's refutation counts on "
        f"`resolve() -> None` meaning more than 'not rendered' -- re-derive it before relying on "
        f"`readiness_probe --remedy`")


def test_the_remedy_case_starts_genuinely_ambiguous() -> None:
    """THE PROBE'S OWN PREMISE, and its first draft got this wrong -- a css that matched one row
    bound at Tier 2 immediately, so there was no refusal to re-drive and the probe reported 'not
    reproduced' while testing nothing. The recorded css must match BOTH rows."""
    assert P.REMEDY_SPEC["css"] == "tbody > tr > td > a", (
        "the remedy spec's css must stay row-agnostic, or the probe measures nothing")
    assert P._TWO_ROWS.count("Cancel</a>") == 2, "the case needs two competing controls"
    assert "display='none'" in P._TWO_ROWS.replace(" ", ""), (
        "the recorded row must be HIDDEN rather than removed -- that asymmetry (querySelectorAll "
        "sees hidden rows, get_by_role does not) is what makes the sibling uniquely matchable")
    assert P.RECORDED_HREF == "/cancel/3", "the verdict compares against the RECORDED row"


def test_the_probe_refuses_to_run_with_no_mode() -> None:
    """An operator surface that does nothing and exits 0 reads as a pass."""
    with pytest.raises(SystemExit):
        P.main([])
