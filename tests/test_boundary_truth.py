"""No failure crossing a boundary may lose its TYPE or its REASON — and one durable rename, not seven.

S10. Five findings that read as five patches and are one invariant, in two halves.

THE DURABLE-WRITE HALF (R4.20). S4 measured `_save_meta`'s `os.replace` failing roughly 1 run in 6 under
full-suite load — a Windows AV/indexer sharing violation moments after the temp file is written — and gave
that ONE call site three attempts with a backoff. It is not one call site. Reproduced at 0.94.0: SEVEN
renames in this package, six of them with no retry at all, every one of them the operation that opens the
window on the next reader. `FlowCache.put` losing that race forgets a recipe; `history` losing it loses a
run log; `audit` losing it drops an evidence artifact. The register's own prescription is the fix shape —
share ONE helper, do not transcribe the retry loop a second time — so the property below is asserted over
the SET of durable writers, and an AST scan makes a bare `os.replace` inexpressible outside the helper.

THE TYPED-ERROR HALF (R4.18, R4.17, R3.11, R4.15). What a caller can catch is a contract, and four places
break it in the same direction — the specific, actionable failure is replaced by a generic one:

    R4.18  `_save_meta` raises a bare OSError, so every `except FlowReplayError` on the replay, batch and
           MCP paths misses it. A write that COMMITTED can surface as `PermissionError` from
           `_record_run` instead of `{"status": "confirmed"}`, with no ledger row written.
    R4.17  that same bare error pre-empts `FlowQuarantineError`, so an operator whose flow returned a
           WRONG VALUE is told about a file permission instead. Reproduced: the reason is LOST.
    R3.11  `_load_meta`'s docstring says "It never raises" and it does — `RecursionError` on a deeply
           nested sidecar, and `OSError` straight out of `Path.exists()`. Both escape the typed arms,
           tracebacking `health()` and the MCP tools/list loop on one bad flow.
    R4.15  `cli` catches exactly `EmptyFlowStoreError`, so every other typed error reaches the user as a
           Python traceback on six verbs, burying the remedy text the error was written to carry.

The last one is why these tests are written as PROPERTIES OVER A SET rather than one case each: R4.15's
real defect is that the handler enumerates a class instead of the family, and a bespoke test for
`MetaUnreadableError` would pass while the next typed error added is still a traceback. Same reason the
fleet verdict enumerates QUIET statuses — a member added tomorrow must be covered by construction.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import time
from pathlib import Path

import pytest

import ultracua.flows as F
from ultracua.cache import CachedFlow, CachedStep, FlowCache
from ultracua.locators import LocatorSpec


class _FlakyReplace:
    """`os.replace` that fails `n` times with the Windows sharing-violation shape, then succeeds.

    Two failures is the reproduction S4 measured, not a worst case invented here: an AV scanner or
    indexer holds the file for a few milliseconds after the write, and the retry that already exists on
    `_save_meta` was sized for exactly this.
    """

    def __init__(self, n: int) -> None:
        self.left, self.calls, self.real = n, 0, os.replace

    def __call__(self, a, b):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise PermissionError(5, "Access is denied")
        return self.real(a, b)


def _flow(key: str) -> CachedFlow:
    return CachedFlow(key=key, goal="g", start_url="http://x/", created_ts=time.time(),
                      steps=[CachedStep(intent="continue", action="click",
                                        locator=LocatorSpec(role="button", name="c", tag="button"))])


def _durable_writers(root: Path):
    """The writers whose durability the fleet depends on AND that propagate the failure, with what each
    one costs when the rename is lost.

    Named rather than discovered, because the point of a cell is the CONSEQUENCE — a discovered list
    would tell a reader nothing about why a lost `history` rename matters.

    Two further durable writers are deliberately absent: `FlowCache.remember_refusal` and `audit`'s
    artifact write both SWALLOW the IO error by design (each is recording something that has already
    happened, and raising would mask it). They would pass this cell without exercising anything, which
    is worse than not being in it — the AST scan below is what covers them, and it covers them better,
    because it also covers the writer added next year.
    """
    import ultracua.history as H

    cache = FlowCache(root=root / "c")
    cache.root.mkdir(parents=True, exist_ok=True)
    return [
        ("flows._save_meta", lambda: F._save_meta(cache, "k", F.FlowMeta()),
         "the trust sidecar: approval, quarantine, contracts, the recipe digest, the read pin"),
        ("FlowCache.put", lambda: cache.put(_flow("k")),
         "the recipe itself — a lost rename is a silently forgotten flow"),
        ("history.save_history", lambda: H.save_history(cache, "k", {"rings": {}, "anchors": {}}),
         "the magnitude ANCHOR, whose loss silently re-baselines at the drifted value"),
    ]


# ==================== the durable-write half (R4.20) ====================


@pytest.mark.parametrize("name", ["flows._save_meta", "FlowCache.put", "history.save_history"])
def test_every_durable_writer_survives_the_transient_rename_failure_that_one_of_them_survives(
        name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The asymmetry, as a property over the writers rather than a note beside one of them.

    `_save_meta` passes this today and `FlowCache.put` does not, which is the whole finding: the guard
    exists, on a sibling, and was never applied to the mechanism they share.
    """
    writers = dict((n, c) for n, c, _ in _durable_writers(tmp_path))
    call = writers[name]
    flaky = _FlakyReplace(2)
    monkeypatch.setattr(os, "replace", flaky)
    try:
        call()
    except OSError as exc:
        pytest.fail(
            f"{name} lost a recipe/sidecar to {flaky.calls} transient sharing violation(s) "
            f"({type(exc).__name__}: {exc}). `_save_meta` survives exactly this, measured at ~1 run in 6 "
            f"under full-suite load; every durable rename here opens the same window on the next reader.")
    assert flaky.calls >= 2, (
        f"premise: the rename must actually have been retried, not skipped — {flaky.calls} call(s)")


