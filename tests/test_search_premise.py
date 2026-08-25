"""`gitea-search` must actually require a search (R4.101, half one).

THE RULE THIS FILE EXISTS TO ENFORCE is already written down for Odoo — *a premise that makes a
scenario discriminating is ASSERTED, not assumed* — and it was never written for Gitea. The cost was
measured: with "marmalade" (an issue TITLE) the answer sat on the start page, the agent read it off
in **zero steps**, nothing was cached, and the run scored `not_authored` — a product-blaming verdict
for a correct answer, on a scenario that measured no search whatsoever.

EVERYTHING HERE IS DERIVED FROM `substrates.ISSUES`, the committed seed, so it runs in the fast tier
with no container. The live half — that Gitea's search reaches bodies and that the list renders none
— was measured against a real container when the term was chosen and is restated in `corpus.py`.
"""
from __future__ import annotations

import pytest

from benchmarks import corpus
from benchmarks.substrates import ISSUES

TITLES = [t for t, _b, _c in ISSUES]
BODIES = [b for _t, b, _c in ISSUES]


def _entry(name: str):
    return next(e for e in corpus.CORPORA["gitea"] if e.scenario.name == name)


def test_the_search_term_appears_in_no_title() -> None:
    """THE LOAD-BEARING ONE. A term in a title is visible on the issue list, so the scenario can be
    answered without acting — which is the whole defect, and it is invisible from the goal text."""
    guilty = [t for t in TITLES if corpus.SEARCH_TERM.lower() in t.lower()]
    assert not guilty, (
        f"{corpus.SEARCH_TERM!r} appears in the title(s) {guilty}, so it is on the issue list and "
        f"the agent can answer `gitea-search` without a single action. That produces a zero-step "
        f"flow, nothing is cached, and the run scores `not_authored` for a correct answer (R4.101).")


def test_the_search_term_appears_in_exactly_one_body() -> None:
    """Unique, or a correct answer cannot be told from a lucky one. `_search_title` enforces this
    against the live substrate too; here it is checked against the seed that produces it, so a bad
    edit to `ISSUES` fails before anything is started."""
    hits = [t for t, b, _c in ISSUES if corpus.SEARCH_TERM.lower() in b.lower()]
    assert len(hits) == 1, (
        f"{corpus.SEARCH_TERM!r} matches {len(hits)} issue bodies ({hits}); the scenario needs "
        f"exactly one so its expected answer is unambiguous")


def test_the_target_issue_is_open() -> None:
    """Deliberate, and it stops the scenario secretly testing two things.

    Gitea's issue list defaults to OPEN. If the search target were closed, an agent that searched
    without preserving `state=all` would find nothing — so the row would be measuring "did you keep
    the filter" as well as "can you search", and a failure would not say which.
    """
    closed = [t for t, b, c in ISSUES if corpus.SEARCH_TERM.lower() in b.lower() and c]
    assert not closed, (
        f"the search target {closed} is CLOSED; an agent that drops `state=all` while searching "
        f"would find nothing and fail for a reason this scenario is not about")


def test_the_goal_and_the_expected_answer_use_the_same_term() -> None:
    """They are two uses of one fact. Typed twice they drift, and the scenario then asks for one
    thing and grades another — silently, because both halves still look reasonable."""
    goal = _entry("gitea-search").scenario.goal
    assert corpus.SEARCH_TERM in goal, (
        f"the goal {goal!r} no longer names {corpus.SEARCH_TERM!r}, which is what the expected "
        f"answer is derived from")


def test_the_search_target_is_not_another_scenarios_answer() -> None:
    """A constant answer must not score twice. `gitea-sort-list` expects the OLDEST issue's title
    and `gitea-open-issue` expects issue 3's, so an agent that always replies with either would
    otherwise pick up a free point here."""
    target = next(t for t, b, _c in ISSUES if corpus.SEARCH_TERM.lower() in b.lower())
    oldest = TITLES[0]                      # ISSUE_EPOCH dates issue #1 first; see substrates.py
    issue_three = TITLES[2]
    assert target not in (oldest, issue_three), (
        f"the search target {target!r} is also another scenario's expected answer, so one constant "
        f"reply scores two rows")


@pytest.mark.parametrize("name", ["gitea-search"])
def test_the_scenario_starts_where_the_answer_is_not(name: str) -> None:
    """The premise stated as the property it really is: the start page lists titles, and the answer
    is not among them. Asserted from the seed rather than by fetching, so it holds in CI."""
    e = _entry(name)
    assert "issues" in e.scenario.url_path, "the start page is the issue LIST"
    target = next(t for t, b, _c in ISSUES if corpus.SEARCH_TERM.lower() in b.lower())
    assert corpus.SEARCH_TERM.lower() not in target.lower(), (
        "the answer's own title contains the term, so it is on the list page after all")
