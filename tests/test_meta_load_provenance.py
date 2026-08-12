"""R3.8 — one transient read error destroys the trust sidecar, irrecoverably and while claiming not to.

`_load_meta` handles a TRANSIENT read failure correctly in isolation. After three attempts it returns a
poisoned in-memory meta (a quarantine, so every surface refuses) and deliberately does NOT touch the
file, logging "leaving the file untouched (it may be perfectly healthy)". On Windows the classic cause is
an AV/indexer sharing violation moments after `os.replace` — which `_save_meta`'s own atomic write is
what opens.

But `_load_meta` is not only a reader. `_update_meta` is load -> mutate -> save, and it saves whatever
the load handed back. So on the HOT write path — `_record_run`, which runs after EVERY replay — the
poisoned meta is serialised straight over the healthy file:

    approved=false  contracts=null  shape=null  read_pin=null  steps_hash=null
    quarantine=meta_unreadable

Two things make this strictly worse than the code it replaced. The old path went through
`_refuse_unreadable_meta`, which renames the original to `<key>.meta.json.corrupt.<ts>` FIRST; this one
skips that, so `os.replace` overwrites the only copy. And the quarantine text the operator is shown
still says "Inspect the preserved `.corrupt.*` copy" — a file that on this path never exists.

The operator then does `flow release` + `flow approve`, and the flow goes green with its H9 value gate
and shape gate GONE and the LLM extractor back on a replay that was pinned 0-LLM. A later wrong value
returns as a clean success.

WHY A FIELD CHECK IS NOT THE FIX
--------------------------------
The obvious patch is "skip the save if `meta.quarantine` is `meta_unreadable`". That fails on the worst
variant: `release()`'s own mutation SETS `quarantine = None`. By the time the save happens the marker it
would test has been erased by the very mutation being applied, and the file is left completely blank —
the H9 quarantine a human was told to investigate, silently forgotten.

So the question `_update_meta` must ask is not "what does this meta say" but "where did it come from" —
did `_load_meta` parse it off disk, or synthesise it? That is PROVENANCE, and it is known for certain at
the point the loader picks its branch. Note the three states are distinct and only ONE of them refuses:
parsed-from-file and file-absent must both still save (the absent case is how a sidecar is first
created); only "unreadable" must not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ultracua.cache import FlowCache


def _healthy_meta(flows_mod, cache, key) -> None:
    """Persist a fully-populated trust sidecar — every field R3.8 destroys."""
    flows_mod._save_meta(cache, key, flows_mod.FlowMeta(
        approved=True, shape={"t": "string"}, steps_hash="abc123", slots_hash="def456",
        contracts_hash="ghi789", contracts={"total": {"type": "number"}},
        read_pin={"sel": "#total", "type": "number"}, runs=5, successes=5))


def _break_reads_of(monkeypatch, target: Path) -> None:
    """Make exactly one file unreadable with a TRANSIENT-looking OSError (the sharing-violation shape).

    Scoped to one path on purpose: a blanket failure would also break the cache read and the test would
    pass for the wrong reason."""
    real = Path.read_text

    def _boom(self, *a, **kw):
        if self == target:
            raise OSError(32, "The process cannot access the file because it is being used by another "
                              "process")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _boom)


def test_a_transient_meta_read_failure_does_not_overwrite_the_healthy_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE defect, on the hot path. `_record_run` runs after every replay and goes through
    `_update_meta`; one sharing violation on the read half and the healthy file is gone."""
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    key = "k1"
    _healthy_meta(flows_mod, cache, key)
    p = flows_mod._meta_path(cache, key)
    before = json.loads(p.read_text(encoding="utf-8"))
    assert before["approved"] is True, "the fixture did not persist a healthy sidecar"

    _break_reads_of(monkeypatch, p)
    # The ordinary post-replay mutation. It must not be able to destroy trust state — and it must say so
    # rather than appearing to succeed, because "the update silently did nothing" is how an operator ends
    # up believing a trust decision took effect.
    with pytest.raises(flows_mod.MetaUnreadableError):
        flows_mod._update_meta(cache, key, lambda m: setattr(m, "runs", m.runs + 1),
                              on_unreadable="raise")

    monkeypatch.undo()
    after = json.loads(p.read_text(encoding="utf-8"))
    lost = [f for f in ("approved", "shape", "steps_hash", "slots_hash", "contracts_hash",
                        "contracts", "read_pin") if before.get(f) and not after.get(f)]
    assert not lost, (
        f"a TRANSIENT read error destroyed {lost} on a healthy sidecar. `_load_meta` refused to touch "
        f"the file and said so in the log — then `_update_meta` saved the poisoned meta it had returned "
        f"straight over it, with no `.corrupt.*` backup, while the quarantine text tells the operator to "
        f"go inspect that backup. after={after}")


