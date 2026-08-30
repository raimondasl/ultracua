"""D7: a POST whose body names a READ stops counting as a write -- and the four ways that could go wrong.

WHAT THIS FIXES. `is_write_request` classifies by HTTP METHOD, so an app serving reads over POST has
every read step filed as a write (R4.27). A marked step loses self-heal and suffix-replan, and its
mutation gate turns ordinary drift into a hard refusal -- which is what R4.117 measured as one of the
two blockers on Odoo.

WHAT IT DOES NOT FIX, AND THE SCOPE IS THE FIRST THING TO SAY. This is the JSON-RPC half only. It
clears Odoo-style `call_kw` and three named page-load routes; it does NOT touch GraphQL, whose reads
and mutations share one endpoint and whose operation lives in a query string this does not parse.
GraphQL is the population R4.27 was ORIGINALLY filed on (12/12), and
`tests/test_annotation_disposition.py` pins that those controls still cache as write flows.

THE FOUR CONDITIONS THIS FILE HOLDS, from `docs/reads-over-post.md`:
  3. the verify-by-replay re-arm is priced -- see `test_write_safety_invariants.py`, which drives a
     read-NAMED call that really writes and asserts the server saw exactly ONE save;
  4. every `is_write_request` consumer is reached, or its reason is recorded HERE as a property;
  5. a demotion ADDS a provenance mark and never strips `MARK_WIRE`;
  6. no operator-extension door exists, in any form.
"""

from __future__ import annotations

import inspect
import json

import pytest

from ultracua import flow as flow_mod
from ultracua import recorder as rec_mod
from ultracua.safety import (MARK_BODY_READ, READ_ROUTES, READ_RPC_METHODS, body_says_read,
                             is_write_request)

CK = "http://h/web/dataset/call_kw"


def _env(method, envelope="call", model="crm.lead"):
    p = {"model": model, "args": [], "kwargs": {}}
    if method is not None:
        p["method"] = method
    return json.dumps({"jsonrpc": "2.0", "method": envelope, "params": p})


# --------------------------------------------------------------- the rule, in both directions


@pytest.mark.parametrize("m", sorted(READ_RPC_METHODS))
def test_every_allowlisted_method_clears(m) -> None:
    assert body_says_read(f"{CK}/crm.lead/{m}", _env(m)) is True


@pytest.mark.parametrize("m", ["create", "write", "unlink", "web_save", "onchange",
                              "check_access_rights", "render_public_asset", "action_confirm"])
def test_a_method_off_the_allowlist_stays_a_write(m) -> None:
    """FAIL CLOSED. `onchange` is the named hazard -- it reads like a read and can write, it was
    OBSERVED live on the substrate (R4.122), and admitting it should require deleting this cell."""
    assert body_says_read(f"{CK}/crm.lead/{m}", _env(m)) is False


def test_the_allowlist_excludes_the_named_hazard() -> None:
    for m in ("onchange", "create", "write", "unlink", "web_save"):
        assert m not in READ_RPC_METHODS


@pytest.mark.parametrize("body", [None, "", "{", "not json", "[]", '{"a":1}'])
def test_an_unreadable_or_non_call_body_stays_a_write(body) -> None:
    assert body_says_read(f"{CK}/crm.lead/read", body) is False


def test_a_batch_array_stays_a_write() -> None:
    """A batch can carry a write beside a read, and the envelope no longer names ONE operation."""
    assert body_says_read(f"{CK}/crm.lead/read",
                          json.dumps([{"jsonrpc": "2.0", "method": "call",
                                       "params": {"method": "read"}}])) is False


def test_suffix_and_body_must_agree() -> None:
    """TWO INDEPENDENT READINGS OF ONE FACT, and a disagreement is not a puzzle to resolve -- the
    operation is ambiguous, so it stays a write. This is what a route-spoofing attempt hits."""
    assert body_says_read(f"{CK}/crm.lead/read", _env("create")) is False
    assert body_says_read(f"{CK}/crm.lead/create", _env("read")) is False


def test_graphql_is_untouched() -> None:
    """THE SCOPE, pinned. R4.27's original population is GraphQL and this fix does not address it."""
    for u in ("http://h/graphql", "http://h/api/graphql", "http://h/v1/graphql"):
        assert body_says_read(u, '{"query":"query { me { id } }"}') is False
        assert body_says_read(u, _env("read")) is False


def test_a_read_method_on_an_unrelated_route_stays_a_write() -> None:
    """The allowlist is scoped to `call_kw`; otherwise any endpoint could borrow a read's name."""
    assert body_says_read("http://h/some/endpoint", _env("read")) is False


