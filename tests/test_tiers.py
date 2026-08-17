"""The tier mechanism, with every claim ARMED rather than asserted.

This file exists because a tier is an instrument, and this register's rule for instruments is that a
green one proves nothing until its violation has been made to go red. Three of S14's cells were green
while reaching no mechanism at all; the `no_llm` stub was inert twice past its own review. So each cell
below either triggers the refusal it claims, or counts the mechanism it claims to reach.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

import _tiers as tier  # the MECHANISM, imported directly rather than through whichever conftest wins


# ---------------------------------------------------------------------------------------------------
# 1. The refusal fires. Both entry styles, because `start()` and `async with` are different doors and
#    wrapping only one is the half-inert shape this mechanism exists to avoid.

@pytest.fixture
def fast_tier():
    """Turn the fast tier's refusal ON for one cell, whatever mode the run itself is in.

    Flipping the flag exercises the SAME wrapper the real `--tier fast` installs (`pytest_configure`
    sets this identical field), so this is the mechanism, not a re-implementation of it.
    """
    was, was_expected = tier.STATE["fast"], tier.STATE["expected"]
    tier.STATE["fast"] = True
    # Declare the intent, so a refusal this cell provokes on purpose is not recorded as an offence and
    # `scripts/derive_test_tiers.py` does not promote the arming cells out of the tier they validate.
    tier.STATE["expected"] = True
    try:
        yield
    finally:
        tier.STATE["fast"], tier.STATE["expected"] = was, was_expected


async def test_fast_tier_refuses_async_playwright_start(fast_tier) -> None:
    """`.start()` — the door `src/ultracua/browser.py:70` uses."""
    with pytest.raises(tier.ChromiumInFastTier):
        await async_playwright().start()


async def test_fast_tier_refuses_async_with_playwright(fast_tier) -> None:
    """`async with` — the door 21 sites in this suite use, and the one a `start`-only wrap misses."""
    with pytest.raises(tier.ChromiumInFastTier):
        async with async_playwright():
            pass  # pragma: no cover - the refusal above is the point


async def test_fast_tier_refuses_a_BrowserSession(fast_tier) -> None:
    """The product's own entry point, so the refusal is proved through the code a test actually calls."""
    from ultracua.browser import BrowserSession

    with pytest.raises(tier.ChromiumInFastTier):
        await BrowserSession(headless=True).start()


def test_the_tier_option_is_actually_wired_to_the_refusal(pytestconfig) -> None:
    """The wiring nothing armed, which is how a tier can quietly stop being one.

    Every cell that proves the refusal uses the `fast_tier` fixture, which sets `STATE["fast"]` BY HAND
    — so all of them would keep passing if `conftest.py` stopped deriving it from `--tier`. Then
    `--tier fast` would run the fast tests with no raiser installed, a browser test misclassified as
    fast would quietly launch one, and the tier would report green while proving nothing. This is the
    only cell that reads the OPTION and the STATE together, and it runs in both tiers.
    """
    assert tier.STATE["fast"] is (pytestconfig.getoption("--tier") == "fast"), (
        "`--tier` and the refusal state disagree: conftest.py no longer derives one from the other, so "
        "the fast tier is not actually refusing anything"
    )


def test_the_probe_is_installed_on_every_known_entry_point() -> None:
    """ANTI-VACUITY. If nothing was wrapped, every refusal cell above would still pass — by never being
    reached — and the tier would be decoration. Assert the wrap COUNT, not merely that it ran."""
    assert len(tier.INSTALLED) >= 4, (
        f"only {len(tier.INSTALLED)} Playwright entry point(s) wrapped; the tier probe is inert and "
        f"every other cell in this file is vacuous"
    )
    wrapped = {(cls.__name__, attr) for cls, attr, _ in tier.INSTALLED}
    assert ("BrowserType", "launch") in wrapped
    assert ("PlaywrightContextManager", "__aenter__") in wrapped


