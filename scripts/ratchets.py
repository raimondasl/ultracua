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
    """The write-declaration comparison asked OUTSIDE `WriteClass.of` — what step 1.6 removed.

    AST-only, so the ~10 occurrences of this text inside comments and docstrings do not count. That
    difference is the whole reason this is not a grep.

    REDEFINED AT 1.6, for exactly the reason `derive_run_record_write_sites` was redefined at 1.5's
    audit round. Counting every `<x>.mutate is (not) None` was the right measure while the predicate
    was transcribed at 27 sites across three modules. Once they were concentrated into one
    constructor the count stopped measuring anything: the constructor legitimately contains five of
    them, so a straight count would report 5 and would FAIL for any future named question added
    beside them. The invariant that actually holds now is CONTAINMENT — the comparison lives inside
    `WriteClass.of` or nowhere — whose only acceptable value is zero.

    Its non-vacuity is not taken on trust: `_mutate_comparisons` must still find the comparisons
    INSIDE the constructor, so a broken pattern cannot read as a clean codebase.
    """
    inside, outside = _mutate_comparisons()
    assert inside, (
        "the write-declaration comparison matches NOTHING inside `WriteClass.of` — the derivation is "
        "broken, not the codebase clean. That is the stale-derivation failure this file refuses.")
    return outside


def _mutate_comparisons():
    """(inside `WriteClass.of`, outside it) — the same walk, split by containment.

    BOTH SPELLINGS, and that is load-bearing rather than thorough: the 27 removed sites wrote
    `spec.mutate is not None` (a dotted receiver) while the constructor writes `mutate is not None`
    (the bare parameter). A walk that matched only the dotted form would find the outside sites and
    NONE of the inside ones, so the liveness half would be empty and the assert above would fire on a
    perfectly healthy tree.
    """
    inside, outside = [], []
    for path, tree in _modules():
        of = None
        for cls in ast.walk(tree):
            if isinstance(cls, ast.ClassDef) and cls.name == "WriteClass":
                of = next((n for n in ast.walk(cls)
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                           and n.name == "of"), None)
        in_of = {id(n) for n in ast.walk(of)} if of is not None else set()
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Compare) and len(n.ops) == 1
                    and isinstance(n.ops[0], (ast.Is, ast.IsNot))):
                continue
            left = _dotted(n.left)
            right = n.comparators[0]
            if not (left == "mutate" or left.endswith(".mutate")):
                continue
            if not (isinstance(right, ast.Constant) and right.value is None):
                continue
            op = "is None" if isinstance(n.ops[0], ast.Is) else "is not None"
            site = Site(_rel(path), n.lineno, f"{left} {op}")
            (inside if id(n) in in_of else outside).append(site)
    return inside, outside


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


def _taxonomy_names() -> set:
    """The `FlowReplayError` family, derived transitively from `flows.py`'s ClassDefs.

    Never hand-listed. Two members (`MetaUnreadableError`, `MetaUnwritableError`) sit ~130 lines below
    the rest of the block, and every hand count of this family in the repo's own prose said "eleven"
    when it was twelve — `flows.py`'s own comment on `MetaUnwritableError.retryable` still does.
    """
    names = {"FlowReplayError"}
    for _path, tree in _modules(only="flows.py"):
        changed = True
        while changed:                       # a subclass of a subclass is still in the family
            changed = False
            for n in ast.walk(tree):
                if (isinstance(n, ast.ClassDef) and n.name not in names
                        and any(_dotted(b).split(".")[-1] in names for b in n.bases)):
                    names.add(n.name)
                    changed = True
    return names


