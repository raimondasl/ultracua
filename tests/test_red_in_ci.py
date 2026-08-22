"""The self-test for reshape-plan step 0.4b -- a UNIT TEST OF THE VERDICT FUNCTION.

`scripts/red_in_ci.py` decides whether a PR's new tests are red against the base's `src/`. It is a
gate, so its own decision function is the thing that must not be wrong, and it is the half that can
be tested without a worktree, two collections and four pytest runs.

The impure half (git, pytest, junit) gets cells too, but only for the two things that have actually
been silent failures elsewhere in this repo: a node-id round trip that quietly maps to the wrong id,
and a collection ERROR being told from a FAILURE.

EVERY CELL HERE IS ARMED. `tests/_arming.py` mutates the function each cell names and requires the
cell to go red -- because `scripts/` is at the repo ROOT and `scripts/prove_red.py` structurally
cannot reach it (R4.77), exactly as it cannot reach `benchmarks/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import red_in_ci as R  # noqa: E402

from _arming import assert_red, mutate_function  # noqa: E402


# ---------------------------------------------------------------------------------------------------
# The vocabulary. Quiet is an allowlist, so the loud set is DERIVED and a verdict added tomorrow is
# loud by default rather than satisfying neither channel (R3.9/CLI-1).

def test_quiet_is_an_allowlist_and_every_loud_verdict_names_a_remedy() -> None:
    assert R.QUIET_VERDICTS <= set(R.VERDICTS), "a quiet name that is not a verdict"
    loud = [v for v in R.VERDICTS if v not in R.QUIET_VERDICTS]
    assert loud, "if nothing is loud this gate cannot fail and is not a gate"
    missing = [v for v in loud if not R.REMEDY.get(v)]
    assert not missing, (
        f"loud verdict(s) with no remedy: {missing}. A refusal that does not say what to do about it "
        f"is the channel that gets `|| true`d, and takes the others dark with it.")
    # And the inverse: a remedy for something that cannot happen is a stale instruction.
    assert not set(R.REMEDY) - set(loud), "REMEDY names a verdict that is quiet or does not exist"
    print(f"quiet={sorted(R.QUIET_VERDICTS)}  loud={loud}")


def test_every_verdict_the_function_can_return_is_in_the_declared_vocabulary() -> None:
    """DERIVED from the source, not from the cells below -- otherwise the set is only as good as the
    cells that happen to reach it, which is how `flow run-all`'s third bucket stayed invisible."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(R.verdict))
    names = {n.args[0].value for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "Verdict"
             and n.args and isinstance(n.args[0], ast.Constant)}
    assert names, "the derivation found no Verdict(...) construction -- it has gone stale"
    assert names <= set(R.VERDICTS), f"undeclared verdict(s): {sorted(names - set(R.VERDICTS))}"
    print(f"{len(names)} verdict(s) reachable in verdict(): {sorted(names)}")


# ---------------------------------------------------------------------------------------------------
# classify_id -- what ONE new id proves, from its outcome on the branch and on the base.

# The full cross product. `branch != passed` collapses to `unusable` whatever the base did, and that
# is the point: a cell that is red on its own branch cannot contribute evidence, however red it also
# is against the base. Single-run designs score exactly those as guards.
CLASSIFY = [(b, m) for b in R.OUTCOMES for m in R.OUTCOMES]


@pytest.mark.parametrize("branch,base", CLASSIFY, ids=[f"{b}/{m}" for b, m in CLASSIFY])
def test_one_id_is_classified_from_both_runs_and_never_from_one(branch: str, base: str) -> None:
    got = R.classify_id(branch, base)
    if branch != "passed":
        expect = "unusable"
    elif base == "failed":
        expect = "guards"
    elif base == "passed":
        expect = "no_guard"
    else:
        expect = "inconclusive"
    assert got == expect, f"{branch}/{base} classified {got!r}, expected {expect!r}"
    assert got in R.STATES


