"""A human may CORRECT a `mutating` mark — but only where the mark was a guess, never where it is evidence.

The provenance field (0.92.0) made the four signals behind one bit distinguishable. This is the verb that
uses it, and it is the first change in this whole arc that can make write safety WORSE rather than more
conservative: everything before it either gated more or refused more. So the rules are written here, as
tests, before the code exists.

WHY IT IS NEEDED. `classify_mutation`'s keyword fallback false-fires on 28% of ordinary read controls
("Payment history" -> `pay`, "Show borders" -> `order`), and the wire promotion files 12/12 GraphQL-style
read POSTs as writes (R4.27). Those flows lose self-heal, suffix-replan, the auth-refresh retry, MCP
exposure and `run_all` inclusion — and D0 is blocked indefinitely precisely because no automated rule can
tell that population from real commits. A human can. This is the sensor-class change D5 demands: not a
better inference, a different kind of answer.

THE ASYMMETRY IS THE WHOLE DESIGN.
  * PROMOTING a step to writing is always allowed — it is strictly more conservative, and costs at worst
    an unused Idempotency-Key on a step that never writes.
  * DEMOTING is allowed ONLY where every recorded source is a GUESS (`keyword`, `caption`). One shred of
    evidence — `form_method`, `wire` — or a safety precaution (`overgate`) or a human's own prior
    declaration (`declared`), and the verb refuses. A human may overrule the classifier's guesswork; a
    human may not overrule a POST that was watched leaving the browser.
  * NO PROVENANCE AT ALL (a flow authored before 0.92.0) refuses too. `None` means "never recorded", not
    "no evidence" — collapsing those is this register's absent-vs-unreadable trap, filed three times.

AND EVERY CHANGE RE-GATES APPROVAL, by construction rather than by remembering: `mutating` is in
`_HASHED_STEP_FIELDS`, so editing it moves the steps digest and replay refuses with `stale_approval`
until a human re-reads the recipe. A demotion is exactly the thing nobody should be able to slip past an
approval granted for the recipe as it was.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key
from ultracua.flows import FlowSpec, MutateSpec, approve, mark_step
from ultracua.locators import LocatorSpec


def _flow(key: str, *steps: CachedStep) -> CachedFlow:
    return CachedFlow(key=key, goal="work the panel", start_url="http://x/", steps=list(steps),
                      created_ts=time.time())


def _step(intent: str, *, mutating: bool, sources) -> CachedStep:
    return CachedStep(intent=intent, action="click", locator=LocatorSpec(role="button", name=intent, tag="button"),
                      mutating=mutating, precond_scope="scope" if mutating else "",
                      mutating_sources=sources)


def _seed(tmp_path: Path, *steps: CachedStep):
    spec = FlowSpec(name="p", goal="work the panel", start_url="http://x/")
    cache = FlowCache(root=tmp_path / "c")
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(_flow(key, *steps))
    return spec, cache, key


# ==================== the permitted direction ====================


def test_a_human_may_demote_a_mark_that_was_only_ever_a_keyword_guess(tmp_path: Path) -> None:
    """R4.27's population: a read control whose NAME tripped the classifier. Nothing observed it write."""
    spec, cache, key = _seed(tmp_path, _step("payment history", mutating=True, sources=["keyword"]))
    mark_step(spec, 0, writes=False, cache=cache)
    step = cache.get(key).steps[0]
    assert step.mutating is False, "the human's verdict must actually take effect"
    assert "human" in (step.mutating_sources or []), (
        f"the human act must be recorded, and the ORIGINAL signals kept — the field documents why the "
        f"step was ever marked. got {step.mutating_sources!r}")
    assert "keyword" in step.mutating_sources, "the superseded guess must remain visible, not be erased"
    assert step.precond_scope, (
        "the precondition must survive a demotion, so a later re-promotion restores a GATED write "
        "rather than a blind one")


