"""H9 layer 2 — deterministic MAGNITUDE defense (a value too far from its own rolling baseline fails loud).

Catches the wrong-but-SAME-SIGN, above-floor, non-null scalar that layer 1 misses (a price 129 -> 40). Pure
band tests + a bounded numbers-only history store + the flows.py wiring (warm-up advisory -> enforce ->
persisted quarantine -> release/rebaseline). Wiring tests PRE-SEED the history ring (via history.save_history)
to avoid many browser replays; the scripted extraction router feeds the per-run value. All key-less.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ultracua import contracts as C
from ultracua import history as H
from ultracua.cache import FlowCache, flow_key
from ultracua.contracts import effective_contracts, magnitude_fields, seed_contracts
from ultracua.flows import (
    FlowQuarantineError,
    FlowReplayError,
    _load_meta,
    health,
    learn,
    release,
    replay,
)

from test_contracts import _learn_read
from test_flows import _ClickFirstLink, _extract_router, _serve, _write_fixture


# ============================ pure band + scope + accrue (no browser) ============================

def test_band_closes_the_129_to_40_gap() -> None:
    assert C.check_magnitude({}, 40, [129] * 5) is not None    # wrong-but-positive scalar -> trips
    assert C.check_magnitude({}, 130, [129] * 5) is None       # legit jitter -> passes
    assert C.check_magnitude({}, 110, [129] * 5) is None       # a 15% promo -> passes (the 0.25 floor headroom)


def test_zero_variance_floor_boundary() -> None:
    # A constant field has mad=0, so tol = 0.25*|median| = 32.25; |129 - x| > 32.25 trips.
    assert C.check_magnitude({}, 98, [129] * 10) is None       # |31| < 32.25
    assert C.check_magnitude({}, 96, [129] * 10) is not None   # |33| > 32.25


def test_mad_self_widens_for_a_volatile_field() -> None:
    ring = [100, 150, 200, 250, 300, 150, 250, 200]           # median 200, MAD 50 -> a wide self-calibrated band
    assert C.check_magnitude({}, 260, ring) is None
    assert C.check_magnitude({}, 480, ring) is None            # inside its own ~2x spread -> no habituation
    assert C.check_magnitude({}, 1000, ring) is not None       # a gross excursion still trips


def test_no_baseline_and_zero_centered_skip() -> None:
    assert C.check_magnitude({}, 40, []) is None               # n==0 -> nothing to compare
    assert C.check_magnitude({}, 5, [0] * 6) is None           # zero-centered AND zero-spread -> no scale


def test_zero_median_with_spread_does_not_crash() -> None:
    # REGRESSION: a sign-oscillating field whose rolling MEDIAN lands exactly on 0 but has real spread (a
    # net-change / P&L) must fail loud with a value-free reason — NOT raise ZeroDivisionError in the reason.
    for ring in ([-5, 0, 5], [-3, 3], [-5.0, -2.0, 0.0, 3.0, 8.0]):
        r = C.check_magnitude({}, 40, ring)                    # 40 is far outside the ±few band -> a violation
        assert r is not None and "40" not in r and "x the allowed" in r   # tolerance-multiple, value-free
    assert C.check_magnitude({}, 2, [-5, 0, 5]) is None        # within the band (|2-0| < ~5*K) -> passes


def test_reason_is_value_free() -> None:
    r = C.check_magnitude({}, 40, [129] * 5)
    assert r and "40" not in r and "129" not in r and "%" in r and "n=" in r


def test_per_field_overrides() -> None:
    assert C.check_magnitude({"max_delta_frac": 1.0}, 40, [129] * 5) is None   # a wide floor tolerates it
    ring = [150, 180, 200, 220, 250]
    assert C.check_magnitude({"delta_k": 0.1}, 400, ring) is not None          # a tight k narrows the MAD band


def test_magnitude_fields_scope() -> None:
    dict_eff = effective_contracts(None, seed_contracts({"price": 129, "name": "x"}))
    assert magnitude_fields(dict_eff, {"price": 129, "name": "x"}) == {"price": 129.0}   # number in, string out
    assert magnitude_fields(effective_contracts(None, seed_contracts(129)), 129) == {"": 129.0}   # root scalar
    assert magnitude_fields(effective_contracts(None, seed_contracts(True)), True) == {}          # bool excluded
    lst = seed_contracts([1, 2, 3, 4, 5])
    assert magnitude_fields(effective_contracts(None, lst), [1, 2, 3, 4, 5]) == {}     # root list / [] out of scope
    off = effective_contracts({"price": {"delta_enabled": False}}, seed_contracts({"price": 129}))
    assert magnitude_fields(off, {"price": 129}) == {}                                 # per-field disabled


def test_accrue_ring_truncates_to_ring_size() -> None:
    assert C.accrue_ring([1, 2, 3], 4) == [1, 2, 3, 4]
    assert len(C.accrue_ring(list(range(30)), 99.0)) == C.DELTA_RING


# ============================ history store (numbers-only, tolerant) ============================

def test_history_load_is_tolerant_and_numbers_only(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path)
    assert H.load_history(cache, "k")["fields"] == {}                    # missing file
    p = H.history_path(cache, "k")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert H.load_history(cache, "k")["fields"] == {}                    # torn/corrupt -> empty, no raise
    H.save_history(cache, "k", {"v": 1, "fields": {"": [1, 2, "leak", True, 3]}})
    assert H.load_history(cache, "k")["fields"][""] == [1, 2, 3]         # string + bool filtered out on load


# ============================ flows.py wiring (browser fixture, pre-seeded ring) =================

def _seed_ring(cache: FlowCache, spec, ring: list) -> str:
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    H.save_history(cache, key, {"v": 1, "fields": {"": list(ring)}})
    return key


async def test_warmed_baseline_quarantines_a_magnitude_drop(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        spec = await _learn_read(cache, base, data=129)        # layer-1 seeds positive; learn resets history
        key = _seed_ring(cache, spec, [129] * 5)               # a warmed baseline (n=5 == warmup)
        with pytest.raises(FlowQuarantineError) as ei:         # 40 is positive (layer 1 OK) but a 69% drop
            await replay(spec, router=_extract_router(40), cache=cache)
        assert ei.value.code == "quarantined" and "magnitude" in str(ei.value)
        assert _load_meta(cache, key).quarantine is not None   # PERSISTED
        assert health(spec, cache=cache).status == "quarantined"
        assert H.load_history(cache, key)["fields"][""] == [129] * 5   # NOT poisoned by the bad value
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_advisory_before_warmup_never_quarantines(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        spec = await _learn_read(cache, base, data=129)
        key = _seed_ring(cache, spec, [129, 129])              # n=2 < warmup 5 -> advisory
        assert await replay(spec, router=_extract_router(40), cache=cache) == 40   # succeeds (advisory)
        assert 40.0 in H.load_history(cache, key)["fields"][""]                    # accrued even while advisory
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_release_default_rejects_and_rebaseline_rewarms(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        spec = await _learn_read(cache, base, data=129)
        key = _seed_ring(cache, spec, [129] * 5)
        with pytest.raises(FlowQuarantineError):
            await replay(spec, router=_extract_router(40), cache=cache)
        # default release re-arms the SAME baseline: a value still at 40 re-quarantines (no silent habituation)
        release(spec, cache=cache)
        with pytest.raises(FlowQuarantineError):
            await replay(spec, router=_extract_router(40), cache=cache)
        # rebaseline clears the baseline: 40 becomes the new normal (n=0 skip -> accrues), no quarantine
        release(spec, cache=cache, rebaseline=True)
        assert await replay(spec, router=_extract_router(40), cache=cache) == 40
        assert H.load_history(cache, key)["fields"][""] == [40]
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_relearn_clears_the_baseline(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        spec = await _learn_read(cache, base, data=129)
        key = _seed_ring(cache, spec, [129] * 5)
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router(129), cache=cache)   # re-learn
        assert H.load_history(cache, key)["fields"] == {}       # a re-authored extraction restarts the window
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_magnitude_quarantine_leaves_no_raw_value_at_rest(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        spec = await _learn_read(cache, base, data=129)
        key = _seed_ring(cache, spec, [129] * 5)
        with pytest.raises(FlowQuarantineError):
            await replay(spec, router=_extract_router(40), cache=cache)
        doc = json.loads(H.history_path(cache, key).read_text(encoding="utf-8"))
        for ring in doc["fields"].values():
            assert all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in ring)
        assert _load_meta(cache, key).quarantine is not None    # reason is value-free (percentages + n only)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_magnitude_knob_change_requires_reapproval(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        spec = await _learn_read(cache, base, data=129)         # approved, no human overlay
        spec.contracts = {"": {"max_delta_frac": 0.5}}          # a magnitude knob change, not re-approved
        with pytest.raises(FlowReplayError) as ei:
            await replay(spec, router=_extract_router(129), cache=cache)
        assert not isinstance(ei.value, FlowQuarantineError) and "re-approve" in str(ei.value)
    finally:
        httpd.shutdown()
        httpd.server_close()
