"""Scoring a settle predicate: the ground truth, and the two failures it exists to catch.

WHY. R4.115's refutation says a retry on `resolve() -> None` cannot work, because `None` means five
things and four are safety refusals. What CAN separate "not rendered yet" from "found it and refused"
is a readiness predicate -- and choosing one by intuition has now failed twice, so
`readiness_probe --settle` scores candidates against a ground truth instead.

`settle_verdicts` is PURE, so the interesting failures are reproducible here with no browser and no
substrate: a predicate that fires inside a mid-render PLATEAU, and a ground truth that takes the
FIRST change rather than the last. Both were found the expensive way first -- as two harnesses
disagreeing, and as a single-run verdict that a second run overturned.
"""

from __future__ import annotations

from benchmarks import readiness_probe as P


def _s(*rows) -> list:
    """(t_ms, els, quiet_ms) -> the sample shape `settle_verdicts` consumes."""
    return [{"t": t, "els": e, "quiet_ms": q, "muts": 0, "ready": "complete"}
            for t, e, q in rows]


# ------------------------------------------------------------------------ the ground truth


def test_ground_truth_is_the_LAST_change_not_the_first() -> None:
    """A page that grows 5 -> 80 -> 255 is not settled at the first change. Taking the first would
    declare Odoo ready at ~100 ms and score every premature predicate as correct -- the measurement
    would confirm whatever it was pointed at."""
    v = P.settle_verdicts(_s((0, 5, 0), (100, 80, 0), (500, 255, 0), (900, 255, 500)))
    assert v["true_ready_ms"] == 500
    assert v["final_els"] == 255 and v["els_at_dcl"] == 5


def test_a_page_that_never_changes_is_ready_at_zero() -> None:
    """Gitea and the static control, measured `true_ready == 0` in every rep. It matters because it
    makes every millisecond a predicate waits there PURE TAX, which is the whole argument for waiting
    conditionally on the replay side."""
    assert P.settle_verdicts(_s((0, 143, 0), (100, 143, 100), (200, 143, 200)))["true_ready_ms"] == 0


def test_an_already_settled_page_is_zero_even_when_sampling_started_late() -> None:
    """THE PROBE'S OWN LATENCY IS NOT THE PAGE'S. The first `evaluate` after `goto` can land
    hundreds of ms in, and if that first observation already holds the final value we know only that
    the page settled at or before it -- never that it settled THEN.

    Reporting the first sample's timestamp instead said Gitea settled at 219-266 ms and a STATIC
    FIXTURE at 16-94 ms, and was one commit away from shipping as a 'correction' to the true claim
    that Gitea is complete at `domcontentloaded`."""
    v = P.settle_verdicts(_s((234, 143, 60), (300, 143, 120), (400, 143, 220)))
    assert v["true_ready_ms"] == 0, "the probe's startup latency was reported as the page's"
    assert v["candidates"]["dcl (today)"]["premature"] is False, (
        "acting at once on a page that was ALREADY settled is not premature; scoring it so would "
        "indict the baseline on every server-rendered page and flatter every alternative")


def test_no_samples_is_empty_not_a_confident_zero() -> None:
    assert P.settle_verdicts([]) == {}


# --------------------------------------------------------------- prematurity vs lateness


def test_els_stable_fires_inside_a_plateau() -> None:
    """THE MEASURED FLAW, reproduced offline. Two equal counts 100 ms apart can BOTH land inside a
    pause mid-render, so `els-stable` reports ready while the page is still growing. Measured 6
    premature over 60 page-reps -- and it is the predicate a rewritten harness used, which is why
    that harness disagreed with a validated one and cost an afternoon."""
    v = P.settle_verdicts(_s((0, 5, 0), (100, 5, 100), (200, 5, 200),      # <- the plateau
                             (600, 255, 0), (900, 255, 300)))
    assert v["true_ready_ms"] == 600
    c = v["candidates"]["els-stable"]
    assert c["fired_ms"] == 100 and c["premature"] is True
    assert c["late_by_ms"] is None, (
        "a premature firing must not report a lateness -- averaging one in would make the WORST "
        "candidate look like the best")


def test_a_quiet_predicate_that_waits_out_the_plateau_is_not_premature() -> None:
    """The control for the cell above: the same page, scored by a predicate that asks the DOM whether
    it is still mutating rather than whether two samples happen to match."""
    v = P.settle_verdicts(_s((0, 5, 0), (100, 5, 100), (200, 5, 200),
                             (600, 255, 0), (900, 255, 300), (1200, 255, 600)))
    c = v["candidates"]["mut-quiet-500"]
    assert c["premature"] is False and c["fired_ms"] == 1200 and c["late_by_ms"] == 600


def test_today_is_scored_as_premature_on_a_slow_page() -> None:
    """`dcl (today)` is the product's current behaviour -- act at once. It must appear in the table
    and be scored like any other candidate, or the measurement has no baseline to improve on.
    Measured 28 premature over 60 page-reps, all 7 Odoo pages."""
    v = P.settle_verdicts(_s((0, 5, 0), (500, 255, 0), (900, 255, 400)))
    assert v["candidates"]["dcl (today)"]["premature"] is True


def test_a_predicate_that_never_fires_is_reported_as_never() -> None:
    """`networkidle` is separately refuted by never firing on Odoo at all. A scorer that recorded a
    non-firing predicate as 0 would rank the most broken candidate first."""
    v = P.settle_verdicts(_s((0, 143, 10), (100, 143, 20), (200, 143, 30)))
    c = v["candidates"]["mut-quiet-500"]
    assert c["fired_ms"] is None and c["premature"] is False and c["late_by_ms"] is None
