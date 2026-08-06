"""A fleet that did not do the work must say so through the channel cron actually watches (R3.9, CLI-1,
R4.14).

`run_all`'s own docstring points cron at two channels — "alert on a non-zero exit (any flow failed)" and
`--alert-webhook`. Both were fed by ONE bucket, `status == "failed"`. A third bucket, `skipped`, fed
neither, and a flow can enter it with NO human act:

  * the UNDECLARED-write skip — `_author_steps`' wire promotion marks a formless fetch-POST `mutating`, so
    re-learning a dashboard READ flow after a redesign can retire it from the fleet permanently;
  * a transient unreadable trust sidecar reads as `approved=False`, i.e. "not approved" — the S4
    provenance work made that distinction available to the WRITER and never applied it to this READER.

Either way `flow run-all` prints `[SKIP]`, exits 0, posts nothing, and cron reports green over a fleet
that has not read anything since the redesign. In the limit: `0 ok, 0 failed, N skipped`, exit 0.

Three properties, and both directions of each are pinned, because "alert on everything" satisfies the
loud half alone and is the alert-fatigue regression that kills the channel:

  1. a skip NOBODY CHOSE is loud;
  2. a skip the operator DID choose (a declared write without --include-writes) stays quiet BESIDE REAL
     WORK — the must-remain-usable clause;
  3. a fleet where nothing ran at all never exits 0, however chosen each individual skip was — cron's
     premise ("this fleet is being monitored") is false and that is not a pass. This is the
     `EmptyFlowStoreError` precedent, whose own comment covers this case verbatim: "never 0, which is
     what made a wrong-cwd cron job look healthy forever".

The first four tests run the REAL `run_all` into the REAL CLI handler and assert only what cron sees —
the exit code, the webhook, the `--json` record. They are deliberately blind to whether the fix lands in
the classification or in the channels, so they fail against pre-fix source for the defect itself rather
than for a helper that does not exist yet. The two after them pin the SHAPE that keeps it fixed: one
verdict, both channels, and a QUIET ALLOWLIST rather than a loud one — which is what makes a status added
tomorrow loud by default (S3's "an exit added tomorrow", one abstraction over).

R4.14 is the same invariant on the sibling verb: `audit_flows`' candidate loop has no per-flow guard at
all, so one flow's judge call aborting discards every finding already made and the run exits reporting
nothing. `run_all` has exactly this guard.
"""

from __future__ import annotations

import argparse
import json
import types
from pathlib import Path

import pytest

from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key


def _flow(key: str, *, mutating: bool = False) -> CachedFlow:
    return CachedFlow(key=key, goal="g", start_url="http://127.0.0.1:1/", created_ts=1e9, steps=[
        CachedStep(intent="go", action="click", locator=None, mutating=mutating),
    ])


def _spec(flows_mod, name: str, **kw):
    return flows_mod.FlowSpec(name=name, goal=f"g-{name}", start_url="http://127.0.0.1:1/", **kw)


def _approve(flows_mod, cache, specs) -> None:
    """Write a REAL approved sidecar for each spec, rather than stubbing `_load_meta`.

    Stubbing the loader is what these fixtures used to do, and it is now the wrong instrument: `run_all`
    asks the loader WHERE the meta came from, so a stub that answers only what it SAYS makes every flow
    look identically approved whatever is on disk — including in the test whose whole subject is a
    sidecar that could not be read.
    """
    for s in specs.values():
        flows_mod._save_meta(cache, flow_key(s.goal, s.start_url, s.scope),
                             flows_mod.FlowMeta(approved=True))


def _cron_sees(monkeypatch, tmp_path, cache, specs, *, include_writes=False):
    """Drive the REAL `run_all` through the REAL `flow run-all` handler and return exactly the three
    things a cron job can observe: the exit code, what the webhook was handed, and the `--json` record.

    Nothing here asserts HOW the answer is reached, so the same test body holds whether visibility is
    fixed in the classification or in the channels.
    """
    from ultracua import cli
    from ultracua import flows as flows_mod

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(flows_mod, "load_spec", lambda n: specs[n])

    async def _replay(spec, **_kw):
        return {"ok": True}

    monkeypatch.setattr(flows_mod, "replay", _replay, raising=False)

    real_run_all = flows_mod.run_all

    async def _pinned(**kwargs):                       # the CLI passes no cache/names; this fleet needs both
        kwargs.pop("names", None)
        return await real_run_all(names=list(specs), cache=cache, **kwargs)

    monkeypatch.setattr(flows_mod, "run_all", _pinned)
    posted: list = []
    monkeypatch.setattr(cli, "_post_alert",
                        lambda url, runs: posted.append((url, sorted(r.name for r in runs))))

    out = tmp_path / "run.json"
    args = argparse.Namespace(include_unapproved=False, include_writes=include_writes, concurrency=2,
                              on_drift="raise", provider="anthropic", json=str(out),
                              alert_webhook="https://hook.example", allow_empty=False)
    with pytest.raises(SystemExit) as ei:
        cli._flow_run_all(args)
    return ei.value.code, posted, json.loads(out.read_text(encoding="utf-8"))


