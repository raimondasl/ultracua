"""The D3 probe's claims, held against the engine's own source (0.176.0).

`benchmarks/row_echo_probe.py` concludes *do not change `src/`*, and such a conclusion is worth
exactly what its reproducibility is worth. These cells pin the two facts its reasoning rests on, so
a probe describing a resolver the code no longer has fails HERE rather than answering confidently
and wrongly one release later -- the discipline `tests/test_gate_probe.py` already applies to
`gate_probe`'s branch.

Read via `inspect.getsource`, never by PATH: `prove_red` installs a mutant as a copy on
`PYTHONPATH`, so a path-reading cell parses pristine source and reports a survivor over a guard that
is perfectly fine (R4.75, and `prove_red`'s own docstring says so).
"""

from __future__ import annotations

import ast
import inspect

from ultracua import locators


def test_a_falsy_anchor_id_really_does_disable_the_row_guard() -> None:
    """MEASUREMENT 1's whole point. D3's prescription is *reject positional tokens*, whose effect at
    capture is `anchor_id=None` -- and that is a no-op precisely because `resolve` returns early on a
    falsy `anchor_id` rather than refusing. If that early return is ever replaced by something that
    treats "no identity" as its own state, the prescription stops being a no-op and D3 must be
    re-adjudicated instead of read out of the register."""
    # `resolve` is module-level, so its source needs no dedent. A first draft ran `cleandoc` over
    # it, which flattens the BODY's indentation too and raised IndentationError -- the cell failing
    # on its own reader rather than on its subject.
    tree = ast.parse(inspect.getsource(locators.resolve))

    guards = [n for n in ast.walk(tree)
              if isinstance(n, ast.If) and any(
                  isinstance(x, ast.Attribute) and x.attr == "anchor_id"
                  for x in ast.walk(n.test))]
    assert guards, (
        "`resolve` no longer branches on `spec.anchor_id`. D3's refutation rests on a falsy "
        "anchor_id meaning NO GUARD; re-derive it with `python -m benchmarks.row_echo_probe`.")

    # The branch must RETURN (i.e. skip the guard), not refuse. A `return None` would be a refusal
    # and would change the answer; a bare `return loc` is the pass-through the measurement assumes.
    returns_through = any(isinstance(st, ast.Return) and st.value is not None
                          for g in guards for st in ast.walk(g))
    assert returns_through, (
        "the anchor_id branch no longer passes the bind THROUGH. If a missing identity now refuses, "
        "D3's prescription is no longer a no-op and the register entry is stale.")


def test_the_css_path_still_stops_at_the_first_id() -> None:
    """MEASUREMENT 3's whole point: the guard is an ECHO on any row with an id *because* `cssPath`
    emits `#<id>` and stops there while `_rowCands` offers `id:<row id>` first. Both halves are
    asserted, because either one changing dissolves the echo and with it the third refutation."""
    css_js = locators._SPECOF_JS
    assert "if (e.id) { parts.unshift('#' + CSS.escape(e.id)); break; }" in css_js, (
        "`cssPath` no longer anchors on the first ancestor id. The echo that D3's third sensor was "
        "built on may be gone; re-run `python -m benchmarks.row_echo_probe --census`.")
    assert "out.push('id:' + c.id)" in locators._ROWID_JS, (
        "`_rowCands` no longer offers the row id first; the echo census is stale.")


def test_the_probe_names_a_scenario_the_corpus_still_has() -> None:
    """The probe's false-positive result is ABOUT `row-shared-action` by name. A corpus that renamed
    or dropped it would leave the probe printing a conclusion about nothing -- the stale-mutation
    failure mode, one instrument over."""
    from benchmarks import drift_fixtures, row_echo_probe

    names = {s["name"] for s in drift_fixtures.SCENARIOS}
    cited = {"row-shared-action", "row-positional"}
    assert cited <= names, f"the probe cites {sorted(cited - names)}, which the corpus no longer has"
    assert "row-shared-action" in inspect.getsource(row_echo_probe.census)
