"""H4 evals: in-profile capture & replay — extension CDP relay + BrowserSession attach mode.

ROADMAP H4: record and replay in the user's real Chrome profile via an MV3 extension acting as
a dumb CDP relay, so flows behind SSO/2FA/hardware keys inherit the live session with NO plaintext
storage_state export. The planned surfaces (probed aspirationally here): an `ultracua.attach`
relay module, `BrowserSession(cdp_endpoint=...)` attach mode (hard-failing unsafe context-option
combos), a profile-login precondition verb reusing `LoginSpec` success checks (`refresh_auth`
refuses over attach), and writes-over-attach default-DENY behind an explicit FlowSpec opt-in.

Partial credit measured today (the building blocks attach mode is specified to ride on):
- `BrowserSession(browser=shared)` non-owning lifecycle (browser.py `_owns_browser=False` path)
- `storage_state` save/seed round-trip (the artifact attach mode is meant to make unnecessary)
- `LoginSpec` success checks + fail-loud `refresh_auth` + the Phase-G `StepConfirm` barrier

Everything here is key-less: local Fixture pages, real headless Chromium, $0.
"""

from __future__ import annotations

from evals.core import Ctx, expect, import_probe, missing, ok, probe, scenario, skip
from evals.fixtures import Fixture, page

# A syntactically-valid CDP websocket endpoint that is never connected to: every probe below
# either fails at the constructor (kwarg not shipped -> TypeError -> `missing`) or would be
# rejected before any network I/O. Nothing in this module dials out.
_FAKE_CDP_WS = "ws://127.0.0.1:9/devtools/browser/00000000-0000-0000-0000-000000000000"


@scenario(
    id="h04.attach.cdp_endpoint_mode",
    title="BrowserSession attach mode (cdp_endpoint) + ultracua.attach relay module",
    group="h04", aspirational=True, tags=("attach", "cdp"),
    notes="H4 plan steps 1-2: attach.py relay + BrowserSession(cdp_endpoint=...) with unsafe-combo hard-fail",
)
async def cdp_endpoint_mode(ctx: Ctx):
    from ultracua.browser import BrowserSession

    checks = []

    # PARTIAL CREDIT: the Playwright entry point the attach mode is specified to call
    # (chromium.connect_over_cdp against the relay's emulated browser-level endpoint) already
    # ships in python-playwright — the transport primitive exists, only ultracua's use is missing.
    from playwright.async_api import BrowserType

    checks.append(expect(hasattr(BrowserType, "connect_over_cdp"),
                         "Playwright connect_over_cdp entry point exists (attach transport primitive)"))

    # The localhost CDP relay module (H4 plan step 1: emulates the browser-level handshake the
    # way playwright-mcp's cdp-relay does). Not built yet -> `missing`.
    found, _ = import_probe("ultracua.attach")
    checks.append(expect(found, "ultracua.attach relay module imports",
                         "no attach relay module yet", aspirational=True))

    # Attach mode on the session itself (H4 plan step 2): a `cdp_endpoint` ctor arg routing
    # start() through connect_over_cdp instead of launch(). Today the kwarg is unexpected ->
    # TypeError -> `missing` (the ctor never dials, so this probe is network-free either way).
    st, val = await probe(lambda: BrowserSession(cdp_endpoint=_FAKE_CDP_WS))
    checks.append(expect(st == "ok", "BrowserSession accepts cdp_endpoint (attach mode)",
                         f"probe={st}: {val}", aspirational=True))

    # Safety spec for the same slice: attach + storage_state must HARD-FAIL (context-creation
    # options can't apply to a relayed default context — silently ignoring auth seeding would
    # violate never-silently-wrong). Distinguish the three worlds explicitly:
    st2, val2 = await probe(lambda: BrowserSession(cdp_endpoint=_FAKE_CDP_WS,
                                                   storage_state=str(ctx.tmp / "state.json")))
    if st2 == "missing":
        checks.append(missing("attach + storage_state combo hard-fails",
                              "no attach mode yet (cdp_endpoint kwarg absent)"))
    elif st2 == "error":  # attach exists AND the unsafe combo is refused loudly — the spec'd behavior
        checks.append(ok("attach + storage_state combo hard-fails", f"refused: {type(val2).__name__}"))
    else:  # ctor accepted the combo — the hard-fail may legitimately live in start(); don't guess
        checks.append(skip("attach + storage_state combo hard-fails",
                           "ctor accepted the combo — needs a start()-level check once attach ships"))
    return checks


