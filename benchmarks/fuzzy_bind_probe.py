"""D2's adjudication, re-derivable in ~15 seconds (0.178.0, R4.156).

    python -m benchmarks.fuzzy_bind_probe             # the four-arm resolver adjudication
    python -m benchmarks.fuzzy_bind_probe --census    # the corpus census (~200 s, runs drift_bench)

D2 asks the resolver to "refuse sole-candidate fuzzy (`role+name~`) binds for MUTATING steps". The
answer was CHANGE-but-not-that-change, and a decision of that shape is worth exactly what its
reproducibility is worth (R4.111's rule, `gate_probe` and `row_echo_probe` one instrument over), so
the measurements live here rather than only in a register entry. No LLM, no substrate, no key.

WHAT THIS ESTABLISHES:

1. THE SCOPE CLAUSE MAKES IT A NO-OP. `--census` counts every `role+name~` bind in the corpus: 22
   rows across two arms, and ZERO on a write scenario. A mutating-scoped refusal never fires.

2. THE HARM IS REAL, ON READS. One of those rows is `fuzzy-decoy/fuzzy-decoy-wins`: the recorded
   name stops matching the renamed target and starts matching a DECOY, which is clicked -- act trail
   `['wrong-decoy', 'WRONG-PAGE']`.

3. THE REMEDY THE CODE NAMED IS THE EXPENSIVE ONE. `locators.py` and `HEALING.md` both said closing
   this needs "a css-agreement gate like Tier 2's, which measured at the same cost as full
   deletion". True, and the mechanism is the point: a positive-agreement gate can only accept a bind
   Tier 2 would have made anyway, so it destroys the augmented-label case on any page whose
   structure ALSO moved.

4. CONTRADICTION-ONLY IS FREE. Refusing only when css resolves uniquely to a DIFFERENT element keeps
   every augmented-label bind and removes the decoy bind.

HOW THE ARMS ARE BUILT, and why a post-filter would have been WRONG. Each candidate is a change
INSIDE `resolve`, and a refused Tier-1 fuzzy bind FALLS THROUGH to Tier 2, where css may re-bind the
same element under a different label. So "pretend the bind was refused" applied to the shipped
resolver's own output over-counts the cost and would have made contradiction-only look identical to
agreement. The arms are therefore real: a patched copy of `src/` on `PYTHONPATH`, the same install
`scripts/prove_red.py` uses (R4.77 -- `benchmarks/` sits at the repo root, so only `src/` is
swappable, which is exactly what is needed here).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The two fixtures that separate the candidates. Both are reduced from real drift-bench rows.
#
#   DECOY  -- `fuzzy-decoy/fuzzy-decoy-wins`. A redesign renames the primary control and moves the old
#             wording onto a neighbour. The recorded css path is UNTOUCHED, so it still resolves to the
#             target while the substring resolves to the decoy: a CONTRADICTION.
#   WRAP   -- the five `rename_augment+wrap` rows. The label is lightly augmented and the structure
#             moved, so the recorded css path is broken and there is nothing to contradict with. This
#             is the row an agreement gate destroys, and it is why it is here.
DECOY_HTML = """<!doctype html><html><body>
  <div><a href="/wrong" data-oracle="wrong-decoy"
         aria-label="Save the document as a draft">Save draft</a></div>
  <div><a href="/done" data-oracle="go" aria-label="Publish">Publish</a></div>
</body></html>"""
DECOY_SPEC = dict(role="link", name="Save the document", tag="a", text="Save",
                  css="body > div:nth-of-type(2) > a")

WRAP_HTML = """<!doctype html><html><body>
  <div><span><a href="/done" data-oracle="go">Proceed now</a></span></div>
