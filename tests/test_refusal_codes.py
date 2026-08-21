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

# WHAT EACH FUNCTION RAISES, as a sorted multiset of class names. Keyed on the FUNCTION, never on the
# line: a table of line numbers churns on every unrelated edit above it, and a table nobody can read
# the diff of is a table nobody reads. The multiset still catches everything that matters — a raise
# re-classed, a raise moved between functions, a raise added or removed — because a change to any of
# those changes some function's list.
#
# At 0.110.0 every entry marked (1.4) below read `FlowReplayError`, so `flow not approved`, `no login
# configured` and `batch has more rows than max_rows` all reached a caller as `code="replay_error"`.
# THE GIT DIFF ON THIS TABLE IS THE RECORD OF WHAT STEP 1.4 CHANGED — review it, not the raise sites.
SITE_TABLE = {
    "_update_meta":    ["MetaUnreadableError"],
    "_save_meta":      ["MetaUnwritableError"],
    "_quarantine":     ["MetaUnwritableError"],
    "_do_quarantine":  ["FlowQuarantineError"],
    "_replay_body":    ["WriteReadbackError", "WriteUnverifiedError"],
    "_form_login":     ["LoginEnvUnsetError",              # (1.4) credentials not in env
                        "LoginFailedError"],               # (1.4) the form could not be auto-filled
    "refresh_auth":    ["LoginFailedError",                # (1.4) login did not appear to succeed
                        "LoginUnconfiguredError",          # (1.4) no `login` on the spec
                        "LoginUnconfiguredError"],         # (1.4) no `storage_state` to save into
    "approve":         ["NotLearnedError"],                # (1.4) nothing to approve
    "unapprove":       ["NotLearnedError"],                # (1.4) nothing to unapprove
    "validate_params": ["ParamValidationError",
                        "ParamValidationError",
                        "ParamValidationError",
                        "SecretEnvUnsetError"],            # (1.4) a secret slot's env var is unset
    "_preflight_row":  ["ApprovalBindingStaleError",       # (1.4) the slot schema moved
                        "ApprovalBindingStaleError",       # (1.4) the value contracts moved
                        "FlowQuarantineError",
                        "NotApprovedError",                # (1.4) flow not approved
                        "PrecheckUnsafeError",             # (1.4) parameterized write + one-shot precheck
                        "RelearnRefusedError",             # (1.4) relearn on a write flow
                        "RelearnRefusedError",             # (1.4) relearn with params
                        "RelearnRefusedError",             # (1.4) relearn on an APPROVED flow
                        "SlotUnboundError",                # (1.4) a supplied slot binds no step
                        "StaleApprovalError",
                        "UnkeyedWriteError",
                        "WriteUnconfirmableError"],        # (1.4) a write flow with no confirm check
    "run_batch":       ["BatchArgumentError",              # (1.4) no spec for a non-empty batch
                        "BatchArgumentError",              # (1.4) a bad on_row_error
                        "BatchBoundExceededError",         # (1.4) more rows than max_rows
                        "BatchUnboundedError",             # (1.4) a write batch with no max_rows
                        "LedgerUnusableError",             # (1.4) a bad/foreign resume ledger
                        "NotLearnedError",                 # (1.4) nothing to batch
                        "UndeclaredWriteError"],           # (1.4) mutating steps, no declaration
}
TOTAL_RAISE_SITES = 36

