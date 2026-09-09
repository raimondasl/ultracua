"""Which in-page JS payloads can break SILENTLY? 0.5's real question, and it is free (0.180.0).

    python -m benchmarks.js_payload_probe            # the classification, offline, ~1 s
    python -m benchmarks.js_payload_probe --break _MUTATION_CTX_JS
                                                     # write a mutated tree and print what to run

Roughly 1,150 lines of in-page JavaScript live in `src/ultracua/` as Python strings joined by `+`,
and **nothing anywhere checks their syntax** -- not the suite, not CI. reshape-plan step 0.5 proposed
`node --check` over every assembled payload. Whether that is worth building turns on ONE question,
which this probe answers.

WHY A SYNTAX ERROR IS USUALLY HARMLESS TO REASON ABOUT. Payloads are assembled at import time, so
the text is identical every run: a broken one breaks EVERY test that drives it. The 599-test browser
suite is already the guard, and `node --check` would only move the failure from 40 minutes to 1
second. That is convenience, not correctness.

THE EXCEPTION, AND THE ONLY REASON THIS QUESTION IS INTERESTING. Five `evaluate` call sites CATCH the
exception and return a benign value -- `{}`, `""`, `"unavailable"` -- deliberately, so the mechanism
degrades to prior behaviour rather than failing a run. For a payload consumed ONLY by such sites, a
syntax error makes the mechanism a silent no-op **while the suite stays green, because failing open
is the designed behaviour**. Three of the five are write-safety guards, so the stake is inviolable #3:

    _ROW_OF_JS         the row-containment guard   -> returns "" == refuse   (R3.7)
    SCOPE_JS           the mutation gate's scope   -> returns "" == fall back (R4.148)
    _MUTATION_CTX_JS   the pre-act classifier ctx  -> returns {} == KEYWORD-ONLY, 45% recall
    _FOCUSED_REF_JS    the press gate's focus ref
    _DETECT_JS         webmcp tool detection

MEASURED AT 0.180.0 AND THE ANSWER IS NO -- ALL FIVE ARE CAUGHT (R4.158). Each was broken with an
unbalanced brace and its natural killers run: `_MUTATION_CTX_JS` 7 failed, `_ROW_OF_JS` 5,
`SCOPE_JS` 3, `_FOCUSED_REF_JS` 3, `_DETECT_JS` 1. So the fail-open class does NOT hide a syntax
error, and 0.5's syntax check buys feedback speed rather than safety. Re-derive with `--break`.

THE CLASSIFICATION IS WHAT THIS SHIPS FOR, not the verdict: "which payload can degrade silently" is
a question anyone touching this surface wants answered, and it is DERIVED from the tree rather than
typed, so a new fail-open consumer appears here without anyone remembering to add it.
"""

from __future__ import annotations

import argparse
import ast
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "ultracua"

# The Playwright entry points that hand a payload to the page.
CONSUMERS = frozenset({"evaluate", "evaluate_handle", "add_init_script", "eval_on_selector"})


def payloads() -> dict:
    """Every module-level `*_JS` string, derived from the tree."""
    found: dict = {}
    for p in sorted(SRC.rglob("*.py")):
        for n in ast.parse(p.read_text(encoding="utf-8")).body:
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id.endswith("_JS"):
                        found[t.id] = p.name
    return found


def _swallowing_handler(node, parents) -> str:
    """The nearest enclosing `except` that does NOT re-raise, or "" if the exception propagates.

    A handler that re-raises is NOT fail-open: the caller still sees the failure, so a broken payload
    breaks the run and the suite is the guard.
    """
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, ast.Try):
            for h in cur.handlers:
                if not any(isinstance(x, ast.Raise) for x in ast.walk(h)):
                    return ast.unparse(h).splitlines()[0][:60]
    return ""


def classify() -> dict:
    """-> {payload: [handler-or-"" per consuming site]}. A payload absent from the result is spliced
    into another payload rather than evaluated directly, so it is covered by whatever covers its
    includer."""
    known = payloads()
    sites: dict = {}
    for p in sorted(SRC.rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in CONSUMERS and node.args):
                continue
            for nm in {n.id for n in ast.walk(node.args[0])
                       if isinstance(n, ast.Name) and n.id in known}:
                sites.setdefault(nm, []).append(_swallowing_handler(node, parents))
    return sites


def break_payload(name: str) -> Path:
    """Copy `src/` and make ONE payload syntactically invalid, for a real red/green measurement.

    An unbalanced brace, not a semantic edit: the point is that the JS PARSER rejects it, which is
    exactly what `node --check` would have caught at edit time.
    """
    known = payloads()
    if name not in known:
        raise SystemExit(f"no payload named {name!r}; known: {sorted(known)}")
    work = Path(tempfile.mkdtemp(prefix="js-break-"))
    shutil.copytree(SRC.parent, work / "src")
    p = work / "src" / "ultracua" / known[name]
    s = p.read_text(encoding="utf-8")
    i = s.index(name + " = ")
    j = s.index('"""', s.index('"""', i) + 3) + 3
    head, body, tail = s[:i], s[i:j], s[j:]
    for probe in ("=> {", "() {", "{"):
        if probe in body:
            body = body.replace(probe, probe + " if (", 1)
            break
    else:
        raise SystemExit(
            f"STALE: {name} has no brace to unbalance -- the payload's shape moved and this probe "
            f"would report a clean measurement of nothing (prove_red's rule).")
    p.write_text(head + body + tail, encoding="utf-8")
    return work / "src"


def main() -> int:
    ap = argparse.ArgumentParser(prog="benchmarks.js_payload_probe")
    ap.add_argument("--break", dest="brk", metavar="PAYLOAD",
                    help="write a tree with this payload syntactically broken, and say what to run")
    args = ap.parse_args()

    if args.brk:
        src = break_payload(args.brk)
        print(f"  broken tree: {src}")
        print(f"  run its killers against it, e.g.:\n")
        print(f'    ANTHROPIC_API_KEY= PYTHONPATH="{src}" uv run --no-sync pytest <killer files> -q')
        print("\n  RED means the suite already guards this payload and a syntax check adds only")
        print("  speed. GREEN means a syntax error in it is invisible -- which is what 0.5 is for.")
        return 0

    sites, known = classify(), payloads()
    print(f"\n  {len(known)} module-level *_JS payloads across "
          f"{len(set(known.values()))} modules; {len(sites)} evaluated directly\n")
    print(f"  {'payload':22} {'module':14} {'sites':>5}  consumers")
    blind = []
    for name in sorted(sites):
        handlers = sites[name]
        kind = ("FAIL-OPEN only" if all(handlers)
                else "propagates" if not any(handlers) else "mixed")
        if all(handlers):
            blind.append(name)
        print(f"  {name:22} {known[name]:14} {len(handlers):5}  {kind}")

    spliced = sorted(set(known) - set(sites))
    print(f"\n  spliced into another payload (covered by its includer): {spliced or 'none'}")
    print(f"\n  CAN DEGRADE SILENTLY: {sorted(blind) or 'none'}")
    print("  Those are the only payloads whose syntax error the suite could miss, because failing")
    print("  open is their DESIGNED behaviour. Measured at 0.180.0: all five are caught anyway")
    print("  (R4.158) -- re-derive with `--break <name>` rather than trusting this line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
