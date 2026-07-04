"""H14 evals: mandated money — cryptographic write mandates + agentic payment rails (ROADMAP.md H14).

The horizon: an approver signs ONE Ed25519 mandate (flow-scope pattern, per-write amount cap,
cumulative budget, validity window, max write count) and scheduled replays execute payment/
procurement flows unattended all week — every bound verified PURELY DETERMINISTICALLY at the
existing mutation gate before a write releases (replay stays 0-LLM), amount caps bound via strict
pinned reads of the on-page amount (any locale/currency ambiguity refuses), spend tracked in a
crash-safe reserve-then-commit ledger that fails CLOSED, and signed evidence packs asserting
SUBMISSION-side facts an auditor can verify offline. x402/HTTP-402 enters the core only as
detect-and-escalate (recognize a payment wall as an interstitial), never as in-core custody.

Today none of the mandate layer exists — these scenarios probe each planned surface aspirationally
(`missing`, never `fail`) and give PARTIAL CREDIT to the shipped primitives the H14 plan explicitly
builds on: the mutation gate + default approval gate ARE the deterministic enforcement point (a
write flow refuses an unapproved replay BEFORE actuation — key-less provable, server as oracle),
the idempotency key is a stable deterministic dedupe basis, pin.py's strict single-token parse
already refuses the "1.999,00" locale trap, the meta-lock atomic-update pattern the ledger will
reuse round-trips, and the gate already stamps its write facts (the minted Idempotency-Key) onto
step traces.

Everything here is requires="none": mandate verification is deterministic AT THE GATE by design
(the red-team literature — mandates don't protect the LLM decision layer — is exactly why it must
never consult a model), so an LLM tier would test nothing this horizon claims.
"""

from __future__ import annotations

import http.server
import threading

from evals.core import Ctx, expect, fail, import_probe, missing, probe, scenario
from evals.fixtures import Fixture, page


