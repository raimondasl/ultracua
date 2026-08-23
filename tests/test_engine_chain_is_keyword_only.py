"""reshape-plan steps 1.1 and 1.8 — the SHAPE of the engine chain, and the REACH of its bundles.

1.1: KEYWORD-ONLY AFTER THE SUBJECT. Six functions took between 8 and 23 arguments positionally.
`_verify_by_replay`'s call was the worst: sixteen positional arguments of which FOUR were a bare
`None`, at positions 5, 7, 9 and 14. Swapping any two of those four is type-silent — `provider`,
`on_step`, `finalize` and `record_har_path` all accept None and three accept a callable — so nothing
in the type system, the suite, or a reviewer's eye separates the right order from a wrong one.
`_learn_n`'s signature carried the scar in a comment: inserting a parameter above `reflect` once made
`reflect` arrive as `aux_routers`.

1.8: TWO BUNDLES INSTEAD OF TWENTY-ONE SCALARS, and it changes what this file has to prove. A
parameter list ENFORCED withholding: `_learn` could not read `params` because it was not handed one,
and `_replay` could not read `grounding`. `RunOptions`/`RunHooks` hand every inner function all of
them, so that enforcement is gone and lives here instead — `RECEIVED_BEFORE_1_8` is what each
function got before the migration, and no function may read outside its row.

FOUR SENSORS, BECAUSE NO ONE OF THEM IS ENOUGH.

  * The ARITY pin (`POSITIONAL_PREFIX`) says a parameter cannot be passed positionally at all. It
    cannot see a MIS-KEYED forward: `_replay(..., on_step=finalize, finalize=on_step)` satisfies it
    completely. That is the critic's clause on 1.1.
  * The FORWARDING pins say every internal call forwards `name=name` unless the difference is
    REGISTERED in `RENAMED`, and that the registered ones are exactly what the source does.
  * The REACH pins (`RECEIVED_BEFORE_1_8`, `CLEARS`) are 1.8's, and they are the only thing standing
    where a signature used to stand.
  * The RUNTIME cell drives the browser-free edges with a distinct object per argument and asserts
    `is` identity on arrival — the only one of the four that can fail for a forward the AST reads as
    fine.

AND ONE PIN THAT IS NOT HERE: how many times each hook FIRES. That is behaviour rather than shape,
it needs a browser, and it lives in `tests/test_hook_fire_counts.py` against a table captured before
1.8 moved anything.

WHAT SHOULD BE POSITIONAL, AND WHY IT STOPS THERE. The prefix is the call's SUBJECT: which page the
run is about (`url`), or which session and step are being acted on. The rule that says where the `*`
may NOT move is derived rather than asserted: no two positional parameters of one function may share
an annotation.
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
    ("_learn_n", "_learn"):                 {"reflections": "reflections or None"},
    ("_replay", "_author_steps"):           {"opts": "opts.without('grounding')",
                                             "max_steps": "settings.max_steps",
                                             "block_mutations": "True"},
    ("_replay", "_replay_step"):            {"idx": "i"},
    ("_verify_by_replay", "_replay"):       {"opts": "opts.without('window_size', 'params', "
                                                     "'dry_run', 'redact', 'aux_routers', "
                                                     "'record_har_path')",
                                             "hooks": "hooks.without('on_step', 'finalize', "
                                                      "'pre_write')",
                                             "flow": "candidate", "provider": "None",
                                             "goal": "candidate.goal"},
    ("run_cached", "_learn"):               {"hooks": "hooks.without('pre_write')"},
    ("run_cached", "_replay"):              {"flow": "cached", "provider": "heal_provider"},
}


# WHAT A CALLER DELIBERATELY DOES NOT FORWARD. Asserted BOTH ways: a declared drop that is actually
# forwarded is a stale entry, and an undeclared one is a parameter silently taking its default.
#
# SHORT SINCE 1.8, and the shortness is the point rather than a loss: what used to be six absent
# arguments in a sixteen-item list is now a NAMED clearing (`opts.without(...)` / `hooks.without(...)`)
# that `CLEARS` below holds. Only the two arguments outside the bundles are left here.
DELIBERATE_DROPS = {
    # `_learn` MAY write — that is how a write flow is discovered at all. `_replay`'s replan may not,
    # and passes `block_mutations=True` explicitly.
    ("_learn", "_author_steps"): frozenset({"block_mutations"}),
    # `reflections` is best-of-N's own state; a single learn has none to pass.
    ("run_cached", "_learn"):    frozenset({"reflections"}),
}

# WHAT EACH FUNCTION CLEARS OUT OF THE BUNDLE BEFORE PASSING IT ON — 1.8's replacement for a
# `None` nine arguments deep. Committed, because these are the deliberate silences of the engine and
# each one is a decision: a verification run carries no caller artefact and makes no paid call; a
# suffix-replan has no grounding model; the learn path never gets `pre_write` (R4.12).
CLEARS = {
    "_verify_by_replay": frozenset({"window_size", "params", "dry_run", "redact", "aux_routers",
                                    "record_har_path", "on_step", "finalize", "pre_write"}),
    "_replay": frozenset({"grounding"}),
    "run_cached": frozenset({"pre_write"}),
}

# WHAT EACH FUNCTION RECEIVED BEFORE 1.8 — captured from `origin/main` at authoring time, and the
# whole point of it: A BUNDLE MAKES AVAILABLE WHAT A PARAMETER LIST WITHHELD. `_learn` never received
# `params` or `dry_run` and must not start reading them merely because they now travel in the same
# object. `_replay` never received `grounding`. Nothing here is a style rule; each is a behaviour
# this step must not change by accident.
RECEIVED_BEFORE_1_8 = {
    "_author_steps": frozenset({"block_mutations", "goal", "governor", "grounding", "max_steps",
                                "on_step", "provider", "session"}),
    "_verify_by_replay": frozenset({"browser", "cache", "candidate", "extra_headers", "governor",
                                    "headless", "key", "prepare", "scope", "storage_state", "url"}),
    "_learn": frozenset({"aux_routers", "browser", "cache", "extra_headers", "finalize", "goal",
                         "governor", "grounding", "headless", "key", "max_steps", "on_step",
                         "prepare", "provider", "record_har_path", "redact", "reflections", "scope",
                         "storage_state", "url", "verifier", "verify_replay", "window_size"}),
    "_learn_n": frozenset({"aux_routers", "browser", "cache", "extra_headers", "finalize", "goal",
                           "governor", "grounding", "headless", "key", "max_steps", "on_step",
                           "prepare", "provider", "record_har_path", "redact", "reflect", "samples",
                           "scope", "storage_state", "url", "verifier", "verify_replay",
                           "window_size"}),
    "_replay": frozenset({"aux_routers", "browser", "cache", "dry_run", "extra_headers", "finalize",
                          "flow", "goal", "governor", "headless", "key", "on_step", "params",
                          "pre_write", "prepare", "provider", "record_har_path", "redact", "scope",
                          "storage_state", "url", "window_size"}),
    "_replay_step": frozenset({"dry_run", "goal", "governor", "idx", "params", "provider", "scope",
                               "session", "step", "tr"}),
}


# ---------------------------------------------------------------------------------------------------
# The derivation. `inspect.getsource(flow_mod)`, NEVER the file by path: `scripts/prove_red.py`
# installs a mutant on PYTHONPATH, so a path-reading scan parses the pristine tree and can never
# contribute a kill (R4.75, measured live during 1.3).

def _tree():
    return ast.parse(inspect.getsource(flow_mod))


def _signatures() -> dict:
    """name -> the def node. UNIQUENESS IS ASSERTED, because a dict keyed on the name is last-wins:
    a nested or duplicated definition would silently shadow the real one and every pin below would be
    about a function nothing calls. Same reason `_edges` asserts one call per pair."""
    out: dict = {}
    for n in ast.walk(_tree()):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in CHAIN:
            assert n.name not in out, (
                f"{n.name} is defined more than once in flow.py — the pins in this file are keyed on "
                f"the name, so one of the two would go unchecked.")
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
        # ONE shared token for "no annotation", not one per parameter name. Two UNANNOTATED
        # positionals are the most ambiguous case there is, and tagging them apart by their own names
        # would have let exactly that pair through the duplicate check below.
        annos = [ast.unparse(a.annotation) if a.annotation else "<unannotated>"
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
    hits = [n for n in ast.walk(_tree())
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == caller]
    assert len(hits) == 1, f"{caller} is defined {len(hits)} times in flow.py — which one forwards?"
    params = {a.arg for a in (hits[0].args.posonlyargs + hits[0].args.args
                              + hits[0].args.kwonlyargs)}
    _pos, kw = _edges()[(caller, callee)]
    return {p: e for p, e in kw.items() if e in params}


# One row per browser-free edge, with the number of identity-checkable forwards COMMITTED. A
# derivation that silently starts finding nothing would otherwise pass this cell with zero assertions
# — the anti-vacuity rule S14 paid for twice.
RUNTIME_EDGES = {
    ("run_cached", "_replay"): 3,
    ("run_cached", "_learn"): 4,
    ("run_cached", "_learn_n"): 4,
    ("_learn_n", "_learn"): 7,
    # The one the critic named at 1.1. It used to pass sixteen positional arguments with four bare
    # `None`s; since 1.8 it passes two bundles with their silences NAMED, and four of its forwards
    # are still objects a sentinel can follow all the way in.
    ("_verify_by_replay", "_replay"): 4,
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
        # REAL BUNDLES, not sentinels, and identity still holds: `_learn_n` dereferences
        # `opts.samples`/`opts.grounding`/`opts.reflect`/`opts.aux_routers` before forwarding, so a
        # `_Tag` would die on the first read. The object that goes in is the object that must come
        # out, which is the whole assertion, and a real one satisfies both.
        tags["opts"] = flow_mod.RunOptions(governor=PacingGovernor(), samples=1)
        tags["hooks"] = flow_mod.RunHooks()
        await flow_mod._learn_n(**tags)
    elif caller == "_verify_by_replay":
        tags["candidate"] = _flow()             # `candidate.goal` is read before the forward
        tags["opts"] = flow_mod.RunOptions(governor=PacingGovernor())
        tags["hooks"] = flow_mod.RunHooks()
        await flow_mod._verify_by_replay(**tags)
    else:  # pragma: no cover - a new row in RUNTIME_EDGES with no driver must fail LOUD
        raise AssertionError(f"no driver for {caller} -> {callee}")


def _params(name: str) -> set:
    return set(inspect.signature(getattr(flow_mod, name)).parameters)


# ---------------------------------------------------------------------------------------------------
# 5. STEP 1.8's OWN PIN. A bundle makes available what a parameter list withheld.

def _bundle_use(fn_name: str) -> tuple:
    """(what this function READS off the bundles, what it CLEARS out of them)."""
    tree = _tree()
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name)
    reads = {n.attr for n in ast.walk(fn)
             if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
             and n.value.id in ("opts", "hooks") and n.attr != "without"}
    clears = {a.value for n in ast.walk(fn)
              if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "without"
              for a in n.args if isinstance(a, ast.Constant)}
    return reads, clears


@pytest.mark.parametrize("fn", sorted(RECEIVED_BEFORE_1_8))
def test_no_engine_function_reads_more_than_it_used_to_receive(fn: str) -> None:
    """THE CENTRAL RISK OF STEP 1.8, and the only cell that can see it.

    Before the bundles, a function could not read what it was not handed — `_learn` had no `params`
    and no `dry_run`, `_replay` had no `grounding`. Threading two objects instead of twenty scalars
    hands every function ALL of them, and the withholding that used to be enforced by the signature
    is now enforced by nothing at all. Except this.

    A read outside the set is not a style problem: it is a behaviour change smuggled into a
    migration, and it would be invisible in a diff that is already moving every call site.
    """
    reads, _clears = _bundle_use(fn)
    extra = sorted(reads - RECEIVED_BEFORE_1_8[fn])
    assert not extra, (
        f"{fn} reads {extra} off the bundle, and did NOT receive {'it' if len(extra) == 1 else 'them'} "
        f"before 1.8. Threading a bundle made {'it' if len(extra) == 1 else 'them'} reachable; that is "
        f"not permission to read {'it' if len(extra) == 1 else 'them'}. If the widening is deliberate "
        f"it is a behaviour change and belongs in its own diff with its own reason.")
    print(f"{fn:<20} reads {len(reads):>2} of the {len(RECEIVED_BEFORE_1_8[fn])} it used to receive")


def test_every_clearing_is_declared_and_every_declaration_clears_something() -> None:
    """BOTH WAYS. An undeclared clearing is a silent withdrawal — the thing 1.8's `without()` exists
    to make visible in the first place; a declared one that no longer happens is a stale entry
    granting cover to nothing."""
    derived = {fn: clears for fn in (*RECEIVED_BEFORE_1_8, "run_cached")
               if (clears := _bundle_use(fn)[1])}
    assert derived == CLEARS, (
        "the clearings the source makes and the CLEARS table disagree.\n"
        + "\n".join(f"  {k}: table={sorted(CLEARS.get(k, ()))} actual={sorted(v)}"
                    for k, v in sorted({**{k: set() for k in CLEARS}, **derived}.items())
                    if CLEARS.get(k, frozenset()) != frozenset(v)))
    for fn, names in sorted(CLEARS.items()):
        print(f"  {fn} clears {sorted(names)}")


def test_r412_is_preserved_and_not_quietly_fixed() -> None:
    """R4.12 is OPEN and must stay open through this step. `_learn` never received `pre_write`, so
    its whole-flow confirm is a bare presence check rather than an absent->present transition.
    Bundling would have CLOSED it by accident — a silent fix inside a migration is as unreviewable
    as a silent break, and the plan's own row for 1.8 says `R4.12 included -- preserved, not fixed`.
    """
    assert "pre_write" in CLEARS["run_cached"], "the learn path was handed `pre_write` — R4.12 fixed"
    assert "pre_write" not in RECEIVED_BEFORE_1_8["_learn"]
    reads, _ = _bundle_use("_learn")
    assert "pre_write" not in reads, "`_learn` now reads `pre_write` — R4.12 closed as a side effect"


def test_every_bundle_field_is_read_by_someone() -> None:
    """ANTI-VACUITY on the bundles themselves. A field nobody reads is a parameter that stopped
    working, and the flat signature made that loud (an unused argument is visible); an object with
    sixteen attributes hides it."""
    import dataclasses
    declared = ({f.name for f in dataclasses.fields(flow_mod.RunOptions)}
                | {f.name for f in dataclasses.fields(flow_mod.RunHooks)})
    read_or_cleared: set = set()
    for fn in (*RECEIVED_BEFORE_1_8, "run_cached"):
        r, c = _bundle_use(fn)
        read_or_cleared |= r | c
    unused = sorted(declared - read_or_cleared)
    assert not unused, (
        f"{unused} travel(s) in a bundle and is read by NOTHING. Before 1.8 an unused parameter was "
        f"visible in a signature; inside an object it is invisible, so this is the cell that keeps it "
        f"honest.")
    print(f"all {len(declared)} bundle fields are read or explicitly cleared somewhere")
