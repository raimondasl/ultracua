"""The steps-bound approval gate (0.60.0) — `approved` now binds to the RECIPE, not just the flow's name.

Before this, `approved` was a sticky bit on the meta sidecar while the STEPS lived in a separate file that
several code paths rewrite: a self-heal or suffix-replan under `on_drift="relearn"`, a `flow record` over an
existing flow, a re-learn, or a hand-edited cache file. So an approved flow could be re-authored underneath
its own approval, and replay would happily run steps no human ever read — at a WRITE, an unreviewed action
against an unreviewed target. That is the human sign-off this project advertises, not actually binding.

`approve()` now stamps `cache.steps_hash(cached_flow)` and pre-flight refuses (`stale_approval`, pre-browser,
0-LLM) when the digest no longer matches — or when it is absent, which is how a flow approved by an older
version presents. The absent case REFUSES rather than back-filling: back-filling would stamp whatever the
steps are now, blessing exactly the drifted recipe the gate exists to catch.

The digest itself (its basis, additive safety, per-field sensitivity) is tested in `test_cache.py`. This file
tests the GATE and the surfaces that report it. All key-less; no browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ultracua.cache import CachedFlow, CachedStep, FlowCache, StepConfirm, flow_key, steps_hash
from ultracua.flows import (
    FlowReplayError,
    FlowSpec,
    MutateSpec,
    StaleApprovalError,
    _load_meta,
    _preflight_row,
    _update_meta,
    approve,
    health,
    save_spec,
    unapprove,
)
from ultracua.locators import LocatorSpec


@pytest.fixture(autouse=True)
def _no_inherited_home(monkeypatch, tmp_path):
    monkeypatch.delenv("ULTRACUA_HOME", raising=False)
    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))


def _read_flow(key: str, *, intent: str = "click the total", css: str = "#total") -> CachedFlow:
    return CachedFlow(
        key=key, goal="read the order total", start_url="https://shop.example.com/orders",
        created_ts=1_700_000_000.0,
        steps=[CachedStep(intent=intent, action="click",
                          locator=LocatorSpec(role="link", name="Orders", tag="a", css=css),
                          precond_fingerprint="fp")],
    )


def _write_flow(key: str) -> CachedFlow:
    return CachedFlow(
        key=key, goal="place the order", start_url="https://shop.example.com/cart",
        created_ts=1_700_000_000.0,
        steps=[CachedStep(intent="click Place order", action="click",
                          locator=LocatorSpec(role="button", name="Place order", tag="button"),
                          precond_fingerprint="fp", mutating=True,
                          confirm=StepConfirm(confirm_text_contains="Order placed",
                                              expects_intent="Place order"))],
    )


def _seeded(tmp_path: Path, flow_factory=_read_flow, **spec_kw) -> tuple[FlowSpec, FlowCache, str, CachedFlow]:
    """A saved spec + a cached flow, ready to approve. No browser, no LLM.

    Deliberately uses the DEFAULT cache root (resolved from `$ULTRACUA_HOME`, which the autouse fixture
    points at this test's tmp dir) rather than an explicit root, so the CLI/MCP surfaces — which construct
    their own `FlowCache()` — see the same store these tests write to."""
    cache = FlowCache()
    spec = FlowSpec(name=spec_kw.pop("name", "orders"), start_url="https://shop.example.com/orders",
                    goal="read the order total", extract="the total", headless=True, **spec_kw)
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    flow = flow_factory(key)
    # keep the spec's identity and the cached flow's identity consistent
    flow = flow.model_copy(update={"goal": spec.goal, "start_url": spec.start_url})
    cache.put(flow)
    save_spec(spec)
    return spec, cache, key, flow


# ============================ approve() stamps the recipe ============================

def test_approve_stamps_the_steps_hash(tmp_path: Path) -> None:
    spec, cache, key, flow = _seeded(tmp_path)
    assert _load_meta(cache, key).steps_hash is None      # nothing stamped before approval
    approve(spec, cache=cache)
    assert _load_meta(cache, key).steps_hash == steps_hash(flow)


def test_an_approved_unchanged_flow_passes_preflight(tmp_path: Path) -> None:
    """The gate must be INERT on the happy path — an approved flow whose steps are untouched replays."""
    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    _preflight_row(spec, None, meta=_load_meta(cache, key), cached_flow=flow, require_approved=True)


# ============================ the gate ============================

def test_re_authored_steps_refuse_under_a_stale_approval(tmp_path: Path) -> None:
    """THE POINT OF THE SLICE. A heal retargets a locator and writes the flow back; the approval bit is
    untouched. Replay must refuse rather than run a target no human reviewed."""
    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    healed = flow.model_copy(update={
        "steps": [flow.steps[0].model_copy(
            update={"locator": flow.steps[0].locator.model_copy(update={"css": "#total-v2"})})]})
    cache.put(healed)                                     # exactly what `flow.py`'s write-back does
    with pytest.raises(StaleApprovalError) as ei:
        _preflight_row(spec, None, meta=_load_meta(cache, key), cached_flow=healed, require_approved=True)
    assert "steps changed since approval" in str(ei.value)
    assert ei.value.code == "stale_approval" and ei.value.retryable is False
    assert isinstance(ei.value, FlowReplayError)           # existing handlers keep catching it


def test_an_injected_extra_step_refuses(tmp_path: Path) -> None:
    """A re-author that APPENDS an action (the shape a suffix-replan produces) is caught too — the digest
    covers step count and order, not just each step's fields."""
    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    extra = CachedStep(intent="click Export all", action="click",
                       locator=LocatorSpec(role="button", name="Export all", tag="button"),
                       precond_fingerprint="fp", mutating=True)
    grown = flow.model_copy(update={"steps": [*flow.steps, extra]})
    cache.put(grown)
    with pytest.raises(StaleApprovalError):
        _preflight_row(spec, None, meta=_load_meta(cache, key), cached_flow=grown, require_approved=True)


def test_a_write_flow_is_gated_even_without_require_approved(tmp_path: Path) -> None:
    """A write is approval-gated implicitly (`is_mutate`), so it must inherit the recipe binding on the same
    implicit path — a caller that never passes `require_approved` must not get an unreviewed write."""
    spec, cache, key, flow = _seeded(
        tmp_path, flow_factory=_write_flow, name="place-order",
        mutate=MutateSpec(confirm_text_contains="Order placed"))
    approve(spec, cache=cache)
    swapped = flow.model_copy(update={
        "steps": [flow.steps[0].model_copy(
            update={"locator": flow.steps[0].locator.model_copy(update={"name": "Delete account"})})]})
    cache.put(swapped)
    with pytest.raises(StaleApprovalError):
        _preflight_row(spec, None, meta=_load_meta(cache, key), cached_flow=swapped)  # note: no flag


def test_the_gate_is_silent_on_an_unapproved_flow(tmp_path: Path) -> None:
    """An unapproved flow is where self-healing is still allowed to happen, by design — the recipe binding
    exists to protect an APPROVAL, so with no approval there is nothing to invalidate."""
    spec, cache, key, flow = _seeded(tmp_path)
    edited = flow.model_copy(update={"steps": [flow.steps[0].model_copy(update={"intent": "reworded"})]})
    cache.put(edited)
    _preflight_row(spec, None, meta=_load_meta(cache, key), cached_flow=edited)   # no raise


def test_re_approving_blesses_the_new_recipe(tmp_path: Path) -> None:
    """The human path out: read the new steps, approve, run. (One flow at a time, by name.)"""
    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    edited = flow.model_copy(update={"steps": [flow.steps[0].model_copy(update={"intent": "reworded"})]})
    cache.put(edited)
    with pytest.raises(StaleApprovalError):
        _preflight_row(spec, None, meta=_load_meta(cache, key), cached_flow=edited, require_approved=True)
    approve(spec, cache=cache)                            # a human re-read the recipe and blessed it
    _preflight_row(spec, None, meta=_load_meta(cache, key), cached_flow=edited, require_approved=True)


def test_unapprove_then_reapprove_rebinds(tmp_path: Path) -> None:
    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    unapprove(spec, cache=cache)
    edited = flow.model_copy(update={"steps": [flow.steps[0].model_copy(update={"intent": "reworded"})]})
    cache.put(edited)
    _preflight_row(spec, None, meta=_load_meta(cache, key), cached_flow=edited)   # unapproved -> inert
    approve(spec, cache=cache)
    assert _load_meta(cache, key).steps_hash == steps_hash(edited)


# ============================ migration ============================

def test_an_approval_from_an_older_version_refuses_and_is_never_backfilled(tmp_path: Path) -> None:
    """MIGRATION. A flow approved before this binding existed has `steps_hash=None`. It must REFUSE with an
    upgrade message — and pre-flight must not quietly stamp the current steps, which would bless whatever
    the flow has become since (the exact failure the gate exists to catch)."""
    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    _update_meta(cache, key, lambda m: setattr(m, "steps_hash", None))   # simulate the old on-disk state
    for _ in range(2):                                                   # twice: proves no back-fill happened
        with pytest.raises(StaleApprovalError) as ei:
            _preflight_row(spec, None, meta=_load_meta(cache, key), cached_flow=flow, require_approved=True)
        assert "older version" in str(ei.value) and "re-approve" in str(ei.value)
        assert _load_meta(cache, key).steps_hash is None


def test_bookkeeping_churn_does_not_invalidate_an_approval(tmp_path: Path) -> None:
    """A re-record that produces IDENTICAL steps must not force a re-approval — only `created_ts` moved.
    (Approval fatigue is its own safety failure: an operator who re-approves daily stops reading.)"""
    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    re_recorded = flow.model_copy(update={"created_ts": flow.created_ts + 86_400})
    cache.put(re_recorded)
    _preflight_row(spec, None, meta=_load_meta(cache, key), cached_flow=re_recorded, require_approved=True)


def test_an_uncomputable_digest_refuses_inside_the_taxonomy(tmp_path: Path, monkeypatch) -> None:
    """A digest we cannot COMPUTE is not a digest that matches. It must refuse as a `FlowReplayError`
    subclass — not escape as a bare TypeError that a caller's `except FlowReplayError` would miss, and not
    be treated as "no change". (Reachable via a hand-written cache file whose `args` holds something
    `json.dumps(sort_keys=True)` rejects.)"""
    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)

    def _boom(_flow):
        raise TypeError("'<' not supported between instances of 'str' and 'int'")

    monkeypatch.setattr("ultracua.flows.steps_hash", _boom)
    with pytest.raises(StaleApprovalError) as ei:
        _preflight_row(spec, None, meta=_load_meta(cache, key), cached_flow=flow, require_approved=True)
    assert "could not verify" in str(ei.value)
    # ...and the fleet view reports it rather than tracebacking (one corrupt flow must not kill `flow status`
    # or the MCP tools/list loop).
    assert health(spec, cache=cache).approval_stale is True


