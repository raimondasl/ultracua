"""H1 evals: attested 0-LLM replay + signed evidence packs (ROADMAP "achievable with focused effort").

Horizon module — most capability checks report `missing` today by design. H1 layers a signed
attestation over the existing deterministic replay: a DSSE/ed25519-signed flow manifest at
`approve` time, a replay-time egress-origin observer (block-mode for reads, attest-mode for
writes), per-run attestation levels (SEALED / ATTESTED) computed from MEASURED Router.totals,
a hash-chained evidence bundle (StepTraces + post-settle screenshots + redacted HAR), and an
offline verifier CLI.

Partial credit measured today — the evidence streams H1 will sign already ship:
- BrowserSession(record_har_path=...) writes a real Playwright HAR on a fixture run
- on_step delivers StepTrace records (timing.py) — the raw material of the hash-chained log
- FlowReport.llm_calls == 0 on provider-less replay is MEASURED, not declared (the SEALED core)
- obs.UsageTotals does per-call measured accounting (what attestation levels compute from)
- flows.approve is the shipped seam where manifest signing lands

Everything here is key-less: local Fixture origins + a scripted provider + headless Chromium.
"""

from __future__ import annotations

from evals.core import Ctx, expect, import_probe, missing, probe, scenario
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


GOAL = "open the daily report page"


def _two_pages() -> Fixture:
    return Fixture({
        "/": page('<a href="/answer">open the daily report</a>'),
        "/answer": page('<h1>Report</h1><p id="total">total: 42</p>'),
    })


@scenario(
    id="h01.attest.module_surface",
    title="attest.py: canonical hash + DSSE/ed25519 sign + offline verify + FlowSpec attest level",
    group="h01", aspirational=True, tags=("attest", "signing"),
)
async def attest_module_surface(ctx: Ctx):
    checks = []
    # H1 plan step 1: a new src/ultracua/attest.py with canonical-JSON content hashing, DSSE PAE
    # encoding + ed25519 signing, and an OFFLINE verify function. Not built yet -> missing.
    present, mod = import_probe("ultracua.attest")
    checks.append(expect(present, "ultracua.attest imports (signing/verify module)",
                         f"{type(mod).__name__}", aspirational=True))
    if present:
        # If the module lands, it must expose BOTH halves: signing at approve time and an
        # offline verify (evidence a third party can check without ultracua installed state).
        has_sign = any(hasattr(mod, n) for n in ("sign", "sign_manifest", "sign_bundle"))
        has_verify = any(hasattr(mod, n) for n in ("verify", "verify_manifest", "verify_bundle"))
        checks.append(expect(has_sign and has_verify, "attest exposes sign + offline verify",
                             f"sign={has_sign} verify={has_verify}", aspirational=True))
    else:
        checks.append(missing("attest exposes sign + offline verify", "module absent"))
    # H1 plan step 5: FlowSpec gains an `attest` level ('sealed'|'attested'). A dataclass without
    # the field raises TypeError on the kwarg — for an aspirational probe that IS the signal.
    from ultracua.flows import FlowSpec

    status, _ = await probe(FlowSpec, name="h01-probe", start_url="http://127.0.0.1/", goal="g",
                            attest="sealed")
    checks.append(expect(status == "ok", "FlowSpec accepts attest='sealed' level",
                         f"probe={status}", aspirational=True))
    return checks


@scenario(
    id="h01.evidence.streams",
    title="evidence streams: HAR + StepTraces ship today; hash-chain + signed bundle are missing",
    group="h01", tags=("evidence", "har"),
)
async def evidence_streams(ctx: Ctx):
    import json

    from ultracua.flow import run_cached

    checks = []
    fx = _two_pages()
    traces = []
    har_path = ctx.tmp / "learn.har"
    with fx.serve() as base:
        learned = await run_cached(base + "/", GOAL, _ClickTheLink(), ctx.cache(), mode="learn",
                                   headless=True, on_step=traces.append,
                                   record_har_path=str(har_path))
    # Anchor: the evidence-producing run itself is shipped behavior — a break here is a real fail.
    checks.append(expect(learned.success, "scripted learn succeeds (the evidence-producing run)",
                         f"note={learned.note!r}"))
    # PARTIAL CREDIT: record_har_path (browser.py) already writes a Playwright HAR on context
    # close — the raw network-evidence stream the H1 bundle will redact + sign (plan step 4).
    entries = []
    if har_path.exists():
        try:
            entries = json.loads(har_path.read_text(encoding="utf-8"))["log"]["entries"]
        except Exception:  # noqa: BLE001 — malformed HAR shows as the check failing, not a crash
            entries = []
    checks.append(expect(bool(entries), "record_har_path wrote a parseable HAR with entries",
                         f"exists={har_path.exists()} entries={len(entries)}"))
    checks.append(expect(any(base in e.get("request", {}).get("url", "") for e in entries),
                         "HAR captured the fixture origin's traffic",
                         f"entries={len(entries)}"))
    # PARTIAL CREDIT: on_step already delivers StepTrace records with span timings — the raw
    # material of the hash-chained step log (plan step 4).
    checks.append(expect(len(traces) >= 1 and all(t.total_ms >= 0 for t in traces),
                         "on_step delivered StepTrace records with timings", f"n={len(traces)}"))
    # ASPIRATIONAL: tamper-evidence — each StepTrace binds the previous record's hash so a
    # post-hoc edit of one record breaks the chain.
    chained = any(hasattr(t, "prev_hash") or "prev_hash" in getattr(t, "meta", {})
                  or "chain_hash" in getattr(t, "meta", {}) for t in traces)
    checks.append(expect(chained, "StepTraces are hash-chained (tamper-evident log)",
                         "no prev_hash/chain_hash on traces", aspirational=True))
    # ASPIRATIONAL: the run surfaces a signed evidence-bundle path in FlowReport.extra
    # (plan step 4: "sign the bundle via attest.py and surface the path in FlowReport.extra").
    checks.append(expect("evidence_bundle" in learned.extra,
                         "FlowReport.extra carries an evidence-bundle path",
                         f"extra_keys={sorted(learned.extra)}", aspirational=True))
    return checks


