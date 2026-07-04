"""H10 evals: Drift-Watch — monitoring-as-product over the fleet verbs (ROADMAP H10).

The horizon: a read-only `flows.watch` verb that replays a flow, appends the typed extraction
to a per-flow history store (`ultracua.history`), semantically diffs it against the previous
value (`ultracua.diffing` — scalar delta, key add/remove, list membership; an INDETERMINATE
comparison must NEVER diff as "no change" — the monitoring-specific form of never-silently-
wrong), and emits change/drift/heal events to alert channels (`ultracua.alerts`); plus
`heal_policy="report"` so a successful heal surfaces as a change event instead of silently
persisting. Guardrails: watch hard-refuses mutating specs (a scheduler re-fires writes) and
enforces a per-flow min-interval (cron stays the scheduler — the deliberate no-scheduler stance).

Today the watch layer itself is unbuilt — those checks report `missing`. What already PASSES is
the substrate the H10 plan rides on: the shipped fleet verbs (`canary` catches entry-page rot
loudly and read-only, `health` exposes dashboard-ready statuses, a failed replay lands in the
run history), the truncation flag at its source (Extraction.truncated — prereq #3, done), the
locked atomic sidecar-write pattern history.py will reuse, and the run-all webhook alert seed.
Key-less: local Fixture pages + ScriptedProvider, real headless Chromium.
"""

from __future__ import annotations

from evals.core import Ctx, expect, fail, import_probe, missing, ok, probe, scenario
from evals.fixtures import Fixture, page

_GOAL = "open the daily metrics page"
# The scripted "teacher": click the portal's metrics link, then declare done — the flow a
# monitor would re-check on a schedule (navigate/confirm shape = genuinely 0 LLM per check).
_STEPS = [
    {"action": "click", "role": "link", "name": "daily metrics", "intent": "open the daily metrics"},
    {"action": "done", "intent": "metrics page reached"},
]


def _metrics_fixture() -> Fixture:
    """A minimal 'vendor portal': an entry page linking to the metrics page a monitor watches."""
    return Fixture({
        "/": page('<h1>Portal</h1><a href="/metrics">daily metrics</a>'),
        "/metrics": page('<h1>Metrics</h1><p id="price">price: 129</p>'),
    })


async def _learn(spec, cache):
    """Key-less learn under the FLOW key (scope=spec.scope) so the fleet verbs — canary, health,
    replay — all find the cached flow exactly as `flows.learn` would have stored it."""
    from ultracua.flow import run_cached
    from ultracua.providers.scripted import ScriptedProvider

    return await run_cached(spec.start_url, spec.goal, ScriptedProvider(list(_STEPS)), cache,
                            mode="learn", headless=True, scope=spec.scope)


@scenario(
    id="h10.watch.canary_entry_rot",
    title="the shipped canary: entry-page rot and a dead site flip to stale LOUDLY, read-only",
    group="h10", tags=("watch", "canary", "fail-loud"),
)
async def canary_entry_rot(ctx: Ctx):
    """PARTIAL CREDIT (shipped): the cheap early-warning probe a Drift-Watch schedule would run
    between full checks. The monitoring stakes: a canary that shrugs at rot is a monitor that
    silently stopped watching."""
    from ultracua.flows import FlowSpec, canary, health

    checks = []
    fx = _metrics_fixture()
    with fx.serve() as base:
        cache = ctx.cache()
        spec = FlowSpec(name="watchme", start_url=base + "/", goal=_GOAL, headless=True)
        # A monitor must distinguish "never learned" from rot — not-learned is its own status,
        # so a misconfigured watch entry can't masquerade as a healthy one.
        pre = await canary(spec, cache=cache)
        checks.append(expect(pre.status == "not-learned",
                             "canary on an unlearned flow reports not-learned (not fresh, not stale)",
                             f"status={pre.status} detail={pre.detail!r}"))
        learned = await _learn(spec, cache)
        if not learned.success:
            # Shipped learn machinery broke — a real regression, not a horizon gap.
            checks.append(fail("learn the monitored flow (baseline)", f"note={learned.note!r}"))
            return checks
        checks.append(ok("learn the monitored flow (baseline)"))
        # Healthy baseline: the entry control still resolves -> fresh (no alert fatigue).
        fresh = await canary(spec, cache=cache)
        checks.append(expect(fresh.status == "fresh", "canary on the live entry page reports fresh",
                             f"status={fresh.status} detail={fresh.detail!r}"))
        # ENTRY ROT — the day-one drift signal: the portal redesign removed the metrics link.
        # The canary must flip to stale the day the site changes, not at the 3am full run.
        fx.pages["/"] = page('<h1>Portal v2</h1><p>metrics moved to the new dashboard</p>')
        rotted = await canary(spec, cache=cache)
        checks.append(expect(rotted.status == "stale",
                             "entry-page rot flips the canary to stale (fails loud, day one)",
                             f"status={rotted.status} detail={rotted.detail!r}"))
        # A monitoring probe must be side-effect-free: nothing on the wire but GETs, and no
        # health record (the canary docstring's promise) — probes must not pollute run history.
        h = health(spec, cache=cache)
        checks.append(expect(not fx.writes and h.runs == 0,
                             "canary probes are READ-ONLY: no writes on the wire, no health record",
                             f"writes={[(w.method, w.path) for w in fx.writes]} runs={h.runs}"))
    # THE SITE DIES (server gone): an unreachable start page is staleness, never "unchanged" —
    # a watch built on this canary can't silently stop watching when the portal goes down.
    dead = await canary(spec, cache=cache)
    checks.append(expect(dead.status == "stale",
                         "a dead start page reports stale (unreachable is loud, never 'no change')",
                         f"status={dead.status} detail={dead.detail!r}"))
    return checks


