"""H9 evals: semantic-wrongness defense — value contracts + judge-sampled canary.

ROADMAP H9: two layers against plausible-but-WRONG extracted data. Layer 1 (hot path, key-less,
deterministic): per-field contracts (type / format regex / numeric range / null-rate ceiling /
list-count lower bounds / max-delta vs rolling median) checked right after the shipped shape gate;
a violation raises a PERSISTED quarantine that future runs refuse until a human `flow release`s.
Layer 2 (async, off the replay verb entirely): a budgeted sampled LLM judge over persisted run
artifacts whose verdict can ONLY quarantine-forward — never gates an in-flight run, never approves,
never clears. Probed future surfaces: ultracua.contracts (FieldContract), FlowSpec.contracts,
FlowQuarantineError, a `quarantined` health status + `flows.release` + `flow release` CLI,
FlowSpec.audit artifact capture, a `flows.audit` judge verb, per-field sketch history JSONL.

Partial credit measured today (the building blocks Layer 1 is specified to ride on):
- the STRUCTURAL gate ships: shape drift on replay raises FlowReplayError and health goes failing
- Extraction.truncated ships (#78): the flag contract seeding depends on (risk: contracts seeded
  from a silently-truncated learn would DEFEND partial data as normal — prereq #3)

The centerpiece is the gap fixture the horizon exists to close: the page value changes 42 -> 9999
between learn and replay — SAME shape, wrong value — and today NOTHING flags it. That check is
recorded as the aspirational `missing`, not a `fail`: shape-drift catching wrong values was never
claimed ("shape-drift can't see wrong-but-present values" — ROADMAP).

Everything here is key-less: local Fixture pages, a scripted agent, MockClient extraction
(the tests/test_flows.py convention), real headless Chromium, $0.
"""

from __future__ import annotations

from evals.core import Ctx, expect, import_probe, scenario
from evals.fixtures import Fixture, page


class _ClickFirstLink:
    """Scripted key-less 'agent': click the first link once (navigating), then done."""

    def __init__(self) -> None:
        self._clicked = False

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        if not self._clicked:
            for el in obs.elements:
                if el.role == "link":
                    self._clicked = True
                    return Action(action="click", intent="open the report page", ref=el.ref), None
        return Action(action="done", intent="done"), None


