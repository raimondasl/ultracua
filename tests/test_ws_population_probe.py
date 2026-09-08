"""The facts D4's decision rests on, held against Playwright and the recorder (0.179.0, R4.157).

D4 asked for "learn-path WebSocket parity with the recorder". The decision was NO CHANGE, and it
rests on three things that can each stop being true without anything else noticing:

  * `WebSocket` is a PAGE event and not a BrowserContext one, so a websocket watcher has exactly one
    scope available and "parity" has nothing to widen to. **If a Playwright upgrade adds a
    context-level websocket event, D4 should be REOPENED** -- that is what the first cell is for;
  * the recorder's watcher really is page-scoped, so "parity with the recorder" means what the
    decision says it means;
  * the probe that measured zero can PROVE its sensor fires, because a zero from an instrument that
    never worked is the failure this repository files most (R4.134, and the probe's own first draft
    pointed at a Chrome-blocked port and read False on its control).
"""

from __future__ import annotations

import ast
import inspect


def test_websocket_is_a_page_event_and_not_a_context_one() -> None:
    """THE FACT THAT MAKES D4 SCOPE-LESS, and the one most likely to change under us.

    The 0.177.0 request watchers had a real choice -- `page.on("request")` vs
    `page.context.on("request")` -- and took the wider one. A websocket watcher has no such choice:
    Playwright surfaces `WebSocket` on `Page` alone. `BrowserContext` offers `ServiceWorker` and
    nothing for shared workers or sockets.

    Read from Playwright's own event tables rather than from documentation prose, so a version that
    ADDS the event fails here and reopens the decision instead of leaving a stale `NO CHANGE`.
    """
    from playwright._impl._browser_context import BrowserContext as CtxImpl
    from playwright._impl._page import Page as PageImpl

    page_events = {k for k in vars(PageImpl.Events) if not k.startswith("_")}
    ctx_events = {k for k in vars(CtxImpl.Events) if not k.startswith("_")}

    assert "WebSocket" in page_events, (
        "Playwright no longer exposes a page-level WebSocket event; the recorder's watcher and D4's "
        "whole subject have moved")
    assert "WebSocket" not in ctx_events, (
        "Playwright now exposes a CONTEXT-level WebSocket event. D4 was decided NO CHANGE partly "
        "because a websocket watcher had exactly one scope and could not be widened the way the "
        "0.177.0 request watchers were (R4.153). That premise is gone -- REOPEN D4 (R4.157).")


def test_the_recorder_watches_websockets_at_page_scope() -> None:
    """"Parity with the recorder" is only meaningful if the recorder's own scope is what the decision
    says. Asserted over the AST, never source text -- this file's own prose contains the call, and a
    substring scan matching its own explanation is this repository's most-repeated scan defect."""
    from ultracua import recorder as rec_mod

    tree = ast.parse(inspect.getsource(rec_mod))
    ws_registrations = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "on"
        and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "websocket"
    ]
    assert ws_registrations, "the recorder no longer watches websockets at all"
    for call in ws_registrations:
        receiver = ast.unparse(call.func.value)
        assert receiver == "page", (
            f"the recorder's websocket watcher is registered on {receiver!r}, not `page`. If a wider "
            f"receiver became available, D4's 'nothing to widen to' premise is void (R4.157).")


def test_the_probe_calibrates_in_both_directions() -> None:
    """A zero is worth what its control is worth. `calibrate()` must FAIL when the sensor never fired
    and when the shared-worker arm does not reproduce -- otherwise the corpus zero this decision
    rests on could be the probe rather than the app."""
    from benchmarks import ws_population_probe as P

    src = inspect.getsource(P.calibrate)
    tree = ast.parse(src)
    returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]
    codes = {n.value.value for n in returns
             if isinstance(n.value, ast.Constant) and isinstance(n.value.value, int)}
    assert {0, 1} <= codes, (
        f"`calibrate` returns {sorted(codes)}; it must be able to FAIL (1) as well as pass (0), or a "
        f"broken sensor calibrates green and every corpus zero measured with it is meaningless")

    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert {"page_ok", "shared_seen"} <= names, (
        f"`calibrate` no longer computes both arms (found {sorted(names & {'page_ok', 'shared_seen'})}). "
        f"The page arm proves the sensor fires; the shared-worker arm proves the realm it cannot "
        f"reach still escapes it.")


def test_the_probe_hops_through_about_blank() -> None:
    """Odoo routes on the HASH, so a goto between two `#action=` urls is a SAME-DOCUMENT navigation
    that measures the view it just left (R4.141) -- and it would under-report in the REASSURING
    direction, which is the one this probe's conclusion depends on.

    Asserted as an argument to a `goto` call: the first draft of the sibling probe's version checked
    that the literal existed anywhere in the function, which deleting the hop would have passed.
    """
    from benchmarks import ws_population_probe as P

    hops = [n for n in ast.walk(ast.parse(inspect.getsource(P.probe)))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "goto" and n.args
            and isinstance(n.args[0], ast.Constant) and n.args[0].value == "about:blank"]
    assert hops, (
        "the probe no longer NAVIGATES to about:blank before each row, so its Odoo rows measure the "
        "previous view (R4.141) and under-report the sockets this decision counted")
