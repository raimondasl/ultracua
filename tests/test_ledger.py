"""H3 slice 2c — the per-row resume ledger, key-less against local fixtures.

The ledger lets a re-run of the SAME batch job SKIP rows that already committed (never re-firing their
writes) while the Idempotency-Key remains the correctness floor (a crash-window re-fire dedupes at the
backend). These tests pin: resume skips landed rows (server-side write oracle), a torn last line is
tolerated, a different job-token is a fresh run, resume=None is byte-identical to today, a content change
de-matches, record-strictly-after-confirm, dry-run+resume previews without appending, and reads no-op.
"""

from __future__ import annotations

import pytest

from ultracua import flows
from ultracua.cache import FlowCache, flow_key
from ultracua.flows import (
    FlowReplayError,
    FlowSpec,
    MutateSpec,
    SlotSpec,
    _plan_idempotency_keys,
    run_batch,
    validate_params,
)
from ultracua.ledger import LedgerError, RunLedger

# Reuse the write fixture + record helper from the run_batch suite (tests/ is on sys.path).
from test_run_batch import _CheckoutSite, _EchoSite, _record_write_flow


def _key(cache, spec, row):
    """The real Idempotency-Key tuple a given row would mint (as run_batch computes it)."""
    cached = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
    return _plan_idempotency_keys(spec, validate_params(spec, row), cached)


# --- unit: RunLedger (no browser) -------------------------------------------------------------
def test_ledger_roundtrip_and_path(tmp_path) -> None:
    cache = FlowCache(root=tmp_path / "flows")
    led = RunLedger.open(cache, "fk123", "job1", "flow:x")
    assert led.path == cache.root / "ledgers" / "fk123.job1.jsonl"
    assert led.committed() == set()                  # absent file -> empty
    assert led.is_committed([]) is False and led.is_committed(["uca-k"]) is False
    led.record(0, ["uca-a", "uca-b"], "confirmed")
    led.record(1, ["uca-c"], "confirmed")
    led.close()
    led2 = RunLedger.open(cache, "fk123", "job1", "flow:x")   # reopen -> both tuples present
    done = led2.committed()
    assert ("uca-a", "uca-b") in done and ("uca-c",) in done
    assert led2.is_committed(["uca-a", "uca-b"]) and not led2.is_committed(["uca-x"])


def test_ledger_header_guard_refuses_foreign_file(tmp_path) -> None:
    cache = FlowCache(root=tmp_path / "flows")
    RunLedger.open(cache, "fk123", "job1", "flow:x").record(0, ["uca-a"], "confirmed")
    # A ledger whose header scope/flow_key doesn't match must NOT authorize skips — raise loud.
    foreign = RunLedger.open(cache, "fk123", "job1", "flow:DIFFERENT")
    with pytest.raises(LedgerError):
        foreign.committed()


def test_ledger_bad_job_id_refused(tmp_path) -> None:
    cache = FlowCache(root=tmp_path / "flows")
    for bad in ("../evil", "a/b", "a\\b", "x" * 65, "has space", ""):
        with pytest.raises(LedgerError):
            RunLedger.open(cache, "fk", bad, "flow:x")


def test_ledger_tolerates_torn_last_line(tmp_path) -> None:
    cache = FlowCache(root=tmp_path / "flows")
    led = RunLedger.open(cache, "fk", "job1", "flow:x")
    led.record(0, ["uca-a"], "confirmed")
    led.close()
    with led.path.open("a", encoding="utf-8") as f:      # a crash mid-append: a truncated JSON line
        f.write('{"kind":"commit","index":1,"keys":["uca-b"')
    done = RunLedger.open(cache, "fk", "job1", "flow:x").committed()
    assert ("uca-a",) in done and ("uca-b",) not in done   # torn line ignored -> its row re-fires


def test_mint_job_id_unique_and_safe() -> None:
    a, b = RunLedger.mint_job_id(), RunLedger.mint_job_id()
    from ultracua.ledger import _SAFE_JOB
    assert a != b and _SAFE_JOB.match(a) and _SAFE_JOB.match(b)