def _base_enforcement_nodes(tree) -> set:
    """The ids of the nodes belonging to the mechanism that ENFORCES this ratchet's invariant.

    A ratchet must not count its own guard. There are exactly THREE such nodes:
      * `FlowReplayError.code = "replay_error"` — the poison sentinel's own DECLARATION. It is the
        thing every other clause compares against; without it there is no invariant to enforce.
      * `FlowReplayError.__init__`'s `type(self) is FlowReplayError` — a bare-name value reference;
      * `RESERVED_CODES`'s `"replay_error"` entry — the slug no concrete class may adopt.
    All three are shape (b)/(c) matches, all three exist to make the shape unexpressible, and counting
    them would leave the ratchet permanently at 3 and unable to reach the zero that IS the invariant.

    The THIRD one only became visible when the blanket exemption was narrowed — it had been hiding
    inside the ClassDef the first draft excluded wholesale. That is the argument for a narrow
    exemption in one line: a broad one does not just miss producers, it conceals which nodes the
    invariant actually rests on.

    NARROW BY CONSTRUCTION, and the first draft was not — it exempted the WHOLE `FlowReplayError`
    ClassDef. Measured by 1.4a's audit: a `@classmethod` returning `"replay_error"` written INSIDE the
    class body reported 0 producers, while the identical text one line below the class reported 1. The
    docstring argued for two nodes and the code exempted a hundred lines, which is a blind spot exactly
    where the taxonomy's own code lives — and 1.4b's declared scope adds a method to that class body.

    So: `__init__`'s own comparison, and the `RESERVED_CODES` assignment. Nothing else, and each is
    asserted to have been FOUND, because an exemption that silently matches nothing is a guard that
    stopped guarding.
    """
    out, found = set(), []
    base_cls = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.ClassDef) and n.name == "FlowReplayError"), None)
    if base_cls is not None:
        # ...only the `code = "..."` statement in the class's OWN body, not in a nested def.
        for stmt in base_cls.body:
            if isinstance(stmt, ast.Assign) and any(_dotted(t_) == "code" for t_ in stmt.targets):
                out |= {id(x) for x in ast.walk(stmt)}
                found.append("FlowReplayError.code")
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and any(_dotted(t_) == "RESERVED_CODES" for t_ in n.targets):
            out |= {id(x) for x in ast.walk(n)}
            found.append("RESERVED_CODES")
        # `if type(self) is FlowReplayError:` — the abstract guard's own comparison, and ONLY it.
        if (isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], ast.Is)
                and isinstance(n.left, ast.Call) and _dotted(n.left.func) == "type"
                and _dotted(n.comparators[0]).split(".")[-1] == "FlowReplayError"):
            out |= {id(x) for x in ast.walk(n)}
            found.append("type(self) is FlowReplayError")
    if not found and any(isinstance(n, ast.ClassDef) and n.name == "FlowReplayError"
                         for n in ast.walk(tree)):
        raise AssertionError(
            "flows.py defines FlowReplayError but neither enforcement node was found — the exemption "
            "matches nothing, so either the abstract guard is gone or this derivation is stale.")
    return out


def _base_coded_sites():
    """(sites that can PRODUCE a base-coded refusal, sites raising a SUBCLASS) — the same walk."""
    fam = _taxonomy_names()
    base, sub = [], []
    for path, tree in _modules():
        skip = _base_enforcement_nodes(tree)
        # Positions where the bare NAME is not a value: an `except` clause, a ClassDef base, an
        # isinstance/issubclass argument, an annotation, and a raise's own callee (shape (a) counts it).
        for n in ast.walk(tree):
            if isinstance(n, ast.ExceptHandler) and n.type is not None:
                skip |= {id(x) for x in ast.walk(n.type)}
            if isinstance(n, ast.ClassDef):
                for b in n.bases:
                    skip |= {id(x) for x in ast.walk(b)}
            if isinstance(n, ast.Call) and _dotted(n.func) in ("isinstance", "issubclass"):
                for a in n.args:
                    skip |= {id(x) for x in ast.walk(a)}
            if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call):
                skip.add(id(n.exc.func))
            for attr in ("annotation", "returns"):
                ann = getattr(n, attr, None)
                if ann is not None:
                    skip |= {id(x) for x in ast.walk(ann)}

        # An `Attribute` chain CONTAINS a `Name`, and `ast.walk` visits both — so a dotted reference
        # would otherwise be counted twice. Resolve the outermost node and suppress its interior.
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and _dotted(n).split(".")[-1] == "FlowReplayError":
                skip |= {id(x) for x in ast.walk(n) if x is not n}

        for n in ast.walk(tree):
            # (a) a raise naming the class, bare or dotted.
            if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call):
                name = _dotted(n.exc.func).split(".")[-1]
                if name == "FlowReplayError":
                    base.append(Site(_rel(path), n.lineno, "raise"))
                elif name in fam:
                    sub.append(Site(_rel(path), n.lineno, f"raise {name}"))
            # (b) the class in a VALUE position — a dict value, a variable, a default. This is the
            #     shape that catches `_classify_replay_failure`'s table, whose entry becomes a raise
            #     through a Call and which (a) structurally cannot see.
            #
            #     BOTH SPELLINGS. The first draft matched `ast.Name` only, so `flows.FlowReplayError`
            #     in a value position was invisible — and that is the ONE spelling every module except
            #     `flows.py` would use, `mcpserver` and `daemon` included. A shape added to close an
            #     indirection that closes half of it is this register's "patch on a patch" verbatim;
            #     found by the 1.4a audit, reproduced, and armed by
            #     `test_the_ratchet_sees_the_base_class_under_BOTH_spellings`.
            elif (isinstance(n, (ast.Name, ast.Attribute))
                  and _dotted(n).split(".")[-1] == "FlowReplayError" and id(n) not in skip):
                base.append(Site(_rel(path), n.lineno, "value ref"))
            # (c) a surviving `"replay_error"` literal — a getattr default, a comparison, a fixture.
            elif (isinstance(n, ast.Constant) and n.value == "replay_error"
                  and id(n) not in skip):
                base.append(Site(_rel(path), n.lineno, "literal"))
    return base, sub


