"""Define a recurring browser task once, then run it — safely, unattended.

A `FlowSpec` is a named, reusable task (start URL + goal + auth + what data to pull).
- `learn()` LLM-authors the flow (and returns the steps + data to inspect),
- `approve()` marks a verified flow trusted,
- `replay()` reproduces it at 0-LLM navigation, returns the extracted data, and **fails loud**:
  it raises `FlowReplayError` on any drift (no cached flow / unresolved locator / data not found /
  the data's *shape* changed vs the learned run) rather than returning wrong data. With
  `on_drift="relearn"` it re-authors the flow instead of raising. Trust metadata (approval +
  the learned output shape) lives in a `<key>.meta.json` sidecar next to the cached flow.

The product-facing layer over the `run_cached` engine — see ROADMAP.md (Phase A/B).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, Union

from .browser import BrowserSession
from .cache import FlowCache, flow_key
from .config import settings
from .extract import extract
from .flow import run_cached
from .obs import get_logger
from .providers import build_router, get_provider

if TYPE_CHECKING:
    from playwright.async_api import Page

_log = get_logger("flows")

# login is either a declarative LoginSpec or an async callable that authenticates a page.
LoginCallable = Callable[["Page"], Awaitable[None]]


@dataclass
class LoginSpec:
    """How to (re)authenticate a flow whose cookie session expires.

    Credentials are read from environment variables **at runtime** (never stored in the spec or
    the cached flow); only the resulting cookies (storage_state) are persisted. The login form is
    filled heuristically (first text/email input + the password input, then Enter) unless explicit
    selectors are given.
    """

    url: str
    username_env: str = "ULTRACUA_USERNAME"
    password_env: str = "ULTRACUA_PASSWORD"
    username_selector: Optional[str] = None
    password_selector: Optional[str] = None
    submit_selector: Optional[str] = None  # None -> press Enter in the password field
    # success check (so a failed login can't poison a working session). Default: assume success
    # if we navigated away from `url`. Override for SPA logins that stay on the same URL.
    success_selector: Optional[str] = None       # an element present only once logged in
    success_url_contains: Optional[str] = None   # a substring the post-login URL must contain
    timeout_ms: Optional[int] = None             # per-step timeout for the login form actions


@dataclass
class MutateSpec:
    """Marks a FlowSpec as a WRITE flow (submit/post/purchase) and declares how to know the
    write landed — Phase D's action-completion verification (ROADMAP Phase D).

    A write that can't be confirmed is fire-and-hope, so a mutate flow MUST declare at least one
    `confirm_*` check: after the flow runs, that condition must hold or replay fails loud
    (`FlowReplayError`) — the write is never silently reported as success because a click didn't
    throw. The check mirrors `LoginSpec`'s success-check shape.

    Optional `precheck_*` gives opt-in idempotency for ONE-SHOT writes (don't purchase twice): a
    cheap separate pre-pass visits `precheck_url` (default: the flow's start_url) and, if the
    end-state is already present, the write is skipped and replay reports `already-done`. Leave it
    unset for RECURRING writes (e.g. placing today's order daily) — a state that legitimately
    recurs would otherwise be skipped. There is deliberately NO durable "already committed" ledger.
    """

    # action-completion verification — at least one is required; ANY that holds = confirmed.
    confirm_selector: Optional[str] = None        # element present only once the write committed
    confirm_text_contains: Optional[str] = None   # substring the post-write page text must contain
    confirm_url_contains: Optional[str] = None     # substring the post-write URL must contain
    timeout_ms: Optional[int] = None              # how long to wait for the confirmation to appear
    # opt-in idempotency precheck (one-shot writes only) — see the class docstring.
    precheck_url: Optional[str] = None            # where to look (default: the flow's start_url)
    precheck_selector: Optional[str] = None
    precheck_text_contains: Optional[str] = None
    precheck_url_contains: Optional[str] = None    # already-done state distinguishable only by URL

    def has_confirm(self) -> bool:
        return any((self.confirm_selector, self.confirm_text_contains, self.confirm_url_contains))

    def has_precheck(self) -> bool:
        return any((self.precheck_selector, self.precheck_text_contains, self.precheck_url_contains))


@dataclass
class FlowSpec:
    """A named, reusable recurring task."""

    name: str
    start_url: str
    goal: str
    extract: Optional[str] = None          # what data to pull (None = navigate-only flow)
    extract_schema: Optional[dict] = None  # optional JSON schema for the extracted `data`
    headers: Optional[dict] = None         # auth via extra HTTP headers
    storage_state: Optional[str] = None    # auth via a Playwright storage_state JSON (cookies)
    login: Optional[Union[LoginSpec, LoginCallable]] = None  # how to (re)authenticate on expiry
    mutate: Optional[MutateSpec] = None    # set -> this is a WRITE flow (Phase D)
    max_steps: Optional[int] = None
    headless: Optional[bool] = None

    @property
    def scope(self) -> str:
        return f"flow:{self.name}"


@dataclass
class LearnResult:
    spec: FlowSpec
    cached: bool       # did a replayable flow get cached?
    steps: list        # the learned steps, for the developer to inspect
    data: Any = None   # extracted data
    found: bool = False
    approved: bool = False
    shape: Any = None  # signature of the extracted data's structure (for replay drift checks)
    note: str = ""


@dataclass
class FlowMeta:
    """Trust + run-history metadata for a learned flow (sidecar next to the cached flow)."""

    approved: bool = False
    shape: Any = None
    learned_ts: float = 0.0
    last_ok_ts: float = 0.0
    # run history (for the fleet health view)
    last_run_ts: float = 0.0
    last_error: Optional[str] = None
    last_error_ts: float = 0.0
    runs: int = 0
    successes: int = 0
    consecutive_failures: int = 0


@dataclass
class FlowHealth:
    """A flow's status for the fleet view."""

    name: str
    status: str  # not-learned | never-run | healthy | failing | stale
    cached: bool
    approved: bool
    runs: int
    successes: int
    consecutive_failures: int
    last_run_ts: float
    last_ok_ts: float
    last_error: Optional[str]


class FlowReplayError(RuntimeError):
    """Replay could not be trusted: no cached flow, page drift, data not found, or shape change."""


# --- data-shape signature (data-level drift detection) ----------------------------------------
def _shape_of(value: Any) -> Any:
    if isinstance(value, dict):
        return {"t": "object", "keys": sorted(str(k) for k in value)}
    if isinstance(value, (list, tuple)):
        items = [_shape_of(v) for v in value]
        first = items[0] if items else None
        return {"t": "array", "item": first if all(it == first for it in items) else "mixed"}
    if isinstance(value, bool):
        return {"t": "bool"}
    if isinstance(value, (int, float)):
        return {"t": "number"}
    if value is None:
        return {"t": "null"}
    return {"t": "string"}


def _shape_matches(recorded: Any, current: Any) -> bool:
    """Lenient structural comparison — counts vary day to day, structure shouldn't."""
    if recorded is None or current is None or recorded == current:
        return True
    if isinstance(recorded, dict) and isinstance(current, dict):
        if recorded.get("t") != current.get("t"):
            return False
        if recorded.get("t") == "array":
            ri, ci = recorded.get("item"), current.get("item")
            if ri in (None, "mixed") or ci in (None, "mixed"):
                return True  # empty/mixed result -> can't disprove
            return _shape_matches(ri, ci)
        if recorded.get("t") == "object":
            return recorded.get("keys") == current.get("keys")
        return True  # same primitive type
    return False


# --- meta sidecar -----------------------------------------------------------------------------
def _meta_path(cache: FlowCache, key: str) -> Path:
    return Path(cache.root) / f"{key}.meta.json"


def _load_meta(cache: FlowCache, key: str) -> FlowMeta:
    p = _meta_path(cache, key)
    if p.exists():
        try:
            return FlowMeta(**json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return FlowMeta()


def _save_meta(cache: FlowCache, key: str, meta: FlowMeta) -> None:
    Path(cache.root).mkdir(parents=True, exist_ok=True)
    # Atomic write (temp + os.replace) so a crash or a concurrent reader never sees a torn file.
    p = _meta_path(cache, key)
    tmp = f"{p}.{os.getpid()}.tmp"
    Path(tmp).write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _record_run(cache: FlowCache, key: str, *, ok: bool, error: Optional[str] = None) -> None:
    """Record a replay outcome into the flow's run history (for the fleet health view).

    The read-modify-write is atomic within a process (no `await` between the reload and the atomic
    `_save_meta`, so the single-threaded event loop can't interleave two records). The temp+replace
    write also keeps the file valid for a concurrent reader in another process.
    """
    meta = _load_meta(cache, key)  # reload so we don't clobber a concurrent shape/approval update
    now = time.time()
    meta.last_run_ts = now
    meta.runs += 1
    if ok:
        meta.last_ok_ts = now
        meta.successes += 1
        meta.consecutive_failures = 0
        meta.last_error = None
    else:
        meta.consecutive_failures += 1
        meta.last_error = error
        meta.last_error_ts = now
    _save_meta(cache, key, meta)


def _default_cache() -> FlowCache:
    return FlowCache()


def _router(provider_name: str):
    provider = get_provider(provider_name)
    return provider, getattr(provider, "router", None) or build_router(provider_name)


async def _condition_present(
    page, *, selector=None, text_contains=None, url_contains=None, timeout_ms=None
) -> bool:
    """ANY-of presence check (shared by the mutate confirm + precheck): True if any set condition
    holds. Polls up to `timeout_ms` (default 5000) so a confirmation that renders a beat late isn't
    missed; pass `timeout_ms=0` for a single immediate check (the precheck wants a fast decision)."""
    budget = 5000 if timeout_ms is None else timeout_ms
    interval = 200
    waited = 0
    while True:
        if url_contains and url_contains in page.url:
            return True
        if text_contains:
            try:
                body = await page.inner_text("body")
            except Exception:  # noqa: BLE001
                body = ""
            if text_contains.lower() in body.lower():
                return True
        if selector:
            try:
                await page.wait_for_selector(selector, timeout=interval)  # this consumes ~interval
                return True
            except Exception:  # noqa: BLE001
                pass
        waited += interval
        if waited >= budget:
            return False
        if not selector:  # selector branch already waited; otherwise pace the poll
            await asyncio.sleep(interval / 1000.0)


def _make_finalize(spec: FlowSpec, router, out: dict):
    async def _finalize(session):
        if spec.mutate is not None:
            # WRITE flow: success is action-completion — the declared confirm check must hold,
            # else the write didn't land and replay fails loud (Phase D).
            m = spec.mutate
            try:
                await session.page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001
                pass
            confirmed = await _condition_present(
                session.page, selector=m.confirm_selector,
                text_contains=m.confirm_text_contains, url_contains=m.confirm_url_contains,
                timeout_ms=m.timeout_ms,
            )
            data = None
            if spec.extract is not None:  # optionally also pull a confirmation number, etc.
                try:
                    text = await session.page.inner_text("body")
                except Exception:  # noqa: BLE001
                    text = ""
                ex = await extract(router, spec.extract, text, schema=spec.extract_schema)
                data = ex.data
            out["data"], out["found"] = data, confirmed
            out["error"] = None if confirmed else "write not confirmed (no completion signal on the page)"
            return {"solved": confirmed, "data": data}
        if spec.extract is None:
            out["found"] = True  # navigate-only flow: reaching the end IS success
            return {"solved": True}
        try:
            await session.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001
            pass
        try:
            text = await session.page.inner_text("body")
        except Exception:  # noqa: BLE001
            text = ""
        ex = await extract(router, spec.extract, text, schema=spec.extract_schema)
        out["data"], out["found"], out["error"] = ex.data, ex.found, ex.error
        return {"solved": ex.found, "data": ex.data}

    return _finalize


# --- auth refresh (re-login when a cookie session expires) ------------------------------------
def _same_page(a: str, b: str) -> bool:
    from urllib.parse import urlsplit

    pa, pb = urlsplit(a), urlsplit(b)
    return (pa.netloc, pa.path.rstrip("/")) == (pb.netloc, pb.path.rstrip("/"))


async def _form_login(page, login: LoginSpec) -> None:
    user = os.environ.get(login.username_env)
    pw = os.environ.get(login.password_env)
    if not user or not pw:
        raise FlowReplayError(
            f"login credentials not in env (need {login.username_env} and {login.password_env})"
        )
    to = {"timeout": login.timeout_ms} if login.timeout_ms else {}  # per-step ceiling, if set
    await page.goto(login.url, wait_until="domcontentloaded", **to)
    try:
        user_loc = (
            page.locator(login.username_selector) if login.username_selector
            else page.locator("input[type=email], input[type=text], input[type=tel]")
        ).first
        await user_loc.fill(user, **to)
        pass_loc = page.locator(login.password_selector or "input[type=password]").first
        await pass_loc.fill(pw, **to)
        if login.submit_selector:
            await page.locator(login.submit_selector).first.click(**to)
        else:
            await pass_loc.press("Enter", **to)
    except Exception as exc:  # noqa: BLE001 - heuristic selectors may not match; guide the user
        raise FlowReplayError(
            f"could not auto-fill the login form at {login.url} ({type(exc).__name__}) — pass "
            f"explicit username_selector/password_selector/submit_selector, or a callable login "
            f"for multi-step/SSO flows"
        ) from None
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:  # noqa: BLE001
        pass


async def _login_succeeded(page, login: LoginSpec) -> bool:
    if login.success_selector:
        try:
            await page.wait_for_selector(login.success_selector, timeout=login.timeout_ms or 5000)
            return True
        except Exception:  # noqa: BLE001
            return False
    if login.success_url_contains:
        return login.success_url_contains in page.url
    return not _same_page(page.url, login.url)  # default: assume success if we left the login page


async def _already_committed(spec: FlowSpec) -> bool:
    """Idempotency precheck: open a fresh page at `mutate.precheck_url` (default the start_url) and
    report whether the desired end-state is ALREADY present — so a one-shot write isn't re-fired.

    A separate, read-only pre-pass (not a `prepare` hook): it must not run the cached steps, and it
    sidesteps the per-step mutation gate entirely. Reads the live page each call, so a legitimately
    recurring write (whose end-state isn't present on a fresh visit) is never wrongly skipped.
    """
    m = spec.mutate
    session = await BrowserSession(
        headless=spec.headless, storage_state=spec.storage_state
    ).start()
    try:
        if spec.headers:
            await session.set_extra_http_headers(spec.headers)
        await session.goto(m.precheck_url or spec.start_url)
        return await _condition_present(
            session.page, selector=m.precheck_selector, text_contains=m.precheck_text_contains,
            url_contains=m.precheck_url_contains, timeout_ms=0,  # a fast skip decision, not a wait
        )
    finally:
        await session.close()


async def _precheck_done(spec: FlowSpec) -> bool:
    """True if this is a write flow with an idempotency precheck whose end-state already holds."""
    return spec.mutate is not None and spec.mutate.has_precheck() and await _already_committed(spec)


async def refresh_auth(spec: FlowSpec, *, headless: Optional[bool] = None) -> None:
    """Re-authenticate `spec.login` and save fresh cookies to `spec.storage_state`.

    Credentials come from the env vars named in the LoginSpec (or are handled by a callable
    login); they are never logged or written into the cached flow — only the resulting cookies.
    For a `LoginSpec`, the login is verified before saving (and the save is atomic), so a failed
    login can't overwrite a working session's cookies.
    """
    if spec.login is None:
        raise FlowReplayError(f"{spec.name!r}: no `login` configured — cannot refresh auth")
    if not spec.storage_state:
        raise FlowReplayError(f"{spec.name!r}: set `storage_state` (a path) so refreshed cookies can be saved")
    _log.info("flow %r: refreshing auth (re-login -> %s)", spec.name, spec.storage_state)
    session = await BrowserSession(
        headless=headless if headless is not None else spec.headless
    ).start()  # a fresh context (no stale cookies) for a clean login
    try:
        if callable(spec.login):
            await spec.login(session.page)
        else:
            await _form_login(session.page, spec.login)
            if not await _login_succeeded(session.page, spec.login):
                raise FlowReplayError(
                    f"{spec.name!r}: login did not appear to succeed (still on the login page or "
                    f"success check unmet) — check credentials/selectors; storage_state left unchanged"
                )
        # Atomic save: write a temp file then replace, so a crash mid-write can't corrupt the
        # working storage_state (and we only get here once login is verified).
        Path(spec.storage_state).parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{spec.storage_state}.tmp"
        await session.save_storage_state(tmp)
        os.replace(tmp, spec.storage_state)
        _log.info("flow %r: auth refreshed OK", spec.name)
    finally:
        await session.close()


# --- learn / approve / replay -----------------------------------------------------------------
async def learn(
    spec: FlowSpec, *, samples: int = 1, provider_name: Optional[str] = None, provider=None,
    router=None, cache: Optional[FlowCache] = None,
) -> LearnResult:
    """LLM-author the flow, cache it, record its output shape, and return it to inspect.

    Discovery (the learn run) is the reliability bottleneck — the LLM sometimes fails to author a
    working flow. `samples > 1` re-authors up to N times and keeps the FIRST attempt the verifier
    confirms (`found` — data extracted / write confirmed / navigate-only solved), trading LLM cost
    for a higher first-try success rate on flaky/ambiguous pages. Each attempt gets a fresh
    provider+router (so the LLM resamples); passing an explicit `provider` AND `router` forces a
    single attempt (a stateful teacher can't be replayed). A re-learn preserves any `approved` flag.
    """
    cache = cache or _default_cache()
    fixed = provider is not None and router is not None  # a caller-supplied teacher -> one attempt
    attempts = 1 if fixed else max(1, samples)
    best: Optional[LearnResult] = None
    for i in range(attempts):
        if fixed:
            p, r = provider, router
        else:
            dp, dr = _router(provider_name or settings.provider)  # fresh each attempt -> LLM resamples
            p = provider if provider is not None else dp
            r = router if router is not None else dr
        res = await _learn_once(spec, provider=p, router=r, cache=cache)
        if res.cached and res.found:
            if i:
                _log.info("flow %r: discovery verified on attempt %d/%d", spec.name, i + 1, attempts)
            return res  # the cache now holds this verified flow
        best = res
    if attempts > 1:
        _log.warning("flow %r: discovery unverified after %d samples", spec.name, attempts)
    return best


async def _learn_once(
    spec: FlowSpec, *, provider, router, cache: FlowCache,
) -> LearnResult:
    """One discovery attempt: LLM-author the flow, cache it, record its output shape."""
    out: dict = {}
    report = await run_cached(
        url=spec.start_url, goal=spec.goal, provider=provider, cache=cache, mode="learn",
        max_steps=spec.max_steps, headless=spec.headless, scope=spec.scope,
        extra_headers=spec.headers, storage_state=spec.storage_state,
        finalize=_make_finalize(spec, router, out),
    )
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cached = cache.get(key)
    data, found = out.get("data"), bool(out.get("found"))
    meta = _load_meta(cache, key)  # preserve `approved` across re-learns
    if cached is not None:
        meta.shape, meta.learned_ts = _shape_of(data), time.time()
        _save_meta(cache, key, meta)
    return LearnResult(
        spec=spec, cached=cached is not None, steps=list(cached.steps) if cached else [],
        data=data, found=found, approved=meta.approved, shape=_shape_of(data),
        note=report.note or report.mode,
    )


def approve(spec: FlowSpec, *, cache: Optional[FlowCache] = None) -> None:
    """Mark a learned flow trusted (so `replay(require_approved=True)` will run it)."""
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    if cache.get(key) is None:
        raise FlowReplayError(f"{spec.name!r}: nothing to approve — learn the flow first")
    meta = _load_meta(cache, key)
    meta.approved = True
    _save_meta(cache, key, meta)


def unapprove(spec: FlowSpec, *, cache: Optional[FlowCache] = None) -> None:
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    meta = _load_meta(cache, key)
    meta.approved = False
    _save_meta(cache, key, meta)


def health(spec: FlowSpec, *, cache: Optional[FlowCache] = None, stale_after: Optional[float] = None) -> FlowHealth:
    """A flow's status for the fleet view: not-learned / never-run / healthy / failing / stale.

    `stale_after` (seconds): a flow whose last success is older than this counts as `stale`.
    """
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cached = cache.get(key) is not None
    meta = _load_meta(cache, key)
    if not cached:
        status = "not-learned"
    elif meta.runs == 0:
        status = "never-run"
    elif meta.consecutive_failures > 0:
        status = "failing"
    elif stale_after is not None and meta.last_ok_ts and (time.time() - meta.last_ok_ts) > stale_after:
        status = "stale"
    else:
        status = "healthy"
    return FlowHealth(
        name=spec.name, status=status, cached=cached, approved=meta.approved,
        runs=meta.runs, successes=meta.successes, consecutive_failures=meta.consecutive_failures,
        last_run_ts=meta.last_run_ts, last_ok_ts=meta.last_ok_ts, last_error=meta.last_error,
    )


async def _attempt_replay(spec, router, cache, key, meta, check_shape):
    """One pure 0-LLM replay attempt. Returns (ok, data, reason)."""
    out: dict = {}
    report = await run_cached(
        url=spec.start_url, goal=spec.goal, provider=None, cache=cache, mode="replay",
        max_steps=spec.max_steps, headless=spec.headless, scope=spec.scope,
        extra_headers=spec.headers, storage_state=spec.storage_state,
        finalize=_make_finalize(spec, router, out),
    )
    if report.mode == "miss":
        return False, None, "no learned flow — run learn first"
    if not report.success:
        return False, None, f"replay failed (page drift?): {report.note or report.mode}"
    if (spec.extract is not None or spec.mutate is not None) and not out.get("found"):
        # a write flow gates `found` on the confirm check, so an unconfirmed write fails here
        return False, None, f"data not found / write not confirmed on replay: {out.get('error')}"
    data = out.get("data")
    if check_shape and meta.shape is not None and not _shape_matches(meta.shape, _shape_of(data)):
        return False, None, f"data shape changed vs the learned flow (expected {meta.shape})"
    return True, data, ""


async def replay(
    spec: FlowSpec, *, require_approved: bool = False, on_drift: str = "raise",
    check_shape: bool = True, auth_refresh: bool = True, provider_name: Optional[str] = None,
    provider=None, router=None, cache: Optional[FlowCache] = None,
) -> Any:
    """Replay the learned flow at 0-LLM navigation and return the extracted data.

    Trust controls for unattended use:
      - `require_approved=True` — refuse to run a flow that hasn't been `approve`d.
      - `check_shape=True` — treat a change in the data's *structure* (vs the learned run) as drift.
      - `auth_refresh=True` — on drift, if `spec.login` is set, re-login (refresh cookies) and
        retry once before giving up (handles an expired session).
      - `on_drift="raise"` (default) — raise `FlowReplayError` on any drift (never return wrong
        data); `on_drift="relearn"` — re-author the flow instead and return the fresh data.

    WRITE flows (`spec.mutate` set, Phase D) behave differently: they default to approval-gated
    (a write is human-verified before unattended runs), refuse `on_drift="relearn"` (re-authoring
    a write would re-perform it), verify the write landed (fail loud if not), and return a dict
    `{"status": "confirmed" | "already-done", "data": <optional extracted data>}`.
    """
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    meta = _load_meta(cache, key)
    is_mutate = spec.mutate is not None
    if is_mutate:
        if not spec.mutate.has_confirm():
            raise FlowReplayError(
                f"{spec.name!r}: a write flow needs a confirm check — set "
                f"mutate.confirm_selector / confirm_text_contains / confirm_url_contains"
            )
        if on_drift == "relearn":
            raise FlowReplayError(
                f"{spec.name!r}: on_drift='relearn' is refused for a write flow (re-authoring would "
                f"re-perform the write) — re-learn manually and re-approve instead"
            )
    # A write defaults to approval-gated even without require_approved (stronger trust for writes).
    if (require_approved or is_mutate) and not meta.approved:
        raise FlowReplayError(f"{spec.name!r}: flow not approved — learn it, verify it, then approve")

    # Idempotency precheck (opt-in, one-shot writes): if the end-state already holds, skip the write.
    if await _precheck_done(spec):
        _record_run(cache, key, ok=True)
        _log.info("flow %r: write already done (idempotency precheck) — skipped", spec.name)
        return {"status": "already-done", "data": None}

    if on_drift == "relearn":
        # relearn re-authors the flow, so it needs both an agent provider and an extraction router
        if provider is None or router is None:
            dp, dr = _router(provider_name or settings.provider)
            provider = provider if provider is not None else dp
            router = router if router is not None else dr
    elif router is None and spec.extract is not None:
        # extraction only (incl. a write that also extracts a confirmation number): build just the
        # router, no agent provider. Flows that don't extract (navigate-only reads, or writes whose
        # confirm check is selector/url/text based) never call the LLM -> no router needed.
        router = build_router(provider_name or settings.provider)

    def _ok(data):
        _record_run(cache, key, ok=True)
        _log.info("flow %r: replay ok%s", spec.name, " (write confirmed)" if is_mutate else "")
        return {"status": "confirmed", "data": data} if is_mutate else data

    try:
        ok, data, reason = await _attempt_replay(spec, router, cache, key, meta, check_shape)
        if ok:
            return _ok(data)
        # The session may have expired — re-login (refresh cookies) and retry once. A WRITE flow is
        # NOT retried unless it has an idempotency precheck: a first attempt may have committed the
        # write before failing its confirm check, and a blind retry would double-submit. With a
        # precheck we re-check first and skip if the write already landed.
        retry_ok = auth_refresh and spec.login is not None and (not is_mutate or spec.mutate.has_precheck())
        if retry_ok:
            try:
                await refresh_auth(spec, headless=spec.headless)
                if await _precheck_done(spec):  # the first attempt's write may have landed
                    _record_run(cache, key, ok=True)
                    return {"status": "already-done", "data": None}
                ok, data, reason2 = await _attempt_replay(spec, router, cache, key, meta, check_shape)
                if ok:
                    return _ok(data)
                reason = f"{reason}; after auth refresh: {reason2}"
            except Exception as exc:  # noqa: BLE001 - any refresh failure -> fall through to relearn/raise
                reason = f"{reason}; auth refresh failed: {type(exc).__name__}: {exc}"
        elif is_mutate and auth_refresh and spec.login is not None:
            reason = (f"{reason}; not retrying a write after auth refresh without an idempotency "
                      f"precheck (would risk a double-submit) — add mutate.precheck_* or run "
                      f"`flow login` then replay")
        if on_drift == "relearn":  # (refused above for write flows)
            res = await learn(spec, provider=provider, router=router, cache=cache)
            if res.cached and res.found:
                return _ok(res.data)
            reason = f"replay drifted ({reason}) and re-learn failed ({res.note})"
        _record_run(cache, key, ok=False, error=reason)
        _log.warning("flow %r: replay FAILED — %s", spec.name, reason)
        raise FlowReplayError(f"{spec.name!r}: {reason}")
    except FlowReplayError:
        raise  # the failure above is already recorded in health
    except Exception as exc:  # noqa: BLE001 - an unexpected crash (browser/extract) is still a failed run
        _record_run(cache, key, ok=False, error=f"{type(exc).__name__}: {exc}")
        _log.warning("flow %r: replay crashed — %s: %s", spec.name, type(exc).__name__, exc)
        raise


# --- spec persistence (for the `ultracua flow` CLI) -------------------------------------------
def _specs_dir() -> Path:
    return Path(".ultracua") / "specs"


def _only_known(data: dict, cls) -> dict:
    """Drop keys that aren't fields of `cls` — so a spec written by another version still loads."""
    fields = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in fields}


def save_spec(spec: FlowSpec) -> Path:
    """Persist a flow spec as JSON under `.ultracua/specs/` (relative to cwd).

    Note: the spec records `storage_state` (a *path* to a cookies file), never credentials. The
    cookies file it points at is a live session — keep it out of version control (it's secret).
    """
    if callable(spec.login):
        raise ValueError("a callable `login` can't be saved — use a LoginSpec, or the library API")
    d = _specs_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{spec.name}.json"
    p.write_text(json.dumps(asdict(spec), indent=2), encoding="utf-8")
    return p


def load_spec(name: str) -> FlowSpec:
    p = _specs_dir() / f"{name}.json"
    if not p.exists():
        raise FileNotFoundError(f"no saved flow {name!r} (looked in {p})")
    data = _only_known(json.loads(p.read_text(encoding="utf-8")), FlowSpec)
    if isinstance(data.get("login"), dict):
        data["login"] = LoginSpec(**_only_known(data["login"], LoginSpec))
    if isinstance(data.get("mutate"), dict):
        data["mutate"] = MutateSpec(**_only_known(data["mutate"], MutateSpec))
    return FlowSpec(**data)


def list_specs() -> list[str]:
    d = _specs_dir()
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []
