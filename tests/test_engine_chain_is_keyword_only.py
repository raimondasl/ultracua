"""reshape-plan step 1.1 — the engine chain is KEYWORD-ONLY after its subject.

WHAT THIS IS FOR. Six functions in `flow.py` took between 8 and 23 arguments positionally, and the
call chain filled them in order. `flow.py:752` was the worst of them: sixteen positional arguments of
which FOUR were a bare `None`, at positions 5, 7, 9 and 14. Swapping any two of those four is
type-silent — `provider`, `on_step`, `finalize` and `record_har_path` all accept None, and three of
the four accept a callable — so nothing in the type system, in the suite, or in a reviewer's eye
separates the right order from a wrong one. `_learn_n`'s own signature carries the scar in a comment:
inserting a parameter above `reflect` once made `reflect` arrive as `aux_routers`.

TWO SENSORS, DELIBERATELY, BECAUSE NEITHER IS ENOUGH ALONE.

  * The ARITY pin (`POSITIONAL_PREFIX`) says a parameter cannot be passed positionally at all. It
    cannot see a MIS-KEYED forward: `_replay(..., on_step=finalize, finalize=on_step)` satisfies it
    completely. That is the critic's clause on this step.
  * The FORWARDING pins say every internal call forwards `name=name` unless the difference is
    REGISTERED, and that the registered ones are exactly what the source does. Fourteen rows, so a
    mistranslated site is visible on sight rather than buried in 130 identical ones.
  * And the RUNTIME cell drives the edges that need no browser with a distinct sentinel per argument
    and asserts `is` identity on arrival — which is the only one of the three that can fail for a
    forward the AST reads as fine.

WHAT SHOULD BE POSITIONAL, AND WHY IT STOPS THERE. The prefix is the call's SUBJECT: which page the
run is about (`url`), or which session and step are being acted on. Everything else — configuration,
injected collaborators, flags — is keyword. The rule that says where the `*` may NOT move is derived
below rather than asserted: no two positional parameters of one function may share an annotation.
`_learn(url, goal, key, ...)` opened with THREE `str`s and `_replay(url, key, ...)` with two in a
DIFFERENT order, which is the swap this step exists to make impossible.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import ultracua.flow as flow_mod
from ultracua.cache import CachedFlow, FlowCache
from ultracua.flow import FlowReport, PacingGovernor

ROOT = Path(__file__).resolve().parents[1]

CHAIN = ("_learn", "_learn_n", "_replay", "_verify_by_replay", "_replay_step", "_author_steps")

# THE COMMITTED PREFIX. Reviewing a diff of this table IS reviewing a change to what may be passed
# positionally, which is the same move `tests/test_refusal_codes.py` makes for the refusal taxonomy:
# hold the binding as one table keyed on the FUNCTION, not a cell per parameter.
POSITIONAL_PREFIX = {
    "_author_steps":     ("session",),
    "_verify_by_replay": ("url",),
    "_learn":            ("url",),
    "_learn_n":          ("url",),
    "_replay":           ("url",),
    # TWO, and both are the subject: which step, in which session. `provider` would be a third
    # distinct type and could legally stay under the rule below — it is keyword anyway, because the
    # rule is a floor on where the `*` may go and not an instruction to put it as late as possible.
    "_replay_step":      ("session", "step"),
}

# EVERY FORWARD IS `name=name` UNLESS IT IS HERE. Fourteen rows against ~130 forwards: the shape is
# the assertion and this table is the exception list, rather than 130 hand-typed rows of which the
# table would only be as good as its worst entry.
RENAMED = {
    ("_learn", "_author_steps"):            {"goal": "author_goal"},
    ("_replay", "_author_steps"):           {"max_steps": "settings.max_steps",
                                             "block_mutations": "True"},
    ("_replay", "_replay_step"):            {"idx": "i"},
    ("_learn_n", "_learn"):                 {"reflections": "reflections or None"},
    ("_verify_by_replay", "_replay"):       {"flow": "candidate", "provider": "None",
                                             "on_step": "None", "finalize": "None",
                                             "goal": "candidate.goal", "record_har_path": "None"},
    ("run_cached", "_replay"):              {"flow": "cached", "provider": "heal_provider"},
}

# WHAT A CALLER DELIBERATELY DOES NOT FORWARD. Asserted BOTH ways: a declared drop that is actually
# forwarded is a stale entry, and an undeclared one is a parameter silently taking its default.
DELIBERATE_DROPS = {
    # `_learn` MAY write — that is how a write flow is discovered at all. `_replay`'s replan may not.
    ("_learn", "_author_steps"):      frozenset({"block_mutations"}),
    # A suffix-replan re-authors from `goal` alone; there is no grounding model on the replay path.
    ("_replay", "_author_steps"):     frozenset({"grounding"}),
    # A verification run is navigation-fidelity only: no params, no dry-run arbiter, no pre-write
    # probe, no redaction and no aux routers, because it performs no write and makes no paid call.
    ("_verify_by_replay", "_replay"): frozenset({"window_size", "params", "dry_run", "pre_write",
                                                 "redact", "aux_routers"}),
    # `reflections` is best-of-N's own state; a single learn has none to pass.
    ("run_cached", "_learn"):         frozenset({"reflections"}),
}


# ---------------------------------------------------------------------------------------------------
# The derivation. `inspect.getsource(flow_mod)`, NEVER the file by path: `scripts/prove_red.py`
# installs a mutant on PYTHONPATH, so a path-reading scan parses the pristine tree and can never
# contribute a kill (R4.75, measured live during 1.3).

def _tree():
    return ast.parse(inspect.getsource(flow_mod))


def _signatures() -> dict:
    out = {}
    for n in ast.walk(_tree()):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in CHAIN:
            out[n.name] = n
    return out


def _edges() -> dict:
    """(caller, callee) -> (positional exprs, {kwarg: expr}). One edge per pair; asserted below."""
    out: dict = {}
    seen: list = []
    for fn in ast.walk(_tree()):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for c in ast.walk(fn):
            if isinstance(c, ast.Call) and getattr(c.func, "id", None) in CHAIN:
                key = (fn.name, c.func.id)
                seen.append(key)
                out[key] = ([ast.unparse(a) for a in c.args],
                            {k.arg: ast.unparse(k.value) for k in c.keywords if k.arg})
    assert len(seen) == len(set(seen)), (
        f"a (caller, callee) pair calls the engine chain twice: "
        f"{sorted({k for k in seen if seen.count(k) > 1})}. The tables in this file are keyed on the "
        f"pair, so the second call would silently overwrite the first and go unchecked.")
    return out


# ---------------------------------------------------------------------------------------------------
# The arity pin.

def test_the_engine_chain_is_keyword_only_after_its_subject() -> None:
    sigs = _signatures()
    missing = sorted(set(CHAIN) - set(sigs))
    assert not missing, (
        f"{missing} no longer exist in flow.py under those names. A structural scan that names ONE "
        f"function asserts a negative about a body that can walk away (1.5) — fail for the MISSING "
        f"half rather than quietly scanning whatever is found.")

    derived = {name: tuple(a.arg for a in n.args.posonlyargs + n.args.args)
               for name, n in sigs.items()}
    assert derived == POSITIONAL_PREFIX, (
        "the positional prefix of the engine chain has moved.\n"
        + "\n".join(f"  {k}: {POSITIONAL_PREFIX.get(k)} -> {v}"
                    for k, v in sorted(derived.items()) if POSITIONAL_PREFIX.get(k) != v)
        + "\nAdding a positional parameter re-opens the type-silent swap step 1.1 closed. If the move "
          "is deliberate, change the table in the same diff.")
    for name, n in sigs.items():
        assert n.args.kwonlyargs, f"{name} has no keyword-only parameters at all — the `*` is gone"
    total = sum(len(v) for v in derived.values())
    print(f"{total} positional parameters across the chain "
          + ", ".join(f"{k}={len(v)}" for k, v in sorted(derived.items())))


def test_no_two_positional_parameters_of_one_function_share_a_type() -> None:
    """The rule that says where the `*` may NOT move, derived rather than asserted.

    Before 1.1, `_learn` opened `(url, goal, key)` — three `str` — and `_replay` opened `(url, key)`,
    the same two in a different order. A reader moving between them had nothing to catch a swap. Any
    prefix that satisfies this cell is one where a positional mistake is a TYPE error.
    """
    for name, n in _signatures().items():
        positional = list(n.args.posonlyargs) + list(n.args.args)
        annos = [ast.unparse(a.annotation) if a.annotation else f"<unannotated:{a.arg}>"
                 for a in positional]
        dupes = {a for a in annos if annos.count(a) > 1}
        assert not dupes, (
            f"{name}'s positional prefix takes {annos} — {sorted(dupes)} appears more than once, so "
            f"swapping those two arguments is type-silent and this step bought nothing for them.")
        print(f"  {name}({', '.join(f'{a.arg}: {t}' for a, t in zip(positional, annos))})")


def test_every_internal_call_passes_only_the_subject_positionally() -> None:
    """The arity pin's other half: the SIGNATURE permitting only a subject is worth nothing if a call
    site still spells out fifteen positional arguments — it would simply be a TypeError waiting for a
    caller nobody ran. Asserted against the declared prefix, per callee."""
    edges = _edges()
    assert len(edges) >= 9, f"only {len(edges)} engine call sites found — the derivation is stale"
    for (caller, callee), (pos, _kw) in sorted(edges.items()):
        allowed = POSITIONAL_PREFIX[callee]
        assert len(pos) <= len(allowed), (
            f"{caller} -> {callee} passes {len(pos)} positional argument(s) {pos}, but only "
            f"{list(allowed)} may be positional.")
    print(f"{len(edges)} internal call sites, all within their declared prefix")


# ---------------------------------------------------------------------------------------------------
# The forwarding pins. The arity pin cannot see a MIS-KEYED placeholder; these can.

def test_every_forward_is_name_equals_name_unless_it_is_registered() -> None:
    edges = _edges()
    surprises: dict = {}
    for key, (_pos, kw) in sorted(edges.items()):
        declared = RENAMED.get(key, {})
        differs = {p: e for p, e in kw.items() if e != p}
        if differs != declared:
            surprises[key] = (declared, differs)
    assert not surprises, (
        "an internal forward passes something other than the parameter of the same name, and it is "
        "not registered in RENAMED:\n"
        + "\n".join(f"  {c} -> {t}\n    registered: {d}\n    actual:     {a}"
                    for (c, t), (d, a) in sorted(surprises.items()))
        + "\nThis is the table a mistranslated site is visible in. If the change is deliberate, put "
          "it in RENAMED in the same diff so the next reader sees a one-line review rather than a "
          "130-argument one.")
    assert set(RENAMED) <= set(edges), (
        f"RENAMED names call sites that no longer exist: {sorted(set(RENAMED) - set(edges))}")
    n = sum(len(v) for v in RENAMED.values())
    total = sum(len(kw) for _p, kw in edges.values())
    print(f"{total} forwards across {len(edges)} sites; {n} registered as something other than "
          f"`name=name`:")
    for (c, t), d in sorted(RENAMED.items()):
        for p, e in sorted(d.items()):
            print(f"    {c} -> {t}: {p} = {e}")


def test_what_a_caller_drops_is_declared_and_declared_drops_are_really_dropped() -> None:
    """BOTH WAYS. An undeclared drop is a parameter silently taking its default -- which is how
    `_replay`'s replan could quietly acquire a grounding model, or `_verify_by_replay` a dry-run
    arbiter. A declared drop that is actually forwarded is a stale entry granting silence to nothing.
    """
    sigs, edges = _signatures(), _edges()
    wrong: list = []
    for key, (_pos, kw) in sorted(edges.items()):
        callee = key[1]
        kwonly = {a.arg for a in sigs[callee].args.kwonlyargs}
        actual = kwonly - set(kw)
        declared = DELIBERATE_DROPS.get(key, frozenset())
        if actual != declared:
            wrong.append((key, sorted(declared), sorted(actual)))
    assert not wrong, "\n".join(
        f"{c} -> {t}: declared drops {d}, actual {a}" for (c, t), d, a in wrong)
    assert set(DELIBERATE_DROPS) <= set(edges), (
        f"DELIBERATE_DROPS names call sites that no longer exist: "
        f"{sorted(set(DELIBERATE_DROPS) - set(edges))}")
    for key, d in sorted(DELIBERATE_DROPS.items()):
        print(f"  {key[0]} -> {key[1]} drops {sorted(d)}")


# ---------------------------------------------------------------------------------------------------
# The runtime cell. A distinct object per argument, `is` identity on arrival.

class _Tag:
    """A value that is only equal to itself, for a parameter nothing inspects."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<{self.name}>"


