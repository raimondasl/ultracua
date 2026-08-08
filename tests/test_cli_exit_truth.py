"""Work that did not succeed must exit nonzero, with the reason printed (CLI-2..5).

S7a fixed ONE instance of that rule, for `flow run-all`. These are the four surfaces that still break it.
Each is small alone; together they are the difference between a CLI you can script against and one you
cannot, because every one of them reports failure as success to `$?`.

    CLI-2  the ROOT command always exits 0, and never prints `report.note`
    CLI-3  `flow learn` exits 0 on a REFUSED learn, and drops the refusal reason for a generic line
           that is actively wrong about the cause
    CLI-4  `flow canary` exits 0 for an all-`not-learned` fleet — CLI-1's exact shape, recorded as
           known-identical when S7a landed rather than left to be rediscovered
    CLI-5  `flow status` reports 0 unreviewed advisories when the meta is UNREADABLE — the habituation
           counter reads clean exactly when the trust state is broken

The two rules S7a established, applied here rather than re-derived:

  * enumerate the QUIET outcomes, never the loud ones, so a status added tomorrow is loud by default;
  * a bare failure with no reason is the fail-loud inviolable read as fail-quiet — `flow.py` says so in
    as many words about the very `note` field CLI-2 discards.
"""

from __future__ import annotations

import argparse
import types
from pathlib import Path

import pytest

from ultracua import cli


def _ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


# ==================== CLI-2: the root command ====================


def _amain_args(**kw) -> argparse.Namespace:
    base = dict(url="http://127.0.0.1:1/", goal="do the thing", provider="mock", tier="strong",
                mode="auto", scope="default", fresh=False, max_steps=None, headless=True)
    base.update(kw)
    return _ns(**base)


async def test_the_root_command_exits_nonzero_when_the_run_failed(monkeypatch, capsys) -> None:
    """A scripted caller checking `$?` currently sees success on total failure.

    `run_cached` RETURNS `FlowReport(success=False)` rather than raising on every failure path — miss,
    escalate, verify-failed, the unattributed-write refusal — and `_amain` prints the flag without ever
    converting it into an exit code.
    """
    async def _fake(*a, **kw):
        return types.SimpleNamespace(
            mode="miss", success=False, llm_calls=0, healed_steps=0, step_traces=[],
            avg_step_ms=0.0, total_ms=0.0, final_text="", note="no cached flow for key", extra={})

    monkeypatch.setattr(cli, "run_cached", _fake)   # cli.py binds it at import
    monkeypatch.setattr(cli, "get_provider", lambda n: types.SimpleNamespace(tier="strong"))

    with pytest.raises(SystemExit) as ei:
        await cli._amain(_amain_args())
    assert ei.value.code != 0, "a failed run reported success to the shell"


async def test_the_root_command_prints_the_reason_it_was_given(monkeypatch, capsys) -> None:
    """`flow.py` populates `note` deliberately, with a comment that a bare `success=False` with no reason
    "is the fail-loud inviolable read as fail-quiet". The daemon surfaces it; the CLI dropped it, so a
    human saw a failure with no explanation the code had already computed.
    """
    async def _fake(*a, **kw):
        return types.SimpleNamespace(
            mode="learn", success=False, llm_calls=1, healed_steps=0, step_traces=[],
            avg_step_ms=0.0, total_ms=0.0, final_text="",
            note="a write fired on the wire that no step could be attributed to", extra={})

    monkeypatch.setattr(cli, "run_cached", _fake)   # cli.py binds it at import
    monkeypatch.setattr(cli, "get_provider", lambda n: types.SimpleNamespace(tier="strong"))

    with pytest.raises(SystemExit):
        await cli._amain(_amain_args())
    out = capsys.readouterr().out
    assert "no step could be attributed" in out, "the reason the engine computed was never shown"


async def test_a_successful_root_run_still_exits_zero(monkeypatch) -> None:
    """The must-remain-usable half. Without it, "exit nonzero" is satisfied by exiting nonzero always,
    which is the over-refusal shape this project has shipped once already."""
    async def _fake(*a, **kw):
        return types.SimpleNamespace(
            mode="replay", success=True, llm_calls=0, healed_steps=0, step_traces=[],
            avg_step_ms=0.0, total_ms=0.0, final_text="42", note="", extra={})

    monkeypatch.setattr(cli, "run_cached", _fake)   # cli.py binds it at import
    monkeypatch.setattr(cli, "get_provider", lambda n: types.SimpleNamespace(tier="strong"))

    with pytest.raises(SystemExit) as ei:
        await cli._amain(_amain_args())
    assert ei.value.code == 0


# ==================== CLI-3: flow learn ====================


def _learn_args(**kw) -> argparse.Namespace:
    base = dict(name="f", url="http://127.0.0.1:1/", goal="g", extract=None, provider="mock",
                samples=1, pin_read=False, fresh=False, headed=False, scope="default",
                max_steps=None, contracts=None, audit=None, slots=None, storage_state=None,
                header=None, login_url=None, username_env=None, password_env=None,
                username_selector=None, password_selector=None, submit_selector=None,
                success_selector=None, success_url_contains=None, timeout_ms=None,
                confirm_text_contains=None, confirm_selector=None, confirm_url_contains=None,
                precheck_text_contains=None, writable_slots=None, mutate=False)
    base.update(kw)
    return _ns(**base)