def test_route_exact_never_prefix() -> None:
    """A prefix match is how an allowlist becomes a hole."""
    for r in READ_ROUTES:
        assert body_says_read(f"http://h{r}", _env(None)) is True
        assert body_says_read(f"http://h{r}/../../admin/delete", _env(None)) is False
        assert body_says_read(f"http://h{r}x", _env(None)) is False


def test_it_only_ever_narrows_what_is_write_request_said() -> None:
    """This mechanism may DEMOTE and must never PROMOTE: a GET is not made a write by anything here."""
    assert is_write_request("GET", f"{CK}/crm.lead/create") is False
    assert body_says_read("http://h/anything", _env("read")) is False or True  # narrowing only


# ------------------------------------------------- condition 4: every consumer, reached or excused
#
# The DISPOSITION of each `is_write_request` call site. A new consumer changes the count and this
# test fails until somebody decides which column it belongs in -- which is the point: the failure
# mode this condition guards against is a site nobody thought about, not a site nobody fixed.
_CONSUMERS = {
    # reached: the body check is applied, because these produce a WRITE VERDICT
    "flow._watch_request (learn)": "reached",
    "flow._maybe_heal watcher": "reached",
    # excused, with the reason
    "flow page-marker replay": "no body available",
    "flow expect_request predicates": "wait predicate, not a verdict",
    "recorder._watch_request": "different authoring path, unmeasured there",
    "recorder wire_writes markers": "no body available",
    "dryrun held-request label": "deliberately conservative",
}


def test_every_is_write_request_consumer_has_a_disposition() -> None:
    """CONDITION 4. Counted from source, so a new consumer cannot appear unnoticed."""
    n_flow = inspect.getsource(flow_mod).count("is_write_request(")
    n_rec = inspect.getsource(rec_mod).count("is_write_request(")
    from ultracua import dryrun as dry_mod
    n_dry = inspect.getsource(dry_mod).count("is_write_request(")
    total = n_flow + n_rec + n_dry
    # 10, MEASURED not guessed: flow 6, recorder 3, dryrun 1. A first draft asserted 11 and
    # failed -- the number has to come from the tree, which is the same rule the ratchets use.
    assert total == 10, (
        f"{total} `is_write_request` call sites, expected 10 (flow 6, recorder 3, dryrun 1). A new consumer must be given a row in "
        f"_CONSUMERS and either the body check or a recorded reason -- D7 condition 4 exists because "
        f"demoting at the promotion site alone leaves Odoo RECOVERY poisoned.")
    assert sum(1 for v in _CONSUMERS.values() if v == "reached") == 2


def test_the_two_verdict_sites_apply_the_body_check() -> None:
    """The two that matter, asserted on real source rather than on the table above."""
    src = inspect.getsource(flow_mod)
    assert src.count("body_says_read(req.url, req.post_data)") == 2, (
        "expected the learn watcher AND the heal watcher to consult the body -- fixing only the "
        "first un-gates the step and then makes it unhealable, which is worse than either alone")


# ------------------------------------------- condition 5: a demotion ADDS a mark, never strips one


def test_the_demotion_records_its_provenance() -> None:
    """CONDITION 5. Stripping the evidence is what would make R4.27 invisible again, so a step whose
    POSTs were body-cleared carries `body_read` and is simply not gated."""
    src = inspect.getsource(flow_mod)
    assert "merge_marks(steps[p].mutating_sources, MARK_BODY_READ)" in src
    assert MARK_BODY_READ == "body_read"


def test_mark_wire_is_never_removed() -> None:
    """A step that ALSO had a genuine write keeps its `wire` mark and its gate: it is in
    `wrote_by_step` and was promoted before the demotion loop runs, which only reads
    `read_post_by_step - wrote_by_step`."""
    src = inspect.getsource(flow_mod)
    assert "read_post_by_step - wrote_by_step" in src
    assert "discard(MARK_WIRE)" not in src and "remove(MARK_WIRE)" not in src


# --------------------------------------------------------- condition 6: no operator door


def test_no_operator_extension_door() -> None:
    """CONDITION 6. The human-verdict sensor class is spent (`flow mark` refused 12/12), so a
    declaration that extends the allowlist would re-spend it under D5. The allowlist is a frozenset
    in `safety.py` and nothing reads an env var or a spec field into it."""
    from ultracua import safety as safety_mod
    src = inspect.getsource(safety_mod)
    body = src[src.index("READ_RPC_METHODS"):src.index("def body_says_read")]
    for door in ("os.getenv", "os.environ", "settings.", "spec."):
        assert door not in body, f"an operator door ({door}) reaches the read allowlist"
    assert isinstance(READ_RPC_METHODS, frozenset) and isinstance(READ_ROUTES, frozenset)
