"""Run EVERY mutation registry under `tests/mutations/` and mint ONE verdict — reshape-plan step 0.6.

    python scripts/mutation_sweep.py --tier fast     # the per-PR half: browser-free registries only
    python scripts/mutation_sweep.py                 # the weekly half: all of them, needs Chromium
    python scripts/mutation_sweep.py --list          # what would run, and on which side, and why

WHAT THIS IS FOR. `scripts/prove_red.py` proves ONE registry red. Until now the SET of registries
lived in `.github/workflows/ci.yml` as six hand-typed steps, each with its killer suite as a
`--tests` flag. Two things were wrong with that and neither could fail a test:

  * a registry added to `tests/mutations/` and not to `ci.yml` simply never ran, with every job
    green — the "quiet is an allowlist" defect (R3.9/CLI-1) wearing a CI hat, and this project has
    shipped that shape twice already;
  * the killer suite was invisible to whoever wrote or reviewed the registry.

Both are closed by DERIVING. The registry set comes from the directory, so a seventh is swept by
construction. The killer suite comes from the registry (`KILLED_BY`, or a per-mutant override), so
reviewing a mutation and reviewing what will be run against it are the same diff.

THE TIER SPLIT IS DERIVED TOO, and that is the part that could not be a declaration. `red-proof`
installs no Playwright deliberately — a killer-suite leg with a browser cell fails EVERY mutant's
baseline (measured on CI: 8 failed / 135 passed on both arms, while green locally), which reads as a
hole in the matrix rather than as a missing browser. So a registry whose killers touch a browser
MUST run in the weekly job and MUST NOT run in the merge gate. That fact is read out of the tier
manifest, the same artifact `--tier fast` itself is built from, rather than from a `NEEDS_BROWSER`
flag somebody keeps in their head. A registry is browser-side if ANY id in ANY of its killer files
is a browser test — the conservative direction, because a killer DESELECTED by the fast tier would
report its mutant as a survivor.

THE VERDICT IS `flows.sweep_verdict`, not a fourth hand-rolled condition. CLI-4 was CLI-1 on another
verb and got that function rather than a third copy of the rule; this is the same shape a third time
(quiet is an allowlist; a sweep where NOTHING ran is exit 2, not "healthy"), so it gets the same
function. `QUIET` holds exactly one status, so a status added tomorrow is loud by default.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import prove_red  # noqa: E402  (after the path insert, deliberately)

from ultracua.flows import sweep_verdict  # noqa: E402

# THE SAME MANIFEST THE TIER MACHINERY IS CURRENTLY USING, override included (R4.142).
#
# `tier_marks.py merge` validates a CANDIDATE manifest by re-running the fast tier with
# `ULTRACUA_TIER_MANIFEST` pointing at it. `tests/_tiers.py` honours that; this file did not, so
# during validation the tier SELECTION came from the candidate while this registry's tier DERIVATION
# came from the committed file. A PR adding a mutation registry whose killer file is also new could
# therefore never converge: the killer is absent from the committed manifest, `_tier_of` refuses (as
# it should -- guessing is wrong in both directions), the fast tier goes red, and the merge reports
# "the fast tier is RED but no test launched a browser", which reads as a real product failure.
# Measured on this slice: three cells red, candidate refused, and no amount of re-running helps.
#
# The env NAME is imported from `tests/_tiers.py` rather than retyped -- two spellings of one
# variable is how one file silently stops honouring what the other does.
_MANIFEST_DEFAULT = ROOT / "tests" / ".browser_tests.json"
sys.path.insert(0, str(ROOT))
from tests._tiers import _MANIFEST_ENV  # noqa: E402
MANIFEST = Path(os.environ[_MANIFEST_ENV]) if os.getenv(_MANIFEST_ENV) else _MANIFEST_DEFAULT
REGISTRY_DIR = ROOT / "tests" / "mutations"

# QUIET IS AN ALLOWLIST, and it holds one entry. Everything else — an unregistered survivor, a stale
# find-text, a killer suite that does not pass unmutated, a registry with no mutants left in it — is
# loud without anybody having to think of it in advance.
QUIET = frozenset({"clean"})
# ...and what counts as having CHECKED something. `suite_dead` and `empty` are deliberately ABSENT:
# in both, not one mutant was scored, so a sweep made entirely of those checked nothing and must not
# be able to report health. (Clause 1 fires first and makes it exit 1 either way — the point is that
# the distinction survives into the report a human reads, not that it changes this exit code.)
WORKED = frozenset({"clean", "survivors", "broken"})

# `prove_red`'s exit codes, mapped to a status with a remedy. Keyed off the NAMED constants rather
# than off literals, so the day a fourth code is added the map is one edit in one place — and an
# exit code absent from here is not silently folded into an existing bucket (see `run_one`).
STATUS_OF = {
    0: "clean",
    prove_red.SURVIVORS: "survivors",
    prove_red.BROKEN: "broken",
    prove_red.SUITE_DEAD: "suite_dead",
}


@dataclass
class RegistryResult:
    """One registry's outcome. `status` is what `sweep_verdict` reads; the rest is for the human."""

    name: str
    status: str
    detail: str
    mutants: int
    browser: bool
    output: str = ""


