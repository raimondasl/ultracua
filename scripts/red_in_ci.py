"""RED-IN-CI. Run a PR's NEW test ids against the BASE's `src/`, and fail if they all pass.

    uv run --no-sync python scripts/red_in_ci.py --base origin/main

THE CLAIM THIS INSTRUMENT MAKES, and it is deliberately narrow: *at least one* test this PR adds
fails when the PR's `src/` is swapped for the base's. That is the difference between a regression
test and a test-shaped comment. CLAUDE.md has said "verify a regression test fails against the old
code" since round 1 and it has caught several no-op fixes by hand; this is the hand-check turned
into a job, because a discipline nobody re-measures is how a green instrument stays green while
covering less.

WHAT IS SWAPPED, AND WHAT IS NOT. Only `src/` comes from the base — a worktree of it goes first on
`PYTHONPATH`, exactly as `scripts/prove_red.py` installs a mutant. `tests/`, `conftest.py`,
`benchmarks/` and `scripts/` are the PR's throughout, because the question is "does this PR's test
notice this PR's `src/` change", not "did the base's suite pass".

EVERY NEW ID IS RUN TWICE, and that is not belt-and-braces. A test that fails against the base is
only evidence if it PASSES against the branch: a new browser cell on a runner with no Chromium, a
cell that is red for an unrelated reason, an id whose fixture is broken -- each fails against the
base too, and a single-run design reports every one of them as a guard. This is `prove_red`'s own
`_require_a_live_killer_suite` rule (check the UNMUTATED tree first, or every mutant scores as
killed by a suite that is simply broken) applied one level up.

AN IMPORT ERROR AGAINST THE BASE IS INCONCLUSIVE, NEVER A KILL. A PR that adds `src/ultracua/foo.py`
gives its new tests nothing to import from the base, so they die at collection -- non-zero exit,
which a naive reading scores as RED. It proves nothing about whether the test would catch a
regression. Reported as its own loud outcome with its own remedy (ship a registered mutation in
`tests/mutations/`), which is the point of `flows.REGISTRY`'s 27 codes one instrument over: a
refusal names a remedy.

ONLY `src/` IS SWAPPABLE, AND THAT IS STRUCTURAL RATHER THAN A CHOICE. `ultracua` lives under `src/`,
which reaches the interpreter only through the editable install -- so a `PYTHONPATH` entry beats it.
`benchmarks/` and `scripts/` sit at the repo ROOT, which is `sys.path[0]` under pytest, and nothing on
`PYTHONPATH` can get in front of that; swapping them would mean putting the base's whole tree first,
which would swap `tests/` too and answer a different question. This is R4.77 wearing a second hat, and
it is MEASURED here rather than assumed: run against pre-1.3 `main`, ten of 1.3's new cells came back
`guards` and every one of its `benchmarks/variance.py` cells came back `no_guard`. So a PR whose new
tests aim at `benchmarks/` needs `tests/_arming.py`, and this job's loud channel says so.

A LOUD CHANNEL NEEDS AN ACKNOWLEDGEMENT OR IT GETS `|| true`d (R3.9/CLI-1). The discharge here is
DERIVED, never typed: a PR that ships a registered mutation under `tests/mutations/` has proven a
guard red through `scripts/prove_red.py`, which is a stronger instrument than this one and reaches the
two populations this one cannot. That is reported as its own verdict rather than folded into a pass,
so nothing about it is hidden.

QUIET IS AN ALLOWLIST (R3.9). `QUIET_VERDICTS` below is the closed set; a verdict added tomorrow is
LOUD by default rather than silently satisfying neither channel.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# What one test id can be, per run. `missing` is its own word rather than folded into `error`: an id
# that pytest never reported at all means this harness asked for something that did not exist, which
# is a fault in the harness and not a fact about the test.
OUTCOMES = ("passed", "failed", "error", "skipped", "missing")

# What one test id proves, given both runs.
STATES = ("guards", "no_guard", "inconclusive", "unusable")

VERDICTS = ("no_src_change", "no_new_tests", "red", "acknowledged_by_mutation",
            "all_green", "inconclusive", "harness")

# THE CLOSED SET OF QUIET OUTCOMES. Everything else is loud. Enumerating the quiet ones rather than
# the loud ones is R3.9/CLI-1's finding: `flow run-all` had two cron channels each testing
# `status == "failed"`, so a third bucket satisfying neither was invisible in both.
QUIET_VERDICTS = frozenset({"no_src_change", "no_new_tests", "red", "acknowledged_by_mutation"})

# The remedy each loud verdict names. A refusal without a remedy gets `|| true`d.
REMEDY = {
    "all_green": (
        "Every new test in this PR passes against the base's src/. Three ways that happens: the src/ "
        "change is not guarded at all; the guard is a structural scan that reads the repo BY PATH, "
        "which sees the pristine tree here for the same reason it does under prove_red (R4.75) -- use "
        "`inspect.getsource(module)`; or the new cells aim at benchmarks//scripts/, which this job "
        "structurally cannot swap (see the module docstring) -- arm those with tests/_arming.py and "
        "ship a registered mutation."
    ),
    "inconclusive": (
        "No new test FAILED against the base, and at least one could not run there (an import that "
        "does not exist on the base, or a skip). That is not evidence. Ship a registered mutation in "
        "tests/mutations/ and run it through scripts/prove_red.py, which does not need the base to "
        "hold the new symbol."
    ),
    "harness": (
        "This instrument could not answer its own question. Fix the harness before reading anything "
        "else in this job -- a broken derivation reports the suite as stronger than it is."
    ),
}


@dataclass(frozen=True)
class Verdict:
    name: str
    detail: str
    counts: dict = field(default_factory=dict)

    @property
    def quiet(self) -> bool:
        return self.name in QUIET_VERDICTS

    @property
    def remedy(self) -> str:
        return REMEDY.get(self.name, "")


def classify_id(branch: str, base: str) -> str:
    """What one new test id proves, from its outcome on the BRANCH and on the BASE.

    The branch outcome is a precondition, not a second opinion. A cell that does not pass on its own
    branch cannot contribute evidence about the base whatever it does there, so it is `unusable` and
    named -- rather than silently counted as a guard because it happened to be red twice.
    """
    if branch not in OUTCOMES or base not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {branch!r}/{base!r}")
    if branch != "passed":
        return "unusable"
    if base == "failed":
        return "guards"
    if base == "passed":
        return "no_guard"
    # error / skipped / missing against the base: the test did not RUN there, so its non-pass says
    # nothing about whether it would notice a regression.
    return "inconclusive"


def verdict(*, src_touched: bool, diff_adds_test_defs: bool, new_ids: "list[str]",
            states: dict, mutations_shipped: bool = False) -> Verdict:
    """The whole decision, as a pure function of five facts. This is what the self-test drives.

    ORDER MATTERS AND IS THE DESIGN. `harness` outranks every finding, because a derivation that
    could not run makes both `red` and `all_green` statements about an unknown set -- that is B3's
    channel-0 coverage rule, where 13 of 14 unscored scenarios gated GREEN on the one that scored.

    `red` OUTRANKS `inconclusive`, and the inverse was considered and refused. The job's question is
    "does this PR add at least one guard", and one genuine red answers it yes; failing the PR anyway
    because a SECOND new test could not run against the base would put a ceiling on the job with
    nothing to acknowledge it -- the D0 over-refusal shape. The inconclusive count is printed either
    way, so the fact does not go dark.

    `mutations_shipped` DISCHARGES the two findings and NOTHING ELSE. It cannot reach `harness` (a
    broken derivation is not discharged by evidence from elsewhere) and it does not pre-empt `red`
    (which is already quiet, and is the stronger statement of the two).
    """
    counts = {s: sum(1 for v in states.values() if v == s) for s in STATES}
    counts["new_ids"] = len(new_ids)

    if not src_touched:
        return Verdict("no_src_change",
                       "the diff touches no src/ file, so there is no behaviour change to guard",
                       counts)

    # THE CROSS-CHECK, and it is two INDEPENDENT derivations disagreeing rather than one asserting
    # itself: `new_ids` comes from differencing two pytest collections, `diff_adds_test_defs` from
    # the diff text. If the diff adds a test definition and the collection difference is empty, the
    # id extractor is broken and nothing below it means anything.
    if diff_adds_test_defs and not new_ids:
        return Verdict("harness",
                       "the diff adds `def test_` lines but differencing the two collections found "
                       "NO new ids -- the extractor is broken, or the base collection failed",
                       counts)

    if not new_ids:
        # A NAMED RESIDUAL, not an oversight. The sensor is id-level, so it cannot see a src/ change
        # guarded by editing an EXISTING cell's body (1.3 did exactly that to test_ratchets.py, and
        # produced no new id). Making this loud would fire on every such PR with nothing to
        # acknowledge it; the honest move is to report the limit rather than to over-gate.
        return Verdict("no_new_tests",
                       "src/ changed and this PR adds no new test id. NOT adjudicated: an existing "
                       "cell whose body changed is invisible to an id-level sensor",
                       counts)

    unseen = [i for i in new_ids if i not in states]
    if unseen:
        return Verdict("harness",
                       f"{len(unseen)} new id(s) were never classified -- the runs did not report "
                       f"them: {unseen[:5]}",
                       counts)

    if counts["guards"]:
        return Verdict("red",
                       f"{counts['guards']} of {len(new_ids)} new test(s) pass on this branch and "
                       f"FAIL against the base",
                       counts)
    if not counts["inconclusive"] and not counts["no_guard"]:
        # Everything left is `unusable`: nothing passed on this branch either. Decided BEFORE the
        # discharge, because it is a harness fact rather than a coverage finding.
        return Verdict("harness",
                       f"none of the {len(new_ids)} new test(s) pass on THIS branch, so none of them "
                       f"can say anything about the base",
                       counts)

    if mutations_shipped:
        return Verdict("acknowledged_by_mutation",
                       "no new test failed against the base, but this PR ships a registered mutation "
                       "under tests/mutations/ -- which prove_red proves red against a scratch copy, "
                       "and which reaches the two populations this job structurally cannot",
                       counts)

    if counts["inconclusive"]:
        return Verdict("inconclusive",
                       f"no new test failed against the base, and {counts['inconclusive']} could "
                       f"not run there at all",
                       counts)
    return Verdict("all_green",
                   f"all {counts['no_guard']} runnable new test(s) pass against the base's src/",
                   counts)


# ---------------------------------------------------------------------------------------------------
# The impure half: git, pytest, junit.

def _git(*args: str, cwd: Path = ROOT) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def changed_files(base: str) -> "list[str]":
    return [l for l in _git("diff", "--name-only", f"{base}...HEAD").splitlines() if l.strip()]


def diff_adds_test_defs(base: str) -> bool:
    """Does the diff ADD a test definition? Added lines only, so a moved file does not count twice."""
    out = _git("diff", "--unified=0", f"{base}...HEAD", "--", "tests")
    for line in out.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:].lstrip()
        if body.startswith("def test_") or body.startswith("async def test_"):
            return True
    return False


def _keyless_env(extra_path: "Path | None" = None) -> dict:
    env = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        env[k] = ""
    if extra_path is not None:
        env["PYTHONPATH"] = str(extra_path) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def collect_ids(cwd: Path, *, src_from: "Path | None" = None) -> "list[str]":
    """Collect the ids of the tree at `cwd`, with that tree's OWN `src/` on the path.

    The base collection gets the base's `src/` deliberately: the editable install points at THIS
    repo's `src/`, so without it the base's `conftest.py` would be importing the branch's package and
    a collection difference could come from the wrong tree entirely.
    """
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=cwd, capture_output=True, text=True, env=_keyless_env(src_from))
    if p.returncode != 0:
        raise SystemExit(
            f"collection in {cwd} exited {p.returncode}. Both collections must succeed or the "
            f"difference between them is not a set of NEW ids, it is noise.\n"
            + (p.stdout or "")[-3000:] + (p.stderr or "")[-2000:])
    return [l.strip() for l in p.stdout.splitlines() if "::" in l and not l.startswith(" ")]


def run_ids(ids: "list[str]", *, src_from: "Path | None", junit: Path) -> dict:
    """Run these ids from the PR's tests, optionally with another tree's `src/` first on the path.

    Returns id -> outcome. An id pytest never mentions is `missing`, unless its FILE carries a
    collection error, in which case every id in that file is `error` -- which is the ImportError
    case, and it must not be confused with a failure.
    """
    subprocess.run(
        [sys.executable, "-m", "pytest", *ids, "-q", "--tb=no", "-p", "no:cacheprovider",
         f"--junitxml={junit}"],
        cwd=ROOT, capture_output=True, text=True, env=_keyless_env(src_from))
    return parse_junit(junit, ids)


def parse_junit(path: Path, ids: "list[str]") -> dict:
    """junit-xml -> {id: outcome}. `<error>` and `<failure>` are DIFFERENT and stay different."""
    out: dict = {}
    errored_files: set = set()
    if path.exists():
        for case in ET.parse(path).getroot().iter("testcase"):
            cls, name = case.get("classname") or "", case.get("name") or ""
            kind = "passed"
            if case.find("error") is not None:
                kind = "error"
            elif case.find("failure") is not None:
                kind = "failed"
            elif case.find("skipped") is not None:
                kind = "skipped"
            # A COLLECTION error has no test name to hang on: pytest emits the file path as the
            # classname. Remember the file so its ids can be told from `missing`.
            if kind == "error" and not cls.strip():
                errored_files.add(name.replace("\\", "/"))
            out[_node_id(cls, name)] = kind
    resolved = {}
    for i in ids:
        if i in out:
            resolved[i] = out[i]
            continue
        f = i.split("::", 1)[0].replace("\\", "/")
        resolved[i] = "error" if any(f in e or e in f for e in errored_files) else "missing"
    return resolved


def _node_id(classname: str, name: str) -> str:
    """junit's (classname, name) back into a pytest node id.

    `tests.test_a.TestB` + `test_c` -> `tests/test_a.py::TestB::test_c`. Parametrised names keep
    their `[...]` suffix, which is what makes a new parametrize CELL a new id here.
    """
    if not classname:
        return name
    parts = classname.split(".")
    for n, part in enumerate(parts):
        if part.startswith("test_") or part.startswith("conftest"):
            file_part = "/".join(parts[:n + 1]) + ".py"
            rest = parts[n + 1:]
            return "::".join([file_part, *rest, name])
    return "::".join([classname.replace(".", "/") + ".py", name])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main",
                    help="the ref whose src/ the new tests must fail against")
    ap.add_argument("--max-ids", type=int, default=400,
                    help="refuse rather than run an unbounded set (a rename can make the whole "
                         "suite look new)")
    args = ap.parse_args()

    base_sha = _git("rev-parse", args.base).strip()
    files = changed_files(args.base)
    src_touched = any(f.startswith("src/") for f in files)
    mutations = [f for f in files if f.startswith("tests/mutations/") and f.endswith(".py")]
    adds_tests = diff_adds_test_defs(args.base)
    print(f"base {args.base} ({base_sha[:12]}); {len(files)} changed file(s); "
          f"src touched: {src_touched}; diff adds `def test_`: {adds_tests}; "
          f"registered mutations touched: {mutations or 'none'}")

    if not src_touched:
        v = verdict(src_touched=False, diff_adds_test_defs=adds_tests, new_ids=[], states={},
                    mutations_shipped=bool(mutations))
        return _report(v)

    with tempfile.TemporaryDirectory(prefix="red-in-ci-") as tmp:
        tree = Path(tmp) / "base"
        _git("worktree", "add", "--detach", str(tree), base_sha)
        try:
            branch_ids = collect_ids(ROOT)
            base_ids = collect_ids(tree, src_from=tree / "src")
            new_ids = sorted(set(branch_ids) - set(base_ids))
            print(f"{len(branch_ids)} ids here, {len(base_ids)} on the base -> {len(new_ids)} new")

            if len(new_ids) > args.max_ids:
                print(f"\nREFUSING: {len(new_ids)} new ids exceeds --max-ids {args.max_ids}. A "
                      f"rename or a collection difference can make the whole suite look new, and "
                      f"running it twice here would time out rather than answer anything.")
                return 2

            states: dict = {}
            if new_ids:
                jb, jm = Path(tmp) / "branch.xml", Path(tmp) / "base-src.xml"
                on_branch = run_ids(new_ids, src_from=None, junit=jb)
                on_base = run_ids(new_ids, src_from=tree / "src", junit=jm)
                for i in new_ids:
                    states[i] = classify_id(on_branch.get(i, "missing"), on_base.get(i, "missing"))
                for i in new_ids:
                    print(f"  {states[i]:<13} {on_branch.get(i, 'missing'):>7} here / "
                          f"{on_base.get(i, 'missing'):>7} on base   {i}")

            v = verdict(src_touched=True, diff_adds_test_defs=adds_tests,
                        new_ids=new_ids, states=states, mutations_shipped=bool(mutations))
            return _report(v)
        finally:
            shutil.rmtree(tree, ignore_errors=True)
            _git("worktree", "prune")


def _report(v: Verdict) -> int:
    print(f"\n== {v.name.upper()} ==\n{v.detail}")
    if v.quiet:
        return 0
    print(f"\nREMEDY: {v.remedy}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
