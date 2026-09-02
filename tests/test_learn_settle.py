"""The learn-side settle: what it promises, where it is applied, and how it fails.

WHY IT EXISTS. `_author_steps` snapshots, decides, acts. On a client-rendered app that snapshot lands
on an unpainted shell -- Odoo serves 5 elements at `domcontentloaded` and settles to 80-255 around
470-860 ms -- so the agent decides from a page that is not there, re-navigates because nothing it
wants is visible (R4.118), and the loop's existing `no_progress` bail cannot stop it because
`state_changed` only asks whether the page DIFFERS and every unpainted shell differs (R4.119).

WHY A SETTLE RATHER THAN A NEW GUARD. The guard already exists. Feeding it a settled observation
re-arms it and adds nothing that can refuse, which keeps this away from D0 entirely. Measured on real
Odoo: six navigations to the same url left `no_progress` at 0 before and reached 4 -- the limit --
after.

WHY 200 ms. `readiness_probe --settle` scored candidates against a ground truth over 60 page-reps
(R4.120): `mut-quiet-200` was the cheapest that was NEVER premature, where acting immediately was
premature 28 times and "two equal element counts" 17.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from ultracua import flow as flow_mod
from ultracua.browser import BrowserSession
from ultracua.config import settings


# --------------------------------------------------------------- what it promises (live)


@pytest.mark.asyncio
async def test_a_static_page_settles_quiet() -> None:
    """The common case, and the floor: a page that never mutates goes quiet after one quiet window."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content("<body><h1>static</h1><a href='#'>x</a></body>")
        assert await s.await_settled() == "quiet"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_a_page_that_never_stops_mutating_hits_the_cap() -> None:
    """THE RESIDUAL, pinned. A page with a persistent ticker never goes quiet -- none exists in the
    measured corpus, so this is the case the numbers could not speak to. On the cap the settle gives
    up and the caller proceeds, which is EXACTLY the behaviour before this mechanism existed: the
    worst case is "no better than before", never "worse"."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content(
            "<body><div id=t></div><script>setInterval(function(){"
            "document.getElementById('t').textContent = Date.now();}, 20);</script></body>")
        assert await s.await_settled(quiet_ms=200, cap_ms=600) == "cap"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_a_mutating_page_that_stops_is_waited_out() -> None:
    """The case the whole mechanism is for: mutations that stop AFTER the caller asks. A predicate
    that sampled twice and compared could return inside this burst (R4.120 measured that failing 17
    times); one that restarts its timer on every mutation cannot."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.set_content(
            "<body><div id=t></div><script>let n=0;const h=setInterval(function(){"
            "document.getElementById('t').appendChild(document.createElement('span'));"
            "if (++n > 8) clearInterval(h);}, 30);</script></body>")
        assert await s.await_settled(quiet_ms=200, cap_ms=5000) == "quiet"
        assert await s.page.evaluate("() => document.querySelectorAll('#t span').length") >= 8, (
            "returned before the burst finished -- the quiet timer is not being restarted")
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_a_closed_page_is_unavailable_not_an_exception() -> None:
    """FAIL-OPEN. A page mid-navigation makes `evaluate` throw, and a settle that turned a transient
    into a failed step would be a regression bought with a diagnostic."""
    s = BrowserSession(headless=True)
    await s.start()
    try:
        await s.page.close()
        assert await s.await_settled() == "unavailable"
    finally:
        await s.close()


# ------------------------------------------------------- where it is applied (structural)


def test_the_learn_settles_before_both_of_its_observations() -> None:
    """BOTH sites, and the second is the one that re-arms the bail. The loop-head settle is what lets
    the agent SEE the page; the verify settle is what makes `changed` mean "my action did something"
    rather than "the render advanced"."""
    src = inspect.getsource(flow_mod)
    assert 'tr.meta["settled"] = await session.await_settled()' in src, "loop-head settle missing"
    assert 'tr.meta["settled_after"] = await session.await_settled()' in src, "verify settle missing"
    head = src.index('tr.meta["settled"] = await session.await_settled()')
    assert head < src.index("obs = await session.snapshot()"), (
        "the settle must PRECEDE the observation it is settling for")


