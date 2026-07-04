"""H7 evals: deterministic control flow — repeat-over-list, paginate-until, branch-on-state.

ROADMAP H7: extend the cached-flow IR from a linear step list to a small CLOSED set of typed
nodes — `LoopStep` (row-template container + body resolved within each row + cardinality bounds +
row-key dedupe), `PaginateStep` (next-control + deterministic termination predicate + max-pages),
branch-on-state predicates — synthesized once at discovery and replayed 0-LLM by an interpreter
with fail-loud guards. Co-requisite: row-scoped 0-LLM cell pins (per-row column reads), because
control flow alone leaves per-page extraction as an LLM call. The probed future surfaces:
cache.py loop/paginate node types, `locators.resolve(root=...)` within-row binding, row/cell pin
APIs on pin.py, loop verbs in the authoring action schema, coverage counts in FlowReport.

Partial credit measured today (the building blocks the interpreter is specified to ride on):
- linear learn->0-LLM replay faithfully unrolls a fixed-N pagination (the documented limit:
  the unroll cannot adapt when the dataset grows — H7's structurally-inexpressible task class)
- `resolve(unique=True)` fail-loud ambiguity (the semantics `unique-within-row` extends)
- scalar pins: verified 0-LLM reads that REFUSE ambiguous and non-scalar values
- `condition_present` selector/text/url predicates (PaginateStep's termination primitive)
- `snapshot.hash_scope` (the list-container fingerprint for the loop precondition)

Everything here is key-less: local Fixture pages, scripted provider, real headless Chromium, $0.
"""

from __future__ import annotations

from evals.core import Ctx, expect, missing, ok, probe, scenario
from evals.fixtures import Fixture, page


class _ClickNextUntilGone:
    """Scripted key-less 'agent' for the pagination fixture: click the 'next page' link while one
    exists, else declare done. The SCRIPT holds the loop — the cached artifact it produces is a
    linear UNROLL (click-next x N), which is exactly the pre-H7 IR limit under measurement."""

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        for el in obs.elements:
            if el.role == "link" and "next" in (el.name or "").lower():
                return Action(action="click", intent="go to the next page of the report", ref=el.ref), None
        return Action(action="done", intent="visited every page of the report"), None


def _rows(*orders: int) -> str:
    return "<ul>" + "".join(f"<li>order {o}</li>" for o in orders) + "</ul>"


def _paginated_fixture() -> Fixture:
    """3-page order report: rows + a 'next page' link, last page has no next (the termination
    state a PaginateStep's `until` predicate would encode deterministically)."""
    return Fixture({
        "/": page("<h1>orders</h1>" + _rows(1001, 1002, 1003) + '<a href="/p2">next page</a>',
                  title="orders p1"),
        "/p2": page("<h1>orders</h1>" + _rows(1004, 1005, 1006) + '<a href="/p3">next page</a>',
                    title="orders p2"),
        "/p3": page("<h1>orders</h1>" + _rows(1007, 1008, 1009), title="orders p3"),
    })