def test_promotion_is_allowed_when_the_step_has_a_precondition_to_gate_with(tmp_path: Path) -> None:
    """A human who knows a bland-named control commits may say so, whatever the classifier thought.

    THE FIRST DRAFT OF THIS TEST WAS NAMED `..._is_always_allowed_because_it_is_the_conservative_
    direction` AND ASSERTED A GATELESS WRITE. Promotion is conservative only if the resulting write is
    actually GATED; see the sibling below for the case that is not."""
    # A LEARN-path read step: `flow.py` gives every step the page fingerprint, so there is something
    # for the gate to check. (A RECORDED read step has neither, and is refused — see the sibling.)
    spec, cache, key = _seed(tmp_path, CachedStep(
        intent="continue", action="click",
        locator=LocatorSpec(role="button", name="continue", tag="button"),
        mutating=False, precond_scope="", precond_fingerprint="fp-abc", mutating_sources=None))
    mark_step(spec, 0, writes=True, cache=cache)
    step = cache.get(key).steps[0]
    assert step.mutating is True
    assert step.mutating_sources == ["human"]
    assert step.precond_fingerprint, "a promoted write must keep something for the mutation gate to check"


def test_a_promotion_that_would_create_an_UNGATED_write_is_refused(tmp_path: Path) -> None:
    """The critical this slice shipped in its first draft, and it inverted the design's own claim.

    `recorder._step_from_event` never sets `precond_fingerprint`, and only scopes a step it ALREADY
    considers mutating. So promoting an unscoped RECORDED step caches `mutating=True` with neither a
    scope nor a fingerprint — and `_replay_step`'s gate takes neither branch, so `drifted` stays False
    and the write fires blind under any drift. recorder.py states that invariant in capitals and
    refuses to author it; this verb must not create through the back door what the recorder refuses to
    create through the front.
    """
    spec, cache, key = _seed(tmp_path, CachedStep(
        intent="continue", action="click",
        locator=LocatorSpec(role="button", name="continue", tag="button"),
        mutating=False, precond_scope="", precond_fingerprint="", mutating_sources=None))
    with pytest.raises(ValueError) as ei:
        mark_step(spec, 0, writes=True, cache=cache)
    assert "precondition" in str(ei.value).lower()
    assert "re-record" in str(ei.value).lower(), "a refusal must name a remedy that actually works"
    assert cache.get(key).steps[0].mutating is False, "a refused promotion must change NOTHING on disk"


def test_a_demote_then_promote_round_trip_is_LOSSLESS(tmp_path: Path) -> None:
    """The sequence a human correcting-and-re-correcting will actually perform, and the one that broke.

    The first draft cleared `precond_scope` on demotion, so the round trip returned `mutating=True`
    with an empty gate. Measured in a browser at the time: the mutation gate went from REFUSING a
    drifted write to a no-op, and the order was placed against a form that had changed since a human
    approved it. Each annotation was individually legal; the SEQUENCE was the defect.
    """
    spec, cache, key = _seed(tmp_path, _step("place the order", mutating=True, sources=["keyword"]))
    before = cache.get(key).steps[0].precond_scope
    assert before, "the fixture must start with a real precondition or this proves nothing"
    mark_step(spec, 0, writes=False, cache=cache)
    assert cache.get(key).steps[0].precond_scope == before, (
        "a demotion must not destroy the precondition — it costs nothing on a non-mutating step and it "
        "is what makes the verb reversible")
    mark_step(spec, 0, writes=True, cache=cache)
    step = cache.get(key).steps[0]
    assert step.mutating is True and step.precond_scope == before, (
        f"the round trip must restore the step to a GATED write; got mutating={step.mutating} "
        f"precond_scope={step.precond_scope!r}")


# ==================== the refusals, which are the point ====================