@scenario(
    id="h10.watch.health_lifecycle",
    title="the shipped health verb: the dashboard-ready status arc a drift dashboard would read",
    group="h10", tags=("watch", "health"),
)
async def health_lifecycle(ctx: Ctx):
    """PARTIAL CREDIT (shipped): the full status arc — not-learned -> never-run -> healthy ->
    stale (freshness window) -> failing — driven by real key-less replays. This is the state
    store the H10 dashboard phase reads; the arc must be truthful before a watch layer can be."""
    import asyncio

    from ultracua.flows import FlowReplayError, FlowSpec, health, replay

    checks = []
    fx = _metrics_fixture()
    with fx.serve() as base:
        cache = ctx.cache()
        spec = FlowSpec(name="lifecycle", start_url=base + "/", goal=_GOAL, headless=True)
        checks.append(expect(health(spec, cache=cache).status == "not-learned",
                             "an unlearned flow's health is not-learned",
                             f"status={health(spec, cache=cache).status}"))
        learned = await _learn(spec, cache)
        if not learned.success:
            checks.append(fail("learn the monitored flow (baseline)", f"note={learned.note!r}"))
            return checks
        h = health(spec, cache=cache)
        checks.append(expect(h.status == "never-run" and h.cached,
                             "a learned-but-never-replayed flow is never-run (cached=True)",
                             f"status={h.status} cached={h.cached}"))
        # The per-check economics H10 leans on: a navigate/confirm flow replays through the verb
        # layer with NO provider, NO router, NO key — the code path never builds an LLM client —
        # and the success is recorded into the health arc a dashboard reads.
        try:
            await replay(spec, cache=cache)
            h = health(spec, cache=cache)
            checks.append(expect(h.status == "healthy" and h.runs == 1 and h.successes == 1,
                                 "a key-less (0-LLM) replay is recorded: healthy, runs=1, successes=1",
                                 f"status={h.status} runs={h.runs} successes={h.successes}"))
        except Exception as exc:  # noqa: BLE001 — a shipped key-less replay must not raise here
            checks.append(fail("a key-less (0-LLM) replay is recorded: healthy, runs=1, successes=1",
                               f"{type(exc).__name__}: {exc}"))
        # The freshness window: monitoring whose last success is too old must surface as STALE —
        # the shipped seed for "the monitor silently stopped watching" (MFA decay, dead cron).
        await asyncio.sleep(0.05)
        h = health(spec, cache=cache, stale_after=0.01)
        checks.append(expect(h.status == "stale",
                             "a success older than stale_after reports stale (silent-stop guard)",
                             f"status={h.status}"))
        # DRIFT: the entry link disappears. The replay must fail LOUD (FlowReplayError, never a
        # silent success/wrong data) and the failure must land in health for the fleet view.
        fx.pages["/"] = page('<h1>Portal v2</h1><p>metrics moved to the new dashboard</p>')
        try:
            got = await replay(spec, cache=cache)
            checks.append(fail("a drifted replay raises FlowReplayError (never silent)",
                               f"replay returned {got!r} instead of raising"))
        except FlowReplayError:
            checks.append(ok("a drifted replay raises FlowReplayError (never silent)"))
        except Exception as exc:  # noqa: BLE001 — wrong exception type is still a shipped-behavior bug
            checks.append(fail("a drifted replay raises FlowReplayError (never silent)",
                               f"raised {type(exc).__name__} instead: {exc}"))
        h = health(spec, cache=cache)
        checks.append(expect(h.status == "failing" and h.consecutive_failures == 1 and bool(h.last_error),
                             "the drift failure is recorded: failing, streak=1, last_error set",
                             f"status={h.status} streak={h.consecutive_failures} err={h.last_error!r}"))
    return checks


