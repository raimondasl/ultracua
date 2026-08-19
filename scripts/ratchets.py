"""Ratchets: shapes that may only ever SHRINK, counted by AST rather than by hand.

Step 0.4a of `docs/reshape-plan.md`. Phase 1 removes six shapes that manufacture the recurring defect
classes, and 1.4, 1.5 and 1.6 each name "a ratchet" in their *Pinned by* cell. This is that instrument,
landed BEFORE any `src/` change so the counts are frozen before the edits that move them.

    uv run --no-sync python scripts/ratchets.py            # --check (what the suite runs)
    uv run --no-sync python scripts/ratchets.py --print    # every site, per ratchet
    uv run --no-sync python scripts/ratchets.py --update   # re-seed after a legitimate change

**Why AST and not grep, with the receipts.** The plan's own §1 table states these counts, and they were
produced by grep. Re-derived here:

| shape | plan says | AST says | why they differ |
|---|---|---|---|
| `spec.mutate is (not) None` | 33 | **27** | grep counted the shape inside COMMENTS — three findings' worth of prose discusses this exact line |
| `flow_key(spec.goal, …)` | 24 | **24** | matches, once the derivation requires all three args to be attributes of one receiver |
| bare `raise FlowReplayError(` | 24 | **24** | matches |
| `raise SystemExit` in `cli.py` | 34 | **34** | matches |
| `record.<field> =` sites | 10 | **16** | grep-era number; and a naive derivation also catches `obs.py`'s `record.run_id`, which is a LOGGING record, not the `RunRecord` |

Two of the five were wrong and one was measuring a different thing — which is the argument for the file
you are reading. A number typed into prose is a claim; a number derived from the tree is a measurement.

**The rules, and why a shrink is also a failure.**

* **growth** fails: the shape is being removed, not added. The message names the new sites.
* **shrink** fails too, asking for `--update`. A ratchet that silently tolerates progress stops
  ratcheting: the next regression is measured against the OLD, looser number and slips in under it.
* **a derivation that finds ZERO** while its baseline is positive fails with its own message. That is
  almost always a broken pattern rather than a fixed shape — the same rule `scripts/prove_red.py`
  applies to a mutation whose find-text has gone stale, and for the same reason: a silent zero reports
  the codebase as cleaner than it is.
* a ratchet in the baseline with no derivation, or a derivation with no baseline, fails. Neither half
  may drift out from under the other.

Anti-vacuity is not left to inspection: `tests/test_ratchets.py` INJECTS one extra site per ratchet into
a scratch copy of `src/` and requires the count to rise by exactly one. A derivation that matches
nothing, or matches everything, cannot pass that.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "ultracua"
BASELINE = ROOT / "tests" / ".ratchets.json"

# The engine chain 1.1 makes keyword-only. Named here so the ratchet and the step agree on the set.
ENGINE_CHAIN = ("_learn", "_learn_n", "_replay", "_verify_by_replay", "_replay_step", "_author_steps")


@dataclass(frozen=True)
class Site:
    file: str          # repo-relative, forward slashes
    line: int
    note: str = ""

    def __str__(self) -> str:
        return f"{self.file}:{self.line}" + (f"  {self.note}" if self.note else "")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


# Six derivations x every module was six parses of the same file, and this runs in the FAST TIER — the
# pre-commit signal 0.1 exists to keep under a minute. Keyed on the stat, so a scratch copy mutated by
# `tests/test_ratchets.py` is re-parsed rather than served stale.
_PARSED: dict = {}


def _modules(only: str | None = None):
    for path in sorted(SRC.rglob("*.py")):
        if only is not None and path.name != only:
            continue
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        tree = _PARSED.get(key)
        if tree is None:
            tree = _PARSED[key] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        yield path, tree


def _dotted(node) -> str:
    """`a.b.c` for an Attribute/Name chain, "" for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


# ---------------------------------------------------------------------------------------------------
# The derivations. Each returns the SITES, never a number — the number is len().

