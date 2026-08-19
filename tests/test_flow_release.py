"""`flow release` must actually reach `release()` — R4.42, reshape-plan step 1.2.

`flow.py:206-208` refuses to re-author a flow whose write could not be attributed, and tells the
operator: *"Clear it with `flow release` once the cause is addressed."* That remedy was dead through the
CLI. `_flow_release` asked `health()` first and returned early for any status that is not
`"quarantined"`, and `health()` reports `"refused"` — so the one command the engine names printed
*"nothing to release"* and cleared nothing.

**Two shapes, and the obvious fix only reaches one of them.** Widening the check to accept `"refused"`
handles the uncached case, but `health()` only reports `"refused"` when the flow is NOT cached
(`flows.py:2073`): a refusal recorded against a flow that still has a recipe on disk reads `healthy` /
`never-run` / `failing`, and the widened check returns early exactly as before. That is why 1.2 DELETES
the pre-check rather than widening it, and why the second cell below exists — it is red under the
tempting fix as well as under the shipped code.

The CLI's message then has to come from what `release()` actually cleared rather than from what
`health()` guessed, which is the same "verdicts stored where evidence should be" shape the plan's root
cause 2 names.
"""

from __future__ import annotations

import time

import pytest

from ultracua import cli
from ultracua import flows as flows_mod
from ultracua.cache import CachedFlow, FlowCache, flow_key


def _spec(name: str = "transfer") -> flows_mod.FlowSpec:
    return flows_mod.FlowSpec(name=name, start_url="https://example.test/pay", goal="send money")


def _key(spec) -> str:
    return flow_key(spec.goal, spec.start_url, spec.scope)


def _saved(name: str = "transfer"):
    """A spec on disk plus its cache — the state `flow release --name <n>` starts from."""
    spec = _spec(name)
    flows_mod.save_spec(spec)
    return spec, FlowCache(), _key(spec)


def _cache_a_recipe(cache: FlowCache, key: str, spec) -> None:
    cache.put(CachedFlow(key=key, goal=spec.goal, start_url=spec.start_url, steps=[],
                         created_ts=time.time()))


# ---------------------------------------------------------------------------------------------------
# 1. The two shapes. Both RED against shipped behaviour; the second is red under the widened check too.

