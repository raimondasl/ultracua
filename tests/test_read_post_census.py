"""D7's candidate rule, pinned as a pure function -- especially where it must REFUSE.

WHAT THIS IS. `docs/reads-over-post.md`'s lead candidate demotes a POST to read only when the route
is Odoo's `call_kw`, the envelope parses as JSON-RPC `method:"call"`, and the ORM method is on a
committed read allowlist. Everything else stays a write. The rule lives in `benchmarks/` on purpose:
it is a PROPOSAL UNDER MEASUREMENT, so a bad answer costs a file nobody imports rather than a revert
on the write rail.

WHY THE CELLS LEAN ON REFUSAL. This is a demotion, so its errors are asymmetric: failing to demote a
read costs availability, and demoting a write costs inviolable #3. The measured negative control
(`--write`) put real `create` and `web_save` requests in front of it and both stayed writes; these
cells hold that offline, plus every fail-closed branch the survey names -- batch arrays, non-`call`
envelopes, unparseable bodies, unknown methods, and suffix/body disagreement.

`onchange` has a cell of its own. It is the documented write-in-a-read-shaped-call hazard, it was
OBSERVED live during the control run, and a future allowlist edit that admits it should have to
delete an assertion that says why not.
"""

from __future__ import annotations

import json

import pytest

from benchmarks.read_post_census import DRAFT_READ_METHODS, classify

CALL_KW = "http://h/web/dataset/call_kw"


def _body(method: str | None, envelope: str = "call", params_extra=None) -> str:
    params = {"model": "crm.lead", "args": [], "kwargs": {}}
    if method is not None:
        params["method"] = method
    params.update(params_extra or {})
    return json.dumps({"jsonrpc": "2.0", "method": envelope, "params": params})


# ------------------------------------------------------------------ what it demotes


@pytest.mark.parametrize("method", ["web_search_read", "get_views", "read", "search_count"])
def test_a_read_method_on_call_kw_demotes(method) -> None:
    """The population the fix exists for. `web_search_read` is what every measured Odoo read STEP
    caused -- one POST per click, and the step clears only if it demotes."""
    v = classify(f"{CALL_KW}/crm.lead/{method}", _body(method))
    assert v["demote"] is True and v["rpc_method"] == method


def test_a_named_read_route_demotes() -> None:
    v = classify("http://h/web/action/load", _body(None))
    assert v["demote"] is True and "route-exact" in v["why"]


# --------------------------------------------------------- what it must REFUSE to demote


@pytest.mark.parametrize("method", ["create", "web_save", "write", "unlink"])
def test_a_write_method_stays_a_write(method) -> None:
    """THE NEGATIVE CONTROL, offline. `create` and `web_save` were driven live through a real Odoo
    session and both stayed writes; these hold that without a substrate."""
    v = classify(f"{CALL_KW}/crm.lead/{method}", _body(method))
    assert v["demote"] is False


def test_onchange_stays_a_write() -> None:
    """THE NAMED HAZARD. `onchange` reads like a read and can write; it was OBSERVED live during the
    control run. Admitting it to the allowlist should require deleting this cell on purpose."""
    assert "onchange" not in DRAFT_READ_METHODS
    assert classify(f"{CALL_KW}/crm.lead/onchange", _body("onchange"))["demote"] is False


def test_an_unknown_method_stays_a_write() -> None:
    """FAILING CLOSED is the whole shape: enumerate the quiet set, and everything else is loud.
    `systray_get_activities`, `has_group`, `check_access_rights` and `render_public_asset` were all
    observed live and all stay writes until someone adds them deliberately."""
    for m in ("systray_get_activities", "has_group", "check_access_rights", "render_public_asset"):
        assert classify(f"{CALL_KW}/res.users/{m}", _body(m))["demote"] is False


def test_a_batch_array_stays_a_write() -> None:
    """A JSON-RPC batch can carry a write beside a read, and the envelope no longer names one
    operation."""
    body = json.dumps([{"jsonrpc": "2.0", "method": "call",
                        "params": {"method": "read"}}])
    assert classify(f"{CALL_KW}/crm.lead/read", body)["demote"] is False


def test_a_non_call_envelope_stays_a_write() -> None:
    assert classify(f"{CALL_KW}/crm.lead/read", _body("read", envelope="execute"))["demote"] is False


@pytest.mark.parametrize("body", [None, "", "not json", "{", "[]"])
def test_an_unreadable_body_stays_a_write(body) -> None:
    """No body, or one that cannot be parsed, is an operation nobody has identified."""
    assert classify(f"{CALL_KW}/crm.lead/read", body)["demote"] is False


def test_suffix_and_body_must_agree() -> None:
    """TWO INDEPENDENT READINGS OF ONE FACT. The census measured that Odoo carries the ORM method in
    BOTH the path and the body and that they agree, which is what makes the cross-check available at
    no cost. A disagreement is not a puzzle to resolve -- the operation is ambiguous, so it stays a
    write. This is the cell that would catch a route-spoofing attempt."""
    v = classify(f"{CALL_KW}/crm.lead/read", _body("create"))
    assert v["demote"] is False and "!=" in v["why"]


def test_a_read_method_on_an_unrelated_route_stays_a_write() -> None:
    """The allowlist is scoped to `call_kw`, not to the method name anywhere it appears -- otherwise
    any endpoint could borrow a read's name."""
    assert classify("http://h/some/other/endpoint", _body("read"))["demote"] is False


def test_the_mail_bus_routes_stay_writes() -> None:
    """Measured: Odoo's `/mail/*` routes carry no ORM method, so a body classifier structurally
    cannot clear them. They are PAGE-LOAD chrome and were measured NOT to land in act windows, which
    is why per-step clearance was 100% where per-POST clearance was 59% -- but nothing here demotes
    them, and a v1 that started to would be doing it without evidence."""
    for path in ("/mail/init_messaging", "/mail/inbox/messages", "/mail/load_message_failures"):
        assert classify(f"http://h{path}", _body(None))["demote"] is False
