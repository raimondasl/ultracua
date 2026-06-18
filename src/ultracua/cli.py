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


async def _flow_learn(args: argparse.Namespace) -> None:
    from .flows import FlowSpec, LoginSpec, learn, save_spec

    login = None
    if args.login_url:
        login = LoginSpec(
            url=args.login_url, username_env=args.username_env, password_env=args.password_env,
            username_selector=args.username_selector, password_selector=args.password_selector,
            submit_selector=args.submit_selector, success_selector=args.success_selector,
            success_url_contains=args.success_url_contains,
        )
    spec = FlowSpec(
        name=args.name, start_url=args.url, goal=args.goal, extract=args.extract,
        headers=_parse_headers(args.header) or None, storage_state=args.storage_state,
        login=login, headless=(False if args.headed else None),
    )
    if args.fresh:
        FlowCache().delete(flow_key(spec.goal, spec.start_url, spec.scope))
    save_spec(spec)
    res = await learn(spec, provider_name=args.provider)
    print(f"flow {spec.name!r}: cached={res.cached} found={res.found} ({len(res.steps)} step(s))")
    for i, s in enumerate(res.steps):
        print(f"  {i}: {s.action} {s.intent!r}")
    print("data: " + json.dumps(res.data, ensure_ascii=False))
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
    from .flows import load_spec, refresh_auth

    spec = load_spec(args.name)
    await refresh_auth(spec)
    print(f"refreshed auth for {spec.name!r} -> {spec.storage_state}")


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
    for name in names:
        h = health(load_spec(name))
        print(f"{h.name}: {h.status}  approved={h.approved}  "
              f"runs={h.runs} ok={h.successes} fails={h.consecutive_failures}  "
              f"last_ok={_ago(h.last_ok_ts)}")
        if h.last_error and h.status == "failing":
            print(f"    last error: {h.last_error}")


def _flow_main(argv) -> None:
    p = argparse.ArgumentParser(prog="ultracua flow", description="Define + run recurring browser flows.")
    sub = p.add_subparsers(dest="cmd", required=True)
    prov = dict(default=settings.provider, choices=["anthropic", "openai", "gemini", "mock"])

    pl = sub.add_parser("learn", help="LLM-author + cache a flow, then inspect it.")
    pl.add_argument("--name", required=True)
    pl.add_argument("--url", required=True)
    pl.add_argument("--goal", required=True)
    pl.add_argument("--extract", help="instruction for what data to pull (omit for navigate-only).")
    pl.add_argument("--header", action="append", help="auth header K=V (repeatable).")
    pl.add_argument("--storage-state", dest="storage_state", help="Playwright storage_state JSON path (cookie auth).")
    pl.add_argument("--login-url", dest="login_url", help="login page URL — enables auth refresh on drift.")
    pl.add_argument("--username-env", dest="username_env", default="ULTRACUA_USERNAME",
                    help="env var holding the login username (default ULTRACUA_USERNAME).")
    pl.add_argument("--password-env", dest="password_env", default="ULTRACUA_PASSWORD",
                    help="env var holding the login password (default ULTRACUA_PASSWORD).")
    pl.add_argument("--username-selector", dest="username_selector")
    pl.add_argument("--password-selector", dest="password_selector")
    pl.add_argument("--submit-selector", dest="submit_selector")
    pl.add_argument("--success-selector", dest="success_selector",
                    help="element present only once logged in (login success check).")
    pl.add_argument("--success-url-contains", dest="success_url_contains",
                    help="substring the post-login URL must contain (login success check).")
    pl.add_argument("--provider", **prov)
    pl.add_argument("--headed", action="store_true")
    pl.add_argument("--fresh", action="store_true", help="clear any cached flow first.")

    pr = sub.add_parser("replay", help="Replay a saved flow (0-LLM nav); print the data; fails loud on drift.")
    pr.add_argument("--name", required=True)
    pr.add_argument("--provider", **prov)
    pr.add_argument("--require-approved", dest="require_approved", action="store_true",
                    help="refuse to run a flow that hasn't been approved.")
    pr.add_argument("--on-drift", dest="on_drift", default="raise", choices=["raise", "relearn"],
                    help="raise = fail loud on drift (default); relearn = re-author the flow instead.")
    pr.add_argument("--no-auth-refresh", dest="auth_refresh", action="store_false",
                    help="don't re-login on drift (default: refresh an expired session and retry).")

    pa = sub.add_parser("approve", help="Mark a learned flow trusted (for --require-approved replays).")
    pa.add_argument("--name", required=True)

    plg = sub.add_parser("login", help="Re-authenticate a flow now (refresh its storage_state cookies).")
    plg.add_argument("--name", required=True)

    pi = sub.add_parser("inspect", help="Print a saved flow's spec + learned steps.")
    pi.add_argument("--name", required=True)

    pst = sub.add_parser("status", help="Show health (runs / last success / drift) for saved flows.")
    pst.add_argument("--name", help="a single flow (default: all).")

    sub.add_parser("list", help="List saved flows.")

    args = p.parse_args(argv)
    if args.cmd == "learn":
        asyncio.run(_flow_learn(args))
    elif args.cmd == "replay":
        asyncio.run(_flow_replay(args))
    elif args.cmd == "approve":
        _flow_approve(args)
    elif args.cmd == "login":
        asyncio.run(_flow_login(args))
    elif args.cmd == "inspect":
        _flow_inspect(args)
    elif args.cmd == "status":
        _flow_status(args)
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
    args = p.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