# ============================ the reporting surfaces ============================

def test_health_reports_a_stale_approval(tmp_path: Path) -> None:
    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    assert health(spec, cache=cache).approval_stale is False
    cache.put(flow.model_copy(update={"steps": [flow.steps[0].model_copy(update={"intent": "x"})]}))
    assert health(spec, cache=cache).approval_stale is True
    # ...and the older-version case reports the same way (it needs the same human action)
    approve(spec, cache=cache)
    _update_meta(cache, key, lambda m: setattr(m, "steps_hash", None))
    assert health(spec, cache=cache).approval_stale is True


def test_health_does_not_flag_an_unapproved_or_unlearned_flow(tmp_path: Path) -> None:
    """`approval_stale` must mean "this approval is not binding", not "this flow has no approval" — an
    operator staring at a fleet needs those to look different."""
    spec, cache, key, flow = _seeded(tmp_path)
    assert health(spec, cache=cache).approval_stale is False        # learned, never approved
    cache.delete(key)
    assert health(spec, cache=cache).approval_stale is False        # not learned at all


def test_mcp_unlists_a_stale_flow_rather_than_serving_a_tool_that_always_fails(tmp_path: Path) -> None:
    from ultracua.mcpserver import list_flow_tools

    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    assert [t.spec_name for t in list_flow_tools(cache)] == [spec.name]
    cache.put(flow.model_copy(update={"steps": [flow.steps[0].model_copy(update={"intent": "x"})]}))
    assert list_flow_tools(cache) == []