def test_a_bare_os_replace_is_INEXPRESSIBLE_outside_the_one_durable_rename_helper() -> None:
    """The enforcement half. Behaviour tests cover the writers that exist; this covers the one added
    tomorrow, which is where every sibling-guard defect in this register has come from.

    Positive control included: the scan must find the offending shape when it is present, or it is
    theatre — the regex version of an earlier enforcement test was exactly that, and was rebuilt as an
    AST scan for this reason.
    """
    import ultracua

    pkg = Path(ultracua.__file__).parent
    allowed = {"fsio.py"}
    offenders: list[str] = []
    for py in sorted(pkg.rglob("*.py")):
        if py.name in allowed:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "replace"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"):
                offenders.append(f"{py.relative_to(pkg)}:{node.lineno}")

    # POSITIVE CONTROL — the same walk over a snippet that DOES call it, so a scan that silently matches
    # nothing (a renamed helper, a changed AST shape) fails here instead of passing everywhere.
    ctl = ast.parse("import os\ndef f(a, b):\n    os.replace(a, b)\n")
    found_ctl = [n for n in ast.walk(ctl)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "replace" and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == "os"]
    assert found_ctl, "the AST scan does not detect its own positive control — it proves nothing"

    assert not offenders, (
        f"{len(offenders)} bare `os.replace` call(s) outside `fsio.py`: {offenders}. Each is a durable "
        f"rename with no retry, which S4 measured failing ~1 run in 6 under load. Route it through the "
        f"one helper instead of transcribing the retry loop again — transcription is how this became "
        f"seven sites with one guard.")


# ==================== the typed-error half ====================