def derive_spec_mutate_raw() -> list:
    """`<x>.mutate is (not) None` — the raw write predicate 1.6 replaces with named questions.

    AST-only, so the ~6 occurrences of this text inside comments and docstrings do not count. That
    difference is the whole reason this is not a grep.
    """
    out = []
    for path, tree in _modules():
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Compare) and len(n.ops) == 1
                    and isinstance(n.ops[0], (ast.Is, ast.IsNot))):
                continue
            left = _dotted(n.left)
            right = n.comparators[0]
            if left.endswith(".mutate") and isinstance(right, ast.Constant) and right.value is None:
                op = "is None" if isinstance(n.ops[0], ast.Is) else "is not None"
                out.append(Site(_rel(path), n.lineno, f"{left} {op}"))
    return out


def derive_flow_key_transcriptions() -> list:
    """`flow_key(x.goal, x.start_url, x.scope)` — three attributes of ONE receiver.

    Keyed on the SHAPE rather than on the receiver being spelled `spec`, so renaming the variable does
    not un-ratchet the site. `FlowSpec.key` (step 1.6) is what removes them.
    """
    out = []
    for path, tree in _modules():
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and _dotted(n.func).split(".")[-1] == "flow_key"):
                continue
            if len(n.args) != 3 or n.keywords:
                continue
            recvs, attrs = set(), []
            for a in n.args:
                if not isinstance(a, ast.Attribute) or not isinstance(a.value, ast.Name):
                    recvs.add(None)
                    break
                recvs.add(a.value.id)
                attrs.append(a.attr)
            if len(recvs) == 1 and None not in recvs:
                out.append(Site(_rel(path), n.lineno, f"{recvs.pop()}.{'/'.join(attrs)}"))
    return out


def derive_bare_flow_replay_error() -> list:
    """`raise FlowReplayError(` — the base class, so every one of them carries `code='replay_error'`.

    Step 1.4 gives the ~10 refusal kinds distinct codes; B3 must not freeze its vocabulary before then.
    """
    out = []
    for path, tree in _modules():
        for n in ast.walk(tree):
            if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) \
                    and _dotted(n.exc.func).split(".")[-1] == "FlowReplayError":
                out.append(Site(_rel(path), n.lineno))
    return out


def derive_cli_system_exit() -> list:
    """`raise SystemExit` in `cli.py` — Phase 3's single-funnel candidate (34 -> 2)."""
    out = []
    for path, tree in _modules(only="cli.py"):
        for n in ast.walk(tree):
            if not isinstance(n, ast.Raise) or n.exc is None:
                continue
            target = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
            if _dotted(target).split(".")[-1] == "SystemExit":
                out.append(Site(_rel(path), n.lineno))
    return out


def derive_run_record_write_sites() -> list:
    """Assignment statements writing `record.<field>` in `flows.py` from OUTSIDE `_RecordSink`.

    REDEFINED AT 1.5's AUDIT ROUND, and the reason matters more than the number. This counted every
    such statement in the file, which was the right measure while the record was written at sixteen
    sites across two functions. Once they were collapsed into one class the count stopped measuring
    anything: two of the audit's own fixes — clearing a stale `note`, and forcing usage to UNKNOWN when
    the fold breaks — ADD a write inside the sink, and the ratchet failed for them. Re-seeding upward
    on growth is how a ratchet becomes theatre, so the derivation moved to the invariant that actually
    holds: **writes outside the sink**, whose only acceptable value is zero.

    Its non-vacuity is not taken on trust — `_writes_in_flows` must still find the writes INSIDE the
    sink, and `assert_pattern_is_live` below fails if it does not, so a broken pattern cannot read as a
    clean codebase. `tests/test_replay_exit_matrix.py::test_every_record_write_is_inside_the_sink` is
    the sharper guard: it keys on `RunRecord`'s field set rather than on a variable's name.
    """
    inside, outside = _writes_in_flows()
    assert inside, (
        "the `record.<field> =` pattern matches NOTHING inside `_RecordSink` — the derivation is "
        "broken, not the codebase clean. That is the stale-derivation failure this file refuses.")
    return outside


