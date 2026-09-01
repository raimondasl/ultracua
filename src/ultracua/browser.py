"""Warm browser session — the long-lived process that holds a persistent
Playwright/CDP connection (PLAN.md component 1).

Keeping this hot across steps is the single biggest structural win: it avoids the
150-400ms per-step reconnect tax. Phase 0 uses Playwright's high-level actions, which
give us actionability checks + auto-waiting for free (the reliability win); Phase 1+
will reach for the raw CDPSession on the cached fast-path.
"""

from __future__ import annotations

import asyncio
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from .config import settings
from .snapshot import capture
from .types import Action, Observation

# --- the shared Playwright DRIVER -------------------------------------------------------------
# Starting the driver is a Node subprocess launch + handshake, and it dominates session setup:
# MEASURED on this project, per session — driver start 364 ms, chromium.launch() 81 ms,
# new_context()+new_page() 58 ms. So a session that starts its own driver pays ~600 ms, of which the
# browser is barely an eighth.
#
# The driver is reusable across many browsers, so it is shared per EVENT LOOP. Per loop, not per process:
# a `Playwright` object owns a transport bound to the loop that created it, and handing it to a different
# loop (a second `asyncio.run`, which is what pytest-asyncio does per test) would use a dead transport.
# Keyed weakly so a finished loop's entry simply disappears.
#
# WHY THE REFERENCE COUNT IS NOT ENOUGH ON ITS OWN, measured before this was written: with a plain
# refcount, SERIAL use — one session at a time, which is exactly `run_batch`'s row loop — swings 1 -> 0 -> 1
# and stops the driver between every session. Five serial sessions: 3129 ms per-session-driver vs 3029 ms
# with a naive refcount (i.e. no win), vs 1092 ms when a reference is HELD across the run. So the win only
# exists if a caller spanning several sessions holds one: see `driver_scope()`, used by the batch/fleet
# verbs. A lone session still starts and stops its own driver, exactly as before.


@dataclass
class _DriverRef:
    pw: Optional[Playwright] = None
    refs: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_drivers: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _DriverRef]" = weakref.WeakKeyDictionary()


async def _acquire_driver() -> Playwright:
    """Start (or join) THIS loop's driver and take a reference. Never returns another loop's driver."""
    loop = asyncio.get_running_loop()
    ref = _drivers.get(loop)
    if ref is None:
        # Get-or-create is SYNCHRONOUS — no await between the miss and the insert — so two coroutines on
        # this loop cannot both install an entry. The `await` that actually starts the driver is under the
        # per-entry lock below.
        ref = _drivers[loop] = _DriverRef()
    async with ref.lock:
        if ref.pw is None:
            ref.pw = await async_playwright().start()
        ref.refs += 1
        return ref.pw


async def _release_driver() -> None:
    """Drop a reference; stop the driver when the last one goes, so nothing is orphaned."""
    loop = asyncio.get_running_loop()
    ref = _drivers.get(loop)
    if ref is None:
        return
    async with ref.lock:
        ref.refs = max(0, ref.refs - 1)
        if ref.refs or ref.pw is None:
            return
        pw, ref.pw = ref.pw, None
        try:
            await pw.stop()
        except Exception:  # noqa: BLE001 — a driver already gone must not mask the caller's own error
            pass


@asynccontextmanager
async def driver_scope() -> AsyncIterator[None]:
    """Hold this loop's Playwright driver open across a SEQUENCE of sessions.

    For any caller that creates several sessions one after another — `run_batch`'s rows, `run_all`'s fleet,
    a best-of-N learn and its verify-by-replay — this is what turns the shared driver from a no-op into the
    measured ~2.9x setup win, because it keeps the reference count off zero between them. Nestable (it is
    just another reference), and it releases on the exception path."""
    await _acquire_driver()
    try:
        yield
    finally:
        await _release_driver()