def test_an_import_error_against_the_base_is_never_a_kill() -> None:
    """The critic's clause on 0.4b, and the reason the two runs report `error` separately at all.

    A PR that adds `src/ultracua/foo.py` gives its new tests nothing to import from the base. They
    die at COLLECTION, pytest exits non-zero, and a design that reads the exit code scores it RED --
    a green gate over a test that has never executed a line of the code it claims to guard.
    """
    assert R.classify_id("passed", "error") == "inconclusive"
    assert R.classify_id("passed", "failed") == "guards"
    assert R.classify_id("passed", "skipped") == "inconclusive", "a skip runs nothing either"
    assert R.classify_id("passed", "missing") == "inconclusive"


def test_an_outcome_outside_the_vocabulary_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="outcome must be one of"):
        R.classify_id("passed", "xpassed")
    with pytest.raises(ValueError, match="outcome must be one of"):
        R.classify_id("", "passed")


# ---------------------------------------------------------------------------------------------------
# verdict -- the gate itself.

def _v(**kw):
    base = dict(src_touched=True, diff_adds_test_defs=True, new_ids=["a"], states={"a": "guards"})
    base.update(kw)
    return R.verdict(**base)


def test_a_src_touching_pr_whose_new_tests_all_pass_against_the_base_FAILS() -> None:
    """0.4b's whole sentence, in one cell."""
    v = _v(states={"a": "no_guard", "b": "no_guard"}, new_ids=["a", "b"])
    assert v.name == "all_green"
    assert not v.quiet, "the finding this job exists for must be LOUD"
    assert v.remedy


def test_one_genuine_red_is_enough_and_the_job_goes_quiet() -> None:
    v = _v(states={"a": "guards", "b": "no_guard", "c": "inconclusive"}, new_ids=["a", "b", "c"])
    assert v.name == "red" and v.quiet
    assert v.counts["guards"] == 1 and v.counts["inconclusive"] == 1, (
        "the counts must still carry the inconclusive fact -- a quiet verdict may not take it dark")


def test_a_pr_that_touches_no_src_is_quiet_and_nothing_is_run() -> None:
    v = R.verdict(src_touched=False, diff_adds_test_defs=True, new_ids=[], states={})
    assert v.name == "no_src_change" and v.quiet


def test_the_two_derivations_disagreeing_is_a_HARNESS_failure_and_outranks_everything() -> None:
    """`new_ids` differences two collections; `diff_adds_test_defs` reads the diff text. They are
    independent, and the extractor being broken makes every verdict below a claim about an unknown
    set -- B3's channel-0 rule, where 13 unscored scenarios gated green on the one that scored."""
    v = R.verdict(src_touched=True, diff_adds_test_defs=True, new_ids=[], states={})
    assert v.name == "harness" and not v.quiet


def test_an_id_that_was_never_classified_is_a_harness_failure_not_a_pass() -> None:
    v = _v(new_ids=["a", "b"], states={"a": "guards"})
    assert v.name == "harness", "a red must not be reported over an incomplete set"
    assert "b" in v.detail


def test_new_tests_that_do_not_pass_on_their_own_branch_prove_nothing() -> None:
    v = _v(new_ids=["a", "b"], states={"a": "unusable", "b": "unusable"})
    assert v.name == "harness" and not v.quiet
    assert "THIS branch" in v.detail


def test_a_src_change_with_no_new_id_is_quiet_and_names_its_own_residual() -> None:
    """NOT loud, deliberately. The sensor is id-level, so a src change guarded by editing an EXISTING
    cell's body is invisible to it -- 1.3 did exactly that to `test_ratchets.py`. Making this loud
    would fire on every such PR with nothing to acknowledge it, which is the D0 shape."""
    v = R.verdict(src_touched=True, diff_adds_test_defs=False, new_ids=[], states={})
    assert v.name == "no_new_tests" and v.quiet
    assert "NOT adjudicated" in v.detail, "a residual that is not stated is a residual nobody knows"


def test_inconclusive_is_loud_and_names_the_registered_mutation_as_its_remedy() -> None:
    v = _v(new_ids=["a"], states={"a": "inconclusive"})
    assert v.name == "inconclusive" and not v.quiet
    assert "tests/mutations/" in v.remedy and "prove_red" in v.remedy


