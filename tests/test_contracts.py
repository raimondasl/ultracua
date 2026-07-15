"""H9 layer 1 — deterministic VALUE contracts (fail loud on a same-shape-but-wrong value).

Two layers of tests: (1) the PURE predicate module `ultracua.contracts` (fast, no browser); (2) the flows.py
WIRING — learn auto-seeds a contract, a wrong replayed value QUARANTINES (persisted), every future run refuses
0-LLM until `release()`. Wiring tests reuse the browser fixture from test_flows (a scripted agent + a
MockClient extraction router that returns the exact `data` per replay), so no API key is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ultracua import contracts as C
from ultracua import flows
from ultracua.cache import FlowCache, flow_key
from ultracua.flows import (
    FlowQuarantineError,
    FlowReplayError,
    FlowSpec,
    _load_meta,
    _meta_path,
    approve,
    health,
    learn,
    release,
    replay,
)

# Browser fixture + scripted extraction router (tests/ has no __init__ → sibling import works).
from test_flows import _ClickFirstLink, _extract_router, _serve, _serve_auth, _write_fixture


# ============================ pure predicate layer (no browser) ============================

def test_seed_from_root_dict() -> None:
    seed = C.seed_contracts({"price": 129, "name": "widget", "when": "2026-07-15"}, truncated=False)
    assert seed["price"] == {"type": "number", "nullable": False, "positive": True}
    assert seed["name"] == {"type": "string", "nullable": False}
    assert seed["when"]["pattern"] and seed["when"]["nullable"] is False   # ISO date recognized


def test_seed_from_root_list_count_floor() -> None:
    seed = C.seed_contracts(list(range(1, 501)), truncated=False)
    assert seed[""]["min_count"] == 250                     # 0.5 * len — only a >50% collapse trips
    assert seed["[]"]["type"] == "number" and seed["[]"]["positive"] is True


def test_check_passes_on_good_value() -> None:
    eff = C.effective_contracts(None, C.seed_contracts({"price": 129}, truncated=False))
    assert C.check_contracts(eff, {"price": 130}) is None


@pytest.mark.parametrize("wrong,needle", [
    ({"price": 0}, "positive"),
    ({"price": -5}, "positive"),
    ({"price": None}, "non-null"),
])
def test_check_trips_on_wrong_scalar(wrong, needle) -> None:
    eff = C.effective_contracts(None, C.seed_contracts({"price": 129}, truncated=False))
    reason = C.check_contracts(eff, wrong)
    assert reason is not None and needle in reason
    assert "129" not in reason and str(wrong["price"]) not in reason  # VALUE-FREE reason


def test_check_trips_on_bad_format_and_count() -> None:
    date_eff = C.effective_contracts(None, C.seed_contracts({"d": "2026-07-15"}, truncated=False))
    assert C.check_contracts(date_eff, {"d": "N/A"}) is not None      # same shape (string), wrong format
    list_eff = C.effective_contracts(None, C.seed_contracts(list(range(1, 11)), truncated=False))
    assert C.check_contracts(list_eff, [1, 2]) is not None            # 10 -> 2 : below floor 5
    assert C.check_contracts(list_eff, list(range(1, 9))) is None     # 8 : within tolerance


def test_empty_list_hole_is_closed() -> None:
    # _shape_matches treats an empty/mixed array as "can't disprove" — the count floor closes that.
    eff = C.effective_contracts(None, C.seed_contracts([1, 2, 3, 4, 5], truncated=False))
    assert C.check_contracts(eff, []) is not None


def test_effective_overlay_relaxes_one_attr() -> None:
    seed = C.seed_contracts({"price": 129}, truncated=False)          # positive=True
    eff = C.effective_contracts({"price": {"positive": False}}, seed)
    assert C.check_contracts(eff, {"price": 0}) is None               # relaxed exactly that predicate
    assert eff["price"]["type"] == "number" and eff["price"]["nullable"] is False   # the rest survives


def test_disabled_field_skips_all_checks() -> None:
    seed = C.seed_contracts({"price": 129}, truncated=False)
    eff = C.effective_contracts({"price": {"enabled": False}}, seed)
    assert C.check_contracts(eff, {"price": None}) is None


def test_truncated_learn_seeds_no_count_floor_or_null_rate() -> None:
    seed = C.seed_contracts([1, 2, 3, 4, 5, 6], truncated=True)
    assert "" not in (seed or {})                     # no min_count from a cut list
    assert "null_rate_max" not in seed.get("[]", {})  # no null-rate from a cut list
    assert seed["[]"]["type"] == "number"             # the visible values are still real


def test_truncated_replay_under_floor_still_fails_loud() -> None:
    # A floor learned from a full page; a truncated SHORT replay over it must fail loud (a short-because-cut
    # list is exactly the partial data we must not bless), while null-rate is suppressed under truncation.
    eff = {"": {"min_count": 5}, "[].x": {"null_rate_max": 0.2}}
    assert C.check_contracts(eff, [1, 2], truncated=True) is not None            # count floor: enforced
    listy = [{"x": 1}, {"x": None}, {"x": None}]
    assert C.check_contracts({"[].x": {"null_rate_max": 0.2}}, listy, truncated=True) is None  # rate: suppressed


def test_list_of_objects_null_rate() -> None:
    rows = [{"amt": 1}, {"amt": 2}, {"amt": 3}, {"amt": 4}, {"amt": 5}]
    seed = C.seed_contracts(rows, truncated=False)
    eff = C.effective_contracts(None, seed)
    assert C.check_contracts(eff, rows) is None
    half_null = [{"amt": 1}, {"amt": None}, {"amt": None}, {"amt": None}, {"amt": 5}]
    assert C.check_contracts(eff, half_null) is not None   # 3/5 null > 0.2


def test_scalar_field_degrading_to_list_or_dict_trips() -> None:
    # REGRESSION: a scalar field that becomes a list/dict is a same-shape (dict keys unchanged) wrong value —
    # the shape gate is keys-only, so the VALUE contract's type predicate must catch it, not iterate the list.
    eff = C.effective_contracts(None, C.seed_contracts({"price": 129}))
    assert C.check_contracts(eff, {"price": []}) is not None       # collapsed to an empty list
    assert C.check_contracts(eff, {"price": [1, 2]}) is not None    # became a list of numbers
    assert C.check_contracts(eff, {"price": {"x": 1}}) is not None  # became a dict
    assert C.check_contracts(eff, {"price": 130}) is None           # a real scalar still passes


def test_list_of_objects_with_a_nonobject_item_trips() -> None:
    # REGRESSION: one stray non-dict item (a footer / "Load more") must NOT silently disable every per-key
    # contract for the whole list (the shape gate maps a heterogeneous list to "mixed" and can't disprove it).
    eff = C.effective_contracts(None, C.seed_contracts([{"price": i} for i in range(1, 11)]))
    assert C.check_contracts(eff, [{"price": 0}] * 9 + ["Load more"]) is not None
    assert C.check_contracts(eff, [{"price": None}] * 9 + ["x"]) is not None
    assert C.check_contracts(eff, [{"price": i} for i in range(1, 11)]) is None


# ============================ flows.py wiring (browser fixture) ============================

async def _learn_read(cache, base, *, data, name="answer"):
    """Learn + approve a READ flow whose extraction returns `data` (seeds a value contract)."""
    spec = FlowSpec(name=name, start_url=f"{base}/page1.html", goal="open the answer page",
                    extract="the answer number", headless=True)
    res = await learn(spec, provider=_ClickFirstLink(), router=_extract_router(data), cache=cache)
    assert res.cached, res.note
    approve(spec, cache=cache)
    return spec


async def test_wrong_value_quarantines_and_refuses_future_runs(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        spec = await _learn_read(cache, base, data=129)          # seeds positive/number/non-null
        key = flow_key(spec.goal, spec.start_url, spec.scope)

        with pytest.raises(FlowQuarantineError) as ei:           # a wrong value fails loud
            await replay(spec, router=_extract_router(0), cache=cache)
        assert ei.value.code == "quarantined"
        assert health(spec, cache=cache).status == "quarantined"
        assert _load_meta(cache, key).quarantine is not None     # PERSISTED

        # every FUTURE run refuses 0-LLM at pre-flight — even a would-be-GOOD value — until release
        with pytest.raises(FlowQuarantineError) as ei2:
            await replay(spec, router=_extract_router(129), cache=cache)
        assert "release" in str(ei2.value).lower()               # the pre-flight refusal, not a fresh replay
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_release_clears_and_rearms(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        spec = await _learn_read(cache, base, data=129)
        with pytest.raises(FlowQuarantineError):
            await replay(spec, router=_extract_router(0), cache=cache)

        release(spec, cache=cache)
        assert await replay(spec, router=_extract_router(130), cache=cache) == 130   # good value flows again
        h = health(spec, cache=cache)
        assert h.status == "healthy" and h.consecutive_failures == 0

        # release RE-ARMS the same contract — a still-wrong value re-quarantines (no silent habituation)
        with pytest.raises(FlowQuarantineError):
            await replay(spec, router=_extract_router(-1), cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_no_raw_values_persisted_in_meta(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    _SENTINEL = "SENSITIVE-LEAK-7f3a"
    try:
        spec = await _learn_read(cache, base, data="2026-07-15")     # seeds an ISO-date pattern
        with pytest.raises(FlowQuarantineError):
            await replay(spec, router=_extract_router(_SENTINEL), cache=cache)  # same shape (str), bad format
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        meta_text = _meta_path(cache, key).read_text(encoding="utf-8")
        assert _SENTINEL not in meta_text, "a raw extracted value leaked into the meta sidecar"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_contract_change_requires_reapproval(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        spec = await _learn_read(cache, base, data=129)              # approved with no human overlay
        spec.contracts = {"": {"positive": False}}                   # a LOOSENED guarantee, not re-approved
        with pytest.raises(FlowReplayError) as ei:
            await replay(spec, router=_extract_router(0), cache=cache)
        assert not isinstance(ei.value, FlowQuarantineError)         # a config re-bless, not a data quarantine
        assert "re-approve" in str(ei.value)

        approve(spec, cache=cache)                                   # re-bless the relaxed contract
        assert await replay(spec, router=_extract_router(0), cache=cache) == 0   # 0 now passes (positive off)
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_relearn_reseeds_contracts(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        spec = await _learn_read(cache, base, data=129)
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        assert _load_meta(cache, key).contracts[""]["positive"] is True
        # a re-learn on a page whose value is now a plain string re-derives the seed (no stale number contract)
        await learn(spec, provider=_ClickFirstLink(), router=_extract_router("hello"), cache=cache)
        assert _load_meta(cache, key).contracts[""]["type"] == "string"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_write_flow_is_not_contract_checked(tmp_path, monkeypatch) -> None:
    # A write flow's meta.contracts stays None (the write rail is never disturbed by value contracts).
    from test_run_batch import _CheckoutSite, _record_write_flow

    monkeypatch.chdir(tmp_path)
    cache = FlowCache()
    site = _CheckoutSite()
    httpd, base = site.serve()
    try:
        spec = await _record_write_flow(site, base, cache)
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        assert _load_meta(cache, key).contracts is None
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_mcp_quarantined_read_returns_typed_error(tmp_path, monkeypatch) -> None:
    from ultracua.mcpserver import call_flow_tool

    monkeypatch.chdir(tmp_path)
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache()
    try:
        spec = await _learn_read(cache, base, data=129, name="lookup")
        flows.save_spec(spec)
        with pytest.raises(FlowQuarantineError):                      # quarantine it
            await replay(spec, router=_extract_router(0), cache=cache)
        out = await call_flow_tool("lookup", cache)                  # a read tool call on a quarantined flow
        assert not out.ok and out.code == "quarantined" and out.retryable is False
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_dropping_the_overlay_requires_reapproval(tmp_path: Path) -> None:
    # REGRESSION: dropping the whole human overlay after approval LOOSENS the guarantee and must re-approve
    # (the hash guard must not short-circuit on `spec.contracts` being falsy).
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    try:
        spec = FlowSpec(name="answer", start_url=f"{base}/page1.html", goal="open the answer page",
                        extract="the answer number", headless=True, contracts={"": {"min": 1}})
        assert (await learn(spec, provider=_ClickFirstLink(), router=_extract_router(129), cache=cache)).cached
        approve(spec, cache=cache)                       # stamps contracts_hash over the {min:1} overlay
        spec.contracts = None                            # DROP it -> a weakened guarantee, not re-approved
        with pytest.raises(FlowReplayError) as ei:
            await replay(spec, router=_extract_router(129), cache=cache)
        assert not isinstance(ei.value, FlowQuarantineError) and "re-approve" in str(ei.value)
        approve(spec, cache=cache)                        # re-bless
        assert await replay(spec, router=_extract_router(129), cache=cache) == 129
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_quarantine_on_auth_refresh_retry_persists(tmp_path: Path, monkeypatch) -> None:
    # REGRESSION (blocking): a value violation detected on the POST-auth-refresh attempt must PERSIST the
    # quarantine — else future runs replay the known-wrong flow 0-LLM instead of refusing.
    from ultracua.flows import LoginSpec, refresh_auth

    monkeypatch.setenv("TEST_USER", "alice")
    monkeypatch.setenv("TEST_PASS", "secret")
    httpd, base = _serve_auth()
    ss = tmp_path / "state.json"
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="auth-q", start_url=f"{base}/home", goal="open the answer page",
                    extract="the answer number", storage_state=str(ss), headless=True,
                    login=LoginSpec(url=f"{base}/login", username_env="TEST_USER", password_env="TEST_PASS"))
    try:
        await refresh_auth(spec)                                    # log in -> save cookies
        assert (await learn(spec, provider=_ClickFirstLink(), router=_extract_router(129), cache=cache)).data == 129
        approve(spec, cache=cache)                                  # seeds positive on 129
        ss.write_text('{"cookies": [], "origins": []}', encoding="utf-8")   # expire -> attempt 1 drifts
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        # attempt 1 drifts (logged out); auth-refresh re-logins; attempt 2 extracts 0 -> value violation
        with pytest.raises(FlowQuarantineError):
            await replay(spec, router=_extract_router(0, 0), cache=cache)
        assert _load_meta(cache, key).quarantine is not None       # PERSISTED (the fix)
        assert health(spec, cache=cache).status == "quarantined"
    finally:
        httpd.shutdown()
        httpd.server_close()