async def test_flow_learn_exits_nonzero_when_the_learn_was_refused(monkeypatch, tmp_path) -> None:
    """A refusal is not a success. `flow learn` printed a warning and returned 0, so a provisioning
    script that learns a fleet cannot tell a refused flow from a learned one."""
    monkeypatch.chdir(tmp_path)

    async def _fake_learn(spec, **kw):
        return types.SimpleNamespace(
            spec=spec, cached=False, found=False, steps=[], data=None, approved=False, pinned=False,
            note="a write fired on the wire that no step could be attributed to — refusing to cache it. "
                 "Record it with `flow record`.")

    monkeypatch.setattr("ultracua.flows.learn", _fake_learn)
    monkeypatch.setattr("ultracua.flows.save_spec", lambda s: None)

    with pytest.raises(SystemExit) as ei:
        await cli._flow_learn(_learn_args())
    assert ei.value.code != 0, "a refused learn exited 0"


async def test_flow_learn_prints_the_refusal_reason_not_a_generic_line(monkeypatch, tmp_path, capsys) -> None:
    """THE WORSE HALF, and the reason this is not merely an exit-code fix. The generic line says "the
    agent took no clean steps", which for the refusal population is FALSE and misdirecting: the agent's
    steps were fine, the flow was refused on write safety. The precise reason was already in
    `res.note` — including the remedy — and was discarded.
    """
    monkeypatch.chdir(tmp_path)
    reason = ("a write fired on the wire that no step could be attributed to — refusing to cache it. "
              "Record it with `flow record`.")

    async def _fake_learn(spec, **kw):
        return types.SimpleNamespace(spec=spec, cached=False, found=False, steps=[], data=None,
                                     approved=False, pinned=False, note=reason)

    monkeypatch.setattr("ultracua.flows.learn", _fake_learn)
    monkeypatch.setattr("ultracua.flows.save_spec", lambda s: None)

    with pytest.raises(SystemExit):
        await cli._flow_learn(_learn_args())
    out = capsys.readouterr().out
    assert "no step could be attributed" in out and "flow record" in out, (
        "the operator was told the agent took no clean steps, which is not what happened")


# ==================== CLI-4: flow canary ====================


def _canary(name: str, status: str):
    return types.SimpleNamespace(name=name, status=status, detail="")


def test_canary_exits_nonzero_when_the_whole_fleet_is_not_learned(monkeypatch) -> None:
    """CLI-1's shape exactly, on the freshness monitor. `not-learned` counts toward neither bucket, so a
    wiped cache or a wrong-cwd job prints `0 fresh, 0 stale`, exits 0, and reports healthy while
    checking nothing — which is the whole job of a canary, inverted.
    """
    async def _fake(**kw):
        return [_canary("a", "not-learned"), _canary("b", "not-learned")]

    monkeypatch.setattr("ultracua.flows.canary_all", _fake)
    with pytest.raises(SystemExit) as ei:
        cli._flow_canary(_ns(name=None, concurrency=4, allow_empty=False))
    assert ei.value.code != 0, "a fleet that checked nothing reported fresh"


def test_canary_stays_quiet_when_flows_are_actually_fresh(monkeypatch) -> None:
    """The must-remain-usable half again: a genuinely fresh fleet must not start paging people."""
    async def _fake(**kw):
        return [_canary("a", "fresh"), _canary("b", "fresh")]

    monkeypatch.setattr("ultracua.flows.canary_all", _fake)
    with pytest.raises(SystemExit) as ei:
        cli._flow_canary(_ns(name=None, concurrency=4, allow_empty=False))
    assert ei.value.code == 0


def test_a_canary_status_added_tomorrow_is_loud_by_default(monkeypatch) -> None:
    """S7a's rule, carried rather than re-derived: enumerate the QUIET statuses. Keying off the loud ones
    is exactly how `not-learned` came to satisfy neither channel here and `skipped` did in run-all."""
    async def _fake(**kw):
        return [_canary("a", "fresh"), _canary("b", "deferred")]

    monkeypatch.setattr("ultracua.flows.canary_all", _fake)
    with pytest.raises(SystemExit) as ei:
        cli._flow_canary(_ns(name=None, concurrency=4, allow_empty=False))
    assert ei.value.code != 0, "an unknown canary status was treated as fine"


# ==================== CLI-5: the advisory counter ====================


def test_an_unreadable_meta_does_not_report_zero_unreviewed_advisories(monkeypatch, tmp_path) -> None:
    """`_audit_advisories` swallowed EVERY exception to 0, so the habituation counter that the feature
    exists to surface reads clean exactly when the trust state is broken. Zero and "cannot tell" are
    different facts — the same distinction S4 drew for the sidecar loader, one surface over.
    """
    monkeypatch.chdir(tmp_path)
    from ultracua import flows as flows_mod

    def _boom(*a, **kw):
        raise OSError(32, "sharing violation")

    # PATCH `flows._load_meta`, NOT `cli._load_meta`. `_audit_advisories` imports it INSIDE the function
    # body, so setting an attribute on `cli` creates one nothing reads — the first draft did exactly
    # that and still went green, because `load_spec` raised first on a flow that does not exist. It
    # would have "passed" while never once exercising an unreadable META, which is what it claims to
    # test. A real spec is saved below so `load_spec` succeeds and the meta read is the only thing left
    # that can fail.
    flows_mod.save_spec(flows_mod.FlowSpec(name="f", start_url="http://127.0.0.1:1/", goal="g"))
    monkeypatch.setattr(flows_mod, "_load_meta", _boom)

    got = cli._audit_advisories("f")
    assert got is None, (
        f"an unreadable meta reported {got!r} unreviewed advisories — indistinguishable from a clean "
        f"flow, which is the one state that most warrants a look")
