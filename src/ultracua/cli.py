"""`ultracua` command-line entry point.

Phase 1: runs a goal through the flow cache. First run on a (goal, url) LEARNS and caches
the flow; subsequent runs REPLAY it with no LLM. Use --mode to force learn/replay and
--fresh to clear the cached flow first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from typing import Optional

from .cache import CacheUnreadableError, FlowCache, flow_key
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
    # CLI-2. `run_cached` RETURNS `success=False` rather than raising on every failure path (miss,
    # escalate, verify-failed, the unattributed-write refusal), and this printed the flag without ever
    # converting it into an exit code — so a scripted caller checking `$?` saw success on total failure.
    #
    # The note is printed for the reason flow.py states about populating it: "a bare success=False with
    # no reason ... is the fail-loud inviolable read as fail-quiet". The daemon already surfaces it; this
    # surface dropped the explanation the engine had already computed.
    if report.note:
        print(f"reason: {report.note}")
    raise SystemExit(0 if report.success else 1)


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


def _warn_if_approval_stale(spec) -> None:
    """Say it at the moment it happens: re-authoring an APPROVED flow's steps invalidates the approval, so
    the flow is now UNRUNNABLE until a human re-approves. `flow status` also reports it, but an operator who
    just ran `flow learn`/`flow record` and saw "cached=True" would otherwise not find out until the next
    scheduled run refused."""
    try:
        from .flows import health
    except Exception:  # noqa: BLE001 — a courtesy warning must never break the command that succeeded
        return
    if not health(spec).approval_stale:
        return
    print(f"WARNING: {spec.name!r} is APPROVED, but these steps are NOT the ones that were approved — replay "
          f"will now REFUSE (stale_approval).\n"
          f"         Review them and re-approve:\n"
          f"           ultracua flow inspect --name {spec.name}\n"
          f"           ultracua flow approve --name {spec.name}")


def _warn_if_shape_baseline_kept(spec, data, *, had_data: bool = True) -> None:
    """An APPROVED flow deliberately KEEPS the data shape + value contracts a human blessed, so a re-author does
    NOT re-baseline them. Say so, rather than let "cached=True" / "recorded + verified" imply the repair landed.

    `had_data=False` (the recorder, which extracts nothing) just states that the baselines were preserved. With
    data, the check uses the SAME lenient `_shape_matches` the replay gate uses, so it can't cry wolf over an
    empty-vs-populated list."""
    from .cache import FlowCache as _FC
    from .flows import _load_meta, _shape_matches, _shape_of, flow_key as _fk

    meta = _load_meta(_FC(), _fk(spec.goal, spec.start_url, spec.scope))
    if meta.shape is None and meta.contracts is None:
        return                                     # nothing was preserved, nothing to warn about
    fresh = _shape_of(data) if had_data else None
    if fresh is not None and _shape_matches(meta.shape, fresh):
        return                                     # the replay gate would accept it — stay quiet
    # A WRITE flow must NEVER be told to re-learn: re-authoring would actuate the write again. Its safe
    # authoring path is the recorder (gated + wire-attributed + approval-gated).
    redo = (f"ultracua flow record --name {spec.name} ..." if spec.write.declares_write
            else f"ultracua flow learn --name {spec.name} ...")
    detail = (f"this run's shape is {fresh}, so replay will STILL fail loud on shape drift"
              if fresh is not None else
              "a re-record does not re-baseline them, so replay still enforces the OLD shape/contracts")
    print(f"WARNING: {spec.name!r} is APPROVED, so its blessed data shape + value contracts were KEPT "
          f"({meta.shape}) — {detail}.\n"
          f"         To adopt the new baseline deliberately:\n"
          f"           ultracua flow unapprove --name {spec.name}\n"
          f"           {redo}\n"
          f"           ultracua flow approve --name {spec.name}")


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
        FlowCache().delete(spec.key)
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
        # CLI-3, and the MESSAGE is the worse half. The generic line below claims "the agent took no
        # clean steps", which for the refusal population is false and misdirecting: the steps were fine
        # and the flow was refused on write safety. `res.note` already carries the precise reason AND its
        # remedy, and was discarded. Exit nonzero too, so a provisioning script can tell a refused flow
        # from a learned one.
        print(f"WARNING: nothing was cached — {res.note}" if res.note else
              "WARNING: no replayable flow was cached (the agent took no clean steps).")
        raise SystemExit(1)
    elif not res.approved:
        print(f"verify the above, then approve it: ultracua flow approve --name {spec.name}")
    else:
        _warn_if_approval_stale(spec)
        _warn_if_shape_baseline_kept(spec, res.data)


async def _flow_dry_run(args: argparse.Namespace) -> None:
    """Show what a write flow WOULD send, with every write held. The pre-approval artifact.

    Bodies are printed and never written anywhere: no `--json`, no HAR, nothing on disk. A held body is
    whatever the page computed, which for a real flow can include values a human typed — persisting it
    would create a secrets-at-rest problem the rest of the project takes care to avoid.
    """
    from .dryrun import ATTRIBUTED
    from .flows import dry_run, load_spec

    spec = load_spec(args.name)
    params: Optional[dict] = None
    for item in getattr(args, "param", None) or []:
        k, sep, v = item.partition("=")
        if not sep:
            raise SystemExit(f"--param expects NAME=VALUE, got {item!r}")
        params = params or {}
        params[k.strip()] = v
    rep = await dry_run(spec, params)

    if rep.aborted:
        print(f"DRY RUN ABORTED ({rep.aborted}): {rep.abort_detail}")
        print("Nothing was written. An abort means a channel could not be PROVEN held — it is not a "
              "report about the flow.")
        raise SystemExit(2)

    print(f"dry run of {spec.name!r} — {len(rep.held)} request(s) HELD, none sent\n")
    for h in rep.held:
        # Keyed off the ONE attributed state, never off a list of the noisy ones: `== "ambiguous"` would
        # render a state added tomorrow through the CONFIDENT branch, which is the quiet-by-default
        # failure this project already shipped once (R3.9/CLI-1).
        if h.attribution == ATTRIBUTED and h.step >= 0:
            where = f"step {h.step} {h.intent!r}"
        elif h.attribution == "ungated":
            where = "outside any step"
        else:
            # Never render this as a step. A write deferred out of an earlier step arrives inside a
            # later step's window, and naming either one is the R3.12 defect.
            cands = ", ".join(str(c) for c in h.candidates) or "unknown"
            where = f"NOT ATTRIBUTABLE to a step ({h.attribution}) — candidates: {cands}"
        print(f"  HELD  {where}")
        print(f"        {h.method} {h.url}")
        if h.idempotency_key:
            print(f"        Idempotency-Key: {h.idempotency_key}")
        if h.body:
            print(f"        body: {h.body[:500]}")
        if h.telemetry:
            print("        (classified as telemetry — held anyway; classification decides reporting, "
                  "never whether bytes are released)")
        print()

    # THREE numbers, never two. "1 of 1 held" would read as complete coverage of a 3-write flow that
    # stopped at the first hold, which is the single most dangerous thing this report could imply.
    print(f"== {rep.writes_reached} of {rep.writes_planned} planned write(s) reached; "
          f"{rep.requests_held} request(s) held ==")
    for w in rep.warnings:
        print(f"  ! {w}")
    for g in rep.approval_gates_skipped:
        print(f"  ~ approval gate skipped: {g}")
    if rep.precheck_skipped:
        print("  ~ the one-shot idempotency precheck was skipped (it opens an unarbitered browser)")
    print("\nConfirm barriers were NOT evaluated: a held write produces a synthesized response, so a "
          "barrier could only report 'held — unverifiable'.")
    print("This is not evidence the flow works. It is evidence of what it would send.")


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
    from .cache import FlowCache, flow_key
    from .flows import _contracts_hash, _load_meta, _slots_hash, approve, load_spec

    if getattr(args, "all", False):
        _flow_approve_all(args)
        return
    if not args.name:
        raise SystemExit("approve needs --name NAME (or --all to re-approve a whole read fleet)")
    spec = load_spec(args.name)
    # One `approve` stamps THREE bindings — the recipe, the slot schema, and the value-contract overlay. Read
    # the BEFORE state so we can say which of them this approval actually moved: silently re-blessing a widened
    # slot domain (or a loosened contract) as a side effect of blessing a recipe is how a gate gets defeated by
    # the very command meant to enforce it.
    before = _load_meta(FlowCache(), spec.key)
    also = []
    if before.approved and _slots_hash(spec) != before.slots_hash:
        also.append("its SLOT SCHEMA (a changed/widened slot domain — check `flow inspect --name "
                    f"{spec.name}`)")
    if before.approved and _contracts_hash(spec) != before.contracts_hash:
        also.append("its VALUE CONTRACTS (a tightened or loosened guarantee — check `flow contracts --name "
                    f"{spec.name}`)")
    approve(spec)
    print(f"approved {spec.name!r} — `flow replay --name {spec.name} --require-approved` will run it")
    if also:
        print("NOTE: this also re-approved " + "; and ".join(also))


def _step_line(i: int, s, spec) -> str:
    """One step, as a human reviewing an approval needs to see it: everything the approval digest BINDS.
    A step whose value comes from a SECRET slot prints the slot name, never the frozen literal."""
    parts = [f"  {i}: {s.action} {s.intent!r}"]
    if s.locator is not None:
        what = s.locator.name or s.locator.testid or s.locator.css or s.locator.elem_id or ""
        parts.append(f"  -> {s.locator.role or s.locator.tag or '?'} {what!r}")
    if s.slot:
        slot = (spec.slots or {}).get(s.slot)
        parts.append(f"  [slot {s.slot}"
                     + (" SECRET" if slot is not None and getattr(slot, "secret", False) else "") + "]")
    if getattr(s, "secret", False):
        # A CREDENTIAL field. Defence in depth: the recorder already blanks the value at capture, so there
        # should be nothing to leak — but this renderer feeds BOTH `flow inspect` and `flow approve --all`
        # (which reprints fleet-wide), and it must never be the thing that echoes a secret from an older
        # cache file or a hand-edit. Shown, not hidden: a human approving this needs to see the field is here.
        parts.append('  [SECRET FIELD]  text="***"')
    elif s.text is not None and s.text != "":
        slot = (spec.slots or {}).get(s.slot) if s.slot else None
        if slot is not None and getattr(slot, "secret", False):
            parts.append('  text="***"')            # never echo a secret slot's frozen literal
        else:
            shown = s.text if len(s.text) <= 60 else s.text[:57] + "..."
            parts.append(f"  text={shown!r}")
    if s.mutating:
        # Show WHICH signal marked it. Without this the operator sees one bit for four very different
        # claims and cannot tell the 28%-false-positive keyword guess from a POST that was watched
        # leaving the browser — which is exactly the judgement `flow mark` asks them to make.
        src = ",".join(s.mutating_sources) if s.mutating_sources else "source not recorded"
        parts.append(f"  **MUTATING** ({src})")
    elif s.mutating_sources:
        # A step a human demoted keeps its history, so the override stays visible on the recipe they
        # re-approve rather than looking like a step nothing ever marked.
        parts.append(f"  [not writing — was marked by {','.join(s.mutating_sources)}]")
    return "".join(parts)


def _flow_mark(args: argparse.Namespace) -> None:
    """`flow mark` — the human's verdict on one step's write status (D0 lever ii, acting half).

    Exits NONZERO on a refusal, with the reason printed: this is a write-path decision, and a refusal
    that exits 0 is the CLI-truth defect S7a/S7b spent two slices removing.
    """
    from .flows import load_spec, mark_step

    spec = load_spec(args.name)
    try:
        changed = mark_step(spec, args.step, writes=bool(args.write))
    except ValueError as exc:
        print(f"refused: {exc}")
        raise SystemExit(2)
    verdict = "WRITING" if args.write else "not writing"
    if not changed:
        print(f"step {args.step} of {args.name!r} was already marked {verdict} — nothing changed, "
              f"and its approval is untouched.")
        return
    print(f"step {args.step} of {args.name!r} is now marked {verdict} (recorded as a human verdict).")
    print("The recipe changed, so its approval is now STALE. Re-read it and re-approve:")
    print(f"  ultracua flow inspect --name {args.name}")
    print(f"  ultracua flow approve --name {args.name}")


def _flow_approve_all(args: argparse.Namespace) -> None:
    """Bulk re-approval, for the one-time upgrade to steps-bound approvals (see `cache.steps_hash`).

    Deliberately awkward, because bulk-blessing recipes is exactly the thing this release exists to stop
    being easy:
      - it PRINTS every flow's steps first — including each step's typed text and slot binding, i.e. the
        things the digest actually binds, not just an action list;
      - it REFUSES any flow with a mutating step — a write's recipe gets read one at a time, by a human,
        with `flow inspect`. There is no `--include-writes`;
      - it REFUSES a flow with a PENDING slot-schema or value-contract re-approval. `approve()` stamps all
        three bindings at once, so blessing such a flow here would silently clear a *different* trust gate
        (a widened slot domain, a loosened contract) that this printout does not show. Those go one at a
        time too;
      - it requires an interactive confirmation (or an explicit `--yes`, so a documented migration script
        can run non-interactively — the operator typed it, which is the same consent one level up).
    """
    from .cache import FlowCache, flow_key
    from .config import flow_home
    from .flows import (_contracts_hash, _load_meta, _slots_hash, approve, is_write_flow, list_specs,
                        load_spec)

    names = list_specs()
    if not names:
        raise SystemExit(f"no saved flows in {flow_home()} — nothing to approve")
    cache = FlowCache()
    pending: list[tuple[str, object]] = []   # (name, spec) — eligible reads
    skipped: list[tuple[str, str]] = []      # (name, why)
    for name in names:
        try:
            spec = load_spec(name)
        except Exception as exc:  # noqa: BLE001 — one unreadable spec must not block the migration
            skipped.append((name, f"unreadable spec: {exc}"))
            continue
        key = spec.key
        try:
            cached = cache.get(key)
        except CacheUnreadableError as exc:
            # Same rule as the unreadable-spec branch above, applied to the sibling read: one bad file
            # must not abort a bulk approve. It is SKIPPED, never approved — `is_write_flow` below decides
            # whether this flow needs individual review, and an unreadable recipe cannot answer that.
            skipped.append((name, f"cached recipe unreadable — refusing to bulk-approve it blind: {exc}"))
            continue
        if cached is None:
            skipped.append((name, "not learned yet"))
            continue
        if is_write_flow(spec, cached):
            skipped.append((name, "WRITE flow — review it individually (`flow inspect --name "
                                  f"{name}`) and approve by name"))
            continue
        meta = _load_meta(cache, key)
        if meta.quarantine is not None:
            skipped.append((name, f"quarantined — `flow release --name {name}` it first"))
            continue
        if meta.approved and _slots_hash(spec) != meta.slots_hash:
            skipped.append((name, "its SLOT SCHEMA also changed since approval — that is a separate "
                                  "re-approval this printout does not show. Review `flow inspect --name "
                                  f"{name}` and approve by name"))
            continue
        if meta.approved and _contracts_hash(spec) != meta.contracts_hash:
            skipped.append((name, "its VALUE CONTRACTS also changed since approval — that is a separate "
                                  f"re-approval this printout does not show. Review `flow contracts --name "
                                  f"{name}` and approve by name"))
            continue
        pending.append((name, spec))
        first = "" if meta.approved else "   [FIRST approval — this flow was never approved before]"
        print(f"\n== {name}: {len(cached.steps)} step(s)  [{spec.start_url}]{first}")
        for i, s in enumerate(cached.steps):
            print(_step_line(i, s, spec))
    for name, why in skipped:
        print(f"\n-- SKIPPING {name}: {why}")
    if not pending:
        raise SystemExit("\nnothing eligible for bulk approval (see the skip reasons above)")
    print(f"\nabout to approve {len(pending)} read flow(s): {', '.join(n for n, _ in pending)}")
    if not getattr(args, "yes", False):
        if not sys.stdin.isatty():
            raise SystemExit("refusing to bulk-approve without a human: re-run attached to a terminal, or "
                             "pass --yes if you have read the recipes above.")
        if input("approve these? [y/N] ").strip().lower() not in ("y", "yes"):
            raise SystemExit("aborted — nothing approved")
    for name, spec in pending:
        approve(spec)
    print(f"approved {len(pending)} flow(s). Writes were skipped on purpose — approve each by name after "
          f"`flow inspect --name <name>`.")


def _flow_unapprove(args: argparse.Namespace) -> None:
    """Withdraw approval. Needed as a real verb (not just a Python API) because an APPROVED flow deliberately
    KEEPS its learned data shape + value contracts across a re-learn — so when a page legitimately restructures,
    `unapprove -> learn -> approve` is the only way to re-baseline them under a human's eye."""
    from .flows import load_spec, unapprove

    spec = load_spec(args.name)
    unapprove(spec)
    print(f"unapproved {spec.name!r} — its learned shape + value contracts will be RE-SEEDED by the next "
          f"`flow learn --name {spec.name}`. Review the result, then `flow approve --name {spec.name}`.")


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
    cached = FlowCache().get(spec.key)
    if cached:
        # Every `stale_approval` refusal sends the operator here to decide whether to re-approve, so this must
        # show what the approval digest actually BINDS — the target, the typed text, the slot, the mutating
        # flag — not just an action list. (A secret slot's literal is never echoed.)
        print(f"\nlearned {len(cached.steps)} step(s):")
        for i, s in enumerate(cached.steps):
            print(_step_line(i, s, spec))
    else:
        print("\n(no learned flow cached yet — run: ultracua flow learn ...)")