# --- scripted pieces (key-less: no LLM, no network beyond localhost) ---------------------------
def _mock_router():
    """A Router backed by MockClient: `learn()` builds a REAL provider router when none is passed
    (which needs an API key), so a scripted one is always injected. The write flows here extract
    nothing, so the canned response is never even consulted."""
    from ultracua.llm.base import Router, Tier
    from ultracua.llm.mock import MockClient

    mc = MockClient(actions=[{"found": True, "data": None}], tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


class _PlaceOrder:
    """Scripted key-less 'agent' for a write flow: click the order submit button once, then done
    (the confirmation page has no matching button, so the second decide() falls through to done)."""

    def __init__(self) -> None:
        self._clicked = False

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        if not self._clicked:
            for el in obs.elements:
                if el.role == "button" and "order" in (el.name or "").lower():
                    self._clicked = True
                    return Action(action="click", intent="place the order", ref=el.ref), None
        return Action(action="done", intent="done"), None


class _OpenReport:
    """Scripted key-less 'agent' for a read flow: click the first link, then declare done."""

    def __init__(self) -> None:
        self._clicked = False

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        if not self._clicked:
            for el in obs.elements:
                if el.role == "link":
                    self._clicked = True
                    return Action(action="click", intent="open the invoices report", ref=el.ref), None
        return Action(action="done", intent="done"), None


# The vendor-portal write shape H14's near-term task class names (invoice approval / order
# placement): a REAL method=post form, so the submit click is classified mutating by the form's
# METHOD (the structural signal) and replays through the mutation gate a mandate would hook.
_CHECKOUT = page('<h1>Checkout</h1><p id="total">total: EUR 1,999</p>'
                 '<form method="post" action="/order">'
                 '<input type="hidden" name="qty" value="1">'
                 '<button type="submit">Place the order</button></form>')
_CONFIRM = page("<h1>Order placed</h1><p>Confirmation #777</p>", title="confirm")


# --- H14 plan step 1: the signed-mandate format + operator verbs --------------------------------
@scenario(
    id="h14.mandate.core_surfaces",
    title="mandate core: ultracua.mandate, FlowSpec.mandate, `flow mandate issue|verify` verbs",
    group="h14", aspirational=True, tags=("mandate", "writes"),
)
async def mandate_core_surfaces(ctx: Ctx):
    import json

    import ultracua.flows as flows_mod
    from ultracua.flows import FlowSpec, MutateSpec, load_spec, save_spec

    checks = []
    # Capability (plan step 1): a mandate module — Mandate dataclass (scope pattern, per-write cap,
    # cumulative cap, validity window, max writes, key id), canonical byte serialization, Ed25519
    # sign/verify. The whole Layer-1 program hangs off this import.
    has_mod, mod = import_probe("ultracua.mandate")
    checks.append(expect(has_mod,
                         "ultracua.mandate exists (Mandate + canonical bytes + Ed25519 sign/verify)",
                         f"{type(mod).__name__}: {mod}", aspirational=True))
    # Capability: a flow CARRIES its grant — the planned optional FlowSpec.mandate field. FlowSpec
    # is a dataclass, so an unexpected kwarg raises TypeError -> `missing` via probe().
    status, exc = await probe(FlowSpec, name="pay", start_url="http://127.0.0.1/x",
                              goal="pay the invoice", mandate={"per_write_cap": "EUR 2000"})
    checks.append(expect(status == "ok", "FlowSpec accepts a mandate=... grant",
                         f"{type(exc).__name__}: {exc}", aspirational=True))
    # Capability: the operator verbs — `flow mandate issue|verify`, planned as flows.py functions
    # backing cli.py subparsers (so an approver can mint/check a grant without writing code).
    verbs = any(callable(getattr(flows_mod, n, None)) for n in
                ("mandate_issue", "issue_mandate", "mandate_verify", "verify_mandate")) or \
        (has_mod and any(callable(getattr(mod, n, None)) for n in ("issue", "verify", "sign")))
    checks.append(expect(verbs, "mandate issue/verify verbs exist (flows functions behind the CLI)",
                         "no mandate verbs in ultracua.flows (or ultracua.mandate)",
                         aspirational=True))
    # PARTIAL CREDIT (shipped): the plan claims FlowSpec.mandate lands forward-compat FREE via
    # flows._only_known. Prove that precondition end-to-end TODAY: a spec file written by a
    # mandate-aware FUTURE version (extra "mandate" key) still loads with its write-confirm
    # declaration intact, instead of raising and bricking the saved flow. The specs dir is
    # cwd-relative by design, so it is redirected into ctx.tmp (never the repo's .ultracua/).
    orig = flows_mod._specs_dir
    flows_mod._specs_dir = lambda: ctx.tmp / "specs"
    try:
        spec = FlowSpec(name="mandate-fwd", start_url="http://127.0.0.1/x", goal="pay the invoice",
                        mutate=MutateSpec(confirm_text_contains="Paid"))
        p = save_spec(spec)
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw["mandate"] = {"key_id": "k1", "per_write_cap": "EUR 2000", "sig": "ed25519:..."}
        p.write_text(json.dumps(raw), encoding="utf-8")
        got = load_spec("mandate-fwd")
        checks.append(expect(got.name == "mandate-fwd" and got.mutate is not None
                             and got.mutate.confirm_text_contains == "Paid",
                             "a spec from a mandate-aware future version still loads (forward-compat seam)",
                             "the unknown mandate field broke load_spec"))
    finally:
        flows_mod._specs_dir = orig
    return checks


# --- H14 plan step 2: the deterministic enforcement point (shipped) + the mandate hook ----------
@scenario(
    id="h14.gate.enforcement_point",
    title="shipped enforcement point: a write flow refuses an unapproved replay BEFORE actuation",
    group="h14", tags=("mandate", "writes", "gate"),
)
async def gate_enforcement_point(ctx: Ctx):
    import inspect

    from ultracua.flows import FlowReplayError, FlowSpec, MutateSpec, approve, learn, replay

    checks = []
    fx = Fixture({"/checkout": _CHECKOUT, "/confirm": _CONFIRM}, post_redirect="/confirm")
    with fx.serve() as base:
        cache = ctx.cache()
        spec = FlowSpec(name="mandate-order", start_url=f"{base}/checkout", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Order placed"), headless=True)
        res = await learn(spec, provider=_PlaceOrder(), router=_mock_router(), cache=cache)
        checks.append(expect(res.cached and res.found and len(fx.writes) == 1,
                             "learn lands exactly one confirmed write",
                             f"cached={res.cached} found={res.found} writes={len(fx.writes)}"))
        # PARTIAL CREDIT (shipped): H14's Layer-1 bet is that the enforcement POINT already exists —
        # "mandates are approval gates made portable/parameterized/signed". Prove the primitive: a
        # write flow refuses replay under standing authority it does not have (writes are
        # approval-gated by DEFAULT, no opt-in flag needed).
        try:
            out = await replay(spec, cache=cache)
            checks.append(fail("unapproved write replay refuses LOUD", f"silently returned {out!r}"))
        except FlowReplayError as exc:
            checks.append(expect("approv" in str(exc).lower(), "unapproved write replay refuses LOUD",
                                 f"raised, but the message names no approval: {exc}"))
        # ...and the refusal happens BEFORE actuation — the exact pre-release point a mandate's
        # bound checks must sit at. Server-side oracle: no second write ever arrived.
        checks.append(expect(len(fx.writes) == 1, "refusal happened BEFORE actuation (no write left)",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
        # Deterministic release: once authority is granted, the SAME gate releases exactly one
        # write and verifies it landed (Phase-D confirm) — signed grants swap in at this hinge.
        approve(spec, cache=cache)
        result = await replay(spec, cache=cache)
        checks.append(expect(isinstance(result, dict) and result.get("status") == "confirmed"
                             and len(fx.writes) == 2,
                             "approved replay releases EXACTLY one write and confirms it",
                             f"result={result!r} writes={len(fx.writes)}"))
        # Capability (plan step 2): replay takes the signed grant itself for gate verification.
        # Signature inspection, not a live call — deterministic in every future (a call probe would
        # conflate "kwarg rejected" with unrelated replay errors).
        checks.append(expect("mandate" in inspect.signature(replay).parameters,
                             "replay accepts a mandate=... grant to verify at the gate",
                             "replay() has no mandate channel", aspirational=True))
    return checks


# --- H14 plan step 2 (amount side): MutateSpec.amount_pin + strict currency parsing -------------
@scenario(
    id="h14.amount.pin_strict",
    title="amount caps: MutateSpec.amount_pin + strict currency parse (ambiguity refuses)",
    group="h14", aspirational=True, tags=("mandate", "amount", "fail-loud"),
)
async def amount_pin_strict(ctx: Ctx):
    import inspect

    import ultracua.flows as flows_mod
    import ultracua.pin as pin_mod
    import ultracua.safety as safety_mod
    from ultracua.locators import resolve

    checks = []
    # Capability (plan step 2): the cap binding site — MutateSpec.amount_pin, a strict 0-LLM pinned
    # read of the on-page amount the gate compares against the mandate's per-write cap before the
    # write releases. MutateSpec is a dataclass: unexpected kwarg -> TypeError -> `missing`.
    status, exc = await probe(flows_mod.MutateSpec, confirm_text_contains="Paid",
                              amount_pin={"selector": "#total", "currency": "EUR"})
    checks.append(expect(status == "ok", "MutateSpec accepts amount_pin=... (the cap binding site)",
                         f"{type(exc).__name__}: {exc}", aspirational=True))
    # Capability: a strict currency-amount parser (exact expected-currency token match; any locale
    # or format ambiguity refuses — the H14 risk list: a lenient parser is worse than no mandate).
    has_mod, mod = import_probe("ultracua.mandate")
    mods = [safety_mod, pin_mod, flows_mod] + ([mod] if has_mod else [])
    names = ("parse_amount", "amount_of", "parse_money", "parse_currency", "strict_amount")
    checks.append(expect(any(callable(getattr(m, n, None)) for m in mods for n in names),
                         "a strict currency-amount parse surface exists",
                         "no amount parser in safety/pin/flows (or ultracua.mandate)",
                         aspirational=True))
    # PARTIAL CREDIT (shipped): the refusal POSTURE amount_pin is planned to reuse — pin._parse
    # accepts exactly ONE well-formed numeric token and refuses everything else, including the
    # risk-list locale trap ("1.999,00" as an int must refuse, never mis-read and approve an
    # over-cap write) and a two-number blob (subtotal alongside total).
    p = pin_mod._parse
    strict = (p("Total: 1,999", "int") == 1999
              and p("1999 or 2000", "int") is None
              and p("1.999,00", "int") is None)
    checks.append(expect(strict, "pin's strict single-token parse refuses ambiguity + the locale trap",
                         f"got {p('Total: 1,999', 'int')!r} / {p('1999 or 2000', 'int')!r} / "
                         f"{p('1.999,00', 'int')!r}"))
    # PARTIAL CREDIT (shipped): the resolution primitive the pin binds with — locators.resolve
    # exposes unique=True (an ambiguous target NEVER binds `.first`), the same fail-on-ambiguity
    # contract the mutation gate already runs on. The plan binds amount_pin via resolve(unique=True).
    checks.append(expect("unique" in inspect.signature(resolve).parameters,
                         "resolve(unique=True) exists (fail-loud ambiguity contract for the pin)",
                         f"resolve params: {tuple(inspect.signature(resolve).parameters)}"))
    return checks


# --- H14 plan steps 3-4: reserve-then-commit spend ledger + idempotency basis widening ----------
@scenario(
    id="h14.ledger.reserve_commit",
    title="spend ledger: reserve-then-commit + fail-closed suspension; the locked meta substrate",
    group="h14", aspirational=True, tags=("mandate", "ledger", "writes", "idempotency"),
)
async def ledger_reserve_commit(ctx: Ctx):
    import dataclasses

    import ultracua.flows as flows_mod
    from ultracua.flows import FlowMeta, _load_meta, _update_meta
    from ultracua.safety import idempotency_key

    checks = []
    # Capability (plan step 3): a per-mandate spend ledger with reserve-then-commit around each
    # write, so a crash between write-fire and ledger-commit can never under-count spend (the
    # conscious revision of MutateSpec's documented no-durable-ledger stance, mandate flows only).
    has_mod, mod = import_probe("ultracua.mandate")
    mods = [flows_mod] + ([mod] if has_mod else [])
    names = ("SpendLedger", "spend_ledger", "ledger_reserve", "reserve_spend", "Ledger")
    checks.append(expect(any(getattr(m, n, None) is not None for m in mods for n in names),
                         "a spend-ledger surface exists (reserve-then-commit)",
                         "no ledger surface in ultracua.flows / ultracua.mandate", aspirational=True))
    # Capability: fail-CLOSED suspension — a mandate stranded between fire and commit is suspended
    # pending human reconcile (a durable state + an operator verb, wherever they land).
    meta_fields = {f.name for f in dataclasses.fields(FlowMeta)}
    suspended = ("suspended" in meta_fields) or any(
        callable(getattr(m, n, None)) for m in mods
        for n in ("suspend", "reconcile", "suspend_mandate", "reconcile_mandate"))
    checks.append(expect(suspended, "a suspension/reconcile state exists (fail closed after a crash)",
                         f"FlowMeta fields: {sorted(meta_fields)}", aspirational=True))
    # PARTIAL CREDIT (shipped): the persistence substrate the ledger is PLANNED on — the meta
    # sidecar's cross-process-locked, atomic-replace read-modify-write. Two sequential updates
    # round-trip without loss in a scenario-local cache (never the repo's .ultracua/).
    cache = ctx.cache()
    _update_meta(cache, "cafed00d", lambda m: setattr(m, "runs", m.runs + 1))
    _update_meta(cache, "cafed00d", lambda m: setattr(m, "runs", m.runs + 1))
    checks.append(expect(_load_meta(cache, "cafed00d").runs == 2,
                         "the locked atomic meta-update pattern round-trips (the ledger's substrate)",
                         f"runs={_load_meta(cache, 'cafed00d').runs}"))
    # PARTIAL CREDIT (shipped): the dedupe basis the widening must NOT destabilize — the same
    # (scope, step, intent) mints the same key on every run, so a same-write retry can never
    # double-spend behind a server-side dedupe.
    k1 = idempotency_key("flow:pay", 1, "pay the invoice")
    k2 = idempotency_key("flow:pay", 1, "pay the invoice")
    checks.append(expect(k1 == k2 and k1.startswith("uca-"),
                         "idempotency-key derivation is deterministic (the documented current basis)",
                         f"{k1} vs {k2}"))
    # Capability (plan step 4): the basis WIDENS to include mandate_id (+ bound slot values) so two
    # DIFFERENT mandated payments never share a dedupe key — distinct invoices suppressed as
    # duplicates is the silent-missing-write trap. unexpected-kwarg TypeError == not built yet.
    st, out = await probe(idempotency_key, "flow:pay", 1, "pay the invoice", mandate_id="m-1")
    if st == "ok":
        st2, out2 = await probe(idempotency_key, "flow:pay", 1, "pay the invoice", mandate_id="m-2")
        checks.append(expect(st2 == "ok" and out != out2,
                             "mandate_id enters the idempotency basis (distinct mandates, distinct keys)",
                             f"{out} vs {out2}", aspirational=True))
    else:
        checks.append(missing("mandate_id enters the idempotency basis (distinct mandates, distinct keys)",
                              f"{type(out).__name__}: {out}"))
    return checks


# --- H14 Layer-2 core slice: x402 / HTTP-402 detect-and-escalate --------------------------------
_HOME = page('<a href="/answer">open the invoices report</a>')
_ANSWER = page('<h1>Invoices</h1><p>3 open invoices</p>', title="invoices")
_CAPTCHA = page('<h1>Security check</h1><p>Checking your browser before accessing — '
                'verify you are human.</p>', title="Just a moment")
_PAYWALL = page('<h1>402 Payment Required</h1><p>This resource requires payment: settle via the '
                'x402 protocol to continue.</p>', title="402 Payment Required")


def _serve_status_pages(state: dict) -> tuple:
    """Fixture-shaped local server whose GET routes carry a mutable (status, html) pair.
    evals/fixtures.py always answers 200, and the x402 probe needs a REAL HTTP 402 status line (a
    future detector may key on the status, not just page text), so extend locally per the eval
    ground rules instead of modifying the shared fixture."""

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:  # keep eval output clean
            pass

        def do_GET(self) -> None:  # noqa: N802
            entry = state.get(self.path.split("?")[0])
            if entry is None:
                self.send_error(404)
                return
            code, html = entry
            body = html.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@scenario(
    id="h14.x402.detect_escalate",
    title="x402/HTTP-402 detect-and-escalate: shipped escalate channel; 402 recognition probed",
    group="h14", aspirational=True, tags=("x402", "escalate", "interstitial"),
)
async def x402_detect_escalate(ctx: Ctx):
    from ultracua.flow import run_cached
    from ultracua.safety import looks_like_interstitial

    checks = []
    state = {"/": (200, _HOME), "/answer": (200, _ANSWER)}
    httpd, base = _serve_status_pages(state)
    try:
        cache = ctx.cache()
        goal = "open the invoices report"
        learned = await run_cached(base + "/", goal, _OpenReport(), cache, mode="learn", headless=True)
        checks.append(expect(learned.success, "baseline read flow learns on the fixture",
                             f"note={learned.note!r}"))
        # PARTIAL CREDIT (shipped): the DISTINCT escalation channel 402-detection plugs into. A
        # CAPTCHA interstitial on replay must terminate as mode="escalate" (hand off to a human),
        # never a silent success and never a retry burn — H14 demotes x402 to exactly this shape.
        state["/"] = (200, _CAPTCHA)
        rep = await run_cached(base + "/", goal, None, cache, mode="replay", headless=True)
        checks.append(expect(rep.mode == "escalate" and not rep.success,
                             "a CAPTCHA interstitial escalates distinctly (mode=escalate, not success)",
                             f"mode={rep.mode} success={rep.success} note={rep.note!r}"))
        # Capability: an HTTP-402 payment wall is ALSO an interstitial — replay should escalate
        # distinctly ("a human must decide whether to pay"), never burn retries as generic drift.
        # Today the paywall matches no INTERSTITIAL_SIGNALS entry, so it dies a generic replay
        # failure — indistinguishable from ordinary page drift in the report.
        state["/"] = (402, _PAYWALL)
        rep2 = await run_cached(base + "/", goal, None, cache, mode="replay", headless=True)
        checks.append(expect(rep2.mode == "escalate",
                             "an HTTP-402/x402 paywall escalates distinctly on replay",
                             f"mode={rep2.mode} note={rep2.note!r} (402 is not a recognized interstitial)",
                             aspirational=True))
        # ...and the classifier itself: safety.looks_like_interstitial is the plan's named
        # extension point (detect-and-escalate only, never in-core stablecoin custody).
        checks.append(expect(
            looks_like_interstitial(base + "/", "402 Payment Required",
                                    "Payment required: settle via the x402 protocol to continue."),
            "looks_like_interstitial recognizes payment-required/x402 signals",
            "no 402/x402/payment-required entries in INTERSTITIAL_SIGNALS", aspirational=True))
    finally:
        httpd.shutdown()
        httpd.server_close()
    return checks


# --- H14 plan step 5: signed evidence packs (submission-side facts, offline verify) -------------
@scenario(
    id="h14.evidence.packs",
    title="evidence packs: offline-verifiable submission-side packs; the gate's trace facts today",
    group="h14", aspirational=True, tags=("mandate", "evidence", "audit"),
)
async def evidence_packs(ctx: Ctx):
    import dataclasses
    import inspect

    import ultracua.flows as flows_mod
    from ultracua.flow import FlowReport, run_cached

    checks = []
    # Capability (plan step 5): a signed per-run evidence pack + a `flow evidence verify` offline
    # verb. Packs must assert SUBMISSION-side facts only (what ultracua submitted under the
    # mandate, never what the merchant settled — claiming settlement is the silently-wrong-audit
    # trap the risk list pins).
    has_mod, mod = import_probe("ultracua.mandate")
    has_ev, ev_mod = import_probe("ultracua.evidence")
    mods = [flows_mod] + [m for present, m in ((has_mod, mod), (has_ev, ev_mod)) if present]
    names = ("evidence_verify", "verify_evidence", "EvidencePack", "evidence_pack")
    checks.append(expect(any(getattr(m, n, None) is not None for m in mods for n in names),
                         "an evidence-pack surface exists (emit + offline verify)",
                         "no evidence surface in flows / ultracua.mandate / ultracua.evidence",
                         aspirational=True))
    # PARTIAL CREDIT (shipped): the two emission seams the plan names — the on_step(StepTrace)
    # callback and FlowReport.extra — both exist today for a pack builder to ride.
    sig = inspect.signature(run_cached)
    report_fields = {f.name for f in dataclasses.fields(FlowReport)}
    checks.append(expect("on_step" in sig.parameters and "extra" in report_fields,
                         "the on_step seam + FlowReport.extra exist (the pack's emission points)",
                         f"on_step={'on_step' in sig.parameters} extra={'extra' in report_fields}"))

    # PARTIAL CREDIT (shipped, measured on a real replayed write): the mutation gate already stamps
    # a mandate-to-write binding fact onto the write step's trace — the minted Idempotency-Key —
    # and releases exactly one write (server as oracle). Engine-level replay is used because the
    # trace stream is the engine's surface; the approval gate itself is proven in h14.gate.*.
    fx = Fixture({"/checkout": _CHECKOUT, "/confirm": _CONFIRM}, post_redirect="/confirm")
    with fx.serve() as base:
        cache = ctx.cache()
        goal = "place the order"
        learned = await run_cached(base + "/checkout", goal, _PlaceOrder(), cache,
                                   mode="learn", headless=True)
        rep = await run_cached(base + "/checkout", goal, None, cache, mode="replay", headless=True)
        muts = [t for t in rep.step_traces if t.meta.get("mutating")]
        checks.append(expect(
            learned.success and rep.success and len(muts) == 1 and len(fx.writes) == 2
            and str(muts[0].meta.get("idempotency_key", "")).startswith("uca-"),
            "the gate stamps its write facts (Idempotency-Key) on the trace and releases ONE write",
            f"learn={learned.success} replay={rep.success} gated_traces={len(muts)} "
            f"writes={len(fx.writes)} meta={muts[0].meta if muts else None}"))
        # Capability: the SUBMISSION-side fingerprints a pack must record — the pre/post scope
        # fingerprints the gate compares internally (what the form looked like when the write
        # released) — surfaced on the trace/report for a pack to sign. Today they stay internal.
        exposed = bool(muts and any(("scope" in k or "fingerprint" in k) for k in muts[0].meta)) \
            or ("evidence" in rep.extra)
        checks.append(expect(exposed,
                             "the gate exposes pre/post scope fingerprints for a pack to record",
                             f"trace meta keys today: {sorted(muts[0].meta) if muts else []}",
                             aspirational=True))
    return checks
