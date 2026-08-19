"""What the tier manifest COSTS to re-derive, measured rather than complained about.

Every PR that changes the test collection needs `pytest -q --store-browser-marks` — a FULL run, because
`guard_full_collection` refuses a narrowed one — followed by `scripts/derive_test_tiers.py`. That is a
~32-minute tax on a slice that may have added five tests, and there is an obvious cheaper design:
`--store-browser-marks` could MERGE new ids into the manifest instead of rewriting it, so only the new
tests need running.

This file exists because that trade should be decided by data, and the two numbers it needs are of
different kinds:

* **the deltas** — how each manifest revision differed from the last — are DERIVED FROM GIT here, never
  typed. `git log --follow` over the manifest gives every revision, and the classification moves fall
  out of set arithmetic on the two lists.
* **the timings** — what each re-derivation actually cost in wall-clock — are appended by the pipeline
  itself (`conftest.py`'s sessionfinish for the marks phase, `derive_test_tiers.py` for the fixed-point
  loop) into `tests/.manifest_cost.jsonl`. Nothing here is hand-entered; a run that was never measured
  is REPORTED AS UNMEASURED rather than estimated, which is this repo's standing rule for a field it
  would otherwise have to guess.

    uv run --no-sync python scripts/manifest_cost.py report

**What the trade actually costs, stated precisely, because the obvious statement of it is wrong.** The
merge design does NOT weaken the "a deleted test lingers" guarantee: `test_the_manifest_covers_
everything_this_run_collected` checks both directions (`known - collected` as well as
`collected - known`) and is a fast test, so a deleted or renamed test is caught in seconds by an
instrument that has nothing to do with this one. What merging loses is **DE-CLASSIFICATION**: a test
that used to launch a browser and no longer does keeps its browser mark forever, still collects, and so
never trips that guard — it is simply deselected from the fast tier for the rest of time. The failure is
silent, cumulative and one-directional: the fast tier decays toward nothing while every job stays green.

That is not hypothetical. Of the three manifest deltas in git so far, ONE de-classified three tests —
and the three were `tests/test_tiers.py`'s own arming cells, which the merge design would have kept
deselected from the very tier they exist to validate.

**Two kinds of de-classification, and the counter above only sees one.** Measured on the run that
seeded this ledger: the marks phase alone de-classified FIFTEEN tests, all of `tests/test_drift_bench.py`
— pure attribution noise, because whichever sibling primes the shared bench is charged for the launch —
and the fixed-point loop promoted every one of them back in a single round. The committed manifest is
therefore always a fixed point, and the deltas derived here are committed-to-committed, so what the
`declass` column counts is the GENUINE kind: a test that really stopped launching. That is the kind
merging would freeze. (It would suppress the noise as well, which sounds like a bonus and is the same
mechanism: merging cannot tell the two apart either, and it resolves both by never letting go.)

**A second optimisation, measured and refused.** "Only re-derive when the collection changed" saves
nothing: all three deltas so far added new ids (5, 5, 26), so a collection fingerprint would have gated
zero runs while costing a 4-second collect on every PR.

**What this ledger CANNOT see: host load.** It records wall-clock and nothing about what else the
machine was doing. Measured at 0.109.0, the same suite recorded 39m30s where the three previous runs
recorded ~32m, and the fast tier read 84 s in the same window against 68.0 s and 67.2 s on two clean
re-runs minutes later — so roughly a fifth of that entry is load, not work. The rule below reads a
CUMULATIVE total against a 4-hour threshold, which is the direction that matters (an inflated entry
brings the trade forward, never delays it), but do not quote a single row as the cost of a re-derivation.
This repo's standing rule applies to its own instruments: a timing taken under unknown load is not
evidence.

**The alternative that costs neither.** CI already runs the full suite on every PR, sharded two ways on
two OSes. Marks written per shard and merged in a following job would give a full derivation at ZERO
marginal minutes, on both platforms, with de-classification intact. It is a CANDIDATE, deliberately
not built yet and not yet a numbered step in `docs/reshape-plan.md`: the rule below is what says when it
is worth building, and building it first would be the same "fix before measuring" this file exists to
avoid.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_REL = "tests/.browser_tests.json"
LEDGER = ROOT / "tests" / ".manifest_cost.jsonl"

PHASES = ("marks", "fixed_point")

# The rule, executable rather than argued. Both halves must hold before the cheap design is taken.
SWITCH_AFTER_HOURS = 4.0     # cumulative MEASURED marks-phase wall time
CLEAN_WINDOW = 10            # consecutive recent deltas that must contain no de-classification


# ---------------------------------------------------------------------------------------------------
# The ledger: timings only, appended by the pipeline, never edited.

def append(record: dict) -> None:
    """Append one timing record. Called by the pipeline, not by a human."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def read_ledger(path: Path | None = None) -> list[dict]:
    path = path or LEDGER
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:  # a corrupt ledger is loud, never skipped
            raise ValueError(f"{path.name}:{n} is not JSON: {exc}") from exc
    return out


