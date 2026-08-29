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


def test_compose_needs_both_a_scenario_and_a_recipe() -> None:
    """The 2x2 cannot be assembled from one of them, and defaulting either would silently measure a
    different recipe than the operator named."""
    with pytest.raises(SystemExit):
        P.main(["--compose"])
    with pytest.raises(SystemExit):
        P.main(["--compose", "--scenario", "odoo-sort-list"])


# ------------------------------------------------------ the recipe-shape census (R4.118)


def _recipe(*actions, texts=None):
    texts = texts or [None] * len(actions)
    return {"steps": [{"action": a, "text": t} for a, t in zip(actions, texts)]}


def test_a_navigate_only_recipe_is_flagged_degenerate() -> None:
    """THE PROPERTY. Measured on Odoo: `odoo-filter-status` cached 20 steps of which 20 were
    `navigate`, and `odoo-open-record` 8 of 8. Such a recipe replays every step ok and performs no
    task at all, so the failure surfaces far from its cause -- or not at all."""
    s = P.recipe_shape(_recipe("navigate", "navigate", "navigate"))
    assert s["degenerate_navigate_only"] is True
    assert s["navigate_fraction"] == 1.0


def test_a_recipe_that_acts_is_not_flagged() -> None:
    """BOTH DIRECTIONS. A census that flags everything is as useless as one that flags nothing --
    the real `odoo-sort-list` recipe is navigate + scroll + 3 clicks and must come back clean."""
    s = P.recipe_shape(_recipe("navigate", "scroll", "click", "click", "click"))
    assert s["degenerate_navigate_only"] is False
    assert s["kinds"]["click"] == 3


def test_repeated_navigations_are_counted() -> None:
    """The signature of an agent that cannot tell whether the page arrived: it re-navigates to the
    SAME url. Measured 12 repeats in `odoo-filter-status`."""
    s = P.recipe_shape(_recipe("navigate", "navigate", "navigate",
                               texts=["/a", "/a", "/b"]))
    assert s["repeated_navigations"] == 1


@pytest.mark.parametrize("url, malformed", [
    ("http://h/web#action=&model=crm.lead&view_type=list", True),   # measured, 4x in one recipe
    ("http://h/web#action=", True),                                 # trailing, nothing after it
    ("http://h/web#action=318&model=crm.lead", False),              # a real action id
    ("http://h/odoo/crm", False),
])
def test_an_empty_action_parameter_is_detected(url, malformed) -> None:
    """Odoo cannot resolve `action=` with nothing after it, so it serves the DEFAULT app (Discuss)
    and every later step runs against the wrong page while reporting ok. Both directions asserted:
    a real action id must NOT be flagged, or the census cries wolf on healthy recipes."""
    s = P.recipe_shape(_recipe("navigate", texts=[url]))
    assert bool(s["malformed_urls"]) is malformed


def test_a_scroll_only_recipe_is_degenerate_though_no_navigate_is_involved() -> None:
    """THE MEASURED BLIND SPOT. When the learn-side settle fixed R4.118's navigate thrash,
    `odoo-open-record` came back with FOUR `scroll` steps and nothing else -- the same "moved the
    browser, touched nothing" shape -- and `degenerate_navigate_only` called it clean. The general
    predicate is LOCOMOTION vs INTERACTION (R4.121)."""
    s = P.recipe_shape(_recipe("scroll", "scroll", "scroll", "scroll"))
    assert s["degenerate_navigate_only"] is False, "no navigate is involved, so the specific flag stays off"
    assert s["degenerate_no_interaction"] is True


def test_a_single_click_recipe_is_not_degenerate() -> None:
    """BOTH DIRECTIONS. The predicate is deliberately NOT 'all steps share one action', which would
    condemn a legitimate one-click flow -- the shortest useful recipe there is."""
    assert P.recipe_shape(_recipe("click"))["degenerate_no_interaction"] is False


def test_locomotion_mixed_with_one_interaction_is_not_degenerate() -> None:
    """`odoo-filter-status` after the settle is `type` + `click`; `odoo-sort-list` is two clicks. A
    recipe that navigates and scrolls its way to ONE real action still performs its task."""
    assert P.recipe_shape(_recipe("navigate", "scroll", "scroll", "click")
                          )["degenerate_no_interaction"] is False


def test_an_empty_recipe_is_not_called_degenerate() -> None:
    """`0 navigates == 0 steps` is vacuously true, and a zero-step recipe is R4.101's
    `no_actions_needed`, which is a different thing entirely."""
    assert P.recipe_shape({"steps": []})["degenerate_navigate_only"] is False


# ------------------------------------------------------------- the composition (R4.117)


def test_the_composition_criterion_is_the_replay_not_the_oracle() -> None:
    """R4.116: `odoo-sort-list`'s extractor is handed the whole page with the goal as its prompt and
    answers correctly over an ASCENDING list, so `RESULT == EXPECTED` cannot evidence that the replay
    did the task. The 2x2's cells are decided by `every_step_ok` and the source must say so."""
    import inspect
    src = inspect.getsource(P)
    assert "every_step_ok=bool(out[\"ok\"] and steps and all(s.get(\"ok\") for s in steps))" in src, (
        "the composition's criterion moved; if it ever becomes the oracle, R4.116 makes every cell a "
        "false pass")


def test_the_readiness_ceiling_restores_every_binding_it_replaced() -> None:
    """It monkeypatches THREE module globals, and an arm that leaves them patched silently makes the
    NEXT arm a readiness arm too -- which would turn the 2x2 into two identical rows."""
    from ultracua import flow as flow_mod
    from ultracua.browser import BrowserSession

    before = (L.resolve, flow_mod.resolve, flow_mod.scope_fingerprint, BrowserSession.snapshot)
    P._patch_readiness()
    assert L.resolve is not before[0], "the patch did not take, so the ceiling measures nothing"
    P._unpatch()
    assert (L.resolve, flow_mod.resolve, flow_mod.scope_fingerprint,
            BrowserSession.snapshot) == before
