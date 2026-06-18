"""The write/auth flow benchmark doubles as a key-less integration test of the Phase D + auth
lifecycle: write action-completion, one-shot idempotency, and auth-refresh recovery from expiry."""

from __future__ import annotations

from pathlib import Path

from benchmarks.write_flow_bench import scenario_auth, scenario_idempotent, scenario_write


async def test_write_scenario_confirms_the_write(tmp_path: Path) -> None:
    r = await scenario_write("scripted", tmp_path)
    assert r["passed"] and r["status"] == "confirmed" and r["orders"] >= 2


async def test_idempotent_scenario_skips_the_duplicate(tmp_path: Path) -> None:
    r = await scenario_idempotent("scripted", tmp_path)
    assert r["passed"] and r["status"] == "already-done"
    assert r["orders"] == r["after_learn"]  # the replay did NOT re-fire the write


async def test_auth_scenario_recovers_from_session_expiry(tmp_path: Path) -> None:
    r = await scenario_auth("scripted", tmp_path)
    assert r["passed"] and r["first"] == 42 and r["recovered_after_expiry"] == 42
