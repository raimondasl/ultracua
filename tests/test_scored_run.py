"""The end-to-end runner's pure parts, and R4.92's discriminator.

`benchmarks/scored_run.py` needs Docker, a browser and a key to run for real, so what is testable
here is what it DERIVES: the FlowSpec it builds from a corpus entry, the gate evidence it reads off
a cached recipe, and the record it hands `outcomes.classify`. Those are exactly the parts that
decided a wrong verdict on the first live run — the draft handed `classify` the LEARN's data
alongside the REPLAY's refusal code and got `ok` for a run the mutation gate had refused.

THE PIN AT THE BOTTOM IS THE LOAD-BEARING ONE. R4.92's whole fix rests on `flow.py` setting
`StepTrace.meta["gate"]` in exactly one place, inside the mutating branch. That is an internal
detail of the engine, not a contract, and nothing else in the repo depends on it — so if it is
renamed, the benchmark silently stops seeing gate refusals and quietly reports `refused` again. A
silent regression in the headline number is the worst failure mode this repo has, so the marker is
pinned structurally and the pin fails when it is MISSING rather than scanning whatever it finds.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from benchmarks import corpus as C
from benchmarks import outcomes as O
from benchmarks import scored_run as SR
from benchmarks import substrates as S
from ultracua import flow as flow_mod


# ---------------------------------------------------------------------------------------------
# the spec it builds
# ---------------------------------------------------------------------------------------------

def _entry(name: str):
    return next(e for s in ("gitea", "odoo") for e in C.for_substrate(s)
                if e.scenario.name == name)


def test_the_agent_is_pointed_at_the_proxy_and_the_oracle_is_not() -> None:
    """The separation the whole evidence design rests on. If the spec named the substrate directly,
    the proxy would see nothing and `odoo-idempotent-replay` would report that the write mechanism
    never ran — for a run that keyed one perfectly."""
    spec = SR.spec_for(_entry("odoo-sort-list"), "http://127.0.0.1:9", "/tmp/a.json")
    assert spec.start_url.startswith("http://127.0.0.1:9")
    assert spec.login.url.startswith("http://127.0.0.1:9")
    assert S.Odoo().url not in spec.start_url, "the agent would bypass the proxy"


def test_a_read_declares_an_extraction_and_a_write_does_not() -> None:
    """A write is adjudicated by what LANDED — `CorpusEntry` refuses an expected answer for one, and
    a spec that asked the engine to extract data from a write would be scoring it on its own say-so
    through the back door."""
    assert SR.spec_for(_entry("odoo-sort-list"), "http://x", "/tmp/a").extract
    assert SR.spec_for(_entry("odoo-create-lead"), "http://x", "/tmp/a").extract is None


def test_credentials_are_passed_by_ENV_NAME_and_never_by_value() -> None:
    """`LoginSpec` reads them at run time and persists only cookies. A spec carrying the literal
    would put a password into the cached flow, which is the one thing this repo never does."""
    spec = SR.spec_for(_entry("odoo-sort-list"), "http://x", "/tmp/a")
    assert spec.login.username_env == SR.USER_ENV and spec.login.password_env == SR.PASS_ENV
    blob = repr(spec)
    assert S.ODOO_PASSWORD not in blob and S.GITEA_PASSWORD not in blob


def test_every_corpus_substrate_has_login_wiring() -> None:
    """Derived, so a third substrate cannot join the corpus and fail at run time with a KeyError
    after the reset has already been paid for."""
    assert sorted(SR.LOGIN) == sorted(C.CORPORA)


# ---------------------------------------------------------------------------------------------
# R4.92 — which component refused
# ---------------------------------------------------------------------------------------------

class _Step:
    def __init__(self, mutating=False, sources=()):
        self.mutating, self.mutating_sources = mutating, list(sources)


class _Cached:
    def __init__(self, steps, approved=None):
        self.steps = steps
        self.meta = type("M", (), {"approved": approved})()


class _Cache:
    def __init__(self, cached):
        self._c = cached

    def get(self, _key):
        return self._c


class _Spec:
    key = "k"
    write = type("W", (), {"declares_write": False})()


def test_the_gate_evidence_reads_the_recipe_and_the_run() -> None:
    ev = SR._gate_evidence(_Spec(), _Cache(_Cached([_Step(True, ("wire",)), _Step()])),
                           gate_refused=True, pinned=True)
    assert ev["present"] and ev["mutating_steps"] == 1
    assert ev["mutating_sources"] == ("wire",)
    assert ev["mutation_gate_refused"] is True and ev["substrate_pinned"] is True
    assert O.GateEvidence(**ev).marked_as_a_write is True


def test_an_unlearned_flow_yields_no_gate_evidence_rather_than_a_confident_empty_one() -> None:
    """`GateEvidence(present=False)` and `{}` are different: B3 refuses to infer over-gating without
    a readable recipe, and a default-constructed one would claim the recipe WAS read and said
    nothing."""
    assert SR._gate_evidence(_Spec(), _Cache(None)) == {}


@pytest.mark.parametrize("refused,pinned,expect", [
    (True, True, O.OVER_GATED),      # the measured run: the gate refused a read on a pinned world
    (False, True, O.REFUSED),        # an ordinary step drifted; nothing gated anything
    (True, False, O.REFUSED),        # the drift arm: the gate refused, but drift is expected there
    (False, False, O.REFUSED),
])
def test_over_gated_needs_the_gate_AND_a_pinned_substrate(refused, pinned, expect) -> None:
    """THE R4.92 DECISION, all four corners.

    Both facts are required and they come from different places: `mutation_gate_refused` from the
    run's step traces, `substrate_pinned` from the harness. Either alone is an inference — a recipe
    marked as a write can still suffer genuine drift, and a gate refusal on an UNPINNED substrate is
    the drift arm working as designed rather than over-gating.

    The `(True, False)` row is the one that matters most. Its default is the safe direction on
    purpose: forgetting to affirm `substrate_pinned` costs a `refused` and understates the finding,
    whereas an opt-out would inflate the benchmark's headline against the product.
    """
    gate = O.GateEvidence(present=True, mutating_steps=4, mutating_sources=("wire",),
                          declares_write=False, mutation_gate_refused=refused,
                          substrate_pinned=pinned)
    v = O.classify(O.ScenarioTruth(name="odoo-sort-list", mutating=False),
                   _Run(code="drift"), O.Oracle(available=True, data_correct=None), gate)
    assert v.outcome == expect, v


def test_a_read_that_answered_correctly_is_still_ok_even_if_the_gate_refused() -> None:
    """Anti-vacuity in the direction that matters: the new clause sits AFTER the data verdicts, so a
    run that produced the right answer is `ok` whatever else happened. Over-gating is a way of
    FAILING a read, not a label for every gated one."""
    gate = O.GateEvidence(present=True, mutating_steps=4, mutating_sources=("wire",),
                          declares_write=False, mutation_gate_refused=True, substrate_pinned=True)
    v = O.classify(O.ScenarioTruth(name="odoo-sort-list", mutating=False),
                   _Run(code="drift"), O.Oracle(available=True, data_correct=True), gate)
    assert v.outcome == O.OK, v


def test_a_WRITE_scenario_never_reaches_the_over_gated_clause() -> None:
    """`over_gated` is a READ outcome. A write refused by the write machinery is the machinery doing
    its job, and B3's write vocabulary has `refused_correctly` / `refused_wrongly` for it."""
    gate = O.GateEvidence(present=True, mutating_steps=4, mutating_sources=("wire",),
                          declares_write=True, mutation_gate_refused=True, substrate_pinned=True)
    v = O.classify(O.ScenarioTruth(name="odoo-create-lead", mutating=True),
                   _Run(code="drift"), O.Oracle(available=True), gate)
    assert v.outcome in O.WRITE_OUTCOMES, v
    assert v.outcome != O.OVER_GATED