@scenario(
    id="h01.attest.measured_zero_llm",
    title="SEALED substrate: provider-less replay MEASURES llm_calls==0; per-run attestation missing",
    group="h01", tags=("attest", "zero-llm"),
)
async def measured_zero_llm(ctx: Ctx):
    from ultracua.flow import run_cached
    from ultracua.obs import UsageTotals

    checks = []
    fx = _two_pages()
    with fx.serve() as base:
        cache = ctx.cache()
        await run_cached(base + "/", GOAL, _ClickTheLink(), cache, mode="learn", headless=True)
        replayed = await run_cached(base + "/", GOAL, None, cache, mode="replay", headless=True)
    # PARTIAL CREDIT: SEALED's core claim — provably 0 LLM calls — is already a MEASURED number:
    # a provider=None replay reports llm_calls from counting, never from declaration (the risk
    # list calls declared-not-measured levels the project's cardinal sin).
    checks.append(expect(replayed.success and replayed.llm_calls == 0,
                         "provider-less replay measures llm_calls==0 (the SEALED evidence)",
                         f"success={replayed.success} llm_calls={replayed.llm_calls}"))
    # PARTIAL CREDIT: with no provider there is no heal path — SEALED's 'heal disabled'
    # precondition falls out of construction today (mode stays 'replay', never 'replay+heal').
    checks.append(expect(replayed.mode == "replay",
                         "no provider -> no heal path (mode stays 'replay')",
                         f"mode={replayed.mode}"))
    # PARTIAL CREDIT: UsageTotals (obs.py) is the measured accounting the attestation LEVELS
    # must be computed from — snapshot/since gives an exact per-run call-count delta.
    tot = UsageTotals()
    snap = tot.snapshot()

    class _OneCall:  # duck-typed usage of a single LLM response
        input_tokens, output_tokens, cache_read_tokens, cache_write_tokens = 10, 5, 0, 0

    tot.add(_OneCall())
    delta = tot.since(snap)
    checks.append(expect(delta.calls == 1 and delta.input_tokens == 10,
                         "UsageTotals snapshot/since measures per-run call counts",
                         f"delta calls={delta.calls} in={delta.input_tokens}"))
    # ASPIRATIONAL: a per-run attestation record with a level COMPUTED from measured totals
    # (SEALED: 0 calls; ATTESTED: exactly one non-actuating extraction call).
    att = replayed.extra.get("attestation")
    checks.append(expect(isinstance(att, dict) and att.get("level") in ("SEALED", "ATTESTED"),
                         "FlowReport.extra carries a per-run attestation with a computed level",
                         f"extra_keys={sorted(replayed.extra)}", aspirational=True))
    return checks


