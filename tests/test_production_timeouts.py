"""CI may buy itself more time; the SHIPPED default may not drift (R4.24).

A localhost round-trip stalled past the 5 s action budget on the Windows runner, so CI now sets
`ULTRACUA_ACTION_TIMEOUT_MS=15000`. That is a mitigation for a machine measurably slower than a dev box,
and it is legitimate for exactly one reason: **it does not change what ships.**

The failure mode this file exists to prevent is the obvious next step for whoever meets the flake again
and does not know the history — raise the DEFAULT instead of the CI env var, and the flake goes quiet
everywhere at the cost of every real user waiting three times as long before a genuinely stuck page fails
loud. S17 states the rule ("do not weaken the production bound"); a rule in a document is not a guard, and
this register's own recurring finding is that citing a guard is not carrying it.

Asserted against the SOURCE rather than the imported value, because `Settings` reads the environment at
class-definition time: under CI's env the imported default IS 15000, so an import-time check here would
assert the very thing it is meant to catch.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_CONFIG = Path("src/ultracua/config.py")

# name -> (env var, shipped default). Both timeouts, because raising either one hides the same class of
# stall, and `nav_timeout_ms` is the one a form-POST navigation actually rides.
_BOUNDS = {
    "action_timeout_ms": ("ULTRACUA_ACTION_TIMEOUT_MS", "5000"),
    "nav_timeout_ms": ("ULTRACUA_NAV_TIMEOUT_MS", "15000"),
}


def _defaults_from_source() -> dict:
    """`{field: (env_var, default_literal)}` for every `int(os.getenv("X", "N"))` field in Settings."""
    tree = ast.parse(_CONFIG.read_text(encoding="utf-8"))
    out: dict = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value):
            continue
        call = node.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "int":
            inner = call.args[0] if call.args else None
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "getenv" and len(inner.args) == 2):
                env, default = inner.args
                if isinstance(env, ast.Constant) and isinstance(default, ast.Constant):
                    out[node.target.id] = (env.value, default.value)
    return out


@pytest.mark.parametrize("field", sorted(_BOUNDS))
def test_the_shipped_timeout_default_has_not_been_raised(field: str) -> None:
    """If this fails because CI is flaky, the fix is the CI env var, not this number."""
    found = _defaults_from_source()
    assert field in found, (
        f"{field} is no longer an `int(os.getenv(...))` default in config.py — this guard has stopped "
        f"guarding anything, which is worse than it failing")
    env, default = found[field]
    assert (env, default) == _BOUNDS[field], (
        f"{field} ships as {found[field]}, expected {_BOUNDS[field]}. Raising a production timeout makes "
        f"every real user wait longer before a stuck page fails LOUD. CI buys time with "
        f"`{_BOUNDS[field][0]}` in .github/workflows/ci.yml; that is the knob to reach for.")


def test_the_guard_can_actually_fail() -> None:
    """Anti-vacuity. A parser that silently finds nothing would let every assertion above pass over an
    empty dict — the shape this project has already shipped twice (a shard checker that under-collected,
    a redaction docstring that cited a floor it did not carry)."""
    found = _defaults_from_source()
    assert len(found) >= 2, f"the source parser found {len(found)} int-env defaults; it is not working"
    assert found["action_timeout_ms"] == ("ULTRACUA_ACTION_TIMEOUT_MS", "5000")


def test_raising_the_bound_for_ci_is_an_env_var_and_the_env_var_still_exists() -> None:
    """The escape hatch must stay available even though R4.24's mitigation was NOT shipped.

    The whole argument for not touching these defaults is that a slow environment has a knob of its own.
    If someone deletes the `os.getenv` indirection — "nothing sets it, so simplify" — the only remaining
    way to give CI more time becomes editing the shipped default, which is the thing above forbids. The
    knob is load-bearing precisely when it is unused.
    """
    found = _defaults_from_source()
    for field, (env, _default) in _BOUNDS.items():
        assert found.get(field, (None, None))[0] == env, (
            f"{field} no longer reads {env} from the environment — a slow machine now has no way to buy "
            f"time except by changing what ships")