class _Run:
    def __init__(self, code="", authored=None, harness=""):
        self.agent_ran = True
        self.agent_error = "DriftError: ..." if code else ""
        self.harness_error = harness
        self.agent_error_code = code
        self.claimed_complete = None
        self.authored = authored


# ---------------------------------------------------------------------------------------------
# the marker R4.92 rests on, pinned so it cannot walk away
# ---------------------------------------------------------------------------------------------

def test_the_mutation_gate_marker_is_set_in_exactly_one_place_and_only_for_a_write() -> None:
    """R4.92's DISCRIMINATOR, pinned structurally.

    `meta["gate"]` is an internal detail of `flow.py`. Nothing in `src/` reads it, so nothing in
    `src/` would notice if it were renamed — and the benchmark would silently stop distinguishing
    "the write machinery blocked a read" from "the page moved", falling back to `refused` with no
    test going red. That is a silent regression in the headline number.

    Asserted on the AST rather than by grepping text, and it fails when the marker is MISSING rather
    than scanning whatever it happens to find — CLAUDE.md's rule about a scan that names one
    function asserting a negative about a body that can walk away.
    """
    sites, guarded = _gate_marker_sites(inspect.getsource(flow_mod))

    assert len(sites) == 1, (
        f"`meta['gate']` is assigned at {len(sites)} sites in flow.py, not 1. R4.92 reads it as "
        f"'the MUTATION GATE refused'; a second writer makes that reading wrong, and zero writers "
        f"makes the benchmark silently report `refused` for every over-gated read.")

    # ...and it must sit inside `if step.mutating:`, or it stops meaning "a write was gated".
    assert guarded, (
        "`meta['gate']` is no longer inside an `if <step>.mutating:` branch, so a trace carrying it "
        "no longer proves the mutation gate was what refused — which is the entire basis of R4.92.")