@scenario(
    id="h07.ir.control_flow_nodes",
    title="typed control-flow nodes in the cached-flow IR (LoopStep / PaginateStep / branch-on-state)",
    group="h07", aspirational=True, tags=("control-flow", "ir", "cache"),
    notes="H7 plan step 1: discriminated-union nodes in cache.py, additive per the StepConfirm precedent",
)
async def control_flow_nodes(ctx: Ctx):
    import time

    import ultracua.cache as cache_mod
    from ultracua.cache import SCHEMA_VERSION, CachedStep

    checks = []

    # PARTIAL CREDIT: the additive-evolution precedent the plan explicitly rides on — StepConfirm
    # landed as an Optional field defaulting None so older flows deserialize unchanged, no schema
    # bump. Loop/paginate nodes are specified to join CachedFlow.steps the same additive way.
    fld = CachedStep.model_fields.get("confirm")
    checks.append(expect(fld is not None and fld.default is None,
                         "additive optional-field precedent exists (CachedStep.confirm, no schema bump)"))

    # Control for the refusal probe below: a well-formed LINEAR flow file loads fine, so the
    # unknown-node refusal we measure next is specifically about the node, not a malformed file.
    cache = ctx.cache()
    cache.root.mkdir(parents=True, exist_ok=True)

    def _flow_json(key: str, action: str) -> str:
        import json
        return json.dumps({"key": key, "goal": "g", "start_url": "http://127.0.0.1:9/",
                           "steps": [{"intent": "open", "action": action}],
                           "created_ts": time.time(), "schema_version": SCHEMA_VERSION})

    (cache.root / "0707aaaaaaaaaaaa.json").write_text(_flow_json("0707aaaaaaaaaaaa", "click"),
                                                      encoding="utf-8")
    checks.append(expect(cache.get("0707aaaaaaaaaaaa") is not None,
                         "control: a well-formed linear flow file loads (refusal probe is non-vacuous)"))

    # SHIPPED FAIL-LOUD: a flow whose step carries an unknown node/action type is a MISS on read —
    # it is never half-executed with the unknown step skipped. This is the loud-refusal contract
    # the plan requires unknown FUTURE node types to keep ("unknown node types refuse loudly").
    (cache.root / "0707bbbbbbbbbbbb.json").write_text(_flow_json("0707bbbbbbbbbbbb", "repeat_over"),
                                                      encoding="utf-8")
    checks.append(expect(cache.get("0707bbbbbbbbbbbb") is None,
                         "a flow with an unknown node type is a loud miss, never a partial run"))

    # The typed nodes themselves (H7 plan step 1). Not built yet -> `missing`.
    checks.append(expect(any(hasattr(cache_mod, n) for n in ("LoopStep", "RepeatStep", "LoopNode")),
                         "LoopStep node type exists (repeat-over-rows container + body + cardinality)",
                         "no loop node in ultracua.cache", aspirational=True))
    checks.append(expect(any(hasattr(cache_mod, n) for n in ("PaginateStep", "PaginateNode")),
                         "PaginateStep node type exists (next-control + termination predicate + max-pages)",
                         "no paginate node in ultracua.cache", aspirational=True))
    checks.append(expect(any(hasattr(cache_mod, n) for n in ("BranchStep", "BranchNode", "Branch")),
                         "branch-on-state node type exists (deterministic predicate selects a step list)",
                         "no branch node in ultracua.cache", aspirational=True))
    return checks