def _flow_list() -> None:
    from .config import flow_home
    from .flows import list_specs

    names = list_specs()
    # Name the resolved home when there is nothing to list — that is usually the entire diagnosis for
    # "why does it say no flows?" (the store is cwd-relative unless $ULTRACUA_HOME is set).
    print("\n".join(names) if names else f"(no saved flows in {flow_home()})")


def _flow_serve_mcp(args: argparse.Namespace) -> None:
    from .config import flow_home
    from .flows import list_specs
    from .mcpserver import list_flow_tools, serve

    expose_writes = getattr(args, "expose_writes", False)
    # An MCP client launches its servers with an ARBITRARY cwd, and the flow home is cwd-relative unless
    # $ULTRACUA_HOME is set — so this surface was the most likely of all to come up "healthy" exposing ZERO
    # tools, with the failure visible nowhere. Refuse to start on a genuinely EMPTY store. (A store that has
    # specs but none approved yet still starts: that's a legitimate mid-setup state.)
    if not list_specs() and not getattr(args, "allow_empty", False):
        print(f"error: no flows found in {flow_home()} — refusing to serve an empty tool list. Set "
              f"$ULTRACUA_HOME (in your MCP client's `env` block) or launch from the project that owns "
              f".ultracua/. Pass --allow-empty to serve anyway.", file=sys.stderr)
        raise SystemExit(2)
    tools = list_flow_tools(expose_writes=expose_writes)  # preview to stderr; stdout is the MCP protocol
    reads = [t.name for t in tools if not t.is_write]
    writes = [t.name for t in tools if t.is_write]
    print(f"ultracua MCP server: exposing {len(reads)} approved read-flow tool(s) over stdio: "
          f"{', '.join(reads) or '(none)'}", file=sys.stderr)
    if expose_writes:
        print(f"  + {len(writes)} WRITE tool(s): {', '.join(writes) or '(none)'}", file=sys.stderr)
        print("  CAVEAT: write tools perform real, irreversible actions, require an interactive confirm per "
              "call, and run under YOUR (the operator's) identity — every caller rides your identity until "
              "per-caller auth (Phase I) lands. Only expose writes to a client you trust.", file=sys.stderr)
    else:
        print("  (writes not exposed — pass --expose-writes to also serve approved write flows)", file=sys.stderr)
    try:
        asyncio.run(serve(expose_writes=expose_writes))
    except RuntimeError as exc:  # the mcp SDK isn't installed -> a clear, actionable message
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        pass


