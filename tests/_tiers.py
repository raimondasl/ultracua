"""The tier mechanism: a launch probe, a derived manifest, and a refusal.

Separated from `conftest.py` on purpose. `conftest.py` is pytest WIRING (hooks, options, a fixture); this
is the MECHANISM, and keeping it in a plain module means the mechanism can be imported and tested
directly — by `tests/test_tiers.py` — instead of through whichever `conftest` happens to win on
`sys.path` when two of them exist. An instrument that can only be exercised through the thing it
instruments is how a stub stays inert past its own review (S14, twice).

WHY THIS EXISTS. 414 of the 836 tests `.test_durations` measures take <=0.5 s and total 9.6 s — 0.8% of
the suite's time — and there has been no way to select them. The whole 21-31 min suite is therefore the
only local signal, it is Windows-only here, and its cost is what pushes an author toward "just the files
my change is about". That shortcut is measured: B1 (PR #165) ran targeted subsets and the full suite then
found 7 breakages the subsets could not, because the breakage was in what the change TOUCHED (argument
binding) rather than in what it was ABOUT (accounting).

WHY THE MARKS ARE DERIVED, NOT DECLARED. A hand-written `@pytest.mark.browser` is a list, and this
register's standing finding is that a list is only as good as its worst entry (R9, R3.11, R4.31, R4.38,
S14's `_FACTORIES` — which let a replay build 105 real Anthropic clients with every cell green). So a
test is a browser test IF AND ONLY IF it was OBSERVED launching one, and the fast tier does not merely
skip the others: it makes a launch RAISE, so a test that is unlisted and launches anyway fails LOUD
instead of quietly running slowly. Silence is not evidence that the tier is clean.

WHY THE WRAP IS ON THE CLASS. Nine test modules do `from playwright.async_api import async_playwright`
at import time. Patching that NAME in one module reaches none of them — which is exactly the S14 trap
(`flows.py:41` binds `run_cached` at import, so patching `ultracua.flow.run_cached` never reached
`_attempt_replay`). Patching the CLASS attribute reaches every holder of every binding, including
`src/ultracua/browser.py:70,165` and `src/ultracua/parallel.py:38-39`.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

MANIFEST = Path(__file__).parent / ".browser_tests.json"

# An override, and it exists for ONE reason: a merged manifest must be VALIDATED BEFORE it replaces the
# committed one. `scripts/tier_marks.py` writes a CANDIDATE, points the fixed-point loop at it through
# this variable, and swaps only if the loop converges. The alternative — write the real file, then check
# it — leaves a wrong manifest on disk whenever the check fails, and the check's own failure branch says
# "Fix it; do not re-run this script", i.e. it would hand the reader a confident misdiagnosis over an
# artifact that costs a 36-minute run to rebuild.
#
# Deliberately NOT a general knob: nothing in the suite reads it, and `tests/test_tiers.py` pins that
# the committed path is what an unset environment resolves to.
_MANIFEST_ENV = "ULTRACUA_TIER_MANIFEST"
_DEFAULT_MANIFEST = MANIFEST
if os.getenv(_MANIFEST_ENV):
    MANIFEST = Path(os.environ[_MANIFEST_ENV])

# Scrubbed for every run. `ultracua.config` calls `load_dotenv()` at import, and `load_dotenv` does not
# override a variable that is already SET — so an empty value is enough, which is what CLAUDE.md tells
# every contributor to type by hand. Doing it here makes a local run identical to CI's by default,
# closing the divergence that let a `provider=None` test drive REAL API calls locally (S8/0.84.0) while
# failing both CI arms. `tests/test_tiers.py` pins the "empty is enough" premise itself.
#
# `ANTHROPIC_AUTH_TOKEN` is here because the Anthropic SDK resolves auth from it as well: leaving it set
# defeats the very `Could not resolve authentication method` signal that made S8/0.84.0 visible on CI.
# TWO STATED BOUNDS, because this list is the hand-written shape the rest of this module exists to
# escape, and neither is closed here:
#   * it is a LIST — deriving it from what the leaf adapters actually read (the move the `build_client`
#     AST scan made) is what would close the class; Bedrock/Vertex vars are not covered.
#   * it is IN-PROCESS. On win32 `os.environ[var] = ""` DELETES the variable from the real environment
#     block, so a CHILD process sees it absent rather than empty and its own `load_dotenv()` would then
#     populate it from `.env`. Today's exposure is nil — the only subprocess in the suite is a
#     `python -c` lock probe in `tests/test_health_lock.py` that reaches no provider — but a future test
#     that shells out inherits the S8 hazard, and the two pinning cells would stay green.
PROVIDER_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY",
                     "GOOGLE_API_KEY")


class ChromiumInFastTier(BaseException):
    """A test not classified as a browser test tried to launch one under `--tier fast`.

    Loud on purpose. The alternative — letting it run — is how a tier silently becomes the whole suite
    again; the alternative to THAT (skipping it) is how a tier silently stops covering something.

    **BaseException, not RuntimeError, and that is the whole point.** As a `RuntimeError` this was
    swallowable by any broad `except Exception` on the path — and the suite has 20 of them in `flows.py`
    alone, 18 in `flow.py`. Measured before the change: a cell wrapping a launch in `try/except
    Exception` PASSED under `--tier fast`, the run exited 0, and the offenders file recorded a violation
    nobody would ever read. A refusal a caller can catch is a refusal that reports green, which is this
    register's own quiet-outcome failure one instrument over. `BaseException` puts it beside pytest's own
    control-flow exceptions: `except Exception` cannot reach it, `pytest.raises` still can, and pytest
    reports it as an ordinary FAILURE rather than a session interrupt (all three verified).
    """


# The one piece of mutable state, module-level so the wrappers close over nothing per-run:
#   n       — wrapped calls since the session started (monotonic). ANY of them makes a test a browser
#             test, because starting the driver is browser-tier work too (~364 ms, browser.py:32).
#   by_call — the same, split per entry point. Kept because "how many browsers did this open" is an
#             invariant worth asserting, while "how many wrapped calls" depends on whether this loop's
#             driver was already up (`_acquire_driver`, browser.py:59-72, caches it per event loop).
#   refused — launches the fast tier turned away. Counted SEPARATELY from `n` because a refusal is not
#             a launch: charging it to `n` classified the refusal cells themselves as browser tests.
#   fast    — whether a launch must raise instead of proceeding.
#   expected — a cell is deliberately provoking the refusal to prove it fires; the refusal still raises,
#             but it is not recorded as an offence. Set ONLY by the `fast_tier` fixture.
STATE: dict = {"n": 0, "by_call": {}, "refused": 0, "fast": False, "expected": False}

# nodeid -> wrapped calls observed while that test ran.
PER_TEST: dict[str, int] = {}

# Every nodeid THIS run collected, recorded before tier filtering. Lets a cell assert the manifest is
# current rather than merely internally consistent — a stale one is otherwise invisible until someone
# runs a tiered command.
COLLECTED: list = []

# The test currently running, and the fast-tier tests caught launching a browser.
#
# WHY THIS EXISTS — the probe's own blind spot, found by the probe. Attribution is ORDER-DEPENDENT: when
# a module shares browser work through a fixture, whichever test triggers it first is charged for the
# launch and its siblings are charged nothing, so they classify as "fast" while being unable to run
# without a browser at all. Measured on the first derivation: all 15 non-launching tests in
# `test_drift_bench.py` classify fast because `test_every_absolute_invariant_holds` primes the shared
# bench, and every one of them launches when the fast tier deselects that sibling.
#
# So the manifest is not the output of one run — it is a FIXED POINT: derive per-test counts from a full
# run, then promote whatever the fast tier catches, until the fast tier catches nothing.
# `scripts/derive_test_tiers.py` runs that loop, and the CI fast job is what keeps it honest afterwards
# (a manifest that stops being a fixed point turns that job red, naming the tests).
CURRENT: list = [None]
FAST_OFFENDERS: set = set()
OFFENDERS_FILE = Path(__file__).parent / ".fast_offenders.json"

# nodeid -> the WORST outcome it reported this run ("passed" / "skipped" / "failed").
#
# Two jobs. As a KEY SET it is the sensor behind `guard_every_selected_test_ran` (a skip reports,
# `--collect-only` does not). As a VALUE it decides whether an observation may DE-CLASSIFY a test:
# a test that died in setup — a raising fixture, an unreachable browser — reports and launches
# nothing, which is indistinguishable from "no longer needs a browser" if you only look at the launch
# count. So browser -> fast requires a PASSING observation; see `merge_observations`.
REPORTED: dict = {}

# Ranked worst-last, so `max` over a test's reports gives the one that decides.
_OUTCOME_RANK = {"passed": 0, "skipped": 1, "failed": 2}

# Every Playwright entry point that can open a browser or start the driver. Listed from the CLASS rather
# than from what this repo calls today: `launch` and `__aenter__` are the two the suite uses, and the
# others are here so a future `connect_over_cdp` cannot enter through an unwatched door.
PATCH_TARGETS = (
    ("playwright.async_api._generated", "BrowserType", "launch"),
    ("playwright.async_api._generated", "BrowserType", "launch_persistent_context"),
    ("playwright.async_api._generated", "BrowserType", "connect_over_cdp"),
    # `PlaywrightContextManager.start()` is a one-line delegate to `__aenter__`, so wrapping the dunder
    # covers BOTH `async with async_playwright()` (21 sites in this suite) and `.start()`
    # (`browser.py:70`). Wrapping `start` alone would be inert for every `async with` — the same
    # half-measure this module's docstring warns about, one level down.
    ("playwright.async_api._context_manager", "PlaywrightContextManager", "__aenter__"),
)

INSTALLED: list = []


def scrub_provider_keys() -> None:
    for var in PROVIDER_KEY_VARS:
        os.environ[var] = ""


def install_probes() -> int:
    """Wrap each entry point so it COUNTS, and (in the fast tier) REFUSES. Returns how many were wrapped."""
    for mod_name, cls_name, attr in PATCH_TARGETS:
        try:
            cls = getattr(importlib.import_module(mod_name), cls_name)
            real = getattr(cls, attr)
        except (ImportError, AttributeError):  # pragma: no cover - a Playwright version without it
            continue

        def _wrap(real=real, label=f"{cls_name}.{attr}"):
            async def probe(self, *a, **kw):
                # REFUSE FIRST, COUNT SECOND. The counter answers "did this test launch a browser", and
                # a refused call launched nothing — it never reaches `real(...)`. Counting it charged the
                # three cells that ARM the refusal as browser tests, so the tier deselected exactly the
                # cells that prove it works, and shipped with its own refusal unarmed. Nothing raised and
                # all twelve cells stayed green either way, which is what made it worth fixing at the
                # source rather than by adjusting the manifest.
                if STATE["fast"]:
                    STATE["refused"] += 1
                    # An EXPECTED refusal is not an offence. The cells that prove the refusal works
                    # trigger it on purpose, and recording them exiled the tier's own arming cells to
                    # the browser tier on any round where something ELSE failed — finding #1 surviving
                    # one level down, in the promotion path rather than the counter. The fixture that
                    # flips the flag declares the intent; nothing else may.
                    if CURRENT[0] and not STATE["expected"]:
                        FAST_OFFENDERS.add(CURRENT[0])
                    raise ChromiumInFastTier(
                        f"{label} was called under `--tier fast`. Either this test is not in "
                        f"{MANIFEST.name} and should be (regenerate with `pytest "
                        f"--store-browser-marks`), or it newly launches a browser and belongs in the "
                        f"browser tier."
                    )
                STATE["n"] += 1
                STATE["by_call"][label] = STATE["by_call"].get(label, 0) + 1
                return await real(self, *a, **kw)

            return probe

        setattr(cls, attr, _wrap())
        INSTALLED.append((cls, attr, real))
    return len(INSTALLED)


def uninstall_probes() -> None:
    """Restore the ORIGINALS — LIFO, so nested wrapping unwinds instead of half-restoring.

    Found by self-audit before this shipped: restoring in insertion order after two installs replays
    `original, probe1` and leaves **probe1** attached, because entry 2's saved "real" IS probe1. Not
    reachable from a normal session (`pytest_configure` runs once), but "restore the wrong thing" is a
    class this register has paid for elsewhere, and LIFO makes it correct by construction rather than by
    the caller never doing the thing. Pinned by `test_the_probe_restores_the_original_even_if_installed_twice`.
    """
    for cls, attr, real in reversed(INSTALLED):
        setattr(cls, attr, real)
    INSTALLED.clear()


def load_manifest() -> dict:
    if not MANIFEST.exists():
        raise FileNotFoundError(
            f"{MANIFEST} is missing — regenerate it with `pytest --store-browser-marks` (a FULL run)."
        )
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def partition(nodeids, tier: str, data: dict) -> "tuple[list, list]":
    """`(keep, drop)` for a tier, or raise if anything collected is unclassified.

    DERIVE-AND-COMPARE, the `check_shard_coverage` shape. A test in NEITHER list would silently not run
    in the fast tier while the tier reported green — the same silent-coverage-loss class that checker
    exists for. Ids in the manifest but not collected are fine (a dependency group not installed); the
    reverse never is.
    """
    browser_ids, fast_ids = set(data["browser"]), set(data["fast"])
    known = browser_ids | fast_ids
    unclassified = sorted(n for n in nodeids if n not in known)
    if unclassified:
        raise UnclassifiedTests(unclassified)
    wanted = fast_ids if tier == "fast" else browser_ids
    return [n for n in nodeids if n in wanted], [n for n in nodeids if n not in wanted]


class UnclassifiedTests(Exception):
    """Collected tests that no tier claims — a tiered run must refuse rather than skip them."""

    def __init__(self, nodeids) -> None:
        self.nodeids = list(nodeids)
        shown = "\n  ".join(self.nodeids[:20]) + ("\n  ..." if len(self.nodeids) > 20 else "")
        super().__init__(
            f"{len(self.nodeids)} collected test(s) are not classified in {MANIFEST.name}, so a tiered "
            f"run would silently skip them. Regenerate with `pytest --store-browser-marks`.\n  {shown}"
        )


def write_offenders() -> int:
    """Record the fast-tier tests caught launching, so the derivation loop needs no output parsing.

    **Deletes a stale file when there is nothing to report.** It used to only ever WRITE, so a file from
    an earlier round outlived the condition that produced it — observed: an otherwise-clean run left the
    four arming cells named on disk, describing a defect that had already been fixed. A diagnostic that
    outlives its cause is worse than none, because the next reader trusts it. The derivation loop
    deletes it per round and was never at risk; a human reading the tree was.
    """
    if not FAST_OFFENDERS:
        OFFENDERS_FILE.unlink(missing_ok=True)
        return 0
    OFFENDERS_FILE.write_text(json.dumps(sorted(FAST_OFFENDERS), indent=1) + "\n", encoding="utf-8")
    return len(FAST_OFFENDERS)


def promote(nodeids) -> "tuple[int, int]":
    """Move `nodeids` from the fast tier to the browser tier and rewrite the manifest."""
    data = load_manifest()
    promoting = set(nodeids)
    fast = [i for i in data["fast"] if i not in promoting]
    browser = sorted(set(data["browser"]) | promoting)
    _write(browser, fast)
    return len(browser), len(fast)


class PartialDerivation(Exception):
    """`--store-browser-marks` was run over a SUBSET, which would silently discard the rest.

    Measured before this guard existed: `pytest tests/test_tiers.py --store-browser-marks` rewrote the
    manifest to 12 ids, deleting 1091 classifications from a committed artifact and reporting it as a
    success ("wrote .browser_tests.json: 4 browser, 8 fast"). The NEXT run is loud — every unclassified
    test refuses — but the artifact is already gone, and restoring it costs a 31-minute run. The
    `--tier` guard beside this one covered the wrong axis: it stops a tiered regeneration and says
    nothing about a narrowed one.
    """


def guard_full_collection(collected: int) -> None:
    """Refuse to rewrite the manifest from a run that COLLECTED less than it already describes."""
    if not MANIFEST.exists():
        return  # first derivation: there is nothing to lose
    known = load_manifest().get("total", 0)
    if collected < known:
        raise PartialDerivation(
            f"--store-browser-marks collected {collected} test(s) but {MANIFEST.name} already "
            f"classifies {known}. Writing now would DELETE {known - collected} classification(s). "
            f"Run it over the whole suite (`pytest -q --store-browser-marks`), or delete the manifest "
            f"first if you really mean to derive from scratch."
        )


def guard_every_selected_test_ran(selected_ids, reported_ids) -> None:
    """Refuse to rewrite the manifest from a run that SELECTED tests it never actually executed.

    The general sensor, and it replaces a growing list of per-flag ones. `write_manifest()` classifies
    from `PER_TEST`, which is populated by an autouse fixture — so ANY route that collects a test
    without running it silently drops that test's classification.

    MEASURED, live, against the committed manifest: `pytest -q --collect-only --store-browser-marks`
    printed "wrote .browser_tests.json: 0 browser, 0 fast" — a TOTAL WIPE of 1201 classifications,
    reported as a success. Neither the tier guard, nor `guard_full_collection` (collection was whole),
    nor `guard_not_narrowed` (nothing was deselected) could see it.

    Why REPORTS rather than `PER_TEST`, which would be the obvious sensor: a SKIPPED test never runs
    the autouse fixture, so it is absent from `PER_TEST` — keying on that would refuse any machine
    where a test legitimately skips (a missing optional dependency group, a platform guard). A skip
    DOES produce reports. Measured on a 3-test file with 2 skipped: `items == 3`, `reported == 3`,
    `PER_TEST == 1`. Under `--collect-only`: `reported == 0`.

    So this one check covers `--collect-only`, a run aborted part-way, and a collection error that
    prevents execution — without a per-flag exemption for any of them, and without refusing a skip.
    """
    missing = sorted(set(selected_ids) - set(reported_ids))
    if missing:
        raise PartialDerivation(
            f"--store-browser-marks selected {len(selected_ids)} test(s) but {len(missing)} of them "
            f"never RAN, so the manifest is written from an incomplete observation and would drop "
            f"their classifications.\n"
            f"    The usual cause is `--collect-only` (measured: it wrote a 0-browser, 0-fast "
            f"manifest over 1201 classifications and called it a success). A run aborted part-way "
            f"does the same. Re-run to completion: `pytest -q --store-browser-marks`.\n"
            f"    First few: " + ", ".join(missing[:3])
        )


def guard_not_narrowed(collected: int, selected: int) -> None:
    """Refuse to rewrite the manifest from a run that SELECTED less than it collected.

    THE THIRD AXIS, and it was open. `PartialDerivation`'s own docstring says the `--tier` guard
    "covered the wrong axis: it stops a tiered regeneration and says nothing about a narrowed one",
    and `guard_full_collection` was the answer for narrowing-by-collection. But a run can also be
    narrowed by DESELECTION, which leaves the collection whole and shrinks only what runs — and that
    is invisible to both.

    MEASURED, because it is not obvious. Under `pytest --splits 2 --group 1`, pytest-split deselects
    AFTER the root conftest's `pytest_collection_modifyitems` records `COLLECTED`. So on a shard:

        COLLECTED = 1201     (the guard above sees a WHOLE collection and passes)
        session.items = 626  (what actually runs, so what `PER_TEST` covers)

    `write_manifest()` classifies from `PER_TEST`, so a sharded `--store-browser-marks` would have
    written 626 classifications, DELETED ~575, and printed "wrote .browser_tests.json: N browser,
    M fast" as a success — the exact disaster the class above is named for, one axis over. Verified by
    calling `guard_full_collection(1201)` against the committed 1201-id manifest: it passes.

    Deselection is the right sensor rather than "did every test run", and the difference matters:
    a SKIPPED test stays in `session.items` (measured: 1 passed + 2 skipped -> items == 3), so a
    machine that legitimately skips is not refused, while `--splits`, `-k`, `-m` and `--deselect` are
    all caught by one check. That is the CLASS, not a fourth per-axis guard.
    """
    if selected < collected:
        raise PartialDerivation(
            f"--store-browser-marks collected {collected} test(s) but only {selected} were SELECTED "
            f"to run — {collected - selected} were deselected, and the manifest is written from what "
            f"RAN. Writing now would drop every deselected test's classification while the collection "
            f"looked whole.\n"
            f"    This is what `--splits`/`--group` (a CI shard), `-k`, `-m` and `--deselect` all do. "
            f"Re-run without them: `pytest -q --store-browser-marks`."
        )


def write_manifest() -> "tuple[int, int]":
    browser = sorted(nid for nid, n in PER_TEST.items() if n > 0)
    fast = sorted(nid for nid, n in PER_TEST.items() if n == 0)
    _write(browser, fast)
    return len(browser), len(fast)


def _write(browser, fast) -> None:
    MANIFEST.write_text(
        json.dumps(
            {
                "_comment": (
                    "DERIVED, never hand-edited. A test is a browser test iff it was OBSERVED launching "
                    "one, OR the fast tier later caught it launching (attribution is order-dependent "
                    "when a module shares browser work through a fixture). Regenerate with "
                    "`pytest -q --store-browser-marks` then `python scripts/derive_test_tiers.py`, "
                    "which iterates to the fixed point. An unclassified test makes a tiered run fail "
                    "loudly rather than skip it."
                ),
                "total": len(browser) + len(fast),
                "browser": sorted(browser),
                "fast": sorted(fast),
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------------------------------
# OBSERVATIONS: the manifest, derived from runs that already happened.
#
# Re-deriving the manifest costs a full suite run — 1917-3151 s across the twelve entries in
# `tests/.manifest_cost.jsonl`. CI already pays that on every PR, four times over (two shards x two
# OSes), and throws the evidence away. An OBSERVATION is that evidence, serialised.
#
# The shape is deliberately NOT "the browser ids". It carries `collected` / `selected` / `reported`
# as well, because those are what let a merge tell apart three cases a bare id list conflates: a test
# that ran and did not launch (a classification), a test no part selected (a missing shard), and a
# test that was selected and never ran (a truncated observation). Only the first is evidence; the
# other two are reasons to REFUSE.

SCHEMA = 1


class MergeRefused(Exception):
    """The parts do not describe this tree, or do not describe it completely."""


def observation(*, collected, selected, reported, launches, exitstatus, seconds, label) -> dict:
    """One run's worth of evidence. Writes no classification and never touches the manifest."""
    return {
        "schema": SCHEMA,
        "label": label,
        "platform": sys.platform,
        "exitstatus": int(exitstatus),
        "seconds": round(float(seconds), 1),
        "collected": sorted(collected),
        "selected": sorted(selected),
        "reported": {k: reported[k] for k in sorted(reported)},
        "launches": {k: int(v) for k, v in sorted(launches.items()) if v},
    }