@scenario(
    id="h07.replay.pagination_unroll_limit",
    title="a paginated bulk-read learns as a fixed unroll — replay cannot adapt when the dataset grows",
    group="h07", tags=("control-flow", "pagination", "replay"),
    notes="documented-limit baseline: the task class H7's PaginateStep + coverage guards makes expressible",
)
async def pagination_unroll_limit(ctx: Ctx):
    from ultracua.flow import run_cached

    checks = []
    fx = _paginated_fixture()
    goal = "collect every order row across all pages of the report"
    with fx.serve() as base:
        cache = ctx.cache()

        # SHIPPED BASELINE: the scripted provider unrolls the pagination at learn time — the
        # linear IR CAN capture a fixed-N walk (click-next, click-next), just not the loop itself.
        learned = await run_cached(base + "/", goal, _ClickNextUntilGone(), cache,
                                   mode="learn", headless=True)
        checks.append(expect(learned.success, "learn unrolls a fixed-N pagination into a linear flow",
                             f"note={learned.note!r}"))
        checks.append(expect("/p2" in fx.gets and "/p3" in fx.gets,
                             "learn visited all 3 pages of the learn-time dataset",
                             f"gets={fx.gets}"))

        # Grow the dataset AFTER learning: page 3 now has a next link and a page 4 exists.
        # (Same server + port, so the flow key still hits — only the site content changed.)
        fx.pages["/p3"] = page("<h1>orders</h1>" + _rows(1007, 1008, 1009)
                               + '<a href="/p4">next page</a>', title="orders p3")
        fx.pages["/p4"] = page("<h1>orders</h1>" + _rows(1010, 1011, 1012), title="orders p4")

        n0 = len(fx.gets)
        replayed = await run_cached(base + "/", goal, None, cache, mode="replay", headless=True)
        replay_gets = fx.gets[n0:]

        # SHIPPED: replay stays deterministic and 0-LLM — growing a TAIL page can't break the
        # learned prefix (the inviolable the H7 interpreter must preserve while adding the loop).
        checks.append(expect(replayed.success and replayed.llm_calls == 0,
                             "replay of the unrolled flow stays deterministic and 0-LLM after growth",
                             f"success={replayed.success} llm_calls={replayed.llm_calls}"))

        # DOCUMENTED LIMIT (passes as the honest baseline): the unroll replays EXACTLY the learned
        # page count — page 4 is structurally unreachable, so 'every row across ALL pages' silently
        # under-covers. This is the inexpressible task class PaginateStep's termination predicate +
        # monotonic-progress/max-pages guards exist to fix; the check documents today's shape.
        checks.append(expect("/p3" in replay_gets and "/p4" not in replay_gets,
                             "linear unroll replays exactly the learned page count (new tail page unreached)",
                             f"replay_gets={replay_gets}"))

        # The fail-loud half of the gap: H7 specifies coverage counts (pages visited / rows
        # collected / matched-vs-skipped) surfacing in FlowReport so a partial scrape fails LOUD
        # instead of returning well-shaped under-coverage. No such surfacing today -> missing.
        cov_keys = ("pages_visited", "rows_collected", "iterations", "loop_stats", "coverage",
                    "matched", "skipped")
        checks.append(expect(any(k in replayed.extra for k in cov_keys),
                             "FlowReport surfaces iteration/coverage counts for loud partial-scrape failure",
                             f"extra keys={sorted(replayed.extra)}", aspirational=True))
    return checks


@scenario(
    id="h07.locators.scoped_resolve",
    title="row-scoped locator resolution: resolve(root=...) with unique-within-row semantics",
    group="h07", aspirational=True, tags=("control-flow", "locators"),
    notes="H7 plan step 3: resolve() grows a root: Page|Locator param so loop bodies bind within each row",
)
async def scoped_resolve(ctx: Ctx):
    from ultracua.browser import BrowserSession
    from ultracua.locators import LocatorSpec, resolve

    checks = []
    # Three identical per-row 'view' links — page-wide they are ambiguous; within one row, unique.
    # This is the exact shape a LoopStep body faces on every iteration.
    fx = Fixture({"/": page(
        "<table>"
        '<tr><td>Alpha</td><td><a href="/r/1">view</a></td></tr>'
        '<tr><td>Beta</td><td><a href="/r/2">view</a></td></tr>'
        '<tr><td>Gamma</td><td><a href="/r/3">view</a></td></tr>'
        "</table>"
        '<a id="settings" href="/settings">settings</a>', title="rows")})
    with fx.serve() as base:
        s = await BrowserSession(headless=True).start()
        try:
            await s.goto(base + "/")

            # SHIPPED: page-wide resolve(unique=True) REFUSES the 3-way-ambiguous per-row link
            # (returns None, never guess-picks .first). This fail-loud contract is precisely what
            # `unique=True means unique-within-row` extends — without it a loop body click would
            # silently actuate row 1 forever.
            spec_view = LocatorSpec(role="link", name="view", tag="a", text="view")
            checks.append(expect(await resolve(s.page, spec_view, unique=True) is None,
                                 "resolve(unique=True) refuses an ambiguous per-row target (fail loud)"))

            # Control: the same resolver DOES bind a unique target, so the refusal above is about
            # ambiguity, not a broken resolver.
            spec_settings = LocatorSpec(role="link", name="settings", tag="a", elem_id="settings")
            checks.append(expect(await resolve(s.page, spec_settings, unique=True) is not None,
                                 "control: resolve(unique=True) binds a unique target normally"))

            # PARTIAL CREDIT: the Playwright scoping primitive resolve(root=) will delegate to —
            # a row-rooted get_by_role narrows 3 page-wide matches to exactly 1 within the row.
            row = s.page.locator("tr", has_text="Beta")
            page_wide = await s.page.get_by_role("link", name="view").count()
            in_row = await row.get_by_role("link", name="view").count()
            checks.append(expect(page_wide == 3 and in_row == 1,
                                 "Playwright row-root scoping primitive narrows 3 matches to 1",
                                 f"page_wide={page_wide} in_row={in_row}"))

            # The H7 surface itself: resolve() accepting a root= scope. Today the kwarg is
            # unexpected -> TypeError -> missing (core.py converts it; no browser work happens).
            st, val = await probe(resolve, s.page, spec_view, unique=True, root=row)
            checks.append(expect(st == "ok", "resolve accepts root= (unique-within-row binding)",
                                 f"probe={st}: {val}", aspirational=True))
        finally:
            await s.close()
    return checks


