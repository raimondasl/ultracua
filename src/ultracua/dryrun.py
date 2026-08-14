"""H5 dry-run replay — the network arbiter that HOLDS every write.

A dry run replays an approved-or-not write flow with the mutation gate fully armed, but no write ever
leaves the browser. The point is a pre-approval artifact: the exact POST bodies this flow *would* send,
reviewable before `approve()`, on a site with no staging environment.

THE GUARANTEE IS THE FEATURE. A dry run that writes for real is worse than no dry run at all, so the design
rule throughout is: **anything unprovably held aborts**. Every claim below was measured against Playwright
1.60.0 + headless Chromium with a local server counting the bytes that actually arrived — not read from
documentation.

FOUR MEASURED DECISIONS, each of which deletes a way this could silently write:

1. `continue_()` IS REACHABLE ONLY FOR GET / HEAD / OPTIONS.
   The obvious design holds `safety.is_write_request` matches and continues everything else — which means
   a POST to a known telemetry host is released to the network. That is not a rider, it is an
   arbitrary-write channel: a continued POST answered with a **method-preserving redirect (307/308)**
   spawns a second request `context.route` NEVER SEES, and it arrives with the full body. Measured:
       POST -> 307 -> POST      1 of 2 hops routed, BOTH reached the server
       POST -> 307 -> 307 -> POST   1 of 3 routed, ALL THREE reached the server
       form-POST navigation -> 307  1 of 2 routed, both reached
   So any host on the telemetry list could convert a "dry" run into a real write to any endpoint it names,
   with the page's own body, and the report would show nothing.
   The fix is policy, and it makes the invariant machine-checkable: telemetry classification decides
   RECORD-vs-ABORT, never HOLD-vs-RELEASE. It is airtight against redirect-born writes because no redirect
   status turns a GET into a POST (302/303 degrade toward GET; 307/308 preserve) — and a request that never
   reaches a server cannot be redirected by one.

2. `context.route`, NEVER `page.route`.
   A service-worker-originated `fetch(POST)` is delivered to `context.route` and held; under `page.route`
   it is not seen and REACHES THE SERVER. The choice is a write-safety guarantee, not a style preference.
   `service_workers="block"` is set as well, so the SW cannot spoof a confirm barrier either.

3. A NAVIGATION WRITE IS FULFILLED `204`, NOT WITH SYNTHETIC HTML.
   Fulfilling a form-POST navigation with a synthetic page navigates the document to the form's action
   URL — so a flow whose `confirm_url_contains` is its own action would see a genuine URL transition and
   FALSELY PASS a barrier for a write that never happened. `204` leaves the page on the pre-write document.
   (`route.abort()` is worse: `chrome-error://chromewebdata/`, page destroyed.)

4. JS MAY ONLY DROP WHAT `context.route` CANNOT SEE.
   An init-script patch that swallows a call hides it from the route layer entirely — so a `sendBeacon`
   drop-patch would delete the route layer's own proof for that channel, and a `fetch` drop-patch would
   additionally break the engine's existing `page.expect_request` and zero the held count while looking
   safe. Route holds beacons reliably (measured on `mix()`, `pagehide`, `visibilitychange`, `<a ping>`), so
   JS observes-and-calls-through for those and only POISONS the channels route cannot see at all
   (`RTCPeerConnection`, `WebTransport`).

WHAT THIS CANNOT CLAIM. A held write is fulfilled with a synthesized response, so every step after the
first hold runs against fictional page state: write #2's body may be computed from a response that never
existed. The report therefore states three numbers, never two, and labels post-hold steps unrepresentative.
Confirm barriers are reported `held — unverifiable` and are never evaluated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .obs import get_logger
from .safety import is_write_request

_log = get_logger("dryrun")

# The ONLY methods that may reach the network. Everything else is fulfilled, telemetry or not — see the
# redirect leak in the module docstring. Deliberately a module constant so a test can assert on it.
IDEMPOTENT_METHODS = ("GET", "HEAD", "OPTIONS")

# How much the report may CLAIM about which step caused a held write. Three states, because two would
# lie in the way R3.7 has now cost two attempts: `step == -1` alone would mean BOTH "nobody gated this"
# and "more than one step could own it", and a consumer cannot tell those apart — different facts, and
# different things for a human to do about them.
#
# QUIET IS AN ALLOWLIST (R3.9/CLI-1): `ATTRIBUTED` is the only state the report passes over in silence,
# so a state added tomorrow is loud by default rather than invisible in a `!= "ambiguous"` test.
ATTRIBUTED = "step"                                   # this write arrived while exactly one step was live
ATTRIBUTION_STATES = (ATTRIBUTED, "ambiguous", "ungated")

# Channels with no interception surface at any layer. Poisoned in-page so a call raises loudly rather than
# escaping; reaching one is an abort, because "we could not observe this" is not a safe outcome.
_UNINTERCEPTABLE_JS = r"""
(() => {
  const poison = (name) => {
    try {
      const Boom = function () {
        throw new Error('ultracua dry-run: ' + name + ' is not interceptable and is disabled');
      };
      Object.defineProperty(window, name, {value: Boom, configurable: false, writable: false});
    } catch (e) {}
  };
  poison('RTCPeerConnection');
  poison('webkitRTCPeerConnection');
  poison('WebTransport');
})();
"""


@dataclass
class HeldWrite:
    """One write that was intercepted and dropped. `body` is shown to a human and NEVER persisted."""

    step: int
    intent: str
    method: str
    url: str
    body: str
    resource_type: str
    idempotency_key: str = ""
    navigation: bool = False
    in_window: bool = True
    late: bool = False                 # arrived in the grace tail after the window closed
    telemetry: bool = False            # classified as a telemetry host: recorded, still held
    ts: float = 0.0
    # WAS this write gated (`in_window`) and WHICH step caused it (`attribution`) are different
    # questions — collapsing them was R3.3's sixth-pass finding, so they stay on separate axes here.
    # `in_window` still decides the `ungated-write` abort and nothing else reads `attribution` for it.
    attribution: str = ATTRIBUTED
    candidates: list = field(default_factory=list)     # when ambiguous: every step that could own it

    @property
    def earliest_step(self) -> int:
        """The LOWEST step index this write could belong to, or -1 if none is known. Consumers that ask
        "from which step is the report no longer representative" must use this, never `step`: an
        ambiguous hold whose `step` is -1 would otherwise read as "no hold happened here" and quietly
        certify the whole recipe."""
        if self.attribution == ATTRIBUTED:
            return self.step
        return min(self.candidates) if self.candidates else -1


@dataclass
class DryRunReport:
    """The artifact. Deliberately has NO `success` field: a run that holds one write and stops is both a
    complete, useful dry run AND a flow not shown to work, so any boolean would be read as
    "safe to approve"."""

    leaked: bool = False
    aborted: Optional[str] = None
    abort_detail: str = ""
    held: list = field(default_factory=list)          # list[HeldWrite]
    writes_planned: int = 0                            # mutating steps in the cached flow
    writes_reached: int = 0                            # mutating steps actually actuated
    requests_held: int = 0                             # HTTP requests dropped (>= writes_reached)
    steps_representative: int = 0                      # steps before the first hold
    channels: list = field(default_factory=list)       # channel-coverage canary results
    warnings: list = field(default_factory=list)
    approval_gates_skipped: list = field(default_factory=list)
    precheck_skipped: bool = False
    steps_hash: str = ""

    @property
    def ok(self) -> bool:
        """Ran to completion without a safety abort. NOT 'the flow works'."""
        return self.aborted is None and not self.leaked


class DryRunArbiter:
    """Installed on the browser CONTEXT for the whole session; the act window only decides record-vs-abort.

    The route is never added or removed per step: a route removed while a request is in flight is exactly
    the leak this class exists to prevent.
    """

    def __init__(self) -> None:
        self.report = DryRunReport()
        self.routed: list = []          # (method, url) seen by the route layer
        self.observed: list = []        # (method, url) seen by context.on("request") — the second ledger
        self.faults: list = []
        self._window = {"open": False, "until": 0.0, "step": -1, "intent": "", "key": "",
                        "settled": False}
        # Windows that CLOSED without their own write being attributed, and whose grace tail has not yet
        # expired: each is still a possible owner of whatever arrives next. See `_record`.
        self._pending: list = []
        self._inflight = 0
        self._grace_ms = 0.0

    # -- lifecycle ---------------------------------------------------------------------------------
    async def install(self, context) -> None:
        """Arm the context. Order matters: routes and init scripts must exist before any page does."""
        await context.route("**/*", self._handle)
        await context.add_init_script(_UNINTERCEPTABLE_JS)
        # The SECOND, INDEPENDENT ledger. `context.route` cannot see redirect hops; `context.on("request")`
        # can, with `redirected_from` populated. Under the GET-only continue policy a redirected
        # non-idempotent request is structurally unreachable — so if this ever fires, the hold guarantee is
        # already broken and the run must abort. This is what makes the mechanism re-prove itself per run
        # rather than per Playwright version.
        context.on("request", self._on_request)
        try:
            await context.route_web_socket("**/*", self._ws_handle)
        except AttributeError:          # older Playwright: no WS routing -> we cannot promise a hold
            self._latch("websocket-unroutable",
                        "this Playwright build cannot intercept WebSockets, so a frame could not be held")

    # -- the one choke point -----------------------------------------------------------------------
    async def _handle(self, route) -> None:
        """EVERY path is wrapped: an unwrapped raise leaves the page's fetch pending FOREVER (measured —
        never resolves, never rejects), the server gets nothing, and Playwright re-raises the error
        attached to an unrelated later call. Fail-closed, but indistinguishable from "this step fired no
        write". The latch turns that into a loud, attributable abort."""
        self._inflight += 1
        try:
            r = route.request
            method = (r.method or "").upper()
            self.routed.append((method, r.url))
            if method in IDEMPOTENT_METHODS:
                await route.continue_()          # THE ONLY continue_ IN THIS FILE
                return
            # From here down the bytes never leave the browser, whatever we decide.
            rec = self._record(r, method)
            if r.is_navigation_request():
                # 204 keeps the page on the pre-write document. Synthetic HTML would navigate to the form's
                # action URL and could falsely satisfy a `confirm_url_contains` barrier.
                await route.fulfill(status=204)
            else:
                await route.fulfill(status=200, content_type="application/json", body="{}")
            self._adjudicate(rec)                # latch AFTER the drop, never before
        except BaseException as exc:             # noqa: BLE001 — see the docstring
            self.faults.append(repr(exc))
            try:
                await route.abort()
            except Exception:                    # noqa: BLE001
                pass
            self._latch("arbiter-fault", f"{type(exc).__name__}: {exc}")
        finally:
            self._inflight -= 1

    def _record(self, r, method: str) -> HeldWrite:
        try:
            body = r.post_data or ""
        except Exception:                        # noqa: BLE001 — binary/unavailable body
            body = "<unavailable>"
        st = self._state()
        step, intent, attribution, candidates = self._attribute(st)
        rec = HeldWrite(
            step=step, intent=intent, method=method, url=r.url,
            body=body, resource_type=getattr(r, "resource_type", "") or "",
            idempotency_key=(r.headers or {}).get("idempotency-key", ""),
            navigation=bool(r.is_navigation_request()), in_window=(st != "closed"),
            late=(st == "closing"), telemetry=not is_write_request(method, r.url), ts=time.time(),
            attribution=attribution, candidates=candidates,
        )
        self.report.held.append(rec)
        self.report.requests_held += 1
        return rec

    def _attribute(self, st: str) -> tuple:
        """WHICH step may this held write be labelled with? The ONE place that decides, so the rule is
        enforced once rather than per branch.

        This does NOT attribute a deferred write — that is blocked indefinitely by decision D5 and no
        amount of window arithmetic changes it. It decides only whether the report has EARNED the right
        to name a step, and says so out loud when it has not. The grace tail still appears in the rule,
        but it now governs how OFTEN "ambiguous" is reported and never whether a WRONG step is named, so
        no constant can be tuned into a false claim — which is the property D5's impossibility #1 says a
        temporal *attribution* design can never have.
        """
        if st == "closed":
            # Outside every window. `_adjudicate` aborts on this; the label must not imply a step
            # anyway — it used to inherit whichever step ran last.
            return -1, "", "ungated", []
        live = self._live_candidates()
        if live:
            cands = sorted({p[0] for p in live} | {self._window["step"]})
            return -1, "", "ambiguous", cands
        self._window["settled"] = True
        return self._window["step"], self._window["intent"], ATTRIBUTED, []

    def _adjudicate(self, rec: HeldWrite) -> None:
        if not rec.in_window:
            # A write the flow never gated. It was HELD (the bytes are still in the browser), but a flow
            # that writes outside its own act window is not one this tool can make promises about.
            self._latch("ungated-write",
                        f"{rec.method} {rec.url} fired outside any mutating step's act window")

    def _on_request(self, request) -> None:
        try:
            method = (request.method or "").upper()
            self.observed.append((method, request.url))
            if method not in IDEMPOTENT_METHODS and request.redirected_from is not None:
                self.report.leaked = True
                self._latch("leak-redirect",
                            f"a redirected {method} to {request.url} bypassed the route layer")
        except Exception:                        # noqa: BLE001 — never let telemetry break the run
            pass

    async def _ws_handle(self, ws) -> None:
        """Deliberately never calls `connect_to_server()`, so the socket is fully mocked and the server is
        never contacted. A frame is opaque bytes that `is_write_request` cannot classify and no plausible
        response can be synthesized — so a send is an abort, but a LEAK-FREE one: the frame never left."""
        try:
            url = getattr(ws, "url", "")
            self.report.channels.append(f"websocket:{url}")

            def _on_msg(_payload) -> None:
                self._latch("websocket-frame",
                            f"the page sent a WebSocket frame to {url}, which cannot be held or classified")

            ws.on_message(_on_msg)
        except Exception as exc:                 # noqa: BLE001
            self._latch("arbiter-fault", f"websocket handler: {exc}")

    # -- the act window ----------------------------------------------------------------------------
    def open_window(self, *, step: int, intent: str, key: str, grace_ms: float) -> None:
        # R3.12. There is ONE slot, so opening a window destroys the previous step's claim on it — while
        # that step's grace tail says a write of its own may still be coming. Carry it forward as a
        # PENDING candidate instead of letting the new step inherit its writes.
        #
        # A window that already saw its own write is `settled` and is NOT carried: without that, every
        # ordinary multi-write flow (each write landing in its own window, tens of ms apart, all well
        # inside a 2 s tail) would report every hold as ambiguous — honest and useless, which is D0's
        # shape wearing a reporting hat.
        prev = self._window
        if prev["step"] >= 0 and not prev["open"] and not prev["settled"]:
            self._pending.append((prev["step"], prev["intent"], prev["until"]))
        self._window.update({"open": True, "until": 0.0, "step": step, "intent": intent, "key": key,
                             "settled": False})
        self._grace_ms = grace_ms

    def _live_candidates(self) -> list:
        """Pending windows whose grace tail has not expired. Pruned here so the list cannot grow."""
        now = time.time()
        self._pending = [p for p in self._pending if now < p[2]]
        return self._pending

    def close_window(self) -> None:
        # A grace tail, mirroring `flow._author_steps`' act window: a click-triggered write can land a few
        # ms after the actuation returns, and calling that "ungated" would abort the safest possible flow.
        self._window["open"] = False
        self._window["until"] = time.time() + (self._grace_ms / 1000.0)

    def _state(self) -> str:
        if self._window["open"]:
            return "open"
        return "closing" if time.time() < self._window["until"] else "closed"

    async def drain(self, timeout_ms: float) -> None:
        """Wait for in-flight route handlers before the window closes.

        MANDATORY, and the ordering is subtle: the `request` event that the engine's existing
        `page.expect_request` resolves on fires BEFORE the route handler runs. At the moment that context
        manager returns, the `HeldWrite` may not be recorded yet — so closing the window on that signal
        alone would classify the flow's own expected write as out-of-window and trip `ungated-write`,
        turning the safest possible flow into an abort."""
        import asyncio

        deadline = time.time() + (timeout_ms / 1000.0)
        while self._inflight > 0 and time.time() < deadline:
            await asyncio.sleep(0.005)
        if self._inflight > 0:
            self._latch("arbiter-fault", f"{self._inflight} request(s) still in flight after drain")

    # -- abort ---------------------------------------------------------------------------------------
    def _latch(self, kind: str, detail: str) -> None:
        """First abort wins — a later, vaguer reason must never overwrite the first, most specific one."""
        if self.report.aborted is None:
            self.report.aborted = kind
            self.report.abort_detail = detail
            _log.warning("dry-run ABORT (%s): %s", kind, detail)

    @property
    def aborted(self) -> bool:
        return self.report.aborted is not None

    def reconcile(self) -> None:
        """End-of-run cross-check of the two ledgers. Any non-idempotent request the event layer saw but
        the route layer did not is a request that escaped interception."""
        routed = {(m, u) for m, u in self.routed}
        for m, u in self.observed:
            if m not in IDEMPOTENT_METHODS and (m, u) not in routed:
                self.report.leaked = True
                self._latch("leak-unrouted", f"{m} {u} was never seen by the route layer")