# --- behavioral: resume skips landed rows (the required crash-sim pin) ------------------------
async def test_resume_skips_committed_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        rows = [{"qty": "9"}, {"qty": "8"}, {"qty": "7"}]
        # Simulate a prior run that committed rows 0 and 1: append THEIR real key-tuples to the ledger.
        led = RunLedger.open(cache, flow_key(spec.goal, spec.start_url, spec.scope), "job1", spec.scope)
        for i in (0, 1):
            led.record(i, _key(cache, spec, rows[i]), "confirmed")
        led.close()

        out = await run_batch(spec, rows, max_rows=10, resume="job1", cache=cache)
        assert out.status == "ok" and out.job_id == "job1"
        assert [r.status for r in out.rows] == ["resumed", "resumed", "ok"]
        assert out.resumed == 2 and out.ok_count == 1
        # ONLY the not-yet-committed row (qty=7) reached the server — rows 0/1 were NOT re-fired.
        assert [w[0] for w in site.writes] == ["/order"]
        assert "qty=7" in site.writes[0][2]
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_resume_records_committed_rows_for_next_run(tmp_path, monkeypatch) -> None:
    # A fresh run under a job-id records each landed row; a SECOND run under the same id skips them all.
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        rows = [{"qty": "9"}, {"qty": "8"}]
        first = await run_batch(spec, rows, max_rows=10, resume="jobA", cache=cache)
        assert first.status == "ok" and first.ok_count == 2 and len(site.writes) == 2
        del site.writes[:]
        # Re-run the SAME job -> every row already committed -> all skipped, ZERO new writes.
        second = await run_batch(spec, rows, max_rows=10, resume="jobA", cache=cache)
        assert second.status == "ok" and second.resumed == 2 and second.ok_count == 0
        assert site.writes == []
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_resume_different_token_is_fresh(tmp_path, monkeypatch) -> None:
    # A DIFFERENT job-id is an independent run (a legitimate recurrence) — every row actuates again.
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        rows = [{"qty": "9"}, {"qty": "8"}]
        await run_batch(spec, rows, max_rows=10, resume="A", cache=cache)
        del site.writes[:]
        out = await run_batch(spec, rows, max_rows=10, resume="B", cache=cache)   # new token
        assert out.status == "ok" and out.ok_count == 2 and out.resumed == 0
        assert [w[0] for w in site.writes] == ["/order", "/order"]
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_resume_none_writes_no_ledger(tmp_path, monkeypatch) -> None:
    # No resume id -> no ledger built (byte-identical to today); nothing under .ultracua/ledgers.
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        out = await run_batch(spec, [{"qty": "9"}], max_rows=10, cache=cache)
        assert out.status == "ok" and out.job_id is None and out.resumed == 0
        assert not (cache.root / "ledgers").exists()
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_resume_content_change_dematches_and_refires(tmp_path, monkeypatch) -> None:
    # A row whose value changed since the prior run mints a DIFFERENT key-tuple -> it de-matches the old
    # ledger entry and correctly re-fires as a genuinely new write (content-derived identity).
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        led = RunLedger.open(cache, flow_key(spec.goal, spec.start_url, spec.scope), "job1", spec.scope)
        led.record(0, _key(cache, spec, {"qty": "9"}), "confirmed")   # old value committed
        led.close()
        out = await run_batch(spec, [{"qty": "5"}], max_rows=10, resume="job1", cache=cache)  # value changed
        assert out.rows[0].status == "ok" and out.resumed == 0        # not falsely skipped
        assert "qty=5" in site.writes[-1][2]
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_failed_row_not_recorded_so_resume_refires(tmp_path, monkeypatch) -> None:
    # Record-strictly-after-confirm: a row whose write is refused (mutation-gate drift) never lands in the
    # ledger, so a re-run RE-FIRES it (never a false skip of an un-committed write).
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        # Drift the form so the write is refused at actuation.
        site.checkout_html = site.CHECKOUT.replace(
            "<button type='submit'>",
            "<label for='c'>coupon</label><input id='c' name='c'><button type='submit'>")
        first = await run_batch(spec, [{"qty": "9"}], max_rows=10, resume="job1", cache=cache)
        assert first.status == "failed" and first.rows[0].status == "failed"
        # The refused row is NOT in the ledger.
        led = RunLedger.open(cache, flow_key(spec.goal, spec.start_url, spec.scope), "job1", spec.scope)
        assert not led.is_committed(_key(cache, spec, {"qty": "9"}))
        # Un-drift and resume: the row re-fires (it never committed).
        site.checkout_html = site.CHECKOUT
        del site.writes[:]
        second = await run_batch(spec, [{"qty": "9"}], max_rows=10, resume="job1", cache=cache)
        assert second.status == "ok" and second.ok_count == 1 and len(site.writes) == 1
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_dry_run_resume_previews_without_appending(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        rows = [{"qty": "9"}, {"qty": "8"}]
        led = RunLedger.open(cache, flow_key(spec.goal, spec.start_url, spec.scope), "job1", spec.scope)
        led.record(0, _key(cache, spec, rows[0]), "confirmed")
        led.close()
        size_before = led.path.stat().st_size
        plan = await run_batch(spec, rows, max_rows=10, dry_run=True, resume="job1", cache=cache)
        assert plan.status == "planned" and plan.dry_run is True
        assert [r.status for r in plan.rows] == ["resumed", "planned"] and plan.resumed == 1
        assert site.writes == []                              # dry-run actuates nothing
        assert led.path.stat().st_size == size_before          # ...and appends nothing to the ledger
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_cli_run_batch_dry_run_resume_preview(tmp_path, monkeypatch, capsys) -> None:
    # The `flow run-batch --resume <id>` dry-run path (no --commit, no browser): loads rows, previews the
    # plan under the ledger, prints PLAN rows + a PLANNED roll-up, exits 0.
    import time as _t

    from ultracua.cache import CachedFlow, CachedStep
    from ultracua.cli import _flow_main
    from ultracua.locators import LocatorSpec

    monkeypatch.chdir(tmp_path)
    spec = FlowSpec(name="order", start_url="http://127.0.0.1:9/checkout", goal="place the order",
                    mutate=MutateSpec(confirm_text_contains="ok"), slots={"qty": SlotSpec(type="string")})
    flows.save_spec(spec)
    cache = FlowCache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, created_ts=_t.time(), steps=[
        CachedStep(intent="type qty", action="type", text="7", slot="qty",
                   locator=LocatorSpec(role="textbox", name="qty", tag="input")),
        CachedStep(intent="place the order", action="click", mutating=True,
                   locator=LocatorSpec(role="button", name="Place the order", tag="button")),
    ]))
    flows.approve(spec, cache=cache)
    rows_file = tmp_path / "rows.json"
    rows_file.write_text('[{"qty": "9"}, {"qty": "8"}]', encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        _flow_main(["run-batch", "--name", "order", "--rows", str(rows_file),
                    "--max-rows", "10", "--resume", "job1"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "PLAN" in out and "PLANNED" in out


async def test_reads_resume_is_noop(tmp_path, monkeypatch) -> None:
    # A READ batch is idempotent — resume builds no ledger and re-runs every row.
    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _EchoSite()
    httpd, base = site.serve()
    try:
        spec = FlowSpec(name="lookup", start_url=base + "/", goal="enter the code", headless=True,
                        slots={"code": SlotSpec(type="string")})

        async def _demo(pg) -> None:
            await pg.fill("#q", "alpha-7")
            await pg.locator("#q").blur()

        await flows.record(spec, demo=_demo, headless=True, cache=cache)
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        for s in flow.steps:
            if s.action == "type":
                s.slot = "code"
        cache.put(flow)
        flows.approve(spec, cache=cache)

        site.gets.clear()
        out = await run_batch(spec, [{"code": "beta-9"}], resume="job1", cache=cache)
        assert out.status == "ok" and out.ok_count == 1 and out.resumed == 0
        assert not (cache.root / "ledgers").exists()          # reads build no ledger
        assert "/typed-beta-9" in site.gets
    finally:
        httpd.shutdown()
        httpd.server_close()