def test_the_probe_restores_the_original_even_if_installed_twice() -> None:
    """A defect this file's own self-audit found before it shipped.

    `uninstall_probes` used to restore in INSERTION order, so after two installs it replayed
    `original, probe1` and left probe1 attached — entry 2's saved "real" is probe1, not the original.
    Unreachable from a normal session, which is exactly why it would have sat there. LIFO fixes it by
    construction; this arms the violation the fix removes.
    """
    from playwright.async_api._generated import BrowserType

    original = BrowserType.launch
    saved = list(tier.INSTALLED)
    tier.INSTALLED.clear()
    try:
        tier.install_probes()
        tier.install_probes()          # the nesting the old code could not unwind
        assert BrowserType.launch is not original, "the probe did not attach; this cell proves nothing"
        tier.uninstall_probes()
        assert BrowserType.launch is original, (
            "uninstall left a wrapper attached — restoration is order-dependent again"
        )
    finally:
        tier.INSTALLED[:] = saved      # hand the session back the probes it was running with


# ---------------------------------------------------------------------------------------------------
# 2. The counter is LIVE — a browser test, deliberately, because that is the only way to prove the
#    wrapper is on the path a real session takes rather than on one nothing calls.

async def test_a_real_session_is_observed_launching_exactly_one_browser() -> None:
    from ultracua.browser import BrowserSession

    key = "BrowserType.launch"
    before_total = tier.STATE["n"]
    before_launch = tier.STATE["by_call"].get(key, 0)

    session = BrowserSession(headless=True)
    await session.start()
    try:
        launched = tier.STATE["by_call"].get(key, 0) - before_launch
        total = tier.STATE["n"] - before_total
    finally:
        await session.close()

    # ONE browser per session is the invariant. The TOTAL is deliberately not pinned to a constant: it
    # also counts starting the driver, which `_acquire_driver` (browser.py:59-72) caches per event loop,
    # so it is 2 on a fresh loop and 1 on a loop whose driver is already up. Asserting the constant would
    # be pinning the cache's current behaviour under a name that claims to be about launches.
    assert launched == 1, f"one session launched {launched} browser(s) — the probe is miscounting"
    assert total >= launched, "the total must include the launch it is derived from"


# ---------------------------------------------------------------------------------------------------
# 3. The manifest classifies the whole collection, and says so by construction.

async def test_a_refused_launch_is_not_counted_as_a_launch(fast_tier) -> None:
    """The defect an audit found in this instrument, armed.

    The probe used to increment the launch counter BEFORE checking the fast flag, so a refusal was
    charged as a launch. The victims were the only cells that arm the refusal — they were classified as
    browser tests and deselected by the very tier they validate, which is why the fast tier shipped
    proving nothing about its own refusal. Both the right and the wrong behaviour left all twelve cells
    green, so this is the cell that can tell them apart.
    """
    before_n, before_refused = tier.STATE["n"], tier.STATE["refused"]
    with pytest.raises(tier.ChromiumInFastTier):
        await async_playwright().start()
    assert tier.STATE["n"] == before_n, "a REFUSED launch was charged to the launch counter"
    assert tier.STATE["refused"] == before_refused + 1, "the refusal was not counted as a refusal"


async def test_an_expected_refusal_is_not_recorded_as_an_offence() -> None:
    """The same defect one level down, in the PROMOTION path rather than the counter.

    Fixing the counter made these cells derive as fast; the derivation then promoted them back, because
    `FAST_OFFENDERS` could not tell a refusal a cell provoked ON PURPOSE from one a misclassified test
    caused. Measured: the arming cells were promoted to the browser tier on the round where the 15
    `test_drift_bench.py` cells failed — collateral of an unrelated failure.

    Drives the REAL probe both ways rather than re-stating its rule, because a cell that re-implements
    the mechanism it checks passes when the mechanism is deleted.
    """
    before = set(tier.FAST_OFFENDERS)
    saved = (tier.STATE["fast"], tier.STATE["expected"], tier.CURRENT[0])
    tier.CURRENT[0] = "tests/test_invented.py::deliberate"
    try:
        tier.STATE["fast"], tier.STATE["expected"] = True, True
        with pytest.raises(tier.ChromiumInFastTier):
            await async_playwright().start()
        assert tier.FAST_OFFENDERS == before, "an EXPECTED refusal was recorded as an offence"

        tier.STATE["expected"] = False           # the identical refusal, now unannounced
        with pytest.raises(tier.ChromiumInFastTier):
            await async_playwright().start()
        assert tier.CURRENT[0] in tier.FAST_OFFENDERS, "an UNEXPECTED refusal went unrecorded"
    finally:
        tier.FAST_OFFENDERS.clear()
        tier.FAST_OFFENDERS.update(before)
        tier.STATE["fast"], tier.STATE["expected"], tier.CURRENT[0] = saved