def head_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):  # pragma: no cover - no git
        return "unknown"


# ---------------------------------------------------------------------------------------------------
# The deltas: derived from git, never typed.

@dataclass
class Delta:
    rev: str
    date: str
    subject: str
    total: int
    new: int = 0
    gone: int = 0
    declassified: list = field(default_factory=list)   # browser -> fast: what merging would MISS
    promoted: int = 0                                  # fast -> browser: what the fixed point finds


def _revisions() -> list[tuple[str, str, str]]:
    out = subprocess.run(
        ["git", "log", "--reverse", "--format=%h|%ad|%s", "--date=short", "--follow", "--", MANIFEST_REL],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    rows = []
    for line in out.splitlines():
        rev, date, subject = line.split("|", 2)
        rows.append((rev, date, subject))
    return rows


def _manifest_at(rev: str) -> tuple[set, set]:
    blob = subprocess.run(["git", "show", f"{rev}:{MANIFEST_REL}"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout
    data = json.loads(blob)
    return set(data["browser"]), set(data["fast"])


def deltas_from_git() -> list[Delta]:
    """Every manifest revision, and how it differed from the one before it."""
    rows = []
    prev = None
    for rev, date, subject in _revisions():
        browser, fast = _manifest_at(rev)
        d = Delta(rev=rev, date=date, subject=subject, total=len(browser) + len(fast))
        if prev is not None:
            pb, pf = prev
            cur_all, prev_all = browser | fast, pb | pf
            d.new = len(cur_all - prev_all)
            d.gone = len(prev_all - cur_all)
            d.declassified = sorted(pb & fast)
            d.promoted = len(pf & browser)
        rows.append(d)
        prev = (browser, fast)
    return rows


def classify_moves(prev_browser, prev_fast, browser, fast) -> dict:
    """The set arithmetic, exposed so a test can arm it with synthetic manifests."""
    prev_browser, prev_fast = set(prev_browser), set(prev_fast)
    browser, fast = set(browser), set(fast)
    cur_all, prev_all = browser | fast, prev_browser | prev_fast
    return {
        "new": len(cur_all - prev_all),
        "gone": len(prev_all - cur_all),
        "declassified": sorted(prev_browser & fast),
        "promoted": sorted(prev_fast & browser),
    }


# ---------------------------------------------------------------------------------------------------
# The rule.

@dataclass
class Verdict:
    switch: bool
    reasons: list


def verdict(deltas: list, ledger: list,
            *, after_hours: float = SWITCH_AFTER_HOURS, window: int = CLEAN_WINDOW) -> Verdict:
    """May the rewrite be traded for a merge? Both halves must hold.

    (a) the tax is real — cumulative MEASURED marks-phase wall time exceeds `after_hours`; and
    (b) the thing merging would lose is not happening — no de-classification in the last `window`
        deltas.

    Unmeasured re-derivations count toward NEITHER: an unknown cost cannot argue for a cheaper design,
    which is the conservative direction here (the trade is one-way and its failure is silent).
    """
    hours = sum(r["seconds"] for r in ledger if r.get("phase") == "marks") / 3600.0
    recent = [d for d in deltas[1:]][-window:]           # deltas[0] is the first revision: no delta
    declassified = sum(len(d.declassified) for d in recent)

    reasons = []
    tax_is_real = hours > after_hours
    reasons.append(
        f"{'PASS' if tax_is_real else 'FAIL'} (a) measured marks-phase cost {hours:.2f} h "
        f"{'>' if tax_is_real else '<='} {after_hours} h threshold "
        f"({sum(1 for r in ledger if r.get('phase') == 'marks')} run(s) measured)"
    )
    clean = declassified == 0
    reasons.append(
        f"{'PASS' if clean else 'FAIL'} (b) {declassified} de-classification(s) in the last "
        f"{len(recent)} delta(s) — merging would have kept every one of them stale"
    )
    return Verdict(switch=tax_is_real and clean, reasons=reasons)


# ---------------------------------------------------------------------------------------------------
# Surfaces.

def cmd_report(_args) -> int:
    deltas = deltas_from_git()
    ledger = read_ledger()

    print("manifest re-derivations, DERIVED from git\n")
    hdr = f"  {'rev':9} {'date':11} {'total':>6} {'new':>5} {'gone':>5} {'declass':>8} {'promoted':>9}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for i, d in enumerate(deltas):
        if i == 0:
            print(f"  {d.rev:9} {d.date:11} {d.total:6} {'-':>5} {'-':>5} {'-':>8} {'-':>9}   (first)")
        else:
            print(f"  {d.rev:9} {d.date:11} {d.total:6} {d.new:5} {d.gone:5} "
                  f"{len(d.declassified):8} {d.promoted:9}")
            for nid in d.declassified:
                print(f"  {'':44}browser->fast  {nid}")

    print(f"\ntimings, APPENDED by the pipeline ({LEDGER.name})\n")
    if not ledger:
        print("  (empty — no re-derivation has run since the ledger was added)")
    else:
        for r in ledger:
            extra = f"rounds={r['rounds']}" if r.get("rounds") is not None else ""
            print(f"  {r.get('when', '?'):20} {r['phase']:12} {r['seconds']:8.1f}s  "
                  f"collected={r.get('collected', '?'):<6} {r.get('commit', '?'):9} {extra}")
    marks = [r for r in ledger if r.get("phase") == "marks"]
    fixed = [r for r in ledger if r.get("phase") == "fixed_point"]
    unmeasured = max(0, len(deltas) - len(marks))
    print(f"\n  marks phase:       {len(marks)} measured, {sum(r['seconds'] for r in marks) / 3600:.2f} h total")
    print(f"  fixed-point phase: {len(fixed)} measured, {sum(r['seconds'] for r in fixed) / 60:.1f} min total")
    print(f"  UNMEASURED:        {unmeasured} manifest revision(s) predate the ledger — not estimated")

    v = verdict(deltas, ledger)
    print(f"\nVERDICT: {'SWITCH to merge-mode' if v.switch else 'DO NOT switch to merge-mode'}")
    for r in v.reasons:
        print(f"  {r}")
    if not v.switch:
        print("  see this file's docstring for the alternative that costs neither (CI-derived marks)")
    return 0


def cmd_record(args) -> int:
    rec = {"phase": args.phase, "seconds": round(args.seconds, 1), "collected": args.collected,
           "commit": head_sha(), "when": args.when}
    if args.rounds is not None:
        rec["rounds"] = args.rounds
    if args.promoted is not None:
        rec["promoted"] = args.promoted
    append(rec)
    print(f"recorded: {rec}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report", help="git-derived deltas + ledger timings + the verdict")
    r = sub.add_parser("record", help="append one timing (the pipeline calls this, not a human)")
    r.add_argument("--phase", choices=PHASES, required=True)
    r.add_argument("--seconds", type=float, required=True)
    r.add_argument("--collected", type=int, required=True)
    r.add_argument("--rounds", type=int, default=None)
    r.add_argument("--promoted", type=int, default=None)
    r.add_argument("--when", default="")
    args = ap.parse_args(argv)
    return {"report": cmd_report, "record": cmd_record}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
