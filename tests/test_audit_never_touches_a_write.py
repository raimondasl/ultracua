"""The H9 audit's stated guarantee is "never captured, never judged" for a write flow. Both halves are
false (R4.10, plus what checking it turned up).

`audit_flows`' docstring promises the judge cannot "approve, clear, un-fail, rebaseline, edit contracts,
or touch a write flow — those capabilities are never constructed". The layer is LLM-driven and therefore
non-deterministic, so that fence is the whole reason it is allowed near a production flow at all.

TWO SEPARATE HOLES, found by asking the same question at two sites:

  1. THE JUDGE'S GATE ASKS THE WRONG QUESTION (R4.10 as filed). `flows.py` skips on
     `spec.mutate is not None` — the DECLARATION. An UNDECLARED write (`spec.mutate is None`, a cached
     step `mutating=True`, which is what the wire promotion produces when a re-learned read starts
     POSTing through GraphQL) sails past it and is judged. In `enforce` mode an LLM finding can then
     quarantine it. `is_write_flow` exists precisely to answer "does this write" and this call site never
     got it — R3.5's fifth-transcription shape, one surface over.

  2. THE CAPTURE HAS NO WRITE GATE AT ALL. `audit.capture`'s own docstring says "the CALLER gates opt-in
     / write-flow / deterministic-gates-passed"; the caller checks `mode == "replay" and spec.audit` and
     nothing else. So the artifact — up to 3000 chars of the POST-COMMIT page plus the extracted data —
     is written to `<cache>/audit/<key>/` for a write flow, declared or not. The `0o700` on that
     directory is `if os.name == "posix"`, so on Windows it lands at the default ACL. `flow audit` then
     prints "write flow (never captured, never judged)" for that flow: the second clause is true and the
     first is false, and the first is the one that would stop an operator looking.

     A mechanism that documents a gate as its caller's responsibility, and a caller that does not
     implement it, is this project's signature pattern stated in its own source.

Ordering note: fixing (1) means calling `cache.get(key)` inside the candidate loop, which raises
`CacheUnreadableError` — an escape there discarded every flow already judged until S7a added the per-flow
guard (R4.14). That is why R4.14 was this finding's precondition and why it could not land earlier.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_contracts import _learn_read
from test_flows import _ClickFirstLink, _extract_router, _serve, _write_fixture  # noqa: F401

from ultracua import audit as A
from ultracua import flows
from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowSpec, MutateSpec, approve, audit_flows, replay
from ultracua.llm.base import Router, Tier
from ultracua.llm.mock import MockClient

_CREEP = [95, 92, 90, 88, 86, 84, 82, 80, 78, 76, 74, 72, 70, 68, 66, 64, 62, 60, 58, 56]
_ANCHOR = 129.0
_FINDING = {"verdict": "suspicious", "trend": "monotonic_drift", "drift_explained": "unexplained",
            "confidence": 0.9, "reason_code": "slow_drift", "evidence_quote": "Unit price 56.00",
            "injection_suspected": False}


def _judge_router(*payloads):
    mc = MockClient(actions=[dict(p) for p in payloads], tool_name="report_findings")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m")), mc


def _promote_to_undeclared_write(cache: FlowCache, key: str) -> None:
    """What the wire promotion does to a re-learned READ whose button now POSTs: one cached step gains
    `mutating=True` while `spec.mutate` stays None. This is the population both holes are about."""
    cached = cache.get(key)
    assert cached is not None and cached.steps, "premise: the flow must be learned"
    cached.steps[0].mutating = True
    cache.put(cached)


# ==================== 1. the judge's gate (R4.10 as filed) ====================


async def test_an_undeclared_write_is_never_judged(tmp_path: Path, monkeypatch) -> None:
    """The guarantee says a write flow is never judged. An undeclared one is — and in `enforce` mode an
    LLM's finding can quarantine it, which is the one thing this layer promises it cannot do to a write.

    The direction of the resulting harm is conservative (a quarantine refuses future runs), so this is
    not a silent wrong write. It is a trust-boundary violation: a non-deterministic judge reaching a
    population the design excluded, on the flows where nobody knows there is a write at all.
    """
    monkeypatch.chdir(tmp_path)
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache()
    try:
        spec = await _learn_read(cache, base, data=129, name="undeclared")
        spec.audit = "enforce"
        flows.save_spec(spec)
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        _promote_to_undeclared_write(cache, key)
        assert flows.is_write_flow(spec, cache.get(key)) is True, "premise: this IS a write flow"
        assert spec.mutate is None, "premise: and it is UNDECLARED"

        A.capture(cache, key, goal=spec.goal, extract=spec.extract, data=56.0,
                  page_text="Unit price 56.00", rings={"": list(_CREEP)}, anchors={"": _ANCHOR},
                  signals=["anchor_drift"], report_mode="replay", healed=0, truncated=False)

        router, _mc = _judge_router(_FINDING)
        run = await audit_flows(names=["undeclared"], cache=cache, router=router)

        assert run.judged == 0, (
            f"an undeclared write was judged ({run.judged} artifact(s), {run.calls} LLM call(s)) — the "
            f"layer's guarantee is 'never judged' for a write flow")
        assert run.quarantined == 0, "and an LLM finding must never quarantine a write flow"
        assert any("write flow" in why for _n, why in run.skipped), "it must be skipped AS a write flow"
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== 2. the capture that has no gate at all ====================


async def test_a_write_flow_never_has_an_artifact_written_to_disk(
    tmp_path: Path, monkeypatch,
) -> None:
    """The half that is not in the filing. The judge gate is the SECOND line of defence; the first is
    supposed to be that a write flow's evidence is never persisted at all — which is what "never
    captured" claims and what `audit.capture`'s docstring delegates to its caller.

    What lands on disk is the post-commit page: the confirmation, its reference, whatever the site echoes
    back. Env-resolved secrets are scrubbed by `_redact`, but a shipping address is not a secret VALUE,
    it is page content.
    """
    monkeypatch.chdir(tmp_path)
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache()
    try:
        spec = await _learn_read(cache, base, data=129, name="cap")
        spec.audit = "advisory"
        flows.save_spec(spec)
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        _promote_to_undeclared_write(cache, key)
        # RE-APPROVE after the promotion, or the steps-hash gate refuses the replay and this test measures
        # nothing — the first draft did exactly that and failed with `StaleApprovalError`. In the real
        # sequence the promotion happens during LEARN, so the human approves the recipe with the mutating
        # step already in it; approving here reproduces that order rather than working around the gate.
        approve(spec, cache=cache)
        flows._update_meta(cache, key, lambda m: setattr(m, "audit_due", True), on_unreadable="raise")

        await replay(spec, router=_extract_router(129), cache=cache)

        assert A.load_artifacts(cache, key) == [], (
            "a write flow's evidence artifact was written to disk — `flow audit` reports 'never "
            "captured' for exactly this flow")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_declared_write_flow_is_never_captured_either(tmp_path: Path, monkeypatch) -> None:
    """The other half of the capture matrix. The UNDECLARED case above is the one the filing is about,
    but the guarantee is stated for write flows generally — and a declared write was captured too, since
    the gate that would have stopped it did not exist in either form. Arming is refused now (below), so
    reaching this state takes a spec edited by hand or a flow that gained its declaration after the fact;
    the gate must hold anyway, because the arming refusal is not the load-bearing one.
    """
    monkeypatch.chdir(tmp_path)
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache()
    try:
        spec = await _learn_read(cache, base, data=129, name="declared")
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        spec.audit = "advisory"
        spec.mutate = MutateSpec(confirm_text_contains="Paid")   # declared AFTER the fact, by hand
        flows.save_spec(spec)
        flows._update_meta(cache, key, lambda m: setattr(m, "audit_due", True), on_unreadable="raise")

        try:
            await replay(spec, router=_extract_router(129), cache=cache)
        except Exception:
            pass          # a declared write with no confirm on the page fails loud; the CAPTURE is the point

        assert A.load_artifacts(cache, key) == [], "a declared write flow's artifact reached disk"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_read_flow_is_still_captured_and_judged(tmp_path: Path, monkeypatch) -> None:
    """THE MUST-REMAIN-USABLE CLAUSE, and without it every assertion above is satisfied by disabling the
    H9 layer outright — which is a regression, not a fix, and is the exact shape that shipped once as the
    0.74.0 over-refusal. The judge exists to catch a flow that returns a plausible WRONG value; a read
    flow must still be captured on a drift signal and still be judged.
    """
    monkeypatch.chdir(tmp_path)
    _write_fixture(tmp_path)
    httpd, base = _serve(tmp_path)
    cache = FlowCache()
    try:
        spec = await _learn_read(cache, base, data=129, name="read")
        spec.audit = "advisory"
        flows.save_spec(spec)
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        assert flows.is_write_flow(spec, cache.get(key)) is False, "premise: an ordinary read"
        flows._update_meta(cache, key, lambda m: setattr(m, "audit_due", True), on_unreadable="raise")

        await replay(spec, router=_extract_router(129), cache=cache)
        assert A.load_artifacts(cache, key), "a READ flow must still be captured — the layer still works"

        router, _mc = _judge_router(_FINDING)
        run = await audit_flows(names=["read"], cache=cache, router=router)
        assert run.judged == 1, f"a READ flow must still be judged (skipped: {run.skipped})"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_arming_the_audit_on_a_declared_write_flow_is_refused(tmp_path: Path, monkeypatch) -> None:
    """The third surface, and the cheapest place to stop all of it: nothing prevents an operator arming
    the layer on a flow it must never touch. `flow audit --set-mode enforce` even prints "a corroborated
    finding can now QUARANTINE this flow" — about a flow the guarantee excludes.

    A refusal here is not sufficient on its own (the undeclared population above has no declaration to
    check at arming time, and a read can BECOME a write after a re-learn), which is why the capture gate
    is the load-bearing one. It is still the first thing an operator should hit.
    """
    from ultracua import cli

    monkeypatch.chdir(tmp_path)
    spec = FlowSpec(name="pay", start_url="http://127.0.0.1:1/", goal="pay the invoice",
                    mutate=MutateSpec(confirm_text_contains="Paid"))
    flows.save_spec(spec)
    args = type("A", (), {"cmd": "audit", "name": "pay", "set_mode": "enforce", "list": False,
                          "show": False, "max_calls": 0, "dry_run": False, "keep": False,
                          "provider": "anthropic", "verbose": False, "allow_empty": False,
                          "purge": False})()

    with pytest.raises(SystemExit) as ei:
        cli._flow_audit(args)
    assert "write" in str(ei.value).lower(), (
        "arming the H9 judge on a declared write flow must be refused with a reason")
    assert flows.load_spec("pay").audit is None, "and it must not have been persisted"