@scenario(
    id="h07.pins.row_scoped_cells",
    title="row-scoped 0-LLM cell pins (the co-requisite that keeps bulk reads 0-LLM at replay)",
    group="h07", aspirational=True, tags=("control-flow", "pins", "read"),
    notes="H7 plan step 4: per-column cell LocatorSpecs relative to the row root, verified at learn",
)
async def row_scoped_cells(ctx: Ctx):
    from ultracua.browser import BrowserSession
    from ultracua.pin import find_pin, read_pin

    checks = []
    # A unique id-anchored scalar (pinnable today) + the SAME value '42' in two different rows
    # (unpinnable today: ambiguous page-wide, unique only relative to a row root).
    fx = Fixture({"/": page(
        '<p>grand total: <span id="grand-total">7209</span></p>'
        "<table>"
        "<tr><td>Alpha</td><td>42</td></tr>"
        "<tr><td>Beta</td><td>42</td></tr>"
        "</table>", title="report")})
    with fx.serve() as base:
        s = await BrowserSession(headless=True).start()
        try:
            await s.goto(base + "/")

            # SHIPPED: the scalar pinned-read primitive row-scoped cells extend — pin a unique
            # id-anchored value at learn time, read it back 0-LLM (no model, no key, strict parse).
            pin = await find_pin(s.page, 7209)
            if pin is None:
                checks.append(missing("scalar pin round-trips a unique id-anchored value",
                                      "find_pin declined a unique id-anchored scalar"))
            else:
                got = await read_pin(s.page, pin)
                checks.append(expect(got == 7209, "scalar pin round-trips a unique id-anchored value",
                                     f"read back {got!r}"))

            # SHIPPED FAIL-LOUD: a value present in TWO rows is refused (None), never guess-pinned
            # to one of them — exactly the ambiguity a row-root scope resolves deterministically.
            checks.append(expect(await find_pin(s.page, 42) is None,
                                 "find_pin refuses a value that is ambiguous across rows (never guesses)"))

            # DOCUMENTED LIMIT (passes as the honest baseline): pins are scalar-only — a LIST value
            # is refused, so bulk row reads stay an LLM extraction call at replay. This is the
            # co-requisite gap: control-flow nodes alone would leave per-page reads costing 1 LLM
            # call per page, silently breaking the 0-LLM replay economics.
            checks.append(expect(await find_pin(s.page, ["Alpha", "Beta"]) is None,
                                 "pins are scalar-only today: a list value is refused, not guessed"))

            # The H7 surface: reading a pin RELATIVE to a row root (unique-within-row). Today the
            # kwarg is unexpected -> TypeError -> missing.
            dummy = pin or {"locator": {"tag": "td", "css": "td"}, "value_type": "int"}
            st, val = await probe(read_pin, s.page, dummy, root=s.page.locator("tr").first)
            checks.append(expect(st == "ok", "read_pin accepts a row root (row-scoped cell read)",
                                 f"probe={st}: {val}", aspirational=True))

            # The row/cell pin AUTHORING surface (per-column cell specs verified at learn time,
            # learn-run vs replay-run row sets compared). No such API on pin.py yet -> missing.
            import ultracua.pin as pin_mod
            author_names = ("find_row_pins", "pin_rows", "find_cell_pins", "read_row_pins", "find_pins")
            checks.append(expect(any(hasattr(pin_mod, n) for n in author_names),
                                 "row/cell pin authoring API exists",
                                 f"none of {author_names} on ultracua.pin", aspirational=True))
        finally:
            await s.close()
    return checks