def test_the_runner_reads_that_marker_and_not_the_refusal_message() -> None:
    """`reshape-plan` 2.2 forbids bucketing on message labels by name. The gate's three refusals are
    distinguishable only by prose (`target missing/ambiguous`, `form/section drift`, `page drift`),
    so a runner that matched on those would be doing exactly that."""
    src = inspect.getsource(SR.score_one)
    assert '.get("gate") == "drift"' in src, "the runner no longer reads the structured marker"
    for prose in ("mutation gate:", "target missing", "refusing to re-drive"):
        assert prose not in src, f"the runner matches the refusal MESSAGE ({prose!r})"


def _gate_marker_sites(src: str):
    """Every `<x>.meta["gate"] = ...` in `src`, and whether the first sits inside `if <x>.mutating:`.

    EXTRACTED SO THE SCAN ITSELF CAN BE ARMED. A structural pin that has only ever been run against
    the source it passes on is a pin nobody has watched refuse — and this repo has shipped three of
    those in one slice. The two cells below drive it against source that lacks the marker and source
    that has it in the wrong place.
    """
    tree = ast.parse(src)
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if (isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Attribute)
                and tgt.value.attr == "meta"
                and isinstance(tgt.slice, ast.Constant) and tgt.slice.value == "gate"):
            sites.append(node)
    guarded = [n for n in ast.walk(tree)
               if isinstance(n, ast.If)
               and isinstance(n.test, ast.Attribute) and n.test.attr == "mutating"
               and any(s is sites[0] for sub in n.body for s in ast.walk(sub))] if sites else []
    return sites, guarded


def test_the_marker_scan_finds_nothing_when_the_marker_is_gone() -> None:
    """The pin's RED direction. Without this the scan could be broken — matching nothing, ever — and
    the cell above would still pass on the day the marker was renamed, because `len([]) == 1` is the
    failure it is supposed to report and a scan that always returns `[]` fails it for the wrong
    reason. Driving it against source that genuinely lacks the marker is what separates those."""
    src = textwrap.dedent("""
        def f(step, tr):
            tr.meta["other"] = "drift"
    """)
    sites, guarded = _gate_marker_sites(src)
    assert sites == [] and guarded == []


def test_the_marker_scan_notices_it_moving_outside_the_mutating_branch() -> None:
    """The second half. A marker set on EVERY step would still be one assignment, so a count alone
    would pass while `meta['gate']` had stopped meaning 'the mutation gate refused' — which is the
    entire basis of R4.92."""
    unguarded = textwrap.dedent("""
        def f(step, tr):
            if step.visible:
                tr.meta["gate"] = "drift"
    """)
    sites, guarded = _gate_marker_sites(unguarded)
    assert len(sites) == 1 and guarded == [], "an unguarded marker must not read as guarded"

    real = textwrap.dedent("""
        def f(step, tr):
            if step.mutating:
                tr.meta["gate"] = "drift"
    """)
    sites, guarded = _gate_marker_sites(real)
    assert len(sites) == 1 and guarded, "the real shape must read as guarded"


# ---------------------------------------------------------------------------------------------
# the MutateSpec the runner builds
# ---------------------------------------------------------------------------------------------

def test_a_read_gets_no_mutate_spec_at_all() -> None:
    """`spec.mutate is None` is what keeps `declares_write` False and the whole write machinery off
    a read flow. A read that acquired one would manufacture the over-gating the corpus measures."""
    assert SR._mutate_spec(_entry("odoo-sort-list"), "http://x") is None
    assert SR.spec_for(_entry("odoo-sort-list"), "http://x", "/tmp/a").write.declares_write is False


def test_a_write_gets_a_confirm_and_declares_itself() -> None:
    spec = SR.spec_for(_entry("odoo-create-lead"), "http://x", "/tmp/a")
    assert spec.write.declares_write is True
    assert spec.mutate.confirm_text_contains == "Bench probe lead"
    assert spec.mutate.precheck_url is None, "only the precheck scenario may declare one"


def test_the_precheck_url_is_built_on_the_SAME_base_the_agent_uses() -> None:
    """The agent is pointed at the evidence proxy, whose port is ephemeral. A precheck URL that
    named a different origin would send `_already_committed` to look for the end-state somewhere the
    run never touched — and it would report `already-done` never, or always, for the wrong reason."""
    spec = SR.spec_for(_entry("odoo-idempotent-replay"), "http://127.0.0.1:5555", "/tmp/a")
    assert spec.mutate.precheck_url.startswith("http://127.0.0.1:5555")
    assert spec.start_url.startswith("http://127.0.0.1:5555")