def _extract_router(*datas):
    """A Router whose successive extraction calls return {found: True, data: <each>} — scripted
    MockClient extraction (tests/test_flows.py convention): deterministic, key-less, 0 real LLM."""
    from ultracua.llm.base import Router, Tier
    from ultracua.llm.mock import MockClient

    mc = MockClient(actions=[{"found": True, "data": d} for d in datas], tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


def _not_found_router():
    """Scripted extraction that reports the data is NOT on the page (found=False)."""
    from ultracua.llm.base import Router, Tier
    from ultracua.llm.mock import MockClient

    mc = MockClient(actions=[{"found": False, "error": "not on the page"}], tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


def _report_fixture() -> Fixture:
    """Two-page read flow: home -> report page carrying the value the flow extracts."""
    return Fixture({
        "/": page('<a href="/report">open the daily report</a>', title="home"),
        "/report": page("<h1>Report</h1><p>total: 42</p>", title="report"),
    })


def _report_spec(base: str, name: str):
    from ultracua.flows import FlowSpec

    return FlowSpec(name=name, start_url=base + "/", goal="open the daily report page",
                    extract="the report total", headless=True)


@scenario(
    id="h09.shape.structural_gate",
    title="shipped Layer 0: a structural shape change on replay fails LOUD, never returns reshaped data",
    group="h09", tags=("fail-loud", "read", "drift"),
    notes="the seam H9 Layer 1 extends: contracts are specified to run right after this gate",
)
async def structural_gate(ctx: Ctx):
    from ultracua.flows import FlowReplayError, health, learn, replay

    checks = []
    fx = _report_fixture()
    with fx.serve() as base:
        cache = ctx.cache()
        spec = _report_spec(base, "shapegate")

        # SHIPPED: learn records a structural signature of the extracted data in the meta sidecar —
        # the baseline every replay is compared against (and the seam contracts would be seeded at).
        learned = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        checks.append(expect(learned.cached and learned.found and learned.shape is not None,
                             "learn caches the flow and records the data's shape signature",
                             f"cached={learned.cached} found={learned.found} shape={learned.shape!r}"))
        if not learned.cached:
            return checks  # nothing learned -> the gate probes below would be vacuous

        # SHIPPED FAIL-LOUD: replay extracts a LIST where a number was learned -> the shape gate
        # refuses with FlowReplayError instead of returning the reshaped data (inviolable #2).
        exc = None
        try:
            await replay(spec, router=_extract_router(["a", "b"]), cache=cache)
        except Exception as e:  # noqa: BLE001 — recorded as a check, never raised out
            exc = e
        checks.append(expect(isinstance(exc, FlowReplayError),
                             "number -> list on replay raises FlowReplayError (never returns reshaped data)",
                             f"exc={exc!r}"))
        checks.append(expect(exc is not None and "shape" in str(exc).lower(),
                             "the drift error NAMES the shape change (actionable diagnosis, not a bare fail)",
                             f"exc={exc}"))

        # SHIPPED: the refused run lands in the health sidecar as a failing run with the error kept —
        # the trust surface a future `quarantined` status is specified to join.
        h = health(spec, cache=cache)
        checks.append(expect(h.status == "failing" and h.last_error is not None,
                             "health records the drift as failing with last_error preserved",
                             f"status={h.status} last_error={h.last_error!r}"))
    return checks


@scenario(
    id="h09.truncation.flag",
    title="shipped prereq #3: Extraction.truncated is set whenever the page text was cut (#78)",
    group="h09", tags=("read", "fail-loud", "truncation"),
    notes="contracts seeded from a truncated learn would DEFEND partial data — the flag makes that visible",
)
async def truncation_flag(ctx: Ctx):
    import dataclasses

    from ultracua.extract import Extraction, extract

    checks = []
    # SHIPPED API CONTRACT: the Extraction result type declares `truncated` — the signal H9's
    # contract seeding must consult so count-lower-bounds are never seeded from a partial read.
    checks.append(expect("truncated" in {f.name for f in dataclasses.fields(Extraction)},
                         "Extraction declares a `truncated` field (the seed-time trust signal)"))

    long_text = "item " * 200  # ~1000 normalized chars, far past the max_chars cut below

    # SHIPPED: data FOUND on a cut page still carries truncated=True — a possibly-short list is
    # flagged, never silently returned as complete (the count-drop failure mode Layer 1 guards).
    ex = await extract(_extract_router(42), "the report total", long_text, max_chars=100)
    checks.append(expect(ex.truncated is True and ex.found and ex.data == 42,
                         "found-on-a-cut-page keeps truncated=True (short list flagged, not silent)",
                         f"truncated={ex.truncated} found={ex.found} data={ex.data!r}"))

    # SHIPPED: an un-cut page reports truncated=False — the flag is a real signal, not a constant
    # (a flag that cried wolf would train callers to ignore it).
    ex2 = await extract(_extract_router(42), "the report total", "total: 42", max_chars=100)
    checks.append(expect(ex2.truncated is False,
                         "an un-cut page reports truncated=False (no false alarms)",
                         f"truncated={ex2.truncated}"))

    # SHIPPED: 'not found' on a cut page keeps truncated=True — a false negative (the answer lived
    # past the cut) is marked suspect instead of being trusted as a clean miss.
    ex3 = await extract(_not_found_router(), "the report total", long_text, max_chars=100)
    checks.append(expect(ex3.found is False and ex3.truncated is True,
                         "'not found' on a cut page keeps truncated=True (false negative marked suspect)",
                         f"found={ex3.found} truncated={ex3.truncated}"))
    return checks


@scenario(
    id="h09.value.same_shape_wrong_value",
    title="the gap: a same-shape WRONG value (42 -> 9999) replays undetected today",
    group="h09", aspirational=True, tags=("fail-loud", "contracts", "drift"),
    notes="ROADMAP admits it: 'shape-drift can't see wrong-but-present values' — H9's whole reason to exist",
)
async def same_shape_wrong_value(ctx: Ctx):
    import dataclasses

    from ultracua.cache import flow_key
    from ultracua.flows import FlowMeta, _load_meta, _meta_path, health, learn, replay

    checks = []
    fx = _report_fixture()
    with fx.serve() as base:
        cache = ctx.cache()
        spec = _report_spec(base, "wrongval")

        # Baseline: learn the flow with the honest value 42 (shape signature: number).
        learned = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)
        checks.append(expect(learned.cached and learned.found and learned.data == 42,
                             "learn caches the flow with the honest value 42 (baseline)",
                             f"cached={learned.cached} data={learned.data!r}"))
        if not learned.cached:
            return checks  # can't measure the wrong-value gap without a learned flow

        # The page's value 'changes' to 9999 between learn and replay — SAME shape ({'t':'number'}),
        # plausible magnitude, wrong value. The exact failure class Layer 1 contracts (range /
        # max-delta-vs-median) exist to catch.
        exc = None
        data = None
        try:
            data = await replay(spec, router=_extract_router(9999), cache=cache)
        except Exception as e:  # noqa: BLE001 — a future FlowQuarantineError would land here
            exc = e

        # SHIPPED (and future-proof): replay must either return the LIVE value verbatim or refuse
        # loudly — never a third thing (e.g. the stale learned 42, or a mangled value). This stays
        # true after H9 lands: a contract violation raising quarantine also satisfies it.
        checks.append(expect(exc is not None or data == 9999,
                             "replay returns the live value verbatim or refuses loudly (never stale/mangled)",
                             f"data={data!r} exc={exc!r}"))

        # THE GAP (aspirational): did ANY shipped signal flag the wrong value? No exception, health
        # still healthy, no quarantine marker in the meta sidecar -> flagged=False -> `missing`.
        # When Layer 1 lands, any one of these flips this check to pass.
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        meta = _load_meta(cache, key)
        h = health(spec, cache=cache)
        quarantined = bool(getattr(meta, "quarantined", False)
                           or getattr(meta, "quarantined_reason", None))
        flagged = exc is not None or quarantined or h.status not in ("healthy", "never-run")
        checks.append(expect(flagged,
                             "SOME signal flags the same-shape wrong value (contract violation / quarantine)",
                             f"exc=None status={h.status} quarantined={quarantined} — value contracts not built",
                             aspirational=True))

        # Layer 1 persistence (aspirational): a violation must outlive the process — a PERSISTED
        # quarantine flag in FlowMeta that future runs refuse until a human releases it.
        meta_fields = {f.name for f in dataclasses.fields(FlowMeta)}
        checks.append(expect(bool({"quarantined", "quarantined_reason"} & meta_fields),
                             "FlowMeta persists a quarantine flag (violations survive the process)",
                             f"no quarantine field in FlowMeta ({sorted(meta_fields)})",
                             aspirational=True))

        # Layer 1 history (aspirational, plan step 3): each run appends a per-field SKETCH record
        # (type / numeric value / list count — never raw values in meta) to <key>.history.jsonl,
        # the input for rolling-median / count-drop guards. No such sidecar today -> missing.
        hist = _meta_path(cache, key).with_name(f"{key}.history.jsonl")
        checks.append(expect(hist.exists(),
                             "replay appends a per-field sketch history record (<key>.history.jsonl)",
                             "no history sidecar written by replay", aspirational=True))
    return checks


@scenario(
    id="h09.contracts.layer1_surface",
    title="Layer 1 API surface: contracts module, FlowSpec.contracts, FlowQuarantineError",
    group="h09", aspirational=True, tags=("contracts", "quarantine"),
    notes="H9 plan steps 1-2: contracts.py + FlowSpec.contracts + FlowQuarantineError(FlowReplayError)",
)
async def contracts_layer1_surface(ctx: Ctx):
    import dataclasses

    import ultracua.flows as flows_mod
    from ultracua.flows import FlowMeta, FlowSpec

    checks = []

    # The pure key-less contracts module (plan step 1): FieldContract (type / format / min / max /
    # nullable / count-bounds / max-delta-pct) + check(data) -> violations. Not built -> missing.
    has_mod, mod = import_probe("ultracua.contracts")
    checks.append(expect(has_mod, "ultracua.contracts module exists (per-field value contracts)",
                         "no contracts module", aspirational=True))
    checks.append(expect(has_mod and hasattr(mod, "FieldContract"),
                         "contracts.FieldContract type exists (type/format/range/count/max-delta)",
                         "no FieldContract type", aspirational=True))

    # Developer-declared contracts ride on FlowSpec (plan step 1; `_only_known` spec loading gives
    # forward-compat for free once the field exists). Not built -> missing.
    spec_fields = {f.name for f in dataclasses.fields(FlowSpec)}
    checks.append(expect("contracts" in spec_fields,
                         "FlowSpec.contracts field exists (declare contracts on the spec)",
                         f"no contracts field on FlowSpec ({sorted(spec_fields)})", aspirational=True))

    # The loud refusal type (plan step 2): a contract violation must be a FlowReplayError SUBTYPE so
    # existing fail-loud callers catch it unchanged while new callers can tell quarantine apart.
    qerr = getattr(flows_mod, "FlowQuarantineError", None)
    checks.append(expect(qerr is not None and isinstance(qerr, type)
                         and issubclass(qerr, flows_mod.FlowReplayError),
                         "FlowQuarantineError exists and subclasses FlowReplayError",
                         "no FlowQuarantineError in ultracua.flows", aspirational=True))

    # Learn-time auto-seeding target (plan step 1: types/formats seeded where meta.shape is already
    # refreshed): the seeded contracts must live in the meta sidecar next to the shape. -> missing.
    meta_fields = {f.name for f in dataclasses.fields(FlowMeta)}
    checks.append(expect("contracts" in meta_fields,
                         "learn auto-seeds contracts into the meta sidecar (types/formats at learn)",
                         f"no contracts field in FlowMeta ({sorted(meta_fields)})", aspirational=True))
    return checks


@scenario(
    id="h09.quarantine.lifecycle_surface",
    title="quarantine lifecycle + async judge surface: release verb, quarantined status, flow audit",
    group="h09", aspirational=True, tags=("quarantine", "judge"),
    notes="H9 plan steps 2/4/5: human-only release; artifacts opt-in; judge is quarantine-forward ONLY",
)
async def quarantine_lifecycle_surface(ctx: Ctx):
    import dataclasses
    import inspect
    import re

    import ultracua.cli as cli_mod
    import ultracua.flows as flows_mod
    from ultracua.flows import FlowSpec

    checks = []

    # The ONLY way out of quarantine is an explicit human release (plan step 2) — an auto-clear
    # would be silent-wrong-data by the back door. Verb not built -> missing.
    checks.append(expect(callable(getattr(flows_mod, "release", None)),
                         "flows.release verb exists (human-only exit from quarantine)",
                         "no release verb in ultracua.flows", aspirational=True))

    # Coarse plumbing probe: ANY quarantine handling in the flows verb layer (the `quarantined`
    # health status, run_all skipping quarantined flows, the meta flag). Flips to pass when any of
    # the plan-step-2 plumbing lands; today the word does not appear in the module. -> missing.
    src = inspect.getsource(flows_mod)
    checks.append(expect("quarantined" in src,
                         "quarantine plumbing in the flows verb layer (health status / run_all skip)",
                         "no quarantine handling anywhere in ultracua.flows", aspirational=True))

    # The operator ergonomics half (plan step 2): without a one-command `flow release`, operators
    # habituate to workarounds and quarantine degrades to warn-and-pass by human behavior.
    csrc = inspect.getsource(cli_mod)
    checks.append(expect(re.search(r"add_parser\(\s*['\"]release['\"]", csrc) is not None,
                         "`flow release` CLI subcommand exists (one-command human override)",
                         "no release subcommand in ultracua.cli", aspirational=True))

    # Layer 2 (plan steps 4-5): opt-in artifact capture on the spec + the async `audit` judge verb
    # (budgeted, sampled, quarantine-forward ONLY — never on the replay data-release path, never
    # able to approve/clear). Neither surface exists -> missing.
    spec_fields = {f.name for f in dataclasses.fields(FlowSpec)}
    checks.append(expect("audit" in spec_fields,
                         "FlowSpec.audit opt-in artifact capture flag exists (bounded retention)",
                         f"no audit field on FlowSpec ({sorted(spec_fields)})", aspirational=True))
    checks.append(expect(callable(getattr(flows_mod, "audit", None)),
                         "flows.audit judge verb exists (async, quarantine-forward only)",
                         "no audit verb in ultracua.flows", aspirational=True))
    return checks