@scenario(
    id="h01.egress.allowlist",
    title="egress allowlist: cross-origin contact observable today; capture + enforcement missing",
    group="h01", aspirational=True, tags=("egress", "allowlist"),
)
async def egress_allowlist(ctx: Ctx):
    from ultracua.cache import CachedFlow
    from ultracua.flow import run_cached

    checks = []
    # Two local origins (distinct ports = distinct origins): the flow lives on A; A's pages embed
    # a resource from B — exactly the shape a manifest allowlist must capture (B is 'non-manifest
    # egress' unless recorded at learn time).
    fx_b = Fixture({"/pixel": page("beacon", title="b")})
    with fx_b.serve() as base_b:
        fx_a = Fixture({
            "/": page(f'<a href="/answer">open the daily report</a><img src="{base_b}/pixel">'),
            "/answer": page(f'<h1>Report</h1><img src="{base_b}/pixel">'),
        })
        with fx_a.serve() as base_a:
            cache = ctx.cache()
            learned = await run_cached(base_a + "/", GOAL, _ClickTheLink(), cache, mode="learn",
                                       headless=True)
            replayed = await run_cached(base_a + "/", GOAL, None, cache, mode="replay",
                                        headless=True)
            # PARTIAL CREDIT: today's replay DOES contact the second origin (the img fetch
            # reaches B's server) — the gap H1's observer closes is real and measurable key-lessly.
            checks.append(expect(learned.success and replayed.success and len(fx_b.gets) >= 1,
                                 "cross-origin egress is observable on a fixture replay (the target gap)",
                                 f"learn={learned.success} replay={replayed.success} b_gets={fx_b.gets}"))
            # ASPIRATIONAL (plan step 3): replay accepts an egress policy ('enforce' route-aborts
            # non-manifest origins for read flows; 'attest' observes then fails loud). An
            # unexpected-kwarg TypeError is the not-built-yet signal, handled by probe().
            status, _ = await probe(run_cached, base_a + "/", GOAL, None, cache, mode="replay",
                                    headless=True, egress="enforce")
            checks.append(expect(status == "ok", "replay accepts egress='enforce'|'attest' policy",
                                 f"probe={status}", aspirational=True))
    # ASPIRATIONAL (plan step 2): record-time origin capture persisted on the cached flow so the
    # signed manifest can embed the allowlist (eTLD+1-normalized, TELEMETRY_HOSTS-aware).
    fields = getattr(CachedFlow, "model_fields", None) or getattr(CachedFlow, "__fields__", {})
    checks.append(expect("origins" in fields,
                         "CachedFlow persists observed origins (the allowlist basis)",
                         f"fields={sorted(fields)}", aspirational=True))
    # ASPIRATIONAL: the replay report exposes the origins actually contacted, so the per-run
    # attestation can state 'zero non-manifest origins' from observation, not assumption.
    checks.append(expect(any(k in replayed.extra for k in ("origins", "observed_origins")),
                         "FlowReport.extra records observed origins",
                         f"extra_keys={sorted(replayed.extra)}", aspirational=True))
    return checks


@scenario(
    id="h01.approve.manifest_and_verifier_cli",
    title="approve-time signed manifest sidecar + offline 'verify-evidence' / 'egress-review' CLI verbs",
    group="h01", aspirational=True, tags=("attest", "cli", "manifest"),
)
async def approve_manifest_and_cli(ctx: Ctx):
    from pathlib import Path

    import ultracua.cli as cli
    from ultracua.cache import flow_key
    from ultracua.flow import run_cached
    from ultracua.flows import FlowSpec, _load_meta, approve

    checks = []
    fx = _two_pages()
    with fx.serve() as base:
        spec = FlowSpec(name="h01-manifest", start_url=base + "/", goal=GOAL)
        cache = ctx.cache()
        learned = await run_cached(spec.start_url, spec.goal, _ClickTheLink(), cache,
                                   mode="learn", scope=spec.scope, headless=True)
        checks.append(expect(learned.success, "learn under the flow scope caches a flow",
                             f"note={learned.note!r}"))
        approve(spec, cache=cache)
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        meta = _load_meta(cache, key)
        # PARTIAL CREDIT: flows.approve — the exact seam H1 signs at (plan step 1) — ships and
        # flips the trust bit the manifest will be bound to.
        checks.append(expect(meta.approved is True,
                             "flows.approve marks the flow trusted (the signing seam ships)"))
        # ASPIRATIONAL (plan step 1): approve also writes a signed manifest SIDECAR
        # (<key>.manifest.json — deliberately NOT FlowMeta: unknown FlowMeta fields must never
        # risk the run history, the #78 forward-compat lesson).
        root = Path(cache.root)
        sidecars = [p.name for p in root.glob("*manifest*")]
        checks.append(expect((root / f"{key}.manifest.json").exists() or bool(sidecars),
                             "approve writes a signed flow-manifest sidecar",
                             f"sidecars={sidecars}", aspirational=True))
    # ASPIRATIONAL (plan steps 2 + 4): offline evidence verification and allowlist re-sign
    # tooling as `ultracua flow` verbs. Probed by scanning the CLI source for the verb strings:
    # argparse exits (SystemExit) on an unknown verb, which probe() would report as an error,
    # so a source scan is the crash-free way to ask "is the verb registered?".
    src = Path(cli.__file__).read_text(encoding="utf-8")
    checks.append(expect('"verify-evidence"' in src, "CLI has a 'flow verify-evidence' verb",
                         "verb not registered in cli.py", aspirational=True))
    checks.append(expect('"egress-review"' in src, "CLI has a 'flow egress-review' verb",
                         "verb not registered in cli.py", aspirational=True))
    return checks
