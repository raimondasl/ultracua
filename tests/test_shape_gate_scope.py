"""WHICH reads the shape gate can refuse, and which it structurally cannot. (R4.140.)

`odoo-filter-status` refuses `shape_drift` about one run in three because its extractor optionally
emits a second key. The obvious worry was that every extracting read carries the same latent
variance and the others had merely been lucky -- at a 1/3 rate a clean three-rep run happens 30% of
the time, so their records were NOT evidence of immunity.

Driving the other four Odoo reads five times each answered it: **19 of 19 scored runs returned a
bare string**, and `_shape_matches` compares two same-primitive shapes as EQUAL whatever they
contain. So a scalar-returning read cannot fail this way at all -- structurally, not by luck.

That measurement cost real money and dies with the terminal it printed in. What survives is the
STRUCTURAL half, pinned here: the gate's scope is decided by `_shape_of`'s TYPE, so this file is
what a future reader checks instead of re-buying twenty learns. The empirical half -- that those
four goals really do yield primitives -- is `baselines/runs/odoo_0159_extract_shapes.json` and
`benchmarks/extract_shape_probe.py`, which re-derives it.

Both directions matter. Without the "an object DOES drift" half the property is satisfied by a gate
that never refuses anything, which is the regression this repository has actually shipped before.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from ultracua import flows

MEASURED = pathlib.Path(__file__).resolve().parents[1] / "baselines" / "runs" / "odoo_0159_extract_shapes.json"


def _matches(a, b) -> bool:
    return flows._shape_matches(flows._shape_of(a), flows._shape_of(b))


@pytest.mark.parametrize("left,right", [
    ("Won", "Gemini Furniture"),          # the two extremes actually observed across scenarios
    ("Need 20 Desks", ""),                # an empty answer is still a string
    (6, 0),
    (True, False),
])
def test_a_scalar_read_cannot_shape_drift_however_its_VALUE_moves(left, right):
    """THE IMMUNITY, and it is about the TYPE rather than the content.

    `_shape_of` reduces a scalar to `{"t": "<primitive>"}` -- **the value is not in the shape at
    all** -- so two different strings produce BYTE-IDENTICAL shapes and `_shape_matches` accepts
    them at its `recorded == current` short-circuit. A read whose answer is one string is therefore
    outside `shape_drift`'s reach no matter what the page says, which is the whole reason R4.140
    does not generalize.

    THE IMMUNITY HAS TWO INDEPENDENT SOURCES, WHICH AN ARMING PASS ESTABLISHED AND NO AMOUNT OF
    READING WOULD HAVE. (1) `_shape_of` discards the value, so two strings give byte-identical
    shapes and `recorded == current` accepts them. (2) Even with the value put back IN, the dict
    branch finds `t` equal and the `same primitive type` arm answers True. Breaking EITHER alone
    leaves this cell green; breaking BOTH kills it (verified). So the cell is not vacuous, and the
    scope claim underneath R4.140 does not hang on a single line -- which is worth knowing, because
    a claim that four corpus rows CANNOT fail is only as strong as its weakest guarantee.

    Two warnings fall out for anyone editing `_shape_of`: putting a value into a scalar's shape
    converts every scalar read into a drift-able one, and it would not fail here alone.
    """
    assert _matches(left, right), (
        f"a scalar read became shape-drift-able: {left!r} vs {right!r}. R4.140's scope claim -- that "
        f"only composite-answer reads can refuse `shape_drift` -- rests on this, and four Odoo rows "
        f"are declared immune because of it")


@pytest.mark.parametrize("left,right,why", [
    (6, "6", "a count the extractor quoted as text"),
    (6, 6.0, None),                        # number/number: NOT a drift, and must not become one
    (True, 1, "a bool answered as a number"),
])
def test_a_PRIMITIVE_TYPE_change_is_still_drift_which_is_the_scalar_residual(left, right, why):
    """The residual the immunity does NOT cover, kept explicit so nobody reads "scalars are safe".

    `_shape_matches` compares `t` first, so `6` and `"6"` DO drift. That is the live risk for a goal
    asking "how many" -- exactly R4.140's row -- and it is why rewording that goal to return a scalar
    narrows the failure rather than removing it. `6` vs `6.0` is deliberately in the other class:
    `_shape_of` calls both `number`, and a gate that split them would refuse on an extractor writing
    a whole number two ways.
    """
    if why is None:
        assert _matches(left, right), "int and float are one primitive here; splitting them invents a refusal"
    else:
        assert not _matches(left, right), (
            f"a primitive-type change stopped being drift ({why}): {left!r} vs {right!r}. This is the "
            f"direction that returns silently-different data to a caller who pinned nothing")


@pytest.mark.parametrize("left,right,drifts", [
    ({"count": 6}, {"count": 0}, False),                              # same keys, any values
    ({"count": 6}, {"count": 6, "opportunities": []}, True),          # THE MEASURED DEFECT
    ({"count": 6, "opportunities": []}, {"count": 6}, True),          # and its mirror
    ({"count": 6}, {"total": 6}, True),                               # renamed key
])
def test_an_OBJECT_read_drifts_on_its_KEY_SET_which_is_the_defect_R4_140_reports(left, right, drifts):
    """The other half, without which "scalars are immune" is satisfied by a gate that refuses nothing.

    The second row IS R4.140: the extractor answered `{'count': 6}` on one run and
    `{'count': 6, 'opportunities': [...]}` on another, both correct, and the gate compared key sets
    with EXACT equality. Its mirror is listed because a dropped key is the case the gate genuinely
    exists for -- a field vanishing because the page changed -- and any remedy that makes the second
    row pass must leave the third refusing.
    """
    assert _matches(left, right) is not drifts, (
        f"the object arm changed: {left!r} vs {right!r} should "
        f"{'refuse' if drifts else 'pass'}. If a fix for R4.140 made this pass by widening "
        f"`_shape_matches`, it disabled the check that catches a vanished field")


def test_the_measured_corpus_shapes_still_say_what_the_finding_claims():
    """The EMPIRICAL half, committed, because the structural cells above are true of any codebase.

    They say a scalar read is immune; they cannot say the corpus's reads RETURN scalars, which is
    what cost twenty learns. A row here going composite is the signal that R4.140's scope has widened
    and the immunity argument no longer covers it.
    """
    doc = json.loads(MEASURED.read_text(encoding="utf-8"))
    rows = doc["scenarios"]
    assert doc["substrate"] == "odoo" and rows, "the measured artifact is empty"

    composite = {n: r for n, r in rows.items() if r["shape_t"] == "object"}
    assert set(composite) == {"odoo-filter-status"}, (
        f"the set of composite-answer Odoo reads moved to {sorted(composite)}. R4.140 is declared "
        f"specific to `odoo-filter-status`; another row returning an object is another row that can "
        f"refuse `shape_drift`, and the honesty page says only one can")

    for name, r in rows.items():
        if name in composite:
            continue
        assert r["distinct_shapes"] == 1 and r["shape_t"] == "string", (
            f"{name} was measured returning {r['shape_t']!r} in {r['distinct_shapes']} distinct "
            f"shapes; the immunity argument covers a read that returns ONE primitive type")


# --- the instrument's own pure half ---------------------------------------------------------------
# `benchmarks/extract_shape_probe.py` spends money to answer the empirical question, so the parts
# that can be driven offline are driven offline. Imported as a MODULE and called through it, which is
# the form `_arming.mutate_function` can reach.
from benchmarks import extract_shape_probe as ESP  # noqa: E402


@pytest.mark.parametrize("value", ["Won", "", 6, 6.0, True, None, {"count": 6}, ["a", "b"]])
def test_the_probe_asks_the_PRODUCT_for_a_shape_rather_than_deriving_one(value):
    """A probe that re-implements what it measures measures itself.

    `web_survey` did exactly that -- it copied `nameOf` and reported Odoo at 36% unnamed against the
    product's own 19%. So `shape_key` must be `_shape_of` plus a stable rendering and nothing else;
    a divergence here means the artifact records a shape the gate would never compute.
    """
    expected = None if value is None else json.dumps(flows._shape_of(value), sort_keys=True)
    assert ESP.shape_key(value) == expected


def test_a_run_that_observed_nothing_is_not_counted_as_a_stable_shape():
    """`None` is absence of evidence, and the distinction is the whole finding one level down.

    One `odoo-menu-nav` rep came back unscored with no data. Folding that in as a shape would let a
    scenario that never produced an answer read as "1 distinct shape over N runs" -- a confident
    claim of stability built out of runs that observed nothing.
    """
    assert ESP.shape_key(None) is None


def test_the_probe_refuses_a_write_scenario_and_an_unknown_name():
    """A write's answer is not extracted, so there is no shape to compare and no question to ask."""
    with pytest.raises(SystemExit) as ei:
        ESP.main(["--substrate", "odoo", "--only", "odoo-create-lead"])
    assert "no read scenarios" in str(ei.value)

    with pytest.raises(SystemExit) as ei:
        ESP.main(["--substrate", "odoo", "--only", "not-a-scenario"])
    assert "no such scenario" in str(ei.value)