# ==================== 1. a skip nobody chose is loud ====================


def test_an_undeclared_write_retiring_itself_from_the_fleet_reaches_cron(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE FINDING. A read flow re-learned after a site redesign whose "Load" button now goes through
    `fetch('/graphql',{method:'POST'})`: the wire promotion caches `mutating=True`, `spec.mutate` is still
    None, and from the next tick on the flow is skipped forever. Nothing has been read since the redesign
    and every automated signal says healthy.

    The skip itself is correct and load-bearing — with no confirm barrier replay cannot tell whether the
    write landed, and `--include-writes` deliberately does not cover it. What is wrong is that it is
    SILENT. There is no escape hatch for this population today (declaring `mutate` demands a confirm a
    read cannot satisfy; `flow record` re-derives the same verdict), which makes visibility the whole of
    what we can offer them.
    """
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    specs = {"dash": _spec(flows_mod, "dash")}
    key = flow_key(specs["dash"].goal, specs["dash"].start_url, specs["dash"].scope)
    cache.put(_flow(key, mutating=True))
    _approve(flows_mod, cache, specs)

    # include_writes=True on purpose: consent to run VERIFIABLE writes is not consent to fire this one,
    # so the skip stands — and must still be visible.
    code, posted, record = _cron_sees(monkeypatch, tmp_path, cache, specs, include_writes=True)

    assert "UNDECLARED write" in (record["flows"][0]["error"] or ""), "premise: it hit that branch"
    assert code != 0, (
        "a flow that retired itself from the fleet with no human act exited 0 — cron reports green over a "
        "fleet that ran nothing")
    assert posted == [("https://hook.example", ["dash"])], "and the webhook must carry it"


def test_a_sidecar_that_could_not_be_read_is_not_reported_as_not_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sibling gap. S4 gave the loader provenance so a WRITER cannot mistake "could not read it" for
    "there isn't one" — and this READER, which decides whether to run the flow at all, still asks only
    what the meta SAYS. A synthesised `approved=False` is indistinguishable from a human's `flow
    unapprove`, so one AV sharing violation silently drops the flow from the tick.

    Quiet is the wrong answer in BOTH directions here: we cannot show the flow is approved (so we must not
    run it) and we cannot show it isn't (so we must not call it a chosen skip).
    """
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    specs = {"inv": _spec(flows_mod, "inv")}
    key = flow_key(specs["inv"].goal, specs["inv"].start_url, specs["inv"].scope)
    cache.put(_flow(key))
    flows_mod._save_meta(cache, key, flows_mod.FlowMeta(approved=True))

    real = Path.read_text
    meta_name = f"{key}.meta.json"

    def _boom(self, *a, **kw):
        if self.name == meta_name:
            raise OSError(32, "The process cannot access the file because it is being used by another "
                              "process")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _boom)
    code, posted, record = _cron_sees(monkeypatch, tmp_path, cache, specs)

    assert "not approved" not in (record["flows"][0]["error"] or ""), (
        "an unreadable sidecar was reported as the human act 'not approved' — the two are not the same "
        "fact, and only one of them is a reason to go quiet")
    assert code != 0 and posted, "and it must reach cron"