@pytest.mark.parametrize("states,expect", [
    ({"a": "no_guard"}, "acknowledged_by_mutation"),
    ({"a": "inconclusive"}, "acknowledged_by_mutation"),
    ({"a": "guards"}, "red"),
    ({"a": "unusable"}, "harness"),
], ids=["all_green->discharged", "inconclusive->discharged", "red stays red", "harness is NOT discharged"])
def test_a_shipped_mutation_discharges_the_two_findings_and_nothing_else(states, expect) -> None:
    """The acknowledgement, and the exact bound on it.

    A registered mutation is proven red by `prove_red` against a scratch copy, so it is a STRONGER
    instrument than this job and reaches the two populations this job structurally cannot swap
    (`benchmarks/`, `scripts/`). It discharges the two coverage findings. It does NOT discharge
    `harness`: evidence from elsewhere does not repair a derivation that could not run.
    """
    v = _v(new_ids=["a"], states=states, mutations_shipped=True)
    assert v.name == expect


def test_the_acknowledgement_is_reported_as_itself_and_not_folded_into_a_pass() -> None:
    v = _v(new_ids=["a"], states={"a": "no_guard"}, mutations_shipped=True)
    assert v.name == "acknowledged_by_mutation" and v.quiet
    assert v.name != "red", "a discharge that renders as a red is a discharge nobody can audit"
    assert "prove_red" in v.detail


# ---------------------------------------------------------------------------------------------------
# The impure half, where a silent mapping error would make everything above meaningless.

@pytest.mark.parametrize("classname,name,expect", [
    ("tests.test_obs", "test_usage_since_delta", "tests/test_obs.py::test_usage_since_delta"),
    ("tests.test_a.TestB", "test_c", "tests/test_a.py::TestB::test_c"),
    ("tests.sub.test_a", "test_b", "tests/sub/test_a.py::test_b"),
    ("tests.test_a", "test_b[a real cost]", "tests/test_a.py::test_b[a real cost]"),
])
def test_a_junit_testcase_maps_back_to_the_node_id_pytest_would_print(classname, name, expect) -> None:
    """A wrong mapping makes every id `missing`, which the gate reads as a harness failure -- loud,
    but for the wrong reason. The parametrised row is load-bearing: a new parametrize CELL is a new
    id, which is how this instrument sees a table gaining a row."""
    assert R._node_id(classname, name) == expect


def test_the_node_id_round_trip_holds_against_a_real_pytest_run(tmp_path: Path) -> None:
    """Not a hand-written fixture -- pytest's OWN junit writer, so the mapping cannot drift from it.

    A cell that asserts its author's idea of a format is worth nothing when the format moves. Run
    against a throwaway file rather than this repo's suite, deliberately: pointing it at `tests/`
    would put a second full conftest import inside the fast tier, which is already over its budget.
    """
    import subprocess
    (tmp_path / "test_sample.py").write_text(
        "import pytest\n"
        "def test_plain(): pass\n"
        "@pytest.mark.parametrize('x', ['a real cost'])\n"
        "def test_param(x): pass\n"
        "class TestGroup:\n"
        "    def test_inside(self): pass\n"
        "def test_fails(): assert False\n"
        "@pytest.mark.skip(reason='by design')\n"
        "def test_skips(): pass\n", encoding="utf-8")
    junit = tmp_path / "j.xml"
    subprocess.run([sys.executable, "-m", "pytest", "test_sample.py", "-q", "--tb=no",
                    "-p", "no:cacheprovider", f"--junitxml={junit}"],
                   cwd=tmp_path, capture_output=True, text=True)
    got = R.parse_junit(junit, [
        "test_sample.py::test_plain",
        "test_sample.py::test_param[a real cost]",
        "test_sample.py::TestGroup::test_inside",
        "test_sample.py::test_fails",
        "test_sample.py::test_skips",
        "test_sample.py::never_written",
    ])
    assert got == {
        "test_sample.py::test_plain": "passed",
        "test_sample.py::test_param[a real cost]": "passed",
        "test_sample.py::TestGroup::test_inside": "passed",
        "test_sample.py::test_fails": "failed",
        "test_sample.py::test_skips": "skipped",
        # An id pytest never mentioned is `missing`, not silently `passed` -- which is what makes an
        # incomplete run a HARNESS verdict rather than a quiet red.
        "test_sample.py::never_written": "missing",
    }, got


