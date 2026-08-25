"""The fold probe's own arithmetic, offline (R4.102).

NO CONTAINER AND NO BROWSER. `probe()` is a navigate-and-evaluate wrapper; what is worth pinning is
the REPORT's reasoning, and above all `body_scrolls` — the property that exists to say a naive
"is the page taller than the viewport" hint would be inert on Odoo. That claim is the reason the
probe counts elements instead of comparing heights, so it is the claim a test has to hold.
"""
from __future__ import annotations

import pytest

from benchmarks.fold_probe import COUNT_JS, FoldReport


def _report(**over) -> FoldReport:
    base = dict(url="http://x/y", in_viewport=50, off_screen=0, doc_height=720,
                viewport_height=720, hidden=())
    return FoldReport(**{**base, **over})


def test_a_page_that_fits_reports_nothing_hidden() -> None:
    r = _report()
    assert r.off_screen == 0 and r.body_scrolls is False
    assert "HIDDEN" not in str(r)


def test_the_gitea_shape_is_a_taller_body() -> None:
    """`gitea-comment`, measured: a 1512px document in a 720px viewport, 15 controls below."""
    r = _report(off_screen=15, doc_height=1512, in_viewport=57)
    assert r.body_scrolls is True
    assert "HIDDEN" in str(r)


def test_the_odoo_shape_hides_controls_WITHOUT_a_taller_body() -> None:
    """THE MEASUREMENT THE WHOLE INSTRUMENT IS SHAPED AROUND.

    `odoo-sort-list`: `document.body.scrollHeight` is 720 in a 720px viewport — the body does not
    scroll at all — and **12** interactable controls are still outside it, because Odoo scrolls an
    inner container. A hint derived from `docH > vpH` would fire on Gitea and be silently inert here,
    which is a half-fix that reads as a whole one.
    """
    r = _report(off_screen=12, doc_height=720, viewport_height=720, in_viewport=67)
    assert r.off_screen == 12
    assert r.body_scrolls is False, "the naive height test must be FALSE on this shape"
    assert "THE BODY DOES NOT SCROLL" in str(r), (
        "the report has to say so out loud, or a reader sees `hidden=12` beside a 720/720 page and "
        "concludes the probe is broken")


def test_the_hidden_controls_are_named_not_merely_counted() -> None:
    """Whether a hidden control matters depends on the task, and only a human reading the names can
    tell the footer links from the button the scenario needs."""
    r = _report(off_screen=1, doc_height=1512,
                hidden=(("button", "Start Time Tracking", 859),))
    assert r.hidden[0][1] == "Start Time Tracking"


@pytest.mark.parametrize("needle", ["r.top > innerHeight", "r.bottom < 0",
                                    "r.left > innerWidth", "r.right < 0"])
def test_the_probe_rejects_on_the_same_test_the_snapshot_does(needle: str) -> None:
    """DERIVED FROM `snapshot.py`, not re-invented. Its rejection is
    `r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth`; a probe using a
    narrower test (only "below") would under-report a horizontally scrolled table, and a wider one
    would report controls the snapshot would never have offered anyway — a false alarm in an
    instrument, which is worse than no instrument."""
    assert needle in COUNT_JS


def test_the_probe_mirrors_the_snapshots_visibility_rejection() -> None:
    """Same reason: counting `display:none` controls as "hidden by the fold" would blame the
    viewport for elements the page itself never showed."""
    for needle in ("visibility", "display", "opacity"):
        assert needle in COUNT_JS


def test_the_snapshot_really_does_reject_off_screen_elements() -> None:
    """THE PREMISE, read from the product rather than asserted about it. If this ever stops being
    true the probe is measuring a limit that no longer exists, and every number it prints is noise.
    """
    import inspect

    from ultracua import snapshot as snap

    src = inspect.getsource(snap)
    assert "r.top > innerHeight" in src, (
        "the snapshot no longer rejects elements below the viewport — R4.102's premise is gone and "
        "this probe needs re-deriving before its output means anything")