def test_unapprove_is_the_acknowledgement_for_a_flow_that_cannot_be_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loud channel with no way to acknowledge gets `|| true`d, and then the other 49 flows go dark
    with it. This population is the one that cannot make the alarm stop by fixing anything: a READ whose
    fetch-POST the wire promotion marked `mutating` has no working remedy (see the test above), so
    without an acknowledgement the fix above would cause the very alert fatigue it is pinned against.

    It exists already and needs no new capability — `flow unapprove` is a human act that says "I know,
    leave it out", and a human's decision outranks a guess the classifier made. That is why the trust
    read now runs BEFORE the write gate: with the order the other way round this flow stays loud forever
    however the operator answers. Note the fleet-level guard still holds underneath — acknowledge every
    flow and `run-all` exits 2 for a fleet that runs nothing.
    """
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    specs = {"dash": _spec(flows_mod, "dash"), "other": _spec(flows_mod, "other")}
    for s in specs.values():
        cache.put(_flow(flow_key(s.goal, s.start_url, s.scope), mutating=(s.name == "dash")))
    _approve(flows_mod, cache, specs)
    # the operator acknowledges the one they cannot fix
    flows_mod._save_meta(cache, flow_key(specs["dash"].goal, specs["dash"].start_url, specs["dash"].scope),
                         flows_mod.FlowMeta(approved=False))

    code, posted, record = _cron_sees(monkeypatch, tmp_path, cache, specs, include_writes=True)

    by_name = {f["name"]: f for f in record["flows"]}
    assert by_name["dash"]["status"] == "skipped" and by_name["dash"]["error"] == "not approved"
    assert by_name["other"]["status"] == "ok", "the rest of the fleet keeps running"
    assert code == 0 and posted == [], "an acknowledged flow must stop waking people"


# ==================== 2. the must-remain-usable clause ====================


def test_a_chosen_skip_beside_real_work_stays_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half that a fix satisfying only the tests above would break, and the reason to pin it: an
    operator whose store holds one declared write flow and runs the read fleet nightly must NOT get a red
    cron every night. That alert goes to `|| true` within a week and the channel is dead for everyone.

    `[SKIP] write flow (use --include-writes)` is a standing configuration choice with nothing to fix.
    """
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    specs = {"read": _spec(flows_mod, "read"),
             "pay": _spec(flows_mod, "pay", mutate=flows_mod.MutateSpec(confirm_text_contains="Paid"))}
    for s in specs.values():
        cache.put(_flow(flow_key(s.goal, s.start_url, s.scope)))
    _approve(flows_mod, cache, specs)

    code, posted, record = _cron_sees(monkeypatch, tmp_path, cache, specs)   # include_writes=False

    by_name = {f["name"]: f for f in record["flows"]}
    assert by_name["pay"]["status"] == "skipped" and by_name["read"]["status"] == "ok", "premise"
    assert code == 0, f"a chosen skip beside real work must stay quiet, got exit {code}"
    assert posted == [], "and must not wake anyone"


# ==================== 3. a fleet that ran nothing ====================


def test_a_fleet_where_nothing_ran_never_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every skip here IS the operator's own choice, so property 1 says nothing and property 2 says stay
    quiet — and the run is still a lie. Cron is monitoring an empty set: `0 ok, 0 failed, 2 skipped`,
    exit 0, forever. `EmptyFlowStoreError` already made this call for a fleet that resolved zero flows;
    resolving two and running neither is the same fact one step later.
    """
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    specs = {n: _spec(flows_mod, n, mutate=flows_mod.MutateSpec(confirm_text_contains="Paid"))
             for n in ("pay", "ship")}
    for s in specs.values():
        cache.put(_flow(flow_key(s.goal, s.start_url, s.scope)))
    _approve(flows_mod, cache, specs)

    code, posted, record = _cron_sees(monkeypatch, tmp_path, cache, specs)

    assert record["ok"] == 0 and record["failed"] == 0 and record["skipped"] == 2, "premise"
    assert code != 0, "a fleet that ran nothing reported healthy"
    assert posted == [("https://hook.example", ["pay", "ship"])], (
        "the webhook must name the flows that did not run — that is the whole content of the alert")


# ==================== the shape that keeps it fixed ====================


def test_the_exit_code_and_the_webhook_are_computed_once(tmp_path: Path) -> None:
    """The shape, not the cases. The defect was two channels each carrying their own copy of the
    condition (`if failed and args.alert_webhook` / `SystemExit(1 if failed else 0)`), so a bucket
    matching neither was invisible in both. One verdict, both channels — over every shape a fleet can
    take, a nonzero exit and a non-empty alert list agree.

    The empty fleet is the one exemption, and it is not a loophole: `run_all` raises
    `EmptyFlowStoreError` before returning `[]` unless the caller passed `allow_empty`, and there is no
    flow to name in the payload.
    """
    from ultracua import flows as flows_mod

    def _run(status, error=None):
        return flows_mod.FleetRun(name=status, ok=(status == "ok"), status=status, error=error)

    shapes = [
        [_run("ok")],
        [_run("ok"), _run("skipped")],
        [_run("failed", "drift")],
        [_run("ok"), _run("failed", "drift")],
        [_run("skipped")],
        [_run("skipped"), _run("skipped")],
        [_run("failed", "drift"), _run("skipped")],
    ]
    for runs in shapes:
        v = flows_mod.fleet_verdict(runs)
        assert bool(v.alerts) == bool(v.exit_code), (
            f"channels disagree for {[r.status for r in runs]}: exit={v.exit_code} alerts={v.alerts}")
    assert flows_mod.fleet_verdict([]).exit_code != 0, "an empty fleet ran nothing either"


def test_allow_empty_is_consent_to_an_empty_STORE_not_to_a_fleet_that_ran_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caught by an existing test the moment "nothing ran" was added, and worth its own pin because the
    two facts are one word apart. `--allow-empty` is a documented escape hatch — a fleet home that
    legitimately holds no flows — and overriding it would break a working configuration to fix a
    different one. It buys exactly ZERO FLOWS RESOLVED; a store that resolved flows and ran none of them
    was never covered by any flag on this command.
    """
    from ultracua import flows as flows_mod

    assert flows_mod.fleet_verdict([], allow_empty=True).exit_code == 0
    skipped = [flows_mod.FleetRun(name="pay", ok=False, status="skipped", error="write flow")]
    assert flows_mod.fleet_verdict(skipped, allow_empty=True).exit_code == 2, (
        "consent to an empty store is not consent to a fleet that resolved flows and ran none")