</body></html>"""
WRAP_SPEC = dict(role="link", name="Proceed", tag="a", text="Proceed", css="body > div > a")

# --- the candidate rules, as SOURCE patches against the shipped file -------------------------------
_SHIPPED = """                css_kind, css_first = await classify(css_loc)
                if css_kind == "unique" and not await _same_element(first, css_first):"""
_GUARD = """            if label == "role+name~" and css_loc is not None:"""

ARMS: dict = {
    "shipped": [],
    # the pre-D2 resolver: the guard never runs
    "control": [(_GUARD, """            if False and label == "role+name~" and css_loc is not None:""")],
    # the remedy `locators.py` and HEALING.md named: css must AGREE
    "agreement": [(_SHIPPED, """                css_kind, css_first = await classify(css_loc)
                if css_kind != "unique" or not await _same_element(first, css_first):""")],
    # deletion-equivalent: the fuzzy candidate is never offered at Tier 1
    "refuse_all": [('        confident.append(("role+name~", page.get_by_role(spec.role, '
                    'name=spec.name, exact=False)))  # type: ignore[arg-type]',
                    "        pass  # arm: the fuzzy candidate is withheld")],
}


async def _bind(html: str, spec_kw: dict) -> tuple:
    """Drive the REAL resolver. Never a reimplementation of it (R4.134)."""
    from playwright.async_api import async_playwright

    from ultracua import locators as L

    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        try:
            pg = await (await b.new_context()).new_page()
            await pg.set_content(html)
            spec = L.LocatorSpec(anchor=None, anchor_source=None, **spec_kw)
            sink: dict = {}
            loc = await L.resolve(pg, spec, unique=True, sink=sink)
            if loc is None:
                return None, sink
            return await loc.get_attribute("data-oracle"), sink
        finally:
            await b.close()


async def _run_arm_inproc() -> None:
    """Executed INSIDE a subprocess whose PYTHONPATH names one arm's `src/`."""
    from ultracua import locators as L
    out = {"locators": L.__file__}
    for name, (html, spec) in (("decoy", (DECOY_HTML, DECOY_SPEC)),
                               ("wrap", (WRAP_HTML, WRAP_SPEC))):
        bound, sink = await _bind(html, spec)
        out[name] = {"bound": bound, "bound_by": sink.get("bound_by"),
                     "contradicted": bool(sink.get("fuzzy_contradicted"))}
    print("__RESULT__" + json.dumps(out))


def _build(arm: str, work: Path) -> Path:
    dst = work / arm
    shutil.copytree(ROOT / "src", dst / "src")
    p = dst / "src" / "ultracua" / "locators.py"
    s = io.open(p, encoding="utf-8", newline="").read()
    for find, repl in ARMS[arm]:
        if s.count(find) != 1:
            raise SystemExit(
                f"arm {arm!r}: its anchor matched {s.count(find)} times in locators.py, not once. "
                f"The resolver moved and this probe now describes code that is not there -- "
                f"re-express the arm rather than trusting its output (prove_red's stale rule).")
        s = s.replace(find, repl, 1)
    io.open(p, "w", encoding="utf-8", newline="").write(s)
    return dst / "src"