def test_a_collection_error_is_an_error_and_a_failure_is_a_failure(tmp_path: Path) -> None:
    """They exit pytest the same way and mean opposite things here. A `<failure>` is the evidence
    this job wants; an `<error>` is a test that never ran."""
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuites><testsuite>'
        '<testcase classname="tests.test_x" name="test_a"><failure>boom</failure></testcase>'
        '<testcase classname="tests.test_x" name="test_b"><skipped/></testcase>'
        '<testcase classname="" name="tests/test_y.py"><error>ImportError</error></testcase>'
        '</testsuite></testsuites>', encoding="utf-8")
    got = R.parse_junit(junit, ["tests/test_x.py::test_a", "tests/test_x.py::test_b",
                                "tests/test_y.py::test_c", "tests/test_z.py::test_d"])
    assert got["tests/test_x.py::test_a"] == "failed"
    assert got["tests/test_x.py::test_b"] == "skipped"
    assert got["tests/test_y.py::test_c"] == "error", (
        "a file-level collection error must reach every id in that file -- otherwise an ImportError "
        "against the base reads as `missing`, which is a harness failure rather than the "
        "inconclusive it actually is")
    assert got["tests/test_z.py::test_d"] == "missing"


def test_a_collection_error_reaches_only_its_OWN_files_ids() -> None:
    """Path-SEGMENT equality, not `in`. A substring test makes `tests/test_a.py` match
    `other/tests/test_a.py`, so one file's ImportError would be published as another file's -- `error`
    (inconclusive, ship a mutant) where the honest answer is `missing` (this harness asked for
    something that does not exist)."""
    import tempfile
    junit = Path(tempfile.mkdtemp()) / "j.xml"
    junit.write_text(
        '<testsuites><testsuite>'
        '<testcase classname="" name="other/tests/test_a.py"><error>ImportError</error></testcase>'
        '</testsuite></testsuites>', encoding="utf-8")
    got = R.parse_junit(junit, ["tests/test_a.py::t", "other/tests/test_a.py::t",
                                "tests/test_ab.py::t"])
    assert got == {"tests/test_a.py::t": "missing",
                   "other/tests/test_a.py::t": "error",
                   "tests/test_ab.py::t": "missing"}, got


def test_a_collected_line_must_look_like_a_node_id() -> None:
    """`pytest -q --collect-only` also prints a warnings summary, and a warning that NAMES a node id
    would otherwise enter `new_ids` as a test present in neither run -- which the gate reads as a
    harness failure. Loud, but for something nobody could act on."""
    matched = [l for l in [
        "tests/test_a.py::test_b",
        "tests/test_a.py::TestC::test_d[a real cost]",
        "  /path/site-packages/x.py:12: PytestUnraisableWarning: tests/test_a.py::test_b leaked",
        "1612 tests collected in 3.42s",
        "",
        "ERROR tests/test_a.py - ImportError: no module named q",
    ] if R._NODE_ID.match(l.strip())]
    assert matched == ["tests/test_a.py::test_b",
                       "tests/test_a.py::TestC::test_d[a real cost]"], matched