def test_a_meta_that_cannot_be_written_raises_a_TYPED_error_every_caller_already_catches(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R4.18. The replay, batch and MCP paths all guard with `except FlowReplayError`, and a bare
    `PermissionError`/`OSError` walks past every one of them.

    ASSERTED AT `_save_meta`, WHICH IS THE MECHANISM. An earlier draft of this test asserted it at
    `_record_run` instead, and the pre-merge audit showed why that was the wrong surface: `_record_run`
    is bookkeeping, it runs immediately before four deliberate raises in `replay()`, and making IT loud
    is what let a sidecar blip replace `WriteUnverifiedError` with "…RETRY". Typing the error is R4.18;
    choosing which callers let it through is a separate decision, made in the two cells further down.
    """
    cache = FlowCache(root=tmp_path / "c")
    cache.root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(os, "replace", _FlakyReplace(99))
    with pytest.raises(F.FlowReplayError) as ei:
        F._save_meta(cache, "k", F.FlowMeta(approved=True))
    assert "sidecar" in str(ei.value).lower() or "meta" in str(ei.value).lower(), (
        f"the typed error must say WHAT could not be written; got {str(ei.value)!r}")
    assert isinstance(ei.value.__cause__, OSError), "the errno must stay attached for a human debugging it"


def test_a_quarantine_whose_sidecar_cannot_be_written_still_tells_the_operator_the_REAL_reason(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R4.17, reproduced: the H9 reason is LOST and replaced by a file-permission error.

    Both facts must reach the operator, and the ORDER matters — "your flow returned a wrong value" is
    the actionable one; "and the quarantine could not be persisted, so the next run will NOT refuse" is
    the aggravating one. Reporting only the second is how a value defect reads as an ops blip.
    """
    cache = FlowCache(root=tmp_path / "c")
    reason = "the total contract returned 7 where the baseline says 42"
    monkeypatch.setattr(os, "replace", _FlakyReplace(99))
    with pytest.raises(Exception) as ei:
        F._quarantine(cache, "k", reason=reason)
    msg = str(ei.value)
    assert "42" in msg and "7" in msg, (
        f"the H9 reason was replaced by the IO error — the operator learns about a file instead of a "
        f"wrong value. got {msg!r}")
    assert isinstance(ei.value, F.FlowReplayError), (
        f"and it must stay inside the family every caller catches; got {type(ei.value).__name__}")


def test_a_CORRUPT_sidecar_still_reports_corrupt_when_the_quarantine_cannot_be_written(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FOUND BY THE SIBLING CHECK, IN THIS SLICE'S OWN FIX, BEFORE IT SHIPPED.

    Typing `_save_meta`'s failure (R4.18) silently UN-CAUGHT it one call away: `_refuse_unreadable_meta`
    guards its save with `except OSError`, which a `MetaUnwritableError` no longer satisfies. The error
    would then have escaped into the new totality wrapper and come back as `unreadable` — so a genuinely
    CORRUPT sidecar, whose recovery is "inspect the preserved `.corrupt.*` copy and re-learn", would have
    told the operator to RETRY a transient blip that never happened. Both messages are loud; exactly one
    of them is true, and the fix would have swapped them.

    This is the register's most-repeated shape reproduced by a fix FOR that shape, which is why the
    slice pins it rather than just correcting it.
    """
    cache = FlowCache(root=tmp_path / "c")
    cache.root.mkdir(parents=True, exist_ok=True)
    p = F._meta_path(cache, "k")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json at all", encoding="utf-8")     # real corruption: the bytes are not a meta
    monkeypatch.setattr(os, "replace", _FlakyReplace(99))  # ...and the quarantine cannot be persisted

    meta, provenance = F._load_meta_with_provenance(cache, "k")
    assert provenance == "corrupt", (
        f"a sidecar whose BYTES were read and are not a meta is corrupt, whatever happened next; "
        f"reporting {provenance!r} sends the operator to the wrong remedy")
    assert meta.quarantine, "and the run must still refuse on a poisoned meta"


@pytest.mark.parametrize("hostile", ["deeply_nested", "exists_raises"])
def test_load_meta_NEVER_raises_which_is_what_its_docstring_promises(
        hostile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R3.11. `health()` and the MCP tools/list loop iterate the whole fleet; one bad sidecar must not
    take the listing down with it, which is precisely what the docstring undertakes.

    Both arms escape today, and they escape for the same reason — the arms enumerate the exception types
    someone thought of (`ValueError`, `UnicodeDecodeError`, `OSError`) rather than closing the set. A
    `RecursionError` is neither, and `Path.exists()` raising is a FOURTH provenance the three-state model
    does not name.
    """
    cache = FlowCache(root=tmp_path / "c")
    cache.root.mkdir(parents=True, exist_ok=True)
    p = F._meta_path(cache, "k")
    p.parent.mkdir(parents=True, exist_ok=True)

    if hostile == "deeply_nested":
        p.write_text("[" * 20000 + "]" * 20000, encoding="utf-8")
    else:
        p.write_text(json.dumps({"approved": True}), encoding="utf-8")

        def _boom(self):
            raise OSError(5, "Access is denied")

        monkeypatch.setattr(Path, "exists", _boom)

    meta, provenance = F._load_meta_with_provenance(cache, "k")
    assert provenance in ("file", "absent", "unreadable", "corrupt"), provenance
    assert provenance != "absent", (
        "a sidecar that could not be READ must never read as ABSENT — that is R3.8, one transient "
        "failure blanking approval, contracts, shape, the recipe digest and the read pin")
    assert meta is not None


def _typed_error_family() -> list[type]:
    out, seen = [], set()

    def walk(c: type) -> None:
        for s in c.__subclasses__():
            if s not in seen:
                seen.add(s)
                out.append(s)
                walk(s)

    walk(F.FlowReplayError)
    return [F.FlowReplayError] + out


@pytest.mark.parametrize("err", _typed_error_family(), ids=lambda c: c.__name__)
def test_no_typed_error_reaches_the_user_as_a_traceback(
        err: type, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """R4.15, as a property over the FAMILY rather than over the one class that was reported.

    The handler catches exactly `EmptyFlowStoreError`, so every other typed error tracebacks on six
    verbs and buries the remedy text — which for `MetaUnreadableError` is the difference between "retry,
    it was an AV scan" and a user deleting a healthy sidecar. Parametrising over
    `FlowReplayError.__subclasses__()` means an error class added tomorrow is covered by construction;
    a bespoke test for the reported class would pass while the next one is still a traceback.
    """
    import ultracua.cli as cli

    def _boom(args):
        raise err("the sidecar could not be read; if this was a transient sharing violation, RETRY")

    monkeypatch.setattr(cli, "_flow_dispatch", _boom)
    with pytest.raises(SystemExit) as ei:
        cli._flow_main(["list"])
    assert ei.value.code not in (0, None), (
        f"{err.__name__} must not exit 0 — a failure path that exits 0 is how a cron job reports green")
    out = capsys.readouterr()
    assert "RETRY" in (out.err + out.out), (
        f"{err.__name__} reached the user without its remedy text; the error carries the operator's "
        f"whole recovery path and a traceback buries it")
    assert "Traceback" not in (out.err + out.out)


# ==================== what the pre-merge adversarial audit found IN this fix ====================
#
# Two survivors out of 26 filed, 24 refuted. Both are the register's own shapes reproduced by the code
# closing them, which is why they get pinned here rather than just corrected.


def test_a_failed_durable_write_leaves_no_temp_behind_whichever_half_failed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AUDIT FINDING 1. The rename half always cleaned up; the WRITE half did not.

    So ENOSPC / EIO / a quota hit left a stray `.tmp` beside the destination while `_save_meta`'s new
    error message told the operator it had been removed — a fail-loud message with a false clause, which
    is precisely the defect the `_META_CORRUPT` / `_META_UNREADABLE` split exists to have fixed once.

    The stray itself was pre-existing; the sentence denying it was new. Both are closed in `fsio`, not in
    the message, because seven writers share that helper and conditionalising one sentence in one of them
    leaves the other six stranding temps — R4.20's own shape, one level down.
    """
    cache = FlowCache(root=tmp_path / "c")
    cache.root.mkdir(parents=True, exist_ok=True)

    def _no_space(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", _no_space)
    with pytest.raises(F.MetaUnwritableError) as ei:
        F._save_meta(cache, "k", F.FlowMeta(approved=True))

    strays = sorted(p.name for p in cache.root.rglob("*.tmp"))
    assert not strays, (
        f"the write half left {strays} behind. A stray temp beside a sidecar is indistinguishable from a "
        f"torn write to whoever looks next — which is the reason the rename half cleans up.")
    assert "temp file has been removed" in str(ei.value), (
        "and the message may only make that claim while it is true of BOTH halves")


@pytest.mark.parametrize("ok", [True, False], ids=["success", "failure"])
def test_BOOKKEEPING_never_raises_so_it_cannot_replace_the_verdict_a_caller_already_holds(
        ok: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AUDIT FINDING 2 — CRITICAL, inviolable #3, and created by the fix for R4.18.

    `_record_run` runs immediately before four deliberate raises in `replay()`. Once its failure became
    typed it propagated out of those positions, so a transient sharing violation swapped the exception:
    `WriteUnverifiedError` ("the commit actuated and cannot be verified", retryable=False) became
    `MetaUnwritableError` ("nothing was corrupted … RETRY", retryable=True) — with no ledger row, since
    the arming point only stamps `landed` when an attempt observed the confirm transition. An MCP agent
    honouring `retryable` re-invokes, `ledger.is_committed` is False, and the commit fires twice.

    THE GUARD ALREADY EXISTED ON THE SIBLING HALF. `_record_run` passes `on_unreadable="skip"` and its
    own comment explains why bookkeeping must not mask a real failure with an IO error; the SAVE half
    never got the same treatment. So the fix is here, in the mechanism, and asserted over both outcome
    shapes rather than over the five call sites — a per-caller rule is what a sixth raise site forgets.
    """
    cache = FlowCache(root=tmp_path / "c")
    cache.root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(os, "replace", _FlakyReplace(99))
    F._record_run(cache, "k", ok=ok, error=None if ok else "the commit actuated but cannot be verified")


def test_but_every_TRUST_CHANGING_write_still_refuses_loudly(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """THE OTHER HALF, AND IT IS WHAT STOPS THE FIX ABOVE FROM BEING A REGRESSION.

    "Make the sidecar failure quiet" satisfies the property above completely and undoes R4.18, whose
    harm was an untyped error escaping every handler. The line is the one `_record_run`'s own comment
    draws: bookkeeping (run counters, last_error) is survivable and stays quiet; anything that changes
    what the system is ALLOWED to do stays loud, because silently not persisting one of those leaves an
    operator believing a trust decision took effect when it did not.

    Without this cell the safe-looking direction is a one-line change away, which is the exact shape the
    write-safety matrix's "must remain learnable" clause exists to prevent.
    """
    cache = FlowCache(root=tmp_path / "c")
    cache.root.mkdir(parents=True, exist_ok=True)
    F._save_meta(cache, "k", F.FlowMeta())               # a real sidecar first, so these reach the save
    monkeypatch.setattr(os, "replace", _FlakyReplace(99))

    loud = {
        "_save_meta": lambda: F._save_meta(cache, "k", F.FlowMeta(approved=True)),
        "_update_meta": lambda: F._update_meta(
            cache, "k", lambda m: setattr(m, "approved", True), on_unreadable="raise"),
        "_quarantine": lambda: F._quarantine(cache, "k", reason="the total returned 7, baseline 42"),
    }
    for name, call in loud.items():
        with pytest.raises(F.FlowReplayError) as ei:
            call()
        assert isinstance(ei.value, F.MetaUnwritableError), (
            f"{name} must still refuse loudly and typed; got {type(ei.value).__name__}")
    # and the H9 finding still survives its own persistence failure (R4.17)
    with pytest.raises(F.FlowReplayError) as ei:
        F._quarantine(cache, "k", reason="the total returned 7, baseline 42")
    assert "42" in str(ei.value) and "7" in str(ei.value)


def test_the_unwritable_sidecar_error_is_not_advertised_as_retryable() -> None:
    """The belt-and-braces half of the same finding, pinned because the first draft had it inverted.

    `retryable=True` was copied from the READ twin, where it is right: `MetaUnreadableError` is only ever
    raised pre-write, so nothing has actuated. This class is raised from post-actuation positions too.
    Of the whole typed family only the classes that cannot follow an actuation are retryable, and this
    assertion states that rule rather than one class's flag.
    """
    assert F.MetaUnwritableError.retryable is False
    post_actuation = [F.WriteUnverifiedError, F.WriteReadbackError, F.MetaUnwritableError,
                      F.FlowQuarantineError]
    bad = [c.__name__ for c in post_actuation if c.retryable]
    assert not bad, (
        f"{bad} tell an autonomous agent to re-run a flow that may already have committed. Direction of "
        f"error decides this: a missed auto-retry costs one manual re-run; a wrong one double-submits.")


def test_replay_still_records_every_outcome_so_the_quiet_half_did_not_become_no_half() -> None:
    """The premise the two cells above rest on: `replay()` must still TRY to record on every path.

    Making the failure quiet is only correct if the call still happens — "never raises" is also
    satisfied by never being called, and that would take the fleet health view and the failure streak
    with it silently. Structural rather than behavioural because the paths are a browser-fixture each.
    """
    import ultracua

    src = (Path(ultracua.__file__).parent / "flows.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # BOTH functions since 1.5 split them: `replay()` is a wrapper whose only job is calling
    # `_RecordSink.finish` exactly once, and every outcome now lives in `_replay_body`. Scanning the
    # wrapper alone found ZERO sites and said so loudly — which is this guard working, not failing:
    # "never raises" is also satisfied by never being called, and so is "still records" by scanning
    # the wrong function.
    fns = [n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
           and n.name in ("replay", "_replay_body")]
    assert {f.name for f in fns} == {"replay", "_replay_body"}, (
        f"could not find both halves of replay ({sorted(f.name for f in fns)}) — a rename would make "
        f"this assertion vacuous")

    calls = [n for fn in fns for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_record_run"]
    assert len(calls) >= 8, (
        f"`replay`+`_replay_body` record the run at only {len(calls)} site(s). There are eight — success, "
        f"already-done (x2), quarantine, write_unverified, write_unreadable, the generic failure and "
        f"the crash handler. A missing one is a run the health view never hears about.")


def test_the_family_scan_is_not_empty() -> None:
    """The premise the parametrisation rests on. A `__subclasses__()` walk that finds nothing produces
    zero test cases and a green run — the shape that let a shard hole and an unfalsifiable matrix both
    ship here."""
    fam = _typed_error_family()
    assert len(fam) >= 8, f"only {len(fam)} typed errors discovered: {[c.__name__ for c in fam]}"
    assert F.MetaUnreadableError in fam, "the reported class must be in the scanned family"