def adjudicate() -> int:
    work = Path(tempfile.mkdtemp(prefix="d2-arms-"))
    rows = {}
    try:
        for arm in ("control", "shipped", "agreement", "refuse_all"):
            src = _build(arm, work)
            env = dict(os.environ, PYTHONPATH=str(src), ANTHROPIC_API_KEY="")
            r = subprocess.run([sys.executable, "-c",
                                "import asyncio,sys;sys.path.insert(0,r'%s');"
                                "from benchmarks.fuzzy_bind_probe import _run_arm_inproc;"
                                "asyncio.run(_run_arm_inproc())" % ROOT],
                               capture_output=True, text=True, env=env, cwd=str(ROOT))
            line = next((ln for ln in r.stdout.splitlines() if ln.startswith("__RESULT__")), None)
            if line is None:
                print(r.stdout[-2000:], r.stderr[-2000:])
                raise SystemExit(f"arm {arm!r} produced no result")
            rows[arm] = json.loads(line[len("__RESULT__"):])
            # The arm swap is PROVEN, not assumed: a probe reporting four identical rows because the
            # PYTHONPATH never took effect is the failure mode this line exists to make visible (R4.75).
            assert str(work) in rows[arm]["locators"], f"arm {arm!r} loaded the tree's own locators.py"
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\n  THE DECOY ROW (`fuzzy-decoy/fuzzy-decoy-wins`) -- the harm D2 is about")
    print(f"  {'arm':12} {'bound':14} {'by':12} {'verdict'}")
    for arm in ("control", "shipped", "agreement", "refuse_all"):
        d = rows[arm]["decoy"]
        verdict = ("WRONG -- clicks the decoy" if d["bound"] == "wrong-decoy"
                   else "refused (loud)" if d["bound"] is None else f"bound {d['bound']}")
        print(f"  {arm:12} {str(d['bound']):14} {str(d['bound_by']):12} {verdict}")

    print("\n  THE AUGMENTED-LABEL ROW (`rename_augment+wrap`) -- the COST control")
    print(f"  {'arm':12} {'bound':14} {'by':12} {'verdict'}")
    for arm in ("control", "shipped", "agreement", "refuse_all"):
        d = rows[arm]["wrap"]
        verdict = "kept (0-LLM)" if d["bound"] == "go" else "LOST -- this is the 84 -> 79 cost"
        print(f"  {arm:12} {str(d['bound']):14} {str(d['bound_by']):12} {verdict}")

    ok = (rows["control"]["decoy"]["bound"] == "wrong-decoy"
          and rows["shipped"]["decoy"]["bound"] is None
          and rows["shipped"]["decoy"]["contradicted"]
          and rows["shipped"]["wrap"]["bound"] == "go"
          and rows["agreement"]["wrap"]["bound"] != "go"
          and rows["refuse_all"]["wrap"]["bound"] != "go")
    print("\n  D2 as measured: contradiction-only removes the wrong bind and keeps the augmented "
          "label;\n  agreement and deletion both lose it. Corpus prices: silent_wrong 6 -> 4 with the "
          "survival\n  curve byte-identical (contradiction) against 84 -> 79 and k50 6 -> 2 "
          "(agreement / deletion).")
    if not ok:
        print("\n  THE ADJUDICATION DID NOT REPRODUCE. Do not quote the numbers above.")
        return 1
    return 0


async def census() -> int:
    """Which corpus rows bind by `role+name~`, and are ANY of them writes? (runs the real bench)"""
    from benchmarks import drift_bench as DB

    captured: dict = {}
    real = DB._score

    def spy(rows, **kw):
        # `measure()` returns the SCORED record and not its rows, and the rows are the subject here.
        # Wrapping the scorer is how the census reads them without a second implementation of the run.
        captured["rows"] = rows
        return real(rows, **kw)

    DB._score = spy
    try:
        await DB.measure()
    finally:
        DB._score = real

    rows = captured["rows"]
    fuzzy = [r for r in rows if "role+name~" in (r["bound_by"] or [])]
    writes = [r for r in fuzzy if DB.SCENARIOS_BY_NAME[r["scenario"]].get("write")]
    print(f"\n  {len(rows)} rows over both arms; {len(fuzzy)} carry a `role+name~` bind\n")
    print(f"  {'row':44} {'arm':8} {'write?':7} {'outcome'}")
    for r in sorted(fuzzy, key=lambda r: (r["row_id"], r["arm"])):
        w = bool(DB.SCENARIOS_BY_NAME[r["scenario"]].get("write"))
        print(f"  {r['row_id'].split(':', 1)[-1]:44} {r['arm']:8} {str(w):7} {r['outcome']}")
    print(f"\n  ON WRITE SCENARIOS: {len(writes)} of {len(fuzzy)}")
    print("  D2's scope clause is 'for MUTATING steps'. At zero write rows it can never fire, which")
    print("  is why the shipped rule is scope-wide -- the harm this decision is about is on READS.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="benchmarks.fuzzy_bind_probe")
    ap.add_argument("--census", action="store_true",
                    help="run the real drift bench and count role+name~ binds (~200 s)")
    args = ap.parse_args()
    if args.census:
        return asyncio.run(census())
    return adjudicate()


if __name__ == "__main__":
    raise SystemExit(main())