async def test_mcp_call_of_an_unlisted_stale_flow_says_why(tmp_path: Path) -> None:
    from ultracua.mcpserver import call_flow_tool, list_flow_tools

    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    tool = list_flow_tools(cache)[0].name
    cache.put(flow.model_copy(update={"steps": [flow.steps[0].model_copy(update={"intent": "x"})]}))
    out = await call_flow_tool(tool, cache)
    assert out.ok is False and out.code == "unknown_tool"
    assert "reviewed steps" in out.message


def test_cli_status_prints_the_stale_approval_line(tmp_path: Path, capsys) -> None:
    from ultracua import cli

    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    cache.put(flow.model_copy(update={"steps": [flow.steps[0].model_copy(update={"intent": "x"})]}))
    cli._flow_main(["status", "--name", spec.name])
    out = capsys.readouterr().out
    assert "APPROVAL STALE" in out and "flow approve" in out


# ============================ `flow approve --all` ============================

def test_cli_approve_all_blesses_reads_and_refuses_writes(tmp_path: Path, capsys) -> None:
    """The bulk migration path. It must print each recipe, approve the reads, and REFUSE to bulk-bless a
    write — a write's steps get read one at a time, by a human."""
    from ultracua import cli

    read_spec, cache, _, _ = _seeded(tmp_path)
    w_cache = FlowCache()
    w_spec = FlowSpec(name="place-order", start_url="https://shop.example.com/cart", goal="place the order",
                      headless=True, mutate=MutateSpec(confirm_text_contains="Order placed"))
    w_key = flow_key(w_spec.goal, w_spec.start_url, w_spec.scope)
    w_cache.put(_write_flow(w_key))
    save_spec(w_spec)

    cli._flow_main(["approve", "--all", "--yes"])
    out = capsys.readouterr().out
    assert "click the total" in out                       # the read's recipe was PRINTED, not just counted
    assert "SKIPPING place-order" in out and "WRITE flow" in out
    assert health(read_spec, cache=cache).approved is True
    assert _load_meta(w_cache, w_key).approved is False   # the write is untouched