def _ago(ts: float) -> str:
    if not ts:
        return "never"
    import time as _t

    d = max(0.0, _t.time() - ts)
    for unit, sec in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= sec:
            return f"{int(d / sec)}{unit} ago"
    return f"{int(d)}s ago"


def _audit_advisories(name: str) -> Optional[int]:
    """Unreviewed H9 audit advisories for a flow, or None when the meta could not be READ (CLI-only;
    never part of FlowHealth's status vocabulary)."""
    try:
        from .cache import FlowCache, flow_key
        from .flows import _load_meta, load_spec

        spec = load_spec(name)
        meta = _load_meta(FlowCache(), spec.key)
        return int(getattr(meta, "audit_advisories", 0) or 0)
    except Exception:  # noqa: BLE001 - a status line must never break `flow status`
        # CLI-5: None, not 0. The habituation counter this exists to surface would otherwise read CLEAN
        # exactly when the trust state is unreadable — "we could not tell" reported as "there are none",
        # which is the distinction S4 drew for the sidecar loader, one surface over.
        return None


def _flow_status(args: argparse.Namespace) -> None:
    from .flows import health, list_specs, load_spec

    from .config import flow_home

    names = [args.name] if args.name else list_specs()
    if not names:
        print(f"(no saved flows in {flow_home()})")
        return
    stale_after = args.stale_after * 3600 if args.stale_after else None  # hours -> seconds
    for name in names:
        h = health(load_spec(name), stale_after=stale_after)
        print(f"{h.name}: {h.status}  approved={h.approved}  "
              f"runs={h.runs} ok={h.successes} fails={h.consecutive_failures}  "
              f"last_ok={_ago(h.last_ok_ts)}")
        if h.last_error and h.status not in ("healthy", "never-run", "not-learned"):
            print(f"    last error: {h.last_error}")
        # The approval bit is set but no longer binds the steps on disk -> every run REFUSES at pre-flight.
        # Surfaced here (not folded into `status`) because it is orthogonal to run health: a flow can read
        # `healthy` on its history and still be unrunnable until a human re-approves.
        if h.approval_stale:
            print(f"    APPROVAL STALE: the cached steps are not the ones that were approved — replay will "
                  f"refuse. Review with `flow inspect --name {name}`, then `flow approve --name {name}`.")
        # H9 judge: surface unreviewed advisories so habituation is MEASURED, not a silent pile-up. This
        # ESCALATES without quarantining — FlowHealth's status vocabulary is deliberately unchanged.
        adv = _audit_advisories(name)
        if adv is None:
            # CLI-5. `if adv:` treated "cannot tell" and "none" identically, so the one state that most
            # warrants a look printed nothing at all.
            print(f"    audit: UNKNOWN — the trust sidecar could not be read, so the unreviewed-advisory "
                  f"count is not available for {name!r}")
        elif adv:
            print(f"    audit: {adv} unreviewed advisor{'y' if adv == 1 else 'ies'} — "
                  f"`flow audit --list --name {name}`")