@scenario(
    id="h10.watch.history_store",
    title="per-flow extraction history: the JSONL store is unbuilt; its flag + write pattern are shipped",
    group="h10", aspirational=True, tags=("watch", "history", "horizon"),
)
async def history_store(ctx: Ctx):
    """H10 plan step 1: `ultracua.history` — an append-only per-flow JSONL recording ts, value,
    shape hash, healed steps, replay ms, and a truncation flag. The store is missing; both of
    its ingredients pass: the truncation flag exists at its source (prereq #3) and the locked
    atomic sidecar-write pattern the plan reuses works."""
    import dataclasses

    from ultracua.extract import Extraction
    from ultracua.flows import _load_meta, _update_meta

    checks = []
    # PARTIAL CREDIT: the truncation flag the history record must persist has a real producer —
    # extract.py reports a cut read on the Extraction itself, never only in a log line. Without
    # this, a change below the truncation line would diff as "unchanged" (the forbidden silent
    # false-negative); behavioral coverage lives in core.resilience.extract_truncation_flag.
    checks.append(expect("truncated" in {f.name for f in dataclasses.fields(Extraction)},
                         "Extraction carries the truncated flag the history record will persist"))
    # PARTIAL CREDIT: the write template the plan names for history appends — _update_meta's
    # lock -> load -> mutate -> atomic-replace — round-trips on a fresh cache root (ctx.tmp).
    cache = ctx.cache()
    _update_meta(cache, "cafeh10a", lambda m: setattr(m, "runs", 3))
    checks.append(expect(_load_meta(cache, "cafeh10a").runs == 3,
                         "the locked atomic sidecar-write pattern (history's write template) round-trips",
                         f"runs={_load_meta(cache, 'cafeh10a').runs}"))
    # HORIZON: the store itself.
    ok_mod, mod = import_probe("ultracua.history")
    if not ok_mod:
        checks.append(missing("ultracua.history module exists (append-only per-flow JSONL store)",
                              f"{type(mod).__name__}: {mod}"))
        checks.append(missing("history records carry ts/value/shape/truncated for the differ",
                              "unverifiable until the history store exists"))
    else:
        checks.append(ok("ultracua.history module exists (append-only per-flow JSONL store)"))
        names = {n for n in dir(mod) if not n.startswith("__")}
        # The differ consumes consecutive records, so the store must expose an append/read surface.
        checks.append(expect(bool({"append", "record", "read", "History", "HistoryStore"} & names),
                             "history records carry ts/value/shape/truncated for the differ",
                             f"no append/read entrypoint found among {sorted(names)}", aspirational=True))
    return checks