def test_cli_approve_all_refuses_a_flow_with_a_pending_spec_re_approval(tmp_path: Path, capsys) -> None:
    """`approve()` stamps all THREE bindings at once (recipe, slot schema, contract overlay). So bulk-approving
    a flow whose slot domain or contract overlay ALSO drifted would silently clear a trust gate this printout
    never showed — a widened payee enum blessed as a side effect of a recipe migration. Refuse it here."""
    from ultracua import cli
    from ultracua.flows import SlotSpec

    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    spec.slots = {"payee": SlotSpec(type="string")}      # widened AFTER approval, never re-approved
    save_spec(spec)
    with pytest.raises(SystemExit):
        cli._flow_main(["approve", "--all", "--yes"])
    out = capsys.readouterr().out
    assert "SLOT SCHEMA also changed" in out
    assert _load_meta(cache, key).slots_hash is None      # NOT blessed as a side effect


def test_cli_approve_all_shows_the_typed_text_and_marks_a_first_approval(tmp_path: Path, capsys) -> None:
    """A review print that omits the typed text is not a review of what the digest binds. And a never-approved
    flow must be labelled — an operator running a *migration* should see it is granting new trust, not
    re-granting old trust."""
    from ultracua import cli

    spec, cache, key, flow = _seeded(tmp_path)
    typed = flow.model_copy(update={"steps": [flow.steps[0].model_copy(
        update={"action": "type", "text": "acme-corp", "slot": "payee"})]})
    cache.put(typed)
    cli._flow_main(["approve", "--all", "--yes"])
    out = capsys.readouterr().out
    assert "'acme-corp'" in out and "[slot payee]" in out
    assert "FIRST approval" in out


def test_cli_approve_all_never_echoes_a_secret_slots_literal(tmp_path: Path, capsys) -> None:
    from ultracua import cli
    from ultracua.flows import SlotSpec

    spec, cache, key, flow = _seeded(tmp_path)
    spec.slots = {"token": SlotSpec(type="string", secret=True, secret_env="DEMO_TOKEN")}
    save_spec(spec)
    leaky = flow.model_copy(update={"steps": [flow.steps[0].model_copy(
        update={"action": "type", "text": "hunter2-not-a-real-secret", "slot": "token"})]})
    cache.put(leaky)
    cli._flow_main(["approve", "--all", "--yes"])
    out = capsys.readouterr().out
    assert "hunter2" not in out
    assert "[slot token SECRET]" in out and 'text="***"' in out


def test_cli_approve_all_refuses_non_interactively_without_yes(tmp_path: Path, monkeypatch) -> None:
    from ultracua import cli

    _seeded(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    with pytest.raises(SystemExit) as ei:
        cli._flow_main(["approve", "--all"])
    assert "without a human" in str(ei.value.code)


def test_cli_approve_by_name_says_when_it_also_re_blessed_the_spec(tmp_path: Path, capsys) -> None:
    """One `approve` stamps three bindings. If it re-blesses a widened slot domain as a side effect of
    blessing a recipe, the operator must be told — otherwise the command meant to enforce the gate is how
    the gate gets defeated."""
    from ultracua import cli
    from ultracua.flows import SlotSpec

    spec, cache, key, flow = _seeded(tmp_path)
    approve(spec, cache=cache)
    cli._flow_main(["approve", "--name", spec.name])
    assert "also re-approved" not in capsys.readouterr().out       # nothing changed -> stay quiet
    spec.slots = {"payee": SlotSpec(type="string")}
    save_spec(spec)
    cli._flow_main(["approve", "--name", spec.name])
    out = capsys.readouterr().out
    assert "also re-approved" in out and "SLOT SCHEMA" in out


def test_cli_approve_requires_a_name_or_all(tmp_path: Path) -> None:
    from ultracua import cli

    with pytest.raises(SystemExit) as ei:
        cli._flow_main(["approve"])
    assert "--name" in str(ei.value.code)