def test_a_transient_read_during_release_does_not_blank_the_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE VARIANT A FIELD CHECK CANNOT CATCH, and the reason the fix must be provenance.

    `release()` mutates `quarantine = None`. If the transient lands on the read inside its
    `_update_meta`, a guard that tests `meta.quarantine == meta_unreadable` looks at a value the mutation
    has already erased — so it saves, and the file is left blank: no approval, no contracts, no shape,
    and the H9 quarantine a human was told to investigate silently gone."""
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    key = "k2"
    flows_mod._save_meta(cache, key, flows_mod.FlowMeta(
        approved=True, shape={"t": "number"}, steps_hash="abc123",
        quarantine={"code": "value_contract", "reason": "total went negative", "ts": 1e9}))
    p = flows_mod._meta_path(cache, key)

    _break_reads_of(monkeypatch, p)
    with pytest.raises(flows_mod.MetaUnreadableError):
        flows_mod._update_meta(cache, key, lambda m: setattr(m, "quarantine", None),
                              on_unreadable="raise")

    monkeypatch.undo()
    after = json.loads(p.read_text(encoding="utf-8"))
    assert after.get("quarantine") is not None, (
        f"a transient read during `release` erased the H9 quarantine a human was told to investigate — "
        f"and with it the approval, shape and steps digest. The mutation sets quarantine=None, so any "
        f"guard that inspects the meta's CONTENTS is looking at a marker the mutation just removed. "
        f"after={after}")
    assert after.get("approved") is True and after.get("shape"), (
        f"the rest of the trust state went with it: after={after}")


def test_an_unreadable_sidecar_does_not_replace_the_H9_quarantine_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE COST OF GETTING THE REFUSAL DIRECTION WRONG, and the reason `_quarantine` skips rather than
    raises. Found by an adversarial pass on the first draft of this very fix.

    `_do_quarantine` persists an H9 value-contract quarantine and THEN raises `FlowQuarantineError` with
    the value-free reason ("field 'total': flipped non-positive"). If the persist raises instead, four
    things go at once: the operator is told about an IO error rather than a wrong VALUE; `_record_run`
    never captures the reason; the quarantine is not persisted either (so no better than skipping); and
    the error's `retryable` flips False -> True, which on the MCP surface invites an agent to re-run a
    flow that just produced wrong data — the exact opposite of `FlowQuarantineError`'s own docstring.

    The first draft of this fix answered that by making `_quarantine` SKIP. A second adversarial pass
    killed it, and the reason is the more useful lesson: `_quarantine` is SHARED, and its other caller is
    the audit judge — whose finding is by construction NOT deterministically re-derivable (`audit_flows`
    only judges flows that already passed both deterministic gates) and whose evidence artifact is
    dropped immediately afterwards. Skipping there loses the finding permanently while `flow audit`
    prints "[QUARANTINED]" for a flow that is still approved and still returning the wrong value.

    So it RAISES, and each caller handles it: `_do_quarantine` catches it and still raises
    `FlowQuarantineError` with the H9 reason plus a note that persistence failed; `audit_flows` gained
    the per-flow guard it was missing and KEEPS the artifact so the finding can be re-judged.

    Fix the caller that lacks the guard, not the mechanism they share."""
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    key = "k5"
    _healthy_meta(flows_mod, cache, key)
    _break_reads_of(monkeypatch, flows_mod._meta_path(cache, key))

    with pytest.raises(flows_mod.MetaUnreadableError):
        flows_mod._quarantine(cache, key, reason="field 'total': flipped non-positive")
    with pytest.raises(flows_mod.MetaUnreadableError):
        # The audit sink must surface it too — silently dropping a judge finding is unrecoverable.
        flows_mod.QuarantineSink(cache, key, "inv").quarantine("slow_drift")

    monkeypatch.undo()
    after = json.loads(flows_mod._meta_path(cache, key).read_text(encoding="utf-8"))
    assert after.get("approved") is True and after.get("shape"), (
        f"refusing the quarantine write must still leave the sidecar untouched: {after}")