def test_release_clears_a_learn_time_refusal_on_an_UNCACHED_flow(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    spec, cache, key = _saved()
    cache.remember_refusal(key, "unattributed_write", "a write nothing could account for")
    assert cache.refusal(key) is not None, "premise: the refusal is on disk before the release"

    cli._flow_main(["release", "--name", spec.name])

    assert cache.refusal(key) is None, (
        "`flow release` left the learn-time refusal in place, so the remedy flow.py:206 names is dead "
        "through the CLI and the flow can never be re-authored"
    )
    assert "nothing to release" not in capsys.readouterr().out


def test_release_clears_a_refusal_recorded_against_a_flow_that_still_has_a_RECIPE(
    tmp_path, monkeypatch, capsys
) -> None:
    """The shape a widened status check does NOT reach.

    With a recipe on disk, `health()` never reports `"refused"` — the ladder at flows.py:2068 tests
    `not cached and refused is not None` — so `status` is `never-run` here and any pre-check keyed on
    the status returns early. Deleting the pre-check is what makes this pass.
    """
    monkeypatch.chdir(tmp_path)
    spec, cache, key = _saved()
    _cache_a_recipe(cache, key, spec)
    cache.remember_refusal(key, "unattributed_write", "a write nothing could account for")

    assert flows_mod.health(spec).status != "refused", (
        "premise: with a recipe on disk the status is NOT 'refused', which is why widening the "
        "pre-check to accept 'refused' would not reach this case"
    )

    cli._flow_main(["release", "--name", spec.name])

    assert cache.refusal(key) is None, "a refusal beside a cached recipe survived `flow release`"
    assert "nothing to release" not in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------------
# 2. What the command reports is what it DID, not what `health()` guessed.

def test_release_reports_what_it_actually_cleared(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    spec, cache, key = _saved()
    cache.remember_refusal(key, "unattributed_write", "why")

    res = flows_mod.release(spec, cache=cache)

    assert res.refusal is True and res.quarantine is False and res.baseline is False, res
    assert res.cleared == ("refusal",), res.cleared


def test_release_reports_a_quarantine_and_a_rebaseline_separately(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    spec, cache, key = _saved()
    flows_mod._quarantine(cache, key, reason="value out of contract")

    res = flows_mod.release(spec, cache=cache, rebaseline=True)

    assert res.quarantine is True and res.baseline is True, res
    assert res.refusal is False, "there was no refusal on record — reporting one would be a lie"
    assert set(res.cleared) == {"quarantine", "baseline"}, res.cleared


def test_a_flow_with_nothing_held_reports_nothing_cleared(tmp_path, monkeypatch, capsys) -> None:
    """The quiet direction, pinned as hard as the loud one: a release that clears nothing must SAY so.

    Without this the fix could be "always print released", which passes every cell above.
    """
    monkeypatch.chdir(tmp_path)
    spec, cache, key = _saved()

    res = flows_mod.release(spec, cache=cache)
    assert res.cleared == (), res

    cli._flow_main(["release", "--name", spec.name])
    assert "nothing to release" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------------
# 3. The behaviour that already worked must keep working — deleting a guard is where that gets lost.

def test_releasing_a_quarantine_through_the_cli_still_clears_it(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    spec, cache, key = _saved()
    # A recipe on disk, because that is the only state in which `health()` reports "quarantined" at all:
    # its ladder tests `not cached` FIRST, so an uncached quarantined flow reads "not-learned" — a THIRD
    # door into the same dead pre-check, and one more reason the fix is deletion rather than widening.
    _cache_a_recipe(cache, key, spec)
    flows_mod._quarantine(cache, key, reason="value out of contract")
    assert flows_mod.health(spec).status == "quarantined"

    cli._flow_main(["release", "--name", spec.name])

    assert flows_mod._load_meta(cache, key).quarantine is None
    out = capsys.readouterr().out
    assert "RE-ARMS" in out, "the re-arming warning is the point of the command; it must survive"


def test_release_still_clears_BOTH_holds_in_one_verb(tmp_path, monkeypatch) -> None:
    """R3.13's rule: one human verb clears the quarantine AND the engine's refusal memory."""
    monkeypatch.chdir(tmp_path)
    spec, cache, key = _saved()
    _cache_a_recipe(cache, key, spec)
    flows_mod._quarantine(cache, key, reason="value out of contract")
    cache.remember_refusal(key, "unattributed_write", "why")

    res = flows_mod.release(spec, cache=cache)

    assert flows_mod._load_meta(cache, key).quarantine is None
    assert cache.refusal(key) is None
    assert set(res.cleared) == {"quarantine", "refusal"}, res.cleared


def test_the_partial_release_ordering_still_holds(tmp_path, monkeypatch) -> None:
    """`release()`'s own comment: clearing the refusal FIRST, above a meta write that can raise, leaves
    the refusal gone (so a re-learn may fire the write again) while the quarantine stays. The fix must
    not reorder that, so this drives the error path and requires the refusal to have SURVIVED."""
    monkeypatch.chdir(tmp_path)
    spec, cache, key = _saved()
    flows_mod._quarantine(cache, key, reason="value out of contract")
    cache.remember_refusal(key, "unattributed_write", "why")

    def _boom(*a, **kw):
        raise OSError(32, "sharing violation")

    monkeypatch.setattr(flows_mod, "_update_meta", _boom)
    with pytest.raises(OSError):
        flows_mod.release(spec, cache=cache)

    assert cache.refusal(key) is not None, (
        "the refusal was cleared on a path where the quarantine write FAILED — the partial release "
        "`release()`'s own comment says the ordering exists to prevent"
    )