@scenario(
    id="h07.discovery.loop_authoring",
    title="loop/paginate/branch verbs in the authoring surface + the deterministic guard primitives",
    group="h07", aspirational=True, tags=("control-flow", "discovery"),
    notes="H7 plan step 5: discovery emits loop nodes (read-only) via new structured-output verbs",
)
async def loop_authoring(ctx: Ctx):
    import inspect
    from typing import get_args

    from ultracua.conditions import condition_present
    from ultracua.providers.base import ACTION_TOOL
    from ultracua.snapshot import hash_scope
    from ultracua.types import ActionType

    checks = []

    # PARTIAL CREDIT: PaginateStep's termination predicate is specified to REUSE the shipped
    # condition_present ANY-of primitive — verify it carries the selector/text/url predicate
    # params (the deterministic 'until' vocabulary already exists; only the node is missing).
    params = set(inspect.signature(condition_present).parameters)
    checks.append(expect({"selector", "text_contains", "url_contains"} <= params,
                         "condition_present carries the selector/text/url predicates PaginateStep reuses",
                         f"params={sorted(params)}"))

    # PARTIAL CREDIT: the list-container fingerprint for the loop PRECONDITION (a changed default
    # filter/sort must fail loud, not return a well-shaped wrong row set) is specified on
    # snapshot.hash_scope — verify the primitive is deterministic AND discriminating.
    a = hash_scope([["list", "orders", "ul"], ["link", "next page", "a"]])
    b = hash_scope([["list", "orders", "ul"], ["link", "next page", "a"]])
    c = hash_scope([["list", "orders (filtered)", "ul"], ["link", "next page", "a"]])
    checks.append(expect(bool(a) and a == b and a != c,
                         "hash_scope is deterministic + discriminating (the loop-precondition fingerprint)",
                         f"a={a} b={b} c={c}"))

    # PARTIAL CREDIT: the closed single-action structured-output schema the loop verbs would
    # extend already ships (strict tool schema -> replay never needs a retry).
    enum = set(ACTION_TOOL["input_schema"]["properties"]["action"]["enum"])
    checks.append(expect({"click", "done"} <= enum,
                         "closed single-action authoring schema exists (ACTION_TOOL enum)",
                         f"enum={sorted(enum)}"))

    # The loop verbs themselves, in BOTH places a new verb must land (types.ActionType and the
    # provider-neutral tool schema). Neither exists yet -> missing.
    loop_verbs = ("repeat_over", "paginate_until", "branch", "loop")
    checks.append(expect(any(v in get_args(ActionType) for v in loop_verbs),
                         "ActionType includes loop/paginate/branch verbs",
                         f"no loop verbs in {get_args(ActionType)}", aspirational=True))
    checks.append(expect(any(v in enum for v in loop_verbs),
                         "ACTION_TOOL enum includes loop/paginate/branch verbs",
                         "no loop verbs in the authoring tool schema", aspirational=True))
    return checks
