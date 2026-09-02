"""WHY did a replay step fail? Instrument the resolver and photograph the page at the failure.

    uv run --no-sync python -m benchmarks.replay_step_probe --scenario odoo-create-lead

**THIS SPENDS MONEY** -- one learn plus one replay per scenario, ~$0.08-0.15 on Odoo.

A `DriftError` says "locator unresolved or ambiguous (drift)", and those are two different worlds
that the return value structurally cannot separate -- which is why `resolve` carries
`sink["saw_candidates"]` (R4.115). FALSE means nothing in the page answered the recorded spec;
TRUE means the page answered and the resolver DECLINED. This probe reads that sensor for every
resolve of a real replay and, at the first failure, records what the page actually held.

IT EXISTS BECAUSE READING THE MESSAGE WAS WRONG FOR FIVE VERSIONS. R4.111 recorded the Odoo write
rows as `resolve(..., unique=True)` failing to bind uniquely on a generated DOM -- an AMBIGUITY
story. Measured with this: `saw_candidates=False` on the first attempt AND on R4.115's
settle-and-retry, so the element is ABSENT; and the page at that moment is the LEADS LIST with no
form on it, because the preceding click on `button "New"` bound, executed without error, and left
the page where it was. The failing step is not the broken one.

PATCHES THE BINDING IN `flow.py`, NOT THE DEFINITION IN `locators.py`. `from .locators import
resolve` binds the function OBJECT, so patching the source module never reaches the caller -- S14's
lesson, which cost a slice when a `no_llm` fixture patched the wrong module and 105 real API clients
were built while every cell passed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from ultracua import flow as flow_mod

from . import corpus

# Read the page the way a human would ask about it: is there a form, what fields are on offer, and
# what does the body say. Deliberately NOT the product's snapshot -- the question here is what the
# DOM held, and routing it through the observation would re-impose the cap and the fold that may be
# part of the story.
_PAGE_JS = r"""
() => {
  const fields = [];
  for (const el of document.querySelectorAll('input,textarea,select')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    fields.push({tag: el.tagName.toLowerCase(), id: el.id || null,
                 placeholder: el.getAttribute('placeholder'),
                 aria: el.getAttribute('aria-label'), top: Math.round(r.top)});
  }
  return {url: location.href.slice(0, 120),
          has_form: !!document.querySelector('.o_form_view,form'),
          field_count: fields.length, fields: fields.slice(0, 12),
          text: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 240)};
}
"""


def _spec_row(spec) -> dict:
    return {"role": getattr(spec, "role", None), "name": (getattr(spec, "name", None) or "")[:40],
            "tag": getattr(spec, "tag", None), "elem_id": getattr(spec, "elem_id", None),
            "testid": getattr(spec, "testid", None), "text": (getattr(spec, "text", None) or "")[:40]}


async def probe(scenario: str, *, wire: bool = False) -> dict:
    from . import scored_run

    calls: list[dict] = []
    requests: list[dict] = []
    page_at_failure: dict | None = None
    real_resolve = flow_mod.resolve
    t0 = time.monotonic()

    def hook(page) -> None:
        """Log every request, interleaved with the resolves, on one clock.

        WITHOUT THIS THE PHOTOGRAPH IS AMBIGUOUS, which cost a slice. R4.143 read a page that still
        showed the list after a click and concluded the click was INERT. The wire says otherwise:
        the click fires `POST .../onchange` 47 ms after the resolve, and the form's render is then
        gated on a lazily-loaded asset bundle plus a `render_public_asset` call. "The page has not
        changed" and "the click did nothing" are different worlds, and only the request log
        separates them.
        """
        if getattr(page, "_ultracua_wire_hooked", False):
            return
        page._ultracua_wire_hooked = True
        page.on("request", lambda r: requests.append(
            {"ms": round((time.monotonic() - t0) * 1000), "method": r.method, "url": r.url[-70:]}))

    async def spy(page, spec, *a, **kw):
        nonlocal page_at_failure
        if wire:
            hook(page)
        sink = kw.get("sink")
        out = await real_resolve(page, spec, *a, **kw)
        row = dict(_spec_row(spec), ms=round((time.monotonic() - t0) * 1000),
                   bound=out is not None,
                   # THE SENSOR THIS PROBE EXISTS FOR. `None` means the caller passed no sink, which
                   # is NOT the same as False -- reporting it as False would invent an absence.
                   saw_candidates=(sink or {}).get("saw_candidates"),
                   bound_by=(sink or {}).get("bound_by"))
        calls.append(row)
        if out is None and page_at_failure is None:
            try:
                page_at_failure = await page.evaluate(_PAGE_JS)
            except Exception as exc:  # noqa: BLE001 -- a dead page must not replace the diagnosis
                page_at_failure = {"error": f"{type(exc).__name__}: {exc}"}
        return out

    flow_mod.resolve = spy
    try:
        out, scored = await scored_run.score_one(scenario)
    finally:
        flow_mod.resolve = real_resolve

    rp = out.get("replay") or {}
    gate = out.get("gate") or {}
    return {"scenario": scenario,
            "outcome": scored.verdict.outcome if scored else None,
            "recipe_steps": out.get("steps"), "mutating_steps": gate.get("mutating_steps"),
            "gate_refused": gate.get("mutation_gate_refused"),
            "replay_error": rp.get("error"), "resolves": calls,
            "page_at_failure": page_at_failure, "requests": requests}


def render(r: dict) -> None:
    print(f"\n=== {r['scenario']}: {r['outcome']!r} ===")
    print(f"  steps={r['recipe_steps']!r} mutating={r['mutating_steps']!r} "
          f"gate_refused={r['gate_refused']!r}")
    print(f"  error: {r['replay_error']!r}")
    print(f"  --- {len(r['resolves'])} resolve call(s) ---")
    for c in r["resolves"]:
        print(f"    {c.get('ms', 0):7} ms  bound={c['bound']!s:5} saw_candidates={c['saw_candidates']!s:5} "
              f"by={c['bound_by']!r:14} role={c['role']!r} name={c['name']!r} id={c['elem_id']!r}")
    if r.get("requests"):
        # INTERLEAVED ON ONE CLOCK, from the first resolve onward. A request AFTER a click is the
        # difference between "the click did nothing" and "the render is not finished".
        first = min((c.get("ms", 0) for c in r["resolves"]), default=0)
        rows = [(c["ms"], f"resolve {c['role']}/{c['name']!r} -> "
                          f"{'BOUND' if c['bound'] else 'none'}") for c in r["resolves"]]
        rows += [(q["ms"], f"  {q['method']} {q['url']}") for q in r["requests"] if q["ms"] >= first]
        print(f"  --- wire, interleaved ({len(r['requests'])} requests total) ---")
        for ms, line in sorted(rows)[:40]:
            print(f"    {ms:7} ms  {line}")
    p = r["page_at_failure"]
    if p:
        print("  --- the page at the FIRST failing resolve ---")
        if "error" in p:
            print(f"    could not read it: {p['error']}")
        else:
            print(f"    url={p['url']}\n    has_form={p['has_form']}  visible fields={p['field_count']}")
            for f in p["fields"][:6]:
                print(f"    field {f}")
            print(f"    text={p['text'][:180]!r}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", action="append", required=True, help="repeatable")
    ap.add_argument("--wire", action="store_true",
                    help="log every request on the same clock as the resolves -- the only thing that "
                         "separates an inert click from an unfinished render")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    known = {e.scenario.name for s in corpus.CORPORA for e in corpus.for_substrate(s)}
    unknown = [s for s in a.scenario if s not in known]
    if unknown:
        raise SystemExit(f"no such scenario: {unknown}")

    rows = [asyncio.run(probe(s, wire=a.wire)) for s in a.scenario]
    for r in rows:
        render(r)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"rows": rows}, indent=2) + "\n")
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
