"""Arm a guard: mutate the thing it watches, in-process, and require the guard to go RED.

WHY THIS EXISTS RATHER THAN `scripts/prove_red.py`. That harness installs a mutant by putting a copy
of `src/` first on `PYTHONPATH`, and pytest puts the repo ROOT at `sys.path[0]` — so a mutation of
anything under `benchmarks/` is never the module the tests import, and every one reports as a
SURVIVOR while the guard is fine (R4.77). For `src/` code, use `prove_red`: it is stronger, because
it runs a whole suite against the mutant rather than one named cell.

WHY IT IS SHARED. B3 built this for `benchmarks/outcomes.py` and 1.3 needs it for
`benchmarks/variance.py`. Two copies of one harness is how the harness drifts, and this file's own
lessons — every one of them paid for by a false verdict — are exactly what a second copy would lose.
"""

from __future__ import annotations

import inspect
import linecache
import os
import tempfile
import textwrap


def mutate_function(monkeypatch, module, name: str, find: str, repl: str) -> None:
    """Rebind `module.<name>` to a source-level mutation of itself, for this test only.

    A stale or ambiguous `find` raises rather than silently applying nothing — `prove_red.py`'s own
    rule, and the reason it reports a non-matching mutation as an ERROR and not as a survivor.

    THE MUTANT'S SOURCE IS RETRIEVABLE, and that is not a nicety. Several guards are STRUCTURAL —
    they read `inspect.getsource` and scan the AST. Compiling a mutant under a `<mutant:name>`
    pseudo-filename makes `getsource` raise `OSError: could not get source code`, the guard dies
    before it reaches its scan, and this harness scores the crash as a kill. That happened THREE
    times while B3 was being written, twice in cells added an hour after the first was fixed by
    hand — which is what says the fix belongs here rather than in a cell.

    So the mutant is compiled under a `.py` filename registered in `linecache`, which is one of the
    two things `inspect.getsourcefile` accepts for a path that does not exist on disk. Nothing is
    written to disk, and `monkeypatch` removes the entry afterwards.

    NOTE THE HOLE THIS STILL HAS: the `find` text is checked, the REPLACEMENT is not. A replacement
    that no longer compiles against the current module applies cleanly and then dies at call time —
    measured, when a helper gained a parameter and four mutations kept calling it the old way.
    `assert_red` refuses a `TypeError` as a kill for exactly that reason.
    """
    src = textwrap.dedent(inspect.getsource(getattr(module, name)))
    n = src.count(find)
    if n != 1:
        raise AssertionError(
            f"mutation for {module.__name__}.{name} matches its find-text {n} times, not once. The "
            f"function has moved and this mutation now proves nothing — re-express it against the "
            f"current source rather than deleting it.\nfind: {find!r}")
    mutated = src.replace(find, repl, 1)
    path = os.path.join(tempfile.gettempdir(), f"ultracua_mutant_{module.__name__}_{name}.py")
    monkeypatch.setitem(linecache.cache, path,
                        (len(mutated), None, mutated.splitlines(True), path))
    ns: dict = {}
    exec(compile(mutated, path, "exec"), module.__dict__, ns)
    mutant = ns[name]
    # Proven, not assumed: a structural guard may be about to call this on the mutant, and if it
    # raises the guard never runs and its failure is credited to the mutation.
    assert inspect.getsource(mutant), f"the mutant of {name!r} has no retrievable source"
    monkeypatch.setattr(module, name, mutant)


def mutate_value(monkeypatch, module, name: str, value) -> None:
    assert hasattr(module, name), f"{name} no longer exists in {module.__name__}"
    monkeypatch.setattr(module, name, value)


def assert_red(guard, *args) -> str:
    """Run one guard cell and require it to fail. Returns the message, for printing.

    `AssertionError` or a domain exception both count: a guard whose subject has been broken may
    notice by asserting or by the mutated code blowing up on the way. What does NOT count is
    passing, and what also does not count is `TypeError` from calling the guard wrongly — S14's
    third trap, where a bad kwarg raised and the harness read it as a legitimate refusal.

    `BaseException`, NOT `Exception`, and this cost five false verdicts on its first run. A guard
    written as `with pytest.raises(...)` fails by raising `_pytest.outcomes.Failed`, which descends
    from `BaseException` — so an `except Exception` here silently let the loudest kind of guard
    (the ones asserting a REFUSAL) escape as "the cell passed against a mutation". The instrument
    was reporting its own blind spot as a hole in the suite.
    """
    try:
        guard(*args)
    except (KeyboardInterrupt, SystemExit):
        raise
    except TypeError as exc:
        raise AssertionError(
            f"{guard.__name__} raised TypeError ({exc}) — that is this harness calling the cell "
            f"wrongly, not the cell noticing the mutation") from exc
    except BaseException as exc:  # noqa: BLE001 - any genuine complaint is a kill
        return f"{type(exc).__name__}: {str(exc).splitlines()[0][:110]}"
    raise AssertionError(
        f"{guard.__name__} PASSED against a mutation that violates the property it names. The cell "
        f"is not guarding what its name says it guards.")
