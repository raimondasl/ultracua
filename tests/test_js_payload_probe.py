"""The classification 0.5's decision rests on, held against the tree (0.180.0, R4.158).

`benchmarks/js_payload_probe.py` answers the only question that made 0.5's syntax check interesting:
which in-page payloads are consumed ONLY by call sites that swallow the exception, so a syntax error
in them would degrade a mechanism silently while the suite stayed green.

The verdict was NO CHANGE -- all five such payloads are caught anyway. That verdict is a measurement
over a CLASSIFICATION, so the classification is what rots. These cells hold the two ways it can:

  * the analyser stops recognising a consumer, so a payload is silently dropped from the census and
    the "can degrade silently" set shrinks for a reason nobody sees;
  * the analyser stops recognising a swallowing handler, so everything reads as `propagates` and the
    interesting set empties out -- a clean report of nothing, which is the shape this repository
    files most.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "ultracua"


def test_the_consumer_set_covers_every_playwright_entry_point_src_actually_uses() -> None:
    """DERIVED BOTH WAYS. A hand-written tuple is only as good as its worst entry (S14), and here a
    missing entry point silently removes a payload from the census rather than failing.

    So the set is compared against every method src/ actually calls with a `*_JS` name in its first
    argument: an entry point used in the tree and absent from `CONSUMERS` fails here.
    """
    from benchmarks import js_payload_probe as P

    known = set(P.payloads())
    used: set = set()
    for p in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.args
                    and any(isinstance(n, ast.Name) and n.id in known
                            for n in ast.walk(node.args[0]))):
                used.add(node.func.attr)

    missing = used - set(P.CONSUMERS)
    assert not missing, (
        f"src/ hands a `*_JS` payload to {sorted(missing)}, which `CONSUMERS` does not name. Those "
        f"call sites are invisible to the census, so a payload consumed only through them reads as "
        f"'spliced' and its fail-open exposure is never assessed (R4.158).")


def test_the_classification_is_not_degenerate() -> None:
    """ANTI-VACUITY, in both directions. An analyser that never finds a swallowing handler reports
    everything as `propagates` and concludes there is nothing to protect; one that finds a handler
    everywhere reports the opposite. Either is a clean-looking report of nothing.

    Deliberately NOT a count: the numbers move whenever `src/` grows a payload, and a cell that
    pins them would fail for an unrelated reason and get relaxed. What must hold is that BOTH
    populations are non-empty.
    """
    from benchmarks import js_payload_probe as P

    sites = P.classify()
    assert sites, "no payload is evaluated directly at all -- the census found nothing to classify"

    fail_open = [n for n, hs in sites.items() if all(hs)]
    propagates = [n for n, hs in sites.items() if not any(hs)]
    assert fail_open, (
        "no payload classifies as FAIL-OPEN. Either every swallowing `except` gained a re-raise -- "
        "which would be a real and welcome change worth recording -- or `_swallowing_handler` "
        "stopped recognising one, and the probe now reports that nothing can degrade silently.")
    assert propagates, (
        "EVERY payload classifies as fail-open, which means the handler test is matching something "
        "it should not; the census would then overstate the silent-degradation surface.")


def test_every_payload_is_either_evaluated_or_spliced_into_one_that_is() -> None:
    """A `*_JS` string that nothing evaluates and nothing splices is DEAD, and the census would
    quietly list it under 'spliced' rather than naming it. Checked by looking for the name inside
    another payload's own source text, which is how splicing is written here (`+ _ROWID_JS +`)."""
    from benchmarks import js_payload_probe as P

    known = P.payloads()
    evaluated = set(P.classify())
    src_text = {p.name: p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")}

    orphans = []
    for name, module in known.items():
        if name in evaluated:
            continue
        # spliced: its NAME appears in a concatenation somewhere other than its own assignment
        body = src_text[module]
        uses = body.count(name) + sum(t.count(name) for m, t in src_text.items() if m != module)
        if uses <= 1:                      # only its own `NAME = ` assignment
            orphans.append(f"{name} ({module})")
    assert not orphans, (
        f"these payloads are neither evaluated nor spliced into one that is: {orphans}. Dead in-page "
        f"JS is worse than unused Python -- nothing type-checks it and nothing runs it, so it rots "
        f"invisibly and the next reader assumes it is live.")


def test_breaking_a_payload_refuses_a_stale_anchor() -> None:
    """`--break` is what makes the verdict re-derivable, and a stale anchor would make it produce an
    UNBROKEN tree -- so every killer passes and the probe reports the payload as guarded when the
    measurement never happened. `prove_red`'s rule, applied to a probe."""
    from benchmarks import js_payload_probe as P

    src = inspect.getsource(P.break_payload)
    tree = ast.parse(src)
    raises = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
    assert len(raises) >= 2, (
        f"`break_payload` has {len(raises)} raise(s); it must refuse BOTH an unknown payload name "
        f"and a payload whose shape no longer offers a brace to unbalance")
    assert any("STALE" in ast.unparse(n) for n in raises), (
        "`break_payload` no longer refuses a stale anchor by name, so a payload whose literal moved "
        "would yield an unmodified tree and a green measurement of nothing")