def _flow_release(args: argparse.Namespace) -> None:
    from .flows import load_spec, release

    spec = load_spec(args.name)
    rebaseline = getattr(args, "rebaseline", False)
    # NO PRE-CHECK (R4.42). This used to ask `health()` and return early unless the status was exactly
    # "quarantined", which killed the remedy `flow.py:206` names — and WIDENING it to accept "refused"
    # reaches only one of the three states that reads wrong, because `health()`'s ladder tests `not
    # cached` before both the refusal and the quarantine. `release()` is already idempotent and already
    # knows what it cleared, so ask IT, and report that instead of a status that stood in for it.
    res = release(spec, rebaseline=rebaseline)
    if not res.cleared:
        print(f"{spec.name!r}: nothing to release — no quarantine and no learn-time refusal on record")
        return
    print(f"released {spec.name!r}: cleared {', '.join(res.cleared)}")
    if rebaseline:
        print(f"  the magnitude baseline is gone — the field RE-WARMS at the new normal (advisory until it "
              f"re-accrues). Use this only for a genuine, permanent level shift.")
    else:
        print(f"  the next run RE-ARMS the same contracts (it re-quarantines if the value is still wrong). "
              f"Fix the upstream value, relax via `flow contracts --set`, or, for a real permanent level "
              f"shift, `flow release --rebaseline`.")


def _coerce_contract_value(v: str):
    """Coerce a CLI string to the JSON scalar a contract attr expects (bool / int / float / str)."""
    low = v.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def _flow_contracts(args: argparse.Namespace) -> None:
    from .contracts import CONTRACT_ATTRS
    from .flows import contracts_for, load_spec, save_spec

    spec = load_spec(args.name)
    if args.set or args.disable or args.enable:
        overlay = {k: dict(v) for k, v in (spec.contracts or {}).items()}
        for item in args.set or []:
            path_attr, sep, value = item.partition("=")
            if not sep:
                raise SystemExit(f"--set expects PATH:ATTR=VALUE, got {item!r}")
            path, _, attr = path_attr.rpartition(":")
            if attr not in CONTRACT_ATTRS:
                raise SystemExit(f"unknown contract attr {attr!r}; allowed: {sorted(CONTRACT_ATTRS)}")
            overlay.setdefault(path, {})[attr] = _coerce_contract_value(value)
        for path in args.disable or []:
            overlay.setdefault(path, {})["enabled"] = False
        for path in args.enable or []:
            overlay.setdefault(path, {})["enabled"] = True
        spec.contracts = overlay
        save_spec(spec)
        print(f"updated the value-contract overlay on {spec.name!r} — RE-APPROVE before replay "
              f"(`flow approve --name {spec.name}`): a changed contract must be re-blessed.")
    eff, quar = contracts_for(spec)
    if quar:
        print(f"QUARANTINED: {quar.get('reason')}   (`flow release --name {spec.name}` to clear)")
    if not eff:
        print("(no value contracts yet — re-learn to auto-seed them, or add one with --set)")
        return
    print(f"value contracts for {spec.name!r} (field: predicates):")
    for path in sorted(eff):
        print(f"  {path or '<root>'}: {eff[path]}")


