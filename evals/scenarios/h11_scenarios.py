"""H11 evals: Web Bot Auth signed-agent identity (ROADMAP H11).

The horizon: an opt-in RFC 9421 request signer — per-deployment Ed25519 keys in a new
`ultracua.botauth` module, wired into `BrowserSession` as an `identity=` route handler —
plus evidence-gated typed "identity rejected" outcomes (`safety.challenge_evidence`:
the cf-mitigated response header first, text heuristics only as fallback) surfaced as a
`FlowIdentityRejectedError`, a distinct FlowMeta counter, and `flow identity init|status`
CLI verbs. Goal: a bot-manager block becomes a TYPED, attributable ops signal instead of
mystery drift — no stealth, verifiable declared identity, replay stays 0-LLM (pure crypto).

Today the signer, wiring, typed error, and CLI are unbuilt — those checks report `missing`.
What already PASSES is the machinery H11 rides on: the interstitial text heuristic
(`safety.looks_like_interstitial`) and replay's escalate-not-retry behavior on a challenge
page (the generic fallback the evidence-gated classifier must degrade to); the context
header channel the signer must MERGE with (`set_extra_http_headers`, the Idempotency-Key
path); HAR capture for challenge forensics; and the env-only-secret / typed-error /
FlowMeta-run-history precedents the new pieces extend. Key-less: local Fixture pages +
ScriptedProvider, real headless Chromium only where replay behavior is under test.
"""

from __future__ import annotations

from evals.core import Ctx, expect, fail, import_probe, missing, ok, probe, scenario
from evals.fixtures import Fixture, page

_GOAL = "open the daily report"
# The scripted "teacher": click the report link, declare done — the tests/ read-flow convention.
_STEPS = [
    {"action": "click", "role": "link", "name": "daily report", "intent": "open the daily report"},
    {"action": "done", "intent": "report open"},
]

# A Cloudflare-style challenge page. Uses two signals safety.INTERSTITIAL_SIGNALS actually
# carries ("checking your browser", "verify you are human") so detection is the shipped
# heuristic, not a lucky substring.
_CHALLENGE = page(
    "<h1>Just a moment...</h1>"
    "<p>Checking your browser before accessing shop.test.</p>"
    "<p>Verify you are human by completing the action below.</p>",
    title="Just a moment...",
)


def _annotation_names(cls) -> set[str]:
    """Field names of a (data)class via its own __annotations__ — tolerant of non-dataclasses."""
    return set(getattr(cls, "__annotations__", {}) or {})


@scenario(
    id="h11.signer.botauth_surface",
    title="botauth module surfaces: AgentIdentity signer + JWKS directory / Agent Card emitters",
    group="h11", aspirational=True, tags=("identity", "botauth", "horizon"),
)
async def botauth_surface(ctx: Ctx):
    """ASPIRATIONAL: plan slice 1 — `src/ultracua/botauth.py` with the env-keyed Ed25519
    RFC 9421 signer and emitters for the self-hosted `.well-known` key directory + Signature
    Agent Card. Probed by name so each reports `missing` until it ships (and flips to pass
    the day it does). One PARTIAL-CREDIT pass: the env-only secret pattern the key mirrors."""
    import dataclasses

    checks = []
    ok_mod, mod = import_probe("ultracua.botauth")
    if ok_mod:
        # The signer object: Ed25519 key resolved from an env var, mints Signature-Agent /
        # Signature-Input / Signature headers over @authority + signature-agent.
        checks.append(expect(hasattr(mod, "AgentIdentity"),
                             "ultracua.botauth ships AgentIdentity (env-keyed Ed25519 RFC 9421 signer)",
                             "module exists but has no AgentIdentity", aspirational=True))
        # Emitters for the artifacts the user must self-host at
        # /.well-known/http-message-signatures-directory (fuzzy name scan — exact emitter
        # names are unpinned in the plan; re-key when they ship).
        names = " ".join(dir(mod)).lower()
        checks.append(expect("directory" in names,
                             "botauth emits the .well-known JWKS key directory",
                             "no *directory* emitter in ultracua.botauth", aspirational=True))
        checks.append(expect("card" in names,
                             "botauth emits the Signature Agent Card JSON",
                             "no *card* emitter in ultracua.botauth", aspirational=True))
    else:
        checks.append(missing("ultracua.botauth ships AgentIdentity (env-keyed Ed25519 RFC 9421 signer)",
                              "no ultracua.botauth module yet"))
        checks.append(missing("botauth emits the .well-known JWKS key directory",
                              "no ultracua.botauth module yet"))
        checks.append(missing("botauth emits the Signature Agent Card JSON",
                              "no ultracua.botauth module yet"))
    # PARTIAL CREDIT: the plan keys the private key off an env var "mirroring the LoginSpec
    # env-only secret pattern" — that pattern (secrets named by *_env fields, resolved at
    # runtime, never persisted by save_spec) is shipped and is what botauth extends.
    from ultracua.flows import LoginSpec

    login_fields = {f.name for f in dataclasses.fields(LoginSpec)}
    checks.append(expect({"username_env", "password_env"} <= login_fields,
                         "the env-only secret pattern the Ed25519 key will mirror is shipped (LoginSpec *_env)",
                         f"LoginSpec fields={sorted(login_fields)}"))
    return checks


