"""The merge contract: what an OBSERVATION must prove before it may become the manifest.

`tests/.browser_tests.json` is the artifact whose loss costs a 36-minute suite run, and step 0.8 adds
a SECOND way to write it — from parts emitted by runs that already happened, including CI's four
shards. A second writer for an expensive artifact is exactly where this register expects the next
defect, so the contract is asserted here rather than trusted, and each cell names the case it exists
for.

The contract, in one sentence: a merge may only write a classification it OBSERVED, for THIS tree,
completely. Everything below is one of the three ways that fails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import _tiers as tier


def part(label, collected, selected=None, launches=(), outcomes=None, path="p.json",
         platform="linux"):
    """One observation, in the shape `--emit-marks` writes.

    `platform` is explicit and defaults to something that is NOT this host, because the merge treats a
    single-platform part set as unable to de-classify — so a helper that silently stamped
    `sys.platform` everywhere would make every de-classification cell depend on where it ran.
    """
    selected = list(collected) if selected is None else list(selected)
    reported = {i: "passed" for i in selected}
    if outcomes:
        reported.update(outcomes)
    rec = tier.observation(
        collected=collected, selected=selected, reported=reported,
        launches={i: 1 for i in launches}, exitstatus=0, seconds=1.0, label=label)
    rec["platform"] = platform
    return (Path(path), rec)


def both_platforms(*parts):
    """The same parts, restamped so the set covers two platforms.

    De-classification requires evidence from more than one platform (see the hold cell below), so a
    cell that means to exercise browser -> fast has to say so.
    """
    out = []
    for n, (path, d) in enumerate(parts):
        d = dict(d)
        d["platform"] = "linux" if n % 2 == 0 else "win32"
        out.append((path, d))
    return out


TREE = ["a::1", "a::2", "b::1", "b::2"]


# ---------------------------------------------------------------------------------------------------
# 1. The happy path, and the union rule.

def test_two_shards_merge_into_one_classification() -> None:
    """The shape CI produces: each part ran half, and between them they saw everything."""
    parts = [
        part("ubuntu-1", TREE, selected=["a::1", "a::2"], launches=["a::1"]),
        part("ubuntu-2", TREE, selected=["b::1", "b::2"], launches=["b::2"]),
    ]
    browser, fast = tier.merge_observations(parts, TREE)
    assert browser == ["a::1", "b::2"]
    assert fast == ["a::2", "b::1"]


def test_a_launch_anywhere_makes_it_a_browser_test() -> None:
    """The union is the SAFE direction and the asymmetry is the reason.

    A test wrongly in the browser tier merely runs slowly. One wrongly in the fast tier RAISES
    (`ChromiumInFastTier`, a BaseException). So a launch seen on ONE platform classifies for both —
    which matters because the two OSes do not have to agree.
    """
    parts = [
        part("windows-1", TREE, selected=TREE, launches=["a::1"]),
        part("ubuntu-1", TREE, selected=TREE, launches=[]),          # same tests, no launch here
    ]
    browser, _fast = tier.merge_observations(parts, TREE)
    assert browser == ["a::1"], "a launch observed on one arm must classify on both"


# ---------------------------------------------------------------------------------------------------
# 2. IDENTITY — the parts must describe THIS tree.

def test_parts_describing_a_different_tree_are_refused_in_both_directions() -> None:
    """The `pull_request` trap, which a commit sha cannot catch.

    GitHub does not test your branch on a PR — it tests `refs/pull/N/merge`. Verified: run
    32380417760 reports headSha 5d47964 while its checkout says `HEAD is now at 2c908fc Merge 5d47964
    into 4f7ac67`. So a run correctly IDENTIFIED by your commit collected a different TREE, and once
    main has moved that tree has tests you do not have.

    Refusing on the id set catches it, and catches a stale part, a part from `main`, and a tree edited
    after the push, with no extra sensors.
    """
    theirs = TREE + ["c::added_on_main"]
    with pytest.raises(tier.MergeRefused) as exc:
        tier.merge_observations([part("x", theirs, selected=theirs)], TREE)
    assert "c::added_on_main" in str(exc.value)
    assert "refs/pull" in str(exc.value), "it must say WHY this normally happens, or nobody can act"

    # ...and the other direction: this tree has a test the observation never saw.
    with pytest.raises(tier.MergeRefused) as exc2:
        tier.merge_observations([part("x", TREE[:-1], selected=TREE[:-1])], TREE)
    assert TREE[-1] in str(exc2.value)


# ---------------------------------------------------------------------------------------------------
# 3. COMPLETENESS — every test must have been RUN by some part.

def test_a_missing_shard_is_refused_rather_than_classified_from_nothing() -> None:
    """The failure mode the whole design must not have: one artifact absent, three present.

    Both parts COLLECT the whole tree (they are shards of it), so identity passes. But nothing ran
    `b::*`, and classifying them from no evidence would silently move real browser tests into the
    fast tier, where they RAISE.
    """
    only_first_half = [part("ubuntu-1", TREE, selected=["a::1", "a::2"], launches=["a::1"])]
    with pytest.raises(tier.MergeRefused) as exc:
        tier.merge_observations(only_first_half, TREE)
    msg = str(exc.value)
    assert "b::1" in msg and "RUN by none" in msg
    assert "ubuntu-1" in msg, "it must name the parts it DID have, so the gap is obvious"


def test_a_truncated_part_is_refused() -> None:
    """A job killed at the wall, or a `--collect-only` part: selected ids that never reported."""
    p, d = part("ubuntu-1", TREE, selected=TREE)
    d["reported"].pop("b::2")
    with pytest.raises(tier.MergeRefused) as exc:
        tier.merge_observations([(p, d)], TREE)
    assert "TRUNCATED" in str(exc.value) and "b::2" in str(exc.value)


def test_an_empty_part_cannot_classify_anything() -> None:
    """Every set-equality clause is satisfied by the empty set, so drive it explicitly."""
    with pytest.raises(tier.MergeRefused):
        tier.merge_observations([part("empty", [], selected=[])], TREE)
    with pytest.raises(tier.MergeRefused):
        tier.merge_observations([], TREE)
    with pytest.raises(tier.MergeRefused, match="collected NO tests"):
        tier.merge_observations([part("x", TREE)], [])


# ---------------------------------------------------------------------------------------------------
# 4. A RE-RUN, which is the most common CI action there is.

def test_a_rerun_keeps_the_evidence_of_every_attempt() -> None:
    """"Re-run failed jobs" leaves two artifacts for one (os, shard) — measured on run 31677764463,
    which held `resource-samples-windows-latest-1-1` AND `...-1-2`. Treating that as an error would
    refuse exactly when a human most needs the tool.

    THE FIRST DRAFT OF THIS CELL ASSERTED THE COUNTEREXAMPLE, and it is worth keeping written down.
    It said `assert browser == []` — "the LATER attempt observed no launch, so its answer is the one
    that counts" — three lines below a neighbour declaring the opposite rule, that a launch seen
    ANYWHERE classifies. Last-wins on launches measurably moves a test that launched on attempt 1
    into the fast tier, where it RAISES.

    The two fields are not symmetrical. `selected`/`reported` are a COMPLETENESS record and the later
    attempt is authoritative — that is the whole reason to reconcile, since the earlier attempt of a
    re-run is usually truncated (it was killed; that is why it was re-run). `launches` are EVIDENCE,
    and evidence does not expire.
    """
    attempt1 = part("ubuntu-1", TREE, selected=["a::1", "a::2"], launches=["a::1"], path="a1.json")
    attempt2 = part("ubuntu-1", TREE, selected=["a::1", "a::2"], launches=[], path="a2.json")
    second = part("ubuntu-2", TREE, selected=["b::1", "b::2"], launches=[])

    browser, _fast = tier.merge_observations([attempt1, attempt2, second], TREE)
    assert browser == ["a::1"], "a launch observed on attempt 1 must survive a quieter attempt 2"

    # ...and order must not decide, which is what a last-wins implementation would make it do.
    browser2, _ = tier.merge_observations([attempt2, attempt1, second], TREE)
    assert browser2 == browser, "the reconciliation must be order-independent for launches"

    # The completeness record still comes from the LAST attempt: attempt 1 truncated, attempt 2 whole.
    p1, d1 = part("ubuntu-1", TREE, selected=["a::1", "a::2"], launches=["a::1"], path="t1.json")
    d1["reported"].pop("a::2")                       # attempt 1 was killed part-way
    ok = tier.merge_observations([(p1, d1), attempt2, second], TREE)
    assert ok[0] == ["a::1"], (
        "a truncated EARLIER attempt must be superseded by the complete later one rather than "
        "refusing the whole merge — that is what reconciliation is for")


# ---------------------------------------------------------------------------------------------------
# 5. DE-CLASSIFICATION needs a passing observation behind it.

def test_a_test_that_died_in_setup_keeps_its_browser_mark(tmp_path, monkeypatch) -> None:
    """The subtle one, and the reason `reported` carries an OUTCOME rather than just a key set.

    A test that errors in setup — a raising fixture, an unreachable browser — REPORTS and LAUNCHES
    NOTHING. Read only the launch count and that is identical to "it stopped needing a browser". So a
    red shard would quietly move its whole browser population into the fast tier, where every one of
    them then RAISES.

    Direction of error decides which way to be wrong: keeping a stale browser mark costs fast-tier
    size; dropping a real one breaks the tier. So browser -> fast requires a PASS.
    """
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"total": 4, "browser": ["a::1"], "fast": ["a::2", "b::1", "b::2"]}),
                        encoding="utf-8")
    monkeypatch.setattr(tier, "MANIFEST", manifest)

    dead = both_platforms(part("ubuntu-1", TREE, selected=TREE, launches=[], outcomes={"a::1": "failed"}),
                          part("win-1", TREE, selected=TREE, launches=[], path="w.json"))
    browser, fast = tier.merge_observations(dead, TREE)
    assert browser == ["a::1"], "a FAILED observation must not de-classify a known browser test"
    assert "a::1" not in fast

    # ...and a PASSING observation of the same shape genuinely de-classifies. Without this half the
    # cell above is satisfied by never de-classifying anything, which is the regression that has
    # already shipped once in this repo (the "must remain learnable" clause).
    alive = both_platforms(part("ubuntu-1", TREE, selected=TREE, launches=[]),
                           part("win-1", TREE, selected=TREE, launches=[], path="w.json"))
    browser2, fast2 = tier.merge_observations(alive, TREE)
    assert browser2 == [] and "a::1" in fast2, "a clean run MUST be able to return a test to the tier"


# ---------------------------------------------------------------------------------------------------
# 6. A part that cannot be read is an ERROR, never a skip.

def test_a_corrupt_part_names_the_file_instead_of_being_skipped(tmp_path) -> None:
    """`scripts/manifest_cost.py` states the rule for its ledger ("a corrupt ledger is loud, never
    skipped"). Here it matters more: a silently dropped part means the merge writes a manifest missing
    everything that part observed, and reports success."""
    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(tier.MergeRefused, match="broken.json"):
        tier.load_observation(bad)

    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schema": 0, "label": "x"}), encoding="utf-8")
    with pytest.raises(tier.MergeRefused, match="schema"):
        tier.load_observation(old)

    incomplete = tmp_path / "partial.json"
    incomplete.write_text(json.dumps({"schema": tier.SCHEMA, "label": "x"}), encoding="utf-8")
    with pytest.raises(tier.MergeRefused, match="missing"):
        tier.load_observation(incomplete)


# ---------------------------------------------------------------------------------------------------
# 7. The manifest override exists for candidate-then-swap and for nothing else.

def test_the_manifest_default_is_the_committed_path_whatever_the_environment() -> None:
    """The override must not become an ambient knob — but this must test the RESOLUTION, not the
    environment, and the first draft got that wrong in a way worth keeping written down.

    It asserted `not os.getenv("ULTRACUA_TIER_MANIFEST")`. That is FALSE exactly when the override is
    doing its job: `tier_marks.py merge` validates the candidate by running this very suite with the
    variable set, so the cell failed the validation it was supposed to protect — and, because the
    design writes a candidate rather than the real file, the run reported "the candidate did NOT
    converge. The committed manifest is UNTOUCHED." A cell asserting an ambient fact about its own
    process is not a property of the code.

    What is a property of the code: the DEFAULT is the committed path, and the override reads the
    variable it claims to. Both hold under either environment.
    """
    assert tier._DEFAULT_MANIFEST.name == ".browser_tests.json"
    assert tier._DEFAULT_MANIFEST.parent.name == "tests"
    assert tier._MANIFEST_ENV == "ULTRACUA_TIER_MANIFEST"

    import os
    if not os.getenv(tier._MANIFEST_ENV):
        assert tier.MANIFEST == tier._DEFAULT_MANIFEST, (
            "no override is set, yet the live manifest is not the committed one")


# ---------------------------------------------------------------------------------------------------
# 8. THE PRODUCER. Every cell above builds parts in memory; this one drives the REAL hooks.

class _Report:
    def __init__(self, nodeid, when, failed=False, skipped=False):
        self.nodeid, self.when, self.failed, self.skipped = nodeid, when, failed, skipped


class _Item:
    def __init__(self, nodeid):
        self.nodeid = nodeid


class _Config:
    def __init__(self, emit):
        self._o = {"--store-browser-marks": False, "--tier": "all", "--emit-marks": emit}

    def getoption(self, name):
        return self._o[name]      # KeyError on an unknown option, deliberately — see test_manifest_cost


class _Session:
    def __init__(self, ids, emit):
        self.config = _Config(emit)
        self.items = [_Item(i) for i in ids]


def test_the_real_hooks_produce_a_part_the_merge_can_read(tmp_path, monkeypatch) -> None:
    """The round trip, through the code a run actually executes.

    Every other cell in this file calls `tier.observation()` in memory, so all of them would stay green
    if `pytest_runtest_logreport` stopped recording outcomes, if `pytest_sessionfinish` stopped writing
    the file, or if the two disagreed about the shape. That is S14's inert-stub shape: a helper that
    diverges from the producer proves only that the helper is self-consistent.

    It also ARMS the worst-outcome rule, which nothing else drives. pytest emits three reports per test
    (setup / call / teardown); a test that PASSES setup and FAILS the call must come out "failed", so
    inverting the comparator in `pytest_runtest_logreport` turns this red. Without it, that comparator
    could be flipped and all ten cells above would still pass while a red shard silently de-classified
    its whole browser population.
    """
    import conftest

    ids = ["m.py::ok", "m.py::boom", "m.py::skipped"]
    monkeypatch.setattr(tier, "COLLECTED", list(ids))
    monkeypatch.setattr(tier, "PER_TEST", {"m.py::ok": 2, "m.py::boom": 0, "m.py::skipped": 0})
    monkeypatch.setattr(tier, "REPORTED", {})
    monkeypatch.setattr(conftest, "_STARTED", [0.0])

    # pytest's real three-phase stream, including the case the WORST-outcome rule exists for.
    for r in (_Report("m.py::ok", "setup"), _Report("m.py::ok", "call"), _Report("m.py::ok", "teardown"),
              _Report("m.py::boom", "setup"), _Report("m.py::boom", "call", failed=True),
              _Report("m.py::boom", "teardown"),
              _Report("m.py::skipped", "setup", skipped=True), _Report("m.py::skipped", "teardown")):
        conftest.pytest_runtest_logreport(r)

    assert tier.REPORTED["m.py::boom"] == "failed", (
        "a test that passed setup and failed the call was not recorded as failed — the worst-outcome "
        "rule is inverted, and a red shard would be treated as a clean observation")
    assert tier.REPORTED["m.py::ok"] == "passed"
    assert tier.REPORTED["m.py::skipped"] == "skipped"

    out = tmp_path / "part.json"
    conftest.pytest_sessionfinish(_Session(ids, str(out)), 1)

    # ...and the file the producer wrote must satisfy the consumer's own loader and contract.
    loaded = tier.load_observation(out)
    assert loaded["launches"] == {"m.py::ok": 2}, "only launching ids are carried"
    assert loaded["reported"]["m.py::boom"] == "failed"
    assert set(loaded["selected"]) == set(ids) and set(loaded["collected"]) == set(ids)

    browser, fast = tier.merge_observations([(out, loaded)], ids)
    assert browser == ["m.py::ok"] and fast == ["m.py::boom", "m.py::skipped"]


def test_a_single_platform_part_set_may_not_declassify(tmp_path, monkeypatch) -> None:
    """The local path is single-platform BY CONSTRUCTION, and that is the hazard.

    MEASURED before the hold existed: two windows parts covering the whole tree pass identity and
    coverage, the merge SUCCEEDS, and a test that only launches on ubuntu moves browser -> fast — where
    CI's ubuntu `fast` job then RAISES on it. Candidate-then-swap cannot catch it either: the
    fixed-point loop runs `--tier fast` on the producing platform, so it never exercises the arm that
    would object.

    A HOLD rather than a refusal, so there is no D0 exposure — new tests still classify and new
    launches still promote. Only the unsafe direction is withheld, and it says so.
    """
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(
        {"total": 4, "browser": ["a::1"], "fast": ["a::2", "b::1", "b::2"]}), encoding="utf-8")
    monkeypatch.setattr(tier, "MANIFEST", manifest)

    one_arm = [part("win-1", TREE, selected=["a::1", "a::2"], platform="win32"),
               part("win-2", TREE, selected=["b::1", "b::2"], platform="win32", path="w2.json")]
    notes = []
    browser, fast = tier.merge_observations(one_arm, TREE, notes)
    assert "a::1" in browser, "a single-platform observation must not de-classify"
    assert "a::1" not in fast
    assert notes and "HELD 1" in notes[0] and "one platform" in notes[0], (
        "the hold must be VISIBLE; a silent one is indistinguishable from the merge simply working")

    # ARM THE OTHER DIRECTION, or "hold everything" would satisfy the assertion above.
    two_arms = both_platforms(*one_arm)
    notes2 = []
    browser2, fast2 = tier.merge_observations(two_arms, TREE, notes2)
    assert browser2 == [] and "a::1" in fast2, "with both arms present the de-classification must land"
    assert not notes2, "nothing should be held when the evidence is complete"