def test_replay_never_settles_UNCONDITIONALLY() -> None:
    """THE ASYMMETRY IS THE DESIGN, not an oversight. A server-rendered page measures
    `true_ready = 0` in every rep (R4.120), so an unconditional wait on the replay path is pure tax
    on the product's own speed claim.

    THIS CELL PREVIOUSLY PINNED THE COUNT AT TWO and it FIRED when the replay-side sensor landed at
    0.148.0 -- which is the guard working: it demanded an argument for the third call site rather
    than letting it in quietly. The argument is that replay's settle is CONDITIONAL: it lives inside
    `_retry_if_unpainted` and is reached only after a resolve has already failed with
    `saw_candidates` False, so nothing is paid on the happy path or on any refusal.

    IT FIRED A SECOND TIME AT 0.158.0, for R4.115's sites (2) and (3) -- the mutation gate deciding
    drift from an unsettled page. That argument, since this cell exists to make it be made:

      * IT IS NOT ON EVERY REPLAY STEP. The call is inside `if step.mutating:`, so a read step pays
        nothing and the speed claim is untouched for the population R4.120 measured.
      * THE COST WAS MEASURED, NOT ASSERTED: on an already-quiet page `await_settled()` returns
        `already-quiet` in a median of **3.3 ms on Gitea and 2.2 ms on Odoo** (max 6.1), because
        `_quiet_for_ms` short-circuits when the page has been still for the quiet window already.
      * THE COST OF NOT DOING IT (R4.139): a byte-identical recipe was passed by this gate once and
        refused twice across three reps, with `mutation_gate_refused` the only differing field.

    IT FIRED A THIRD TIME AT 0.165.0, for R4.144's bounded poll -- a SECOND settle inside the
    guarded helper, at the top of the retry loop. That argument:

      * IT IS NOT ON THE HAPPY PATH. It is reached only after a settle that GENUINELY WAITED, on a
        target the resolver has already reported ABSENT (`saw_candidates` False). A resolve that
        binds never enters the helper at all, and the `already-quiet` skip above still short-circuits
        before the loop -- so `drift_bench`'s static corpus (42 such retries, 10.1 s) never reaches
        it.
      * THE TAX WAS MEASURED AT ZERO, NOT ARGUED: across `gitea-sort-list`, `gitea-search` and
        `gitea-comment` the retry path fired **0 times**, which is what R4.120's `true_ready = 0`
        predicts for a server-rendered page.
      * THE COST OF NOT DOING IT: `odoo-create-lead` was `refused_wrongly`, and with the poll it is
        **`true` 3/3**, binding at look 5 of ~5 at 719-906 ms.

    So the invariant is the SHAPE, in four named positions: the learn's two are unconditional, the
    step's TWO are inside the guarded helper (one before the first look, one per subsequent look),
    and the gate's is inside the `step.mutating` branch. A new one dropped anywhere else still fails
    here.

    AND THE COUNT IS TAKEN FROM THE AST, NOT FROM THE TEXT. Counting `src.count("await_settled()")`
    made this cell red on the new PROSE explaining the poll -- the NINTH time a scan in this
    repository has matched the comment written to explain it, and the first time inside a cell whose
    subject is a COUNT. 0.161.0 recorded the rule as absolute; this is it applied.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(flow_mod)))

    def _settles(node) -> int:
        return sum(1 for n in ast.walk(node)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "await_settled")

    src = inspect.getsource(flow_mod)
    total = _settles(tree)
    helper = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                  and n.name == "_retry_if_unpainted")
    inside_helper = _settles(helper)
    assert inside_helper == 2, (
        f"expected the replay's step settles to be the retry helper's two -- one before the first "
        f"look, one per subsequent look -- found {inside_helper}")

    # The gate's settle must sit INSIDE the mutating branch -- a read step must not pay for it.
    gate_call = 'tr.meta["gate_settled"] = await session.await_settled()'
    assert gate_call in src, "the gate's settle is missing (R4.115 sites 2 and 3)"
    guard_at = src.index("if step.mutating:")
    assert guard_at < src.index(gate_call), (
        "the gate's settle escaped the `if step.mutating:` branch, so every READ step now waits -- "
        "which is exactly the pure tax this cell exists to refuse")
    assert src.index(gate_call) < src.index("current = await scope_fingerprint(target)"), (
        "the settle must PRECEDE the drift comparison it exists to make like-for-like")

    assert total == 5, (
        f"expected 2 unconditional LEARN settles + 2 in the guarded retry helper + 1 inside the "
        f"mutation gate, found {total} -- a new unconditional wait on the replay path is pure tax on "
        f"the substrates R4.120 measured at `true_ready = 0`, and needs the same argument this cell "
        f"has now forced three times")


def test_the_settle_window_is_the_measured_one() -> None:
    """A tuning constant nobody can source is how a measured number becomes folklore. These two came
    from `readiness_probe --settle` over 60 page-reps; the cap clears the largest observed firing."""
    assert settings.settle_quiet_ms == 200
    assert settings.settle_cap_ms == 2000
    assert settings.settle_cap_ms > settings.settle_quiet_ms, (
        "a cap at or below the quiet window makes every settle return `cap` and the mechanism inert")
