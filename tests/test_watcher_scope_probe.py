"""The watcher-scope probe's claims, held against the engine's own source (0.177.0).

`benchmarks/watcher_scope_probe.py` answers the question that decides whether moving the learn and
heal watchers to context scope is safe: does the wider scope pull in traffic that would be
CLASSIFIED AS A WRITE and mark a step mutating that never wrote? It measured zero on both baselined
substrates. A probe reporting a reassuring zero is worth exactly what its fidelity is worth, so the
two things its answer rests on are pinned here.

Read from the AST, not from source text -- this repository has gone red on its own prose ten times.
"""

from __future__ import annotations

import ast
import inspect


def test_the_probe_classifies_with_the_engines_own_two_predicates() -> None:
    """The probe must ask what `_watch_request` asks, in the same order: `is_write_request` says
    write AND `body_says_read` does not clear it. A reimplementation would measure the probe rather
    than the product -- R4.134's rule, which cost a whole survey once."""
    from benchmarks import watcher_scope_probe as P

    used = {n.func.id for n in ast.walk(ast.parse(inspect.getsource(P._classify)))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert {"is_write_request", "body_says_read"} <= used, (
        f"`_classify` calls {sorted(used)}. It must use the engine's own two predicates; anything "
        f"else makes the zero it reports a fact about the probe.")

    # ...and they must be the ENGINE's, not local look-alikes.
    src = inspect.getsource(P)
    assert "from ultracua.safety import body_says_read, is_write_request" in src, (
        "the probe no longer imports both predicates from `ultracua.safety`")


def test_the_learn_watcher_still_asks_those_two_in_that_order() -> None:
    """The other half of the same claim, and the half that rots: if `_watch_request` grows a third
    condition, the probe's zero stops describing it. Derived from the engine, so it fails HERE."""
    from ultracua import flow as flow_mod

    src = inspect.getsource(flow_mod)
    watcher = next((n for n in ast.walk(ast.parse(src))
                    if isinstance(n, ast.FunctionDef) and n.name == "_watch_request"), None)
    assert watcher is not None, "`_watch_request` is gone; the probe describes a watcher that moved"

    called = [n.func.id for n in ast.walk(watcher)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "is_write_request" in called and "body_says_read" in called, (
        f"`_watch_request` now calls {sorted(set(called))}. The probe mirrors two predicates; if the "
        f"watcher's classification changed, re-run "
        f"`python -m benchmarks.watcher_scope_probe` and re-read its zero before trusting it.")


def test_the_probe_hops_through_about_blank() -> None:
    """Odoo routes on the HASH, so a goto between two `#action=` urls is a SAME-DOCUMENT navigation
    that resolves instantly and measures the view it just left (R4.141). Without the hop every Odoo
    row is quietly wrong, and quietly wrong in the reassuring direction -- fewer requests observed."""
    from benchmarks import watcher_scope_probe as P

    consts = {n.value for n in ast.walk(ast.parse(inspect.getsource(P.probe)))
              if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "about:blank" in consts, (
        "the probe no longer hops through about:blank, so its Odoo rows measure the previous view")
