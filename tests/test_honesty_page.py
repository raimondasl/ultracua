"""The customer benchmark's honesty page cannot go stale as the register moves. (2.4 / B5.)

WHAT AN HONESTY PAGE IS FOR, and why it needs a test at all. `baselines/README.md`'s standing job is
to say, per number, what it does and does not prove. For the customer benchmark a large part of that
is defects: `gitea-start-timer` scores 0/3 because of a filed grounding limit, and Odoo has no
baseline at all because a filed over-marking dominates its refusals. Those sentences are TRUE TODAY
and each of them stops being true the moment its finding is fixed — at which point the page is
quietly telling a reader that a number means something it no longer means. Nothing would say so:
prose does not fail a test, and the register is edited by different slices from the page.

So the block is machine-checked against `docs/register/state.json`, BOTH WAYS:

  * every declared id must still be OPEN. A fixed one means the caveat is stale and the number it
    qualifies may have moved;
  * every OPEN finding cited anywhere in the honesty region must be declared. Citing one in prose
    without declaring it is how a live caveat ends up in the narrative and outside the checked list —
    which is this repo's "quiet is an allowlist" rule (R3.9/CLI-1) wearing a documentation hat.

WHAT IT DELIBERATELY DOES NOT ASSERT: that every open finding in the register appears here. There are
49, and most say nothing about this benchmark. A list that had to name all of them would be noise,
and noise is how a channel stops being read. The declared set is a judgement; what the test removes
is the chance of that judgement silently rotting.

A FIXED finding may still be cited as HISTORY — R4.106 is, in the paragraph explaining why
`cost_usd` is the maximum observed rather than the mean. That is why the forward direction is scoped
to OPEN ids rather than to every id in the text.

Nothing here launches a browser or spends anything: it is two files and a regex.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from _arming import assert_red
from benchmarks import outcomes

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "baselines" / "README.md"
REGISTER = ROOT / "docs" / "register" / "state.json"

REGION_OPEN = "<!-- honesty:customer-bench -->"
REGION_CLOSE = "<!-- /honesty:customer-bench -->"
BLOCK_OPEN = "<!-- open-findings:customer-bench -->"
BLOCK_CLOSE = "<!-- /open-findings -->"

_ID = re.compile(r"\bR\d+\.\d+\b")


def _module_strings(module) -> set:
    """Every string constant the module's compiled code objects hold, transitively.

    The gate compares a reason string that is written as adjacent literals across several source
    lines; Python folds those at compile time, so the whole string exists in exactly one
    `co_consts` entry and NOWHERE contiguously in the source text. A scan over `getsource` therefore
    reports a perfectly good acknowledgement as stale -- which is what the first draft did.
    """
    import types

    out, seen = set(), set()

    def walk(code) -> None:
        if id(code) in seen:
            return
        seen.add(id(code))
        for c in code.co_consts:
            if isinstance(c, str):
                out.add(c)
            elif isinstance(c, types.CodeType):
                walk(c)

    for obj in vars(module).values():
        fn = getattr(obj, "__code__", None)
        if fn is not None:
            walk(fn)
        elif isinstance(obj, type):
            for m in vars(obj).values():
                if getattr(m, "__code__", None) is not None:
                    walk(m.__code__)
    return out


def register_status() -> "dict[str, str]":
    data = json.loads(REGISTER.read_text(encoding="utf-8"))
    return {f["id"]: f["status"] for f in data["findings"]}


def region(text: str) -> str:
    """The honesty region's body — the ONE definition, shared with the arming cell.

    Delimited by explicit markers rather than by "from the `## customer_v1` heading to the end of the
    file", which is the same thing until somebody appends a section and the scan silently swallows
    it. Two copies of this predicate is how an arming proof ends up proving something the real check
    does not do, which is why `refused_hits` is shared one file over.
    """
    assert text.count(REGION_OPEN) == 1 and text.count(REGION_CLOSE) == 1, (
        f"the honesty region markers are not a matched pair in {PAGE.name}; this scan reads nothing"
    )
    return text[text.index(REGION_OPEN):text.index(REGION_CLOSE)]


def declared(text: str) -> "list[str]":
    """The ids the page DECLARES as live caveats, in order, from the machine-readable block."""
    body = region(text)
    assert BLOCK_OPEN in body and BLOCK_CLOSE in body, (
        f"the open-findings block is missing from the honesty region in {PAGE.name}"
    )
    inner = body[body.index(BLOCK_OPEN) + len(BLOCK_OPEN):body.index(BLOCK_CLOSE)]
    return list(dict.fromkeys(_ID.findall(inner)))


def cited(text: str) -> "list[str]":
    """Every finding id mentioned in the honesty region, block included."""
    return list(dict.fromkeys(_ID.findall(region(text))))


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_the_block_is_not_empty(page: str) -> None:
    """Anti-vacuity, first, because both properties below are satisfied by an empty list."""
    ids = declared(page)
    assert len(ids) >= 3, (
        f"the honesty block declares only {ids}. Either the markers have stopped matching — in "
        f"which case every assertion here is vacuous — or the customer benchmark has acquired no "
        f"caveats at all, which given a corpus with a 0/3 row and an absent substrate is not "
        f"credible."
    )
    assert len(ids) == len(set(ids)), f"an id is declared twice: {ids}"


def test_every_declared_caveat_is_still_open(page: str) -> None:
    """The direction that ROTS. A fixed finding makes its paragraph a lie by omission."""
    status = register_status()
    unknown = [i for i in declared(page) if i not in status]
    assert not unknown, (
        f"the honesty page declares {unknown}, which the register has never heard of. Either the id "
        f"is a typo — in which case the caveat is unchecked — or the finding was renumbered."
    )
    closed = {i: status[i] for i in declared(page) if status[i] != "open"}
    assert not closed, (
        f"these caveats are declared live and their findings are no longer open: {closed}. The "
        f"number each one qualifies may have moved, so the honest action is to RE-MEASURE and "
        f"rewrite the paragraph, not to delete the line. If the caveat is genuinely historical, say "
        f"so in prose outside the block — R4.106 is cited that way already."
    )


def test_every_open_finding_the_page_cites_is_declared(page: str) -> None:
    """The other direction. Quiet is an allowlist: a new caveat is declared or the test is red."""
    status = register_status()
    live = [i for i in cited(page) if status.get(i) == "open"]
    missing = sorted(set(live) - set(declared(page)))
    assert not missing, (
        f"these OPEN findings are cited in the honesty region but not declared in its block: "
        f"{missing}. A caveat that lives only in prose is one nothing can fail for — add it to the "
        f"block with a line on what it does to these numbers, or, if it is being mentioned as "
        f"history rather than as a live limit, say that explicitly."
    )


def test_the_baseline_the_page_describes_is_the_one_on_disk(page: str) -> None:
    """The page is about an ARTIFACT, so the artifact has to exist and match what is claimed.

    Cheap, and it closes the gap that would otherwise make every assertion above true of a file
    nobody ships. The numbers are checked rather than the prose paraphrased: `0.762` and `n=21`
    appear in the text, and both come from the JSON.
    """
    # DERIVED FROM DISK, not named. This cell hard-coded `customer_v1_gitea.json` while Gitea was
    # the only baseline; the Odoo half (2.4b) then shipped as a SECOND artifact, and a named check
    # would have gone on passing while saying nothing about it. Globbing means a third substrate's
    # baseline cannot ship undocumented either. `.acknowledged.` files are excluded: they are the
    # gate's allowlist, not a baseline, and they carry no metrics to quote.
    found = sorted(q for q in (ROOT / "baselines").glob("customer_v1_*.json")
                   if ".acknowledged." not in q.name)
    assert found, (
        "no `baselines/customer_v1_*.json` exists, so this whole page describes nothing. Either the "
        "baselines were renamed — in which case this cell is now vacuous and must follow them — or "
        "an artifact the honesty region is built on has been deleted."
    )
    body = region(page)
    for artifact in found:
        data = json.loads(artifact.read_text(encoding="utf-8"))
        m = data["metrics"]["availability_rate"]
        assert artifact.name in body, (
            f"`{artifact.name}` is committed but the honesty region never names it. A baseline that "
            f"nothing on this page describes is a number shipped without its caveats."
        )
        assert f"{m['mean']:.3f}" in body, (
            f"the page does not quote {artifact.name}'s availability_rate ({m['mean']:.3f}); it has "
            f"drifted from the artifact it describes"
        )
        assert f"n={m['n']}" in body, (
            f"the page does not quote {artifact.name}'s n ({m['n']})"
        )

    # THE GATE'S ALLOWLIST IS PART OF THE ARTIFACT, so a baseline that acknowledges rows must have a
    # committed file to acknowledge them with -- and one that acknowledges nothing must still have
    # the file, holding `[]`. An ABSENT allowlist and an EMPTY one read identically at the call site
    # and mean different things: "nothing is signed for" versus "nobody has looked".
    for artifact in found:
        ack = artifact.with_name(artifact.name.replace(".json", ".acknowledged.json"))
        assert ack.exists(), (
            f"`{artifact.name}` has no `{ack.name}`. The weekly job passes `--acknowledge` for every "
            f"substrate so the two legs stay one shape; an empty list is how a baseline says it "
            f"signs for nothing, and an absent file is how one silently stops being checked."
        )
        pairs = json.loads(ack.read_text(encoding="utf-8"))
        assert isinstance(pairs, list) and all(
            isinstance(x, list) and len(x) == 2 for x in pairs), (
            f"`{ack.name}` must be a list of [scenario, reason] pairs; `corpus_run` feeds it "
            f"straight to `gate_bench_record(acknowledged=...)`, which keys on the tuple."
        )

        # THE PAIR IS KEYED ON THE REASON STRING, so a reword in `benchmarks/outcomes.py` silently
        # stops it matching and the acknowledged row starts failing the weekly gate for a reason the
        # operator cannot see. Nothing bound the two before: the reason is a bare literal, not a
        # named constant, and it was copied into the committed file by hand. Binding it here makes a
        # reword a red test in the slice that does the rewording, rather than a red benchmark a week
        # later.
        #
        # MATCHED AGAINST THE COMPILED CONSTANTS, NOT THE SOURCE TEXT, and the first draft got that
        # wrong. The reason is written as three adjacent string literals across three lines, so it
        # does not appear contiguously in `getsource` at all -- the scan went red against a perfectly
        # good acknowledgement. Python folds adjacent literals at COMPILE time, so the whole string
        # is one entry in a code object's `co_consts`, which is also what the running gate compares.
        # (This is the repo's "never assert over source TEXT" rule arriving on a tenth surface.)
        for scenario, reason in pairs:
            assert reason in _module_strings(outcomes), (
                f"`{ack.name}` acknowledges {scenario!r} by a reason string that "
                f"`benchmarks/outcomes.py` no longer produces. The gate matches the "
                f"(scenario, reason) PAIR verbatim, so this acknowledgement is now inert and that "
                f"row will fail the weekly run with nothing saying why. Re-copy the reason from a "
                f"record, do not paraphrase it.\n  committed: {reason!r}"
            )


def test_both_directions_go_red_when_armed(page: str) -> None:
    """A cell that cannot fail is not a test, and all three above assert a NEGATIVE about a list.

    Each mutation asserts it CHANGED something first — a find-text that no longer matches reports
    this cell as stronger than it is, which is the rule `prove_red` applies to a stale mutation.
    """
    # (a) declare a caveat whose finding is FIXED -> the staleness cell must fire. R4.106 is the
    # real example: it is cited above as history and is `fixed`, so promoting it into the block is
    # exactly the mistake this guards.
    assert register_status()["R4.106"] == "fixed", (
        "R4.106 is no longer fixed, so this mutation no longer states the property; pick another "
        "closed finding that the page cites"
    )
    stale = page.replace(BLOCK_OPEN + "\n", BLOCK_OPEN + "\n* **R4.106** — armed.\n", 1)
    assert stale != page, "the mutation is STALE; the block's opening marker moved"
    assert_red(test_every_declared_caveat_is_still_open, stale)

    # (b) cite an OPEN finding in the region's prose without declaring it -> the other cell fires.
    #
    # THE ID IS DERIVED, NOT TYPED. The first draft named R4.103, which was open when I picked it
    # from memory and `fixed` by the time the cell ran — this arming would then have been testing
    # nothing, in exactly the way the cell it arms exists to prevent. Take any open finding the page
    # does not already cite.
    victim = next((i for i, s in sorted(register_status().items())
                   if s == "open" and i not in cited(page)), None)
    assert victim, (
        "every open finding in the register is already cited in the honesty region, so there is no "
        "undeclared one to smuggle in and this arming cannot state its property"
    )
    smuggled = page.replace(REGION_CLOSE, f"See also {victim}.\n\n" + REGION_CLOSE, 1)
    assert smuggled != page, "the mutation is STALE; the region's closing marker moved"
    assert_red(test_every_open_finding_the_page_cites_is_declared, smuggled)

    # (c) THE ARTIFACT CELL, armed for the first time at 0.172.0. It was hard-coded to Gitea's
    # filename until the Odoo baseline landed and made it a glob, and a glob that matches nothing --
    # or that matches and asserts nothing -- is exactly the vacuity the two cells above are armed
    # against. Three mutations, all applied to the PAGE TEXT rather than to files on disk, because a
    # cell that writes to `baselines/` to arm itself is a cell that can leave the tree broken when it
    # fails. That rules out mutating the JSON, so the artifact-side properties are stated by removing
    # the page's side of the binding instead.
    for name, mutated, why in (
        ("a committed baseline the page never names",
         page.replace("`customer_v1_odoo.json`", "`the Odoo one`"),
         "the region stops naming an artifact that is on disk"),
        # EVERY occurrence, not the first. A one-shot replace left `0.714` standing in four other
        # sentences of the same region and the cell passed -- the mutation was wrong, not the guard,
        # and the arming harness said so. `in body` is satisfied by ANY occurrence, so a mutation
        # that states "the page no longer quotes this number" has to remove all of them.
        ("the page quotes a number the artifact does not carry",
         page.replace("0.714", "0.888"),
         "the quoted availability drifts from the JSON"),
    ):
        assert mutated != page, f"the mutation is STALE ({name}); {why} no longer matches"
        assert_red(test_the_baseline_the_page_describes_is_the_one_on_disk, mutated)

    # (c) THE QUIET DIRECTION. A finding cited OUTSIDE the region — the drift-bench sections above
    # cite plenty — must not be dragged in. Without this the fix for (b) is "declare every id in the
    # file", which would put drift-bench's caveats in the customer benchmark's block.
    outside = f"Historic: {victim}.\n\n" + page
    assert victim not in cited(outside), (
        "an id outside the honesty region leaked into the scan, so this page's block would be "
        "forced to declare caveats belonging to a different benchmark"
    )
    test_every_open_finding_the_page_cites_is_declared(outside)

    # (d) empty the block -> the anti-vacuity cell fires, because (a) and (b) both pass over nothing.
    start = page.index(BLOCK_OPEN) + len(BLOCK_OPEN)
    emptied = page[:start] + "\n" + page[page.index(BLOCK_CLOSE):]
    assert emptied != page, "the mutation is STALE; the block is already empty"
    assert_red(test_the_block_is_not_empty, emptied)