class _StrTag(str):
    """For a parameter that must really be a `str` (it reaches `flow_key`) and still be identifiable."""


class _TupTag(tuple):
    """For `redact`/`aux_routers`, which are splatted."""


class _IntTag(int):
    """For `samples`, which the dispatch compares to 1 before forwarding it."""


def _flow() -> CachedFlow:
    return CachedFlow(key="k", goal="g", start_url="u", steps=[], created_ts=0.0)


class _Cache:
    """A cache stub that is identifiable, because the real one would be shared by identity anyway."""

    def __init__(self, cached=None) -> None:
        self.cached = cached

    def get(self, key):
        return self.cached

    def refusal(self, key):
        return None


def _checkable(caller: str, callee: str) -> dict:
    """{callee param: caller param} for the forwards a RUNTIME cell can assert identity on.

    DERIVED from the same AST the pins above read, so the two instruments compose instead of stating
    the same thing twice: a forward is identity-checkable exactly when its expression is a bare name
    that is also a parameter of the caller. Everything else (`None`, `settings.max_steps`,
    `reflections or None`, a local like `heal_provider`) is the AST table's job.
    """
    tree = _tree()
    callers = {n.name: n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    params = {a.arg for a in (callers[caller].args.posonlyargs + callers[caller].args.args
                              + callers[caller].args.kwonlyargs)}
    _pos, kw = _edges()[(caller, callee)]
    return {p: e for p, e in kw.items() if e in params}


# One row per browser-free edge, with the number of identity-checkable forwards COMMITTED. A
# derivation that silently starts finding nothing would otherwise pass this cell with zero assertions
# — the anti-vacuity rule S14 paid for twice.
RUNTIME_EDGES = {
    ("run_cached", "_replay"): 18,
    ("run_cached", "_learn"): 20,
    ("run_cached", "_learn_n"): 22,
    ("_learn_n", "_learn"): 21,
    # The one the critic named: sixteen positional arguments with four bare `None`s. Ten of them are
    # identity-checkable here, INCLUDING `flow=candidate` -- a rename to another PARAMETER is still a
    # forward a sentinel can follow, and it is one of the two the mis-keying would have swallowed.
    ("_verify_by_replay", "_replay"): 10,
}


@pytest.mark.parametrize("edge", sorted(RUNTIME_EDGES), ids=lambda e: f"{e[0]}->{e[1]}")
async def test_the_runtime_forward_keeps_each_argument_under_its_own_name(edge, monkeypatch) -> None:
    """The critic's clause on step 1.1, at run time.

    The arity pin is satisfied by `_replay(..., on_step=finalize, finalize=on_step)`. This is not: a
    distinct object goes in under each name and the same object must come out under the same name.
    """
    caller, callee = edge
    expected = _checkable(caller, callee)
    assert len(expected) == RUNTIME_EDGES[edge], (
        f"{caller} -> {callee} now has {len(expected)} identity-checkable forwards, not "
        f"{RUNTIME_EDGES[edge]}. If a forward became a local or a literal it belongs in RENAMED; if "
        f"one became checkable, raise the number in the same diff.")

    seen: dict = {}

    async def _stub(url, **kw):
        seen["url"] = url
        seen.update(kw)
        return FlowReport(mode="learn", success=True, note="stub", extra={"cached": True})

    monkeypatch.setattr(flow_mod, callee, _stub)

    # A distinct object per CALLER parameter. A few must really be their declared type -- `url`,
    # `goal` and `scope` reach `flow_key`; `redact`/`aux_routers` are splatted -- so those are
    # subclasses, which keeps `is` identity while behaving as the real thing.
    tags = {name: _Tag(name) for name in expected.values()}
    tags["url"] = _StrTag("<url>")
    for name in ("goal", "scope", "key"):
        if name in tags:
            tags[name] = _StrTag(f"<{name}>")
    for name in ("redact", "aux_routers"):
        if name in tags:
            tags[name] = _TupTag()

    await _drive(caller, callee, tags)

    # The SUBJECT arrives positionally, so it is not in `expected` -- and it is the one argument every
    # edge here passes, so leaving it unchecked would be the hole in the middle of the cell.
    assert seen.get("url") is tags["url"], (
        f"{caller} -> {callee} did not forward its subject: got {seen.get('url')!r}")
    wrong = {p: (tags[src], seen.get(p)) for p, src in expected.items()
             if seen.get(p) is not tags[src]}
    assert not wrong, (
        f"{caller} -> {callee} delivered {len(wrong)} argument(s) under the wrong name — a MIS-KEYED "
        f"forward, which the arity pin cannot see:\n"
        + "\n".join(f"  {p}: expected {e!r}, got {g!r}" for p, (e, g) in sorted(wrong.items())))
    print(f"{caller} -> {callee}: {len(expected)} argument(s) arrived under their own name")


async def _drive(caller: str, callee: str, tags: dict) -> None:
    """Call `caller` so that it reaches `callee`, with `tags` supplying every checkable parameter.

    SUBSTITUTIONS ARE WRITTEN BACK INTO `tags`, deliberately. Three parameters cannot be a bare
    sentinel because the dispatch inspects them -- `cache` is `.get`/`.refusal`-ed, `governor` and
    `cache` are rebound by `x or default()`, and `samples` is compared to 1. Substituting them in a
    LOCAL copy is how the first draft of this cell asserted identity against an object it had not
    passed, and reported a mis-keyed forward that did not exist.
    """
    if caller == "run_cached":
        # Truthy on purpose: `run_cached` does `cache = cache or FlowCache()`, so a falsy stub would
        # be silently replaced and the identity assertion would be about the engine's own object.
        tags["cache"] = _Cache(_flow() if callee == "_replay" else None)
        tags["governor"] = PacingGovernor()
        if callee == "_learn_n":
            tags["samples"] = _IntTag(2)        # `samples > 1` is what selects this branch
        if callee != "_replay":
            tags.setdefault("provider", _Tag("provider"))  # or the learn path returns `miss`
        kw = {k: v for k, v in tags.items() if k in _params(caller)}
        kw["mode"] = "replay" if callee == "_replay" else "learn"
        await flow_mod.run_cached(**kw)
    elif caller == "_learn_n":
        tags["samples"] = _IntTag(1)
        await flow_mod._learn_n(**tags)
    elif caller == "_verify_by_replay":
        tags["candidate"] = _flow()             # `candidate.goal` is read before the forward
        await flow_mod._verify_by_replay(**tags)
    else:  # pragma: no cover - a new row in RUNTIME_EDGES with no driver must fail LOUD
        raise AssertionError(f"no driver for {caller} -> {callee}")


def _params(name: str) -> set:
    return set(inspect.signature(getattr(flow_mod, name)).parameters)