@scenario(
    id="h10.watch.semantic_diff",
    title="typed semantic diff: unbuilt; 'indeterminate never equals no-change' is THE invariant",
    group="h10", aspirational=True, tags=("watch", "diff", "horizon"),
)
async def semantic_diff(ctx: Ctx):
    """H10 plan step 2: `ultracua.diffing` — typed diff over consecutive history records with
    value normalization. All missing today. The one shipped seed: the shape signature already
    detects the 'field appeared/disappeared' diff class the key add/remove event refines."""
    from ultracua.flows import _shape_matches, _shape_of

    checks = []
    # PARTIAL CREDIT: a dict key ADD is detectable today via the shipped shape signature — the
    # primitive a "field appeared" ChangeEvent will be built on (key removal is symmetric).
    checks.append(expect(not _shape_matches(_shape_of({"price": 129}),
                                            _shape_of({"price": 129, "sale": True})),
                         "a dict key add/remove is detectable today via the shipped shape signature"))
    ok_mod, mod = import_probe("ultracua.diffing")
    if not ok_mod:
        checks.append(missing("ultracua.diffing module exists (typed semantic diff)",
                              f"{type(mod).__name__}: {mod}"))
        # The headline event type: a scalar delta ("price 129 -> 149") as a TYPED change.
        checks.append(missing("a scalar delta becomes a typed change event (price 129 -> 149)",
                              "unverifiable until diffing exists"))
        # Normalization is load-bearing, not cosmetic: extraction nondeterminism ("$129.00" vs
        # "129") must not phantom-diff, or alert fatigue destroys the fail-loud signal.
        checks.append(missing("normalization kills phantom diffs ('$129.00' vs '129')",
                              "unverifiable until diffing exists"))
        # THE monitoring inviolable: truncated/shape-drifted/indeterminate comparisons must emit
        # 'indeterminate', never 'no change' — a false 'unchanged' IS silent wrong data.
        checks.append(missing("an INDETERMINATE (truncated) comparison never diffs as 'no change'",
                              "unverifiable until diffing exists"))
        return checks
    checks.append(ok("ultracua.diffing module exists (typed semantic diff)"))
    fn = getattr(mod, "diff", None) or getattr(mod, "semantic_diff", None)
    if fn is None:
        checks.append(missing("a scalar delta becomes a typed change event (price 129 -> 149)",
                              "diffing module has no diff/semantic_diff entrypoint"))
        checks.append(missing("normalization kills phantom diffs ('$129.00' vs '129')",
                              "diffing module has no diff/semantic_diff entrypoint"))
        checks.append(missing("an INDETERMINATE (truncated) comparison never diffs as 'no change'",
                              "diffing module has no diff/semantic_diff entrypoint"))
        return checks
    # The module shipped: exercise it via probe so an unexpected signature stays `missing`.
    st, out = await probe(fn, 129, 149)
    checks.append(expect(st == "ok" and bool(out),
                         "a scalar delta becomes a typed change event (price 129 -> 149)",
                         f"status={st} out={out!r}", aspirational=True))
    st2, out2 = await probe(fn, "$129.00", "129")
    checks.append(expect(st2 == "ok" and not out2,
                         "normalization kills phantom diffs ('$129.00' vs '129')",
                         f"status={st2} out={out2!r}", aspirational=True))
    # The indeterminate case needs the history-record shape (truncation flag riding alongside the
    # value); re-key this check to the real record type once the store + differ land together.
    checks.append(missing("an INDETERMINATE (truncated) comparison never diffs as 'no change'",
                          "diff entrypoint exists but the truncated-record shape is undefined — "
                          "deepen this eval when history records land"))
    return checks


@scenario(
    id="h10.watch.watch_verb",
    title="flows.watch + guardrails: unbuilt; the fleet verb layer and alert seed it extends pass",
    group="h10", aspirational=True, tags=("watch", "writes", "horizon"),
)
async def watch_verb(ctx: Ctx):
    """H10 plan steps 2/4/5: the watch verb beside run_all/canary, `ultracua.alerts` channels,
    the mutating-spec hard refusal, and the per-flow min-interval guard. Today only the shipped
    substrate passes; the verb and both guardrails are missing."""
    import ultracua.cli as cli
    from ultracua import flows

    checks = []
    # PARTIAL CREDIT: the fleet verb layer watch slots into is shipped — watch is additive at
    # this seam (replay -> history -> diff -> events), zero replay-path changes needed.
    checks.append(expect(all(callable(getattr(flows, n, None))
                             for n in ("run_all", "canary", "canary_all", "health")),
                         "the fleet verb layer watch extends (run_all/canary/canary_all/health) is shipped"))
    # PARTIAL CREDIT: the alert seed alerts.py generalizes — run-all's webhook poster (the
    # `--alert-webhook` path) exists today, so change events have a delivery pattern to copy.
    checks.append(expect(callable(getattr(cli, "_post_alert", None)),
                         "the run-all webhook alert seed (cli._post_alert) is shipped"))
    # HORIZON: generalized channels (webhook/Slack/email; config via env, never secrets on disk).
    ok_alerts, mod = import_probe("ultracua.alerts")
    checks.append(expect(ok_alerts, "ultracua.alerts module exists (webhook/Slack/email channels)",
                         f"{type(mod).__name__}: {mod}", aspirational=True))
    # HORIZON: the verb itself.
    st, watch = await probe(getattr, flows, "watch")
    if st != "ok":
        checks.append(missing("flows.watch verb exists beside run_all/canary",
                              f"{type(watch).__name__}: {watch}"))
        # Write-safety amplification: a scheduler re-runs flows indefinitely, so a write flow in a
        # watch schedule means REPEATED write firing — the refusal must be structural (no override).
        checks.append(missing("watch HARD-REFUSES a mutating spec (a scheduler re-fires writes)",
                              "unverifiable until watch exists"))
        # Account-flagging guard: an over-eager cron entry must refuse, not hammer a logged-in
        # account (vanilla Chromium, no stealth, by design — min-interval is mandatory).
        checks.append(missing("watch enforces a per-flow min-interval (refuses an over-eager cron)",
                              "unverifiable until watch exists"))
        return checks
    checks.append(ok("flows.watch verb exists beside run_all/canary"))
    # The verb shipped: the mutating refusal is now WRITE SAFETY, not a horizon nicety. The spec
    # is unlearned and its URL dead, so the ONLY acceptable outcome is an exception — if watch
    # RETURNS on a mutate spec it silently accepted a write into a re-firing schedule -> hard fail.
    from ultracua.flows import FlowSpec, MutateSpec

    mut = FlowSpec(name="watchwrite", start_url="http://127.0.0.1:1/", goal="submit the order",
                   mutate=MutateSpec(confirm_text_contains="ok"), headless=True)
    st2, out2 = await probe(watch, mut, cache=ctx.cache())
    if st2 == "ok":
        checks.append(fail("watch HARD-REFUSES a mutating spec (a scheduler re-fires writes)",
                           f"watch ACCEPTED a mutating spec and returned {out2!r}"))
    else:
        checks.append(ok("watch HARD-REFUSES a mutating spec (a scheduler re-fires writes)"))
    # Signature-level probe for the interval guard; behavior needs history timestamps to exist.
    import inspect as _inspect

    try:
        params = set(_inspect.signature(watch).parameters)
    except (TypeError, ValueError):
        params = set()
    checks.append(expect("min_interval" in params or "min_interval_s" in params,
                         "watch enforces a per-flow min-interval (refuses an over-eager cron)",
                         f"watch signature has no min_interval parameter: {sorted(params)}",
                         aspirational=True))
    return checks