@pytest.mark.parametrize("sources,why", [
    (["wire"], "a non-idempotent request was WATCHED leaving the browser"),
    (["form_method"], "the target's own form declares a non-idempotent method"),
    (["keyword", "wire"], "a guess that the wire then CONFIRMED is still evidence"),
    (["overgate"], "AB-1's precaution — demoting it re-opens a known hole (R4.5)"),
    (["declared"], "the human already declared this flow a write"),
])
def test_a_human_may_not_overrule_evidence(tmp_path: Path, sources, why) -> None:
    """THE guard. A human may overrule the classifier's guesswork; a human may not overrule a POST that
    was observed. Every one of these refuses, and the message must name the evidence being overruled —
    a refusal whose reason is invisible is one an operator learns to work around."""
    spec, cache, key = _seed(tmp_path, _step("place the order", mutating=True, sources=sources))
    with pytest.raises(ValueError) as ei:
        mark_step(spec, 0, writes=False, cache=cache)
    assert any(s in str(ei.value) for s in sources), (
        f"the refusal must name what it is protecting; got {str(ei.value)!r}")
    assert cache.get(key).steps[0].mutating is True, "a refused demotion must change NOTHING on disk"


def test_a_mark_with_no_recorded_provenance_cannot_be_demoted(tmp_path: Path) -> None:
    """A flow authored before 0.92.0. `None` means "never recorded", NOT "no evidence" — and the
    difference is the whole finding behind R3.1/R3.4 and `landed`'s second wrong version. Fail closed:
    re-learn the flow and the provenance appears, which is a remedy that actually works."""
    spec, cache, key = _seed(tmp_path, _step("pay the invoice", mutating=True, sources=None))
    with pytest.raises(ValueError) as ei:
        mark_step(spec, 0, writes=False, cache=cache)
    assert "provenance" in str(ei.value).lower() or "re-learn" in str(ei.value).lower()
    assert cache.get(key).steps[0].mutating is True


def test_demoting_the_last_write_of_a_DECLARED_write_flow_is_refused(tmp_path: Path) -> None:
    """`spec.mutate` is the human's own statement that this flow commits. Demoting its only marked step
    would leave a declared write planning zero Idempotency-Keys — which `_preflight_row` already raises
    `UnkeyedWriteError` for, at replay. Refusing HERE turns a confusing later failure into a clear one."""
    spec = FlowSpec(name="p", goal="work the panel", start_url="http://x/",
                    mutate=MutateSpec(confirm_text_contains="Saved"))
    cache = FlowCache(root=tmp_path / "c")
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(_flow(key, _step("submit", mutating=True, sources=["keyword"])))
    with pytest.raises(ValueError) as ei:
        mark_step(spec, 0, writes=False, cache=cache)
    assert "declared" in str(ei.value).lower() or "mutate" in str(ei.value).lower()
    assert cache.get(key).steps[0].mutating is True


# ==================== approval must not survive the edit ====================


def test_any_annotation_re_gates_approval(tmp_path: Path) -> None:
    """By construction, not by remembering: `mutating` is hashed, so the digest moves and replay refuses
    with `stale_approval` until a human re-reads the recipe. A demotion is precisely the change nobody
    should be able to slip past an approval granted for the recipe as it stood."""
    from ultracua.flows import _load_meta

    spec, cache, key = _seed(tmp_path, _step("payment history", mutating=True, sources=["keyword"]))
    approve(spec, cache=cache)
    before = _load_meta(cache, key).steps_hash
    assert before, "the flow must actually be approved for this test to mean anything"
    mark_step(spec, 0, writes=False, cache=cache)
    from ultracua.cache import steps_hash
    assert steps_hash(cache.get(key)) != before, (
        "the recipe changed and the approval digest did not move — an approved flow would keep running "
        "a recipe no human re-read")


# ==================== ordinary argument hygiene, since this is a write-path verb ====================


def test_an_out_of_range_step_refuses_rather_than_silently_doing_nothing(tmp_path: Path) -> None:
    spec, cache, _key = _seed(tmp_path, _step("go", mutating=True, sources=["keyword"]))
    with pytest.raises(ValueError):
        mark_step(spec, 7, writes=False, cache=cache)


def test_an_unlearned_flow_refuses(tmp_path: Path) -> None:
    spec = FlowSpec(name="nope", goal="nothing", start_url="http://x/")
    with pytest.raises(ValueError):
        mark_step(spec, 0, writes=False, cache=FlowCache(root=tmp_path / "empty"))