@scenario(
    id="h11.session.identity_wiring",
    title="session wiring: identity= kwarg + config knobs (missing); header-merge + HAR channels (shipped)",
    group="h11", aspirational=True, tags=("identity", "browser", "horizon"),
)
async def identity_wiring(ctx: Ctx):
    """ASPIRATIONAL: plan slice 2 — `BrowserSession(identity=...)` installs the signing route
    handler, opt-in via new config knobs (routing disables the browser HTTP cache, so it must
    never be default-on). PARTIAL CREDIT for the two shipped channels the wiring rides on:
    the extra-headers channel the signer must merge with (never clobbering the Idempotency-Key
    injection) and HAR capture (offline forensics for challenge responses). Signature
    inspection only — no Chromium launch needed, keeps the probe deterministic and fast."""
    import inspect as _inspect

    from ultracua.browser import BrowserSession
    from ultracua.config import settings
    from ultracua.flow import run_cached

    bs_params = _inspect.signature(BrowserSession.__init__).parameters
    return [
        # The route-handler entry point: identity= on the session that owns the context.
        expect("identity" in bs_params,
               "BrowserSession accepts identity= (installs the RFC 9421 signing route handler)",
               "no identity kwarg on BrowserSession.__init__", aspirational=True),
        # And threaded through the engine entrypoint so flows can opt in per-run.
        expect("identity" in _inspect.signature(run_cached).parameters,
               "run_cached threads identity through to the session",
               "run_cached signature has no identity parameter", aspirational=True),
        # Opt-in config: route('**/*') disables the context HTTP cache and erodes the measured
        # replay speedups, so signing must be gated behind explicit settings.
        expect(hasattr(settings, "bot_auth_key_env") and hasattr(settings, "signature_agent_url"),
               "opt-in config knobs exist (bot_auth_key_env, signature_agent_url)",
               "no bot_auth_key_env / signature_agent_url on settings", aspirational=True),
        # PARTIAL CREDIT: the context header channel signing must MERGE with is shipped — the
        # same channel flow.py uses to bracket mutating steps with an Idempotency-Key. A signer
        # that clobbered it would reopen the double-submit hole.
        expect(hasattr(BrowserSession, "set_extra_http_headers"),
               "the context header channel the signer must merge with (Idempotency-Key path) is shipped"),
        # PARTIAL CREDIT: HAR capture is shipped — response headers (cf-mitigated & friends)
        # land in the archive, the offline evidence trail for challenge forensics.
        expect("record_har_path" in bs_params,
               "network evidence capture for challenge forensics (record_har_path) is shipped"),
    ]


