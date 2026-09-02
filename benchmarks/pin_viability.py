"""CAN a corpus read be pinned to a 0-LLM replay? Measured before any learn is bought. ($0.00.)

A read flow that sets `extract` and is not PINNED builds an extraction router on every replay --
`flows.py` says so in the branch itself -- so every corpus read pays one LLM call per replay and none
of them demonstrates the product's central claim. `pin.py` is the mechanism that would: for a SCALAR
answer that is exactly one element's text, it records a resilient LOCATOR at learn and re-reads
`inner_text()` on replay, with no model call at all.

R4.140's measurement supplied half the precondition for free -- 19 of 19 scored runs across four Odoo
reads returned a bare string. This probe measures the OTHER half, which is the one that can refuse:

  1. exactly ONE leaf-most element whose collapsed text equals the value  (`_PIN_JS`)
  2. that element carries an `id` or `data-testid`                        (`find_pin`)

Condition 2 is the sharp one and it is easy to miss: `find_pin` REFUSES a purely positional CSS
anchor, because after a layout shift it would resolve to a different element and return a wrong
value. So an app whose data cells carry no stable identity cannot be pinned however scalar its
answers are, and buying learns to discover that would be paying to read a precondition.

USES THE PRODUCT'S OWN `_PIN_JS` AND `find_pin`, never a re-derivation: `web_survey` copied `nameOf`
into itself and reported Odoo at 36% unnamed against the product's own 19%.

WHAT IT CANNOT SEE, stated because the number would otherwise be over-read: a pin is taken on the
page the flow ENDS on, and this probe looks at each scenario's START page. For a scenario whose
answer is only revealed by its recipe, `on_start_page=False` means "not decidable here", NOT "cannot
be pinned". The `identity_density` census is the substrate-level answer that does not depend on which
page is open.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ultracua.browser import BrowserSession
from ultracua.pin import _PIN_JS, find_pin

from . import corpus
from . import substrates as S

ROOT = Path(__file__).resolve().parents[1]

# How many elements carry a stable identity at all -- the substrate-level precondition, independent
# of any one answer. Counts the LEAF-most text holders, which is the population `_PIN_JS` picks from.
_DENSITY_JS = r"""
() => {
  let leaves = 0, withId = 0, withTestid = 0;
  for (const el of document.querySelectorAll('body *')) {
    // `body` ITSELF is excluded, and it is not a nit. It holds text and its children's text
    // differs from its own, so it passes the leaf test and adds exactly 1 to every page's
    // denominator -- a silent off-by-one in the fraction this probe exists to report. `_PIN_JS`
    // includes it deliberately (it needs an exact text match, which body never has), so the two
    // populations differ ON PURPOSE and this one is the census, not the matcher.
    const t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!t) continue;
    let deeper = false;
    for (const c of el.children) {
      const ct = (c.innerText || c.textContent || '').replace(/\s+/g, ' ').trim();
      if (ct === t) { deeper = true; break; }
    }
    if (deeper) continue;
    leaves += 1;
    if (el.id) withId += 1;
    if (el.getAttribute('data-testid')) withTestid += 1;
  }
  return { leaves, withId, withTestid };
}
"""


async def _auth_state(substrate: str, work: Path) -> str:
    """A logged-in storage_state via the product's own verb (the shape `web_survey` established)."""
    import os

    from ultracua.flows import refresh_auth

    from .scored_run import LOGIN, PASS_ENV, USER_ENV, spec_for

    sub = {"gitea": S.Gitea, "odoo": S.Odoo}[substrate]()
    sub.await_ready()
    cfg = LOGIN[substrate]
    os.environ[USER_ENV], os.environ[PASS_ENV] = cfg["user"], cfg["password"]
    state = str(work / f"auth-{substrate}.json")
    await refresh_auth(spec_for(next(iter(corpus.for_substrate(substrate))), sub.url, state), headless=True)
    return state


async def probe(substrate: str) -> list[dict]:
    work = ROOT / ".scratch" / "pin_viability"
    work.mkdir(parents=True, exist_ok=True)
    sub = {"gitea": S.Gitea, "odoo": S.Odoo}[substrate]()
    sub.await_ready()
    state = await _auth_state(substrate, work)

    rows = []
    async with BrowserSession(headless=True, storage_state=state) as sess:
        for entry in corpus.for_substrate(substrate):
            sc = entry.scenario
            if entry.truth.mutating:
                continue  # a write's answer is not extracted, so there is nothing to pin
            answer = str(entry.expected_answer(sub))
            # A HARD navigation between every scenario, and it is not defensive tidiness.
            # Odoo routes on the HASH (`/web#action=...`), so going from one scenario's url to the
            # next inside one session is a SAME-DOCUMENT navigation: `goto` resolves at once, the
            # SPA re-renders afterwards, and `await_settled` can find the PREVIOUS view already
            # quiet. Measured: three Odoo rows reported 10-22 leaf text holders where a rendered
            # list has 203 -- the probe was reading the page it had just left. `about:blank` first
            # forces a fresh document, so every row is measured on its own view.
            await sess.page.goto("about:blank")
            await sess.page.goto(sub.url + sc.url_path, wait_until="domcontentloaded")
            await sess.await_settled()   # R4.115/R4.120: never read a client-rendered page unsettled

            raw = await sess.page.evaluate(_PIN_JS, answer)
            pin = await find_pin(sess.page, answer)
            dens = await sess.page.evaluate(_DENSITY_JS)
            row = {
                "scenario": sc.name, "substrate": substrate, "answer": answer,
                "on_start_page": raw is not None,
                "unique_leaf": raw is not None,
                "has_identity": bool(raw and (raw.get("elem_id") or raw.get("testid"))),
                "pinnable_here": pin is not None,
                "tag": (raw or {}).get("tag"),
                "elem_id": (raw or {}).get("elem_id"),
                "testid": (raw or {}).get("testid"),
                "identity_density": dens,
            }
            rows.append(row)
            print(f"  {sc.name:24} on_page={row['on_start_page']!s:5} identity={row['has_identity']!s:5} "
                  f"PINNABLE={row['pinnable_here']!s:5} leaves={dens['leaves']:4} "
                  f"with_id={dens['withId']:3} with_testid={dens['withTestid']:3}", flush=True)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--substrate", action="append", choices=sorted(corpus.CORPORA),
                    help="repeatable; default both")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    subs = a.substrate or sorted(corpus.CORPORA)

    rows = []
    for s in subs:
        print(f"\n=== {s} ===")
        rows += asyncio.run(probe(s))

    print("\n=== summary ===")
    for s in subs:
        mine = [r for r in rows if r["substrate"] == s]
        d = [r["identity_density"] for r in mine]
        leaves = sum(x["leaves"] for x in d) or 1
        print(f"  {s:8} {sum(r['pinnable_here'] for r in mine)}/{len(mine)} pinnable on their start page; "
              f"identity density {sum(x['withId'] for x in d)}/{leaves} id, "
              f"{sum(x['withTestid'] for x in d)}/{leaves} testid")
    if a.out:
        Path(a.out).write_text(json.dumps({"rows": rows}, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
