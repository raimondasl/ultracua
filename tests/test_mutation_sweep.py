"""The mutation sweep: the registry set is derived, and the two CI jobs between them run all of it.

WHAT THIS FILE IS ABOUT, and it is not the mutations themselves — `scripts/prove_red.py` proves those
red, and the `red-proof` job re-proves them on every PR. This is about the LIST. Until 0.6 the set of
registries lived in `.github/workflows/ci.yml` as six hand-typed steps, and a seventh registry added
under `tests/mutations/` and not there would simply never have run, with every job green. Nothing in
the suite could fail for it. That is the "quiet is an allowlist" defect (R3.9/CLI-1) wearing a CI hat,
and this project has now shipped that shape twice (`flow run-all`'s third status bucket; the shards).

So the properties here are about DERIVATION and COVERAGE:

  * the registry set comes from the directory, never from a list;
  * the tier split — which registries the merge gate may run and which need the weekly job's browser —
    is read out of the tier manifest, because a declaration would be a fact somebody keeps in their
    head and `red-proof` installs no Playwright deliberately;
  * every registry is run by at least one job, and no browser-side registry is run by the job that
    cannot launch one;
  * quiet is an allowlist of exactly one status.

Nothing here launches a browser or runs a mutation: it reads the registries, the manifest and the two
workflow files. It belongs in the fast tier.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from _arming import assert_red
from test_ci_provisioning import Step, parse_steps, workflows

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mutation_sweep as sweep  # noqa: E402
import prove_red  # noqa: E402

CI = ROOT / ".github" / "workflows" / "ci.yml"
WEEKLY = ROOT / ".github" / "workflows" / "mutation-sweep.yml"


# EVERY ASSERTION BELOW IS OVER `Step.run`, NEVER OVER THE RAW FILE, and that is not a style choice.
# The first draft of this file grepped the workflow TEXT for `--tier fast` and went red on the
# comment in `mutation-sweep.yml` that explains why the merge gate uses it. That is the fifth time a
# scan in this repo has matched its own prose, and the fix is the one already written down: stop
# scanning text, assert the property. `test_ci_provisioning.Step.run` is the instrument that already
# does it correctly -- it takes a YAML block scalar's body by INDENT, so a comment between two steps
# cannot leak in, which its own docstring records as a measured defect (22 captured lines for a
# 6-line script). Reusing it beats a sixth needle, which would only move the collision.


@pytest.fixture(scope="module")
def steps() -> "list[Step]":
    out: list[Step] = []
    for wf in workflows():
        out.extend(parse_steps(wf.read_text(encoding="utf-8"), file=wf.name))
    return out


def sweep_runs(steps: "list[Step]") -> "dict[str, str]":
    """`{workflow file: the run payload}` for every step that invokes the sweep.

    The ONE definition, shared by the cells and by their arming, for the reason `refused_hits` is
    shared one file over: two copies of a predicate is how an arming proof ends up proving something
    the real check does not do.
    """
    return {s.file: s.run for s in steps if "mutation_sweep.py" in s.run}

# The number `CLAUDE.md`, `docs/correctness-survey.md` (PROCESS-4) and `docs/reshape-plan.md`'s
# acceptance column all quote. It is the one hand-typed fact in this file, and it is hand-typed
# because it IS the fact — see `test_the_nine_known_mutants_are_all_nine`.
KNOWN_NINE = 9


# --------------------------------------------------------------------------- the set is derived


def test_the_registry_set_is_the_directory() -> None:
    """No list anywhere. A seventh registry is swept by construction."""
    on_disk = {p.name for p in (ROOT / "tests" / "mutations").glob("*.py")
               if not p.name.startswith("_")}
    assert {p.name for p in sweep.registries()} == on_disk
    assert len(on_disk) >= 6, (
        f"only {sorted(on_disk)} found; either the directory moved or this cell is passing over an "
        f"empty set, which would make every property below vacuous"
    )


def test_no_workflow_step_names_a_registry(steps: "list[Step]") -> None:
    """The whole point, asserted at the surface that used to hold the list.

    Six `prove_red.py tests/mutations/<name>.py --tests ...` steps lived in `ci.yml`. Re-introducing
    one is not wrong in itself — it is wrong because it re-creates a SECOND source of truth about
    which registries exist, and the two then drift silently in the direction that runs less.

    Over `run` payloads, so the comment in `mutation-sweep.yml` that DESCRIBES `tests/mutations/`
    is invisible here — the quiet direction, pinned by the arming cell below.
    """
    named = {s.where: sorted(set(re.findall(r"tests/mutations/[\w]+\.py", s.run)))
             for s in steps if re.search(r"tests/mutations/[\w]+\.py", s.run)}
    assert not named, (
        f"{named} name a registry. The set is derived from `tests/mutations/` by "
        f"`scripts/mutation_sweep.py`; naming one in a workflow makes it a second source of truth, "
        f"which is exactly what 0.6 removed."
    )


def test_both_jobs_invoke_the_sweep_and_only_the_weekly_one_runs_everything(
        steps: "list[Step]") -> None:
    """Derived from the workflows' run payloads, so deleting an invocation fails HERE.

    The asymmetry is the whole design. `--tier fast` is the merge gate, which installs no browser;
    the weekly job passes no tier and therefore runs the browser-side registries too. If the weekly
    job ever grew `--tier fast`, the nine known mutants would stop running anywhere and every job
    would stay green — which is the coverage hole 0.6 exists to close, arriving through the fix.
    """
    runs = sweep_runs(steps)
    assert set(runs) == {CI.name, WEEKLY.name}, (
        f"the sweep is invoked from {sorted(runs)}; it must run in BOTH the merge gate and the "
        f"weekly job, or one half of the registry set is swept by nobody"
    )
    assert "--tier fast" in runs[CI.name], (
        "the merge gate does not pass `--tier fast`, so it will select the browser-side registries "
        "in a job that installs no Playwright — every mutant's baseline fails and the whole set "
        "reads as a hole in the matrix"
    )
    assert "--tier fast" not in runs[WEEKLY.name], (
        "the weekly sweep passes `--tier fast`, which deselects exactly the registries it exists to "
        "run — the nine known mutants would then be swept by NEITHER job while both stayed green"
    )


def test_the_weekly_workflow_is_weekly_and_can_be_asked_for(text: str = "") -> None:
    """A schedule, and a button.

    The button is not a convenience. A sweep whose only trigger is a cron cannot be run against a
    branch that is about to change the thing it measures, so the answer arrives a week after the
    change — and the measured cost of having no manual trigger is already on the record: a CI run
    had to be nudged by closing and reopening a PR on 2026-08-26.
    """
    text = text or WEEKLY.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    cron = re.search(r'cron:\s*"([^"]+)"', text)
    assert cron, "the weekly workflow has no cron schedule"
    fields = cron.group(1).split()
    assert len(fields) == 5, f"malformed cron {cron.group(1)!r}"
    assert fields[2] == "*" and fields[4] != "*", (
        f"cron {cron.group(1)!r} is not WEEKLY — a day-of-week field is what makes it so. A daily "
        f"cadence buys six extra runs of an unchanged tree per week; the plan says weekly."
    )


def test_ci_can_be_dispatched_by_hand() -> None:
    """The other half of the same 2026-08-26 lesson, on the merge gate.

    `pull_request` dispatch lagged by several minutes with no way to ask for a run, so the only
    lever was closing and reopening the PR — a lifecycle event used as a retry button.
    """
    assert "workflow_dispatch:" in CI.read_text(encoding="utf-8")


# ----------------------------------------------------------------- the tier split is derived


def test_the_tier_split_comes_from_the_manifest_not_a_declaration() -> None:
    """A registry is browser-side iff one of its killer files holds a browser test. Both directions.

    Declared membership is what this repo refuses everywhere else (`pytest.mark.browser` is REFUSED
    by a test, for the same reason), and here it would be wrong in two ways at once: a registry
    wrongly called `fast` launches a browser in a job whose conftest RAISES, and one wrongly called
    `browser` leaves the merge gate silently.
    """
    browser_ids, fast_ids = sweep._manifest()
    browser_files = sweep._files_of(browser_ids)
    for path in sweep.registries():
        is_browser, why = sweep.needs_browser(path)
        expected = [f for f in sweep.killer_files(path) if browser_files.get(f)]
        assert why == expected
        assert is_browser == bool(expected), (
            f"{path.name}: derived browser={is_browser} but its killer files with browser tests are "
            f"{expected}"
        )


def test_no_registry_declares_a_killer_the_manifest_has_never_seen() -> None:
    """The third state, refused rather than guessed.

    An unknown killer file is not `fast` and not `browser` — it is unclassifiable, and both guesses
    are wrong in a way nothing downstream can see. `needs_browser` raises and names the remedy.
    """
    for path in sweep.registries():
        sweep.needs_browser(path)          # raises SystemExit naming the file if one is unknown


def test_every_registry_is_run_by_at_least_one_job_and_none_by_the_wrong_one() -> None:
    """THE coverage property. Neither job may silently drop a registry.

    Stated over the two selections rather than over the workflow text, because the workflow text is
    already pinned above and this is about what the selections actually contain.
    """
    everything = {p.name for p in sweep.registries()}
    fast_side, browser_side = set(), set()
    for path in sweep.registries():
        (browser_side if sweep.needs_browser(path)[0] else fast_side).add(path.name)

    assert fast_side | browser_side == everything, "a registry landed in neither selection"
    assert not (fast_side & browser_side), "a registry landed in both"
    assert browser_side, (
        "NO registry is browser-side, so the weekly job is running exactly what the merge gate "
        "already ran and buys nothing. `known_nine.py`'s killers are page-side properties; if this "
        "is empty, either that registry is gone or the manifest has stopped classifying its killers."
    )


# ------------------------------------------------------------------------- the nine, specifically


def test_the_nine_known_mutants_are_all_nine() -> None:
    """reshape-plan 0.6's acceptance criterion, verbatim: the nine must be killed EVERY run.

    They were applied by hand at 0.75.0, each in its own git worktree, and the 9/9 result is quoted
    in three documents. Until this registry existed nothing could re-run them — which is the same
    "a claim nobody re-measures" hole `prove_red`'s docstring names in the small.

    Pinned as an exact count deliberately. A tenth defect class is welcome and belongs in a registry
    of its own or in a diff that also updates `CLAUDE.md`, `docs/correctness-survey.md` (PROCESS-4)
    and `docs/reshape-plan.md`, all three of which say "nine".
    """
    path = ROOT / "tests" / "mutations" / "known_nine.py"
    assert path.exists(), "the nine known mutants registry is gone"
    mod = prove_red._load(path)
    ids = [m[0] for m in mod.MUTANTS]
    assert len(ids) == KNOWN_NINE, f"the registry holds {len(ids)} mutants, not {KNOWN_NINE}: {ids}"
    assert len(set(ids)) == len(ids), "two of the nine share an id"
    assert sweep.needs_browser(path)[0], (
        "the nine are classified browser-free, so the merge gate would run them in a job with no "
        "Chromium — where a killer-suite leg with a browser cell fails EVERY mutant's baseline "
        "(measured on CI at 8 failed / 135 passed on both arms, green locally) and reads as a hole "
        "in the matrix rather than as a missing browser"
    )


def test_every_mutant_everywhere_resolves_a_non_empty_killer_suite() -> None:
    """Through `prove_red.killers_of`, which is what actually runs them.

    A mutant with no killer would be scored by an empty pytest invocation — exit code 5, non-zero,
    reported as KILLED. That is `_require_a_live_killer_suite`'s hole arriving from the registry
    side instead of from a `--tests` typo.
    """
    for path in sweep.registries():
        mod = prove_red._load(path)
        for mutant in mod.MUTANTS:
            killers = prove_red.killers_of(mod, mutant)
            assert killers, f"{path.name}:{mutant[0]} resolves no killer suite"
            for k in killers:
                assert (ROOT / k).exists(), f"{path.name}:{mutant[0]} names a missing killer {k!r}"


def test_every_mutation_everywhere_still_applies_to_the_tree() -> None:
    """STALENESS, checked on every PR rather than once a week.

    `prove_red` already reports a mutation whose find-text no longer matches as an ERROR rather than
    as a survivor, and that is the right behaviour — a stale mutation silently reports the suite as
    stronger than it is. But the browser-side registries only run WEEKLY, so a stale one could sit on
    `main` for six days looking green. The 0.6 slice is the proof that this is the live risk and not
    a hypothetical: SIX of the nine known mutants' sites had moved since 0.75.0, and only the
    find-text check forced them to be re-expressed rather than quietly becoming nine no-ops.

    This does not run anything — it reads `src/` and counts occurrences, which is seconds and no
    browser. Note the limit, because it is a known one: reading `src/` BY PATH means this cell parses
    the pristine tree under `scripts/red_in_ci.py`'s base-swap and can never contribute a kill there
    (R4.75). That is deliberate — it is a structural pin, a different sensor class from the mutants
    themselves, the same way `scripts/ratchets.py` is.
    """
    src = ROOT / "src" / "ultracua"
    stale = []
    for path in sweep.registries():
        mod = prove_red._load(path)
        for mutant in mod.MUTANTS:
            mid, rel, find = mutant[0], mutant[1], mutant[2]
            target = src / rel
            if not target.exists():
                stale.append(f"{path.name}:{mid} names a missing module {rel!r}")
                continue
            n = target.read_text(encoding="utf-8").count(find)
            if n != 1:
                stale.append(f"{path.name}:{mid} matches {rel} {n} times, not once")
    assert not stale, (
        "these mutations no longer apply cleanly, so each proves NOTHING while the sweep reports a "
        "clean run:\n  " + "\n  ".join(stale) +
        "\n\nRe-express the mutation against the site that moved — state the PROPERTY, not the line "
        "number. A find-text matching twice is as bad as one matching zero times: `prove_red` "
        "refuses it as ambiguous, because a mutation that could hit either of two sites names neither."
    )


# -------------------------------------------------------------------------------- the verdict


def test_quiet_is_an_allowlist_of_exactly_one_status() -> None:
    """A status added tomorrow is LOUD, without anybody having thought of it.

    Keying off the loud ones is how `skipped` came to feed neither of `run-all`'s two cron channels.
    """
    assert sweep.QUIET == frozenset({"clean"})
    for status in ("empty", "suite_dead"):
        assert status not in sweep.QUIET and status not in sweep.WORKED, (
            f"{status!r} must be LOUD and must not count as work done. In BOTH of these, not one "
            f"mutant was scored: an emptied registry makes `prove_red` print '0 killed, 0 survived, "
            f"0 broken of 0' and exit ZERO, and a dead killer suite means every mutant would have "
            f"been judged by a broken suite. A sweep made entirely of these checked nothing."
        )


def test_every_exit_code_prove_red_can_return_has_a_status_and_a_remedy() -> None:
    """DERIVED from `prove_red`'s named constants, so a fourth code cannot be quietly absorbed.

    The mapping had a `.get(code, "broken")` default in its first draft, which is B3's `CODE_FAMILY`
    defect one instrument over — a bucket that absorbs whatever nobody classified and reports it with
    a confident label. It cost nothing here and would have said "a stale mutation" about a segfault.
    """
    named = {v for k, v in vars(prove_red).items()
             if k in ("SURVIVORS", "BROKEN", "SUITE_DEAD")}
    assert named == {1, 2, 3}, f"prove_red's exit codes moved: {named}"
    assert set(sweep.STATUS_OF) == named | {0}, (
        f"the sweep maps {sorted(sweep.STATUS_OF)} but `prove_red` can return {sorted(named | {0})}. "
        f"A code with no entry gets `unknown_exit_<n>`, which is loud — but a code that MOVED would "
        f"silently take another code's remedy."
    )
    assert len(set(sweep.STATUS_OF.values())) == len(sweep.STATUS_OF), (
        "two exit codes share a status, so two different remedies print the same word — which is the "
        "overloaded-verdict shape this map was split to remove"
    )


def _result(status: str) -> sweep.RegistryResult:
    return sweep.RegistryResult("r.py", status, "", 1, False)


@pytest.mark.parametrize("statuses,code", [
    (["clean", "clean"], 0),
    (["clean", "survivors"], 1),
    (["clean", "broken"], 1),
    (["empty"], 1),
    ([], 2),
])
def test_the_verdict_maps_the_way_the_sweep_claims(statuses, code) -> None:
    """Driven through the SHARED `flows.sweep_verdict`, not a fourth hand-rolled condition.

    CLI-4 was CLI-1 on another verb and got that function rather than a third copy of the rule; a
    mutation sweep is the same shape a third time — quiet is an allowlist, and a sweep where NOTHING
    ran is exit 2 rather than "healthy".
    """
    from ultracua.flows import sweep_verdict
    verdict = sweep_verdict([_result(s) for s in statuses],
                            quiet=sweep.QUIET, worked=sweep.WORKED, noun="mutation registry")
    assert verdict.exit_code == code, f"{statuses} -> {verdict.exit_code}, expected {code}"


# ------------------------------------------------------------------------------------- arming


def test_the_coverage_cells_go_red_when_armed() -> None:
    """A cell that cannot fail is not a test, and four of these assert a NEGATIVE about a list.

    Each mutation states the property's violation and asserts it CHANGED something first — a
    find-text that no longer matches reports this cell as stronger than it is, which is the rule
    `prove_red` already applies to a stale mutation.

    Driven by re-parsing MUTATED workflow text rather than by patching `Path.read_text`, which is
    read-only on a `WindowsPath` — the first draft did that and died on the restore in its `finally`,
    on the platform this repo is developed on.
    """
    ci_text = CI.read_text(encoding="utf-8")
    weekly_text = WEEKLY.read_text(encoding="utf-8")

    def both(ci: str, weekly: str) -> "list[Step]":
        return parse_steps(ci, file=CI.name) + parse_steps(weekly, file=WEEKLY.name)

    # (a) name a registry in a RUN payload -> the second-source-of-truth cell must fire.
    #
    # ANCHORED ON THE CONTINUATION LINE, not on `--tier fast`. The first draft used the bare flag and
    # `str.replace(..., 1)` hit the COMMENT four lines above the step that explains what the flag
    # does — so the mutation added a registry name to prose, the cell correctly ignored it, and
    # `assert_red` reported the cell as unguarded. The harness was right and the mutation was wrong,
    # which is this repo's standing note about anchoring a mutation on enough context to name ONE
    # site, arriving in the same slice that rewrote these cells to ignore comments.
    named = ci_text.replace("          --tier fast\n",
                            "          --tier fast tests/mutations/b1_wiring.py\n", 1)
    assert named != ci_text, "the mutation is STALE; ci.yml's --tier fast continuation line moved"
    assert_red(test_no_workflow_step_names_a_registry, both(named, weekly_text))

    # (b) THE QUIET DIRECTION, pinned as hard as the loud one, and it is the direction this file
    # got WRONG first: `mutation-sweep.yml`'s comments discuss both `tests/mutations/` and
    # `--tier fast` by name, and a scan over raw text goes red on the paragraph EXPLAINING the rule.
    # A registry named in a COMMENT must stay invisible.
    commented = weekly_text.replace(
        "      - name: Sweep every registry\n",
        "      # a comment naming tests/mutations/b1_wiring.py and --tier fast, which must NOT trip\n"
        "      - name: Sweep every registry\n", 1)
    assert commented != weekly_text, "the comment mutation is STALE"
    leaked = both(ci_text, commented)
    test_no_workflow_step_names_a_registry(leaked)
    test_both_jobs_invoke_the_sweep_and_only_the_weekly_one_runs_everything(leaked)

    # (c) let the weekly job run only the fast half -> the nine would be swept by NEITHER job
    crippled = weekly_text.replace("scripts/mutation_sweep.py\n",
                                   "scripts/mutation_sweep.py --tier fast\n", 1)
    assert crippled != weekly_text, "the mutation is STALE; the weekly invocation changed shape"
    assert_red(test_both_jobs_invoke_the_sweep_and_only_the_weekly_one_runs_everything,
               both(ci_text, crippled))

    # (d) drop the sweep from the merge gate entirely -> the same cell, other direction
    gutted = ci_text.replace("python scripts/mutation_sweep.py\n", "python -c pass\n", 1)
    assert gutted != ci_text, "the mutation is STALE; ci.yml's sweep invocation changed shape"
    assert_red(test_both_jobs_invoke_the_sweep_and_only_the_weekly_one_runs_everything,
               both(gutted, weekly_text))

    # (e) make the schedule daily -> "weekly" is a claim about ONE cron field, and a daily cron
    # satisfies every other assertion in that cell.
    daily = weekly_text.replace('cron: "17 4 * * 1"', 'cron: "17 4 * * *"', 1)
    assert daily != weekly_text, "the mutation is STALE; the cron expression changed shape"
    assert_red(test_the_weekly_workflow_is_weekly_and_can_be_asked_for, daily)
