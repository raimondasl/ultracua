"""H16 evals: fleet-telemetry training flywheel — episode exporter, distilled heal proposer, local tier.

ROADMAP H16 (highly experimental): a staged training program. (1) An opt-in, REDACTION-FIRST
episode/label exporter (`telemetry.py`) that emits JSONL as a byproduct of normal runs — snapshot
element records with bboxes + winning locator binds + verify/heal/barrier verdicts — hooked on the
existing `on_step`/verify seams, OFF the replay path, following the captioner's opt-in pattern.
(2) WinDOM-style distillation of a ~2B local grounding/locator-rank model (corpus augmented by a
crawl-harvest mode reusing the production capture JS byte-identically), deployed ONLY as a gated
pre-LLM heal proposer + a Phase-H fast authoring tier — never inside `locators.resolve` or
`_replay_step`. (3) Sandbox-only replay-reward RFT whose outputs enter the product exclusively
through an eval-gated promotion harness. Writes are untrainable by design.

Partial credit measured today (the seams/gates/substrates the flywheel is specified to ride on):
- `run_cached(on_step=...)` streams StepTraces (spans + meta) through learn AND replay — the
  exporter's hook point, exercised end to end at 0 LLM calls
- ground-truth label bits are observable on FlowReport: verify-by-replay verdict, wire-level
  write attribution, cached-provenance
- the heal-proposer safety gates ship: mutating-bail-first (no proposer ever consulted on a
  write step) + `state_changed` re-validation; `vision.GroundingProvider` + `MockGrounding`
  give the local model its interface and key-less test pattern
- snapshot element records carry DOM-derived bboxes; the capture JS is a single shared source
  (`_SPECOF_JS`/`_ROLEOF_JS`/`_ACCNAME_JS`) — the byte-identical harvest constraint
- the key-less mock tier + fast/strong Router shape + ACTION_TOOL strict schema are exactly
  where a pinned local model plugs in

Everything here is key-less: local Fixture pages, real headless Chromium, scripted providers, $0.
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

from evals.core import MISSING_EXC, Ctx, expect, import_probe, missing, ok, scenario
from evals.fixtures import Fixture, page


class _ClickTheLink:
    """Scripted key-less 'agent': click the first link, then declare done (the tests/ convention)."""

    def __init__(self) -> None:
        self._clicked = False

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        if not self._clicked:
            for el in obs.elements:
                if el.role == "link":
                    self._clicked = True
                    return Action(action="click", intent="open the answer page", ref=el.ref), None
        return Action(action="done", intent="done"), None


class _TypeSecretThenDone:
    """Scripted agent that types a sentinel 'secret' into the first text field, then done —
    produces a cached `type` step whose text is the exact field an episode export must redact."""

    SECRET = "hunter2-fleet-secret"

    def __init__(self) -> None:
        self._typed = False

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        if not self._typed:
            for el in obs.elements:
                if el.role == "textbox" or el.tag == "input":
                    self._typed = True
                    return Action(action="type", intent="enter the search query",
                                  ref=el.ref, text=self.SECRET), None
        return Action(action="done", intent="done"), None


class _CountingProvider:
    """Heal-oracle: records whether the heal path consulted it at all (it must NOT on a write)."""

    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        self.calls += 1
        return Action(action="done", intent="done"), None


def _two_pages() -> Fixture:
    return Fixture({
        "/": page('<a href="/answer">open the daily report</a>'),
        "/answer": page('<h1>Report</h1><p id="total">total: 42</p>'),
    })


@scenario(
    id="h16.exporter.on_step_episode_seam",
    title="opt-in episode exporter (telemetry.py) is missing; its on_step seam streams StepTraces today",
    group="h16", aspirational=True, tags=("telemetry", "exporter", "on_step"),
    notes="H16 plan step 1: JSONL episodes as a byproduct of normal runs, off the replay path",
)
async def exporter_on_step_episode_seam(ctx: Ctx):
    """The exporter turns every run into training labels by collecting StepTraces + verdicts on
    the existing seams. The module and its run_cached opt-in have no surface yet (`missing`);
    the seam itself is exercised end to end: a collector passed as on_step receives per-step
    decision records at learn time and per-step ok-verdict labels at replay time, at 0 LLM calls
    — proof the exporter can be a pure byproduct with zero effect on the replay path."""
    from ultracua.flow import run_cached
    from ultracua.timing import StepTrace

    checks = []
    # THE GAP (plan step 1): the exporter module itself — hooking on_step/verify/heal/barrier and
    # emitting JSONL episodes — does not exist yet.
    ok_tel, tel = import_probe("ultracua.telemetry")
    checks.append(expect(ok_tel, "ultracua.telemetry imports (opt-in episode/label exporter)",
                         f"{type(tel).__name__}", aspirational=True))
    if ok_tel:
        has_exporter = any(callable(getattr(tel, n, None)) for n in
                           ("EpisodeExporter", "exporter", "export_episodes", "episode_exporter"))
        checks.append(expect(has_exporter, "telemetry exposes an episode exporter (JSONL sink)",
                             aspirational=True))
    else:
        checks.append(missing("telemetry exposes an episode exporter (JSONL sink)", "module absent"))
    # The opt-in wiring: run_cached accepting an exporter/sink (captioner-style: explicit, never
    # a surprise) has no parameter yet.
    params = set(inspect.signature(run_cached).parameters)
    checks.append(expect(bool(params & {"telemetry", "exporter", "export_episodes",
                                        "episode_sink", "episodes"}),
                         "run_cached accepts an episode-export opt-in",
                         f"params={sorted(params)}", aspirational=True))

    # Partial credit, exercised: the seam the exporter hooks. Learn streams a StepTrace per
    # decision through on_step, each carrying the action record (meta['action']) + timing spans —
    # the raw material of one episode step.
    fx = _two_pages()
    with fx.serve() as base:
        cache = ctx.cache()
        goal = "open the daily report page"
        learn_got: list = []
        learned = await run_cached(base + "/", goal, _ClickTheLink(), cache, mode="learn",
                                   headless=True, on_step=learn_got.append)
        seam_ok = (learned.success and len(learn_got) >= 2
                   and all(isinstance(t, StepTrace) and "action" in t.meta for t in learn_got))
        checks.append(expect(seam_ok,
                             "learn streams StepTraces (decision record + meta) through on_step",
                             f"success={learned.success} collected={len(learn_got)}"))
        checks.append(expect(any(t.spans and t.total_ms > 0 for t in learn_got),
                             "collected traces carry named timing spans (episode step timings)",
                             f"spans={[len(t.spans) for t in learn_got]}"))
        # Replay: the same seam yields per-step ok-verdict LABELS at zero LLM cost — the exporter
        # can harvest ground-truth bits from the fleet's normal replays without touching the
        # 0-LLM-replay inviolable.
        replay_got: list = []
        replayed = await run_cached(base + "/", goal, None, cache, mode="replay",
                                    headless=True, on_step=replay_got.append)
        label_ok = (replayed.success and replayed.llm_calls == 0 and len(replay_got) >= 1
                    and all("ok" in t.meta for t in replay_got))
        checks.append(expect(label_ok,
                             "replay streams per-step ok-verdict labels via on_step at 0 LLM calls",
                             f"success={replayed.success} llm={replayed.llm_calls} "
                             f"collected={len(replay_got)}"))
    return checks


@scenario(
    id="h16.labels.ground_truth_success_bits",
    title="ground-truth label bits observable on FlowReport: verify verdict + write attribution",
    group="h16", aspirational=True, tags=("telemetry", "labels", "verify"),
    notes="H16 unlock: every verified run yields success bits + wire-level write attribution",
)
async def labels_ground_truth_success_bits(ctx: Ctx):
    """The corpus's value claim is 'training labels with ground-truth success bits and wire-level
    write attribution'. The three label bits the exporter would consume are all observable on a
    shipped FlowReport today (partial credit, exercised with verify_replay=True on a fixture);
    the settings-level opt-in that would switch exporting on does not exist yet."""
    from ultracua.config import settings
    from ultracua.flow import run_cached

    checks = []
    fx = _two_pages()
    with fx.serve() as base:
        report = await run_cached(base + "/", "open the daily report page", _ClickTheLink(),
                                  ctx.cache(), mode="learn", headless=True, verify_replay=True)
        # Success bit: the flow reproduced 0-LLM on a FRESH session before caching — the
        # strongest per-episode label the exporter gets for free.
        checks.append(expect(report.success and report.extra.get("verify") == "passed",
                             "verify-by-replay verdict is observable (ground-truth success bit)",
                             f"success={report.success} verify={report.extra.get('verify')!r}"))
        # Write-attribution bit: wire-truth (did a non-idempotent request fire?), not a guess
        # from the recipe's mutating flags — this is what keeps write episodes out of training.
        checks.append(expect(report.extra.get("performed_write") is False,
                             "wire-level write attribution bit ships (performed_write on the report)",
                             f"performed_write={report.extra.get('performed_write')!r}"))
        # Provenance bit: THIS attempt produced the cached flow (vs replaying a pre-existing one)
        # — an episode needs to know whether it is a discovery or a replay sample.
        checks.append(expect(report.extra.get("cached") is True,
                             "cached-provenance bit ships (this attempt cached the flow)",
                             f"cached={report.extra.get('cached')!r}"))
        # Fixture oracle anchoring the attribution bit: the server really saw no write.
        checks.append(expect(not fx.writes, "fixture oracle: no write reached the server",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
    # THE GAP: the captioner-style opt-in switch (env/config) for episode export — absent means
    # exporting can't even be turned on yet.
    names = {n for n in dir(settings) if not n.startswith("_")}
    checks.append(expect(bool(names & {"telemetry", "telemetry_opt_in", "export_episodes",
                                       "episode_dir", "telemetry_dir"}),
                         "captioner-style opt-in setting for episode export exists",
                         f"no telemetry/export field on Settings", aspirational=True))
    return checks


@scenario(
    id="h16.redaction.typed_value_export",
    title="redaction-first export: typed values persist in plaintext locally; no redaction pass exists",
    group="h16", aspirational=True, tags=("telemetry", "redaction", "privacy"),
    notes="H16 risk: one export leak destroys the trust the product sells — redaction is prereq #1",
)
async def redaction_typed_value_export(ctx: Ctx):
    """CachedStep.text persists the literal typed value (replay needs it — correct locally), so a
    naive export would leak passwords/PII. This scenario learns a real type-flow on a fixture and
    measures BOTH sides: the plaintext reality that makes redaction load-bearing (shipped, pass)
    and the missing redaction surface (aspirational). The opt-in template the exporter is
    specified to follow (record's caption param: explicit, default-off) ships."""
    from ultracua.cache import flow_key
    from ultracua.flow import run_cached
    from ultracua.flows import record

    checks = []
    fx = Fixture({"/": page('<label>search <input type="text" name="q"></label>')})
    with fx.serve() as base:
        cache = ctx.cache()
        goal = "search for the quarterly numbers"
        learned = await run_cached(base + "/", goal, _TypeSecretThenDone(), cache,
                                   mode="learn", headless=True)
        checks.append(expect(learned.success, "learn captures a type step on the fixture",
                             f"note={learned.note!r}"))
        # Shipped replay substrate: the typed value IS the cached step's text — exactly the field
        # a redaction-first exporter must strip (the accessible name + goal suffice as the label).
        flow = cache.get(flow_key(goal, base + "/", "default"))
        typed = [s for s in (flow.steps if flow else []) if s.action == "type"]
        checks.append(expect(bool(typed) and typed[0].text == _TypeSecretThenDone.SECRET,
                             "CachedStep persists the typed value (the exact field an export must redact)",
                             f"typed_steps={len(typed)}"))
        # Grounded risk measurement: the value sits in PLAINTEXT in the on-disk cache file. Fine
        # for local replay; untenable in any episode that leaves the machine.
        raw = "".join(p.read_text(encoding="utf-8")
                      for p in (ctx.tmp / "flows").rglob("*.json"))
        checks.append(expect(_TypeSecretThenDone.SECRET in raw,
                             "typed value sits in plaintext in the local cache (why redaction is load-bearing)"))
    # THE GAP: a capture-time redaction pass over episodes (type-step text, storage_state,
    # LoginSpec env values) has no surface anywhere.
    ok_tel, tel = import_probe("ultracua.telemetry")
    if ok_tel:
        has_redact = any(callable(getattr(tel, n, None)) for n in
                         ("redact", "redact_episode", "redact_step", "redactor"))
        checks.append(expect(has_redact, "telemetry exposes a capture-time redaction pass",
                             aspirational=True))
    else:
        checks.append(missing("telemetry exposes a capture-time redaction pass", "module absent"))
    # Partial credit: the opt-in TEMPLATE the exporter must follow ships — record()'s captioner
    # is an explicit callable defaulting to None (key-less capture never makes a surprise LLM
    # call; the same shape keeps export opt-in, never a surprise upload).
    cap = inspect.signature(record).parameters.get("caption")
    checks.append(expect(cap is not None and cap.default is None,
                         "the captioner opt-in pattern ships (record(caption=None) — explicit, default-off)",
                         f"param={cap}"))
    return checks


@scenario(
    id="h16.heal.local_proposer_gates",
    title="gated pre-LLM heal proposer: the safety gates + interface ship; the proposer seam doesn't",
    group="h16", aspirational=True, tags=("heal", "grounding", "write-safety"),
    notes="H16 plan step 3: proposer behind mutating-bail + unique re-bind + state_changed gates",
)
async def heal_local_proposer_gates(ctx: Ctx):
    """A distilled local model may propose heal candidates ONLY behind the shipped gates: the
    mutating bail stays first (a re-click could double-submit), and every proposal must survive
    the state_changed re-validation or be discarded. Both gates are exercised here key-lessly;
    the proposer seam itself (a pre-LLM branch in _maybe_heal) has no surface yet."""
    from ultracua import vision
    from ultracua.cache import CachedStep
    from ultracua.flow import _maybe_heal, run_cached
    from ultracua.timing import StepTrace
    from ultracua.types import Observation
    from ultracua.verify import state_changed

    checks = []
    # Gate 1 (exercised): a MUTATING step bails out of heal BEFORE consulting any provider —
    # the counting oracle proves no proposer (local or frontier) is ever asked about a write.
    counter = _CountingProvider()
    write_step = CachedStep(intent="submit the order", action="click", mutating=True)
    healed, note, _used = await _maybe_heal(None, write_step, counter, StepTrace(index=0),
                                            "submit the order", "locator drifted")
    checks.append(expect(healed is False and counter.calls == 0,
                         "mutating step bails BEFORE any heal proposer is consulted (gate 1)",
                         f"healed={healed} proposer_calls={counter.calls}"))
    checks.append(expect("not healed" in note,
                         "the write-heal refusal is fail-loud (note names it)", f"note={note!r}"))
    # Gate 3 (exercised): the wrong-bind rejection primitive — a proposal whose click changes
    # nothing must be discarded, never persisted into the cache as a 'healed' locator.
    a = Observation(url="http://x/", title="t", elements=[], fingerprint="f1")
    same = Observation(url="http://x/", title="t", elements=[], fingerprint="f1")
    diff = Observation(url="http://x/", title="t", elements=[], fingerprint="f2")
    checks.append(expect(not state_changed(a, same) and state_changed(a, diff),
                         "state_changed re-validation primitive ships (no-effect proposals rejectable)"))
    # Interface (shipped): the local grounding model's protocol + its key-less test double + a
    # learn-time entry point (run_cached(grounding=...)) all exist — the proposer reuses them.
    params = set(inspect.signature(run_cached).parameters)
    checks.append(expect(hasattr(vision, "GroundingProvider") and hasattr(vision, "MockGrounding")
                         and "grounding" in params,
                         "GroundingProvider protocol + MockGrounding + learn-time grounding entry ship",
                         f"run_cached params={sorted(params)}"))
    # THE GAP: the pre-LLM proposer branch itself — neither run_cached nor _maybe_heal accepts a
    # local proposer today (it must be a SEPARATE slot from the frontier heal provider so the
    # cheap tier is tried first and the model never enters resolve/_replay_step).
    heal_params = set(inspect.signature(_maybe_heal).parameters)
    checks.append(expect(bool((params | heal_params) & {"heal_proposer", "proposer",
                                                        "heal_grounding", "local_proposer"}),
                         "a pre-LLM local heal-proposer seam exists",
                         f"heal params={sorted(heal_params)}", aspirational=True))
    return checks


@scenario(
    id="h16.corpus.capture_js_and_local_tier",
    title="crawl-harvest corpus + pinned local authoring tier: label substrates ship, both surfaces missing",
    group="h16", aspirational=True, tags=("corpus", "grounding", "local-model"),
    notes="H16 plan steps 2 + 4: WinDOM-replication harvest; local LLMClient via the standard seams",
)
async def corpus_capture_js_and_local_tier(ctx: Ctx):
    """Distillation needs (a) a diverse harvested corpus whose labels are byte-identical with
    production capture and (b) a pinned local model plugged in as a standard LLMClient fast tier.
    Neither surface exists ('missing'); every substrate does: bbox-carrying element records on
    real Chromium, a single shared capture-JS source, the key-less mock tier + fast/strong Router
    shape, and the ACTION_TOOL strict schema a small model needs for constrained decoding."""
    from ultracua import locators, snapshot
    from ultracua.browser import BrowserSession
    from ultracua.llm import build_client
    from ultracua.providers import ACTION_TOOL, build_router

    checks = []
    # Partial credit, exercised: snapshot element records carry DOM-derived bboxes + roles +
    # accessible names — the exact (screenshot-free) grounding label WinDOM distilled 2B models
    # from. This is the per-run label the exporter/harvester both emit.
    fx = Fixture({"/": page('<a href="/x">daily report</a><button>refresh totals</button>')})
    with fx.serve() as base:
        session = await BrowserSession(headless=True).start()
        try:
            await session.goto(base + "/")
            obs = await session.snapshot()
            boxed = [el for el in obs.elements if el.bbox and len(el.bbox) == 4]
            checks.append(expect(len(boxed) >= 2 and all(el.role and el.name for el in boxed),
                                 "element records carry bbox + role + name (WinDOM-style grounding labels)",
                                 f"elements={len(obs.elements)} with_bbox={len(boxed)}"))
        finally:
            await session.close()
    # Partial credit: the capture JS is a single shared source — locators imports snapshot's
    # _ROLEOF_JS/_ACCNAME_JS and concatenates _SPECOF_JS — so a harvest script CAN reuse it
    # byte-identically (the constraint that keeps harvested labels distribution-matched).
    checks.append(expect(isinstance(getattr(locators, "_SPECOF_JS", None), str)
                         and isinstance(getattr(snapshot, "_ROLEOF_JS", None), str)
                         and isinstance(getattr(snapshot, "_ACCNAME_JS", None), str),
                         "capture JS is one shared source (_SPECOF_JS + _ROLEOF_JS/_ACCNAME_JS)"))
    # THE GAP (plan step 2): the crawl-harvest corpus mode — neither an ultracua.harvest module
    # nor a benchmarks/ harvest script exists yet.
    ok_harvest, _ = import_probe("ultracua.harvest")
    bench_dir = Path(__file__).resolve().parents[2] / "benchmarks"
    harvest_scripts = list(bench_dir.glob("*harvest*")) if bench_dir.is_dir() else []
    checks.append(expect(ok_harvest or bool(harvest_scripts),
                         "a crawl-harvest corpus mode exists (module or benchmarks script)",
                         f"benchmarks_glob={[p.name for p in harvest_scripts]}", aspirational=True))
    # THE GAP (plan step 4): a pinned local-model backend behind the standard build_client seam.
    # build_client raises ValueError for an unknown backend — for this probe that IS the
    # not-built-yet signal (ValueError isn't in MISSING_EXC, so handle it here).
    try:
        client = build_client("local")
        checks.append(ok("build_client('local') returns a pinned local-model adapter",
                         f"{type(client).__name__}"))
    except (ValueError, *MISSING_EXC):
        checks.append(missing("build_client('local') returns a pinned local-model adapter",
                              "no 'local' backend in build_client"))
    # Partial credit: the tier the local model slots into ships key-lessly — a mock LLMClient
    # builds with no key, and build_router yields the fast/strong Router shape (escalation to
    # strong stays intact when a local fast tier is swapped in).
    router = build_router("mock")
    checks.append(expect(build_client("mock") is not None
                         and getattr(router, "fast", None) is not None
                         and getattr(router, "strong", None) is not None,
                         "key-less mock tier + fast/strong Router shape ship (the local tier's slot)"))
    # Partial credit: constrained decoding substrate — ACTION_TOOL is strict with
    # additionalProperties:false, so a small local model's emissions are schema-guaranteed.
    checks.append(expect(ACTION_TOOL.get("strict") is True
                         and ACTION_TOOL["input_schema"].get("additionalProperties") is False,
                         "ACTION_TOOL strict schema ships (constrained decoding for a small model)"))
    return checks