class BrowserSession:
    def __init__(
        self,
        headless: bool | None = None,
        browser: Browser | None = None,
        record_har_path: str | None = None,
        storage_state: str | None = None,
        window_size: tuple[int, int] | None = None,
        dry_run: "object | None" = None,
    ) -> None:
        self.headless = settings.headless if headless is None else headless
        # Runtime SECRET VALUES to scrub from every Observation this session produces (see
        # `snapshot.capture`). Set by `run_cached(redact=...)`; empty by default, so a session built
        # directly (tests, the recorder) behaves exactly as before.
        self.redact: tuple = ()
        # Optional (width, height) for the browser window/viewport; falls back to the machine-level
        # ULTRACUA_WINDOW_SIZE default. Headed: sizes the real OS window and lets the page fill it;
        # headless: renders the page at this size. None -> Playwright's default (1280x720 viewport).
        self._window_size = window_size if window_size is not None else settings.window_size
        self._pw: Playwright | None = None
        # If a browser is provided, run as a fresh CONTEXT inside it (parallelism) and don't
        # own its lifecycle; otherwise launch (and later close) our own browser.
        self._shared_browser = browser
        self._owns_browser = browser is None
        # Do we hold a reference on this loop's shared driver? Set in start(), cleared in close(), so a
        # double close (or a close after a failed start) can never release a reference twice.
        self._holds_driver = False
        # When set, record a Playwright HAR of all network activity to this path (flushed on
        # context close). This is the trace WebArena-Verified scores against (component:
        # benchmarks/webarena_env.py) — captured via the native record_har_* context options.
        self._record_har_path = record_har_path
        # Path to a Playwright storage_state JSON (cookies + localStorage) to seed the context —
        # cookie-based auth for a recurring Flow, so replay starts already logged in.
        self._storage_state = storage_state
        # H5: a `DryRunArbiter` that HOLDS every write. Installed on the CONTEXT, never the page — a
        # service-worker-originated POST is delivered to `context.route` but NOT to `page.route`, where it
        # reaches the server (measured). See `dryrun.py`.
        self._dry_run = dry_run
        if dry_run is not None and record_har_path:
            # `record_har_content="embed"` would write the raw POST bodies to a HAR on disk, directly
            # against the rule that held bodies are shown to a human and never persisted.
            raise ValueError("dry-run refuses record_har_path: a HAR would persist raw held write bodies")
        self._base_headers: dict = {}
        self.browser: Browser | None = browser
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def start(self) -> "BrowserSession":
        try:
            if self._shared_browser is None:
                self._pw = await _acquire_driver()   # shared per event loop; released in close()
                self._holds_driver = True
                launch_kwargs: dict = {"headless": self.headless}
                if self._window_size is not None:
                    w, h = self._window_size
                    # Size the real OS window (headed; harmless headless) and pin it top-left so a
                    # screen-recorded demo frames predictably.
                    launch_kwargs["args"] = [f"--window-size={w},{h}", "--window-position=0,0"]
                self.browser = await self._pw.chromium.launch(**launch_kwargs)
            context_kwargs: dict = {}
            if self._record_har_path:
                context_kwargs["record_har_path"] = self._record_har_path
                context_kwargs["record_har_content"] = "embed"
            if self._storage_state:
                context_kwargs["storage_state"] = self._storage_state
            if self._window_size is not None:
                w, h = self._window_size
                if self.headless:
                    # No real window to fill -> render the page at the requested size directly.
                    context_kwargs["viewport"] = {"width": w, "height": h}
                else:
                    # Let the page fill the actual window inner area (window minus browser chrome),
                    # so content fills the headed window exactly instead of guessing the chrome height.
                    context_kwargs["no_viewport"] = True
            if self._dry_run is not None:
                # Block service workers: `context.route` does hold an SW-originated fetch, but a live SW
                # can also spoof a confirm barrier's response, and a dry run must not be able to "pass" a
                # barrier for a write that never happened.
                context_kwargs["service_workers"] = "block"
            self.context = await self.browser.new_context(**context_kwargs)
            self.context.set_default_timeout(settings.action_timeout_ms)
            self.context.set_default_navigation_timeout(settings.nav_timeout_ms)
            # WHEN DID THIS PAGE LAST CHANGE? A cheap running stamp, installed at document_start on
            # every page in the context, so `await_settled` can answer "has it ALREADY been quiet
            # long enough" without waiting to find out. Without this history a settle on a fully
            # rendered, static page still costs a full quiet window -- measured at 9.9s across
            # drift_bench's 42 unmatched resolves, 6.1% of that run and the difference between 171s
            # and 181s against its 180s budget. It counts and stamps only; it holds no records.
            await self.context.add_init_script(BrowserSession._MUTATION_STAMP)
            if self._dry_run is not None:
                # ARM BEFORE THE FIRST PAGE EXISTS. A page created before the route is registered could
                # issue a request the arbiter never sees.
                await self._dry_run.install(self.context)
            self.page = await self.context.new_page()
            return self
        except BaseException:
            # A failure AFTER launch (bad storage_state, new_context/new_page raising, cancellation)
            # would otherwise leak the driver + Chromium: the caller's `session = await ...start()`
            # never binds, so its `finally: session.close()` never runs. close() is None-guarded and
            # only stops what we own, so tearing down whatever we created here is safe; then re-raise.
            await self.close()
            raise

    async def goto(self, url: str) -> None:
        assert self.page is not None
        await self.page.goto(url, wait_until="domcontentloaded")

    async def set_extra_http_headers(self, headers: dict[str, str]) -> None:
        """Set the flow's BASE headers on the context (`FlowSpec.headers`). Replaces, as Playwright does."""
        assert self.context is not None
        self._base_headers = dict(headers or {})
        await self.context.set_extra_http_headers(self._base_headers)

    async def set_transient_headers(self, headers: dict[str, str]) -> None:
        """Overlay short-lived headers (the write's Idempotency-Key) ON TOP OF the flow's base headers,
        and restore the base exactly when called with `{}`.

        Playwright's `set_extra_http_headers` REPLACES the whole set. So injecting the Idempotency-Key with
        it wiped `FlowSpec.headers` — the write itself went out with no `Authorization` / `X-Tenant`, and
        because the `finally` then cleared to `{}`, they never came back for the rest of the run. Measured:
            before the write   X-Tenant='42'
            the write          X-Tenant=None     <- posts against the wrong tenant
            every later step   X-Tenant=None     <- and the confirm reads the wrong context
        ...and `replay()` still returned `{"status": "confirmed"}`. Merging is the whole fix."""
        assert self.context is not None
        merged = {**getattr(self, "_base_headers", {}), **(headers or {})}
        await self.context.set_extra_http_headers(merged)

    async def save_storage_state(self, path: str) -> None:
        """Persist the context's cookies + localStorage to a Playwright storage_state JSON
        (the artifact that lets a later session start already authenticated)."""
        assert self.context is not None
        await self.context.storage_state(path=path)

    async def screenshot(self) -> bytes:
        """Viewport screenshot (PNG bytes) — input for the vision fallback tier."""
        assert self.page is not None
        return await self.page.screenshot()

    # Runs at document_start on every page. `last` starts at script-eval time, so a page that has
    # never mutated reports its age rather than 0 -- which is what makes "already quiet" answerable.
    _MUTATION_STAMP = """
    window.__ucq = {last: Date.now()};
    new MutationObserver(function () { window.__ucq.last = Date.now(); })
      .observe(document, {childList: true, subtree: true, attributes: true, characterData: true});
    """

    async def _quiet_for_ms(self) -> float:
        """How long the DOM has been unchanged, from the standing stamp. -1 if it is unavailable."""
        try:
            return float(await self.page.evaluate(
                "() => window.__ucq ? (Date.now() - window.__ucq.last) : -1"))
        except Exception:  # noqa: BLE001 - navigating / destroyed: caller falls back to waiting
            return -1.0

    # Resolves once the DOM has been free of mutations for `quiet` ms, or at `cap` ms regardless.
    # Every mutation RESTARTS the quiet timer, so this is "the page stopped changing", not "some time
    # passed" -- R4.26's rule that a timer is not a boundary, honoured by taking the fact from the
    # platform. The observer is per-call and disconnected on both exits rather than installed as an
    # init script: nothing is left running in the page between calls, and a settle cannot be
    # influenced by mutations that happened before the caller asked.
    _SETTLE_JS = """(o) => new Promise((resolve) => {
      let quiet = null, cap = null;
      const obs = new MutationObserver(() => {
        clearTimeout(quiet);
        quiet = setTimeout(() => done('quiet'), o.quiet);
      });
      const done = (why) => { obs.disconnect(); clearTimeout(quiet); clearTimeout(cap); resolve(why); };
      obs.observe(document, {childList: true, subtree: true, attributes: true, characterData: true});
      quiet = setTimeout(() => done('quiet'), o.quiet);
      cap = setTimeout(() => done('cap'), o.cap);
    })"""

    #: Finds the element the page really scrolls and scrolls it. Returns what it moved, or null
    #: when the WINDOW is the scroller -- in which case the caller wheels, exactly as before.
    _SCROLL_JS = r"""
    (dy) => {
      // THE WINDOW WINS WHEN IT CAN SCROLL, so the common case is untouched: Gitea's issue page has
      // docH 1512 against a 720 viewport and keeps the wheel it always had. Only a page whose
      // document CANNOT scroll reaches the search below, which is precisely the broken case.
      const doc = document.scrollingElement || document.documentElement;
      if (doc.scrollHeight - doc.clientHeight > 1) return null;

      // The biggest scroller actually on screen. Biggest by VISIBLE AREA rather than by scroll
      // room: a tall off-screen drawer would otherwise win over the list the agent is looking at.
      let best = null, bestArea = 0;
      for (const el of document.querySelectorAll("*")) {
        const st = getComputedStyle(el);
        if (!/auto|scroll|overlay/.test(st.overflowY)) continue;
        if (el.scrollHeight - el.clientHeight < 8) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 80 || r.height < 80) continue;
        if (r.bottom < 0 || r.top > innerHeight) continue;
        const area = (Math.min(r.bottom, innerHeight) - Math.max(r.top, 0)) * r.width;
        if (area > bestArea) { bestArea = area; best = el; }
      }
      if (!best) return null;
      const before = best.scrollTop;
      best.scrollBy(0, dy);
      return {moved: best.scrollTop - before};
    }
    """

    async def _scroll_inner_container(self, dy: int) -> bool:
        """Scroll the element the page really scrolls. True if it moved something.

        SCROLLED VIA JS RATHER THAN BY MOVING THE MOUSE OVER IT AND WHEELING. Both work -- the mouse
        version was what the diagnosis used -- but moving the pointer onto an arbitrary container
        fires hover states the agent did not ask for, and on a list of rows that means hover toolbars
        appearing in the very observation the scroll was taken to produce.

        FAIL-OPEN: any error, or no inner scroller, returns False and the caller wheels the window
        exactly as before. The worst case is the behaviour that shipped before this existed.
        """
        try:
            res = await self.page.evaluate(self._SCROLL_JS, dy)
        except Exception:                                              # noqa: BLE001
            return False
        return bool(res and res.get("moved"))

    async def _await_scroll_applied(self) -> None:
        """Resolve once the browser has produced a frame carrying the new scroll offset.

        Companion to `await_settled`, which is DOM-mutation based and structurally blind to
        scrolling. Fail-open like it: a page that navigates out from under the evaluate leaves
        nothing to wait for, and the caller is no worse off than before this existed.
        """
        try:
            await self.page.evaluate(
                "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        except Exception:                                              # noqa: BLE001
            return

    async def await_settled(self, quiet_ms: int = 0, cap_ms: int = 0) -> str:
        """Wait until the DOM stops changing. Returns 'quiet' | 'cap' | 'unavailable'.

        WHY THE LEARN NEEDS THIS. `_author_steps` snapshots, decides, acts. On a client-rendered app
        the snapshot lands on an unpainted shell -- measured, Odoo serves 5 elements at
        `domcontentloaded` and settles to 80-255 around 470-860 ms -- so the agent decides from a page
        that is not there. Two things follow, and the second is why a NEW guard was not the answer:
        the agent re-navigates because nothing it wants is visible (R4.118: two of five Odoo learns
        cached recipes that are 100% `navigate` steps), and the loop's existing `no_progress` bail
        cannot stop it, because `state_changed` only asks whether the page DIFFERS and every arrival
        at a differently-unpainted shell differs (R4.119). Feeding that guard a settled observation
        re-arms it without adding anything that can refuse.

        FAIL-OPEN, DELIBERATELY. A page mid-navigation makes `evaluate` throw, and the honest
        response is to proceed exactly as the code did before this existed -- a settle that turned a
        transient into a failed step would be a regression bought with a diagnostic. Same for the
        cap: on expiry the caller proceeds. The worst case of this mechanism is today's behaviour.
        """
        assert self.page is not None
        q = quiet_ms or settings.settle_quiet_ms
        c = cap_ms or settings.settle_cap_ms
        # ALREADY QUIET? Then there is nothing to wait for. The standing stamp makes this a single
        # ~1ms read instead of a full quiet window, and it is the whole difference between paying the
        # settle on pages that were racing and paying it on every page. R4.120 measured the asymmetry
        # that makes it matter: a server-rendered page is `true_ready = 0` in every rep, so a wait
        # there is pure tax. NOT a weaker predicate -- the condition is the same "no mutation for
        # `quiet` ms", just answered from history rather than by waiting to observe it.
        if await self._quiet_for_ms() >= q:
            # DISTINGUISHED FROM "quiet" ON PURPOSE. "already-quiet" means we waited ZERO ms, so the
            # DOM cannot have changed since the caller's previous look -- which lets a caller skip
            # the work it was going to redo afterwards. `_retry_if_unpainted` uses exactly that: a
            # re-resolve over an unchanged page is provably the same answer, and measured, those
            # re-resolves were the real cost (10.1s across drift_bench, not the waiting).
            return "already-quiet"
        try:
            return await self.page.evaluate(self._SETTLE_JS, {"quiet": q, "cap": c})
        except Exception:  # noqa: BLE001 - navigating / context destroyed / closed: proceed as before
            return "unavailable"

    async def snapshot(self) -> Observation:
        assert self.page is not None
        try:
            return await capture(self.page, settings.max_elements, self.redact)
        except Exception as exc:  # noqa: BLE001
            # A snapshot can race a navigation triggered by the previous action — Playwright's
            # in-page evaluate then fails with "Execution context was destroyed". Wait for the
            # page to settle and retry once before giving up.
            if "context was destroyed" not in str(exc) and "navigation" not in str(exc).lower():
                raise
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=settings.nav_timeout_ms)
            except Exception:  # noqa: BLE001
                pass
            # the retry branch is the SIBLING that gets missed: it must redact too
            return await capture(self.page, settings.max_elements, self.redact)

    async def act(self, action: Action) -> None:
        """Execute a canonical action. Playwright's built-in actionability checks
        (visible/stable/enabled/receives-events) gate clicks and fills automatically."""
        assert self.page is not None
        page = self.page
        a = action.action
        if a == "click":
            await page.click(self._sel(action.ref))
        elif a == "type":
            await page.fill(self._sel(action.ref), action.text or "")
        elif a == "select":  # recorder: choose an <option> by value
            await page.select_option(self._sel(action.ref), action.text or "")
        elif a == "press":
            await page.keyboard.press(action.text or "Enter")
        elif a == "scroll":
            # A recorded scroll carries the absolute Y it settled at (deterministic viewport restore);
            # the agent/vision tier emits a textless scroll -> the legacy fixed wheel-down.
            if action.text and action.text.lstrip("-").isdigit():
                await page.evaluate("(y) => window.scrollTo(0, y)", int(action.text))
            else:
                # SCROLL WHAT ACTUALLY SCROLLS (R4.138). `mouse.wheel` moves the WINDOW, and on an
                # app that scrolls an inner container the window has nowhere to go: measured on 6 of
                # 7 Odoo corpus pages, `window.scrollY` stays 0 and the observation is unchanged.
                # That made R4.102's new below-fold signal actively harmful there -- the agent is
                # told "12 more are further down the page, use `scroll`", scrolls, and sees an
                # identical page. A signal that cannot be acted on is worse than none.
                if not await self._scroll_inner_container(600):
                    await page.mouse.wheel(0, 600)
            # WAIT FOR THE SCROLL TO LAND. `await_settled()` cannot see one: it resolves on DOM
            # MUTATION quiet, and scrolling mutates nothing, so it returns `already-quiet`
            # immediately and the next snapshot can be taken at the OLD offset.
            #
            # MEASURED on `gitea-start-timer` (R4.136): four identical trials returned
            # `already-quiet` every time, and one of the four then snapshotted 73 elements at y=0
            # when the page was about to sit at y=600 with 15. Traced against the real agent, the
            # cost is a wasted turn and then an OVERSHOOT -- it scrolls, sees an identical page,
            # scrolls again, and two scrolls' worth arrive at once. That is the below-fold signal
            # (R4.102) being handed to an agent that cannot see the result of acting on it.
            #
            # TWO FRAMES, not a timeout: the scroll is applied during frame production, so waiting
            # for two `requestAnimationFrame` callbacks flushes it without guessing a duration. A
            # timer here would be R4.26's mistake -- "a timer is not a boundary".
            await self._await_scroll_applied()
        elif a == "navigate":
            await self.goto(action.text or "about:blank")
        elif a == "click_xy":  # vision tier: click pixel coordinates
            x, y = (action.coords or [0, 0])[:2]
            await page.mouse.click(x, y)
        elif a == "webmcp_call":  # WebMCP tier: invoke a site-exposed tool
            from .webmcp import call as webmcp_call

            await webmcp_call(page, action.tool or "", action.args or {})
        # done / give_up are terminal no-ops handled by the agent loop.

    @staticmethod
    def _sel(ref: str | None) -> str:
        if not ref:
            raise ValueError("action requires an element ref")
        return f'[data-ultracua-ref="{ref}"]'

    async def close(self) -> None:
        try:
            if self.context is not None:
                await self.context.close()
            if self._owns_browser and self.browser is not None:
                await self.browser.close()
        finally:
            # Release the DRIVER reference rather than stopping the driver outright: another session (or an
            # enclosing `driver_scope`) may still be using it. It is stopped when the last reference goes,
            # so nothing is orphaned. Idempotent — `_holds_driver` is cleared first, so `close()` twice, or
            # `close()` after a start() that raised, releases exactly once.
            if self._holds_driver:
                self._holds_driver = False
                await _release_driver()

    async def __aenter__(self) -> "BrowserSession":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.close()