def test_a_status_the_verdict_has_never_seen_is_loud(tmp_path: Path) -> None:
    """Quiet is an ALLOWLIST, so the next status added to `FleetRun` is loud until someone argues it into
    the quiet set. The inverse — enumerating the loud statuses — is how `skipped` came to feed neither
    channel.
    """
    from ultracua import flows as flows_mod

    v = flows_mod.fleet_verdict([flows_mod.FleetRun(name="x", ok=False, status="deferred", error="?")])
    assert v.exit_code != 0 and [r.name for r in v.alerts] == ["x"]


# ==================== R4.14: the same invariant on the audit verb ====================


def _router():
    """Enough of a router for `audit_flows`: it only ever touches `.totals`, and only to snapshot."""
    return types.SimpleNamespace(totals=None)


async def test_one_flow_s_judge_failure_does_not_discard_the_whole_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`audit_flows`' candidate loop has no per-flow guard: `audit.judge` (an LLM call), `QuarantineSink`
    and `audit.drop` can each abort the run, discarding the findings of every flow already judged —
    including quarantines, whose whole purpose is to be acted on. `run_all` has exactly this guard;
    this loop never got it.

    The evidence is not re-derivable, either: the artifact each finding was judged from is dropped
    immediately after the call.
    """
    from ultracua import audit as audit_mod
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    specs = {n: _spec(flows_mod, n, audit="advisory") for n in ("a", "b")}
    monkeypatch.setattr(flows_mod, "load_spec", lambda n: specs[n])
    keys = {n: flow_key(s.goal, s.start_url, s.scope) for n, s in specs.items()}
    for s in specs.values():
        cache.put(_flow(flow_key(s.goal, s.start_url, s.scope)))

    monkeypatch.setattr(audit_mod, "prune", lambda *a, **kw: None)
    monkeypatch.setattr(audit_mod, "load_artifacts", lambda c, k: [
        {"ts": 1.0, "mode": "replay", "signals": ["x"], "data": {}, "text": "t",
         "_path": str(tmp_path / f"{k}.json")}])
    monkeypatch.setattr(audit_mod, "drop", lambda p: None)

    judged: list = []

    async def _judge(router, art):
        judged.append(art["_path"])
        if len(judged) == 1:
            raise RuntimeError("provider hung up mid-fleet")
        return {"reason_code": "semantic_mismatch_other", "evidence_quote": "q"}

    monkeypatch.setattr(audit_mod, "judge", _judge)
    monkeypatch.setattr(audit_mod, "decide", lambda *a, **kw: None)

    run = await flows_mod.audit_flows(names=["a", "b"], cache=cache, router=_router())

    assert len(judged) == 2, "one flow's failure aborted the fleet before the other was judged"
    assert run.advisories == 1, "the surviving flow's finding was discarded with it"
    assert run.unjudged >= 1, "an artifact nobody judged must count as UNJUDGED — that is not clean"
    assert any("hung up" in why for _n, why in run.errors), (
        "and the reason must land in `errors`, not `skipped`: `skipped` is the by-design bucket (a write "
        "flow is never judged) and the CLI hides it behind --verbose")
    assert run.exit_code == 3, "unjudged is a distinct, weaker alarm — never 0"
    assert set(keys) == {"a", "b"}                       # keys built above are the fixture's premise


async def test_a_broken_flow_in_the_gathering_half_does_not_abort_the_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same loop's FIRST half. `load_spec` is guarded; `audit.prune`, `audit.load_artifacts` and the
    `_load_meta` quarantine check that follow it are not — and after S4 that `_load_meta` can raise on an
    unreadable sidecar where it used to synthesise one. A guard on one read in a loop that does four is
    the shape this register keeps finding.
    """
    from ultracua import audit as audit_mod
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    specs = {n: _spec(flows_mod, n, audit="advisory") for n in ("bad", "good")}
    monkeypatch.setattr(flows_mod, "load_spec", lambda n: specs[n])
    good_key = flow_key(specs["good"].goal, specs["good"].start_url, specs["good"].scope)

    def _load_artifacts(c, k):
        if k != good_key:
            raise OSError(32, "sharing violation on the audit dir")
        return [{"ts": 1.0, "mode": "replay", "signals": ["x"], "data": {}, "text": "t",
                 "_path": str(tmp_path / "good.json")}]

    monkeypatch.setattr(audit_mod, "prune", lambda *a, **kw: None)
    monkeypatch.setattr(audit_mod, "load_artifacts", _load_artifacts)
    monkeypatch.setattr(audit_mod, "drop", lambda p: None)

    async def _judge(router, art):
        return {"reason_code": "semantic_mismatch_other", "evidence_quote": "q"}

    monkeypatch.setattr(audit_mod, "judge", _judge)
    monkeypatch.setattr(audit_mod, "decide", lambda *a, **kw: None)

    run = await flows_mod.audit_flows(names=["bad", "good"], cache=cache, router=_router())

    assert run.advisories == 1, "the healthy flow must still be judged"
    assert any(n == "bad" for n, _why in run.errors), "and the broken one reported, not swallowed"
    assert run.exit_code != 0, "a flow we could not even look at is not a clean audit"


