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
    was = tier.STATE["fast"]
    tier.STATE["fast"] = True
    try:
        yield
    finally:
        tier.STATE["fast"] = was


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
    missing = sorted(collected - (set(data["browser"]) | set(data["fast"])))
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
