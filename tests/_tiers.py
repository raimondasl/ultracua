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
from pathlib import Path

MANIFEST = Path(__file__).parent / ".browser_tests.json"

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
    """Refuse to rewrite the manifest from a run that collected less than it already describes."""
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