@scenario(
    id="h10.watch.heal_as_signal",
    title="heal_policy='report': unbuilt; the heal seam, extra hook and forward-compat loader pass",
    group="h10", aspirational=True, tags=("watch", "heal", "horizon"),
)
async def heal_as_signal(ctx: Ctx):
    """H10 plan step 3: `heal_policy: 'apply'|'report'` on FlowSpec, threaded to flow.py's
    _maybe_heal — 'report' returns not-healed and surfaces a HealEvent instead of silently
    persisting the healed locator (a silently-applied heal is masked drift for a monitor).
    Missing today; the seams it threads through are shipped and pass."""
    import dataclasses

    from ultracua import flow as flow_mod
    from ultracua.flow import FlowReport
    from ultracua.flows import FlowSpec, _only_known

    checks = []
    # PARTIAL CREDIT: forward-compat spec loading — a spec JSON written by a FUTURE heal_policy
    # version must still load on this version (the plan's "forward-compat free via _only_known"
    # claim). Stays true after shipping too: then the field simply survives the filter.
    st, out = await probe(lambda: FlowSpec(**_only_known(
        {"name": "w", "start_url": "http://127.0.0.1:1/", "goal": "g", "heal_policy": "report"},
        FlowSpec)))
    checks.append(expect(st == "ok",
                         "a spec JSON carrying heal_policy loads on this version (no crash, no trust wipe)",
                         f"status={st} out={out!r}"))
    # PARTIAL CREDIT: the self-heal seam heal_policy threads into exists (flow.py:_maybe_heal) —
    # report-mode is a behavior change AT this seam, not new machinery.
    checks.append(expect(callable(getattr(flow_mod, "_maybe_heal", None)),
                         "the self-heal seam (_maybe_heal) heal_policy threads into is shipped"))
    # PARTIAL CREDIT: the extension point the plan parks the HealEvent in (FlowReport.extra)
    # exists — no report-schema break is needed to ship heal-as-signal.
    checks.append(expect("extra" in {f.name for f in dataclasses.fields(FlowReport)},
                         "FlowReport.extra exists — where report-mode parks the HealEvent"))
    # HORIZON: the field itself. An unexpected-kwarg TypeError IS the not-built signal.
    st2, out2 = await probe(FlowSpec, name="w", start_url="http://127.0.0.1:1/", goal="g",
                            heal_policy="report")
    if st2 == "ok":
        checks.append(ok("FlowSpec accepts heal_policy ('apply'|'report')"))
    else:
        checks.append(missing("FlowSpec accepts heal_policy ('apply'|'report')",
                              f"{type(out2).__name__}: {out2}"))
    # HORIZON: the HealEvent record type (the change-event payload a reported heal emits) —
    # probed across the modules it could plausibly land in.
    spots = [import_probe(n) for n in ("ultracua.flows", "ultracua.flow",
                                       "ultracua.diffing", "ultracua.history")]
    checks.append(expect(any(m_ok and hasattr(m, "HealEvent") for m_ok, m in spots),
                         "a HealEvent record type exists (the reported-heal change payload)",
                         "no HealEvent in flows/flow/diffing/history", aspirational=True))
    return checks