def registries() -> "list[Path]":
    """Every registry in `tests/mutations/`, DERIVED. A seventh is swept without touching this file."""
    return sorted(p for p in REGISTRY_DIR.glob("*.py") if not p.name.startswith("_"))


def _manifest() -> "tuple[set[str], set[str]]":
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return set(data["browser"]), set(data["fast"])


def _files_of(ids: "set[str]") -> "dict[str, int]":
    out: dict[str, int] = {}
    for nid in ids:
        out[nid.split("::")[0]] = out.get(nid.split("::")[0], 0) + 1
    return out


def killer_files(path: Path) -> "list[str]":
    """Every distinct killer file this registry's mutants name, via `prove_red`'s own resolution.

    Through `prove_red.killers_of` rather than by re-reading `KILLED_BY` here, because two readings
    of "what is this mutant scored by" is how the sweep ends up classifying a registry by one suite
    and running it against another.
    """
    mod = prove_red._load(path)
    out: set[str] = set()
    for mutant in mod.MUTANTS:
        out.update(prove_red.killers_of(mod, mutant))
    return sorted(out)


def needs_browser(path: Path) -> "tuple[bool, list[str]]":
    """(does this registry's killer suite touch a browser, which files make it so).

    A killer file the manifest has never heard of RAISES rather than defaulting either way. Guessing
    `browser` would silently drop it out of the merge gate; guessing `fast` would put a launch in a
    job that raises on one. The remedy — regenerate the manifest — is named in the message, and is
    the same two-phase regeneration every slice here already does.
    """
    browser_ids, fast_ids = _manifest()
    known = _files_of(browser_ids | fast_ids)
    browser_files = _files_of(browser_ids)
    unknown = [f for f in killer_files(path) if f not in known]
    if unknown:
        raise SystemExit(
            f"{path.name} names killer file(s) the tier manifest has never seen: {unknown}. The "
            f"tier split of this registry cannot be derived, and guessing it is wrong in both "
            f"directions (guess `browser` and it silently leaves the merge gate; guess `fast` and "
            f"it launches in a job whose conftest RAISES on a launch). Regenerate the manifest: "
            f"`pytest -q --store-browser-marks` then `python scripts/derive_test_tiers.py`."
        )
    hits = [f for f in killer_files(path) if browser_files.get(f)]
    return bool(hits), hits