# ==================== the surface a human actually touches ====================


def test_the_cli_shows_the_evidence_and_exits_nonzero_when_it_refuses(tmp_path: Path, capsys,
                                                                     monkeypatch) -> None:
    """The verb is useless if a human cannot SEE what they would be overruling, and dangerous if a
    refusal is quiet. `flow inspect` prints the sources; `flow mark` exits 2 with the reason — work that
    did not succeed exits nonzero is S7a/S7b's rule, and this is a write-path decision.
    """
    from ultracua import cli

    store = tmp_path / "store"
    store.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ULTRACUA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ULTRACUA_HOME", str(store))

    spec, cache, key = _seed(tmp_path, _step("place the order", mutating=True, sources=["wire"]))
    monkeypatch.setattr(cli, "_flow_mark", cli._flow_mark)         # ensure the real handler is used

    from ultracua.flows import mark_step as _ms
    with pytest.raises(ValueError) as ei:
        _ms(spec, 0, writes=False, cache=cache)
    msg = str(ei.value)
    assert "wire" in msg, f"the refusal must name the evidence; got {msg!r}"
    assert "re-authoring" in msg or "does not overrule" in msg, (
        f"the refusal must say what to do instead, or an operator routes around it; got {msg!r}")


def test_inspect_renders_the_provenance_so_a_human_can_judge_it() -> None:
    """A mark a human cannot see the basis of is a mark they cannot correct — and `flow mark` refuses to
    overrule evidence, so the evidence has to be on screen for the refusal to make sense."""
    from ultracua.cli import _step_line

    gated = _step_line(0, _step("place the order", mutating=True, sources=["keyword", "wire"]), None)
    assert "**MUTATING**" in gated and "keyword" in gated and "wire" in gated, (
        f"the inspect line must show WHICH signal marked the step; got {gated!r}")

    demoted = _step_line(0, _step("payment history", mutating=False, sources=["human", "keyword"]), None)
    assert "keyword" in demoted and "human" in demoted, (
        f"a human's override must stay visible on the recipe they re-approve; got {demoted!r}")

    plain = _step_line(0, _step("open the report", mutating=False, sources=None), None)
    assert "MUTATING" not in plain and "was marked" not in plain, (
        f"an ordinary read step must render unchanged; got {plain!r}")


# ==================== reversibility, and the CLI for real ====================


def test_a_human_may_revise_their_own_verdict(tmp_path: Path) -> None:
    """A promotion used to be a ONE-WAY DOOR: `human` was not demotable, so re-running `flow mark --read`
    refused with "a human verdict does not overrule it" — about the human's OWN verdict. A verb an
    operator cannot undo is one they route around, and it launders nothing: a step carrying real
    evidence keeps that source too, so `['human','wire']` is still refused."""
    spec, cache, key = _seed(tmp_path, CachedStep(
        intent="continue", action="click",
        locator=LocatorSpec(role="button", name="continue", tag="button"),
        mutating=False, precond_scope="", precond_fingerprint="fp", mutating_sources=None))
    assert mark_step(spec, 0, writes=True, cache=cache) is True
    assert mark_step(spec, 0, writes=False, cache=cache) is True, (
        "a human must be able to undo their own mark")
    assert cache.get(key).steps[0].mutating is False


def test_a_human_verdict_does_not_launder_real_evidence(tmp_path: Path) -> None:
    """The reason adding `human` to the demotable set is safe: `merge_marks` is a UNION, so a promotion
    over an evidence-backed step keeps the evidence and the later demotion still refuses."""
    spec, cache, _key = _seed(tmp_path, _step("place the order", mutating=True, sources=["wire"]))
    mark_step(spec, 0, writes=True, cache=cache)          # a no-op re-affirmation, records `human`
    with pytest.raises(ValueError) as ei:
        mark_step(spec, 0, writes=False, cache=cache)
    assert "wire" in str(ei.value)