@scenario(
    id="h11.detect.challenge_evidence",
    title="challenge detection: text-heuristic fallback (shipped) vs evidence-gated classifier (missing)",
    group="h11", aspirational=True, tags=("identity", "safety", "detection"),
)
async def challenge_evidence(ctx: Ctx):
    """Half partial-credit, half horizon: `safety.looks_like_interstitial` — the deterministic
    text fallback the H11 classifier degrades to — is shipped and exact; the hard-evidence
    tier (`safety.challenge_evidence`, cf-mitigated response header first) is not. The gating
    invariant matters because a text-only guess claiming 'identity rejected' would mislabel
    genuine site drift and misroute the ops signal. Pure function calls — no browser."""
    from ultracua import safety

    checks = [
        # PARTIAL CREDIT: a Cloudflare-style challenge body flags via the shipped substring
        # heuristic ("checking your browser" + "verify you are human" are real list entries).
        expect(safety.looks_like_interstitial(
                   "https://shop.test/cart", "Just a moment...",
                   "Checking your browser before accessing shop.test. Verify you are human."),
               "text heuristic flags a Cloudflare-style challenge body"),
        # PARTIAL CREDIT: the URL alone carries a signal (cdn-cgi/challenge-platform) — a
        # challenge that renders no readable text is still caught.
        expect(safety.looks_like_interstitial(
                   "https://shop.test/cdn-cgi/challenge-platform/h/g/orchestrate", "", ""),
               "text heuristic flags the challenge-platform URL signal alone"),
        # PARTIAL CREDIT: false-positive guard. A benign page must NOT flag — a false positive
        # here would route an ordinary replay into escalate/identity-rejected and hide drift.
        expect(not safety.looks_like_interstitial(
                   "https://shop.test/report", "Daily report", "total: 42 orders shipped"),
               "a benign page does NOT flag (a false positive would misroute real drift)"),
    ]
    # ASPIRATIONAL: the hard-evidence tier — plan slice 3's challenge_evidence() checks the
    # cf-mitigated response header / WAF signatures BEFORE any text heuristic.
    if hasattr(safety, "challenge_evidence"):
        status, out = await probe(safety.challenge_evidence,
                                  headers={"cf-mitigated": "challenge"},
                                  url="https://shop.test/cart", title="", text="")
        checks.append(expect(status == "ok" and bool(out),
                             "challenge_evidence classifies a canned cf-mitigated: challenge response",
                             f"probe status={status}: {out}", aspirational=True))
        # The evidence-gating invariant: text-only signals must NOT count as hard evidence of
        # identity rejection (they stay the generic escalate path).
        status2, out2 = await probe(safety.challenge_evidence,
                                    headers={},
                                    url="https://shop.test/cart", title="Just a moment...",
                                    text="checking your browser")
        checks.append(expect(status2 == "ok" and not out2,
                             "text-only signals do NOT count as hard identity-rejection evidence",
                             f"probe status={status2}: {out2}", aspirational=True))
    else:
        checks.append(missing("challenge_evidence classifies a canned cf-mitigated: challenge response",
                              "no challenge_evidence in safety.py yet"))
        checks.append(missing("text-only signals do NOT count as hard identity-rejection evidence",
                              "unverifiable until safety.challenge_evidence exists"))
    return checks


@scenario(
    id="h11.replay.block_typed_not_drift",
    title="a bot-manager block during replay is a typed outcome: escalate now, identity_rejected later",
    group="h11", tags=("identity", "replay", "fail-loud"),
)
async def block_typed_not_drift(ctx: Ctx):
    """PARTIAL CREDIT baseline (shipped — plain expect, a fail here is a regression): replay
    hitting a challenge page returns a TYPED escalate outcome at 0 LLM calls without burning
    retries — the exact behavior H11 upgrades to mode='identity_rejected' when signing was
    active. One ASPIRATIONAL probe: the typed error the upgraded path raises."""
    from ultracua.flow import run_cached
    from ultracua.providers.scripted import ScriptedProvider

    checks = []
    fx = Fixture({
        "/": page('<a href="/answer">open the daily report</a>'),
        "/answer": page('<h1>Report</h1><p id="total">total: 42</p>'),
    })
    with fx.serve() as base:
        cache = ctx.cache()
        learned = await run_cached(base + "/", _GOAL, ScriptedProvider(list(_STEPS)), cache,
                                   mode="learn", headless=True)
        if not learned.success:
            # Shipped learn machinery broke — a real regression, not a horizon gap.
            return [fail("learn the read flow (baseline for the challenge replay)",
                         f"note={learned.note!r}")]
        checks.append(ok("learn the read flow (baseline for the challenge replay)"))

        # Simulate the bot-manager block: the SAME entry URL now serves a challenge page
        # (what a Cloudflare/WAF block looks like to an unsigned replay).
        fx.pages["/"] = _CHALLENGE
        gets_before = len(fx.gets)
        replayed = await run_cached(base + "/", _GOAL, None, cache, mode="replay", headless=True)

        # The block is a TYPED outcome — mode='escalate', never a silent wrong-data attempt
        # and never misreported as ordinary step drift.
        checks.append(expect(replayed.mode == "escalate" and not replayed.success,
                             "replay classifies the challenge page as a typed escalate outcome",
                             f"mode={replayed.mode} success={replayed.success} note={replayed.note!r}"))
        # The classification is deterministic: detecting + reporting the block costs zero LLM
        # calls (H11's classifier is pure header/text checks — same 0-LLM contract).
        checks.append(expect(replayed.llm_calls == 0,
                             "classifying the block costs ZERO LLM calls",
                             f"llm_calls={replayed.llm_calls}"))
        # No retry burn: exactly ONE navigation hit the challenged entry page. Hammering a
        # bot manager with retries is the behavior escalate exists to prevent (and the future
        # identity_rejected path must never retry a mutating step with a fresh signature).
        entry_gets = fx.gets[gets_before:].count("/")
        checks.append(expect(entry_gets == 1,
                             "escalation burns no retries against the challenging origin",
                             f"entry-page GETs during replay={entry_gets}"))
        # ASPIRATIONAL: with signing active the same block should surface as a typed
        # FlowIdentityRejectedError (flows.replay) so ops can tell 'identity rejected'
        # from 'site drifted'. Probe the error type by name.
        ok_flows, flows_mod = import_probe("ultracua.flows")
        checks.append(expect(bool(ok_flows) and hasattr(flows_mod, "FlowIdentityRejectedError"),
                             "a typed FlowIdentityRejectedError exists for signed-replay blocks",
                             "no FlowIdentityRejectedError in ultracua.flows", aspirational=True))
    return checks


