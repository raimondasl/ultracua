"""The replay-side readiness sensor: retry what is unpainted, never what was refused.

THE SHAPE OF THE PROBLEM. `resolve() -> None` means at least five things, and only ONE of them is
"the page has not painted yet". The other four are deliberate safety refusals: ambiguity under
`unique=True`, the Tier-2 cross-check conflict, an identity contradiction, and the row-containment
guard. R4.115 MEASURED what a retry keyed on the return value does -- with two per-row `Cancel` links
and the recorded row hidden at t=400ms it bound `/cancel/30` where `/cancel/3` was recorded, via a
Tier-1 confident candidate nothing cross-checks. That is D5's overloaded-`None` one level up from
`anchor_id`, and R3.7 has defeated two attempts at the same shape.

SO THE SENSOR IS NOT THE RETURN VALUE. `resolve` now reports `sink["saw_candidates"]` from its own
internals: FALSE means no candidate matched anything at all, TRUE means the page answered and the
resolver refused. Only the first is retried.

These cells drive the REAL `resolve` against real pages. The one that matters is
`test_the_wrong_record_bind_is_still_refused` -- it rebuilds R4.115's measured counterexample and
requires the answer to still be None.
"""

from __future__ import annotations

import inspect

import pytest

from ultracua import flow as flow_mod
from ultracua import locators as L
from ultracua.browser import BrowserSession

# Two per-row controls sharing a name, recorded against row #3, whose row is HIDDEN at 400ms -- the
# ordinary SPA client-side delete that `locators.resolve`'s docstring records as producing a
# wrong-row write. The css is row-agnostic so Tier 2 cannot bind it either.
_TWO_ROWS = """
<body><table><tbody>
  <tr id="r3"><td>Acme Corp #3</td><td><a href="/cancel/3">Cancel</a></td></tr>
  <tr id="r30"><td>Beta LLC #30</td><td><a href="/cancel/30">Cancel</a></td></tr>
</tbody></table>
<script>setTimeout(function(){document.getElementById('r3').style.display='none';},400);</script>
</body>
"""
_ROW_SPEC = dict(role="link", name="Cancel", tag="a", text="Cancel", css="tbody > tr > td > a",
                 anchor="Acme Corp #3 Cancel", anchor_source="row", anchor_id=None)


# ------------------------------------------------------- the sensor reports what it saw


