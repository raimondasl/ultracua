"""`actions_taken` counted TRACES, so the value `no_actions_needed` needs could never occur.

THE DEFECT (R4.129). `outcomes._classify_read` mints `no_actions_needed` only when
`actions_taken == 0`. `scored_run` set that to `len(report.traces)` -- and `_author_steps` records a
navigation trace at `index=-1` before its loop and appends another for the terminal `done`, so the
floor is 2 for any completed learn. The clause was unreachable from the day R4.103 introduced it, and
a task whose answer was already on its landing page published as `not_authored`: *the product was
asked to author this flow and did not*, for a run that answered correctly.

WHY `tests/test_no_actions_outcome.py` COULD NOT CATCH IT. All eleven of its cells construct
`_Record(..., actions_taken=0, ...)` by hand. They pin the CLAUSE, faithfully, and say nothing about
whether the runner can supply its input -- this repo's "a green property is worth exactly what its
stub is worth", where the stub is a single integer. So these cells never hand-write the number: they
drive the real `_learn_once` against a real page and read what the real reporting path computes.

MEASURED before the fix, with `done` on turn 1:  traces=2, actions_taken=2.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

# CALL THROUGH THE MODULE BINDING. `from benchmarks.scored_run import actions_taken` binds the
# original function object, so `tests/_arming`'s mutation of the module attribute never reaches these
# cells and every arming mutation SURVIVES while the cells look fine. Measured twice in one session
# -- once here and once in `test_read_post_census.py` -- which is why
# `test_arming_targets_are_reachable` now refuses the direct-import form outright.
from benchmarks import scored_run as SR
from ultracua.cache import FlowCache
from ultracua.flows import FlowSpec, _learn_once
from ultracua.providers.scripted import ScriptedProvider

BODY = (b"<h1>Pipeline</h1><p id='answer'>Top opportunity: Quote for 150 carpets</p>"
        b"<button type=button id='b'>Sort</button>")


def _serve():
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)

    h = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=h.serve_forever, daemon=True).start()
    return h, f"http://127.0.0.1:{h.server_port}"


async def _learn(script: list, tmp: Path):
    httpd, base = _serve()
    try:
        spec = FlowSpec(name="p", goal="report the top opportunity",
                        start_url=f"{base}/", headless=True)
        res = await _learn_once(spec, provider=ScriptedProvider(list(script)), router=None,
                                cache=FlowCache(root=tmp), verify_replay=False)
        return res, list(getattr(res.report, "traces", None) or ())
    finally:
        httpd.shutdown()
        httpd.server_close()


DONE = {"action": "done", "intent": "the answer is already on the page"}
CLICK = {"action": "click", "role": "button", "name": "Sort", "intent": "sort"}


async def test_a_task_needing_no_action_reports_zero(tmp_path: Path) -> None:
    """THE CASE THE CLAUSE EXISTS FOR, driven end to end. Before the fix this returned 2 and the
    outcome could never be minted."""
    res, traces = await _learn([DONE], tmp_path / "a")
    n = SR.actions_taken(traces)
    print(f"    traces={len(traces)}  actions_taken={n}  "
          f"kinds={[(t.index, t.meta.get('stop')) for t in traces]}")
    assert res.found, "the fixture learn did not complete; this cell would prove nothing"
    assert len(traces) >= 2, (
        "PREMISE LOST: the runner no longer records a nav trace plus a terminal trace, so this cell "
        "is no longer exercising the gap it was written for. Re-derive the floor before relaxing it.")
    assert n == 0, (
        f"a learn that took no action reported {n} actions. `no_actions_needed` requires 0, so the "
        f"outcome is unmintable and a correct zero-action answer is published as `not_authored`.")


@pytest.mark.parametrize("script,expected", [
    ([DONE], 0),
    ([CLICK, DONE], 1),
    ([CLICK, CLICK, DONE], 2),
])
async def test_the_count_is_the_agents_own_actions(script, expected, tmp_path: Path) -> None:
    """The scale, not just the zero. A fix that returned a constant 0 would satisfy the cell above
    completely -- and would delete every real authoring failure from the product's account, which is
    the flattering direction R4.103 was filed to close."""
    _res, traces = await _learn(script, tmp_path / f"n{expected}")
    n = SR.actions_taken(traces)
    print(f"    script={[s['action'] for s in script]}  traces={len(traces)}  actions_taken={n}")
    assert n == expected, f"expected {expected} real actions, got {n} from {len(traces)} traces"


async def test_the_navigation_is_not_an_action_the_agent_chose(tmp_path: Path) -> None:
    """The first exclusion, named. The nav trace carries `index=-1`; counting it makes every learn
    look like it did one thing more than it did, and makes zero unreachable."""
    _res, traces = await _learn([DONE], tmp_path / "nav")
    navs = [t for t in traces if t.index < 0]
    print(f"    nav traces={[(t.index, dict(t.meta)) for t in navs]}")
    assert navs, ("PREMISE LOST: no nav trace. The exclusion below is then untested rather than "
                  "satisfied -- re-check `_author_steps` before trusting this file.")
    assert SR.actions_taken(navs) == 0


async def test_a_decision_to_stop_is_not_an_action(tmp_path: Path) -> None:
    """The second exclusion. `done` and `give_up` are the opposite of acting, and `flow.py` sets
    `meta['stop']` in exactly one place -- so the key is the signal rather than the action name."""
    _res, traces = await _learn([CLICK, DONE], tmp_path / "stop")
    stops = [t for t in traces if "stop" in t.meta]
    print(f"    stop traces={[(t.index, t.meta.get('stop')) for t in stops]}")
    assert stops, "PREMISE LOST: no terminal trace, so this exclusion is untested"
    assert SR.actions_taken(stops) == 0


def test_a_failed_action_still_counts_as_an_action() -> None:
    """A DIRECTION THE EXCLUSIONS MUST NOT REACH. The agent acted; the action not working is a
    different fact, and folding it in would recreate R4.103 -- a real authoring failure scored as a
    task that needed no work. Driven on the trace shape rather than through a browser, because what
    is being asserted is that NEITHER exclusion keys on success."""
    class _T:
        def __init__(self, index, meta):
            self.index, self.meta = index, meta

    acted_and_failed = [_T(0, {"action": {"action": "click"}, "error": "locator unresolved"})]
    print(f"    {SR.actions_taken(acted_and_failed)} action(s) counted for a failed click")
    assert SR.actions_taken(acted_and_failed) == 1


def test_no_traces_at_all_is_zero_not_a_crash() -> None:
    """A harness row never reaches the learn, so `res.report` may be absent entirely."""
    assert SR.actions_taken(None) == 0 and SR.actions_taken([]) == 0


async def test_a_real_zero_action_learn_now_mints_no_actions_needed(tmp_path: Path) -> None:
    """THE END-TO-END PROOF, and the only cell here that reaches the vocabulary.

    Everything above shows the COUNT is right. This shows the outcome the count feeds is reachable
    again -- which is the whole finding, because the clause has been unmintable since 0.131.0 while
    eleven cells asserting its behaviour stayed green.

    The numbers come from a REAL learn; only `classify` is called directly. That is the join
    `tests/test_no_actions_outcome.py` never makes, and not making it is how this survived.
    """
    from benchmarks import corpus, outcomes as O
    from benchmarks.scored_run import _Record

    res, traces = await _learn([DONE], tmp_path / "e2e")
    n = SR.actions_taken(traces)
    rec = _Record(True, "", True, authored=bool(res.cached), recipe_steps=len(res.steps or ()),
                  actions_taken=n, learn_found=bool(res.found))
    truth = {e.scenario.name: e for e in corpus.CORPORA["gitea"]}["gitea-search"].truth
    verdict = O.classify(truth, rec, O.Oracle(available=True, data_correct=True))
    print(f"    actions_taken={n} steps={len(res.steps or ())} found={res.found} "
          f"-> {verdict.outcome}")
    assert verdict.outcome == O.NO_ACTIONS_NEEDED, (
        f"a real zero-action learn scored {verdict.outcome!r}. The clause requires "
        f"`actions_taken == 0`; if this is `not_authored` again, the count regressed to counting "
        f"traces and the outcome is unmintable.")


# --- the arithmetic, synchronously ----------------------------------------------------------------
#
# THE PAIRING, stated because a synthetic trace is exactly the hand-built stub this file criticises.
# The async cells above prove the REAL runner emits this shape -- a nav trace at `index=-1`, a
# terminal trace carrying `meta["stop"]`, and one trace per action. These pin the ARITHMETIC over
# that shape, and they exist as separate cells because `tests/_arming.assert_red` calls a guard
# synchronously: handed an `async def` it gets a coroutine back, nothing raises, and it reports a
# FALSE SURVIVOR. So the mutations target these, and the async cells keep them honest about what the
# shape really is. Neither half is sufficient alone.

class _Trace:
    def __init__(self, index, meta):
        self.index, self.meta = index, meta


NAV = _Trace(-1, {})
DONE_TRACE = _Trace(0, {"action": {"action": "done"}, "stop": "done"})
CLICK_TRACE = _Trace(0, {"action": {"action": "click"}})


def test_a_nav_and_a_done_is_zero_actions() -> None:
    """The measured shape of a learn that acted not at all: exactly what `no_actions_needed` needs,
    and what `len(traces)` reported as 2."""
    n = SR.actions_taken([NAV, DONE_TRACE])
    print(f"    nav + done -> {n}")
    assert n == 0, f"a nav trace plus a terminal `done` counted {n} actions; the clause needs 0"


def test_one_action_between_them_is_one() -> None:
    """The scale. A constant 0 satisfies the cell above and erases every real authoring failure."""
    n = SR.actions_taken([NAV, CLICK_TRACE, DONE_TRACE])
    print(f"    nav + click + done -> {n}")
    assert n == 1, f"one real action counted as {n}"