@scenario(
    id="h04.attach.shared_browser_non_owning",
    title="BrowserSession(browser=shared) runs as a context in a caller-owned browser and never closes it",
    group="h04", tags=("attach", "lifecycle"),
    notes="shipped building block: the _owns_browser=False non-lifecycle path H4's attach mode extends",
)
async def shared_browser_non_owning(ctx: Ctx):
    from playwright.async_api import async_playwright

    from ultracua.browser import BrowserSession

    checks = []
    fx = Fixture({"/": page('<h1 id="who">profile home</h1>', title="profile")})
    # The caller owns the browser — exactly the shape of an attach: ultracua joins a browser it
    # did not launch and MUST NOT tear down (closing the user's real Chrome would be catastrophic).
    pw = await async_playwright().start()
    try:
        shared = await pw.chromium.launch(headless=True)
        try:
            with fx.serve() as base:
                s1 = await BrowserSession(browser=shared).start()
                try:
                    await s1.goto(base + "/")
                    obs = await s1.snapshot()
                    # The full observation pipeline works over a session ultracua doesn't own —
                    # attach-mode replay reuses this path unchanged.
                    checks.append(expect(obs.url.startswith(base) and "profile" in obs.title,
                                         "navigate + snapshot work inside a caller-owned browser",
                                         f"url={obs.url} title={obs.title!r}"))
                finally:
                    await s1.close()
                # THE non-owning invariant: close() tore down only the session's context; the
                # shared browser (stand-in for the user's real profile) must survive.
                checks.append(expect(shared.is_connected(),
                                     "session.close() left the shared browser alive (non-owning)"))
                checks.append(expect(len(shared.contexts) == 0,
                                     "session.close() cleaned up its own context",
                                     f"leftover contexts={len(shared.contexts)}"))
                # Browser reuse across flows: a second session attaches to the SAME browser after
                # the first closed — the daemon/replay-many pattern attach mode must support.
                s2 = await BrowserSession(browser=shared).start()
                try:
                    await s2.goto(base + "/")
                    checks.append(expect(s2.page.url.startswith(base),
                                         "a second session attaches to the same browser after the first closed"))
                finally:
                    await s2.close()
        finally:
            await shared.close()
    finally:
        await pw.stop()
    return checks


@scenario(
    id="h04.auth.storage_state_seeding",
    title="storage_state round-trip: save cookies+localStorage, seed a fresh session already authed",
    group="h04", tags=("attach", "auth"),
    notes="shipped building block — and the plaintext-cookie artifact H4's in-profile attach eliminates",
)
async def storage_state_seeding(ctx: Ctx):
    import json

    from ultracua.browser import BrowserSession

    checks = []
    state = ctx.tmp / "state.json"
    fx = Fixture({"/": page('<h1 id="who">anon</h1>', title="auth")})
    with fx.serve() as base:
        # Session 1: acquire "auth" (a cookie + a localStorage token, the two stores a real
        # login leaves behind) and persist it — today's cookie-export auth path.
        s1 = await BrowserSession(headless=True).start()
        try:
            await s1.goto(base + "/")
            await s1.page.evaluate("document.cookie = 'sid=hello42; path=/'")
            await s1.page.evaluate("localStorage.setItem('token', 'tok-7')")
            await s1.save_storage_state(str(state))
        finally:
            await s1.close()
        # The artifact is a PLAINTEXT cookie file — works, but is exactly what H4's no-export
        # attach mode exists to kill. Verifying it here anchors the "before" side of that trade.
        blob = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
        checks.append(expect(state.exists() and "hello42" in state.read_text(encoding="utf-8"),
                             "save_storage_state persists the session cookie (plaintext artifact)",
                             f"keys={sorted(blob)}"))
        # Session 2: seed a FRESH session from the file — replay must start already logged in
        # without re-driving a login form (the recurring-flow auth contract).
        s2 = await BrowserSession(headless=True, storage_state=str(state)).start()
        try:
            await s2.goto(base + "/")
            cookie = await s2.page.evaluate("document.cookie")
            token = await s2.page.evaluate("localStorage.getItem('token')")
        finally:
            await s2.close()
        checks.append(expect("sid=hello42" in (cookie or ""),
                             "a seeded session starts with the cookie already present", f"cookie={cookie!r}"))
        checks.append(expect(token == "tok-7",
                             "a seeded session starts with localStorage already present", f"token={token!r}"))
    return checks