def _writes_in_flows():
    """(inside the sink, outside it) — the same walk, split by containment."""
    inside, outside = [], []
    for path, tree in _modules(only="flows.py"):
        sink = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.ClassDef) and n.name == "_RecordSink"), None)
        in_sink = {id(n) for n in ast.walk(sink)} if sink is not None else set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign):
                targets = n.targets
            elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
                targets = [n.target]
            else:
                continue
            flat = []
            for t_ in targets:
                flat.extend(t_.elts if isinstance(t_, ast.Tuple) else [t_])
            names = [_dotted(t_) for t_ in flat if isinstance(t_, ast.Attribute)]
            hit = [nm for nm in names if nm.startswith("record.")]
            if hit:
                site = Site(_rel(path), n.lineno, ", ".join(hit))
                (inside if id(n) in in_sink else outside).append(site)
    return inside, outside


def _unused_legacy_record_walk() -> list:
    out = []
    for path, tree in _modules(only="flows.py"):
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign):
                targets = n.targets
            elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
                targets = [n.target]
            else:
                continue
            flat = []
            for t in targets:
                flat.extend(t.elts if isinstance(t, ast.Tuple) else [t])
            names = [_dotted(t) for t in flat if isinstance(t, ast.Attribute)]
            hit = [nm for nm in names if nm.startswith("record.")]
            if hit:
                out.append(Site(_rel(path), n.lineno, ", ".join(hit)))
    return out


def derive_engine_positional_params() -> list:
    """Parameters the engine chain accepts POSITIONALLY — what step 1.1's `*` removes.

    One site per parameter, not per function, so the ratchet moves when a single argument becomes
    keyword-only rather than only when a whole signature is converted. The identity prefix each function
    legitimately takes positionally stays in the count until 1.1 decides where the `*` goes; the number
    is a baseline to drive down, not a target that is already correct.
    """
    out = []
    for path, tree in _modules():
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) or n.name not in ENGINE_CHAIN:
                continue
            positional = list(n.args.posonlyargs) + list(n.args.args)
            for a in positional:
                out.append(Site(_rel(path), n.lineno, f"{n.name}({a.arg})"))
    return out


# A ratchet whose END STATE is zero. Normally a derivation matching nothing is the stale-pattern
# failure this file refuses, so an exemption has to be earned: `derive_run_record_write_sites` proves
# its own pattern is live by asserting it still finds the writes INSIDE `_RecordSink` before returning
# the ones outside. Nothing else may join this set without the same proof.
MAY_BE_ZERO = frozenset({"run_record_write_sites"})


RATCHETS = {
    "spec_mutate_raw": (derive_spec_mutate_raw, "reshape-plan 1.6 — WriteClass named questions"),
    "flow_key_transcriptions": (derive_flow_key_transcriptions, "reshape-plan 1.6 — FlowSpec.key"),
    "bare_flow_replay_error": (derive_bare_flow_replay_error, "reshape-plan 1.4 — distinct codes"),
    "cli_system_exit": (derive_cli_system_exit, "reshape-plan Phase 3 — one SystemExit funnel"),
    "run_record_write_sites": (derive_run_record_write_sites,
                               "reshape-plan 1.5 — THE SINK (landed; the invariant is now CONTAINMENT, "
                               "so the only acceptable value is 0)"),
    "engine_positional_params": (derive_engine_positional_params, "reshape-plan 1.1 — keyword-only chain"),
}


# ---------------------------------------------------------------------------------------------------
# Baseline + verdict.

def derive_all() -> dict:
    return {name: fn() for name, (fn, _) in RATCHETS.items()}


def read_baseline(path: Path | None = None) -> dict:
    path = path or BASELINE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("ratchets", {})


