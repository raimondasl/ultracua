"""reshape-plan step 1.6 — the named write questions, and `FlowSpec.key`.

WHAT THIS SLICE ACTUALLY IS. `spec.mutate is not None` was written out at 27 sites across three
modules, and `flow_key(spec.goal, spec.start_url, spec.scope)` at 24. Nothing was wrong with any of
them individually — that is the point. 27 chances to ask the DECLARATION question where the
RECIPE question was meant, and 24 chances to reorder two of three `str` arguments and key a flow to
somebody else's cache entry.

THE DECLARATION / RECIPE SPLIT IS THE DEFECT THIS CLOSES, and it is R3.5. An UNDECLARED write —
nothing declared on the spec, but a cached step carrying `mutating=True`, which is what the learn
path's wire promotion produces for a formless fetch-POST — is a write in FACT and not in
DECLARATION. `_auth_retry_allowed` asked the declaration, scored it retry-safe, and re-fired a commit
under a byte-identical Idempotency-Key. So the two families are now shaped differently and cannot be
reached the same way: a declaration question is an ATTRIBUTE of the spec, a recipe question is a
FUNCTION that visibly takes the recipe.

THIS SLICE CARRIES NO REFUSAL (reshape-plan's standing rule for it), and the first cell below is what
makes that checkable rather than asserted: every field of `WriteClass` is pinned against the RAW
EXPRESSION it replaced, over the full cross-product of `MutateSpec` shapes. If a named question is
even one shape different from the expression it stands in for, that is a behaviour change smuggled
into a rename, and it fails here.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import pathlib
import sys

import pytest

import ultracua.cli as cli_mod
import ultracua.flows as flows_mod
import ultracua.mcpserver.server as server_mod
from ultracua.cache import CachedFlow, CachedStep, StepConfirm, flow_key
from ultracua.flows import (FlowSpec, MutateSpec, WriteClass, is_write_flow,
                            recipe_has_multiple_writes, recipe_write_count)

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _arming import assert_red, mutate_function  # noqa: E402

MODULES = (flows_mod, cli_mod, server_mod)


# ---------------------------------------------------------------------------------------------------
# 1. THE EQUIVALENCE MATRIX. Each named question against the raw expression it replaced.

def _confirm(**kw) -> StepConfirm:
    return StepConfirm(confirm_selector="#ok", **kw)


# Every shape that can change an answer, and a few that deliberately cannot.
#
# `precheck_url` is in here ALONE on purpose: `MutateSpec.has_precheck()` reads only the three
# `precheck_selector/_text_contains/_url_contains` fields, so a spec with just `precheck_url` set
# declares NO precheck. That is the one shape in this table where a reasonable reader would guess
# wrong, and it is exactly what an equivalence matrix is for.
SHAPES = {
    "no mutate block at all": None,
    "declared, nothing else": MutateSpec(),
    "confirm by selector": MutateSpec(confirm_selector="#done"),
    "confirm by text": MutateSpec(confirm_text_contains="thanks"),
    "confirm by url": MutateSpec(confirm_url_contains="/receipt"),
    "precheck by selector": MutateSpec(precheck_selector="#already"),
    "precheck by text": MutateSpec(precheck_text_contains="already"),
    "precheck by url-contains": MutateSpec(precheck_url_contains="/done"),
    "precheck_url ONLY (not a precheck)": MutateSpec(precheck_url="http://x/check"),
    "zero barriers (empty list)": MutateSpec(step_confirms=[]),
    "one barrier": MutateSpec(step_confirms=[_confirm()]),
    "two barriers": MutateSpec(step_confirms=[_confirm(expects_intent="a"),
                                              _confirm(expects_intent="b")]),
    "three barriers": MutateSpec(step_confirms=[_confirm(expects_intent=c) for c in "abc"]),
    "confirm + precheck + two barriers": MutateSpec(
        confirm_selector="#done", precheck_selector="#already",
        step_confirms=[_confirm(expects_intent="a"), _confirm(expects_intent="b")]),
}

# field -> the expression it replaced, as a lambda over the raw `mutate` value. Written out rather
# than imported, because the whole job of this table is to be the BRIDGE between the two spellings:
# reviewing its diff is reviewing a claim that a named question still means what it stood in for.
RAW = {
    "declares_write": lambda m: m is not None,
    "declares_confirm": lambda m: m is not None and m.has_confirm(),
    "declares_precheck": lambda m: m is not None and m.has_precheck(),
    "declares_barriers": lambda m: m is not None and bool(m.step_confirms),
    "declares_multiple_barriers": lambda m: m is not None and m.is_multiwrite(),
}


@pytest.mark.parametrize("shape", sorted(SHAPES), ids=lambda s: s)
def test_every_named_question_equals_the_raw_expression_it_replaced(shape: str) -> None:
    mutate = SHAPES[shape]
    spec = FlowSpec(name="f", start_url="http://h/", goal="g", mutate=mutate)
    got = spec.write
    for field, raw in RAW.items():
        assert getattr(got, field) is bool(raw(mutate)), (
            f"{shape}: `spec.write.{field}` is {getattr(got, field)} but the expression it replaced "
            f"evaluates to {raw(mutate)}. A named question that differs from the raw one on even a "
            f"single shape is a behaviour change smuggled into a rename.")
    print(f"  {shape:38} {got}")


def test_the_matrix_covers_every_field_of_the_value_object() -> None:
    """A field added without a row above would be pinned by nothing — the anti-vacuity direction."""
    declared = {f.name for f in __import__("dataclasses").fields(WriteClass)}
    assert declared == set(RAW), (
        f"WriteClass fields {sorted(declared)} vs pinned {sorted(RAW)} — every field must name the "
        f"raw expression it replaced, or it is a new question nothing checks.")


def test_the_matrix_can_tell_the_shapes_apart() -> None:
    """ANTI-VACUITY. A table where every row answers identically pins nothing at all: it would be
    satisfied by `declares_write = True` everywhere. Each field must be BOTH True and False here."""
    for field in RAW:
        seen = {getattr(FlowSpec(name="f", start_url="http://h/", goal="g", mutate=m).write, field)
                for m in SHAPES.values()}
        assert seen == {True, False}, f"`{field}` is {seen} across every shape — the table cannot fail"
    n_distinct = len({tuple(getattr(FlowSpec(name="f", start_url="h", goal="g", mutate=m).write, f)
                            for f in RAW) for m in SHAPES.values()})
    print(f"{len(SHAPES)} shapes -> {n_distinct} distinct answers")


def test_the_value_object_is_frozen_so_a_question_cannot_be_answered_by_writing_to_it() -> None:
    spec = FlowSpec(name="f", start_url="http://h/", goal="g")
    with pytest.raises(Exception):
        spec.write.declares_write = True        # type: ignore[misc]


# ---------------------------------------------------------------------------------------------------
# 2. THE DECLARATION / RECIPE SPLIT — structural, not a naming convention.

def test_a_recipe_question_cannot_be_asked_of_the_declaration_object() -> None:
    """R3.5, made unaskable. `WriteClass` holds no recipe, so there is nothing on it that could
    answer one — and the recipe functions all take the recipe as an argument, in the open."""
    import dataclasses
    # FIELDS **and** callables. `dir()` alone does not list a dataclass field that has no class-level
    # default, so a check built on it would have been satisfied by an empty set — green while pinning
    # nothing, which is the shape this repo keeps filing.
    public = ({f.name for f in dataclasses.fields(WriteClass)}
              | {n for n in dir(WriteClass) if not n.startswith("_")})
    assert public == set(RAW) | {"of"}, (
        f"WriteClass grew {sorted(public - set(RAW) - {'of'})}. If it is a declaration question it "
        f"needs a row in RAW; if it needs the RECIPE it does not belong on this object at all.")
    for fn in (is_write_flow, recipe_write_count, recipe_has_multiple_writes):
        params = list(inspect.signature(fn).parameters)
        assert "cached_flow" in params, (
            f"{fn.__name__} does not visibly take the recipe — the split is structural only while a "
            f"recipe question is a function you must hand the recipe to.")
    print(f"declaration questions: {sorted(set(RAW))}")
    print(f"recipe questions:      {[f.__name__ for f in (is_write_flow, recipe_write_count, recipe_has_multiple_writes)]}")


# The three `MutateSpec` methods that ANSWER A CLASSIFICATION QUESTION rather than return data.
# Every one of them is a named question on `WriteClass`, so a call to one outside the constructor is
# the raw predicate wearing a different hat.
CLASSIFYING_METHODS = ("has_confirm", "has_precheck", "is_multiwrite")


def _raw_classification_calls(tree: ast.AST) -> list:
    """Calls to a classification method on a MutateSpec, by receiver rather than by method name.

    THE RECEIVER TEST IS LOAD-BEARING IN BOTH DIRECTIONS. `StepConfirm` has its own `has_confirm()`
    — a per-write barrier answering about ITSELF, which is not a flow-level classification and must
    not be flagged; the first draft matched on the method name alone and reported it. And a site can
    evade a receiver test by binding first (`m = spec.mutate; m.has_confirm()`), so names bound from
    `<x>.mutate` inside the same function count as the same receiver.
    """
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            continue
        aliases = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                   for t in n.targets
                   if isinstance(t, ast.Name) and isinstance(n.value, ast.Attribute)
                   and n.value.attr == "mutate"}
        for n in ast.walk(fn):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in CLASSIFYING_METHODS):
                continue
            recv = n.func.value
            is_mutate = ((isinstance(recv, ast.Attribute) and recv.attr == "mutate")
                         or (isinstance(recv, ast.Name) and recv.id in aliases))
            if is_mutate:
                out.append((n.lineno, ast.unparse(n)))
    return sorted(set(out))


def test_a_classification_question_is_never_asked_through_a_raw_dereference() -> None:
    """THE CLASS, not the instances that prompted it.

    `scripts/ratchets.py` counts `<x>.mutate is (not) None` and nothing else, so a site asking
    `spec.mutate.has_confirm()` behind a `declares_write` local was invisible to it. A ratchet-clean
    tree still had FOUR of them, and this scan found them on its first run — two in `_preflight_row`
    and two in `record`, all four verified equivalent against their enclosing guard before conversion.
    """
    offenders = [f"{mod.__name__}:{ln}  {src}" for mod in MODULES
                 for ln, src in _raw_classification_calls(ast.parse(inspect.getsource(mod)))]
    assert not offenders, (
        "a classification question is asked through a raw dereference rather than through "
        f"`spec.write`:\n  " + "\n  ".join(offenders) +
        "\nEvery one of these is already a named question on WriteClass.")
    print(f"the {len(CLASSIFYING_METHODS)} classification questions are asked in ONE place")


@pytest.mark.parametrize("src,expect", [
    ("def f(spec):\n    return spec.mutate.has_confirm()", 1),
    ("def f(spec):\n    m = spec.mutate\n    return m.is_multiwrite()", 1),
    ("def f(spec):\n    return spec.mutate.has_precheck() and spec.mutate.has_confirm()", 2),
    # NOT flagged: a per-write barrier answering about itself, and a named question.
    ("def f(sc):\n    return sc.has_confirm()", 0),
    ("def f(spec):\n    return spec.write.declares_confirm", 0),
    ("def f(spec):\n    return spec.mutate.step_confirms", 0),
], ids=["direct", "via an alias", "two on one line", "a StepConfirm is not a MutateSpec",
        "the named question", "reading data is not asking"])
def test_the_scan_can_tell_a_classification_from_a_lookalike(src: str, expect: int) -> None:
    """The scan is the only thing standing between this slice and a fifth missed site, so it gets its
    own cells rather than resting on 'it found four'. A scan that reports zero over the real tree is
    indistinguishable from a broken one until you show it a tree that has them."""
    assert len(_raw_classification_calls(ast.parse(src))) == expect, ast.dump(ast.parse(src))


def _flow(*mutating: bool) -> CachedFlow:
    return CachedFlow(key="k", goal="g", start_url="u", created_ts=0.0,
                      steps=[CachedStep(intent=f"s{i}", action="click", mutating=m)
                             for i, m in enumerate(mutating)])


@pytest.mark.parametrize("marks,count", [
    ((), 0), ((False,), 0), ((True,), 1), ((True, False), 1), ((True, True), 2),
    ((True, False, True, True), 3),
])
def test_the_recipe_count_is_what_will_actually_fire(marks, count) -> None:
    assert recipe_write_count(_flow(*marks)) == count
    assert recipe_has_multiple_writes(_flow(*marks)) is (count > 1)


def test_a_missing_recipe_counts_zero_rather_than_raising() -> None:
    """`cached_flow=None` is ordinary (nothing learned yet) and reached the old inline sum, which
    handled it. A refactor that raised here would turn a normal state into a crash."""
    assert recipe_write_count(None) == 0 and recipe_has_multiple_writes(None) is False


def test_the_declaration_and_the_recipe_disagree_on_the_shape_that_caused_R35() -> None:
    """The UNDECLARED write, in one cell: nothing declared, a step that in fact commits.

    If these two ever agree on this shape, the split has been collapsed and R3.5 is back.
    """
    spec = FlowSpec(name="f", start_url="http://h/", goal="g")     # no mutate block
    recipe = _flow(True)                                           # ... but a step commits
    assert spec.write.declares_write is False
    assert is_write_flow(spec, recipe) is True
    assert recipe_write_count(recipe) == 1


def test_declared_barriers_and_recipe_writes_are_separately_named_and_can_disagree() -> None:
    """The plan's clause on this step: `record()` permits two mutating steps with NO `step_confirms`,
    which reads as a single write by declaration and commits twice in fact. `_auth_retry_allowed`
    keeps them as separate arms with different messages; collapsing them is R3.5 one level down."""
    spec = FlowSpec(name="f", start_url="http://h/", goal="g", mutate=MutateSpec(confirm_selector="#d"))
    recipe = _flow(True, True)
    assert spec.write.declares_multiple_barriers is False   # ... the human declared one outcome
    assert recipe_has_multiple_writes(recipe) is True       # ... and the recipe commits twice


# ---------------------------------------------------------------------------------------------------
# 3. `FlowSpec.key`.

@pytest.mark.parametrize("goal,url,name", [
    ("g", "http://h/", "f"),
    ("Place the order", "https://shop.example/checkout?x=1", "shop"),
    ("  spaced  goal ", "HTTP://Host.EXAMPLE/a/", "n-a_m e"),
])
def test_the_key_property_is_the_expression_it_replaced(goal, url, name) -> None:
    spec = FlowSpec(name=name, start_url=url, goal=goal)
    assert spec.key == flow_key(spec.goal, spec.start_url, spec.scope)


def test_the_key_is_not_confused_by_the_two_other_strings_on_the_spec() -> None:
    """The failure the 24 transcriptions were 24 chances at: three `str` arguments in a row, of which
    two are swappable without a type error. If `key` ever computed a DIFFERENT permutation these
    would collide."""
    a = FlowSpec(name="f", start_url="http://h/x", goal="http://h/y")
    b = FlowSpec(name="f", start_url="http://h/y", goal="http://h/x")
    assert a.key != b.key, "goal and start_url are interchangeable in the key — they must not be"
    c = FlowSpec(name="other", start_url="http://h/x", goal="http://h/y")
    assert a.key != c.key, "the name (via scope) does not reach the key"


def test_the_key_is_computed_in_exactly_one_place() -> None:
    """The whole point of the property. Two sites remain and BOTH are earned — the ratchet's own note
    says so, and this cell is what stops a third appearing without one."""
    sites = []
    for mod in MODULES:
        tree = ast.parse(inspect.getsource(mod))
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and getattr(n.func, "id", None) == "flow_key"
                    and len(n.args) == 3):
                sites.append((mod.__name__, n.lineno, ast.unparse(n)))
    assert len(sites) == 2, f"expected the 2 earned sites, found {len(sites)}: {sites}"
    exprs = {s[2] for s in sites}
    assert exprs == {"flow_key(self.goal, self.start_url, self.scope)",   # FlowSpec.key itself
                     "flow_key(args.goal, args.url, args.scope)"}, exprs  # the raw CLI: no FlowSpec
    for mod, line, expr in sites:
        print(f"  {mod}:{line}  {expr}")


# ---------------------------------------------------------------------------------------------------
# 4. THE PER-SITE CONVERSION TABLE, keyed on the FUNCTION rather than the line.
#
# A line number is a fact about today's whitespace (1.3 paid for that one). Keyed on the enclosing
# function, this table is a committed statement of WHICH question each caller now asks — so a site
# that silently switches from `declares_write` to `declares_confirm` shows up as a one-line diff.

CONVERSIONS = {
    "cli.py": {
        "_flow_audit": {"declares_write": 1},                 # arming the H9 judge on a write: refused
        "_flow_run_batch": {"declares_write": 1},             # auto-mint a resume id for a committed batch
        "_warn_if_shape_baseline_kept": {"declares_write": 1},  # which re-author command to advise
    },
    "flows.py": {
        "_apply": {"declares_write": 1},                      # writable-slot binding needs a declaration
        "_attempt_replay_body": {"declares_write": 3},        # found / extract_found / the H9 value gate
        # The function the plan singles out. THREE questions, and they must stay three: the
        # declaration, the declared BARRIER count, and the declared precheck. Its fourth question is
        # about the RECIPE and is `recipe_write_count`, which is why it is not on this row.
        "_auth_retry_allowed": {"declares_write": 1, "declares_multiple_barriers": 1,
                                "declares_precheck": 1},
        "_finalize": {"declares_write": 1},                   # a write's success IS its confirm check
        "_learn_once": {"declares_barriers": 1, "declares_multiple_barriers": 1},
        "_make_pre_write": {"declares_confirm": 1},           # no confirm -> no pre-write probe at all
        "_one_guarded": {"declares_write": 1},                # run_all: an undeclared write is skipped
        "_precheck_done": {"declares_precheck": 1},
        "_probe_confirm": {"declares_confirm": 1},            # record(): the pre-demo confirm probe
        # THREE, and the two beyond `declares_write` are the ones the ratchet could not see: they
        # were `spec.mutate.has_confirm()` / `.has_precheck()` guarded by a `declares_write` local,
        # which is a named question asked through a raw dereference. The ratchet counts only the
        # `is (not) None` comparison, so a ratchet-clean tree still had them — found by reading, not
        # by the instrument.
        "_preflight_row": {"declares_write": 1, "declares_confirm": 1, "declares_precheck": 1},
        "_replay_body": {"declares_write": 1},
        "dry_run": {"declares_precheck": 1},                  # ... reports `precheck_skipped`
        "is_write_flow": {"declares_write": 1},               # THE wire-or-declaration predicate
        "learn": {"declares_write": 1},                       # never multi-sample a declared write
        "mark_step": {"declares_write": 1},
        "preflight_keys": {"declares_write": 1},
        # A declared write with no confirm barrier is refused at record time, and that guard was
        # asking `spec.mutate.has_confirm()` inside an `if declared_write:` block — invisible to the
        # ratchet, found by the classification scan below.
        "record": {"declares_write": 1, "declares_confirm": 1},
        "run_batch": {"declares_write": 1},
        "save_spec": {"declares_barriers": 1},                # serialize step_confirms explicitly
    },
    "server.py": {
        "_is_write_flow": {"declares_write": 1},
        "_tool_for": {"declares_confirm": 1, "declares_write": 1},
        "call_flow_tool": {"declares_write": 1},
    },
}


def _asked_questions() -> dict:
    """{module: {function: {question: count}}} for every `<x>.write.<question>` in `src/`.

    Attributed to the INNERMOST enclosing function. A plain `ast.walk` per FunctionDef counts a site
    inside a nested closure under BOTH the closure and its enclosing function — `_make_finalize`
    holds `_finalize`, and the first draft of this table double-counted exactly there. A table whose
    totals are inflated by nesting cannot be reviewed as a conversion record.
    """
    out: dict = {}

    def visit(node, owner: str, bucket_for) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name, bucket_for)
                continue
            if (isinstance(child, ast.Attribute) and isinstance(child.value, ast.Attribute)
                    and child.value.attr == "write"):
                b = bucket_for(owner)
                b[child.attr] = b.get(child.attr, 0) + 1
            visit(child, owner, bucket_for)

    for mod in MODULES:
        short = mod.__name__.split(".")[-1] + ".py"
        tree = ast.parse(inspect.getsource(mod))
        visit(tree, "<module>", lambda name, s=short: out.setdefault(s, {}).setdefault(name, {}))
    return out


def test_the_conversion_table_is_what_the_source_actually_asks() -> None:
    """Reviewing this diff IS reviewing the conversion. 27 raw sites became named questions; if one
    of them got the WRONG name, the count moves under a function here and says which."""
    derived = _asked_questions()
    expected = {m: {f: {q: c for q, c in qs.items() if c} for f, qs in fns.items()}
                for m, fns in CONVERSIONS.items()}
    assert derived == expected, (
        "the questions the source asks are not the ones this table says.\n"
        + "\n".join(f"  {m}.{f}: table={expected.get(m, {}).get(f)} actual={qs}"
                    for m, fns in derived.items() for f, qs in fns.items()
                    if expected.get(m, {}).get(f) != qs)
        + "\n".join(f"  {m}.{f}: table={qs} actual=<absent>"
                    for m, fns in expected.items() for f, qs in fns.items()
                    if derived.get(m, {}).get(f) != qs))
    total = sum(c for fns in derived.values() for qs in fns.values() for c in qs.values())
    print(f"{total} named question(s) across {sum(len(f) for f in derived.values())} functions:")
    for m in sorted(derived):
        for f in sorted(derived[m]):
            print(f"  {m}::{f}  {derived[m][f]}")