def _flow_audit(args: argparse.Namespace) -> None:
    from .audit import AUDIT_MODES, audit_dir, load_artifacts
    from .cache import FlowCache, flow_key
    from .flows import _resolve_fleet, audit_flows, list_specs, load_spec, save_spec
    from .audit import purge as audit_purge

    names = [args.name] if args.name else None
    # --set-mode is the ONLY way to arm enforcement: a human editing the spec. There is deliberately no
    # runtime --enforce flag, so a cron line can never arm what the operator didn't bless.
    if args.set_mode:
        if not args.name:
            raise SystemExit("--set-mode needs --name")
        if args.set_mode not in AUDIT_MODES + ("off",):
            raise SystemExit(f"--set-mode must be one of {AUDIT_MODES + ('off',)}")
        spec = load_spec(args.name)
        # Refuse to ARM the judge on a flow it must never touch. Not sufficient on its own — an
        # undeclared write has no declaration to test here, and a read can BECOME one on a re-learn, which
        # is why the load-bearing gate is at the capture — but it is the first thing an operator hits,
        # and the message that used to print here was "a corroborated finding can now QUARANTINE this
        # flow" about a flow whose whole guarantee is that nothing here can reach it. Turning it OFF is
        # always allowed: that direction only ever removes capability.
        if args.set_mode != "off" and spec.write.declares_write:
            raise SystemExit(
                f"{spec.name!r} declares a write — the H9 judge never captures or judges a write flow, so "
                f"arming it here would promise something that cannot happen. Its value gates are the "
                f"confirm check and `spec.contracts`; `flow audit --set-mode off` to be explicit.")
        spec.audit = None if args.set_mode == "off" else args.set_mode
        save_spec(spec)
        print(f"audit mode for {spec.name!r} is now {spec.audit or 'off'}"
              + (" — a corroborated finding can now QUARANTINE this flow (a human must `flow release` it)"
                 if spec.audit == "enforce" else
                 " — findings are reported only; nothing will be quarantined" if spec.audit else
                 " — nothing is captured and no artifact is written"))
        return

    cache = FlowCache()
    if args.purge:
        purged = 0
        for name in _resolve_fleet(names, allow_empty=getattr(args, "allow_empty", False),
                                   verb="flow audit --purge"):
            purged += 1
            spec = load_spec(name)
            audit_purge(cache, spec.key)
        print(f"purged the audit artifact store for {purged} flow(s)")
        return

    if args.list or args.show:
        for name in _resolve_fleet(names, allow_empty=getattr(args, "allow_empty", False),
                                   verb="flow audit --list"):
            spec = load_spec(name)
            key = spec.key
            arts = load_artifacts(cache, key)
            if not arts:
                continue
            print(f"{name} ({spec.audit or 'off'}): {len(arts)} artifact(s) in {audit_dir(cache, key)}")
            for a in arts if args.show else []:
                print(f"  - ts={a.get('ts'):.0f} mode={a.get('mode')} signals={a.get('signals')} "
                      f"markers={a.get('markers')}\n      data: {str(a.get('data'))[:160]}"
                      f"\n      page: {str(a.get('text'))[:200]}")
        return

    run = asyncio.run(audit_flows(names=names, cache=cache, max_calls=args.max_calls,
                                  dry_run=args.dry_run, keep=args.keep, provider_name=args.provider,
                                  allow_empty=getattr(args, "allow_empty", False)))
    for f in run.findings:
        mark = "QUARANTINED" if f.enforced else "advisory"
        print(f"  [{mark:<11}] {f.name:<24} {f.code}" + (f"\n      quote: {f.quote[:120]}" if f.quote else ""))
    if args.verbose:
        for name, why in run.skipped:
            print(f"  [skipped    ] {name:<24} {why}")
    for name, why in run.errors:
        # UNCONDITIONAL. `skipped` holds the by-design cases (a write flow is never judged), which is
        # noise at INFO; `errors` holds flows we were asked to audit and could not look at. Hiding those
        # behind --verbose would leave `flow audit` printing a clean-looking summary for a fleet it never
        # examined — the exact shape this slice removes from `run-all`.
        print(f"  [NOT AUDITED] {name:<24} {why}")
    print(f"\n== judged {run.judged}, {run.quarantined} quarantined, {run.advisories} advisory, "
          f"{run.unjudged} unjudged ({run.calls} LLM call(s)) ==")
    if run.no_llm:
        print("WARNING: no LLM configured — nothing was judged. UNJUDGED IS NOT CLEAN.")
    elif run.budget_exhausted:
        print(f"WARNING: budget exhausted after {run.calls} call(s) — {run.unjudged} artifact(s) left "
              f"UNJUDGED. Unjudged is NOT clean; raise --max-calls or audit more often.")
    if run.quarantined:
        print("A flow was quarantined by the audit. Investigate the value, then `flow release --name <n>`.")
    raise SystemExit(run.exit_code)