def test_the_ids_are_chunked_so_a_command_line_has_a_length(monkeypatch, tmp_path: Path) -> None:
    """Windows caps a command at 8191 characters and an id here averages ~90. Unchunked, a PR adding
    a hundred parametrize cells would fail with an OS error instead of returning a verdict."""
    batches: list = []

    def _fake_run(argv, **kw):
        batches.append([a for a in argv if "::" in a])
        return None

    monkeypatch.setattr(R.subprocess, "run", _fake_run)
    monkeypatch.setattr(R, "parse_junit", lambda p, ids: {i: "passed" for i in ids})
    monkeypatch.setattr(R, "_CHUNK", 3)
    ids = [f"tests/test_x.py::test_{n}" for n in range(7)]
    got = R.run_ids(ids, src_from=None, junit=tmp_path / "j.xml")
    assert [len(b) for b in batches] == [3, 3, 1], batches
    assert [i for b in batches for i in b] == ids, "chunking dropped or reordered ids"
    assert set(got) == set(ids), "a chunk's outcomes did not reach the merged result"


def test_the_diff_scan_reads_ADDED_lines_only() -> None:
    """A removed `def test_` or an unchanged context line must not satisfy the cross-check, or the
    harness guard fires on a PR that deleted a test."""
    import inspect
    src = inspect.getsource(R.diff_adds_test_defs)
    assert '--unified=0' in src, "context lines start with a space, but zero context is cheaper"
    assert 'line.startswith("+")' in src and 'line.startswith("+++")' in src, (
        "the `+++ b/path` header starts with `+` too and would match a path containing `def test_`")


# ---------------------------------------------------------------------------------------------------
# ARMING. Every guard above is mutated and must go RED. `scripts/` cannot be reached by prove_red
# (R4.77), so the discipline is kept in-process instead of skipped.

def test_the_gate_pin_notices_the_finding_going_quiet(monkeypatch) -> None:
    mutate_function(monkeypatch, R, "verdict",
                    'return Verdict("all_green",', 'return Verdict("red",')
    print(assert_red(test_a_src_touching_pr_whose_new_tests_all_pass_against_the_base_FAILS))


def test_the_gate_pin_notices_a_single_run_design(monkeypatch) -> None:
    """Delete the branch precondition and every `unusable` becomes a verdict about the base."""
    mutate_function(monkeypatch, R, "classify_id",
                    'if branch != "passed":\n        return "unusable"', 'if False:\n        pass')
    print(assert_red(test_one_id_is_classified_from_both_runs_and_never_from_one, "failed", "failed"))


def test_the_gate_pin_notices_an_import_error_scored_as_a_kill(monkeypatch) -> None:
    mutate_function(monkeypatch, R, "classify_id",
                    'if base == "failed":', 'if base in ("failed", "error"):')
    print(assert_red(test_an_import_error_against_the_base_is_never_a_kill))


def test_the_gate_pin_notices_the_harness_check_being_outranked(monkeypatch) -> None:
    """Move the cross-check below the findings and a broken extractor reports `no_new_tests`."""
    mutate_function(monkeypatch, R, "verdict",
                    'if diff_adds_test_defs and not new_ids:', 'if False:')
    print(assert_red(test_the_two_derivations_disagreeing_is_a_HARNESS_failure_and_outranks_everything))


def test_the_gate_pin_notices_an_unclassified_id_being_waved_through(monkeypatch) -> None:
    mutate_function(monkeypatch, R, "verdict",
                    'unseen = [i for i in new_ids if i not in states]', 'unseen = []')
    print(assert_red(test_an_id_that_was_never_classified_is_a_harness_failure_not_a_pass))


def test_the_gate_pin_notices_the_discharge_reaching_harness(monkeypatch) -> None:
    """The exact bound on the acknowledgement: it may not repair a derivation that could not run."""
    mutate_function(monkeypatch, R, "verdict",
                    'if not counts["inconclusive"] and not counts["no_guard"]:', 'if False:')
    print(assert_red(test_a_shipped_mutation_discharges_the_two_findings_and_nothing_else,
                     {"a": "unusable"}, "harness"))


def test_the_gate_pin_notices_a_loud_verdict_losing_its_remedy(monkeypatch) -> None:
    mutate_function(monkeypatch, R, "verdict",
                    'return Verdict("inconclusive",', 'return Verdict("harness",')
    print(assert_red(test_inconclusive_is_loud_and_names_the_registered_mutation_as_its_remedy))