async def test_a_flow_we_could_not_look_at_survives_the_no_llm_rollup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interaction a new counter creates with old code, found by re-reading the diff rather than by
    any test above. `unjudged` was ASSIGNED in the no-LLM early return (`run.unjudged = len(candidates)`)
    because nothing else could set it; the gather guard now increments it first, so the assignment erased
    a flow we had just reported we could not look at. `no_llm` holds the exit code at 3 either way — the
    damage is a summary that reports 0 unjudged beside a printed `[NOT AUDITED]` line.
    """
    from ultracua import audit as audit_mod
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    specs = {"bad": _spec(flows_mod, "bad", audit="advisory")}
    monkeypatch.setattr(flows_mod, "load_spec", lambda n: specs[n])
    monkeypatch.setattr(audit_mod, "prune", lambda *a, **kw: None)
    monkeypatch.setattr(audit_mod, "load_artifacts",
                        lambda c, k: (_ for _ in ()).throw(OSError(32, "sharing violation")))
    monkeypatch.setattr(flows_mod, "_llm_configured", lambda *a, **kw: False)

    run = await flows_mod.audit_flows(names=["bad"], cache=cache)

    assert run.no_llm is True, "premise: the no-LLM roll-up is the path under test"
    assert run.unjudged == 1, "the roll-up erased a flow that was already counted unjudged"
    assert run.errors and run.exit_code == 3


def test_flow_audit_prints_what_it_could_not_look_at_without_verbose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    """The half a per-flow guard gets wrong, and did once here: catching the exception is worthless if the
    result is only visible under `--verbose`. `flow audit` would then print a clean-looking summary and
    exit for a fleet it never examined — the guard wearing the clothes of the failure it prevents.

    The routine `skipped` bucket stays behind --verbose on purpose; the two must not share a channel.
    """
    from ultracua import cli
    from ultracua import flows as flows_mod

    async def _audit(**kw):
        return flows_mod.AuditRun(unjudged=1, skipped=[("quiet", "write flow (never captured)")],
                                  errors=[("broken", "NOT AUDITED: OSError: sharing violation")])

    monkeypatch.setattr(flows_mod, "audit_flows", _audit)
    args = argparse.Namespace(cmd="audit", name=None, list=False, show=False, set_mode=None,
                              max_calls=0, dry_run=False, keep=False, provider="anthropic",
                              verbose=False, allow_empty=False, purge=False)
    with pytest.raises(SystemExit) as ei:
        cli._flow_audit(args)
    out = capsys.readouterr().out
    assert "NOT AUDITED" in out and "broken" in out, "a flow we could not look at must always print"
    assert "quiet" not in out, "a by-design skip must NOT print without --verbose (that is the noise)"
    assert ei.value.code == 3