def run_one(path: Path, *, browser: bool) -> RegistryResult:
    mod = prove_red._load(path)
    n = len(mod.MUTANTS)
    if n == 0:
        # `prove_red` on an emptied registry prints "0 killed, 0 survived, 0 broken of 0" and exits
        # ZERO. That is a registry reporting the suite as proven while proving nothing, so it is
        # caught HERE rather than left to whoever next reads a green log.
        return RegistryResult(path.name, "empty", "the registry holds NO mutants", 0, browser)

    env = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        env[k] = ""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "prove_red.py"), f"tests/mutations/{path.name}"],
        cwd=ROOT, capture_output=True, text=True, env=env)
    out = (proc.stdout or "") + (proc.stderr or "")
    # THREE LOUD STATUSES, THREE REMEDIES. The first draft had two, and the sweep's own first full run
    # showed why that is not enough: a version bump without `uv sync --all-groups` made `test_obs.py`
    # fail on the UNMUTATED tree, `prove_red`'s baseline guard caught it exactly as designed, and two
    # registries were reported as "an UNREGISTERED SURVIVOR, a stale KNOWN_SURVIVORS entry, or a dead
    # killer suite" — three causes behind one word, only one of which is a hole in the matrix. That is
    # the overloaded-verdict shape this repo already has a name for (a CI job's `cancelled` meaning an
    # apt hang, a superseding push and a real over-run), so the code says which.
    #
    # NO DEFAULT BUCKET. `.get(code, "broken")` is what the first draft had, and it is B3's
    # `CODE_FAMILY` defect one instrument over: a bucket that absorbs whatever nobody classified,
    # reported with a confident label. An exit code this map has never heard of — a crash, a
    # segfault, pytest's own 5 — gets its own status naming the number.
    status = STATUS_OF.get(proc.returncode, f"unknown_exit_{proc.returncode}")
    detail = {
        "clean": f"{n} mutant(s), every one accounted for",
        "survivors": "an UNREGISTERED SURVIVOR, or a KNOWN_SURVIVORS entry that is now killed — add "
                     "a cell, or register it with a reason and a finding id",
        "broken": "a STALE or AMBIGUOUS mutation — the site moved and the mutation now proves nothing",
        "suite_dead": "the KILLER SUITE does not pass on the unmutated tree, so NOTHING was proved "
                      "here — every mutant would have been scored by a broken suite. Fix the suite "
                      "first (a stale venv after a version bump is the commonest cause: "
                      "`uv sync --all-groups`)",
    }.get(status, f"`prove_red` exited {proc.returncode}, which this sweep has no name for — read "
                  f"its output above rather than trusting any label here")
    return RegistryResult(path.name, status, detail, n, browser, out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["fast", "all"], default="all",
                    help="`fast` runs only the registries whose killer suites launch no browser — "
                         "what the merge gate can afford. Default `all` needs Chromium.")
    ap.add_argument("--list", action="store_true", help="print the plan and exit 0 without running")
    args = ap.parse_args()

    plan = []
    for path in registries():
        browser, why = needs_browser(path)
        plan.append((path, browser, why))

    selected = [(p, b) for p, b, _ in plan if not (args.tier == "fast" and b)]
    print(f"{len(plan)} registry/registries; tier={args.tier}; selected {len(selected)}\n")
    for path, browser, why in plan:
        side = "browser" if browser else "fast   "
        mark = " " if (path, browser) in selected else "-"
        because = f"  (via {', '.join(why)})" if why else ""
        print(f" {mark} {side}  {path.name}{because}")
    print()
    if args.list:
        return 0

    results = [run_one(p, browser=b) for p, b in selected]
    for r in results:
        if r.status != "clean" and r.output:
            print(f"===== {r.name} =====\n{r.output}")
    print()
    for r in results:
        print(f"  {r.status:10} {r.name:26} {r.detail}")

    verdict = sweep_verdict(results, quiet=QUIET, worked=WORKED, noun="mutation registry")
    print(f"\n{verdict.summary}")
    if verdict.alerts:
        print("\nEach of these is a hole in the matrix, not a bug in the mutation. Read the registry's "
              "output above: add a cell that kills it, re-express a stale mutation against the site "
              "that moved, or register the survivor with a reason and a finding id.")
        for r in verdict.alerts:
            print(f"  {r.name}: {r.detail}")
    return verdict.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
