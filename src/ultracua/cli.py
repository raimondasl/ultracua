"""`ultracua` command-line entry point.

Phase 1: runs a goal through the flow cache. First run on a (goal, url) LEARNS and caches
the flow; subsequent runs REPLAY it with no LLM. Use --mode to force learn/replay and
--fresh to clear the cached flow first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict

from .cache import FlowCache, flow_key
from .config import settings
from .flow import run_cached
from .providers import get_provider
from .timing import StepTrace


def _on_step(tr: StepTrace) -> None:
    print(tr.render())
    bits = []
    if "intent" in tr.meta:
        bits.append(f"intent={tr.meta['intent']!r}")
    if "action" in tr.meta:
        bits.append(f"action={tr.meta['action']}")
    if "ok" in tr.meta:
        bits.append(f"ok={tr.meta['ok']}")
    if tr.meta.get("note"):
        bits.append(f"note={tr.meta['note']}")
    if bits:
        print("         " + "  ".join(bits))


async def _amain(args: argparse.Namespace) -> None:
    cache = FlowCache()
    if args.fresh:
        if cache.delete(flow_key(args.goal, args.url, args.scope)):
            print("(cleared cached flow)")
    provider = get_provider(args.provider)
    if hasattr(provider, "tier"):
        provider.tier = args.tier  # honor --tier on LLM-backed providers
    print(
        f"ultracua: provider={args.provider} tier={args.tier} "
        f"fast={settings.fast_model} strong={settings.model} "
        f"mode={args.mode} headless={settings.headless}\n"
    )
    report = await run_cached(
        args.url,
        args.goal,
        provider,
        cache=cache,
        mode=args.mode,
        scope=args.scope,
        on_step=_on_step,
    )
    print(
        f"\nmode={report.mode} success={report.success} "
        f"llm_calls={report.llm_calls} healed={report.healed_steps}"
    )
    steps = report.step_traces
    if steps:
        print(
            f"{len(steps)} step(s), avg {report.avg_step_ms:.0f} ms/step, "
            f"total {report.total_ms:.0f} ms"
        )


# --- `ultracua flow` subcommand: define + run recurring flows -------------------------------
def _parse_headers(items) -> dict:
    headers = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--header must be K=V, got {it!r}")
        k, v = it.split("=", 1)
        headers[k] = v
    return headers


def _login_from_args(args: argparse.Namespace):
    """Build a LoginSpec from the shared --login-* flags (added by _add_login_args)."""
    from .flows import LoginSpec

    return LoginSpec(
        url=args.login_url, username_env=args.username_env, password_env=args.password_env,
        username_selector=args.username_selector, password_selector=args.password_selector,
        submit_selector=args.submit_selector, success_selector=args.success_selector,
        success_url_contains=args.success_url_contains, timeout_ms=args.timeout_ms,
    )


def _mutate_from_args(args: argparse.Namespace):
    """Build a MutateSpec from the shared --confirm-*/--precheck-* flags (added by _add_mutate_args)."""
    from .flows import MutateSpec

    return MutateSpec(
        confirm_selector=args.confirm_selector, confirm_text_contains=args.confirm_text_contains,
        confirm_url_contains=args.confirm_url_contains, timeout_ms=args.mutate_timeout_ms,
        precheck_url=args.precheck_url, precheck_selector=args.precheck_selector,
        precheck_text_contains=args.precheck_text_contains,
        precheck_url_contains=args.precheck_url_contains,
    )


def _has_confirm_args(args: argparse.Namespace) -> bool:
    return bool(args.confirm_selector or args.confirm_text_contains or args.confirm_url_contains)


async def _flow_learn(args: argparse.Namespace) -> None:
    from .flows import FlowSpec, learn, save_spec

    login = _login_from_args(args) if args.login_url else None
    mutate = _mutate_from_args(args) if _has_confirm_args(args) else None
    spec = FlowSpec(
        name=args.name, start_url=args.url, goal=args.goal, extract=args.extract,
        headers=_parse_headers(args.header) or None, storage_state=args.storage_state,
        login=login, mutate=mutate, pin_read=args.pin_read, headless=(False if args.headed else None),
    )
    if args.fresh:
        FlowCache().delete(flow_key(spec.goal, spec.start_url, spec.scope))
    save_spec(spec)
    res = await learn(spec, provider_name=args.provider, samples=args.samples)
    print(f"flow {spec.name!r}: cached={res.cached} found={res.found} ({len(res.steps)} step(s))")
    for i, s in enumerate(res.steps):
        print(f"  {i}: {s.action} {s.intent!r}")
    print("data: " + json.dumps(res.data, ensure_ascii=False))
    if args.pin_read:
        print("pinned a deterministic 0-LLM read — replay needs no LLM or API key."
              if res.pinned else
              "could NOT pin a 0-LLM read (answer isn't a unique scalar) — replay uses the LLM extractor.")
    if not res.cached:
        print("WARNING: no replayable flow was cached (the agent took no clean steps).")
    elif not res.approved:
        print(f"verify the above, then approve it: ultracua flow approve --name {spec.name}")


async def _flow_replay(args: argparse.Namespace) -> None:
    from .flows import FlowReplayError, load_spec, replay

    spec = load_spec(args.name)
    try:
        data = await replay(
            spec, provider_name=args.provider,
            require_approved=args.require_approved, on_drift=args.on_drift,
            auth_refresh=args.auth_refresh,
        )
    except FlowReplayError as exc:
        raise SystemExit(f"REPLAY FAILED: {exc}")
    print(json.dumps(data, ensure_ascii=False))


def _flow_approve(args: argparse.Namespace) -> None:
    from .flows import approve, load_spec

    spec = load_spec(args.name)
    approve(spec)
    print(f"approved {spec.name!r} — `flow replay --name {spec.name} --require-approved` will run it")


async def _flow_login(args: argparse.Namespace) -> None:
    from .flows import FlowReplayError, load_spec, refresh_auth

    spec = load_spec(args.name)
    try:
        await refresh_auth(spec)  # verifies the login before saving cookies; raises on failure
    except FlowReplayError as exc:
        raise SystemExit(f"LOGIN FAILED: {exc}")
    print(f"login OK — refreshed auth for {spec.name!r} -> {spec.storage_state}")


def _flow_set_login(args: argparse.Namespace) -> None:
    from .flows import load_spec, save_spec

    spec = load_spec(args.name)
    spec.login = _login_from_args(args)
    if args.storage_state:
        spec.storage_state = args.storage_state
    if not spec.storage_state:
        raise SystemExit("set --storage-state (a path) too, so refreshed cookies have somewhere to go")
    save_spec(spec)
    print(f"set login on {spec.name!r} (url={args.login_url}; creds from env "
          f"{args.username_env}/{args.password_env}). Refresh now: "
          f"ultracua flow login --name {spec.name}")


def _flow_set_mutate(args: argparse.Namespace) -> None:
    from .flows import load_spec, save_spec

    spec = load_spec(args.name)
    if not _has_confirm_args(args):
        raise SystemExit("a write flow needs a confirm check — set at least one of "
                         "--confirm-selector / --confirm-text-contains / --confirm-url-contains")
    spec.mutate = _mutate_from_args(args)
    save_spec(spec)
    print(f"set write/mutate on {spec.name!r} — replay will verify the write landed and is now "
          f"approval-gated by default. Re-learn it (performs the write once) then approve: "
          f"ultracua flow learn --name {spec.name} ...; ultracua flow approve --name {spec.name}")


def _flow_inspect(args: argparse.Namespace) -> None:
    from .flows import load_spec

    spec = load_spec(args.name)
    print(json.dumps(asdict(spec), indent=2))
    cached = FlowCache().get(flow_key(spec.goal, spec.start_url, spec.scope))
    if cached:
        print(f"\nlearned {len(cached.steps)} step(s):")
        for i, s in enumerate(cached.steps):
            print(f"  {i}: {s.action} {s.intent!r}")
    else:
        print("\n(no learned flow cached yet — run: ultracua flow learn ...)")


def _flow_list() -> None:
    from .flows import list_specs

    names = list_specs()
    print("\n".join(names) if names else "(no saved flows)")


def _ago(ts: float) -> str:
    if not ts:
        return "never"
    import time as _t

    d = max(0.0, _t.time() - ts)
    for unit, sec in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= sec:
            return f"{int(d / sec)}{unit} ago"
    return f"{int(d)}s ago"


def _flow_status(args: argparse.Namespace) -> None:
    from .flows import health, list_specs, load_spec

    names = [args.name] if args.name else list_specs()
    if not names:
        print("(no saved flows)")
        return
    stale_after = args.stale_after * 3600 if args.stale_after else None  # hours -> seconds
    for name in names:
        h = health(load_spec(name), stale_after=stale_after)
        print(f"{h.name}: {h.status}  approved={h.approved}  "
              f"runs={h.runs} ok={h.successes} fails={h.consecutive_failures}  "
              f"last_ok={_ago(h.last_ok_ts)}")
        if h.last_error and h.status not in ("healthy", "never-run", "not-learned"):
            print(f"    last error: {h.last_error}")


def _post_alert(url: str, failed: list) -> None:
    import urllib.request

    lines = "\n".join(f"- {r.name}: {r.error}" for r in failed)
    payload = {"text": f"ultracua: {len(failed)} flow(s) failed\n{lines}",
               "failed": [{"name": r.name, "error": r.error} for r in failed]}
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=10)  # noqa: S310 - user-supplied alert endpoint
        print("alert webhook posted")
    except Exception as exc:  # noqa: BLE001 - alerting is best-effort; never fail the run on it
        print(f"(alert webhook failed: {type(exc).__name__}: {exc})")


def _flow_run_all(args: argparse.Namespace) -> None:
    from pathlib import Path

    from .flows import run_all

    results = asyncio.run(run_all(
        approved_only=not args.include_unapproved, include_writes=args.include_writes,
        concurrency=args.concurrency, on_drift=args.on_drift, provider_name=args.provider,
    ))
    rank = {"failed": 0, "ok": 1, "skipped": 2}
    for r in sorted(results, key=lambda r: (rank.get(r.status, 3), r.name)):
        mark = {"ok": "OK", "failed": "FAIL", "skipped": "SKIP"}.get(r.status, r.status.upper())
        detail = f"{r.ms:.0f}ms " if r.ms else ""
        detail += json.dumps(r.data, ensure_ascii=False) if r.status == "ok" else (r.error or "")
        print(f"  [{mark:<4}] {r.name:<24} {detail}")
    ok = sum(1 for r in results if r.status == "ok")
    failed = [r for r in results if r.status == "failed"]
    skipped = sum(1 for r in results if r.status == "skipped")
    print(f"\n== {ok} ok, {len(failed)} failed, {skipped} skipped (of {len(results)}) ==")
    if args.json:
        record = {"ok": ok, "failed": len(failed), "skipped": skipped, "total": len(results),
                  "flows": [{"name": r.name, "status": r.status, "ms": round(r.ms), "error": r.error}
                            for r in results]}
        Path(args.json).write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    if failed and args.alert_webhook:
        _post_alert(args.alert_webhook, failed)
    raise SystemExit(1 if failed else 0)  # cron alerts on a non-zero exit


def _flow_record(args: argparse.Namespace) -> None:
    from .flows import FlowSpec, caption_for, record

    # A confirm check (--confirm-*) DECLARES this a WRITE recording — the recorder can't infer the
    # action-completion signal, so a demonstrated write must declare it (just like `flow learn`).
    mutate = _mutate_from_args(args) if _has_confirm_args(args) else None
    spec = FlowSpec(name=args.name, start_url=args.url, goal=args.goal,
                    storage_state=args.storage_state, mutate=mutate)

    async def _demo(page) -> None:  # the "stop signal": the human demos in the browser, then presses Enter
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, input, "\n>>> Demonstrate the flow in the browser window, then press Enter here to finish… ")

    print(f"opening {args.url} — a browser window will appear; perform the flow, then return here.")
    # Opt-in intent caption: one best-effort post-hoc LLM call to label the steps (off the replay path).
    # None when no LLM is configured -> placeholder intents, recording stays key-less.
    res = asyncio.run(record(spec, demo=_demo, headless=False, caption=caption_for(getattr(args, "provider", None))))
    print(f"\ncaptured {len(res.steps)} step(s):")
    for s in res.steps:
        name = (s.locator.name if s.locator else "") or (s.locator.tag if s.locator else "")
        marker = "  [write — gated]" if s.mutating else ""
        if getattr(s, "confirm", None) is not None:  # Phase G: show the per-write barrier bound to this write
            c = s.confirm
            sig = c.confirm_selector or c.confirm_text_contains or c.confirm_url_contains
            marker += f" → confirm: {sig!r}"
        print(f"  {s.action} {name!r}" + (f" = {s.text!r}" if s.text else "") + marker)
    if res.cached:
        from .flows import save_spec

        save_spec(spec)  # persist so `flow replay/approve/run-all --name` find it
        if res.is_write:
            print(f"\nrecorded WRITE flow {spec.name!r} (gated + idempotency-keyed; refuses under drift). "
                  f"It is approval-gated — verify your demo, then approve to run unattended:\n"
                  f"    ultracua flow approve --name {spec.name}")
            if res.note:
                print(res.note)
        else:
            print(f"\nrecorded + verified {spec.name!r} (replays 0-LLM). Approve it to run unattended:\n"
                  f"    ultracua flow approve --name {spec.name}")
    else:
        raise SystemExit(f"\nNOT recorded: {res.note}")


def _flow_canary(args: argparse.Namespace) -> None:
    from .flows import canary, canary_all, load_spec

    if args.name:
        results = [asyncio.run(canary(load_spec(args.name)))]
    else:
        results = asyncio.run(canary_all(concurrency=args.concurrency))
    rank = {"stale": 0, "error": 1, "not-learned": 2, "fresh": 3}
    for r in sorted(results, key=lambda r: (rank.get(r.status, 9), r.name)):
        mark = {"fresh": "FRESH", "stale": "STALE", "error": "ERROR",
                "not-learned": "NEW"}.get(r.status, r.status.upper())
        print(f"  [{mark:<5}] {r.name:<24} {r.detail}")
    stale = [r for r in results if r.status in ("stale", "error")]
    fresh = sum(1 for r in results if r.status == "fresh")
    print(f"\n== {fresh} fresh, {len(stale)} stale/error (of {len(results)}) ==")
    raise SystemExit(1 if stale else 0)  # cron alerts on a non-zero exit


def _add_login_args(parser, *, url_required: bool) -> None:
    """Shared --login-* flags for `learn` (login optional) and `set-login` (login required)."""
    parser.add_argument("--login-url", dest="login_url", required=url_required,
                        help="login page URL — enables auth refresh on drift.")
    parser.add_argument("--username-env", dest="username_env", default="ULTRACUA_USERNAME",
                        help="env var holding the login username (default ULTRACUA_USERNAME).")
    parser.add_argument("--password-env", dest="password_env", default="ULTRACUA_PASSWORD",
                        help="env var holding the login password (default ULTRACUA_PASSWORD).")
    parser.add_argument("--username-selector", dest="username_selector")
    parser.add_argument("--password-selector", dest="password_selector")
    parser.add_argument("--submit-selector", dest="submit_selector",
                        help="click target to submit (omit to press Enter in the password field).")
    parser.add_argument("--success-selector", dest="success_selector",
                        help="element present only once logged in. If neither this nor "
                             "--success-url-contains is set, success = navigated off the login URL "
                             "(override for SPA logins that stay on the same URL).")
    parser.add_argument("--success-url-contains", dest="success_url_contains",
                        help="substring the post-login URL must contain (login success check).")
    parser.add_argument("--timeout-ms", dest="timeout_ms", type=int,
                        help="per-step timeout (ms) for the login form actions.")


def _add_mutate_args(parser) -> None:
    """Shared --confirm-*/--precheck-* flags marking a WRITE flow (Phase D). Setting any
    --confirm-* turns the flow into a write flow whose replay verifies the write landed."""
    parser.add_argument("--confirm-selector", dest="confirm_selector",
                        help="element present only once the write committed (action-completion check).")
    parser.add_argument("--confirm-text-contains", dest="confirm_text_contains",
                        help="substring the post-write page text must contain (action-completion check).")
    parser.add_argument("--confirm-url-contains", dest="confirm_url_contains",
                        help="substring the post-write URL must contain (action-completion check).")
    parser.add_argument("--mutate-timeout-ms", dest="mutate_timeout_ms", type=int,
                        help="how long (ms) to wait for the confirmation to appear.")
    parser.add_argument("--precheck-url", dest="precheck_url",
                        help="idempotency precheck URL (default: the flow's start url).")
    parser.add_argument("--precheck-selector", dest="precheck_selector",
                        help="if present, the write was already done -> skip it (one-shot writes).")
    parser.add_argument("--precheck-text-contains", dest="precheck_text_contains",
                        help="if this text is present, the write was already done -> skip it.")
    parser.add_argument("--precheck-url-contains", dest="precheck_url_contains",
                        help="if the precheck URL contains this, the write was already done -> skip it.")


def _flow_main(argv) -> None:
    p = argparse.ArgumentParser(prog="ultracua flow", description="Define + run recurring browser flows.")
    sub = p.add_subparsers(dest="cmd", required=True)
    prov = dict(default=settings.provider, choices=["anthropic", "openai", "gemini", "mock"])

    pl = sub.add_parser("learn", help="LLM-author + cache a flow, then inspect it.")
    pl.add_argument("--name", required=True)
    pl.add_argument("--url", required=True)
    pl.add_argument("--goal", required=True)
    pl.add_argument("--extract", help="instruction for what data to pull (omit for navigate-only).")
    pl.add_argument("--pin-read", dest="pin_read", action="store_true",
                    help="pin a deterministic 0-LLM read of a scalar answer (replay needs no LLM/key).")
    pl.add_argument("--header", action="append", help="auth header K=V (repeatable).")
    pl.add_argument("--storage-state", dest="storage_state", help="Playwright storage_state JSON path (cookie auth).")
    _add_login_args(pl, url_required=False)
    _add_mutate_args(pl)  # set any --confirm-* to make this a WRITE flow (Phase D)
    pl.add_argument("--provider", **prov)
    pl.add_argument("--headed", action="store_true")
    pl.add_argument("--fresh", action="store_true", help="clear any cached flow first.")
    pl.add_argument("--samples", type=int, default=1,
                    help="re-author up to N times and keep the first verified flow (costs N learns; "
                         "raises discovery success on flaky pages). Default 1.")
    pl.add_argument("--verbose", "-v", action="store_true", help="log learn/heal events (INFO).")

    pr = sub.add_parser("replay", help="Replay a saved flow (0-LLM nav); print the data; fails loud on drift.")
    pr.add_argument("--name", required=True)
    pr.add_argument("--provider", **prov)
    pr.add_argument("--require-approved", dest="require_approved", action="store_true",
                    help="refuse to run a flow that hasn't been approved.")
    pr.add_argument("--on-drift", dest="on_drift", default="raise", choices=["raise", "relearn"],
                    help="raise = fail loud on drift (default); relearn = re-author the flow instead.")
    pr.add_argument("--no-auth-refresh", dest="auth_refresh", action="store_false",
                    help="don't re-login on drift (default: refresh an expired session and retry).")
    pr.add_argument("--verbose", "-v", action="store_true", help="log replay/heal/drift events (INFO).")

    pa = sub.add_parser("approve", help="Mark a learned flow trusted (for --require-approved replays).")
    pa.add_argument("--name", required=True)

    plg = sub.add_parser("login", help="Re-authenticate a flow now (refresh its storage_state cookies).")
    plg.add_argument("--name", required=True)

    psl = sub.add_parser("set-login", help="Attach/replace login + auth refresh on a saved flow.")
    psl.add_argument("--name", required=True)
    psl.add_argument("--storage-state", dest="storage_state",
                     help="where to save refreshed cookies (required if the flow has none yet).")
    _add_login_args(psl, url_required=True)

    psm = sub.add_parser("set-mutate", help="Mark a saved flow a WRITE flow + how to confirm it (Phase D).")
    psm.add_argument("--name", required=True)
    _add_mutate_args(psm)

    pi = sub.add_parser("inspect", help="Print a saved flow's spec + learned steps.")
    pi.add_argument("--name", required=True)

    pst = sub.add_parser("status", help="Show health (runs / last success / drift) for saved flows.")
    pst.add_argument("--name", help="a single flow (default: all).")
    pst.add_argument("--stale-after", dest="stale_after", type=float,
                     help="hours since last success after which a healthy flow counts as 'stale'.")

    pra = sub.add_parser("run-all", help="Replay every saved flow (read + approved by default); "
                                         "report + alert; exits non-zero if any fails. Point cron at this.")
    pra.add_argument("--provider", **prov)
    pra.add_argument("--include-unapproved", dest="include_unapproved", action="store_true",
                     help="also run flows that aren't approved.")
    pra.add_argument("--include-writes", dest="include_writes", action="store_true",
                     help="also run write/mutate flows (PERFORMS the writes).")
    pra.add_argument("--concurrency", type=int, default=None,
                     help="max flows run at once (default ULTRACUA_CONCURRENCY).")
    pra.add_argument("--on-drift", dest="on_drift", default="raise", choices=["raise", "relearn"])
    pra.add_argument("--json", dest="json", help="write a machine-readable run record to this path.")
    pra.add_argument("--alert-webhook", dest="alert_webhook",
                     help="POST a JSON alert here if any flow fails (Slack/Discord/etc. incoming webhook).")
    pra.add_argument("--verbose", "-v", action="store_true", help="log each replay (INFO).")

    prc = sub.add_parser("record", help="RECORD a flow by demonstrating it in a headed browser. Reads are "
                                        "verify-by-replayed; a WRITE needs a --confirm-* check (then it is "
                                        "gated + idempotency-keyed). Approve it to run unattended.")
    prc.add_argument("--name", required=True, help="name to save the flow under.")
    prc.add_argument("--url", required=True, help="start URL to open for the demonstration.")
    prc.add_argument("--goal", required=True, help="a short description of the flow (forms the cache key).")
    prc.add_argument("--storage-state", dest="storage_state",
                     help="a Playwright storage_state JSON (cookies) to start authenticated.")
    _add_mutate_args(prc)  # set any --confirm-* to DECLARE + safely capture a WRITE flow

    pca = sub.add_parser("canary", help="Cheap freshness probe: does each flow still START (0-LLM, "
                                        "read-only, no health record)? Exits non-zero if any is stale. "
                                        "Point cron at this MORE often than run-all to catch rot early.")
    pca.add_argument("--name", help="a single flow (default: all).")
    pca.add_argument("--concurrency", type=int, default=None,
                     help="max flows probed at once (default ULTRACUA_CONCURRENCY).")

    sub.add_parser("list", help="List saved flows.")

    args = p.parse_args(argv)
    from .obs import configure_logging
    configure_logging("INFO" if getattr(args, "verbose", False) else settings.log_level)
    if args.cmd == "learn":
        asyncio.run(_flow_learn(args))
    elif args.cmd == "replay":
        asyncio.run(_flow_replay(args))
    elif args.cmd == "approve":
        _flow_approve(args)
    elif args.cmd == "login":
        asyncio.run(_flow_login(args))
    elif args.cmd == "set-login":
        _flow_set_login(args)
    elif args.cmd == "set-mutate":
        _flow_set_mutate(args)
    elif args.cmd == "inspect":
        _flow_inspect(args)
    elif args.cmd == "status":
        _flow_status(args)
    elif args.cmd == "run-all":
        _flow_run_all(args)
    elif args.cmd == "record":
        _flow_record(args)
    elif args.cmd == "canary":
        _flow_canary(args)
    elif args.cmd == "list":
        _flow_list()


def main() -> None:
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] == "flow":  # `ultracua flow ...` — recurring-flow management
        return _flow_main(argv[1:])

    p = argparse.ArgumentParser(
        prog="ultracua",
        description="ultracua — a browser CUA with a learn-once / replay-fast flow cache.",
    )
    p.add_argument("--url", required=True, help="Starting URL.")
    p.add_argument("--goal", required=True, help="Natural-language goal.")
    p.add_argument(
        "--provider",
        default=settings.provider,
        choices=["anthropic", "openai", "gemini", "mock"],
        help="Provider for learn/heal (default from ULTRACUA_PROVIDER).",
    )
    p.add_argument(
        "--tier",
        default=settings.tier,
        choices=["fast", "strong"],
        help="Default LLM tier for routine steps (escalates to strong on low confidence).",
    )
    p.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "learn", "replay"],
        help="auto: replay if cached else learn; learn: force learn; replay: cache-only.",
    )
    p.add_argument("--scope", default="default", help="Cache scope namespace.")
    p.add_argument(
        "--fresh", action="store_true", help="Delete the cached flow before running."
    )
    p.add_argument("--verbose", "-v", action="store_true", help="log learn/replay/heal events (INFO).")
    args = p.parse_args()
    from .obs import configure_logging
    configure_logging("INFO" if args.verbose else settings.log_level)
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
