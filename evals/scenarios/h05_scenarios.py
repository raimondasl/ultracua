"""H5 evals: dry-run replay — shadow writes with a held-commit review (ROADMAP H5).

The horizon: a replay mode (no heal, no replan) where a network arbiter HOLDS every write
during a mutating step's act window — the same idempotency-header + write-settle bracket
normal replay already opens — records it as a `HeldWrite` (step, intent, method, URL, body),
fulfills it with synthesized success, and shows the human the exact POST bodies the flow
WOULD send, before `approve()`. Uninterceptable channels (WebSocket / service worker /
sendBeacon) are refused, not risked; the report states "N of M writes reached".

Today most of that is unbuilt — those checks report `missing`. What already PASSES is the
machinery the arbiter is designed to ride on: the write pipeline itself (a learned write flow
deterministically re-fires its POST at 0 LLM calls), the act-window bracket (Idempotency-Key
stamped only on the gated replay), and `safety.is_write_request` (the classifier the arbiter
keys interception on). Key-less: local Fixture pages + ScriptedProvider, real headless Chromium.
"""

from __future__ import annotations

from evals.core import Ctx, expect, fail, import_probe, missing, ok, probe, scenario
from evals.fixtures import Fixture, page

_GOAL = "place the order"
# The scripted "teacher": click the order form's submit button (a real form POST -> a real
# write against the fixture server), then declare done — the tests/ write-flow convention.
_STEPS = [
    {"action": "click", "role": "button", "name": "place order", "intent": "place the order"},
    {"action": "done", "intent": "order submitted"},
]


def _order_fixture() -> Fixture:
    """A minimal checkout: the form POSTs /order to the LOCAL server (fx.writes is the oracle
    for 'did a write actually arrive'), which 303-redirects to the confirmation page."""
    return Fixture({
        "/": page('<h1>Checkout</h1>'
                  '<form id="order-form" action="/order" method="post">'
                  '<input type="hidden" name="sku" value="UC-1"/>'
                  '<button type="submit">Place order</button>'
                  '</form>'),
        "/done": page('<h1>Order placed</h1><p id="ok">thanks — order UC-1 confirmed</p>'),
    }, post_redirect="/done")


def _post_spy(captured: list):
    """Pass-through route that records each outgoing POST's headers (the request still reaches
    the fixture server, so fx.writes keeps counting). Needed because WriteRecord has no headers,
    and the Idempotency-Key bracket is exactly where the DryRunArbiter plan opens its hold window."""

    async def prepare(session) -> None:
        async def handler(route) -> None:
            captured.append(dict(route.request.headers))
            await route.continue_()

        await session.page.route("**/order", handler)

    return prepare


@scenario(
    id="h05.dryrun.write_pipeline_baseline",
    title="the write pipeline a dry run must hold is real: replay re-fires the POST, act-window bracketed",
    group="h05", tags=("dry-run", "writes", "baseline"),
)
async def write_pipeline_baseline(ctx: Ctx):
    """PARTIAL CREDIT baseline (shipped behavior — plain expect, a fail here is a regression).
    Proving why dry-run is needed is not the eval; the pass is that the write machinery the
    arbiter will wrap — commit, 0-LLM replay, act-window attribution — works today."""
    from ultracua.flow import run_cached
    from ultracua.providers.scripted import ScriptedProvider

    checks = []
    fx = _order_fixture()
    learn_posts: list = []
    replay_posts: list = []
    with fx.serve() as base:
        cache = ctx.cache()
        learned = await run_cached(base + "/", _GOAL, ScriptedProvider(list(_STEPS)), cache,
                                   mode="learn", prepare=_post_spy(learn_posts), headless=True)
        # The commit path is real: learning the flow drove exactly one POST into the server.
        checks.append(expect(learned.success and len(fx.writes) == 1,
                             "learn drives the order POST to the server exactly once",
                             f"success={learned.success} writes={len(fx.writes)} note={learned.note!r}"))
        if not learned.success:
            return checks  # shipped learn machinery broke — the rest would only cascade

        replayed = await run_cached(base + "/", _GOAL, None, cache, mode="replay",
                                    prepare=_post_spy(replay_posts), headless=True)
        # Deterministic replay of a WRITE flow at 0 LLM calls — the exact run a dry-run mode
        # would shadow (dry-run is strictly MORE 0-LLM: it additionally disables heal/replan).
        checks.append(expect(replayed.success and replayed.llm_calls == 0,
                             "replay of the write flow succeeds at ZERO LLM calls",
                             f"success={replayed.success} llm_calls={replayed.llm_calls}"))
        # Normal replay DOES commit: the write reaches the server again (fx.writes == 2). This
        # is the behavior a dry run must invert — hold the write so this count stays flat.
        checks.append(expect(len(fx.writes) == 2 and fx.writes[-1].path == "/order",
                             "normal replay COMMITS: the write reaches the server a second time",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
        # The act-window bracket the DryRunArbiter plan reuses: the Idempotency-Key header is
        # stamped on the gated replay's write and NOT on the learn run's (flow.py sets/clears it
        # around the mutating actuation — the same open/close the hold window would use).
        learn_keyed = bool(learn_posts and learn_posts[0].get("idempotency-key"))
        replay_keyed = bool(replay_posts
                            and replay_posts[0].get("idempotency-key", "").startswith("uca-"))
        checks.append(expect(replay_keyed and not learn_keyed,
                             "Idempotency-Key brackets ONLY the gated replay write (the arbiter's window)",
                             f"learn_keyed={learn_keyed} replay_keyed={replay_keyed}"))
        # Per-step attribution: the mutating step's trace meta carries the idempotency key — the
        # same step-level causal keying a HeldWrite(step, intent, ...) record needs.
        checks.append(expect(any(t.meta.get("idempotency_key") for t in replayed.traces),
                             "the mutating step's trace meta attributes the write to its step",
                             "no trace carries meta['idempotency_key']"))
    return checks


@scenario(
    id="h05.dryrun.write_classifier",
    title="safety.is_write_request — the shipped classifier the arbiter keys interception on",
    group="h05", tags=("dry-run", "writes", "safety"),
)
async def write_classifier(ctx: Ctx):
    """PARTIAL CREDIT: the network-signature primitive (non-idempotent method + non-telemetry
    host) the DryRunArbiter's route handler matches against is shipped and exact."""
    from ultracua.safety import is_write_request

    return [
        # A form POST to an app host is the thing a dry run must hold.
        expect(is_write_request("POST", "http://127.0.0.1:9999/order"),
               "a POST to an app host classifies as a write"),
        # Reads pass through untouched — dry-run runs the flow live with real reads.
        expect(not is_write_request("GET", "http://127.0.0.1:9999/order?q=1"),
               "a GET read is not classified as a write"),
        # Beacon-aware breadth: analytics beacons must not false-abort every dry run.
        expect(not is_write_request("POST", "https://region1.google-analytics.com/g/collect"),
               "an analytics-host beacon POST is exempt (no false hold/abort)"),
        # Dot-boundary suffix match: a lookalike host must NOT inherit the telemetry exemption —
        # one wrong exemption entry would hide a real write (the allowlist failure shape).
        expect(is_write_request("POST", "https://notgoogle-analytics.com/collect"),
               "a telemetry-lookalike host is still a write (no suffix-match leak)"),
    ]


@scenario(
    id="h05.dryrun.api_surface",
    title="dry-run API surfaces: dryrun module, HeldWrite, flows.dry_run, engine kwarg, SW blocking",
    group="h05", aspirational=True, tags=("dry-run", "writes", "horizon"),
)
async def api_surface(ctx: Ctx):
    """ASPIRATIONAL: the surfaces the H5 plan names, probed so each reports `missing` until it
    ships (and flips to pass the day it does)."""
    import inspect as _inspect

    from ultracua.browser import BrowserSession
    from ultracua.flow import run_cached

    checks = []
    # Plan slice 1: src/ultracua/dryrun.py with the DryRunArbiter (route handler + init-script patch).
    ok_mod, mod = import_probe("ultracua.dryrun")
    if ok_mod:
        checks.append(expect(hasattr(mod, "DryRunArbiter"),
                             "ultracua.dryrun module ships a DryRunArbiter",
                             "module exists but has no DryRunArbiter", aspirational=True))
    else:
        checks.append(missing("ultracua.dryrun module ships a DryRunArbiter",
                              "no ultracua.dryrun module yet"))
    # The review artifact's record type: HeldWrite(step, intent, method, url, body).
    mods = [import_probe(n) for n in ("ultracua.dryrun", "ultracua.types", "ultracua.flow")]
    checks.append(expect(any(m_ok and hasattr(m, "HeldWrite") for m_ok, m in mods),
                         "a HeldWrite record type exists (the held-commit review row)",
                         "no HeldWrite in dryrun/types/flow", aspirational=True))
    # Plan slice 4: a flows.dry_run verb beside replay — no approval gate (it IS the
    # pre-approval artifact), never records health.
    ok_flows, flows_mod = import_probe("ultracua.flows")
    checks.append(expect(bool(ok_flows) and hasattr(flows_mod, "dry_run"),
                         "flows.dry_run verb exists beside replay",
                         "ultracua.flows has no dry_run", aspirational=True))
    # Plan slice 2: dry_run threaded through the engine entrypoint (run_cached -> _replay_step).
    checks.append(expect("dry_run" in _inspect.signature(run_cached).parameters,
                         "run_cached accepts a dry_run flag",
                         "run_cached signature has no dry_run parameter", aspirational=True))
    # Channel refusal: service workers can bypass context.route, so dry-run needs the session to
    # block them. Constructing BrowserSession doesn't launch — a TypeError here means not built.
    status, out = await probe(BrowserSession, service_workers="block")
    if status == "ok":
        checks.append(ok("BrowserSession can block service workers (uninterceptable-channel refusal)"))
    else:
        checks.append(missing("BrowserSession can block service workers (uninterceptable-channel refusal)",
                              f"{type(out).__name__}: {out}"))
    return checks


@scenario(
    id="h05.dryrun.hold_guarantee",
    title="the guarantee: a dry-run replay holds the write — the server must never see it",
    group="h05", aspirational=True, tags=("dry-run", "writes", "horizon"),
)
async def hold_guarantee(ctx: Ctx):
    """ASPIRATIONAL end-to-end: learn a real write flow, then attempt a dry-run replay. Today the
    engine has no dry_run surface -> missing. Once shipped, the hold check is NOT aspirational:
    a 'dry' run that writes is the catastrophic inversion and must FAIL loud."""
    from ultracua.flow import run_cached
    from ultracua.providers.scripted import ScriptedProvider

    checks = []
    fx = _order_fixture()
    with fx.serve() as base:
        cache = ctx.cache()
        learned = await run_cached(base + "/", _GOAL, ScriptedProvider(list(_STEPS)), cache,
                                   mode="learn", headless=True)
        if not learned.success:
            # Shipped learn machinery broke — a real regression, not a horizon gap.
            return [fail("learn the write flow (baseline for the dry-run probe)",
                         f"note={learned.note!r}")]
        checks.append(ok("learn the write flow (baseline for the dry-run probe)"))

        writes_before = len(fx.writes)  # the learn's own (legitimate) commit
        status, out = await probe(run_cached, base + "/", _GOAL, None, cache,
                                  mode="replay", headless=True, dry_run=True)
        if status == "missing":
            # TypeError("unexpected keyword argument 'dry_run'") — the capability isn't built.
            checks.append(missing("engine accepts dry_run=True on a replay",
                                  f"{type(out).__name__}: {out}"))
            checks.append(missing("a held write NEVER reaches the server",
                                  "unverifiable until a dry-run surface exists"))
            checks.append(missing("the report lists the held writes for human review",
                                  "unverifiable until a dry-run surface exists"))
        elif status == "error":
            # The kwarg was ACCEPTED (so the capability is claimed) but the run crashed.
            checks.append(fail("engine accepts dry_run=True on a replay",
                               f"shipped dry_run surface crashed: {type(out).__name__}: {out}"))
        else:
            report = out
            checks.append(ok("engine accepts dry_run=True on a replay"))
            # THE guarantee — the whole feature. A leak here is a real payment sent during the
            # run marketed as safe, so this is a hard fail, never aspirational-missing.
            checks.append(expect(len(fx.writes) == writes_before,
                                 "a held write NEVER reaches the server",
                                 f"dry run LEAKED {len(fx.writes) - writes_before} write(s): "
                                 f"{[(w.method, w.path) for w in fx.writes[writes_before:]]}"))
            # The review artifact: held writes surfaced (plan parks them in FlowReport.extra).
            extra = getattr(report, "extra", None) or {}
            held = extra.get("dry_run") or extra.get("held_writes")
            checks.append(expect(bool(held),
                                 "the report lists the held writes for human review",
                                 "dry run returned no held-write report in FlowReport.extra",
                                 aspirational=True))
    return checks


@scenario(
    id="h05.dryrun.report_honesty",
    title="report honesty scaffolding: extra hook + commit barrier exist; N-of-M labels do not",
    group="h05", aspirational=True, tags=("dry-run", "writes", "horizon"),
)
async def report_honesty(ctx: Ctx):
    """Half partial-credit, half horizon: the extension points the honest report rides on are
    shipped; the honesty fields themselves ('N of M writes reached', unrepresentative labels,
    barrier -> 'held') are not."""
    import dataclasses

    from ultracua.cache import StepConfirm
    from ultracua.flow import FlowReport
    from ultracua.timing import StepTrace

    rep_fields = {f.name for f in dataclasses.fields(FlowReport)}
    tr_fields = {f.name for f in dataclasses.fields(StepTrace)}
    # pydantic v1/v2 tolerant field listing for the StepConfirm barrier model.
    confirm_fields = set(getattr(StepConfirm, "model_fields", None)
                         or getattr(StepConfirm, "__fields__", {}))
    return [
        # PARTIAL CREDIT: the plan returns the held-write report via FlowReport.extra['dry_run'] —
        # the extension dict exists today, so no schema break is needed to ship it.
        expect("extra" in rep_fields,
               "FlowReport has an `extra` extension point for the held-write report"),
        # PARTIAL CREDIT: the Phase-G per-write commit barrier — the thing dry-run converts to a
        # 'held — unverifiable' outcome — is shipped (StepConfirm, attach-time bound per write).
        expect("confirm_selector" in confirm_fields,
               "per-write commit barrier (StepConfirm) exists for dry-run to convert to 'held'"),
        # Horizon: post-held steps must be LABELED unrepresentative (write #2's body was computed
        # from fake write-#1 state) — first-class on StepTrace, or re-key when it ships elsewhere.
        expect("unrepresentative" in tr_fields,
               "StepTrace can label post-held steps unrepresentative",
               "no `unrepresentative` field on StepTrace (may land in trace meta — re-key then)",
               aspirational=True),
        # Horizon: 'N of M writes reached' — a prefix-only dry run must never read as full
        # coverage, or a human approves a silently partial picture.
        expect(bool({"writes_reached", "writes_declared"} & rep_fields),
               "FlowReport carries writes_reached/writes_declared (N-of-M honesty)",
               "no N-of-M counters on FlowReport", aspirational=True),
    ]