# The engine's failure `kind` -> the class `_classify_replay_failure` resolves it to. `"miss"` is the
# 25th refusal and the one no `raise FlowReplayError(` scan can see: it reaches the base through a
# VARIABLE (`raise _classify_replay_failure(kind)(...)`), which is why the class table below and the
# ratchet's shape (b) both exist.
KIND_TABLE = [
    ("miss",             "NotLearnedError"),
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
    twelve — `MetaUnwritableError.retryable`'s own comment says so in `src`.
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


def test_every_refusing_function_is_still_there() -> None:
    """Named functions — so a SPLIT or a RENAME is loud rather than a smaller table.

    This is the half `test_the_site_table_holds` cannot assert on its own: a table derived from a tree
    where `_preflight_row` has been renamed simply contains no `_preflight_row` rows, and an equality
    against a table that also lost them would pass. 1.5 measured three structural scans disarmed at
    once by one function being split in two, so a scan that names a function must fail when the name
    is MISSING, not merely when its contents change.
    """
    present = {n.name for n in ast.walk(_tree())
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = sorted(set(SITE_TABLE) - present)
    assert not missing, (
        f"{missing} no longer exist in flows.py — this file asserts a NEGATIVE about their bodies, so a "
        f"rename or a split makes it check nothing. Rename here too, naming BOTH halves.")


def test_the_site_table_holds() -> None:
    """THE GOLDEN. Which class each function raises, printed, compared to a committed table.

    A diff here is the point, not a failure: it is what 1.4 changes, and reviewing the diff IS
    reviewing the slice. What it forbids is a raise being re-classed, moved or added silently.
    """
    got = _derive_sites()
    derived: dict = {}
    for fn, _line, cls in got:
        derived.setdefault(fn, []).append(cls)
    for fn in derived:
        derived[fn].sort()

    print(f"\n  {len(got)} taxonomy raise sites across {len(derived)} functions")
    for fn, classes in sorted(derived.items()):
        print(f"    {fn:<18} {', '.join(classes)}")

    assert derived == {k: sorted(v) for k, v in SITE_TABLE.items()}, (
        "the refusal/class binding moved. Every difference below is a behaviour change a caller can "
        "see — a code on the MCP wire, in `DryRunReport.aborted`, in a `--json` batch report — so "
        "update SITE_TABLE in the SAME diff and argue the new code's remedy in the PR body.\n"
        + "\n".join(
            f"    {fn:<18} table={sorted(SITE_TABLE.get(fn, []))}  tree={derived.get(fn, [])}"
            for fn in sorted(set(SITE_TABLE) | set(derived))
            if sorted(SITE_TABLE.get(fn, [])) != derived.get(fn, [])))
    assert len(got) == TOTAL_RAISE_SITES, f"{len(got)} raise sites, not {TOTAL_RAISE_SITES}"


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


@pytest.mark.parametrize("fn", sorted(SITE_TABLE))
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
        f"{fn} is in SITE_TABLE but this walk resolved no raise in it — the cell is asserting "
        f"a negative over nothing")
    assert not strangers, (
        f"{fn} raises {strangers} — outside the taxonomy, so every `except FlowReplayError` on the "
        f"replay, batch and MCP paths walks straight past it (R4.18). If it genuinely cannot reach a "
        f"caller, add it to ARGUED_NON_TAXONOMY_RAISES with the argument.")


# ---------------------------------------------------------------------------------------------------
# The INVARIANT, enforced once. Every cell below defines a bad class inside `pytest.raises`, so class
# creation runs `__init_subclass__` and the cell is red by construction — there is no way to write one
# of these that passes vacuously.

def test_the_abstract_base_cannot_be_raised() -> None:
    """`code="replay_error"` names no remedy, and 24 refusals shared it.

    This is the only sensor that reaches the INDIRECT raise as well:
    `raise _classify_replay_failure(kind)(...)` resolves its class at run time, so no AST scan keyed on
    a class name can see it — and before 1.4 that route produced a base-coded refusal for the
    commonest failure a fresh flow has.
    """
    from ultracua.flows import FlowReplayError

    with pytest.raises(TypeError, match="ABSTRACT"):
        FlowReplayError("nope")
    with pytest.raises(TypeError, match="ABSTRACT"):
        raise FlowReplayError("nope")


def test_a_subclass_must_declare_each_axis_rather_than_inherit_it() -> None:
    """R4.18 in one line: `MetaUnwritableError` inherited `retryable=True` from its PRE-write twin, an
    MCP agent honoured it, and a commit that had already actuated fired twice."""
    from ultracua.flows import FlowReplayError

    for omitted in ("code", "retryable", "can_follow_actuation"):
        attrs = {"code": "probe_omit", "retryable": False, "can_follow_actuation": False}
        attrs.pop(omitted)
        with pytest.raises(TypeError, match=f"must declare `{omitted}`"):
            type("_Probe", (FlowReplayError,), attrs)


def test_two_classes_may_not_share_a_code() -> None:
    from ultracua.flows import FlowReplayError

    with pytest.raises(TypeError, match="already belongs to"):
        type("_Probe", (FlowReplayError,),
             {"code": "not_learned", "retryable": False, "can_follow_actuation": False})


def test_a_class_may_not_take_a_code_another_vocabulary_owns() -> None:
    """`ToolOutcome.code` and `SkippedFlow.code` are the SAME FIELD as this taxonomy's on the MCP wire.
    B3 buckets on it, so one slug meaning two things is an overloaded token a consumer cannot resolve.

    THE MATCH IS THE RESERVED BRANCH'S OWN WORDS. The first draft matched
    `another vocabulary|ABSTRACT|belongs`, and `belongs` is what the DUPLICATE-code branch says too —
    so the cell passed whichever branch fired. That is not academic: it would go on passing if a
    reserved code were also admitted into `REGISTRY`, which is precisely the state the audit found
    `not_learned` and `not_approved` in. A cell that cannot tell WHICH guard fired cannot tell you the
    guard you meant has stopped firing.
    """
    from ultracua.flows import REGISTRY, RESERVED_CODES, FlowReplayError

    for stolen in sorted(RESERVED_CODES):
        assert stolen not in REGISTRY, (
            f"{stolen!r} is reserved AND carried by {REGISTRY[stolen].__name__} — the probe below would "
            f"exercise the duplicate branch instead, and this cell would pass without testing reserved")
        with pytest.raises(TypeError, match="another vocabulary"):
            type("_Probe", (FlowReplayError,),
                 {"code": stolen, "retryable": False, "can_follow_actuation": False})


def test_a_code_must_be_a_lower_snake_slug() -> None:
    from ultracua.flows import FlowReplayError

    for bad in ("Not Learned", "notLearned", "", "not-learned", 7):
        with pytest.raises(TypeError, match="lower_snake slug"):
            type("_Probe", (FlowReplayError,),
                 {"code": bad, "retryable": False, "can_follow_actuation": False})


def test_a_post_actuation_class_must_state_landed_rather_than_inherit_a_denial() -> None:
    """The cross-check the two axes owe each other. A class that CAN follow an actuation has a real
    choice to make about landing; inheriting `False` is making it by accident, and a false denial is
    the direction that leaves a committed write unrecorded and re-fired."""
    from ultracua.flows import FlowReplayError

    with pytest.raises(TypeError, match="must state `landed`"):
        type("_Probe", (FlowReplayError,),
             {"code": "probe_post_act", "retryable": False, "can_follow_actuation": True})


def test_a_class_may_not_claim_a_landed_write_it_could_not_have_observed() -> None:
    from ultracua.flows import FlowReplayError

    with pytest.raises(TypeError, match="claims a landed write"):
        type("_Probe", (FlowReplayError,),
             {"code": "probe_liar", "retryable": False, "landed": True,
              "can_follow_actuation": False})


def test_a_class_may_not_be_both_retryable_and_post_actuation() -> None:
    """INVIOLABLE #3, made a TypeError. `retryable=True` tells an autonomous agent to re-run; from a
    position where the write may already have committed, that is a double-submit."""
    from ultracua.flows import FlowReplayError

    with pytest.raises(TypeError, match="may already have committed"):
        type("_Probe", (FlowReplayError,),
             {"code": "probe_retry", "retryable": True, "landed": False,
              "can_follow_actuation": True})


def test_the_guard_does_not_refuse_a_LEGITIMATE_new_class() -> None:
    """THE LIVENESS HALF, and without it every cell above is satisfied by a hook that refuses
    everything — which is the D0 over-refusal shape this register has already shipped once, inside a
    safety fix, where it made `learn()` unable to author any write at all.

    So: a well-formed class must still be definable, must register, and must be catchable as a
    `FlowReplayError`. Cleaned up afterwards so the probe does not leak into `REGISTRY` and change
    what every other cell derives.
    """
    from ultracua.flows import REGISTRY, FlowReplayError

    probe = type("_ProbeOk", (FlowReplayError,),
                 {"code": "probe_ok", "retryable": False, "can_follow_actuation": False})
    try:
        assert REGISTRY["probe_ok"] is probe, "a legitimate class was not registered"
        with pytest.raises(FlowReplayError):
            raise probe("a well-formed refusal must still be raisable and catchable")
    finally:
        REGISTRY.pop("probe_ok", None)


def test_the_registry_key_is_the_class_s_LIVE_code() -> None:
    """`__init_subclass__` keys `REGISTRY` at CLASS-CREATION time, so a code changed afterwards leaves
    the registry pointing at the old slug while the class reports the new one.

    Neither existing guard sees it. The distinct-code property derives `by_code` from live attributes,
    so it only fires if the mutation creates a DUPLICATE; the walk-vs-`REGISTRY` equality compares class
    NAMES, so both sides still match. Exposure is nil today — nothing does a `REGISTRY[code]` lookup —
    but B3 is precisely the consumer that will, and it is one line to pin.
    """
    from ultracua.flows import REGISTRY

    drift = {key: cls.__name__ + "." + cls.code for key, cls in REGISTRY.items() if key != cls.code}
    assert not drift, (
        f"REGISTRY is keyed on a code these classes no longer carry: {drift}. A lookup by the live code "
        f"would KeyError, and a lookup by the stale key returns a class that disagrees with it.")
    assert len(REGISTRY) >= 27, f"REGISTRY holds only {len(REGISTRY)} classes — it is not being populated"


def test_the_runtime_guard_and_the_RATCHET_are_two_sensors_rather_than_two_copies_of_one() -> None:
    """THE ARGUMENT FOR HAVING BOTH, made checkable instead of claimed in a docstring.

    `FlowReplayError.__init__` refuses the base at RUN time — and it is defeatable, measured:

        FlowReplayError.__new__(FlowReplayError)   ->  a raisable instance, code == 'replay_error'

    `__init__` never runs, so `type(self) is FlowReplayError` never fires. Nothing in the tree does
    this, so it is not a live defect; it is the shape this register calls "a guard that asserts a
    negative", and a second guard of the SAME class would inherit the same blind spot.

    The ratchet is a different class of sensor — it reads the SOURCE, at authoring time — and it sees
    exactly what the runtime guard cannot. Verified here rather than asserted: a scratch copy carrying
    `raise FlowReplayError.__new__(FlowReplayError)` makes the derivation report it, because the bare
    NAME lands in a value position that no raise-callee exclusion covers.
    """
    import shutil
    import subprocess
    import sys
    import tempfile

    from ultracua.flows import FlowReplayError

    # The runtime guard's blind spot, demonstrated — not merely described.
    sneaky = FlowReplayError.__new__(FlowReplayError)
    assert sneaky.code == "replay_error", "the bypass no longer produces a base-coded refusal"
    with pytest.raises(FlowReplayError):
        raise sneaky

    # ...and the other sensor catching it.
    root = pathlib.Path(__file__).parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        dst = pathlib.Path(tmp)
        shutil.copytree(root / "src", dst / "src", ignore=shutil.ignore_patterns("__pycache__"))
        with (dst / "src" / "ultracua" / "flows.py").open("a", encoding="utf-8") as fh:
            fh.write("\n\ndef _probe():\n    raise FlowReplayError.__new__(FlowReplayError)\n")
        probe = (
            "import pathlib, sys; sys.path.insert(0, r'%s');"
            "import ratchets as R;"
            "R.ROOT = pathlib.Path(r'%s'); R.SRC = R.ROOT / 'src' / 'ultracua';"
            "print(len(R.derive_bare_flow_replay_error()))" % (root / "scripts", dst)
        )
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
        assert out.returncode == 0, f"the probe failed: {out.stderr}"
        assert int(out.stdout.strip()) > 0, (
            "the ratchet did NOT see `raise FlowReplayError.__new__(FlowReplayError)`. Both sensors are "
            "now blind to the same shape, which makes them two copies of one guard rather than two "
            "sensor classes — and the zero this ratchet reports would no longer mean what it says.")


@pytest.mark.parametrize("spelling", ["FlowReplayError", "flows.FlowReplayError"])
def test_the_ratchet_sees_the_base_class_under_BOTH_spellings(spelling) -> None:
    """FOUND BY THE 1.4a AUDIT, in the shape 1.4a added to close an indirection.

    Shape (b) of `derive_bare_flow_replay_error` exists because `_classify_replay_failure` reaches the
    base through a TABLE — the class sits in a value position and becomes a raise through a variable,
    which the raise-site shape structurally cannot see. Its first draft matched `ast.Name` only.

    MEASURED: a `{"miss": flows.FlowReplayError}` table in `mcpserver/server.py` reported ZERO
    producers. And that is the ONE spelling every module except `flows.py` would use — `mcpserver` and
    `daemon` both import the module, not the name. A shape added to close an indirection that closes
    half of it is this register's "patch on a patch" verbatim, one level down.

    So: both spellings, driven through the real derivation over a scratch copy.
    """
    import shutil
    import subprocess
    import sys
    import tempfile

    root = pathlib.Path(__file__).parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        dst = pathlib.Path(tmp)
        shutil.copytree(root / "src", dst / "src", ignore=shutil.ignore_patterns("__pycache__"))
        # Put the probe where that spelling is natural: the bare name inside flows.py, the dotted one
        # in a module that imports flows — which is what makes this cell about REACH, not syntax.
        target = ("ultracua/flows.py" if spelling == "FlowReplayError"
                  else "ultracua/mcpserver/server.py")
        with (dst / "src" / target).open("a", encoding="utf-8") as fh:
            fh.write(f"\n\n_PROBE_TABLE = {{'miss': {spelling}}}\n")
        probe = (
            "import pathlib, sys; sys.path.insert(0, r'%s');"
            "import ratchets as R;"
            "R.ROOT = pathlib.Path(r'%s'); R.SRC = R.ROOT / 'src' / 'ultracua';"
            "hits = R.derive_bare_flow_replay_error();"
            "print(len(hits))" % (root / "scripts", dst)
        )
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
        assert out.returncode == 0, f"the probe failed: {out.stderr}"
        n = int(out.stdout.strip())
        assert n == 1, (
            f"a `{{'miss': {spelling}}}` table reported {n} base-coded producers, not 1. Zero means the "
            f"derivation is blind to this spelling and its reported zero does not mean what it says; "
            f"two means an Attribute chain is being counted once per node.")


# ---------------------------------------------------------------------------------------------------
# The OTHER vocabularies. `ToolOutcome.code`, `SkippedFlow.code`, `BatchRowResult.code` and
# `RunRecord.failure_code` are fields this taxonomy shares with codes `mcpserver` mints itself, and B3
# buckets on them. Both cells below DERIVE the other vocabulary from source rather than listing it.


def _skipped_flow_codes() -> set:
    """The `SkippedFlow` vocabulary, derived from `mcpserver`'s own source."""
    server = pathlib.Path(__file__).parents[1] / "src" / "ultracua" / "mcpserver" / "server.py"
    tree = ast.parse(server.read_text(encoding="utf-8"), filename=str(server))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "SkippedFlow":
            # (spec_name, code, detail) — positionally, and `code=` if ever passed by keyword.
            for a in n.args[1:2]:
                if isinstance(a, ast.Constant):
                    out.add(a.value)
            for kw in n.keywords:
                if kw.arg == "code" and isinstance(kw.value, ast.Constant):
                    out.add(kw.value.value)
    return out


def test_the_reserved_and_aligned_sets_together_cover_every_skipped_flow_code() -> None:
    """FOUND BY THE 1.4a AUDIT. `RESERVED_CODES`'s own comment said it covered every `SkippedFlow`
    code and listed six of the eight — the two omitted being precisely the two the slice then minted
    as taxonomy classes.

    So the rule read as violated by `NotLearnedError` and `NotApprovedError`, with nothing in the tree
    to say whether that was deliberate. It IS deliberate — they name the same remedy on a different
    surface — but a hand-typed set cannot express intent, only omission.

    DERIVED, so it cannot go stale: every code `mcpserver` mints must be either RESERVED (the taxonomy
    may not take it) or ALIGNED (the taxonomy takes it on purpose, meaning the same thing). A ninth
    `SkippedFlow` code added tomorrow fails here until someone says which.
    """
    from ultracua.flows import ALIGNED_CODES, REGISTRY, RESERVED_CODES

    minted = _skipped_flow_codes()
    assert len(minted) >= 8, f"the derivation found only {sorted(minted)} — it is broken"

    unclassified = sorted(minted - RESERVED_CODES - ALIGNED_CODES)
    assert not unclassified, (
        f"{unclassified} are minted as `SkippedFlow` codes and are in neither RESERVED_CODES nor "
        f"ALIGNED_CODES. Say which: reserved means the taxonomy may not take the slug; aligned means it "
        f"takes it deliberately because the two name the SAME remedy.")

    # ALIGNED means the taxonomy really does use it — an alignment nobody took is just a hole.
    unused = sorted(ALIGNED_CODES - set(REGISTRY))
    assert not unused, f"{unused} are declared aligned but no taxonomy class carries them"
    # ...and RESERVED means it really does NOT.
    stolen = sorted(RESERVED_CODES & set(REGISTRY))
    assert not stolen, f"{stolen} are reserved and a taxonomy class carries them anyway"
    print(f"\n  SkippedFlow mints {len(minted)}; aligned with the taxonomy: {sorted(ALIGNED_CODES)}")


def test_the_quiet_allowlist_is_only_ever_compared_against_SkippedFlow_code() -> None:
    """THE CONTAINMENT THAT MAKES THE ALIGNMENT SAFE, and it is the direction that would hurt.

    `QUIET_SKIPS` is the fleet's quiet allowlist. `not_approved` is in it AND is now a taxonomy code —
    and `NotApprovedError` fires for a flow that "declares a write, which is approval-gated whatever
    the caller asked for". If that slug ever reached `QUIET_SKIPS` from a `ToolOutcome` or a
    `BatchRowResult`, a WRITE REFUSAL would land in the bucket the fleet reads as ordinary, which is
    R3.9/CLI-1 exactly: a flow leaving the fleet with cron reporting green.

    It does not today — both comparisons are against a `SkippedFlow` — and this is what keeps it that
    way. Structural, because the hazard is a comparison someone adds later, not one that exists now.
    """
    server = pathlib.Path(__file__).parents[1] / "src" / "ultracua" / "mcpserver" / "server.py"
    tree = ast.parse(server.read_text(encoding="utf-8"), filename=str(server))

    uses = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Compare) and len(n.ops) == 1
                and isinstance(n.ops[0], (ast.In, ast.NotIn))):
            continue
        if _dotted(n.comparators[0]).split(".")[-1] != "QUIET_SKIPS":
            continue
        uses.append((n.lineno, ast.unparse(n.left)))

    assert uses, "no `... in QUIET_SKIPS` comparison found — this cell is asserting nothing"
    # The left side must be a `SkippedFlow`'s code. Keyed on the RECEIVER's name, which the two live
    # sites spell `s.code` inside a comprehension over `self.skipped` / `listing.skipped`.
    bad = [f"line {ln}: {expr}" for ln, expr in uses if expr not in ("s.code",)]
    assert not bad, (
        f"{bad} compares something other than a `SkippedFlow`'s code against QUIET_SKIPS. Two of that "
        f"allowlist's three members are also taxonomy codes now, and `not_approved` fires for a "
        f"DECLARED WRITE — putting a write refusal in the fleet's quiet bucket (R3.9/CLI-1).")
    print(f"\n  QUIET_SKIPS compared at {len(uses)} site(s), all against a SkippedFlow's code")
