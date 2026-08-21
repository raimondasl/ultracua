"""Every refusal `flows.py` raises, bound to the class it raises — as DATA, not as 24 bespoke cells.

reshape-plan step 1.4. Until this slice, **24 refusals shared `code="replay_error"`**, so a caller —
and, imminently, B3's aggregator — could not tell a stale approval from an unbounded batch from a
missing env var. The step gives them distinct codes; this file is what stops the binding from drifting
afterwards.

WHY A TABLE AND NOT A CELL PER SUBCLASS. The plan's *Pinned by* cell asks for "a cell per subclass at
its raise site". Twenty-four bespoke cells is this register's most-repeated defect shape — a per-branch
test that a twenty-fifth branch is not covered by. So the typed half is enforced ONCE, structurally, by
`FlowReplayError.__init_subclass__` and by `test_inviolable_properties`'s distinct-code property; what
neither of those can see is the BINDING — which class each site actually raises — and that is what the
two committed tables below hold.

THE TABLES ARE THE EVIDENCE. Both were captured GREEN against unmodified `src/` before 1.4 touched a
line, so the diff to them in the same PR is the argued record of what the slice changed. Run with `-s`
to print them.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

FLOWS = pathlib.Path(__file__).parents[1] / "src" / "ultracua" / "flows.py"

# EVERY function that raises a taxonomy refusal, with its TOTAL count — the twelve typed sites
# included, not just the 24 the step re-classes. NAMED, and a missing one is a failure rather than a
# smaller world: a structural scan that names a function asserts a NEGATIVE about a body that can walk
# away from it, and 1.5 measured three scans disarmed at once by one function being split in two.
#
# Derived, then transcribed — the first draft of this table guessed `_preflight_row: 9` from the bare
# raises alone and the cell caught it at 12. The three it missed (`FlowQuarantineError` 2750,
# `UnkeyedWriteError` 2799, `StaleApprovalError` 2855) are the typed SIBLINGS of the nine, which is
# precisely the population 1.4 must not move by accident.
REFUSING_FUNCTIONS = {
    "_update_meta": 1,
    "_save_meta": 1,
    "_form_login": 2,
    "refresh_auth": 3,
    "approve": 1,
    "unapprove": 1,
    "_quarantine": 1,
    "validate_params": 4,
    "_preflight_row": 12,
    "_do_quarantine": 1,
    "_replay_body": 2,
    "run_batch": 7,
}
TOTAL_RAISE_SITES = 36

# (enclosing function, line, class raised). CAPTURED AGAINST UNMODIFIED `src/` AT 0.110.0, where every
# one of the 24 read `FlowReplayError` — which is exactly the finding 1.4 exists to remove.
SITE_TABLE = [
    ("_form_login",     1182, "FlowReplayError"),
    ("_form_login",     1200, "FlowReplayError"),
    ("refresh_auth",    1261, "FlowReplayError"),
    ("refresh_auth",    1263, "FlowReplayError"),
    ("refresh_auth",    1274, "FlowReplayError"),
    ("approve",         1515, "FlowReplayError"),
    ("unapprove",       1538, "FlowReplayError"),
    ("validate_params", 2704, "FlowReplayError"),
    ("_preflight_row",  2774, "FlowReplayError"),
    ("_preflight_row",  2778, "FlowReplayError"),
    ("_preflight_row",  2809, "FlowReplayError"),
    ("_preflight_row",  2820, "FlowReplayError"),
    ("_preflight_row",  2830, "FlowReplayError"),
    ("_preflight_row",  2839, "FlowReplayError"),
    ("_preflight_row",  2866, "FlowReplayError"),
    ("_preflight_row",  2881, "FlowReplayError"),
    ("_preflight_row",  2890, "FlowReplayError"),
    ("run_batch",       3896, "FlowReplayError"),
    ("run_batch",       3898, "FlowReplayError"),
    ("run_batch",       3905, "FlowReplayError"),
    ("run_batch",       3920, "FlowReplayError"),
    ("run_batch",       3934, "FlowReplayError"),
    ("run_batch",       3938, "FlowReplayError"),
    ("run_batch",       3942, "FlowReplayError"),
]

# The engine's failure `kind` -> the class `_classify_replay_failure` resolves it to. `"miss"` is the
# 25th refusal and the one no `raise FlowReplayError(` scan can see: it reaches the base through a
# VARIABLE (`raise _classify_replay_failure(kind)(...)`), which is why the class table below and the
# ratchet's shape (b) both exist.
KIND_TABLE = [
    ("miss",             "FlowReplayError"),
    ("escalate",         "EscalateError"),
    ("shape",            "ShapeDriftError"),
    ("quarantine",       "FlowQuarantineError"),
    ("write_unreadable", "WriteReadbackError"),
    ("write_unverified", "WriteUnverifiedError"),
    ("<default>",        "DriftError"),
]


def _tree():
    return ast.parse(FLOWS.read_text(encoding="utf-8"), filename=str(FLOWS))


def _dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _taxonomy_names(tree) -> set:
    """The FlowReplayError family, derived transitively — never hand-listed.

    Two members (`MetaUnreadableError`, `MetaUnwritableError`) live ~130 lines below the rest of the
    block, and every hand count of this family in the repo's own prose said "eleven" when it was
    twelve. `flows.py:639` still does.
    """
    names = {"FlowReplayError"}
    changed = True
    while changed:
        changed = False
        for n in ast.walk(tree):
            if (isinstance(n, ast.ClassDef) and n.name not in names
                    and any(_dotted(b).split(".")[-1] in names for b in n.bases)):
                names.add(n.name)
                changed = True
    return names


def _owner_of(tree, lineno: str) -> str:
    """The innermost function enclosing a line — innermost, so a nested helper is not credited to its
    parent."""
    owners = [(n.lineno, n.end_lineno, n.name) for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.lineno <= lineno <= n.end_lineno]
    owners.sort(key=lambda o: o[1] - o[0])
    return owners[0][2] if owners else "<module>"


def _derive_sites() -> list:
    tree = _tree()
    fam = _taxonomy_names(tree)
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)):
            continue
        cls = _dotted(n.exc.func).split(".")[-1]
        if cls in fam:
            out.append((_owner_of(tree, n.lineno), n.lineno, cls))
    return sorted(out, key=lambda s: s[1])


def test_the_derivation_sees_the_taxonomy_at_all() -> None:
    """The premise. Asserting a table over whatever a broken walk found is asserting nothing."""
    fam = _taxonomy_names(_tree())
    assert len(fam) >= 13, f"the family walk found only {sorted(fam)} — it is broken, not the tree small"
    for required in ("FlowReplayError", "DriftError", "WriteReadbackError",
                     "MetaUnreadableError", "MetaUnwritableError"):
        assert required in fam, f"{required} is missing from the derived family"


def test_every_refusing_function_is_present_and_owns_the_refusals_it_should() -> None:
    """Named functions, exact counts — so a SPLIT or a RENAME is loud rather than a smaller table.

    This is the half `test_the_site_table_holds` cannot assert on its own: a table derived from a
    tree where `_preflight_row` has been renamed simply contains no `_preflight_row` rows, and an
    equality against a table that also lost them would pass.
    """
    tree = _tree()
    present = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = sorted(set(REFUSING_FUNCTIONS) - present)
    assert not missing, (
        f"{missing} no longer exist in flows.py — this file asserts a NEGATIVE about their bodies, so a "
        f"rename or a split makes it check nothing. Rename here too, naming BOTH halves.")

    counts: dict = {}
    for fn, _line, _cls in _derive_sites():
        counts[fn] = counts.get(fn, 0) + 1
    for fn, want in sorted(REFUSING_FUNCTIONS.items()):
        assert counts.get(fn, 0) == want, (
            f"{fn} raises {counts.get(fn, 0)} taxonomy refusal(s), not {want} — a refusal was added or "
            f"removed. Update SITE_TABLE in the same diff, with the argument in the PR body.")
    # ...and no OTHER function may grow one silently. The per-function check above is blind to a
    # refusal appearing somewhere new, which is how a 25th site would arrive.
    strays = sorted(set(counts) - set(REFUSING_FUNCTIONS))
    assert not strays, (
        f"{strays} raise a taxonomy refusal and are not in REFUSING_FUNCTIONS — a new refusal site. "
        f"Add it here and to SITE_TABLE, with the code's remedy argued in the PR body.")
    assert sum(counts.values()) == TOTAL_RAISE_SITES, (
        f"{sum(counts.values())} taxonomy raise sites, not {TOTAL_RAISE_SITES}")


def test_the_site_table_holds() -> None:
    """THE GOLDEN. Which class each refusal raises, printed, compared to a committed table.

    A diff here is the point, not a failure: it is what 1.4 changes, and reviewing the diff IS
    reviewing the slice. What it forbids is a raise being re-classed silently.
    """
    got = _derive_sites()
    print(f"\n  {len(got)} taxonomy raise sites in flows.py")
    for fn, line, cls in got:
        print(f"    {fn:<16} :{line:<5} {cls}")

    # SITE_TABLE covers the 24 that 1.4 re-classes; the 12 that were already typed are covered by the
    # per-function counts above. Compare only the rows the table claims, keyed on the line.
    claimed = {line: (fn, cls) for fn, line, cls in SITE_TABLE}
    derived = {line: (fn, cls) for fn, line, cls in got}
    for line, (fn, cls) in sorted(claimed.items()):
        assert line in derived, (
            f"SITE_TABLE claims a refusal at flows.py:{line} ({fn}) and none is there — the table has "
            f"gone stale, which makes every row below it untrustworthy too.")
        assert derived[line] == (fn, cls), (
            f"flows.py:{line}: SITE_TABLE says {fn} raises {cls}, the tree says "
            f"{derived[line][0]} raises {derived[line][1]}")


def test_the_kind_table_holds() -> None:
    """The 25th refusal, and the six typed ones beside it.

    `_classify_replay_failure` is the one route to a refusal that no raise-site scan can see, because
    the class arrives through a variable. Driven through the real function rather than parsed.
    """
    from ultracua import flows

    print("\n  _classify_replay_failure:")
    for kind, want in KIND_TABLE:
        got = flows._classify_replay_failure("__no_such_kind__" if kind == "<default>" else kind)
        print(f"    {kind:<18} -> {got.__name__:<22} code={got.code!r}")
        assert got.__name__ == want, f"kind {kind!r} now resolves to {got.__name__}, not {want}"


def test_every_refusal_carries_the_three_machine_readable_fields() -> None:
    """Whatever the binding is, a caller must be able to branch on it without reading prose.

    Deliberately NOT an assertion about WHICH code: that is `SITE_TABLE`'s job. This one holds while
    the table is being changed, which is when a field is most likely to be dropped.
    """
    from ultracua import flows

    tree = _tree()
    classes = sorted(_taxonomy_names(tree))
    print(f"\n  {len(classes)} classes in the family")
    for name in classes:
        cls = getattr(flows, name)
        assert isinstance(cls.code, str) and cls.code.strip(), f"{name} has no code"
        assert isinstance(cls.retryable, bool), f"{name}.retryable is not a bool"
        assert isinstance(cls.landed, bool), (
            f"{name}.landed must stay a two-state bool — it is the ARMING token the retry-dedupe "
            f"ledger reads, a different question from `RunRecord.landed`'s tri-state REPORT")
        print(f"    {name:<24} code={cls.code:<18} retryable={cls.retryable!s:<5} "
              f"landed={cls.landed}")


# The ONE argued non-taxonomy raise inside a refusing function: a programmer-error guard on an
# internal keyword whose only callers pass a literal. It can never reach a caller's
# `except FlowReplayError`, because no caller can produce it. Keyed on (function, class) so a
# `ValueError` appearing anywhere ELSE is still loud.
ARGUED_NON_TAXONOMY_RAISES = {
    ("_update_meta", "ValueError"),   # flows.py:684 — `on_unreadable` must be 'raise'|'skip'
}


def _returns_family(tree, callee: str, fam: set) -> bool:
    """Does a nested factory's RETURN ANNOTATION name the family?

    Two refusals in `flows.py` reach the taxonomy through a call rather than a class name —
    `raise _do_quarantine(reason)` (3181, 3219) and `raise _classify_replay_failure(kind)(...)`
    (3306). The second is the 25th refusal, and the one the `bare_flow_replay_error` ratchet's raise
    shape structurally cannot see: its callee is a Call, so no scan keyed on a class name reads it.
    Resolving through the annotation is what makes this cell able to adjudicate them — and it pins
    those two factories to keep returning family members.
    """
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == callee:
            ann = n.returns
            if ann is None:
                return False
            text = ast.unparse(ann)
            return any(name in text for name in fam)
    return False


@pytest.mark.parametrize("fn", sorted(REFUSING_FUNCTIONS))
def test_every_raise_in_a_refusing_function_resolves_into_the_family(fn) -> None:
    """A refusal that is not a `FlowReplayError` escapes every `except FlowReplayError` on the replay,
    batch and MCP paths — which is R4.18, and it cost a write-safety critical.

    It is also the property that stops 1.4 from "fixing" a bare base raise by turning it into a
    `ValueError`: that would remove it from the ratchet and from the taxonomy in one move, reporting
    the shape as gone while making the failure invisible to every existing handler.

    THREE SHAPES REACH THE FAMILY, and the first draft of this cell knew only one — it asserted "no
    strangers" and the tree produced three, of which two were legitimate indirections it could not
    see. Named here rather than allowlisted away:
      * a class by name             `raise UnkeyedWriteError(...)`
      * a factory returning one     `raise _do_quarantine(reason)`
      * a factory returning a CLASS `raise _classify_replay_failure(kind)(...)`
    """
    tree = _tree()
    fam = _taxonomy_names(tree)
    owner = next((n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn), None)
    assert owner is not None, f"{fn} is gone — see the rename note above"

    strangers, resolved = [], []
    for n in ast.walk(owner):
        if not (isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)):
            continue
        func = n.exc.func
        cls = _dotted(func).split(".")[-1]
        if cls in fam:
            resolved.append(f"{n.lineno}:{cls}")
            continue
        # `raise <factory>(...)` — the factory's return annotation names the family.
        if cls and _returns_family(tree, cls, fam):
            resolved.append(f"{n.lineno}:{cls}()->family")
            continue
        # `raise <factory>(...)(...)` — the inner call returns the CLASS. flows.py:3306.
        if isinstance(func, ast.Call):
            inner = _dotted(func.func).split(".")[-1]
            if inner and _returns_family(tree, inner, fam):
                resolved.append(f"{n.lineno}:{inner}()->type[family]")
                continue
        if (fn, cls) in ARGUED_NON_TAXONOMY_RAISES:
            resolved.append(f"{n.lineno}:{cls} (argued)")
            continue
        strangers.append(f"line {n.lineno}: {cls or ast.unparse(func)}")

    assert resolved, (
        f"{fn} is in REFUSING_FUNCTIONS but this walk resolved no raise in it — the cell is asserting "
        f"a negative over nothing")
    assert not strangers, (
        f"{fn} raises {strangers} — outside the taxonomy, so every `except FlowReplayError` on the "
        f"replay, batch and MCP paths walks straight past it (R4.18). If it genuinely cannot reach a "
        f"caller, add it to ARGUED_NON_TAXONOMY_RAISES with the argument.")