@pytest.mark.asyncio
async def test_nothing_matched_reports_saw_candidates_false() -> None:
    """The unpainted case: the spec describes an element no candidate finds."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content("<body><h1>empty</h1></body>")
        sink: dict = {}
        assert await L.resolve(s.page, L.LocatorSpec(
            role="link", name="Cancel", tag="a", text="Cancel", css="a.gone"),
            unique=True, sink=sink) is None
        assert sink["saw_candidates"] is False
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_an_ambiguous_page_reports_saw_candidates_true() -> None:
    """THE REFUSAL CASE. Two matching controls, `unique=True`: the page answered and `resolve`
    declined to guess. A caller must not wait this out."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content(_TWO_ROWS)
        sink: dict = {}
        assert await L.resolve(s.page, L.LocatorSpec(**_ROW_SPEC), unique=True, sink=sink) is None
        assert sink["saw_candidates"] is True
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_the_row_guard_refusal_also_reports_saw_candidates_true() -> None:
    """THE ONE THAT WOULD HAVE LEAKED. The containment guard lives in `resolve`, ABOVE `_resolve`,
    and refuses an element that bound uniquely in the WRONG record -- so it never reaches the branch
    that sets this flag. Unset would read as falsy and make it the single refusal a retry could walk
    through."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content(
            "<body><table><tbody>"
            "<tr data-id='r30'><td>Beta LLC #30</td><td><a href='/cancel/30'>Cancel</a></td></tr>"
            "</tbody></table></body>")
        sink: dict = {}
        got = await L.resolve(s.page, L.LocatorSpec(
            role="link", name="Cancel", tag="a", text="Cancel", css="tbody > tr > td > a",
            anchor="Acme Corp #3 Cancel", anchor_source="row", anchor_id="r3"),
            unique=True, sink=sink)
        assert got is None and sink.get("row_mismatch")
        assert sink["saw_candidates"] is True, (
            "the row guard refused an element it FOUND; a retry here is the wrong-record bind")
    finally:
        await s.close()


# ------------------------------------------------------------- what the retry does


class _Tr:
    def __init__(self) -> None:
        self.meta: dict = {}


@pytest.mark.asyncio
async def test_the_wrong_record_bind_is_still_refused() -> None:
    """R4.115's MEASURED COUNTEREXAMPLE, rebuilt. A retry keyed on the return value bound
    `/cancel/30` at 0.41 s here. Keyed on `saw_candidates` it must stay None -- and must not wait,
    because waiting is precisely what let the competing row disappear."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content(_TWO_ROWS)
        spec = L.LocatorSpec(**_ROW_SPEC)
        tr = _Tr()
        sink: dict = {}
        loc = await L.resolve(s.page, spec, unique=True, sink=sink)
        out = await flow_mod._retry_if_unpainted(s, s.page, spec, tr, loc, sink=sink)
        assert out is None, "the readiness retry walked through an ambiguity refusal"
        assert tr.meta["readiness_retry"] == "refused"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_a_page_still_painting_is_waited_out_and_bound() -> None:
    """THE CASE THE MECHANISM IS FOR, in the shape it was MEASURED in. Odoo is actively rendering
    when the replay first asks -- 5 elements climbing to 80-255 over 470-860 ms -- so the page is
    NOT mutation-quiet, the settle waits through the render, and the retry finds the element.

    The fixture mutates CONTINUOUSLY and then produces the target, because that is what a booting
    SPA does. An earlier draft of this cell sat inert for 350 ms and then injected in one go, which
    is not that shape: mutation-quiet correctly fired during the inert period, and the retry
    correctly found nothing.

    THAT IS A REAL LIMIT AND IT IS WORTH STATING. Mutation-quiet cannot distinguish "finished
    rendering" from "has not started". A page that is idle when asked and paints later gets one
    retry at the settle and no more -- the same loud failure as today, never a wrong bind."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content(
            "<body><div id=x></div><script>let n = 0;"
            "const h = setInterval(function () {"
            "  const d = document.getElementById('x');"
            "  if (++n < 12) { d.appendChild(document.createElement('span')); return; }"
            "  clearInterval(h);"
            "  d.innerHTML = '<a href=\\'/go\\' id=late>Continue</a>';"
            "}, 40);</script></body>")
        spec = L.LocatorSpec(role="link", name="Continue", tag="a", text="Continue", css="a#late")
        tr = _Tr()
        sink: dict = {}
        loc = await L.resolve(s.page, spec, unique=True, sink=sink)
        assert loc is None and sink["saw_candidates"] is False
        out = await flow_mod._retry_if_unpainted(s, s.page, spec, tr, loc, sink=sink)
        assert out is not None, "a page that was still painting was not waited out"
        # `:bound:<looks>` since 0.165.0 -- the verdict carries HOW MANY looks it took, because one
        # look is not enough for a render whose stages are gated on separate network fetches
        # (R4.144). Matched as a segment rather than a suffix so the count can grow without this
        # cell going red for a reason that is not about the property it asserts.
        assert ":bound:" in tr.meta["readiness_retry"], tr.meta["readiness_retry"]
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_a_genuinely_absent_element_still_fails_after_the_wait() -> None:
    """The retry is bounded and honest: nothing ever appears, so the answer is still None -- just a
    little later. Loud, not silent.

    SINCE 0.165.0 THERE ARE TWO WAYS TO GIVE UP AND BOTH ARE THIS PROPERTY. `still-none` means the
    budget ran out; `stalled` means the page produced no mutation for several consecutive looks, so
    it has STOPPED rather than paused -- which is what keeps a genuinely-absent element from being
    waited on for the full `settle_cap_ms` (measured: 36 drift_bench rows at 2251 ms each, 81 s).
    A static page reaches the second. What this cell is about is that neither ever returns an
    element, so it asserts the SET rather than one spelling.
    """
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content("<body><h1>nothing here</h1></body>")
        spec = L.LocatorSpec(role="link", name="Gone", tag="a", text="Gone", css="a.gone")
        tr = _Tr()
        sink: dict = {}
        loc = await L.resolve(s.page, spec, unique=True, sink=sink)
        out = await flow_mod._retry_if_unpainted(s, s.page, spec, tr, loc, sink=sink)
        assert out is None
        verdict = tr.meta["readiness_retry"]
        assert any(k in verdict for k in (":still-none:", ":stalled:", "already-quiet:skipped")), (
            f"a genuinely absent element produced {verdict!r}, which is not one of the ways this "
            f"retry is allowed to give up -- the only thing it must never do is return an element")
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_a_successful_resolve_never_waits() -> None:
    """THE HAPPY PATH IS UNTOUCHED, which is what keeps the 0-LLM speed claim honest on the
    substrates that never needed a wait."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content("<body><a href='/go' id=here>Continue</a></body>")
        spec = L.LocatorSpec(role="link", name="Continue", tag="a", text="Continue", css="a#here")
        tr = _Tr()
        sink: dict = {}
        loc = await L.resolve(s.page, spec, unique=True, sink=sink)
        assert loc is not None
        assert await flow_mod._retry_if_unpainted(s, s.page, spec, tr, loc, sink=sink) is loc
        assert "readiness_retry" not in tr.meta, "a bound resolve recorded a retry it never made"
    finally:
        await s.close()


# ------------------------------------------------------------------- structural


def test_both_replay_resolve_sites_are_covered() -> None:
    """The step's resolve AND the mutation gate's. R4.117 measured that the composition needs the
    gate too -- a wire-marked navigate gated on a whole-page fingerprint the race makes
    unreproducible is one of the two blockers."""
    src = inspect.getsource(flow_mod)
    assert src.count("_retry_if_unpainted(") == 3, (
        "expected the definition plus exactly two call sites (step resolve, gate resolve)")
    assert 'tag="gate_"' in src, "the gate's retry must record under its own key, not overwrite"


def test_the_retry_is_keyed_on_the_sensor_not_the_return_value() -> None:
    """The whole finding in one assertion. Keyed on `loc is None` alone, this is R4.115's refuted
    remedy; keyed on `saw_candidates`, the four safety refusals keep failing loud."""
    src = inspect.getsource(flow_mod._retry_if_unpainted)
    assert 'if s.get("saw_candidates"):' in src
    assert src.index('if s.get("saw_candidates"):') < src.index("await session.await_settled()"), (
        "the refusal check must come BEFORE any wait -- waiting is what let the competing candidate "
        "disappear in the measured wrong-record bind")