@scenario(
    id="h11.ops.rejection_routing",
    title="ops routing: FlowMeta rejection counter, fleet-view distinction, flow identity CLI verbs",
    group="h11", aspirational=True, tags=("identity", "ops", "horizon"),
)
async def rejection_routing(ctx: Ctx):
    """ASPIRATIONAL: plan slices 4-5 — identity rejections recorded as a DISTINCT FlowMeta
    field (written only through the locked _update_meta path), fleet views (run_all / canary)
    telling 'identity rejected' apart from 'site drifted', and `flow identity init|status`
    CLI verbs for keygen + hosted-directory verification. PARTIAL CREDIT for the two shipped
    precedents these extend: the FlowMeta run-history sidecar and typed replay errors."""
    import dataclasses
    import inspect as _inspect

    from ultracua import flows

    meta_fields = {f.name for f in dataclasses.fields(flows.FlowMeta)}
    checks = [
        # PARTIAL CREDIT: the run-history sidecar (runs/successes/last_error/...) the identity
        # counter extends is shipped — same atomic lock+replace write path, no new plumbing.
        expect({"runs", "successes", "last_error", "consecutive_failures"} <= meta_fields,
               "the FlowMeta run-history sidecar the rejection counter extends is shipped",
               f"FlowMeta fields={sorted(meta_fields)}"),
        # PARTIAL CREDIT: typed replay failures (FlowReplayError) are the precedent
        # FlowIdentityRejectedError follows — the ops channel already speaks typed errors.
        expect(isinstance(getattr(flows, "FlowReplayError", None), type)
               and issubclass(flows.FlowReplayError, Exception),
               "typed replay failure errors (FlowReplayError) are shipped"),
        # ASPIRATIONAL: a distinct identity-rejection field on FlowMeta — kept separate from
        # drift counters so identity classification can never mask real drift.
        expect(any("identity" in f for f in meta_fields),
               "FlowMeta records identity rejections distinctly from drift",
               "no identity-rejection field on FlowMeta", aspirational=True),
    ]
    # ASPIRATIONAL: the fleet views distinguish the two failure kinds — the run_all alert /
    # canary result must say WHICH one happened, or a fleet block reads as mass drift.
    fleet_fields = set()
    for cls_name in ("FleetRun", "CanaryResult"):
        cls = getattr(flows, cls_name, None)
        if cls is not None:
            fleet_fields |= _annotation_names(cls)
    checks.append(expect(any("identity" in f for f in fleet_fields),
                         "fleet views (run_all/canary) distinguish 'identity rejected' from 'site drifted'",
                         f"no identity field on FleetRun/CanaryResult ({sorted(fleet_fields)})",
                         aspirational=True))
    # ASPIRATIONAL: `flow identity init|status` CLI verbs. Probed by static inspection of the
    # parser wiring — invoking argparse with an unknown verb raises SystemExit (a BaseException
    # the runner's guard does not catch), so we must not drive the CLI to find out.
    import ultracua.cli as cli

    src = _inspect.getsource(cli)
    checks.append(expect('add_parser("identity"' in src or "add_parser('identity'" in src,
                         "flow identity init|status CLI verbs exist",
                         "no identity subcommand wired in cli.py", aspirational=True))
    return checks