def write_baseline(sites: dict, path: Path | None = None) -> None:
    path = path or BASELINE
    payload = {
        "_comment": (
            "DERIVED by scripts/ratchets.py, never hand-edited. Each entry counts a SHAPE that Phase 1 "
            "removes; the number may only shrink, and a shrink must be locked in with `--update` in the "
            "same diff that earned it. Regenerate with `python scripts/ratchets.py --update`."
        ),
        "ratchets": {
            name: {
                "total": len(hits),
                "by_file": {f: sum(1 for h in hits if h.file == f)
                            for f in sorted({h.file for h in hits})},
                "disposition": RATCHETS[name][1],
            }
            for name, hits in sorted(sites.items())
        },
    }
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
        fh.write("\n")


def verdict(sites: dict, baseline: dict) -> list:
    """Every problem, as (ratchet, message). Empty means the ratchets hold."""
    problems = []
    for name in sorted(set(sites) | set(baseline)):
        if name not in baseline:
            problems.append((name, "derived but has no baseline entry — run `--update` to seed it"))
            continue
        if name not in sites:
            problems.append((name, "in the baseline but no derivation defines it any more"))
            continue
        hits, want = sites[name], baseline[name]["total"]
        got = len(hits)
        if got == 0 and want > 0:
            problems.append((name, (
                f"the derivation now matches NOTHING while the baseline expects {want}. That is almost "
                f"always a broken pattern, not a fixed shape — a silent zero reports the codebase as "
                f"cleaner than it is. Fix the derivation, or `--update` if the shape is genuinely gone."
            )))
        elif got > want:
            # Name the files that actually GREW, from the per-file baseline — not the last N sites in
            # iteration order. The first arming run of this file reported an innocent line in another
            # module, which is worse than reporting nothing: it sends the reader to code that is fine.
            then = baseline[name].get("by_file", {})
            now = {f: sum(1 for h in hits if h.file == f) for f in {h.file for h in hits}}
            grew = {f: (then.get(f, 0), n) for f, n in sorted(now.items()) if n > then.get(f, 0)}
            detail = []
            for f, (was, is_) in grew.items():
                detail.append(f"{f}: {was} -> {is_}")
                detail += [f"    {h}" for h in hits if h.file == f][:6]
            if not grew:  # same total per file, so the growth is a file the baseline never saw
                detail = [str(h) for h in hits[:6]]
            problems.append((name, (
                f"GREW {want} -> {got}. This shape is being REMOVED, not added ({RATCHETS[name][1]}). "
                f"The file(s) that grew:\n      " + "\n      ".join(detail)
            )))
        elif got < want:
            problems.append((name, (
                f"shrank {want} -> {got} — a win, but it must be LOCKED IN: run "
                f"`python scripts/ratchets.py --update` in the same diff, or the next regression is "
                f"measured against the old, looser number."
            )))
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update", action="store_true", help="re-seed the baseline from the tree")
    ap.add_argument("--print", dest="show", action="store_true", help="print every site")
    args = ap.parse_args(argv)

    sites = derive_all()
    if args.update:
        write_baseline(sites)
        print(f"wrote {BASELINE.relative_to(ROOT).as_posix()}")
        for name, hits in sorted(sites.items()):
            print(f"  {name:26} {len(hits):4}")
        return 0

    if args.show:
        for name, hits in sorted(sites.items()):
            print(f"\n{name}  ({len(hits)})  -> {RATCHETS[name][1]}")
            for h in hits:
                print(f"    {h}")
        return 0

    problems = verdict(sites, read_baseline())
    for name, hits in sorted(sites.items()):
        want = read_baseline().get(name, {}).get("total")
        flag = "ok " if not any(p[0] == name for p in problems) else "FAIL"
        print(f"  {flag} {name:26} {len(hits):4}  (baseline {want})")
    if problems:
        print()
        for name, msg in problems:
            print(f"{name}: {msg}")
        return 1
    print("\nevery ratchet holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
