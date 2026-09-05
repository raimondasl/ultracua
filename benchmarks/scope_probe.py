"""WHY does the mutation gate call a form DRIFTED? Diff the scope arrays it compares.

    uv run --no-sync python -m benchmarks.scope_probe --scenario odoo-idempotent-replay

**THIS SPENDS MONEY** -- one learn plus one replay per scenario, ~$0.06-0.15 on Odoo.

`odoo-idempotent-replay` is `refused_wrongly` 0 of 12 across four series. Its OUTCOME is stable and
its MECHANISM is not: the three 0.156.0 reps refused on the PRECISE branch failing its BIND
(`target missing/ambiguous`), and the nine since read `form/section drift` -- the SCOPE comparison
(R4.143). Nothing has ever looked at what the two scopes actually CONTAIN.

`scope_fingerprint` returns a hash, so the gate can say "these differ" and nothing can say HOW. This
probe captures the RAW `SCOPE_JS` array on every call -- record time and replay time -- plus a
description of the CONTAINER that `el.closest(...)` selected, and diffs them.

THE CONTAINER IS THE POINT, and it is why a hash could never answer this. `SCOPE_JS` scopes to
`el.closest('form, dialog, [role=dialog], fieldset, [role=form], section, main, [role=main],
article') || document.body`. If an OWL-rendered Odoo form has no such ancestor short of `main` or
`body`, the "precise" gate is fingerprinting the WHOLE BACKEND UI -- systray counters, list rows,
notification badges -- and is a whole-page gate wearing a precise gate's name. That is a different
defect from the one the register records, and it is invisible to every instrument built so far.

PATCHES THE BINDING IN `flow.py`, NOT THE DEFINITION IN `snapshot.py`: `from .snapshot import
scope_fingerprint` binds the OBJECT, so patching the source module reaches nothing (S14's lesson).

PHASE IS DERIVED FROM THE CALLING FRAME, NOT FROM A CLOCK OR A HASH. `scope_fingerprint` has exactly
two callers in `flow.py` -- `_author_steps` (record) and `_replay_step` (the gate) -- so the caller's
name IS the phase, and `_CALLERS` asserts that set so a third caller appearing tomorrow is a loud
`unknown:<name>` rather than a silent misattribution. A timing boundary would have to be invented,
and an invented boundary is how a probe reports its own guess as a measurement.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
import time

from ultracua import flow as flow_mod
from ultracua.snapshot import SCOPE_JS

# What did `el.closest(...)` actually select, and how big is it? Answers "is the precise gate
# precise" in one call. The selector string is duplicated from SCOPE_JS deliberately -- this probe
# must describe the container the PRODUCT chose, so a cell pins the two strings equal rather than
# letting an import make them silently agree.
_CONTAINER_JS = r"""
(el) => {
  const SEL = 'form, dialog, [role=dialog], fieldset, [role=form], section, main, [role=main], article';
  const scope = el.closest(SEL);
  const used = scope || document.body;
  const sel = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]',
    '[role=checkbox]', '[role=radio]', '[role=combobox]', '[role=switch]',
  ].join(',');
  return {
    matched: !!scope,
    tag: used.tagName.toLowerCase(),
    id: used.id || null,
    cls: (used.className || '').toString().slice(0, 80),
    interactables: used.querySelectorAll(sel).length,
    page_interactables: document.querySelectorAll(sel).length,
  };
}
"""


# The two functions in `flow.py` that call `scope_fingerprint`, and what each call MEANS. Asserted
# against the live source by `tests/test_scope_probe.py`, so a third caller is a red test rather than
# a row this probe quietly files under the wrong phase.
_CALLERS = {"_author_steps": "record", "_replay_step": "gate"}


def _phase(caller: str) -> str:
    return _CALLERS.get(caller, f"unknown:{caller}")


async def _describe(locator) -> dict:
    try:
        return await locator.evaluate(_CONTAINER_JS)
    except Exception as exc:  # noqa: BLE001 - a dead target must not replace the diagnosis
        return {"error": f"{type(exc).__name__}: {exc}"}


async def _raw(locator):
    try:
        return await locator.evaluate(SCOPE_JS)
    except Exception:  # noqa: BLE001
        return None


def _diff(a, b) -> dict:
    """Element-wise difference between two scope arrays, as multisets of [role, name, tag] triples.

    `count_changes` IS NOT DECORATION -- it is the field that answered R4.148, and the first draft of
    this function did not have it. Set differences are blind to a pure COUNT change, so on the one
    comparison the gate actually made this printed `only_recorded: []`, `only_replayed: []` and
    `identical=False` -- a diff that says "these differ" and shows nothing. The whole answer was
    `['checkbox','','input']` x23 -> x24: one more list row, because the flow's own write had added
    it. An instrument that cannot show the difference it found is one you finish by hand.
    """
    ta = [tuple(x) for x in a]
    tb = [tuple(x) for x in b]
    sa, sb = set(ta), set(tb)
    ca, cb = collections.Counter(ta), collections.Counter(tb)
    return {
        "recorded_len": len(ta), "replayed_len": len(tb),
        "only_recorded": [list(x) for x in ta if x in sa - sb][:25],
        "only_replayed": [list(x) for x in tb if x in sb - sa][:25],
        "count_changes": [{"triple": list(k), "recorded": ca[k], "replayed": cb[k]}
                          for k in sorted(set(ca) | set(cb)) if ca[k] != cb[k]][:25],
        "identical": ta == tb,
        # An ORDER-only difference is its own story: same controls, re-sorted by a re-render.
        "same_multiset": sorted(ta) == sorted(tb),
    }


async def probe(scenario: str) -> dict:
    from . import scored_run

    calls: list = []
    real = flow_mod.scope_fingerprint
    t0 = time.monotonic()

    async def spy(locator):
        out = await real(locator)
        calls.append({
            "seq": len(calls),
            "ms": round((time.monotonic() - t0) * 1000),
            "phase": _phase(sys._getframe(1).f_code.co_name),
            "hash": out,
            "raw": await _raw(locator),
            "container": await _describe(locator),
        })
        return out

    flow_mod.scope_fingerprint = spy
    try:
        out, scored = await scored_run.score_one(scenario)
    finally:
        flow_mod.scope_fingerprint = real

    gate = out.get("gate") or {}
    rp = out.get("replay") or {}

    rec = [c for c in calls if c["phase"] == "record" and c["raw"] is not None]
    rep = [c for c in calls if c["phase"] == "gate" and c["raw"] is not None]
    pairs = [{"recorded_seq": q["seq"], "replayed_seq": r["seq"],
              "hash_equal": q["hash"] == r["hash"], **_diff(q["raw"], r["raw"])}
             for r in rep for q in rec]

    return {"scenario": scenario,
            "outcome": scored.verdict.outcome if scored else None,
            "gate_refused": gate.get("mutation_gate_refused"),
            "mutating_steps": gate.get("mutating_steps"),
            "replay_error": rp.get("error"),
            "calls": calls, "diffs": pairs}


def render(r: dict) -> None:
    print("")
    print(f"=== {r['scenario']}: {r['outcome']!r} ===")
    print(f"  gate_refused={r['gate_refused']!r}  mutating_steps={r['mutating_steps']!r}")
    print(f"  error: {r['replay_error']!r}")
    print(f"  --- {len(r['calls'])} scope_fingerprint call(s) ---")
    for c in r["calls"]:
        k = c["container"]
        if "error" in k:
            print(f"    [{c['seq']}] {c['ms']:7} ms {c['phase']:6} hash={c['hash']!r} "
                  f"container={k['error']}")
            continue
        n = len(c["raw"]) if c["raw"] is not None else None
        pi = k.get("page_interactables") or 0
        share = f"{100.0 * k['interactables'] / pi:.0f}%" if pi else "?"
        print(f"    [{c['seq']}] {c['ms']:7} ms {c['phase']:6} "
              f"hash={(c['hash'] or '')[:12]!r:15} entries={n}")
        print(f"          container: matched={k['matched']} <{k['tag']}"
              f"{' id=' + k['id'] if k['id'] else ''}> class={k['cls'][:44]!r}")
        print(f"          holds {k['interactables']} of the page's {pi} interactables "
              f"({share} of the page)")
    for d in r["diffs"]:
        print(f"  --- diff: recorded[{d['recorded_seq']}] vs replayed[{d['replayed_seq']}] ---")
        print(f"    identical={d['identical']}  same_multiset={d['same_multiset']}  "
              f"lens {d['recorded_len']} -> {d['replayed_len']}")
        for x in d["only_recorded"][:12]:
            print(f"      only when RECORDED: {x}")
        for x in d["only_replayed"][:12]:
            print(f"      only on REPLAY:     {x}")
        for c in d.get("count_changes", [])[:12]:
            print(f"      COUNT CHANGED:      {c['triple']}  x{c['recorded']} -> x{c['replayed']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", action="append", required=True, help="repeatable")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    rows = [asyncio.run(probe(s)) for s in a.scenario]
    for r in rows:
        render(r)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
