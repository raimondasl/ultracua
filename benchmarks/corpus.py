"""The v1 corpus: one scenario, its ground truth and its oracle, bound together so they cannot drift.

WHY ONE OBJECT RATHER THAN THREE LISTS. A scenario needs three things that must agree: what the agent
is asked to do, what the corpus author DECLARES about it (is it a write? must it be refused?), and how
the server is questioned afterwards. Held apart, they drift — and every way they can drift is silent:

  * `Scenario.mutating` says read while `ScenarioTruth.mutating` says write, and `over_gated` is
    measured against the wrong baseline (B3 measures over-gating against the DECLARATION precisely so
    the classifier cannot agree with itself);
  * an oracle registered for a scenario that no longer exists arms cleanly and adjudicates nothing;
  * a scenario with no oracle scores on the agent's own say-so, which is the thing this whole
    benchmark exists not to do.

So `CorpusEntry` carries all three and `for_substrate` derives the oracle set FROM the corpus. There
is no second list to fall out of step with the first.

FOURTEEN, PAIRED. The same read INTENT on both substrates, so a difference in the result is
attributable to architecture rather than to task difficulty (benchmark-plan section 7). Gitea answers
over its HTTP API; Odoo answers over SQL, which is where the R4.86 clock scan finally has something
real to police.

WHAT AN ORACLE HERE CAN AND CANNOT SAY ABOUT A READ. It can say the world did not change — that is a
fact, and a read that changed something is `incorrect_target`, an inviolable. It can say the agent's
answer contains the distinctive fact the task asked for. It cannot certify that a free-text answer is
"correct" in any richer sense, and it does not pretend to: `check_answer` is deliberately LENIENT,
because `wrong_data` is inviolable #2 and a false positive there is a silent-wrong-answer accusation
against a product that answered fine. B3 guards the same direction from the other side by refusing to
mint `wrong_data` unless the run recorded `claimed_complete`.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import oracles as O                      # noqa: E402
from benchmarks import substrates as S                   # noqa: E402
from benchmarks.customer_bench import Scenario           # noqa: E402
from benchmarks.outcomes import Oracle as BenchOracle    # noqa: E402
from benchmarks.outcomes import ScenarioTruth            # noqa: E402


@dataclass(frozen=True)
class CorpusEntry:
    """One scenario, its declared truth, its oracle factory, and how its answer is checked."""

    scenario: Scenario
    truth: ScenarioTruth
    oracle: Callable                       # (substrate) -> O.Oracle
    #: Reads only: (substrate) -> the distinctive fact the answer must carry. None for writes.
    expected_answer: Optional[Callable] = None
    #: DOES THIS READ GOAL DELIBERATELY CARRY A `safety.MUTATING_KEYWORDS` TOKEN?
    #:
    #: The default is False and the guard is the point: a read goal that trips the classifier
    #: MANUFACTURES the `over_gated` this corpus exists to measure, and the benchmark would be
    #: grading its own phrasing. One row declares True -- `odoo-open-record`, where benchmark-plan
    #: section 7 says the tripping IS what the pair isolates, since a sale order cannot be described
    #: without the word "order".
    #:
    #: ASSERTED BOTH WAYS. An undeclared goal that trips is the manufacturing this prevents; a
    #: DECLARED goal that does not trip is a scenario no longer measuring what it claims -- and that
    #: direction is the one that rots silently, because rephrasing a goal is exactly the sort of
    #: tidy-up nobody re-checks.
    #:
    #: IT SPEAKS FOR THE GOAL ONLY, AND A STEP IS A DIFFERENT SURFACE. `keyword_read=False` does NOT
    #: mean the row cannot manufacture over-gating: the classifier also runs over what a STEP
    #: touches, and nothing here declares that. Measured on `gitea-search`, which is
    #: `keyword_read=False` and whose learned recipe still carries `mutating_steps: 1` from source
    #: `keyword` -- the `press Enter` that submits the search. The mutation gate did not refuse it,
    #: so this is a recorded fact rather than a defect (D0: a `mutating` mark is a GUESS; be
    #: conservative because of one, never refuse a flow for one). It is written down because a
    #: reviewer reading the flag alone would conclude the opposite, and because it makes this the
    #: first Gitea row that can exercise the over-gating path at all.
    keyword_read: bool = False
    #: WRITES ONLY: how the product knows the write LANDED, and (opt-in) how it knows it already had.
    #:
    #: A `MutateSpec` requires at least one `confirm_*` -- "a write that can't be confirmed is
    #: fire-and-hope" -- so a write scenario that declares none cannot be learned at all. Held here
    #: rather than in the runner because it is a fact about the SCENARIO, exactly like the goal and
    #: the expected answer, and the runner is meant to drive the corpus rather than annotate it.
    #:
    #: DECLARATIVE, not `MutateSpec` kwargs, so `corpus.py` stays free of engine types and
    #: `precheck_path` can stay relative -- the absolute URL depends on which base the AGENT is
    #: pointed at, and under the evidence proxy that is an ephemeral port. Keys: `text`, `selector`,
    #: `precheck_path`, `precheck_text`. Every value below was MEASURED against a live substrate
    #: before it was written down; `gitea-start-timer`'s was wrong by one character of case on the
    #: first attempt ("Stop timer" for "Stop Timer"), which would have turned a write learn into a
    #: paid failure.
    write_confirm: Optional[dict] = None
    #: Must the REPLAY see the world the LEARN left behind?
    #:
    #: False for every scenario but one, and the default is the common case: the harness resets
    #: between phases so the replay writes onto a clean world and the oracle sees exactly one record.
    #: `odoo-idempotent-replay` is the exception BY DEFINITION -- it exists to check that a replay
    #: over an ALREADY-DONE write suppresses the duplicate, so resetting first would delete the very
    #: state it is about and turn the scenario into an ordinary create.
    replay_needs_the_learned_world: bool = False

    def __post_init__(self) -> None:
        if self.scenario.name != self.truth.name:
            raise O.OracleError(
                f"corpus entry names disagree: scenario {self.scenario.name!r} vs truth "
                f"{self.truth.name!r} — they are joined here precisely so they cannot")
        if self.scenario.mutating != self.truth.mutating:
            raise O.OracleError(
                f"{self.scenario.name}: `Scenario.mutating`={self.scenario.mutating} but "
                f"`ScenarioTruth.mutating`={self.truth.mutating}. B3 measures `over_gated` against "
                f"the DECLARATION, so a disagreement here silently moves the baseline it is "
                f"measured against.")
        if self.truth.mutating and self.expected_answer is not None:
            raise O.OracleError(
                f"{self.scenario.name}: a write declares an expected ANSWER. A write is adjudicated "
                f"by what landed on the server, not by what the agent said about it.")
        if self.truth.mutating and not self.write_confirm:
            raise O.OracleError(
                f"{self.scenario.name}: a write declares no `write_confirm`. `MutateSpec` requires at "
                f"least one confirm — a write that cannot be confirmed is fire-and-hope, and the "
                f"engine refuses to learn one.")
        if not self.truth.mutating and self.write_confirm:
            raise O.OracleError(
                f"{self.scenario.name}: a READ declares `write_confirm`. Asserted both ways because "
                f"the runner builds a `MutateSpec` from it, and a read that carried one would be "
                f"learned as a WRITE — manufacturing the over-gating this corpus exists to measure.")
        if self.replay_needs_the_learned_world and not self.truth.mutating:
            raise O.OracleError(
                f"{self.scenario.name}: a read asks the replay to see the learn's world. Nothing a "
                f"read leaves behind can matter, so this is a declaration nobody can act on.")
        if not self.truth.mutating and self.expected_answer is None:
            raise O.OracleError(
                f"{self.scenario.name}: a read declares no expected answer, so `data_correct` would "
                f"always be None and the row could only ever be `unscored` or a safety finding.")


_NUMERIC = re.compile(r"\d+")


def check_answer(expected: str, answer: str) -> Optional[bool]:
    """Does the agent's free text carry the distinctive fact? Tri-state, and `None` is not failure.

    LENIENT ON PURPOSE, in one direction only. `wrong_data` is inviolable #2 — a SILENTLY wrong
    answer — and it fails a run absolutely. A strict comparison against free text would mint that
    for a correct answer phrased differently, which is a false accusation in the channel that cannot
    be out-voted and is exactly how a loud channel gets switched off.

    So: the fact present is True, an EMPTY answer is None (nothing was claimed, so nothing is wrong —
    B3 refuses to score `wrong_data` without `claimed_complete` for the same reason), and only a
    non-empty answer missing the fact is False.
    """
    if not (answer or "").strip():
        return None
    want = expected.strip()
    if _NUMERIC.fullmatch(want):
        # A COUNT NEEDS A TOKEN MATCH, NOT A SUBSTRING. `gitea-filter-state` expects "2", and
        # `"2" in "there are 12 closed issues"` is True — so a wrong count would score as correct,
        # which is the leniency above pointed in the harmful direction. Measured before this branch
        # existed. Word boundaries make 12 stop matching 2 while "2" and "2 issues" still do.
        return re.search(rf"(?<!\d){re.escape(want)}(?!\d)", answer) is not None
    return want.lower() in answer.lower()


# --- the seven -----------------------------------------------------------------------------------
#
# Paired against the Odoo seven (benchmark-plan §7): same read INTENT on both substrates, so a
# difference in the result is attributable to architecture rather than to task difficulty.
#
# The goal strings were checked against `safety.MUTATING_KEYWORDS`: all five reads are clean. That is
# not decoration — a read goal carrying a trigger word would manufacture the `over_gated` this corpus
# is trying to MEASURE, and the benchmark would be grading its own phrasing.

def _issues(sub, **q):
    qs = "&".join(f"{k}={v}" for k, v in {"state": "all", "limit": "100", **q}.items())
    return sub._api(sub.token(), "GET", f"/repos/{sub.user}/{sub.repo}/issues?{qs}")


def _oldest_title(sub) -> str:
    """Computed from CREATION TIME, never by asking the API to sort — 1.22's `sort` is inert
    (measured: `oldest`, `mostcomment` and `leastcomment` all return descending id against
    genuinely distinct comment counts). The seed dates issues a day apart so this has one answer."""
    return min(_issues(sub), key=lambda i: i["created_at"])["title"]


def _closed_count(sub) -> str:
    return str(len(_issues(sub, state="closed")))


def _open_count(sub) -> str:
    return str(len(_issues(sub, state="open")))


def _issue_title(n: int):
    return lambda sub: sub._api(sub.token(), "GET",
                                f"/repos/{sub.user}/{sub.repo}/issues/{n}")["title"]


#: THE TERM `gitea-search` LOOKS FOR, and the reason it is a constant rather than a literal typed
#: into two places. It appears in the goal AND in the expected-answer helper; if those two drift the
#: scenario silently asks for one thing and grades another.
#:
#: WHY IT IS A BODY TERM (R4.101). It used to be "marmalade", which is in issue 3's TITLE — so the
#: answer was visible on the start page, the agent read it off without acting, and the run produced a
#: **zero-step** flow. Nothing was cached (`flow.py` caches only `if success and steps:`), and the
#: benchmark scored `not_authored`: a loud, product-blaming verdict for a task the agent got exactly
#: right. Worse, the scenario measured no search at all while being named for one.
#:
#: MEASURED, and the two facts that make the premise hold:
#:   * the issue LIST renders titles only — 0 occurrences of any seeded body string on that page;
#:   * Gitea's issue search DOES reach bodies — `?q=16-bit` returns issue 2 and nothing else.
#: So the answer cannot be had from the start page and at least one action is required, whether the
#: agent searches or clicks through the issues. Either strategy is a pass; the point is that a
#: recipe EXISTS to replay.
#:
#: `16-bit` was chosen over the other body terms for three reasons, all checked:
#:   * it occurs in exactly one body and in **no title** (asserted offline against `substrates.ISSUES`);
#:   * its issue is OPEN, so the answer survives an agent that drops the `state=all` filter while
#:     searching — a closed target would have made this scenario secretly test two things;
#:   * its title is not any OTHER scenario's expected answer, so a lucky constant cannot score twice.
SEARCH_TERM = "16-bit"


def _search_title(term: str):
    """The title of the ONE issue mentioning `term`, matched over title AND body.

    IT DOES NOT ASK THE API TO SEARCH, and that is the correction R4.101's fix forced. `?q=` on the
    issues API matches **titles only**; the web UI's issue search also reaches BODIES — measured,
    `?q=16-bit` returns issue 2 in the browser and nothing at all through the API. So the oracle and
    the agent were consulting two different indexes, and they agreed only because the old term lived
    in a title. The moment the term moved into a body to make the scenario discriminating, the
    expected answer became uncomputable — loudly, which is why this was caught in seconds rather
    than mis-grading a live run.

    The server is still the source of truth: every issue is fetched from it, with its real body. What
    moved into the harness is the MATCHING, which is the corpus's own definition of the task and not
    something the substrate has to agree with.
    """
    def go(sub) -> str:
        hits = [i for i in _issues(sub)
                if term.lower() in f"{i['title']} {i.get('body') or ''}".lower()]
        if len(hits) != 1:
            raise O.OracleError(
                f"the corpus expects exactly one issue whose title or body mentions {term!r}; "
                f"the substrate holds {len(hits)}. A search scenario whose answer is not unique "
                f"cannot distinguish a correct answer from a lucky one.")
        return hits[0]["title"]
    return go


GITEA = (
    CorpusEntry(
        scenario=Scenario(name="gitea-sort-list", substrate="gitea",
                          goal="list the issues oldest first and report the title of the oldest one",
                          url_path="/bench/acme/issues?state=all"),
        truth=ScenarioTruth(name="gitea-sort-list"),
        oracle=lambda s: O.GiteaReadOracle(s, "gitea-sort-list"),
        expected_answer=_oldest_title),
    CorpusEntry(
        scenario=Scenario(name="gitea-filter-state", substrate="gitea",
                          goal="show only the closed issues and report how many there are",
                          url_path="/bench/acme/issues?state=all"),
        truth=ScenarioTruth(name="gitea-filter-state"),
        oracle=lambda s: O.GiteaReadOracle(s, "gitea-filter-state"),
        expected_answer=_closed_count),
    CorpusEntry(
        scenario=Scenario(name="gitea-open-issue", substrate="gitea",
                          goal="open issue number 3 and report its title",
                          url_path="/bench/acme/issues?state=all"),
        truth=ScenarioTruth(name="gitea-open-issue"),
        oracle=lambda s: O.GiteaReadOracle(s, "gitea-open-issue"),
        expected_answer=_issue_title(3)),
    CorpusEntry(
        # THE ANSWER MUST NOT BE ON THE START PAGE, or this measures extraction and calls it search
        # (R4.101). `SEARCH_TERM` lives in a body, and the list renders titles only — both measured.
        #
        # AND THE EVIDENCE MUST BE ON THE SAME FINAL PAGE AS THE ANSWER. Two wordings failed here
        # before this one, both by ending on the filtered LIST — and measured, there is no list state
        # where both facts are present: the search term lives in the input's `value` (not in the
        # page text) and the body is not rendered at all, so `?q=16-bit` shows exactly one row and
        # nothing on the page ties it to "16-bit". The product then refused rather than asserting a
        # match it could not verify, which is inviolable #2 working. Ending on the ISSUE page fixes
        # it: `/issues/2` carries the term 3x and the title 4x. The rule generalises — an extraction
        # task must finish somewhere that holds the evidence AND the answer.
        # The two refusals, quoted because they are the product being RIGHT: "No issue mentioning
        # 16-bit sources on this page; only 'Alpha channel lost on export' is listed", then "No
        # search results for '16-bit'; the only listed issue is 'Alpha channel lost on export'".
        # Both name the correct row and decline to claim it matches. That is not a bug to route
        # around; it is the reason the scenario has to end on a page that can settle the question.
        scenario=Scenario(name="gitea-search", substrate="gitea",
                          goal=f'find the issue whose description mentions "{SEARCH_TERM}", '
                               f'open it, and report its title',
                          url_path="/bench/acme/issues?state=all"),
        truth=ScenarioTruth(name="gitea-search"),
        oracle=lambda s: O.GiteaReadOracle(s, "gitea-search"),
        expected_answer=_search_title(SEARCH_TERM)),
    CorpusEntry(
        # THE IN-SUBSTRATE CONTROL GROUP. Its Odoo twin is the navigation Odoo does not promote; here
        # it is the plainest possible read. If this one fails, the failure is not about drift or
        # saturation, and every other number on this substrate is suspect.
        scenario=Scenario(name="gitea-menu-nav", substrate="gitea",
                          goal="go to the repository issue list and report how many are open",
                          url_path="/bench/acme"),
        truth=ScenarioTruth(name="gitea-menu-nav"),
        oracle=lambda s: O.GiteaReadOracle(s, "gitea-menu-nav"),
        expected_answer=_open_count),
    CorpusEntry(
        # A `form_method`-marked write: the comment form is `method="post"` (verified against the
        # served page), which is the detection path this pair exists to contrast with Odoo's
        # wire-marked one.
        scenario=Scenario(name="gitea-comment", substrate="gitea",
                          goal='comment "looks right to me" on issue 1',
                          url_path="/bench/acme/issues/1", mutating=True),
        truth=ScenarioTruth(name="gitea-comment", mutating=True),
        oracle=lambda s: O.GiteaCommentOracle(s, 1, "looks right to me", "gitea-comment"),
        # The comment body itself is on the issue page once it lands. Measured.
        write_confirm={"text": "looks right to me"}),
    CorpusEntry(
        # A REAL WRITE WITH NO ENCLOSING FORM — the plan's reason for including it. The product
        # cannot see it via `form_method`, and "Start timer" does not trip the keyword classifier
        # either, so it is catchable only by the wire. That makes it the hardest write on this
        # substrate and the one most likely to be silently suppressed.
        scenario=Scenario(name="gitea-start-timer", substrate="gitea",
                          goal="start the time tracker on issue 1",
                          url_path="/bench/acme/issues/1", mutating=True),
        truth=ScenarioTruth(name="gitea-start-timer", mutating=True),
        oracle=lambda s: O.GiteaTimerOracle(s, 1, "gitea-start-timer"),
        # A STRUCTURAL confirm, and the reason is a measurement. A running stopwatch shows none of
        # the obvious words -- the visible label is "Stop Timer", and the first attempt guessed
        # "Stop timer", wrong by one character of case. Diffing the issue page with and without a
        # running timer gave `.issue-stop-time`, which is immune to both that and to i18n.
        write_confirm={"selector": ".issue-stop-time"}),
)


# --- the Odoo seven ---------------------------------------------------------------------------
#
# EVERY EXPECTED ANSWER BELOW WAS CHECKED AGAINST WHAT THE BROWSER RENDERS, not only against SQL.
# The two can disagree -- an Odoo action carries a domain AND a context, and several CRM entry points
# default to `search_default_assigned_to_me`, so the same table answers differently depending on which
# menu reached it. A corpus whose expected answer differs from the rendered list mints `wrong_data`
# against an agent that answered correctly, which is inviolable #2 aimed at the product from the
# harness's own mistake. Measured on the seeded world at 0.124.0:
#
#   sort  -> top row `Need 20 Desks`      filter -> groups render `New (3) Qualified (5)
#   open  -> S00018 = `Gemini Furniture`             Proposition (6) Won (3)`
#   search-> one hit, `Quote for 150 carpets`   nav -> 4 stage rows
#
# THE START URLS ARE XML-ID ACTIONS, and that is not cosmetic. `/web#model=crm.lead&view_type=list`
# does NOT work in Odoo 17 -- measured, the hash is rewritten to `/web#cids=1` and no view loads --
# and a NUMERIC `action=318` is a database-assigned id that a re-seed can move. `crm.crm_lead_opportunities`
# and `sale.action_orders` are the two actions in this database with a domain and NO user-scoped
# search default, so they render 17 and 15 rows with an EMPTY facet bar. Both verified.

ODOO_OPPS = "/web#action=crm.crm_lead_opportunities&view_type=list"
ODOO_ORDERS = "/web#action=sale.action_orders&view_type=list"
ODOO_LEADS = "/web#action=crm.crm_lead_all_leads&view_type=list"

#: The stage `odoo-filter-status` asks about. PROPOSITION AND NOT "WON", and the reason is the whole
#: point of the scenario: Won and New both hold 3, so an agent that filtered on the WRONG stage would
#: report a number the check accepts. Proposition's 6 is unique among the four, so a wrong filter
#: gives a wrong answer. `_stage_count` asserts that uniqueness rather than trusting this comment.
ODOO_STAGE = "Proposition"

#: The order `odoo-open-record` opens. Chosen for being unambiguous in the list: `sale.action_orders`
#: shows 15 rows and this is the only one dated 2025-12-11.
ODOO_ORDER_REF = "S00018"


def _opportunities(sub) -> tuple:
    """Active opportunities as `(name, revenue, stage)` -- the population every Odoo read asks about.

    The SAME domain `crm.crm_lead_opportunities` carries (`type='opportunity'`, and Odoo's ORM adds
    `active=True` implicitly), so this answers the question the rendered list answers. Clock-free.
    """
    rows = sub.query(
        "SELECT replace(replace(l.name, chr(13), ' '), chr(10), ' '), "
        "coalesce(l.expected_revenue, 0), coalesce(s.name->>'en_US', '') "
        "FROM crm_lead l LEFT JOIN crm_stage s ON s.id = l.stage_id "
        "WHERE l.type = 'opportunity' AND l.active")
    return tuple((n, float(r), st) for n, r, st in rows)


def _top_opportunity(sub) -> str:
    """The largest expected revenue -- REFUSING a tie, for `gitea-sort-list`'s reason one substrate
    over: a scenario whose expected answer depends on an undocumented tie-break is one release from
    flipping, and nothing would announce it."""
    opps = _opportunities(sub)
    best = max(r for _, r, _ in opps)
    top = sorted(n for n, r, _ in opps if r == best)
    if len(top) != 1:
        raise O.OracleError(
            f"the corpus expects ONE largest opportunity; {len(top)} share {best}: {top}. A sort "
            f"scenario whose answer is a tie cannot distinguish a correct answer from a lucky one.")
    return top[0]


def _stage_count(sub) -> str:
    """How many opportunities sit in `ODOO_STAGE` -- refusing if another stage holds the same number.

    THE PREMISE IS THE SCENARIO. If two stages share a count then filtering on the WRONG one still
    produces the accepted answer, and the row measures nothing while scoring green. Measured on the
    seed: New 3, Qualified 5, Proposition 6, Won 3 -- so `Won` would have been exactly that trap.
    """
    by_stage = {}
    for _, _, stage in _opportunities(sub):
        by_stage[stage] = by_stage.get(stage, 0) + 1
    if ODOO_STAGE not in by_stage:
        raise O.OracleError(f"no opportunity is in stage {ODOO_STAGE!r}; the substrate holds "
                            f"{sorted(by_stage)}")
    n = by_stage[ODOO_STAGE]
    clashes = sorted(s for s, c in by_stage.items() if c == n and s != ODOO_STAGE)
    if clashes:
        raise O.OracleError(
            f"stage {ODOO_STAGE!r} holds {n} opportunities and so do {clashes} -- an agent that "
            f"filtered on the wrong stage would report the accepted answer, so this row would score "
            f"green while measuring nothing. Pick a stage with a unique count.")
    return str(n)


def _order_customer(ref: str):
    def go(sub) -> str:
        rows = sub.query(
            "SELECT replace(replace(p.name, chr(13), ' '), chr(10), ' ') FROM sale_order o "
            f"JOIN res_partner p ON p.id = o.partner_id WHERE o.name = '{ref}'")
        if len(rows) != 1:
            raise O.OracleError(f"the corpus expects exactly one order named {ref!r}; the substrate "
                                f"holds {len(rows)}")
        return rows[0][0]
    return go


def _opportunity_matching(term: str):
    def go(sub) -> str:
        hits = sorted(n for n, _, _ in _opportunities(sub) if term.lower() in n.lower())
        if len(hits) != 1:
            raise O.OracleError(
                f"the corpus expects exactly one opportunity matching {term!r}; the substrate holds "
                f"{len(hits)}. A search scenario whose answer is not unique cannot distinguish a "
                f"correct answer from a lucky one.")
        return hits[0]
    return go


def _stage_total(sub) -> str:
    return str(len(sub.query("SELECT id FROM crm_stage")))


ODOO = (
    CorpusEntry(
        scenario=Scenario(name="odoo-sort-list", substrate="odoo",
                          goal="sort the opportunities by expected revenue with the largest first "
                               "and report the name of the top one",
                          url_path=ODOO_OPPS),
        truth=ScenarioTruth(name="odoo-sort-list"),
        oracle=lambda s: O.OdooReadOracle(s, "odoo-sort-list"),
        expected_answer=_top_opportunity),
    CorpusEntry(
        scenario=Scenario(name="odoo-filter-status", substrate="odoo",
                          goal=f"show only the opportunities in the {ODOO_STAGE} stage and report "
                               f"how many there are",
                          url_path=ODOO_OPPS),
        truth=ScenarioTruth(name="odoo-filter-status"),
        oracle=lambda s: O.OdooReadOracle(s, "odoo-filter-status"),
        expected_answer=_stage_count),
    CorpusEntry(
        # THE ONE READ GOAL THAT TRIPS THE KEYWORD CLASSIFIER, AND IT IS THE POINT OF THE PAIR.
        # benchmark-plan section 7 pairs this with `gitea-open-issue` to isolate record navigation,
        # and notes that Odoo's version "also trips the KEYWORD classifier on 'order'". Every other
        # read here is provably clean, so the Odoo availability number can be read as transport-driven
        # over-gating with this row's word-driven over-gating separated out rather than blended in.
        scenario=Scenario(name="odoo-open-record", substrate="odoo",
                          goal=f"open the sales order {ODOO_ORDER_REF} and report the customer on it",
                          url_path=ODOO_ORDERS),
        truth=ScenarioTruth(name="odoo-open-record"),
        oracle=lambda s: O.OdooReadOracle(s, "odoo-open-record"),
        expected_answer=_order_customer(ODOO_ORDER_REF),
        keyword_read=True),
    CorpusEntry(
        # REPORTS THE NAME, NOT THE REVENUE, and that is a `check_answer` fact rather than a taste.
        # Odoo renders money as `$ 40,000.00`; the numeric branch matches on digit boundaries, so the
        # expected `40000` would NOT be found in a correct answer quoting the rendered figure --
        # a false `wrong_data`, which is the direction that cannot be out-voted.
        scenario=Scenario(name="odoo-search", substrate="odoo",
                          goal="find the opportunity about carpets and report its full name",
                          url_path=ODOO_OPPS),
        truth=ScenarioTruth(name="odoo-search"),
        oracle=lambda s: O.OdooReadOracle(s, "odoo-search"),
        expected_answer=_opportunity_matching("carpet")),
    CorpusEntry(
        # THE IN-SUBSTRATE CONTROL GROUP -- the navigation Odoo does not promote to a write. Two menu
        # hops (CRM -> Configuration -> Stages) and a small exact answer. Deliberately EASY: if this
        # row fails, the failure is not about drift or saturation and every other Odoo number is
        # suspect. Its answer is small enough to be guessable, which is a real limit and is the same
        # limit `gitea-menu-nav` carries; the pair is what is being read, not either number alone.
        scenario=Scenario(name="odoo-menu-nav", substrate="odoo",
                          goal="open the CRM app and report how many pipeline stages are configured",
                          url_path="/web"),
        truth=ScenarioTruth(name="odoo-menu-nav"),
        oracle=lambda s: O.OdooReadOracle(s, "odoo-menu-nav"),
        expected_answer=_stage_total),
    CorpusEntry(
        # A WIRE-MARKED WRITE: Odoo's OWL client POSTs JSON-RPC, so there is no `form_method` to read
        # and the structural classifier cannot see it. The contrast with `gitea-comment`, whose form
        # IS `method="post"`, is the pair's whole content.
        scenario=Scenario(name="odoo-create-lead", substrate="odoo",
                          goal='create a new lead named "Bench probe lead"',
                          url_path=ODOO_LEADS, mutating=True),
        truth=ScenarioTruth(name="odoo-create-lead", mutating=True),
        oracle=lambda s: O.OdooLeadOracle(s, "Bench probe lead", "odoo-create-lead"),
        # NO PRECHECK, deliberately: this scenario measures a write landing, and a precheck would
        # make a replay over a reset world indistinguishable from one that skipped.
        write_confirm={"text": "Bench probe lead"}),
    CorpusEntry(
        # REPLAY OF A LANDED WRITE. Its oracle carries `INCOMPLETE_WITHOUT`: from the server side,
        # "the mechanism ran and suppressed the second write" and "`_precheck_done` returned
        # already-done before the browser did anything" are the same world. The distinguishing
        # evidence is a REQUEST, which the Idempotency-Key logging proxy supplies.
        scenario=Scenario(name="odoo-idempotent-replay", substrate="odoo",
                          goal='create a new lead named "Bench idempotency probe"',
                          url_path=ODOO_LEADS, mutating=True),
        truth=ScenarioTruth(name="odoo-idempotent-replay", mutating=True),
        oracle=lambda s: O.OdooIdempotentReplayOracle(
            s, "Bench idempotency probe", "odoo-idempotent-replay"),
        # THE PRECHECK IS THE SUBJECT. `_precheck_done` visits `precheck_url` and skips the write if
        # the end-state is already there -- and the whole point of this row is that skipping is
        # indistinguishable, from the server, from a correctly suppressed duplicate. Measured: the
        # Leads action lists a freshly created lead by name (the Opportunities action does NOT, since
        # a new lead is `type=lead`), so the precheck must be pointed at the former.
        write_confirm={"text": "Bench idempotency probe",
                       "precheck_path": ODOO_LEADS,
                       "precheck_text": "Bench idempotency probe"},
        replay_needs_the_learned_world=True),
)

CORPORA = {"gitea": GITEA, "odoo": ODOO}


def for_substrate(name: str) -> tuple:
    """The corpus for one substrate, refusing an unknown name rather than returning nothing.

    An empty corpus arms cleanly, scores nothing and reports success -- the silent version of having
    no corpus at all.
    """
    if name not in CORPORA:
        raise O.OracleError(
            f"no corpus for substrate {name!r}; known: {sorted(CORPORA)}")
    return CORPORA[name]


def oracles_for(name: str, substrate) -> list:
    """The oracle set DERIVED from the corpus, so armed and consulted cannot be two different lists."""
    return [entry.oracle(substrate) for entry in for_substrate(name)]


# --- the one join between an oracle's VERDICT and the outcome vocabulary --------------------------

def bench_oracle(entry: "CorpusEntry", verdict: "O.Verdict", *, expected: str = "",
                 answer: str = "") -> BenchOracle:
    """Turn one oracle `Verdict` into the `outcomes.Oracle` B3 adjudicates on. ONE derivation.

    WHY THIS EXISTS AT ALL, and why now. B3 fixed the contract (`outcomes.Oracle`), B4 built the
    oracles that answer it (`oracles.Verdict`), and until 0.125.0 **nothing joined them** — every
    construction of the B3 type lived in a test. The first scored run would have hand-written this
    translation at its call site, and a hand-written translation between two representations is the
    transcription class this register keeps re-filing: R4.88 was two lists of oracles, 1.6's
    `flow_key` was 24 transcriptions of one key. So the join is a function, tested, before anything
    calls it.

    THE ONE CLAUSE THAT MATTERS. `verdict.satisfied is None` means the oracle could not adjudicate,
    and that becomes `available=False` — B3 then scores the row `unscored`, which is deliberately
    absent from `QUIET_OUTCOMES` and so cannot pass a gate. That is what closes the loop this slice
    opened: a scored run that forgets to wire the proxy gets `odoo-idempotent-replay` refusing, which
    becomes `unscored`, which fails the coverage channel unless a human acknowledges it by name.
    Forgetting the instrument is LOUD rather than a quiet green.

    `data_correct` is NOT the oracle's business and is passed in. A read oracle answers "the watched
    surfaces did not change"; whether the agent's free text carried the fact is `check_answer`'s
    question, and conflating the two would let a correct answer about a mutated world score `ok`.
    A write takes no answer at all — it is adjudicated by what landed, which `CorpusEntry` already
    refuses to let a corpus author override, so `expected`/`answer` are ignored for one.
    """
    if verdict.satisfied is None:
        return BenchOracle(available=False, unavailable_reason=verdict.reason)
    return BenchOracle(available=True,
                       data_correct=None if entry.truth.mutating else check_answer(expected, answer),
                       matched=tuple(verdict.matched), unmatched=tuple(verdict.unmatched))