def load_observation(path: Path) -> dict:
    """Read one part. A corrupt or truncated part is an ERROR naming the file, never skipped.

    `scripts/manifest_cost.py` states this rule for its ledger ("a corrupt ledger is loud, never
    skipped"). Here it matters more: a silently dropped part means the merge writes a manifest missing
    everything that part observed, and reports success.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise MergeRefused(f"{path} is not a readable observation: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        got = data.get("schema") if isinstance(data, dict) else "?"
        raise MergeRefused(
            f"{path} has schema {got}, expected {SCHEMA}. Re-emit it rather than merging across "
            f"schemas — an older part may not carry the fields the contract checks."
        )
    for field in ("label", "collected", "selected", "reported", "launches"):
        if field not in data:
            raise MergeRefused(f"{path} is missing {field!r}; it is not a complete observation")
    return data


def reconcile_attempts(parts):
    """One part per label: LAST wins for COVERAGE, but launches are UNIONED.

    Re-running a failed CI job produces a second artifact for the same (os, shard) — measured on run
    31677764463, which held `resource-samples-windows-latest-1-1` AND `...-1-2`. "Re-run failed jobs"
    is the most common CI action there is, so treating duplicates as an error would refuse exactly
    when a human most needs the tool.

    WHY THE TWO FIELDS ARE TREATED DIFFERENTLY, and the first draft got this wrong in the unsafe
    direction. `selected`/`reported` are a COMPLETENESS record, and the later attempt is the
    authoritative one — that is the whole reason to dedup, since the earlier attempt of a re-run is
    usually TRUNCATED (it was killed; that is why it was re-run) and would otherwise fail the
    truncation clause. But `launches` is EVIDENCE, and evidence does not expire: a test observed
    launching on attempt 1 that happens not to reach the browser on attempt 2 is still a browser test.
    Last-wins on launches measurably moved such a test into the fast tier, where it RAISES.

    Same asymmetry as the cross-part union one clause down, and the same reason: over-classifying
    costs fast-tier size, under-classifying breaks the tier.
    """
    keep = {}
    for path, data in parts:
        label = data["label"]
        if label in keep:
            merged = dict(data)
            merged["launches"] = {**keep[label][1]["launches"], **data["launches"]}
            keep[label] = (path, merged)
        else:
            keep[label] = (path, data)
    return [keep[k] for k in sorted(keep)]


def merge_observations(parts, expected, notes=None):
    """`(browser, fast)` from a set of observations, or REFUSE.

    `expected` is THIS tree's collection, and it is the only honest identity. A commit sha is not one:
    on a `pull_request` GitHub checks out `refs/pull/N/merge`, so a run tagged with your branch tip
    actually collected a MERGE of your branch into main — verified, run 32380417760 checked out
    2c908fc, "Merge 5d47964 into 4f7ac67". Matching by sha would hand you marks for a tree containing
    tests you do not have, and the inflated `total` would then DISARM the deletion detector at
    `tests/test_tiers.py`, which only runs when `len(collected) >= data["total"]`.

    Refusing on the ID SET catches that, and catches the same thing arriving by any other route: a
    stale part from an earlier push, a part from `main`, a tree edited after the push. No clean-tree
    check is needed on top — an edited tree collects differently, which is the same signal.
    """
    if not parts:
        raise MergeRefused("no observation parts were given; there is nothing to merge")
    parts = reconcile_attempts(parts)
    expected_set = set(expected)
    if not expected_set:
        raise MergeRefused("this tree collected NO tests; refusing to write an empty manifest")

    # (1) Every part must be a COMPLETE observation of whatever it selected.
    for path, d in parts:
        selected, reported = set(d["selected"]), set(d["reported"])
        if not selected:
            raise MergeRefused(f"{path} selected no tests — an empty part classifies nothing")
        if not selected <= set(d["collected"]):
            raise MergeRefused(f"{path} selected tests it never collected; it is not self-consistent")
        never_ran = sorted(selected - reported)
        if never_ran:
            raise MergeRefused(
                f"{path} selected {len(selected)} test(s) but {len(never_ran)} never ran, so it is a "
                f"TRUNCATED observation — the job was killed, or this was a --collect-only run. "
                f"First few: {', '.join(never_ran[:3])}"
            )

    # (2) IDENTITY, by id set rather than by sha. See the docstring.
    seen = set().union(*(set(d["collected"]) for _p, d in parts))
    if seen != expected_set:
        extra, missing = sorted(seen - expected_set), sorted(expected_set - seen)
        lines = ["these observations describe a DIFFERENT collection than this tree:"]
        if extra:
            lines.append(f"    {len(extra)} they have and this tree does not: {', '.join(extra[:5])}")
        if missing:
            lines.append(f"    {len(missing)} this tree has and they do not: {', '.join(missing[:5])}")
        # WHICH cause, not just THAT it happened. Found by using this tool: a stale local part sitting
        # beside four fresh CI ones was refused with "main has moved" — a confident WRONG diagnosis of
        # a condition that has several causes, which is the overloaded-outcome shape this register
        # keeps paying for (`cancelled` meaning three different things).
        #
        # If the parts DISAGREE WITH EACH OTHER then one of them is stale, and naming it is the
        # actionable answer. Only when they all agree, and differ from the tree together, is "the tree
        # moved" the right thing to say.
        by_size: dict = {}
        for path, d in parts:
            by_size.setdefault(len(d["collected"]), []).append((path, d))
        if len(by_size) > 1:
            majority = max(by_size, key=lambda k: len(by_size[k]))
            lines.append("")
            lines.append("    THE PARTS DISAGREE WITH EACH OTHER, so one of them is stale rather than "
                         "the tree having moved:")
            for size, group in sorted(by_size.items()):
                if size == majority:
                    continue
                for path, d in group:
                    lines.append(f"      {path.name} (label {d['label']!r}) collected {size}, while "
                                 f"{len(by_size[majority])} other part(s) collected {majority}")
            lines.append("    Delete the stale part and merge again. `pull` deliberately leaves "
                         "non-`tier-marks-*` files alone, so a local observation is never destroyed.")
        else:
            lines.append(
                "    Every part agrees, so it is the TREE that differs. A pull_request run tests "
                "refs/pull/N/merge, not your branch tip, so this is what you get once main has moved "
                "— or once you edited a test after pushing. Push, let CI finish, and pull again."
            )
        raise MergeRefused("\n".join(lines))

    # (3) COVERAGE. Every test must have been RUN by some part, or its class is a guess, not evidence.
    ran = set().union(*(set(d["selected"]) for _p, d in parts))
    unobserved = sorted(expected_set - ran)
    if unobserved:
        raise MergeRefused(
            f"{len(unobserved)} test(s) were collected by these observations but RUN by none of them, "
            f"so a shard is missing. Merging now would classify them from no evidence at all. "
            f"First few: {', '.join(unobserved[:3])}\n"
            f"    Parts present: {', '.join(d['label'] for _p, d in parts)}"
        )

    # (4) CLASSIFY. Browser iff observed launching ANYWHERE. The union is the safe direction: a test
    # wrongly in the browser tier merely runs slowly, while one wrongly in the fast tier RAISES.
    launched = set().union(*(set(d["launches"]) for _p, d in parts))

    # (5) A DE-CLASSIFICATION needs a PASSING observation behind it. A test that dies in setup — a
    # raising fixture, an unreachable browser — reports and launches nothing, which reads identically
    # to "it stopped needing a browser". Direction of error decides: keeping a stale browser mark costs
    # fast-tier size, dropping a real one puts a launch in the fast tier where it RAISES.
    was_browser = set(load_manifest().get("browser", ())) if MANIFEST.exists() else set()
    for _path, d in parts:
        for nid, outcome in d["reported"].items():
            if outcome != "passed" and nid in was_browser and nid not in launched:
                launched.add(nid)

    # (6) ...and it needs evidence from EVERY PLATFORM, which a single-platform part set does not have.
    #
    # MEASURED, and this is not an edge case: the documented local path
    # (`pytest --emit-marks ... && tier_marks.py merge`) is single-platform BY CONSTRUCTION. Two
    # windows parts covering the whole tree pass identity and coverage, the merge succeeds, and a test
    # that launches only on ubuntu moves browser -> fast — where CI's ubuntu `fast` job then RAISES on
    # it. Candidate-then-swap cannot catch this either: the fixed-point loop runs `--tier fast` on the
    # producing platform, so it never exercises the arm that would object.
    #
    # This is a HOLD, not a refusal, so there is no D0 exposure: new tests still classify, new launches
    # still promote, and only the unsafe direction is withheld. `platform` was already recorded and
    # read by nothing; this is what it is for.
    platforms = {d.get("platform") for _p, d in parts}
    if len(platforms) < 2:
        held = sorted(was_browser - launched)
        launched |= set(held)
        if held and notes is not None:
            notes.append(
                f"HELD {len(held)} existing browser mark(s): these observations come from one platform "
                f"({', '.join(sorted(str(x) for x in platforms))}), so they cannot show that a test "
                f"stopped needing a browser EVERYWHERE. Pull a full CI set to de-classify."
            )

    return sorted(launched & expected_set), sorted(expected_set - launched)


def write_manifest_from(browser, fast) -> "tuple[int, int]":
    """Write a manifest from an explicit classification (the merge path). See `_write`."""
    _write(sorted(browser), sorted(fast))
    return len(browser), len(fast)