def test_the_refusal_cannot_be_swallowed_by_a_broad_except(fast_tier) -> None:
    """`except Exception` must not be able to turn the refusal green.

    Measured before the fix, when it derived from RuntimeError: a cell wrapping a launch in
    `try/except Exception` PASSED under `--tier fast` and the run exited 0, with the violation recorded
    only in a file nobody reads. `flows.py` alone has 20 broad catches; a refusal any of them can eat is
    a refusal that reports green.
    """
    assert not issubclass(tier.ChromiumInFastTier, Exception), (
        "ChromiumInFastTier derives from Exception again, so any broad `except Exception` on the path "
        "turns the fast tier's loud refusal into a silent pass"
    )
    assert issubclass(tier.ChromiumInFastTier, BaseException)


def test_a_partial_derivation_refuses_rather_than_deleting_the_manifest() -> None:
    """Armed: regenerating from a SUBSET would silently discard everything it did not collect.

    Measured before the guard: `pytest tests/test_tiers.py --store-browser-marks` rewrote the manifest
    to 12 ids — deleting 1091 classifications from a committed artifact — and reported success. The next
    run is loud, but the artifact is already gone and costs a 31-minute run to rebuild.
    """
    data = json.loads(tier.MANIFEST.read_text(encoding="utf-8"))
    with pytest.raises(tier.PartialDerivation) as exc:
        tier.guard_full_collection(12)
    assert str(data["total"]) in str(exc.value), "the refusal must say how much would have been lost"
    tier.guard_full_collection(data["total"])          # a full collection is allowed through
    tier.guard_full_collection(data["total"] + 5)      # so is a grown one


def test_the_manifest_exists_and_is_internally_consistent() -> None:
    data = json.loads(tier.MANIFEST.read_text(encoding="utf-8"))
    browser, fast = data["browser"], data["fast"]
    assert not (set(browser) & set(fast)), "a test cannot be in both tiers"
    assert data["total"] == len(browser) + len(fast)
    # Anti-vacuity in BOTH directions: an empty browser list would make the fast tier the whole suite,
    # and an empty fast list would make it useless. Both have been real failure modes for lists here.
    assert len(browser) >= 400, f"only {len(browser)} browser tests recorded — the probe under-counted"
    assert len(fast) >= 200, f"only {len(fast)} fast tests recorded — the tier would not be worth having"


def test_an_unclassified_test_makes_a_tiered_run_refuse_rather_than_skip() -> None:
    """The silent-coverage-loss guard, armed: a collected id in NEITHER list must raise, not be dropped.

    This is `check_shard_coverage`'s property one instrument over — a test in no shard leaves every
    shard green, and a test in no tier would leave the fast tier green while never running it.
    """
    data = json.loads(tier.MANIFEST.read_text(encoding="utf-8"))
    known_fast, known_browser = data["fast"][0], data["browser"][0]
    stranger = "tests/test_invented.py::test_nobody_classified_me"

    with pytest.raises(tier.UnclassifiedTests) as exc:
        tier.partition([known_fast, stranger], "fast", data)
    assert stranger in str(exc.value), "the refusal must NAME the unclassified test"
    assert exc.value.nodeids == [stranger]

    # And with nothing unclassified it partitions rather than refusing — the guard must not fire on a
    # healthy collection, which is the direction an over-eager guard would break.
    keep, drop = tier.partition([known_fast, known_browser], "fast", data)
    assert keep == [known_fast] and drop == [known_browser]
    keep_b, drop_b = tier.partition([known_fast, known_browser], "browser", data)
    assert keep_b == [known_browser] and drop_b == [known_fast]