def test_re_affirming_a_mark_changes_nothing_and_says_so(tmp_path: Path) -> None:
    """`mutating_sources` is UNHASHED, so a re-affirmation moves no digest — and the CLI must not tell an
    operator their approval went stale when it did not. A false staleness claim trains people to
    re-approve reflexively, which is the opposite of what the gate is for."""
    spec, cache, _key = _seed(tmp_path, _step("payment history", mutating=True, sources=["keyword"]))
    assert mark_step(spec, 0, writes=False, cache=cache) is True
    assert mark_step(spec, 0, writes=False, cache=cache) is False, "a no-op must report itself as one"


def test_demoting_a_step_that_carries_a_commit_barrier_is_refused(tmp_path: Path) -> None:
    """`_attach_step_confirms` binds the Nth StepConfirm to the Nth MUTATING step. Demote one from the
    middle and every later confirm silently re-binds to the wrong write — a silent wrong-gate, which
    this register rates critical every time it has appeared."""
    from ultracua.cache import StepConfirm

    first = _step("submit step 1", mutating=True, sources=["keyword"]).model_copy(
        update={"confirm": StepConfirm(confirm_text_contains="Saved")})
    spec, cache, key = _seed(tmp_path, first, _step("submit step 2", mutating=True, sources=["keyword"]))
    with pytest.raises(ValueError) as ei:
        mark_step(spec, 0, writes=False, cache=cache)
    assert "barrier" in str(ei.value).lower()
    assert cache.get(key).steps[0].mutating is True


def test_the_CLI_really_exits_2_on_a_refusal(tmp_path: Path, monkeypatch, capsys) -> None:
    """THE FIRST VERSION OF THIS TEST WAS NAMED FOR THE CLI AND NEVER INVOKED IT — it called the library
    function and asserted on the exception, so the exit code in its own name was untested. That is the
    "looked thorough while testing nothing" shape, inside the test written to prove the CLI is honest.

    S7a/S7b spent two slices establishing that work which did not succeed exits nonzero with the reason
    PRINTED. This drives the real argv path.
    """
    from ultracua import cli
    from ultracua.flows import save_spec

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    spec = FlowSpec(name="wired", goal="work the panel", start_url="http://x/")
    save_spec(spec)
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    from ultracua.flows import _default_cache
    _default_cache().put(_flow(key, _step("place the order", mutating=True, sources=["wire"])))

    with pytest.raises(SystemExit) as ei:
        cli._flow_main(["mark", "--name", "wired", "--step", "0", "--read"])
    assert ei.value.code == 2, f"a refused write-path decision must exit nonzero; got {ei.value.code}"
    out = capsys.readouterr().out
    assert "refused" in out and "wire" in out, (
        f"the refusal AND the evidence it protects must be printed, not merely logged; got {out!r}")


def test_promoting_a_no_provenance_mark_does_not_launder_it_into_being_demotable(tmp_path: Path) -> None:
    """FOUND BY THE MATRIX DIMENSION, NOT BY ANY BESPOKE TEST IN THIS FILE.

    A step already marked with NO provenance was marked by SOMETHING, and demotion is refused precisely
    because the basis is unrecoverable. But a PROMOTION used to stamp `['human']` over that void —
    claiming the human was the sole basis — after which the next demotion was legal. Two individually
    legal calls defeating the guard: the same sequence-not-call shape as the precondition critical, and
    the second time in this one slice that the defect lived in an ORDER rather than in a step.
    """
    spec, cache, key = _seed(tmp_path, _step("pay the invoice", mutating=True, sources=None))
    mark_step(spec, 0, writes=True, cache=cache)                     # re-affirm; records the human
    with pytest.raises(ValueError) as ei:
        mark_step(spec, 0, writes=False, cache=cache)
    assert "unknown" in str(ei.value), (
        f"the unrecoverable basis must survive the promotion, or the no-provenance guard is one "
        f"annotation away from being bypassed; got {str(ei.value)!r}")
    assert cache.get(key).steps[0].mutating is True
