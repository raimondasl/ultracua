"""The flow HOME resolution + the fail-loud-on-empty-store guard (0.59.0 defect fixes).

Before this, `.ultracua/` was resolved relative to the CURRENT WORKING DIRECTORY with no override, so a cron
job or an MCP server started in the wrong directory found ZERO flows and reported SUCCESS — `flow run-all`
printed "0 ok, 0 failed, 0 skipped (of 0)" and exited 0, forever, doing nothing. That is inviolable #2
("never silently act wrong — fail loud") violated at the ops layer. All key-less; no browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ultracua import flows
from ultracua.cache import FlowCache
from ultracua.config import flow_home
from ultracua.flows import EmptyFlowStoreError, FlowSpec, _resolve_fleet, canary_all, run_all, save_spec


@pytest.fixture(autouse=True)
def _no_inherited_home(monkeypatch, tmp_path):
    """Isolate every test from a real `$ULTRACUA_HOME` AND from any `.ultracua` above pytest's tmp dir —
    otherwise the walk-up would find it and these assertions would fail mysteriously."""
    monkeypatch.delenv("ULTRACUA_HOME", raising=False)
    for d in (tmp_path, *tmp_path.parents):
        assert not (d / ".ultracua").is_dir(), f"an ancestor {d} holds .ultracua — the suite cannot isolate"


# ============================ resolution ============================

def test_env_var_wins_and_is_absolutized(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".ultracua").mkdir()          # a cwd store that must be OVERRIDDEN
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "elsewhere"
    monkeypatch.setenv("ULTRACUA_HOME", str(target))
    assert flow_home() == target.resolve()


def test_an_absolute_env_var_is_stable_across_a_chdir(monkeypatch, tmp_path: Path) -> None:
    """The whole point of the env var: the store must not depend on where the process was started. (A
    RELATIVE value is still cwd-resolved — documented as "use an absolute path", asserted below so the
    footgun is at least pinned rather than surprising.)"""
    sub = tmp_path / "a"
    sub.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "store"))
    resolved = flow_home()
    monkeypatch.chdir(sub)
    assert flow_home() == resolved, "an absolute ULTRACUA_HOME moved with the cwd"

    monkeypatch.setenv("ULTRACUA_HOME", "store")     # relative: documented as cwd-resolved
    monkeypatch.chdir(tmp_path)
    assert flow_home() == (tmp_path / "store").resolve()
    monkeypatch.chdir(sub)
    assert flow_home() == (sub / "store").resolve()


def test_cwd_store_is_used_when_present(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".ultracua").mkdir()
    monkeypatch.chdir(tmp_path)
    assert flow_home() == tmp_path / ".ultracua"      # today's behavior, preserved


def test_walk_up_finds_the_project_store_from_a_subdirectory(monkeypatch, tmp_path: Path) -> None:
    """THE DEFECT-2 FIX: running from a subdirectory used to find nothing at all."""
    (tmp_path / ".ultracua" / "specs").mkdir(parents=True)
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert flow_home() == tmp_path / ".ultracua"


def test_no_store_anywhere_falls_back_to_cwd_never_to_the_user_home(monkeypatch, tmp_path: Path) -> None:
    """There is deliberately NO `~/.ultracua` fallback — a job in the wrong directory must fail loudly, never
    quietly run a DIFFERENT fleet."""
    fake_home = tmp_path / "fakehome"
    (fake_home / ".ultracua" / "specs").mkdir(parents=True)   # a populated user-home store...
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert flow_home() == work / ".ultracua"                   # ...is NOT used
    assert flow_home() != fake_home / ".ultracua"


def test_the_two_roots_can_never_diverge(monkeypatch, tmp_path: Path) -> None:
    """The specs dir and the flow CACHE must resolve from the SAME home — divergence is how a wrong-cwd run
    could half-find a store."""
    monkeypatch.chdir(tmp_path)
    assert FlowCache().root.parent == flows._specs_dir().parent == flow_home()
    assert FlowCache(root=tmp_path / "explicit").root == tmp_path / "explicit"   # explicit still wins


def test_a_spec_round_trips_across_a_chdir_into_a_subdirectory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    save_spec(FlowSpec(name="daily", start_url="http://127.0.0.1:9/", goal="g"))
    assert flows.list_specs() == ["daily"]
    sub = tmp_path / "deep" / "er"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert flows.list_specs() == ["daily"], "the flow was invisible from a subdirectory"
    assert flows.load_spec("daily").name == "daily"


# ============================ fail loud on an empty store ============================

def test_resolve_fleet_distinguishes_explicit_empty_from_found_nothing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    # an EXPLICIT empty list is a caller who asked for nothing — no raise (this pins existing callers)
    assert _resolve_fleet([], allow_empty=False, verb="x") == []
    # names=None meaning "every saved flow", resolving to zero, is the silent-success footgun
    with pytest.raises(EmptyFlowStoreError) as ei:
        _resolve_fleet(None, allow_empty=False, verb="flow run-all")
    assert str(flow_home()) in str(ei.value)          # the diagnosis is IN the error
    assert _resolve_fleet(None, allow_empty=True, verb="x") == []


async def test_fleet_verbs_refuse_an_empty_store(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for verb in (run_all, canary_all):
        with pytest.raises(EmptyFlowStoreError):
            await verb()
        assert await verb(allow_empty=True) == []     # the escape hatch


async def test_audit_refuses_an_empty_store_but_honours_an_explicit_name_list(monkeypatch, tmp_path) -> None:
    from ultracua.flows import audit_flows

    monkeypatch.chdir(tmp_path)
    with pytest.raises(EmptyFlowStoreError):
        await audit_flows()
    assert (await audit_flows(allow_empty=True)).judged == 0
    assert (await audit_flows(names=[])).judged == 0   # explicit empty -> no raise (pins test_audit.py)


# ============================ the CLI exit codes (the actual reported bug) ============================

@pytest.mark.parametrize("verb,expected", [("run-all", 2), ("canary", 2), ("audit", 3)])
def test_cli_fleet_verbs_exit_non_zero_on_an_empty_store(monkeypatch, tmp_path, verb, expected) -> None:
    """THE REGRESSION TEST for the reported defect: these used to exit 0 while doing nothing."""
    from ultracua import cli

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as ei:
        cli._flow_main([verb])
    assert ei.value.code == expected


@pytest.mark.parametrize("verb", ["run-all", "canary"])
def test_cli_allow_empty_restores_exit_zero(monkeypatch, tmp_path, verb) -> None:
    from ultracua import cli

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as ei:
        cli._flow_main([verb, "--allow-empty"])
    assert ei.value.code == 0


def test_cli_serve_mcp_refuses_an_empty_store(monkeypatch, tmp_path) -> None:
    """An MCP client launches its server with an ARBITRARY cwd, so this surface was the likeliest of all to
    come up 'healthy' exposing zero tools with the failure visible nowhere."""
    from ultracua import cli

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as ei:
        cli._flow_main(["serve-mcp"])
    assert ei.value.code == 2


def test_cli_status_and_list_stay_exit_zero_and_print_the_resolved_home(monkeypatch, tmp_path, capsys) -> None:
    """Inspection verbs must not start failing — but they now print the home, which is usually the whole
    diagnosis for 'why does it say no flows?'."""
    from ultracua import cli

    monkeypatch.chdir(tmp_path)
    for verb in ("status", "list"):
        cli._flow_main([verb])                        # no SystemExit
        assert str(flow_home()) in capsys.readouterr().out


def test_the_unapprove_verb_the_docs_prescribe_actually_exists(monkeypatch, tmp_path) -> None:
    """REGRESSION: an approved flow keeps its blessed shape/contracts across a re-learn, and the documented
    recovery is `unapprove -> learn -> approve`. That prescription named a CLI verb that did not exist, so a
    legitimate page restructure wedged the flow with no CLI escape."""
    from ultracua import cli

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as ei:
        cli._flow_main(["unapprove", "--help"])       # argparse exits 0 for a verb that exists
    assert ei.value.code == 0

    import time as _t

    from ultracua.cache import CachedFlow, CachedStep
    from ultracua.locators import LocatorSpec

    spec = FlowSpec(name="wedge", start_url="http://127.0.0.1:9/", goal="g")
    save_spec(spec)
    key = flows.flow_key("g", "http://127.0.0.1:9/", "flow:wedge")
    cache = FlowCache()
    cache.put(CachedFlow(key=key, goal="g", start_url=spec.start_url, created_ts=_t.time(), steps=[
        CachedStep(intent="go", action="click", locator=LocatorSpec(role="link", name="x", tag="a"))]))
    flows.approve(spec, cache=cache)
    assert flows._load_meta(cache, key).approved
    cli._flow_main(["unapprove", "--name", "wedge"])                  # and it runs
    assert not flows._load_meta(cache, key).approved

    # it also mirrors `approve`'s guard rather than minting a sidecar for a never-learned flow
    save_spec(FlowSpec(name="never", start_url="http://127.0.0.1:9/x", goal="g2"))
    with pytest.raises(flows.FlowReplayError, match="nothing to unapprove"):
        cli._flow_main(["unapprove", "--name", "never"])


@pytest.mark.parametrize("extra", [["--purge"], ["--list"], ["--show"]])
def test_cli_audit_maintenance_flags_also_refuse_an_empty_store(monkeypatch, tmp_path, extra) -> None:
    """REGRESSION: `--purge`/`--list` bypassed the fleet resolver, so `flow audit --purge` printed
    'purged the audit artifact store' and exited 0 having deleted NOTHING on a wrong-cwd store — the same
    silent-success class this slice exists to close."""
    from ultracua import cli

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as ei:
        cli._flow_main(["audit", *extra])
    assert ei.value.code == 3
    cli._flow_main(["audit", *extra, "--allow-empty"])   # the escape hatch returns normally


# ============ defect 1 (the safe half): an approved flow refuses unattended self-re-authoring ============

async def test_relearn_is_refused_on_an_approved_flow(tmp_path: Path, monkeypatch) -> None:
    """`on_drift="relearn"` lets the engine self-heal / suffix-replan / re-author the cached steps and write
    them straight back. On an APPROVED flow that would replay LLM-authored steps no human reviewed, under the
    existing approval — and would re-baseline the H9 value contracts. Refuse, like a write flow already does."""
    from ultracua.flows import FlowReplayError, _load_meta, _preflight_row, approve, flow_key, learn, save_spec

    from test_flows import _ClickFirstLink, _extract_router, _serve, _write_fixture

    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        spec = FlowSpec(name="relearn", start_url=f"{base}/page1.html", goal="open the answer page",
                        extract="the answer number", headless=True)
        assert (await learn(spec, provider=_ClickFirstLink(), router=_extract_router(42), cache=cache)).cached
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        cached = cache.get(key)

        # UNAPPROVED: relearn is allowed (nothing has been blessed yet) — today's behavior, preserved
        meta = _load_meta(cache, key)
        _preflight_row(spec, None, meta=meta, cached_flow=cached, on_drift="relearn")

        approve(spec, cache=cache)
        save_spec(spec)
        meta = _load_meta(cache, key)
        # APPROVED: refused, 0-LLM, before any browser opens
        with pytest.raises(FlowReplayError) as ei:
            _preflight_row(spec, None, meta=meta, cached_flow=cached, on_drift="relearn")
        assert "refused for an APPROVED flow" in str(ei.value)
        # ...and the default on_drift="raise" is completely unaffected
        _preflight_row(spec, None, meta=meta, cached_flow=cached, on_drift="raise")
    finally:
        httpd.shutdown()
        httpd.server_close()