def test_the_manifest_covers_everything_this_run_collected() -> None:
    """The live version of the guard above: the manifest must classify THIS collection, not a past one.

    `--tier fast` enforces it at collection time, but the default run — the merge gate, and the one CI
    shards — never calls `partition`, so without this cell a stale manifest would be discovered only by
    whoever next typed `--tier fast`. Deliberately a fast test, so it runs in both tiers.

    Scoped to what THIS run collected, so a deliberate subset (`pytest tests/test_tiers.py`) checks its
    own ids rather than failing for the ones it did not ask for.
    """
    collected = set(tier.COLLECTED)
    assert collected, "the collection hook recorded nothing — this cell would pass vacuously"
    data = json.loads(tier.MANIFEST.read_text(encoding="utf-8"))
    known = set(data["browser"]) | set(data["fast"])

    # THE OTHER DIRECTION, which every guard in the repo was missing. Both this cell and `partition`
    # only ever asked `collected ⊆ manifest`, so a test that stops COLLECTING — deleted, renamed, or
    # silently dropped by a broken parametrize — is invisible: the fast job still exits 0 with the same
    # pass count, and `check_shard_coverage` compares full-vs-shards so it is absent from both sides and
    # the partition still reads clean. Only checked when this run collected the whole suite; a
    # deliberate subset (`pytest tests/test_tiers.py`) legitimately collects fewer.
    if len(collected) >= data["total"]:
        vanished = sorted(known - collected)
        assert not vanished, (
            f"{len(vanished)} test(s) are in {tier.MANIFEST.name} but were NOT collected — they were "
            f"deleted or renamed and nothing else in the repo can notice. Regenerate with "
            f"`pytest -q --store-browser-marks`:\n  " + "\n  ".join(vanished[:20])
        )

    missing = sorted(collected - known)
    assert not missing, (
        f"{len(missing)} collected test(s) are not in {tier.MANIFEST.name}, so `--tier fast` would "
        f"refuse to run. Regenerate with `pytest --store-browser-marks`:\n  "
        + "\n  ".join(missing[:20])
    )


# ---------------------------------------------------------------------------------------------------
# 4. Keys are scrubbed, and the premise that makes an EMPTY value sufficient is pinned.

def test_no_provider_key_is_visible_to_the_suite() -> None:
    import os

    for var in tier.PROVIDER_KEY_VARS:
        assert os.environ.get(var) == "", f"{var} is set inside the suite: a test can reach a provider"


def test_importing_ultracua_config_does_not_repopulate_a_scrubbed_key() -> None:
    """The load-bearing premise, stated in CLAUDE.md and never pinned until now.

    `ultracua.config` calls `load_dotenv()` at import, and the whole key-scrub rests on `load_dotenv`
    NOT overriding a variable that is already set — which is why an empty string is enough. If that ever
    changes, every key-less guarantee in this suite quietly stops holding, and this cell is what notices.
    """
    import importlib
    import os

    import ultracua.config as cfg

    importlib.reload(cfg)  # re-runs load_dotenv() against a real .env if the developer has one
    assert os.environ.get("ANTHROPIC_API_KEY") == "", (
        "load_dotenv() overrode a set-but-empty variable — the scrub is not sufficient any more and "
        "CLAUDE.md's `ANTHROPIC_API_KEY= uv run pytest` recipe no longer does what it claims"
    )


# ---------------------------------------------------------------------------------------------------
# 5. Marks stay DERIVED. A hand-written mark is the list-shaped failure this mechanism replaced.

def test_no_test_file_hand_marks_its_own_tier() -> None:
    offenders = []
    for path in sorted(Path("tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr in ("browser", "fast", "slow") and isinstance(node.value, ast.Attribute) \
                    and node.value.attr == "mark":
                offenders.append(f"{path}:{node.lineno} uses pytest.mark.{node.attr}")
    assert not offenders, (
        "tier membership is DERIVED from an observed launch, never declared — a hand-written mark is a "
        "list, and a list is only as good as its worst entry:\n  " + "\n  ".join(offenders)
    )
