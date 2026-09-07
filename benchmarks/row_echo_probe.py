"""D3's adjudication, re-derivable in ~20 seconds (0.176.0).

    python -m benchmarks.row_echo_probe            # everything below
    python -m benchmarks.row_echo_probe --census   # just the identity census

D3 asks the resolver to "reject purely positional row-identity tokens (`data-index`, `id="row-N"`)".
A conclusion of the form *do not change `src/`* is worth exactly what its reproducibility is worth
(R4.111's rule, `gate_probe` one instrument over), so the four measurements that refuse it live here
rather than only in a register entry. No LLM, no substrate, no key.

WHAT THIS ESTABLISHES, in the order the measurements were taken:

1. THE PRESCRIPTION IS A NO-OP. Rejecting the token sets `anchor_id` to None, and `resolve` reads a
   falsy `anchor_id` as NO GUARD (`locators.py`, the `not (spec.anchor_source == "row" and
   spec.anchor_id)` early return). The identical wrong bind then happens one branch earlier. It
   removes ZERO wrong binds -- the harm moves from *the guard agreed wrongly* to *the guard never
   ran*, which is the overloaded `anchor_id=None` sensor D5 blocks further attempts on.

2. A VALUE-TRACKS-POSITION DETECTOR IS REFUTED BY THE CORPUS'S OWN CONTROL. `row-shared-action`'s
   `hidden:widget=3` is the per-record key R3.1's discrimination rule was built for, and it scores
   positional=True: on an unmutated page a sequential record key and a slot number are the same
   string. The difference appears only AFTER a delete -- a real key keeps its value, a slot number
   does not -- which no single capture can see.

3. THE ROW GUARD IS AN ECHO WHEREVER A css BIND'S PATH IS ANCHORED ON THE ROW'S OWN id.
   `cssPath` starts at the TARGET and stops at the first element carrying an `id`, emitting `#<id>`;
   `_rowCands` offers `id:<row id>` first. Where both land on the row, a css bind and the identity
   checking it are THE SAME TOKEN and the guard cannot disagree.
   **THE FIRST WRITE-UP SAID "ANY ROW WITH AN id" AND THAT IS FALSE, measured two ways.** A target
   carrying its OWN id gives `css='#details-3'` and no echo; and three wrapper divs push the row id
   out of the five-element window (the corpus's `row-nested-action` is already `tr > td > ul > li >
   a`, one wrapper short). A third case falsifies the "cannot disagree" half outright: on a
   self-id page, renumbering the rows but not the controls REFUSES, bound by `elem_id`. The echo is
   narrower than claimed -- which makes the case for refusing it weaker, not stronger. Refusing every echo was measured on the full corpus: 0-LLM survivals
   84 -> 79 and `heal_invalidates_approval` FAILS, because two of the extra refusals re-ground to a
   byte-identical recipe (R4.35) and would be refused again on every future run.

4. CORROBORATING THE ECHO WITH THE ROW'S TEXT PRICES AT 84 -> 83 WITH EVERY INVARIANT HOLDING, AND
   IS STILL REFUSED -- by a guarantee the corpus does not contain.
   `test_row_identity_binding.py::test_an_edited_row_still_binds` says row TEXT is deliberately not
   the identity, because a price or a status changing does not make a row a different record. That
   cell went RED: an edited `#order-3` binds by css, the guard is an echo, the text moved, and the
   rule refuses a row that is the same record. Its docstring had already recorded that a text-keyed
   check was measured to cost 4 rows of 0-LLM survival. **The corpus understated the cost and the
   standing suite did not.**

5. AND THE FOURTH SENSOR DIES ON THE SAME FACT. "Corroborate with a DIFFERENT discriminating
   candidate" works on `#order-3` (whose `href:/cancel/3` is independent) and fails on the row D3 is
   about: `row-positional`'s other candidate is `data-index:3`, which renumbers in lockstep with the
   id it would be corroborating.

So D3's premise is refuted by a sentence already standing in `_ROWID_JS`: *nothing observable in a
single capture separates a positional token from a real key*. Four sensors, four ways of learning it.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright                       # noqa: E402

from ultracua import locators as L                                      # noqa: E402
from benchmarks import drift_fixtures as F                              # noqa: E402

# The renumber D3 is about: delete a row above the target, renumber the survivors as a re-render
# does, and drop the target's own name so the positional path is what decides.
RENUMBER = ("const t = document.querySelector('[data-oracle=\"go\"]');"
            "const tr = t.closest('tr'); const tb = tr.parentElement;"
            "tb.children[0].remove();"
            "Array.from(tb.children).forEach(function (r, n) {"
            "  r.id = 'row-' + (n + 1); r.setAttribute('data-index', String(n + 1)); });"
            "t.removeAttribute('aria-label');")

# THE TOKEN-SHAPE QUERY. Asked of the DOCUMENT, never of "the row's siblings", and that is a
# correction rather than a preference. A first draft walked to the row with
# `t.closest('tr, li, [role=row], [role=listitem]')` and enumerated ITS siblings -- a second
# implementation of a walk the engine already owns, and the two disagreed exactly where the corpus
# has a fixture for the disagreement. On `row-nested-icon` the control sits in `tr > td > ul > li`,
# so the probe stopped at the `<li>` and found ONE sibling, while `anchorOf` -- which produced the
# `id:widget-row-3` being classified -- climbs past the icon-only `<li>` to the `<tr>`. The
# `len(seen) >= 3` floor then reported `tracks position? False` for a token that tracks its position
# perfectly across all 12 rows. **The instrument written to adjudicate D3 reproduced the two-walk
# divergence R3.7's fixture exists to expose** (`_nested_icon_rows`' docstring says so in as many
# words), and printed a wrong cell in the shipped census for it.
#
# So the question is asked directly of the token instead: the elements bearing this token SHAPE, in
# document order, against the numbers they carry. No walk, nothing to diverge from.
SHAPE_JS = r"""(spec) => {
  const [kind, value] = spec;
  const m = /^(.*?)(\d+)$/.exec(value);
  if (!m) return null;
  const prefix = m[1];
  // `hidden:name=value` is queried by NAME, not by attribute presence. Dropping this branch is how
  // the first correction quietly un-refuted measurement 2: `row-shared-action`'s `hidden:widget=3`
  // stopped being flagged, and the false positive the whole refutation rests on disappeared from the
  // census. A detector that cannot see the token it is refuted BY is not the detector under test.
  let els;
  if (kind === 'hidden') {
    const eq = prefix.indexOf('=');
    const name = prefix.slice(0, eq);
    els = Array.from(document.querySelectorAll('input[type=hidden][name="' + name + '"]'))
            .map((e) => e.getAttribute('value'));
  } else {
    const sel = kind === 'id' ? '[id]' : '[' + kind + ']';
    els = Array.from(document.querySelectorAll(sel))
            .map((e) => (kind === 'id' ? e.id : e.getAttribute(kind)))
            .filter((v) => v && v.indexOf(prefix) === 0)
            .map((v) => v.slice(prefix.length));
  }
  const out = [];
  for (const v of els) {
    const mm = /^(\d+)$/.exec(v || '');
    if (mm) out.push(parseInt(mm[1], 10));
  }
  return out;
}"""


async def tracks_position(pg, anchor_id) -> bool:
    """Measurement 2's detector: do the elements carrying this token SHAPE appear in document order
    matching the numbers they carry, up to one constant offset?"""
    kind, _, value = (anchor_id or "").partition(":")
    if kind == "href":
        return False        # a path carries no positional shape this detector can read
    nums = await pg.evaluate(SHAPE_JS, [kind, value])
    if not nums or len(nums) < 3:
        return False
    return len({n - i for i, n in enumerate(nums)}) == 1


def is_echo(spec) -> bool:
    """Measurement 3: does the css path SELECT ON the very attribute the identity is made of?

    STRUCTURAL, not substring. A first draft reduced `hidden:widget=3` to "3", found it inside
    `tr:nth-of-type(3)`, and reported the one fixture built to prove the guard WORKS as circular.
    A guard-disarming rule justified by a coincidence of decimal digits is exactly the false positive
    a blast-radius measurement exists to find, and it found it in the probe rather than in `src/`.
    """
    kind, _, body = (spec.anchor_id or "").partition(":")
    css = spec.css or ""
    if kind == "id":
        return f"#{body}" in css
    if kind.startswith("data-"):
        return f"[{kind}=" in css
    return False


def _without_anchor_id(spec):
    c = copy.copy(spec)
    object.__setattr__(c, "anchor_id", None)
    return c


async def _spec_for(pg, html):
    await pg.set_content(html)
    ok = await pg.evaluate("""() => { const t = document.querySelector('[data-oracle="go"]');
                                      if (!t) return 0; t.setAttribute('data-ultracua-ref','e1');
                                      return 1; }""")
    return await L.describe(pg, "e1") if ok else None


async def _bind(pg, html, spec, js=None):
    await pg.set_content(html)
    if js:
        await pg.evaluate(js)
    sink: dict = {}
    loc = await L.resolve(pg, spec, unique=True, sink=sink)
    if loc is None:
        return "refused", sink
    o = await loc.get_attribute("data-oracle")
    return ("correct" if o == "go" else f"WRONG({o})"), sink


async def census(pg) -> None:
    print("\n=== 1. WHAT EACH ROW SCENARIO RECORDS, AND HOW TWO SENSORS CLASSIFY IT ===\n")
    print(f"{'scenario':20} {'anchor_id':24} {'tracks position?':17} echo?")
    for s in F.SCENARIOS:
        html = F._PAGES.get(s["path"])
        if not html:
            continue
        spec = await _spec_for(pg, html)
        if spec is None or spec.anchor_source != "row":
            continue
        pos = await tracks_position(pg, spec.anchor_id)
        note = ""
        if s["name"] == "row-shared-action" and pos:
            note = "  <-- FALSE POSITIVE: R3.1's per-record key"
        print(f"{s['name']:20} {str(spec.anchor_id):24} {str(pos):17} "
              f"{'ECHO' if is_echo(spec) else 'independent'}{note}")


async def prescription(pg) -> None:
    print("\n=== 2. THE PLAN'S PRESCRIPTION, APPLIED: reject the token -> anchor_id None ===\n")
    spec = await _spec_for(pg, F.POSITIONAL_PAGE)
    seen = []
    for label, s in (("today (token accepted)", spec),
                     ("D3 as written (token rejected)", _without_anchor_id(spec))):
        verdict, sink = await _bind(pg, F.POSITIONAL_PAGE, s, RENUMBER)
        seen.append(verdict)
        print(f"  {label:34} anchor_id={str(s.anchor_id):12} by={str(sink.get('bound_by')):6} "
              f"-> {verdict}")

    # THE VERDICT IS DERIVED FROM WHAT WAS JUST MEASURED, never printed as a literal. The first
    # draft ended with an unconditional "Both open the same stranger ... removes zero wrong binds",
    # and under a mutated `resolve` the adversarial pass got that sentence printed four lines under
    # a table reading `-> refused`. An instrument whose conclusion cannot disagree with its own data
    # is the echo this slice is about, wearing a print statement.
    today, rejected = seen
    if today == rejected == "WRONG(row4)":
        print("\n  Both open the same stranger. `resolve` reads a falsy `anchor_id` as NO GUARD, so")
        print("  the prescription moves the failure one branch earlier and removes zero wrong binds.")
    elif today.startswith("WRONG") and rejected != today:
        print(f"\n  ** THE PRESCRIPTION NOW CHANGES THE OUTCOME ({today} -> {rejected}). R4.152 said")
        print("  it could not. `resolve`'s falsy-anchor_id branch has moved: RE-ADJUDICATE D3.")
    else:
        print(f"\n  ** NEITHER ARM REPRODUCES THE WRONG BIND (today={today}, rejected={rejected}).")
        print("  The corpus row or the resolver has changed; R4.152's measurement 1 is stale.")


async def second_source(pg) -> None:
    print("\n=== 3. THE ROW'S OWN TEXT: the only thing that separates the two cases ===\n")
    spec = await _spec_for(pg, F.POSITIONAL_PAGE)
    safe = ("const t = document.querySelector('[data-oracle=\"go\"]');"
            "t.removeAttribute('aria-label');")
    for label, js in (("DANGEROUS (renumbered)", RENUMBER), ("SAFE (same drift, no renumber)", safe)):
        await pg.set_content(F.POSITIONAL_PAGE)
        await pg.evaluate(js)
        sink: dict = {}
        loc = await L.resolve(pg, spec, unique=True, sink=sink)
        txt = None
        if loc is not None:
            txt = await loc.evaluate(
                "(e) => { const r = e.closest('tr, li, [role=row]');"
                "  return r ? (r.innerText||'').replace(/\\s+/g,' ').trim().slice(0,60) : null; }")
        print(f"  {label:32} bound row text = {txt!r}")
        print(f"  {'':32} recorded anchor = {spec.anchor!r}  -> "
              f"{'agrees' if txt == spec.anchor else 'DISAGREES'}")
    print("\n  It discriminates -- and it is still refused, by `test_an_edited_row_still_binds`:")
    print("  an edited row (a price, a status) is the SAME record and this would refuse it.")
    print("  Measured corpus cost 84 -> 83 survivals; the standing suite is what priced it properly.")


async def main() -> None:
    ap = argparse.ArgumentParser(prog="benchmarks.row_echo_probe")
    ap.add_argument("--census", action="store_true", help="the identity census only")
    args = ap.parse_args()
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        try:
            await census(pg)
            if not args.census:
                await prescription(pg)
                await second_source(pg)
        finally:
            await b.close()


if __name__ == "__main__":
    asyncio.run(main())