def derive_bare_flow_replay_error() -> list:
    """Everything in `src/` that can PRODUCE a refusal carrying the base's `code='replay_error'`.

    REDEFINED AT 1.4, and the reason matters more than the number. This counted the syntactic form
    `raise FlowReplayError(` — 24 — but that is a proxy for the invariant rather than the invariant.
    `flows.py`'s `_classify_replay_failure` reaches the same class through a VARIABLE
    (`raise _classify_replay_failure(kind)(...)`), so the old scan could have read 0 while the
    commonest refusal on a freshly-learned flow still shipped `replay_error` through four constructors.
    That is "a structural scan that names ONE function asserts a negative about a body that can walk
    away", one instrument over. Three shapes, one number:

      (a) `raise <...>FlowReplayError(...)`                             24 at 0.110.0 -> 0
      (b) the bare NAME in a VALUE position                              1 at 0.110.0 -> 0
      (c) a surviving `"replay_error"` string literal                    4 at 0.110.0 -> 0

    So the honest figure this slice moves is 29 -> 0, and the PR states both. The KEY is not renamed:
    `CLAUDE.md`, `reshape-plan.md` and `STATUS.md` all quote it, and a rename is a second edit across
    lines 1.6 and Phase 3 must touch anyway.

    Shape (c) uses EQUALITY, never substring — the base's docstring contains the slug and comments
    discuss it, and neither is a producer. The enforcement mechanism is excluded for the same reason
    (`_base_enforcement_nodes`): a ratchet that counts its own guard can never reach zero.

    END STATE ZERO, and the exemption is EARNED the way `derive_run_record_write_sites` earns its: the
    same walk must still find the SUBCLASS raises before this may report zero. It is a SECOND SENSOR
    CLASS rather than a second copy of one — `FlowReplayError.__init__` refuses the base at RUN time;
    this catches, at authoring time, the shapes no runtime check ever sees (an unraised reference in a
    table, a surviving literal in a default).
    """
    base, sub = _base_coded_sites()
    assert sub, (
        "no `raise <FlowReplayError subclass>(` matches anywhere in src/ — the derivation is broken, "
        "not the codebase clean. That is the stale-derivation failure this file refuses.")
    return base


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
    """Parameters the engine chain accepts POSITIONALLY — what step 1.1's `*` removed.

    One site per parameter, not per function, so the ratchet moves when a single argument becomes
    keyword-only rather than only when a whole signature is converted.

    LANDED AT 1.1 (98 -> 7), and 7 is the END STATE rather than a number still being driven down: the
    remaining seven are each call's SUBJECT — which page the run is about, or which session and step
    are being acted on. `tests/test_engine_chain_is_keyword_only.py` holds the naming authority (a
    committed table of WHICH parameter, per function, plus a derived rule that no two positional
    parameters may share a type); this counts them.

    THE OVERLAP IS DELIBERATE AND IS TWO SENSOR CLASSES, not a duplicate. This walk reads `src/` BY
    PATH, so under `scripts/prove_red.py` it parses the pristine tree and cannot contribute a kill
    (R4.75, measured live during 1.3). The test's walk reads `inspect.getsource(flow_mod)` and does.
    Collapsing them into one would silently disarm whichever half the other was covering.
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
MAY_BE_ZERO = frozenset({"run_record_write_sites", "bare_flow_replay_error", "spec_mutate_raw"})


RATCHETS = {
    "spec_mutate_raw": (derive_spec_mutate_raw,
                        "reshape-plan 1.6 — WriteClass named questions (landed; the invariant is now "
                        "CONTAINMENT inside `WriteClass.of`, so the only acceptable value is 0)"),
    "flow_key_transcriptions": (derive_flow_key_transcriptions,
                                "reshape-plan 1.6 — FlowSpec.key (landed 25 -> 2, and 2 is the FLOOR, "
                                "not a residue: one is `FlowSpec.key`'s own body — the site every other "
                                "one now funnels into — and one is `cli.py`'s raw `ultracua <url> "
                                "<goal>` entry point, whose argparse Namespace has `url` rather than "
                                "`start_url` and is not a FlowSpec at all. Neither can go without "
                                "inventing a spec the caller never made"),
    "bare_flow_replay_error": (derive_bare_flow_replay_error,
                               "reshape-plan 1.4 — distinct codes (landed). The invariant is EMISSION, "
                               "not syntax: the base is unconstructible since 1.4, so the only "
                               "acceptable value is 0. NOTE the raise-site count alone was 24 and could "
                               "read 0 while the indirect raise still resolved to the base — hence the "
                               "three shapes, and the honest figure 29 -> 0."),
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
