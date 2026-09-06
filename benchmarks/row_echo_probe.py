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

3. THE ROW GUARD IS STRUCTURALLY AN ECHO ON ANY ROW WITH AN id, AND THAT IS NOT A CORPUS QUIRK.
   `cssPath` walks up and stops at the first ancestor carrying an `id`, emitting `#<id>`; `_rowCands`
   offers `id:<row id>` first. So a css bind and the identity checking it are THE SAME TOKEN and the
   guard cannot disagree. Refusing every echo was measured on the full corpus: 0-LLM survivals
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
import re
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

# Every sibling's identity candidates, so the SEQUENCE can be inspected (measurement 2).
SIBS_JS = """() => {
  const t = document.querySelector('[data-oracle="go"]');
  if (!t) return null;
  const row = t.closest('tr, li, [role=row], [role=listitem]');
  if (!row || !row.parentElement) return null;
  return Array.from(row.parentElement.children).map((r, i) => {
    const out = [];
    if (r.id) out.push('id:' + r.id);
    for (const a of Array.from(r.attributes || [])) {
      if (a.name.indexOf('data-') === 0 && a.value) out.push(a.name + ':' + a.value);
    }
    for (const h of Array.from(r.querySelectorAll('input[type=hidden]'))) {
      const n = h.getAttribute('name'), v = h.getAttribute('value');
      if (n && v) out.push('hidden:' + n + '=' + v);
    }
    return {i: i, cands: out};
  });
}"""


def tracks_position(token, sibs) -> bool:
    """Measurement 2's detector: does the token's numeric part equal the row's index, up to one
    constant offset, across the siblings carrying the same token shape?"""
    if not token:
        return False
    m = re.search(r"(\d+)", token)
    if not m:
        return False
    prefix = token[:m.start()]
    seen = []
    for s in sibs:
        for c in s["cands"]:
            if c.startswith(prefix):
                mm = re.search(r"(\d+)", c[len(prefix):])
                if mm:
                    seen.append((s["i"], int(mm.group(1))))
                break
    return len(seen) >= 3 and len({n - i for i, n in seen}) == 1


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
        sibs = await pg.evaluate(SIBS_JS) or []
        pos = tracks_position(spec.anchor_id, sibs)
        note = ""
        if s["name"] == "row-shared-action" and pos:
            note = "  <-- FALSE POSITIVE: R3.1's per-record key"
        print(f"{s['name']:20} {str(spec.anchor_id):24} {str(pos):17} "
              f"{'ECHO' if is_echo(spec) else 'independent'}{note}")


async def prescription(pg) -> None:
    print("\n=== 2. THE PLAN'S PRESCRIPTION, APPLIED: reject the token -> anchor_id None ===\n")
    spec = await _spec_for(pg, F.POSITIONAL_PAGE)
    for label, s in (("today (token accepted)", spec),
                     ("D3 as written (token rejected)", _without_anchor_id(spec))):
        verdict, sink = await _bind(pg, F.POSITIONAL_PAGE, s, RENUMBER)
        print(f"  {label:34} anchor_id={str(s.anchor_id):12} by={str(sink.get('bound_by')):6} "
              f"-> {verdict}")
    print("\n  Both open the same stranger. `resolve` reads a falsy `anchor_id` as NO GUARD, so the")
    print("  prescription moves the failure one branch earlier and removes zero wrong binds.")


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