def test_the_transient_reason_does_not_send_the_operator_to_a_backup_that_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The quarantine text an operator actually sees on the transient path. Sharing one string with the
    CORRUPT path made every clause of it false here: nothing was lost, no `.corrupt.*` copy exists, and
    "re-learn and re-approve" is the one action that would discard the H9 shape/contracts baseline
    sitting intact on disk. This slice makes the transient path common, so the text has to be right."""
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    key = "k6"
    _healthy_meta(flows_mod, cache, key)
    p = flows_mod._meta_path(cache, key)
    _break_reads_of(monkeypatch, p)

    meta, provenance = flows_mod._load_meta_with_provenance(cache, key)
    monkeypatch.undo()

    assert provenance == "unreadable"
    reason = (meta.quarantine or {}).get("reason", "")
    # Assert on the INSTRUCTION, not the vocabulary: the corrected text mentions both a `.corrupt.*`
    # copy and re-learning precisely in order to rule them out, so a bare word search would reject the
    # right message as readily as the wrong one.
    low = reason.lower()
    assert "inspect the preserved" not in low, (
        f"the operator is sent to a `.corrupt.*` backup that was never created — nothing was moved "
        f"aside, because nothing was found to be corrupt: {reason}")
    assert "then re-learn and re-approve" not in low, (
        f"the operator is told to re-learn, which discards the shape/contracts baseline that is sitting "
        f"intact on disk and merely unread: {reason}")
    assert "retry" in low, (
        f"a transient read failure clears on its own; the message must say so rather than prescribing "
        f"recovery from a loss that did not happen: {reason}")
    assert list(p.parent.glob("*.corrupt.*")) == [], "nothing should have been preserved aside"
    assert json.loads(p.read_text(encoding="utf-8"))["approved"] is True, "the file must be untouched"


def test_a_transient_failure_on_the_SAVE_is_retried_like_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE SIBLING THE READ GOT AND THE WRITE DID NOT.

    R3.8 is about transient sidecar IO. `_load_meta` retries such a read three times with a backoff,
    because on Windows an AV/indexer sharing violation moments after `os.replace` is the classic case.
    `_save_meta`'s own `os.replace` — the operation that OPENS that window — had no retry at all, so the
    same blip that the read survives crashes the write.

    Found by the concurrency test `test_record_run_no_lost_updates_under_heavy_contention`, which failed
    with `PermissionError: [WinError 5] Access is denied` on exactly that rename, ~1 run in 6 under full
    suite load. Deterministic here rather than probabilistic: fail the rename twice, then let it through.
    """
    import os as os_mod

    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    key = "k7"
    real_replace, calls = os_mod.replace, {"n": 0}

    def _flaky(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(flows_mod.os, "replace", _flaky)
    flows_mod._save_meta(cache, key, flows_mod.FlowMeta(approved=True, shape={"t": "string"}))
    monkeypatch.undo()

    assert calls["n"] == 3, f"expected two retries then success, got {calls['n']} attempts"
    assert flows_mod._load_meta(cache, key).approved is True, (
        "the sidecar was not persisted despite the rename eventually succeeding")


def test_a_permanent_save_failure_raises_and_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half: retrying must not become swallowing. A rename that never succeeds has to surface —
    and must not leave a `.tmp` behind, because the sidecar directory is scanned and a stray temp is
    indistinguishable from a torn write to anyone looking at it later.

    THE EXPECTED TYPE CHANGED IN 0.95.0, deliberately, and this is the whole of R4.18. It used to be a
    bare `OSError`, which is precisely the bug: every `except FlowReplayError` on the replay, batch and
    MCP paths walked past it, so a write that COMMITTED could surface as `PermissionError` out of
    `_record_run` with no ledger row. `MetaUnwritableError` is a `FlowReplayError`, so this assertion is
    STRICTLY STRONGER than the one it replaces — it still fails if the error is swallowed, and it now
    also fails if the error is untyped again.
    """
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    key = "k8"

    def _always(src, dst, *a, **kw):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(flows_mod.os, "replace", _always)
    with pytest.raises(flows_mod.MetaUnwritableError) as ei:
        flows_mod._save_meta(cache, key, flows_mod.FlowMeta(approved=True))
    assert isinstance(ei.value, flows_mod.FlowReplayError), (
        "the point of typing it is that the handlers guarding on the family catch it")
    assert isinstance(ei.value.__cause__, OSError), (
        "and the underlying IO error must stay attached — an operator debugging a permissions problem "
        "needs the errno, not a paraphrase of it")
    monkeypatch.undo()

    assert list((tmp_path / "c").glob("*.tmp")) == [], (
        "a failed save left its temp file behind")


# --- the other half: this must not turn into "never save anything" ------------------------------------

def test_every_return_in_the_loader_carries_a_provenance() -> None:
    """STRUCTURAL. The behavioural tests above cover the read failures that exist today; this covers the
    one added tomorrow.

    The loader's exits must each return `(meta, provenance)` with provenance a literal from the known
    set — a bare `return FlowMeta()` would default to *nothing*, unpack-error at best and silently arm
    the write path at worst. This is the same shape as `test_landed_arms_the_ledger.py`'s `_fail` guard,
    and for the same reason: R3.8 was not about the branches that existed when the retry logic was
    written.

    THE LOADER IS TWO FUNCTIONS SINCE 0.95.0 and this scan follows it, because a structural guard that
    keeps pointing at the old name is worse than none — R3.11 split `_load_meta_with_provenance` into a
    TOTAL wrapper (which cannot raise, by construction) over `_read_meta` (which classifies). So both are
    scanned, and the wrapper is allowed exactly one extra shape: a bare `return _read_meta(...)`
    delegation, which is safe precisely because the callee is held to the same rule in the same test.
    """
    import ast
    import pathlib

    path = pathlib.Path(__file__).parents[1] / "src" / "ultracua" / "flows.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    known = {"file", "absent", "unreadable", "corrupt"}
    offenders, seen, delegations = [], set(), 0

    fns = {n.name: n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name in ("_load_meta_with_provenance", "_read_meta")}
    assert set(fns) == {"_load_meta_with_provenance", "_read_meta"}, (
        f"could not find both halves of the loader (found {sorted(fns)}) — this test asserts a NEGATIVE, "
        f"so a rename would make it pass while checking nothing")

    for name, fn in fns.items():
        nested = {id(r) for inner in ast.walk(fn)
                  if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner is not fn
                  for r in ast.walk(inner) if isinstance(r, ast.Return)}
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or id(node) in nested:
                continue
            v = node.value
            if (isinstance(v, ast.Tuple) and len(v.elts) == 2
                    and isinstance(v.elts[1], ast.Constant) and v.elts[1].value in known):
                seen.add(v.elts[1].value)
            elif (name == "_load_meta_with_provenance" and isinstance(v, ast.Call)
                  and isinstance(v.func, ast.Name) and v.func.id == "_read_meta"):
                delegations += 1     # the wrapper handing off to the half this same test also scans
            else:
                offenders.append(
                    f"{name} line {node.lineno}: not `(meta, <'file'|'absent'|'unreadable'|'corrupt'>)`")

    assert not offenders, (
        "a return in the meta loader does not declare where its meta came from. "
        "`_update_meta` refuses the read-modify-write on 'unreadable' alone, so an undeclared exit "
        "either crashes on unpack or — worse — is treated as a faithful read and its blanks are saved "
        "over the real trust state:\n  " + "\n  ".join(offenders))
    assert delegations == 1, (
        f"the wrapper must delegate to `_read_meta` exactly once ({delegations} found). Zero means the "
        f"totality wrapper stopped calling the classifier and this scan is now guarding a dead path; "
        f"more than one means there is a second entry into classification that the wrapper's `except` "
        f"may not cover.")
    assert seen == known, (
        f"the loader no longer produces every provenance ({sorted(known - seen)} missing). If a state "
        f"was genuinely removed, update this test AND `_update_meta`'s refusal — collapsing 'absent' "
        f"into 'unreadable' stops every new sidecar being created; the other way round is R3.8.")


def test_best_effort_is_never_used_for_a_trust_decision() -> None:
    """`on_unreadable="skip"` downgrades the refusal to a log. That is right for a run counter, and for a
    write whose caller has a BETTER failure to report (`_quarantine`, whose caller then raises the real
    H9 reason). It is WRONG for
    anything that changes what the system is allowed to do: silently not persisting an approval, a
    quarantine, a release or a pin-clear leaves the operator believing a trust decision took effect.

    Pinned by ALLOWLIST rather than by inspection, so a new best-effort site has to be argued for."""
    import ast
    import pathlib

    # (qualified enclosing scope, why the loss is survivable)
    #
    # QUALIFIED BY CLASS, not by bare method name — and that is not pedantry. `AdvisorySink.quarantine`
    # deliberately writes nothing to trust state (it bumps a counter), while `QuarantineSink.quarantine`
    # persists a real H9 quarantine. Both are `quarantine`. A bare-name allowlist would authorise
    # best-effort on the one whose whole job is to make every future run refuse.
    ALLOWED = {
        "_record_run": "run counters + last_error for the health view; runs inside replay()'s except",
        "AdvisorySink.quarantine": "an unreviewed-advisory counter for the out-of-band judge",
        "_capture_audit": "consuming the audit_due flag after the capture it asked for",
    }
    path = pathlib.Path(__file__).parents[1] / "src" / "ultracua" / "flows.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def enclosing(node):
        """`Class.method` when inside a class, else the bare function name."""
        fn = None
        while node in parents:
            node = parents[node]
            if fn is None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = node.name
            elif fn is not None and isinstance(node, ast.ClassDef):
                return f"{node.name}.{fn}"
        return fn or "<module>"

    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_update_meta"):
            continue
        if any(k.arg == "on_unreadable" and getattr(k.value, "value", None) == "skip"
               for k in node.keywords):
            fn = enclosing(node)
            if fn not in ALLOWED:
                offenders.append(f"line {node.lineno}: best_effort=True inside {fn!r}")

    assert not offenders, (
        f"a meta write chose on_unreadable='skip' outside the allowlist "
        f"({sorted(ALLOWED)}). If the write changes approval, quarantine, release state or the read "
        f"pin, it must FAIL LOUD — an operator who is told nothing assumes it took effect:\n  "
        + "\n  ".join(offenders))


def test_an_ordinary_update_still_persists(tmp_path: Path) -> None:
    """LIVENESS. Without this, the property above is satisfied by refusing every write — which would
    silently stop run history, approval and quarantine from ever being recorded."""
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    key = "k3"
    _healthy_meta(flows_mod, cache, key)
    flows_mod._update_meta(cache, key, lambda m: setattr(m, "runs", 42), on_unreadable="raise")
    assert flows_mod._load_meta(cache, key).runs == 42
    assert flows_mod._load_meta(cache, key).approved is True


def test_a_first_write_still_creates_an_absent_sidecar(tmp_path: Path) -> None:
    """The THIRD provenance state, and the one a naive "only save what you parsed" rule would break: an
    ABSENT sidecar is not an unreadable one. A flow that was never learned has no trust state to lose,
    and this is how the file gets created in the first place."""
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    key = "k4"
    assert not flows_mod._meta_path(cache, key).exists()
    flows_mod._update_meta(cache, key, lambda m: setattr(m, "approved", True), on_unreadable="raise")
    assert flows_mod._load_meta(cache, key).approved is True, (
        "an absent sidecar was treated like an unreadable one, so it was never created — approval, run "
        "history and quarantine would all silently stop being recorded for every new flow")