def test_the_selector_confirm_survives_the_translation() -> None:
    """`gitea-start-timer` confirms structurally, because a running stopwatch shows no obvious text
    — the visible label is "Stop Timer" and the first guess was wrong by one character of case."""
    spec = SR.spec_for(_entry("gitea-start-timer"), "http://x", "/tmp/a")
    assert spec.mutate.confirm_selector == ".issue-stop-time"
    assert spec.mutate.confirm_text_contains is None


def test_the_approval_is_read_from_the_sidecar_and_not_from_the_cached_flow() -> None:
    """`approve()` writes through `_update_meta`, so the flag never lands on the CachedFlow. The
    first draft read `cached.meta` and published `approved: null` for a flow it had just approved.

    It changed no outcome on the run that exposed it, which is exactly why it needs a cell:
    `GateEvidence.approved` is what separates "a lifecycle gate refused this read" from "the write
    machinery did", and a wrong value there misdiagnoses the next `not_approved` run rather than the
    one that produced it.
    """
    src = inspect.getsource(SR._gate_evidence)
    assert "_load_meta" in src, "the approval is being read from the wrong object again"
    assert 'getattr(cached, "meta"' not in src


def test_the_step_budget_is_declared_by_the_bench_and_recorded() -> None:
    """`settings.max_steps` is 8, and BOTH attempts at `odoo-create-lead` used exactly 8 calls and
    cached nothing — a harness limit that read as the agent failing the task. The budget is the
    bench's to declare, and every record carries it plus whether the run ended AT it."""
    assert SR.MAX_STEPS > 8, "the budget must not silently inherit the global that caused this"
    spec = SR.spec_for(_entry("odoo-create-lead"), "http://x", "/tmp/a")
    assert spec.max_steps == SR.MAX_STEPS
    src = inspect.getsource(SR.score_one)
    for field in ("hit_step_ceiling", "step_budget"):
        assert field in src, f"a run no longer records {field!r}"


# ---------------------------------------------------------------------------------------------
# a failed learn is a RESULT, not a row that leaves the denominator
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mutating", [False, True])
def test_a_failed_learn_is_scored_rather_than_deleted_from_the_denominator(mutating) -> None:
    """THE FLATTERING BUG, both vocabularies.

    `unscored` removes a row from every rate. §6 budgets a 52-60% discovery-failure rate, so routing
    failed learns there computes `availability_rate` over the scenarios that happened to work —
    answering "of the tasks we could author, how many replay?" while being read as "how many of
    these tasks can the product do?". Roughly a doubling, in the flattering direction.

    And the coverage channel does not save it: that fails a run unless each unscored `(scenario,
    reason)` pair is ACKNOWLEDGED, and a recurring, expected discovery failure is exactly what an
    operator would acknowledge — which is the act that deletes it.
    """
    v = O.classify(O.ScenarioTruth(name="x", mutating=mutating),
                   _Run(authored=False), O.Oracle(available=True))
    assert v.outcome == O.NOT_AUTHORED
    assert v.scored is True, "a discovery failure must stay in the denominator"
    assert v.outcome not in O.QUIET_OUTCOMES, "and it must not read as a pass"


def test_a_harness_fault_still_outranks_a_failed_learn() -> None:
    """If the reset or the login broke, the learn never got a fair attempt, and blaming the product
    for it is the attribution error this whole module is arranged against."""
    v = O.classify(O.ScenarioTruth(name="x"), _Run(authored=False, harness="reset failed"),
                   O.Oracle(available=True))
    assert v.outcome == O.UNSCORED and "harness" in v.reason


def test_a_violation_the_oracle_can_see_still_outranks_a_failed_learn() -> None:
    """A learn can FAIL having already actuated (`LearnResult.performed_write`), so a write the
    server is holding is the more specific statement about what the product did."""
    v = O.classify(O.ScenarioTruth(name="x", mutating=True), _Run(authored=False),
                   O.Oracle(available=True, matched=("a", "b")))
    assert v.outcome == O.DOUBLE


def test_not_authored_is_never_minted_by_inference() -> None:
    """`authored` is tri-state and `None` means "no claim". An arm that does not learn at all — a
    pure-LLM baseline — must not have every scenario relabelled a discovery failure."""
    v = O.classify(O.ScenarioTruth(name="x"), _Run(authored=None),
                   O.Oracle(available=False, unavailable_reason="no proxy"))
    assert v.outcome == O.UNSCORED


def test_the_runner_reports_what_it_learned_and_never_claims_the_agent_did_not_run() -> None:
    """The runner's own half. Its first draft set `agent_ran=False` for a failed learn — a statement
    that is simply false, and the route by which the row reached `unscored`."""
    src = inspect.getsource(SR.score_one)
    assert "authored=out.get(\"learned\")" in src, "the runner no longer reports what it authored"
    assert "agent_ran = False" not in src, "the runner claims the agent did not run again"
