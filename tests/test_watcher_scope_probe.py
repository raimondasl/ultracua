"""The watcher-scope probe's claims, held against the engine's own source (0.177.0).

`benchmarks/watcher_scope_probe.py` answers the question that decides whether moving the learn and
heal watchers to context scope is safe: does the wider scope pull in traffic that would be
CLASSIFIED AS A WRITE and mark a step mutating that never wrote? It measured zero on both baselined
substrates. A probe reporting a reassuring zero is worth exactly what its fidelity is worth, so the
things its answer rests on are pinned here.

THREE OF THESE CELLS WERE REWRITTEN AFTER AN ADVERSARIAL PASS SHOWED THEY COULD NOT FAIL, and the
three failures are worth naming because they are the same failure three ways -- each asserted that
something was PRESENT where the claim was about how it is USED:
  * an order claim checked by membership (`"is_write_request" in called`), which a reordering passes;
  * an `about:blank` claim checked by the literal existing anywhere in the function, which deleting
    the hop passes;
  * an import claim checked by a source-TEXT substring, in a file whose own docstring contains that
    text -- this repository's most-repeated scan defect, arrived at from a new direction.
"""

from __future__ import annotations

import ast
import inspect


def test_the_probe_classifies_with_the_engines_own_two_predicates() -> None:
    """The probe must ask what `_watch_request` asks: `is_write_request` says write AND
    `body_says_read` does not clear it. A reimplementation would measure the probe rather than the
    product -- R4.134's rule, which cost a whole survey once.

    Asserted by OBJECT IDENTITY, not by a source substring. The first draft checked that the import
    line appeared in the file, which its own docstring also satisfies; identity cannot be satisfied
    by a look-alike defined locally."""
    from ultracua import safety
    from benchmarks import watcher_scope_probe as P

    assert P.is_write_request is safety.is_write_request, (
        "the probe's `is_write_request` is not the engine's -- its zero describes the probe")
    assert P.body_says_read is safety.body_says_read, (
        "the probe's `body_says_read` is not the engine's -- its zero describes the probe")

    used = {n.func.id for n in ast.walk(ast.parse(inspect.getsource(P._classify)))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert {"is_write_request", "body_says_read"} <= used, (
        f"`_classify` calls {sorted(used)}; it must use both of the engine's predicates")


def test_the_learn_watcher_asks_exactly_those_two_and_in_that_order() -> None:
    """The other half of the same claim, and the half that rots: if `_watch_request` grows a THIRD
    condition, the probe's zero stops describing it.

    Both halves are now really asserted. The first draft tested membership (`"is_write_request" in
    called`), which a third predicate passes and a reordering passes -- it could not fail for either
    thing its own name promised. The SET is pinned so a third condition fails, and the ORDER is read
    off line numbers, which is a fact about the tree rather than about the characters."""
    from ultracua import flow as flow_mod, safety

    tree = ast.parse(inspect.getsource(flow_mod))
    watcher = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_watch_request"), None)
    assert watcher is not None, "`_watch_request` is gone; the probe describes a watcher that moved"

    # Every call the watcher makes to something `safety` exports -- so a NEW safety predicate is a
    # red cell rather than a silent widening of what the probe would have to mirror.
    exported = {n for n in dir(safety) if not n.startswith("_")}
    calls = [(n.func.id, n.lineno) for n in ast.walk(watcher)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in exported]
    names = {n for n, _ in calls}
    assert names == {"is_write_request", "body_says_read"}, (
        f"`_watch_request` now asks {sorted(names)}. `benchmarks/watcher_scope_probe.py` mirrors "
        f"exactly two predicates, so its '0 write-classified' no longer describes this watcher -- "
        f"re-run it and re-read the number before trusting it.")

    first = min(ln for n, ln in calls if n == "is_write_request")
    second = min(ln for n, ln in calls if n == "body_says_read")
    assert first < second, (
        "`body_says_read` is now consulted before `is_write_request`. The probe's `_classify` short-"
        "circuits in the other order, so the two disagree on any request the method clears.")


def test_the_probe_actually_navigates_to_about_blank() -> None:
    """Odoo routes on the HASH, so a goto between two `#action=` urls is a SAME-DOCUMENT navigation
    that resolves instantly and measures the view it just left (R4.141). Without the hop every Odoo
    row is quietly wrong, and wrong in the REASSURING direction -- fewer requests observed.

    Asserted as an argument to a `goto` call. The first draft asserted the literal existed anywhere
    in the function, so deleting the hop while leaving the comment mentioning it would have passed."""
    from benchmarks import watcher_scope_probe as P

    hops = [n for n in ast.walk(ast.parse(inspect.getsource(P.probe)))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "goto" and n.args
            and isinstance(n.args[0], ast.Constant) and n.args[0].value == "about:blank"]
    assert hops, (
        "the probe no longer NAVIGATES to about:blank before each row, so its Odoo rows measure the "
        "previous view (R4.141) and under-report the traffic this probe exists to count")