def test_the_gate_pin_notices_a_residual_that_stops_being_stated(monkeypatch) -> None:
    mutate_function(monkeypatch, R, "verdict",
                    '"src/ changed and this PR adds no new test id. NOT adjudicated: an existing "',
                    '"src/ changed and this PR adds no new test id. "')
    print(assert_red(test_a_src_change_with_no_new_id_is_quiet_and_names_its_own_residual))


def test_the_node_id_pin_notices_a_wrong_mapping(monkeypatch) -> None:
    mutate_function(monkeypatch, R, "_node_id",
                    'return "::".join([file_part, *rest, name])',
                    'return "::".join([file_part, name])')
    print(assert_red(test_a_junit_testcase_maps_back_to_the_node_id_pytest_would_print,
                     "tests.test_a.TestB", "test_c", "tests/test_a.py::TestB::test_c"))


def test_the_junit_pin_notices_an_error_folded_into_a_failure(monkeypatch, tmp_path: Path) -> None:
    mutate_function(monkeypatch, R, "parse_junit",
                    'kind = "error"\n            elif case.find("failure") is not None:',
                    'kind = "failed"\n            elif case.find("failure") is not None:')
    print(assert_red(test_a_collection_error_is_an_error_and_a_failure_is_a_failure, tmp_path))


def test_the_junit_pin_notices_a_substring_match_borrowing_another_files_error(monkeypatch) -> None:
    mutate_function(monkeypatch, R, "parse_junit",
                    'resolved[i] = "error" if f in errored_files else "missing"',
                    'resolved[i] = "error" if any(f in e or e in f\n'
                    '                                     for e in errored_files) else "missing"')
    print(assert_red(test_a_collection_error_reaches_only_its_OWN_files_ids))


def test_the_junit_pin_notices_the_SUFFIX_hedge_that_actually_shipped(monkeypatch) -> None:
    """Not a hypothetical mutation -- this exact expression was the first draft of the fix for the
    substring bug, and it reintroduced it one suffix over. Armed so the hedge cannot come back as a
    plausible-looking defence against an absolute path."""
    mutate_function(monkeypatch, R, "parse_junit",
                    'resolved[i] = "error" if f in errored_files else "missing"',
                    'resolved[i] = "error" if any(e == f or e.endswith("/" + f)\n'
                    '                                     for e in errored_files) else "missing"')
    print(assert_red(test_a_collection_error_reaches_only_its_OWN_files_ids))


def test_the_collect_pin_notices_a_warnings_line_becoming_a_test_id(monkeypatch) -> None:
    from _arming import mutate_value
    import re as _re
    mutate_value(monkeypatch, R, "_NODE_ID", _re.compile(r"^.*\.py::\S.*$"))
    print(assert_red(test_a_collected_line_must_look_like_a_node_id))


def test_the_chunking_pin_notices_one_unbounded_command_line(monkeypatch, tmp_path: Path) -> None:
    mutate_function(monkeypatch, R, "run_ids",
                    "for n in range(0, len(ids), _CHUNK):\n        batch = ids[n:n + _CHUNK]",
                    "for n in [0]:\n        batch = ids")
    print(assert_red(test_the_ids_are_chunked_so_a_command_line_has_a_length, monkeypatch, tmp_path))


def test_the_vocabulary_pin_notices_a_loud_verdict_with_no_remedy(monkeypatch) -> None:
    from _arming import mutate_value
    mutate_value(monkeypatch, R, "REMEDY", {k: v for k, v in R.REMEDY.items() if k != "all_green"})
    print(assert_red(test_quiet_is_an_allowlist_and_every_loud_verdict_names_a_remedy))


def test_the_vocabulary_pin_notices_a_verdict_that_was_never_declared(monkeypatch) -> None:
    from _arming import mutate_value
    mutate_value(monkeypatch, R, "VERDICTS", tuple(v for v in R.VERDICTS if v != "all_green"))
    print(assert_red(test_every_verdict_the_function_can_return_is_in_the_declared_vocabulary))
