"""What did the learn SEE when it authored each step? (R4.118 instrumentation, 0.144.0.)

WHY THIS EXISTS. The product persisted nothing about the observations a learn was authored from, and
on a client-rendered app that is the difference between a recipe and a transcript of an agent
guessing. R4.118: two of five Odoo learns cached recipes that are 100% `navigate` steps, one
re-navigating to the same url twelve times, and every one of those steps replays `ok`. Diagnosing it
needed a live substrate and six probes, because the artifact carried no evidence either way.

`CachedStep.precond_elements` records the element count of the very observation
`precond_fingerprint` was taken from, so "was this step authored against an unrendered page" becomes
an ARTIFACT question. R4.93 measured Odoo's unrendered shell at 5 elements settling to 80, so the
question has a real answer once the number is there.

THE WHOLE POINT IS THAT IT CHANGES NOTHING ELSE. It is diagnostic, unhashed, additive and defaulted:
no digest moves, no fleet is re-approval-gated, and no guard reads it. These cells pin that, in both
directions.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

from benchmarks import readiness_probe as P
from ultracua import cache as c
from ultracua.cache import CachedFlow, CachedStep, FlowCache
from ultracua.flows import FlowSpec, learn
from ultracua.types import Action


# ------------------------------------------------------------------ the classification contract


def test_precond_elements_is_unhashed_and_moves_no_digest() -> None:
    """THE SAFETY PROPERTY OF THIS WHOLE SLICE. `steps_hash` is what binds an approval, so a field
    that entered the digest would raise `StaleApprovalError` on every approved flow in every fleet
    the first time it was re-authored. Asserted two ways: the classification, and the digest itself
    over a step that carries a value."""
    assert "precond_elements" in c._UNHASHED_STEP_FIELDS
    assert "precond_elements" not in c._HASHED_STEP_FIELDS

    bare = CachedStep(intent="i", action="click")
    seen = CachedStep(intent="i", action="click", precond_elements=5)
    key = "http://x|g|s"
    mk = functools.partial(CachedFlow, key=key, goal="g", start_url="u", created_ts=0.0)
    assert c.steps_hash(mk(steps=[bare])) == c.steps_hash(mk(steps=[seen])), (
        "recording what the learn saw changed the approval digest -- every approved flow would "
        "refuse on its next re-author")


def test_the_field_is_optional_so_older_recipes_still_load() -> None:
    """A recipe cached before this field existed must deserialize unchanged, and must report its
    element count as UNKNOWN rather than as zero -- 'the learn saw nothing' and 'we did not record
    what the learn saw' are different facts."""
    step = CachedStep.model_validate({"intent": "i", "action": "navigate"})
    assert step.precond_elements is None


# ------------------------------------------------------------------------- the census reads it


def test_the_census_separates_unrecorded_from_seen_nothing() -> None:
    """BOTH DIRECTIONS, because collapsing them is this register's most-repeated defect."""
    old = P.recipe_shape({"steps": [{"action": "navigate"}]})
    assert old["precond_elements"]["unrecorded"] is True

    new = P.recipe_shape({"steps": [{"action": "navigate", "precond_elements": 5},
                                    {"action": "click", "precond_elements": 80}]})
    assert new["precond_elements"] == {"min": 5, "max": 80, "n": 2}
    assert "unrecorded" not in new["precond_elements"]


def test_a_zero_is_reported_as_a_real_zero() -> None:
    """`0` is falsy and `None` is falsy, and only one of them means the learn was blind. A census
    that used truthiness would report a genuinely empty page as unrecorded."""
    r = P.recipe_shape({"steps": [{"action": "navigate", "precond_elements": 0}]})
    assert r["precond_elements"] == {"min": 0, "max": 0, "n": 1}


# ------------------------------------------------------- a real learn actually populates it


class _ClickFirstLink:
    """Scripted: click the first link, then stop. No LLM, no key."""

    def __init__(self) -> None:
        self.n = 0

    async def decide(self, goal, obs, history):
        self.n += 1
        if self.n == 1:
            for e in obs.elements:
                if e.role == "link":
                    return Action(action="click", ref=e.ref, intent="open the answer"), None
        return Action(action="done", intent="done"), None


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}"


@pytest.mark.asyncio
async def test_a_real_learn_records_what_it_saw(tmp_path: Path) -> None:
    """THE CELL THAT MATTERS. The classification cells above would all pass against a field nothing
    ever writes -- which is the shape of a green instrument measuring nothing, twice shipped in this
    repo. This drives a real browser learn and asserts the count is present, positive, and equal to
    the size of the page the step was authored against."""
    (tmp_path / "page1.html").write_text(
        "<html><body><h1>Home</h1><a href='page2.html'>see the answer</a></body></html>",
        encoding="utf-8")
    (tmp_path / "page2.html").write_text(
        "<html><body><h1>Answer</h1><p>The answer is 42.</p></body></html>", encoding="utf-8")
    httpd, base = _serve(tmp_path)
    cache = FlowCache(root=tmp_path / "cache")
    spec = FlowSpec(name="answer", start_url=f"{base}/page1.html",
                    goal="open the answer page", headless=True)
    try:
        res = await learn(spec, provider=_ClickFirstLink(), cache=cache)
        assert res.cached and res.steps, "the fixture learn did not cache a recipe"
        for s in res.steps:
            assert s.precond_elements is not None, (
                f"step {s.action!r} carries no `precond_elements`; the field is declared and "
                f"classified but nothing populates it")
        # THE EXACT VALUE, not merely a positive one. `page1.html` holds ONE interactable -- the
        # link -- so the step the agent authored from it must record 1. A constant, an off-by-one,
        # or the count of the VERIFY observation (page2, which has 0 interactables) all fail here,
        # and all three would pass an `is not None and > 0` assertion.
        click = next(s for s in res.steps if s.action == "click")
        assert click.precond_elements == 1, (
            f"expected the one-link page's size (1), got {click.precond_elements!r} -- the count is "
            f"not coming from the observation this step was DECIDED from")
    finally:
        httpd.shutdown()
