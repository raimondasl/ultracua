"""The D3 probe's claims, held against the engine's own behaviour (0.176.0).

`benchmarks/row_echo_probe.py` concludes *do not change `src/`*, and such a conclusion is worth
exactly what its reproducibility is worth. These cells pin the facts its reasoning rests on, so a
probe describing a resolver the code no longer has fails HERE rather than answering confidently and
wrongly one release later -- the discipline `tests/test_gate_probe.py` already applies to
`gate_probe`'s branch.

TWO OF THESE CELLS EXIST IN THEIR PRESENT FORM BECAUSE THE FIRST DRAFT COULD NOT FAIL, and the
adversarial pass proved it by mutation rather than by reading. Both defects are worth naming:

  * the early-return pin collected EVERY `If` whose test mentions `anchor_id` -- two of them -- and
    then asked `any(...)`, so the LATER branch's honest `return loc` satisfied it however the subject
    branch was mutated. It also tested `st.value is not None`, which is an AST test: `return None`
    parses as `Return(value=Constant(None))` and reads as a pass-through. The mutation its own
    docstring named as the thing to catch left it green.
  * the `_rowCands` pin asserted that a LINE EXISTS, while its message claimed an ORDER. Moving the
    id push below the href push -- the change that would actually dissolve the echo -- would not have
    failed it. It is behavioural now: build a row carrying both, and ask which one capture chose.
"""

from __future__ import annotations

import ast
import inspect

from playwright.async_api import async_playwright

from ultracua import locators
from ultracua.locators import describe


def _early_return_guard() -> ast.If:
    """THE branch D3's first refutation rests on: `resolve`'s pass-through when no row identity was
    recorded. Selected by BOTH names in its test, because `anchor_id` alone matches two branches and
    an `any()` over both is satisfied by the wrong one."""
    # `resolve` is module-level, so its source needs no dedent. A first draft ran `cleandoc` over it,
    # which flattens the BODY's indentation too and raised IndentationError -- the cell failing on
    # its own reader rather than on its subject.
    tree = ast.parse(inspect.getsource(locators.resolve))
    named = [n for n in ast.walk(tree)
             if isinstance(n, ast.If)
             and {x.attr for x in ast.walk(n.test) if isinstance(x, ast.Attribute)}
             >= {"anchor_id", "anchor_source"}]
    assert len(named) == 1, (
        f"expected exactly one branch testing both `anchor_source` and `anchor_id`, found "
        f"{len(named)}. `resolve`'s shape moved; re-derive D3 with "
        f"`python -m benchmarks.row_echo_probe` rather than reading the register.")
    return named[0]


def test_a_falsy_anchor_id_really_does_disable_the_row_guard() -> None:
    """MEASUREMENT 1's whole point. D3's prescription is *reject positional tokens*, whose effect at
    capture is `anchor_id=None` -- and that is a no-op precisely because `resolve` hands the bind
    THROUGH on a falsy `anchor_id` rather than refusing. If that ever becomes a refusal, the
    prescription stops being a no-op and D3 must be re-adjudicated."""
    body = _early_return_guard().body
    assert len(body) == 1 and isinstance(body[0], ast.Return), (
        f"the branch no longer ends in a single return: {ast.dump(body[0])[:120]}")

    returned = body[0].value
    assert isinstance(returned, ast.Name), (
        f"the anchor_id branch returns {ast.unparse(returned) if returned else 'nothing'!r} rather "
        f"than the bound locator. A missing identity now REFUSES, so D3's prescription is no longer "
        f"a no-op and R4.152 is stale.")
    assert returned.id == "loc", f"returns {returned.id!r}, not the bind"


def test_the_css_path_still_anchors_on_an_ancestor_id() -> None:
    """MEASUREMENT 3's first half: `cssPath` emits `#<id>` for the first ancestor carrying one and
    stops. Asserted on the STRING because it is JS, and paired below with a behavioural cell for the
    half that a source scan genuinely cannot see."""
    assert "if (e.id) { parts.unshift('#' + CSS.escape(e.id)); break; }" in locators._SPECOF_JS, (
        "`cssPath` no longer anchors on the first ancestor id; the echo census is stale. Re-run "
        "`python -m benchmarks.row_echo_probe --census`.")


async def test_capture_prefers_the_row_id_over_its_href() -> None:
    """MEASUREMENT 3's second half, and it is BEHAVIOURAL because the claim is an ORDER.

    The first draft asserted `"out.push('id:' + c.id)" in _ROWID_JS` under a message about the id
    coming FIRST -- a presence check standing in for a precedence one. Moving that push below the
    href push is exactly the change that dissolves the echo (the identity would then be
    `href:/details/3`, which `cssPath` never selects on), and the old cell would not have noticed.
    So the row below carries BOTH, and capture is asked which it chose.
    """
    html = """<!doctype html><html><body><table><tbody>
      <tr id="row-1"><td>One</td><td><a href="/details/1">Open</a></td></tr>
      <tr id="row-2"><td>Two</td><td><a href="/details/2">Open</a></td></tr>
      <tr id="row-3"><td>Three</td>
        <td><a href="/details/3" data-ultracua-ref="e1">Open</a></td></tr>
    </tbody></table></body></html>"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context()).new_page()
            await page.set_content(html)
            spec = await describe(page, "e1")
        finally:
            await browser.close()
    assert spec.anchor_id == "id:row-3", (
        f"capture chose {spec.anchor_id!r}. D3's echo census assumes the ROW ID wins over an equally "
        f"discriminating href; if that precedence moved, the identity is no longer the token "
        f"`cssPath` selects on and measurement 3 must be re-derived.")


def test_the_probe_names_scenarios_the_corpus_still_has() -> None:
    """The probe's refutation is ABOUT named rows. A corpus that renamed or dropped one would leave
    it printing a conclusion about nothing -- the stale-mutation failure mode, one instrument over."""
    from benchmarks import drift_fixtures, row_echo_probe

    names = {s["name"] for s in drift_fixtures.SCENARIOS}
    cited = {"row-shared-action", "row-positional"}
    assert cited <= names, f"the probe cites {sorted(cited - names)}, which the corpus no longer has"
    assert "row-shared-action" in inspect.getsource(row_echo_probe.census)
