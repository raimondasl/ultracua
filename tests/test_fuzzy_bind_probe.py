"""D2's probe, held against the resolver it claims to describe (0.178.0, R4.156).

`benchmarks/fuzzy_bind_probe.py` adjudicates D2 by building four arms as SOURCE PATCHES to a copy of
`src/` and running the real resolver in each. That design is what makes its cost numbers believable —
a refused Tier-1 fuzzy bind falls through to Tier 2, where css may re-bind the same element, so a
post-filter over the shipped resolver's own output would have under-counted the difference between
the candidates and made contradiction-only look identical to agreement.

It also makes the probe rot in a specific way: an anchor that no longer matches leaves a probe
describing code that is not there. `_build` raises on that at run time; these cells fail at edit time,
in the fast tier, without a browser.
"""

from __future__ import annotations

import ast
import inspect
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _locators_source() -> str:
    return io.open(ROOT / "src" / "ultracua" / "locators.py", encoding="utf-8", newline="").read()


def test_every_arm_anchor_still_matches_the_resolver_exactly_once() -> None:
    """The staleness rule `prove_red` applies to a mutation, applied to a probe's arms.

    An anchor matching ZERO times means the probe silently measures the shipped resolver four times
    and prints four identical rows as if they were an adjudication. An anchor matching TWICE means it
    patches whichever site comes first, which is a different experiment than the one documented.
    """
    from benchmarks import fuzzy_bind_probe as P

    src = _locators_source()
    for arm, patches in P.ARMS.items():
        for find, _repl in patches:
            n = src.count(find)
            assert n == 1, (
                f"arm {arm!r}: its anchor matches {n} times in `locators.py`, not once. The resolver "
                f"moved and this probe now describes code that is not there — re-express the arm "
                f"rather than trusting its output.\nanchor: {find[:120]!r}")


def test_the_shipped_arm_patches_nothing() -> None:
    """`shipped` is the control that must be the tree itself. If it ever grew a patch, every number
    the probe prints would be relative to something that is not what ships."""
    from benchmarks import fuzzy_bind_probe as P

    assert P.ARMS["shipped"] == [], "the `shipped` arm must be the unmodified tree"
    assert set(P.ARMS) == {"shipped", "control", "agreement", "refuse_all"}, (
        f"the arm set changed to {sorted(P.ARMS)}; the printed table and the register entry name "
        f"exactly these four, so one added quietly is a table that no longer says what it measures")


def test_the_probe_drives_the_engines_own_resolver() -> None:
    """Reimplementing the thing under measurement is R4.134's lesson, which cost a whole survey once.
    Asserted structurally: `_bind` calls `L.resolve`, and never a local look-alike."""
    from benchmarks import fuzzy_bind_probe as P

    tree = ast.parse(inspect.getsource(P._bind))
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert any(c.endswith("resolve") for c in calls), (
        f"`_bind` does not call the engine's `resolve` (calls: {sorted(calls)}) — its verdicts would "
        f"describe the probe rather than the product")
    assert any(c.endswith("LocatorSpec") for c in calls), (
        "`_bind` no longer builds a real `LocatorSpec`, so its arms are not being handed the shape "
        "the resolver actually receives")


def test_the_contradiction_check_the_probe_targets_is_still_in_the_resolver() -> None:
    """The subject itself. If D2's clause is deleted from `src/`, three arms become no-ops and the
    probe reports a clean adjudication of nothing — so the clause is pinned here as well as by
    `tests/test_locators.py`'s behavioural pair.

    Read from the AST, never from source TEXT: this file's own prose contains the string, and a
    substring scan matching its own explanation is this repository's most-repeated scan defect.
    """
    from ultracua import locators as L

    fn = next((n for n in ast.walk(ast.parse(inspect.getsource(L)))
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "_resolve"), None)
    assert fn is not None, "`_resolve` is gone; the probe and both pins name a function that moved"

    # the guard is `label == "role+name~"` compared inside the Tier-1 loop
    compares = [n for n in ast.walk(fn)
                if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name)
                and n.left.id == "label"
                and any(isinstance(c, ast.Constant) and c.value == "role+name~" for c in n.comparators)]
    assert compares, (
        "no `label == \"role+name~\"` comparison survives in `_resolve`. D2's contradiction check is "
        "gone, so the fuzzy Tier-1 candidate binds a substring decoy outright again (R4.156).")