@scenario(
    id="h04.precondition.profile_login_probe",
    title="profile-login precondition verb (pre-replay logged-in probe; refresh_auth refuses over attach)",
    group="h04", aspirational=True, tags=("attach", "auth", "fail-loud"),
    notes="H4 plan step 4: reuse LoginSpec success checks as a fail-loud pre-replay probe over attach",
)
async def profile_login_probe(ctx: Ctx):
    import ultracua.flows as flows
    from ultracua.flows import FlowSpec, LoginSpec, refresh_auth

    checks = []

    # PARTIAL CREDIT: the declarative success checks the precondition verb is specified to reuse
    # (LoginSpec.success_selector / success_url_contains) already exist on the spec.
    spec_fields = LoginSpec.__dataclass_fields__
    checks.append(expect("success_selector" in spec_fields and "success_url_contains" in spec_fields,
                         "LoginSpec carries the success checks the precondition probe will reuse"))

    # PARTIAL CREDIT: the logged-in decision primitive exists today as the private helper
    # _login_succeeded (page x LoginSpec -> bool) — H4 wraps it into a public pre-replay verb.
    checks.append(expect(hasattr(flows, "_login_succeeded"),
                         "logged-in success-check primitive exists (flows._login_succeeded)"))

    # The public precondition verb itself (probe the profile is logged in BEFORE replaying over
    # attach; on failure escalate 'log in in your browser, then retry'). Not built yet -> missing.
    verb_names = ("probe_login", "login_precondition", "check_login", "precondition_login")
    checks.append(expect(any(hasattr(flows, n) for n in verb_names),
                         "public profile-login precondition verb exists",
                         f"none of {verb_names} found", aspirational=True))

    # refresh_auth must REFUSE over attach — never drive a form login inside the user's real
    # profile. Today the kwarg is unexpected -> TypeError before any browser work -> missing.
    spec = FlowSpec(name="h04-probe", start_url="http://127.0.0.1:9/", goal="probe",
                    login=LoginSpec(url="http://127.0.0.1:9/login"),
                    storage_state=str(ctx.tmp / "s.json"))
    st, val = await probe(refresh_auth, spec, over_attach=True)
    if st == "missing":
        checks.append(missing("refresh_auth refuses over attach", "no attach-aware refresh_auth yet"))
    elif st == "error" and isinstance(val, flows.FlowReplayError):
        checks.append(ok("refresh_auth refuses over attach", f"refused: {val}"))
    else:  # some other outcome once attach ships — don't guess pass/fail from a probe kwarg name
        checks.append(skip("refresh_auth refuses over attach", f"ambiguous probe result: {st}"))

    # PARTIAL CREDIT (shipped fail-loud): refresh_auth with no login configured refuses LOUDLY
    # instead of silently doing nothing — the failure contract the attach refusal will extend.
    st2, val2 = await probe(refresh_auth, FlowSpec(name="h04-nologin", start_url="http://127.0.0.1:9/",
                                                   goal="probe"))
    checks.append(expect(st2 == "error" and isinstance(val2, flows.FlowReplayError),
                         "refresh_auth fails loud when no login is configured",
                         f"probe={st2}: {val2}"))
    return checks


@scenario(
    id="h04.writes.attach_write_policy",
    title="writes over attach are default-DENY behind an explicit FlowSpec opt-in; recorder rides the relay",
    group="h04", aspirational=True, tags=("attach", "writes", "write-safety"),
    notes="H4 plan steps 3+5: in-profile record transport + default-deny writes + detach->Phase-G barrier",
)
async def attach_write_policy(ctx: Ctx):
    import inspect

    from ultracua.browser import BrowserSession
    from ultracua.flows import FlowSpec

    checks = []

    # The explicit writes-over-attach opt-in on FlowSpec (concurrent human input can double-write,
    # so mutating flows in the user's live profile must be default-DENY). Field scan instead of a
    # ctor probe: immune to unrelated ctor errors, and a dataclass can't hide a field.
    optin_names = ("allow_writes_over_attach", "writes_over_attach", "attach_writes")
    checks.append(expect(any(n in FlowSpec.__dataclass_fields__ for n in optin_names),
                         "FlowSpec has a writes-over-attach opt-in field",
                         f"none of {optin_names} on FlowSpec", aspirational=True))

    # In-profile RECORDING transport (H4 plan step 3): record_demo / flows.record grow an attach
    # transport (cdp_endpoint or a caller-passed attached session). Signature scan — no browser.
    from ultracua.flows import record as flows_record
    from ultracua.recorder import record_demo

    attach_params = ("cdp_endpoint", "attach", "session")
    rd = inspect.signature(record_demo).parameters
    checks.append(expect(any(p in rd for p in attach_params),
                         "record_demo accepts an attach transport",
                         f"params={sorted(rd)}", aspirational=True))
    fr = inspect.signature(flows_record).parameters
    checks.append(expect(any(p in fr for p in attach_params),
                         "flows.record accepts an attach transport",
                         f"params={sorted(fr)}", aspirational=True))

    # PARTIAL CREDIT: the idempotency-header injection surface exists (context-level today).
    # Over a true profile attach, "context" = the user's whole profile, so H4 must verify this is
    # tab-scoped through the relay or refuse mutating flows — the surface under test ships now.
    checks.append(expect(hasattr(BrowserSession, "set_extra_http_headers"),
                         "idempotency-header surface exists (set_extra_http_headers)"))

    # PARTIAL CREDIT: the Phase-G per-write barrier (StepConfirm) exists — the mechanism a
    # mid-write debugger detach (user clicks the infobar Cancel) is specified to fail through
    # as an unconfirmed-write failure rather than a silent maybe-committed write.
    found, cache_mod = import_probe("ultracua.cache")
    checks.append(expect(found and hasattr(cache_mod, "StepConfirm"),
                         "Phase-G StepConfirm barrier exists (detach-mid-write failure path)"))
    return checks