def _post_alert(url: str, alerts: list) -> None:
    """Post whatever `fleet_verdict` judged loud. NOT "the failures" — a fleet where nothing ran alerts
    with its SKIPPED flows, and calling those failures in the payload would be the same lie one layer
    down. The `failed` key keeps its name for webhooks already parsing it; `status` says which it is."""
    import urllib.request

    lines = "\n".join(f"- {r.name} [{getattr(r, 'status', '?')}]: {r.error}" for r in alerts)
    payload = {"text": f"ultracua: {len(alerts)} flow(s) need attention\n{lines}",
               "failed": [{"name": r.name, "status": getattr(r, "status", None), "error": r.error}
                          for r in alerts]}
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

    from .flows import fleet_verdict, run_all

    results = asyncio.run(run_all(
        approved_only=not args.include_unapproved, include_writes=args.include_writes,
        concurrency=args.concurrency, on_drift=args.on_drift, provider_name=args.provider,
        allow_empty=getattr(args, "allow_empty", False),
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
    # ONE verdict, both channels. They used to carry a copy of the condition each (`if failed and
    # args.alert_webhook`, `SystemExit(1 if failed else 0)`), which is why a third bucket satisfying
    # neither was invisible in both — cron reported green over a fleet that had run nothing for weeks.
    verdict = fleet_verdict(results, allow_empty=getattr(args, "allow_empty", False))
    if args.json:
        record = {"ok": ok, "failed": len(failed), "skipped": skipped, "total": len(results),
                  "exit_code": verdict.exit_code, "verdict": verdict.summary,
                  "flows": [{"name": r.name, "status": r.status, "ms": round(r.ms), "error": r.error}
                            for r in results]}
        Path(args.json).write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    if verdict.exit_code:
        print(verdict.summary)
    if verdict.alerts and args.alert_webhook:
        _post_alert(args.alert_webhook, verdict.alerts)
    raise SystemExit(verdict.exit_code)  # cron alerts on a non-zero exit


def _coerce_cell(value, slot):
    """Coerce a CSV string cell to its slot's type (a CSV is all-strings; `validate_params` is strict). An
    un-coercible value is left as-is so `validate_params` raises a typed, per-slot error rather than a
    silent wrong coercion."""
    if value is None or slot is None or not isinstance(value, str):
        return value
    if slot.type == "integer":
        try:
            return int(value)
        except ValueError:
            return value
    if slot.type == "number":
        try:
            return float(value)
        except ValueError:
            return value
    if slot.type == "boolean":
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        return value
    return value  # string


def _load_batch_rows(path: str, spec) -> list:
    """Load `run-batch` rows from a JSON array of `{slot: value}` objects (typed, preferred) or a CSV whose
    header row names the slots (cells coerced per `spec.slots[name].type`). A secret-slot column is refused
    up front — secrets come from `$secret_env`, never a row file."""
    from pathlib import Path

    p = Path(path)
    text = p.read_text(encoding="utf-8")
    slots = spec.slots or {}
    if p.suffix.lower() == ".json":
        rows = json.loads(text)
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise SystemExit("run-batch: --rows JSON must be a list of {slot: value} objects")
    else:  # CSV: header = slot names, cells coerced per slot type
        import csv
        import io

        rows = [{k: _coerce_cell(v, slots.get(k)) for k, v in raw.items() if k is not None}
                for raw in csv.DictReader(io.StringIO(text))]
    secret_names = {n for n, s in slots.items() if s.secret}
    for i, row in enumerate(rows):
        leaked = secret_names & set(row)
        if leaked:
            raise SystemExit(
                f"run-batch: row {i} carries secret slot(s) {sorted(leaked)} — a secret comes from its "
                f"$secret_env, never a row file. Remove those columns.")
    return rows


def _flow_run_batch(args: argparse.Namespace) -> None:
    import dataclasses as _dc
    from pathlib import Path

    from .flows import FlowReplayError, load_spec, run_batch
    from .ledger import RunLedger

    spec = load_spec(args.name)
    rows = _load_batch_rows(args.rows, spec)
    dry_run = not args.commit  # DRY-RUN by default — the CLI analog of the approval bound
    # Resume: reuse the operator's --resume id; else, on a COMMITTED WRITE batch, auto-mint one so even the
    # FIRST run is resumable (the unplanned-crash case) and print it as the resume contract.
    resume = args.resume
    if resume is None and args.commit and spec.write.declares_write:
        resume = RunLedger.mint_job_id()
        print(f"batch job {resume} — re-run with `--resume {resume}` to resume (skips committed rows)\n")
    try:
        report = asyncio.run(run_batch(
            spec, rows, max_rows=args.max_rows, on_row_error=args.on_row_error, dry_run=dry_run,
            provider_name=args.provider, resume=resume))
    except FlowReplayError as exc:  # a pre-flight refusal (unapproved, no max_rows, undeclared write, …)
        raise SystemExit(f"BATCH REFUSED: {exc}")
    mark = {"ok": "OK", "failed": "FAIL", "skipped": "SKIP", "invalid": "BAD", "planned": "PLAN",
            "resumed": "RSMD"}
    for r in report.rows:
        keys = (" " + ",".join(r.idempotency_keys)) if r.idempotency_keys else ""
        detail = r.error or (json.dumps(r.data, ensure_ascii=False)
                             if r.status == "ok" and r.data is not None else "")
        print(f"  [{mark.get(r.status, r.status.upper()):<4}] row {r.index:<4}{keys} {detail}")
    head = "PLANNED (dry-run — pass --commit to actuate)" if dry_run else report.status.upper()
    print(f"\n== {head}: {report.ok_count} ok, {report.resumed} resumed, {report.failed} failed, "
          f"{report.skipped} skipped, {report.invalid} invalid (of {report.total}) ==")
    if args.json:
        Path(args.json).write_text(json.dumps(_dc.asdict(report), indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    bad = report.status in ("failed", "invalid")
    if bad and report.job_id:  # tell the operator how to finish a partial batch
        print(f"\nto resume the rows that DIDN'T commit: "
              f"`ultracua flow run-batch --name {args.name} --rows {args.rows} --commit "
              f"--max-rows {args.max_rows} --resume {report.job_id}`")
    # non-zero exit on any invalid/failed batch (so cron / CI alerts); a clean dry-run plan exits 0.
    raise SystemExit(1 if bad else 0)


def _flow_record(args: argparse.Namespace) -> None:
    from .flows import FlowSpec, caption_for, record

    # A confirm check (--confirm-*) DECLARES this a WRITE recording — the recorder can't infer the
    # action-completion signal, so a demonstrated write must declare it (just like `flow learn`).
    mutate = _mutate_from_args(args) if _has_confirm_args(args) else None
    spec = FlowSpec(name=args.name, start_url=args.url, goal=args.goal,
                    storage_state=args.storage_state, mutate=mutate)
    # H3: comma-separated WRITE field names to bind as parameters (explicit sign-off). CLI pre-checks
    # mirror the library guards for a friendly message before a browser opens.
    ws = set(filter(None, (getattr(args, "writable_slots", None) or "").split(","))) or None
    if ws is not None:
        if not _has_confirm_args(args):
            raise SystemExit("--writable-slots binds WRITE fields and needs a --confirm-* (it declares the "
                             "write); a read flow uses --mine-slots.")
        if getattr(args, "mine_slots", False):
            raise SystemExit("pass --mine-slots (read auto-lift) OR --writable-slots (write sign-off), not both.")

    async def _demo(page) -> None:  # the "stop signal": the human demos in the browser, then presses Enter
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, input, "\n>>> Demonstrate the flow in the browser window, then press Enter here to finish… ")

    print(f"opening {args.url} — a browser window will appear; perform the flow, then return here.")
    # Opt-in intent caption: one best-effort post-hoc LLM call to label the steps (off the replay path).
    # None when no LLM is configured -> placeholder intents, recording stays key-less.
    res = asyncio.run(record(spec, demo=_demo, headless=False,
                             caption=caption_for(getattr(args, "provider", None)),
                             mine_slots=getattr(args, "mine_slots", False), writable_slots=ws))
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
            if spec.slots:
                print(f"\nmined {len(spec.slots)} typed slot(s): {', '.join(sorted(spec.slots))} — replay with "
                      f"`params={{...}}` (e.g. `flows.replay(spec, params={{'{sorted(spec.slots)[0]}': '…'}})`).")
            print(f"\nrecorded + verified {spec.name!r} (replays 0-LLM). Approve it to run unattended:\n"
                  f"    ultracua flow approve --name {spec.name}")
        # Same honesty as `flow learn`: an APPROVED flow keeps the shape a human blessed, so re-recording it
        # does NOT re-baseline — say so rather than let "recorded + verified" imply the repair landed. And a
        # re-record almost always moves the recipe, which un-binds the approval outright.
        _warn_if_approval_stale(spec)
        _warn_if_shape_baseline_kept(spec, None, had_data=False)
    else:
        raise SystemExit(f"\nNOT recorded: {res.note}")


def _flow_canary(args: argparse.Namespace) -> None:
    from .flows import canary, canary_all, load_spec, sweep_verdict

    if args.name:
        results = [asyncio.run(canary(load_spec(args.name)))]
    else:
        results = asyncio.run(canary_all(concurrency=args.concurrency,
                                         allow_empty=getattr(args, "allow_empty", False)))
    rank = {"stale": 0, "error": 1, "not-learned": 2, "fresh": 3}
    for r in sorted(results, key=lambda r: (rank.get(r.status, 9), r.name)):
        mark = {"fresh": "FRESH", "stale": "STALE", "error": "ERROR",
                "not-learned": "NEW"}.get(r.status, r.status.upper())
        print(f"  [{mark:<5}] {r.name:<24} {r.detail}")
    stale = [r for r in results if r.status in ("stale", "error")]
    fresh = sum(1 for r in results if r.status == "fresh")
    print(f"\n== {fresh} fresh, {len(stale)} stale/error (of {len(results)}) ==")
    # CLI-4 IS CLI-1 ON ANOTHER VERB, which S7a recorded as a known-identical shape rather than leaving
    # it to be rediscovered: the old test was `status in ("stale","error")`, so `not-learned` counted
    # toward neither bucket and a wiped cache or wrong-cwd job printed "0 fresh, 0 stale", exited 0, and
    # reported healthy while checking nothing — the exact inversion of what a canary is for.
    #
    # QUIET is the allowlist. `not-learned` stays quiet on its own (a saved-but-not-yet-learned flow is
    # an ordinary intermediate state, and going red for it nightly is how an alert earns its `|| true`),
    # but a sweep where NOTHING was actually checked is loud by the shared rule's second clause. One
    # definition with `run-all`, so a status added tomorrow is loud on both.
    verdict = sweep_verdict(results, quiet=frozenset({"fresh", "not-learned"}),
                            worked=frozenset({"fresh", "stale", "error"}),
                            allow_empty=getattr(args, "allow_empty", False), noun="flow")
    if verdict.exit_code:
        print(verdict.summary)
    raise SystemExit(verdict.exit_code)  # cron alerts on a non-zero exit


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
                    help="raise = fail loud on drift (default); relearn = re-author the flow instead. NOTE: relearn is REFUSED on an APPROVED flow (it would run steps no human reviewed), and on ANY flow with a step marked as writing, approved or not (re-authoring re-runs the flow, which re-performs the write). Use `flow inspect --name X` to see which steps are marked; recover with `flow learn`/`flow record`, then re-approve.")
    pr.add_argument("--no-auth-refresh", dest="auth_refresh", action="store_false",
                    help="don't re-login on drift (default: refresh an expired session and retry).")
    pr.add_argument("--verbose", "-v", action="store_true", help="log replay/heal/drift events (INFO).")

    pa = sub.add_parser("approve", help="Mark a learned flow trusted (for --require-approved replays). "
                                        "Approval BINDS to the flow's steps: if they are re-authored, replay "
                                        "refuses until a human re-approves.")
    pa.add_argument("--name", help="the flow to approve (required unless --all).")
    pa.add_argument("--all", action="store_true",
                    help="print every learned READ flow's steps and approve them in bulk — for the one-time "
                         "upgrade to steps-bound approvals. WRITE flows are always skipped: review each with "
                         "`flow inspect` and approve it by name.")
    pa.add_argument("--yes", action="store_true",
                    help="skip the interactive confirmation for --all (for a documented migration script).")

    pdr = sub.add_parser("dry-run", help="Replay a WRITE flow with every write HELD — see the exact "
                                         "request bodies it WOULD send, before approving it.")
    pdr.add_argument("--name", required=True)
    pdr.add_argument("--param", action="append", metavar="NAME=VALUE",
                     help="a slot value, repeatable (same validation as a real replay).")

    pua = sub.add_parser("unapprove", help="Withdraw approval — also the way to RE-BASELINE an approved "
                                           "flow's learned shape + value contracts "
                                           "(unapprove -> learn -> approve).")
    pua.add_argument("--name", required=True)

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

    prl = sub.add_parser("release", help="Clear an H9 value-contract QUARANTINE after investigating the value.")
    prl.add_argument("--name", required=True)
    prl.add_argument("--rebaseline", action="store_true",
                     help="also clear the magnitude baseline so the field re-warms at the new normal "
                          "(use ONLY for a genuine, permanent level shift).")

    pmk = sub.add_parser("mark", help="Record a HUMAN verdict on whether one step WRITES (see `flow inspect`).")
    pmk.add_argument("--name", required=True)
    pmk.add_argument("--step", required=True, type=int, metavar="N",
                     help="step index, as shown by `flow inspect`.")
    grp = pmk.add_mutually_exclusive_group(required=True)
    grp.add_argument("--read", action="store_true",
                     help="this step does NOT write. Allowed only where the mark was a GUESS "
                          "(keyword/caption); refused where something observed the write.")
    grp.add_argument("--write", action="store_true",
                     help="this step DOES write. Always allowed — it is the conservative direction.")

    pct = sub.add_parser("contracts", help="View / edit a flow's H9 VALUE contracts (fail-loud value guards).")
    pct.add_argument("--name", required=True)
    pct.add_argument("--set", action="append", metavar="PATH:ATTR=VALUE",
                     help="tighten/relax one predicate, e.g. price:positive=false or :min_count=250 (repeatable).")
    pct.add_argument("--disable", action="append", metavar="PATH",
                     help="disable the contract on a field, e.g. price (root = empty PATH) (repeatable).")
    pct.add_argument("--enable", action="append", metavar="PATH", help="re-enable a disabled field (repeatable).")

    pau = sub.add_parser("audit", help="H9 judge: audit captured read artifacts with an LLM (out-of-band). "
                                       "It can only QUARANTINE a flow for a human — never approve or clear one.")
    pau.add_argument("--name", help="a single flow (default: all).")
    pau.add_argument("--set-mode", dest="set_mode", metavar="off|advisory|enforce",
                     help="arm auditing for a flow. 'advisory' reports only; 'enforce' lets a corroborated "
                          "finding quarantine. THE ONLY way to enable enforcement (no runtime flag exists).")
    pau.add_argument("--list", action="store_true", help="list captured artifacts (0 LLM).")
    pau.add_argument("--show", action="store_true", help="print each artifact's evidence (0 LLM).")
    pau.add_argument("--purge", action="store_true", help="delete the captured artifact store now.")
    pau.add_argument("--dry-run", dest="dry_run", action="store_true",
                     help="judge + report but never quarantine (downgrades every flow to advisory).")
    pau.add_argument("--keep", action="store_true", help="keep artifacts after judging (debugging).")
    pau.add_argument("--max-calls", dest="max_calls", type=int, default=0, help="LLM call budget (default 20).")
    pau.add_argument("--provider", **prov)
    pau.add_argument("-v", "--verbose", action="store_true", help="also print skipped flows.")
    pau.add_argument("--allow-empty", dest="allow_empty", action="store_true",
                     help="do not fail when the flow store resolves to zero flows.")

    pra = sub.add_parser("run-all", help="Replay every saved flow (read + approved by default); "
                                         "report + alert; exits non-zero if any fails. Point cron at this.")
    pra.add_argument("--provider", **prov)
    pra.add_argument("--include-unapproved", dest="include_unapproved", action="store_true",
                     help="also run flows that aren't approved.")
    pra.add_argument("--include-writes", dest="include_writes", action="store_true",
                     help="also run write/mutate flows (PERFORMS the writes).")
    pra.add_argument("--concurrency", type=int, default=None,
                     help="max flows run at once (default ULTRACUA_CONCURRENCY).")
    pra.add_argument("--on-drift", dest="on_drift", default="raise", choices=["raise", "relearn"],
                     help="relearn is REFUSED on APPROVED flows (it would run un-reviewed steps), and on any flow with a step marked as writing (re-authoring re-performs the write); with the default approved-only fleet it would fail every flow.")
    pra.add_argument("--allow-empty", dest="allow_empty", action="store_true",
                     help="do not fail when the flow store resolves to zero flows.")
    pra.add_argument("--json", dest="json", help="write a machine-readable run record to this path.")
    pra.add_argument("--alert-webhook", dest="alert_webhook",
                     help="POST a JSON alert here if any flow fails (Slack/Discord/etc. incoming webhook).")
    pra.add_argument("--verbose", "-v", action="store_true", help="log each replay (INFO).")

    prb = sub.add_parser("run-batch", help="H3 slice 2b: drive ONE parameterized flow once per ROW. "
                                           "DRY-RUN by default (plan + preview each row's Idempotency-Key, "
                                           "actuate NOTHING); pass --commit to run. A write batch needs "
                                           "--max-rows. All rows are pre-validated before any actuation.")
    prb.add_argument("--name", required=True, help="the saved flow to batch.")
    prb.add_argument("--rows", required=True,
                     help="rows file: a JSON array of {slot: value} objects (typed), or a CSV whose header "
                          "names the slots (cells coerced per slot type). Secret columns are refused.")
    prb.add_argument("--max-rows", dest="max_rows", type=int, default=None,
                     help="approval bound: refuse if the batch exceeds this. REQUIRED for a write batch.")
    prb.add_argument("--on-row-error", dest="on_row_error", default="stop", choices=["stop", "continue"],
                     help="stop the batch on the first failed row (default), or continue and report each.")
    prb.add_argument("--commit", action="store_true",
                     help="ACTUATE the batch (default is a dry-run: plan + key preview, no side effects).")
    prb.add_argument("--resume", dest="resume", default=None,
                     help="job id: resume a prior run — rows already committed under this id are SKIPPED "
                          "(not re-fired). A fresh/absent id is an independent run. A committed write batch "
                          "auto-mints + prints an id so its first run is resumable.")
    prb.add_argument("--provider", **prov)
    prb.add_argument("--json", dest="json", help="write the machine-readable BatchRun to this path.")
    prb.add_argument("--verbose", "-v", action="store_true", help="log each replay (INFO).")

    prc = sub.add_parser("record", help="RECORD a flow by demonstrating it in a headed browser. Reads are "
                                        "verify-by-replayed; a WRITE needs a --confirm-* check (then it is "
                                        "gated + idempotency-keyed). Approve it to run unattended.")
    prc.add_argument("--name", required=True, help="name to save the flow under.")
    prc.add_argument("--url", required=True, help="start URL to open for the demonstration.")
    prc.add_argument("--goal", required=True, help="a short description of the flow (forms the cache key).")
    prc.add_argument("--storage-state", dest="storage_state",
                     help="a Playwright storage_state JSON (cookies) to start authenticated.")
    prc.add_argument("--mine-slots", dest="mine_slots", action="store_true",
                     help="H3: auto-lift the typed/selected values into typed slots so the flow can be "
                          "replayed with `params={...}` (read flows only). Refuses if a value echoes into a "
                          "later step (a dead template).")
    prc.add_argument("--writable-slots", dest="writable_slots", default=None,
                     help="H3: comma-separated demonstrated WRITE field names to bind as parameters (the "
                          "explicit sign-off; requires --confirm-*). Each names a field by its accessible "
                          "name; a name matching 0 or >1 fields refuses so a money field is never mis-bound.")
    _add_mutate_args(prc)  # set any --confirm-* to DECLARE + safely capture a WRITE flow

    pca = sub.add_parser("canary", help="Cheap freshness probe: does each flow still START (0-LLM, "
                                        "read-only, no health record)? Exits non-zero if any is stale. "
                                        "Point cron at this MORE often than run-all to catch rot early.")
    pca.add_argument("--name", help="a single flow (default: all).")
    pca.add_argument("--concurrency", type=int, default=None,
                     help="max flows probed at once (default ULTRACUA_CONCURRENCY).")
    pca.add_argument("--allow-empty", dest="allow_empty", action="store_true",
                     help="do not fail when the flow store resolves to zero flows.")

    sub.add_parser("list", help="List saved flows.")
    psm = sub.add_parser("serve-mcp", help="Serve APPROVED READ flows as MCP tools over stdio (H2; needs "
                                           "`uv sync --group mcp`). Writes are not exposed by default.")
    psm.add_argument("--allow-empty", dest="allow_empty", action="store_true",
                     help="serve even when the flow store resolves to zero flows.")
    psm.add_argument("--expose-writes", dest="expose_writes", action="store_true",
                     help="Also expose APPROVED, DECLARED write flows as tools. Each call requires an "
                          "interactive elicitation confirm (a client without elicitation is refused); a "
                          "retry of the same args is deduped, and calls run under the OPERATOR's identity.")

    args = p.parse_args(argv)
    from .flows import EmptyFlowStoreError, FlowReplayError
    from .obs import configure_logging
    configure_logging("INFO" if getattr(args, "verbose", False) else settings.log_level)
    try:
        _flow_dispatch(args)
    except EmptyFlowStoreError as exc:
        # A fleet verb resolved ZERO flows. Exit 3 for `audit` (it already uses 3 for "we did not look"),
        # 2 for the rest — never 0, which is what made a wrong-cwd cron job look healthy forever.
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(3 if args.cmd == "audit" else 2)
    except FlowReplayError as exc:
        # R4.15. This used to catch exactly `EmptyFlowStoreError`, so every OTHER typed error reached the
        # user as a Python traceback on six verbs (`approve`, `unapprove`, `release`, `learn`, `record`,
        # `audit`) — and the thing a traceback buries is the remedy, which for `MetaUnreadableError` is
        # the difference between "retry, that was an AV scan" and a user deleting a healthy sidecar.
        #
        # CATCH THE FAMILY, NOT A MEMBER. Naming the reported class would fix the reported verb and leave
        # the next typed error a traceback — the same enumerate-the-loud-outcomes error the fleet verdict
        # was rebuilt to avoid. Every `FlowReplayError` carries a message written FOR an operator, so
        # rendering the base class is not a widening of blast radius, it is the contract those messages
        # were already written under. The verbs that render these themselves (`replay`, `run-batch`,
        # `dry-run`) still do — this handler only ever sees what they did not.
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _flow_dispatch(args: argparse.Namespace) -> None:
    if args.cmd == "learn":
        asyncio.run(_flow_learn(args))
    elif args.cmd == "replay":
        asyncio.run(_flow_replay(args))
    elif args.cmd == "dry-run":
        asyncio.run(_flow_dry_run(args))
    elif args.cmd == "approve":
        _flow_approve(args)
    elif args.cmd == "unapprove":
        _flow_unapprove(args)
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
    elif args.cmd == "release":
        _flow_release(args)
    elif args.cmd == "mark":
        _flow_mark(args)
    elif args.cmd == "contracts":
        _flow_contracts(args)
    elif args.cmd == "audit":
        _flow_audit(args)
    elif args.cmd == "run-all":
        _flow_run_all(args)
    elif args.cmd == "run-batch":
        _flow_run_batch(args)
    elif args.cmd == "record":
        _flow_record(args)
    elif args.cmd == "canary":
        _flow_canary(args)
    elif args.cmd == "list":
        _flow_list()
    elif args.cmd == "serve-mcp":
        _flow_serve_mcp(args)


def main() -> None:
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
