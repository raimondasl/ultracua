from __future__ import annotations

from pathlib import Path

import time

import pytest

from ultracua.cache import (
    SCHEMA_VERSION,
    STEPS_HASH_VERSION,
    CachedFlow,
    CachedStep,
    FlowCache,
    StepConfirm,
    flow_key,
    steps_hash,
)
from ultracua.locators import LocatorSpec


def test_flow_key_normalizes_goal_and_url() -> None:
    a = flow_key("Add to Cart", "https://Example.com/path/")
    b = flow_key("  add   to cart ", "https://example.com/path")
    assert a == b
    assert flow_key("x", "https://e.com") != flow_key("y", "https://e.com")
    assert flow_key("x", "https://e.com", scope="s1") != flow_key("x", "https://e.com", scope="s2")


def test_cache_round_trip(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path)
    key = flow_key("g", "https://e.com")
    assert cache.get(key) is None

    flow = CachedFlow(
        key=key,
        goal="g",
        start_url="https://e.com",
        created_ts=1.0,
        steps=[
            CachedStep(
                intent="click submit",
                action="click",
                locator=LocatorSpec(role="button", name="Submit", tag="button"),
            )
        ],
    )
    cache.put(flow)

    got = cache.get(key)
    assert got is not None
    assert got.steps[0].locator is not None
    assert got.steps[0].locator.name == "Submit"

    assert cache.delete(key) is True
    assert cache.get(key) is None


def _flow(key: str, **kw) -> CachedFlow:
    base = dict(
        key=key,
        goal="g",
        start_url="https://e.com",
        created_ts=time.time(),
        steps=[CachedStep(intent="i", action="click")],
    )
    base.update(kw)
    return CachedFlow(**base)


def test_expired_entry_is_a_miss(tmp_path) -> None:
    cache = FlowCache(root=tmp_path, ttl_seconds=0.0)
    key = flow_key("g", "https://e.com")
    cache.put(_flow(key, created_ts=time.time() - 10))
    assert cache.get(key) is None  # aged past ttl=0 -> miss


def test_incompatible_schema_is_a_miss(tmp_path) -> None:
    cache = FlowCache(root=tmp_path)
    key = flow_key("g", "https://e.com")
    cache.put(_flow(key, schema_version=SCHEMA_VERSION - 1))
    assert cache.get(key) is None
    cache.put(_flow(key))  # current schema
    assert cache.get(key) is not None


# --- steps_hash: the approval-bound recipe digest ---------------------------------------------------
# These tests exist to keep the digest TRUSTWORTHY over time, because it is what `flow approve` binds to.
# The CENSUS fails CI when a model grows a field nobody classified; the GOLDEN VECTOR fails CI when the
# basis bytes move (which would un-approve every flow in every deployed fleet); the sensitivity tests
# prove each hashed field actually participates and each unhashed one actually doesn't.


def _golden_flow() -> CachedFlow:
    """A fully-populated 2-step write flow — every hashed field non-default, both nested models present."""
    return CachedFlow(
        key="deadbeefdeadbeef",
        goal="place the weekly supply order",
        start_url="https://shop.example.com/reorder",
        created_ts=1_700_000_000.0,
        steps=[
            CachedStep(
                intent="type the quantity",
                action="type",
                locator=LocatorSpec(
                    role="textbox", name="Quantity", tag="input", elem_id="qty",
                    testid="qty-field", placeholder="0", text="12", css="#qty",
                    anchor="Order form", anchor_source="heading",
                ),
                text="12",
                coords=[10, 20],
                tool="noop.tool",
                args={"a": 1},
                precond_fingerprint="fp-page-1",
                precond_scope="fp-form-1",
                mutating=False,
                slot="quantity",
                slot_domain={"max_length": 3, "pattern": "^[0-9]+$"},
            ),
            CachedStep(
                intent="click Place order",
                action="click",
                locator=LocatorSpec(role="button", name="Place order", tag="button"),
                precond_fingerprint="fp-page-2",
                precond_scope="fp-form-2",
                mutating=True,
                confirm=StepConfirm(
                    confirm_selector=".order-confirmed",
                    confirm_text_contains="Order placed",
                    confirm_url_contains="/orders/",
                    timeout_ms=9000,
                    expects_intent="Place order",
                ),
            ),
        ],
    )


def test_every_model_field_is_classified_hashed_or_unhashed() -> None:
    """FIELD CENSUS. The digest basis is an explicit allow-list (so an additive release can't silently
    un-approve every fleet). The cost of an allow-list is that a NEW field is invisible to it by default
    — including a new field that changes what a run DOES. This test is the tripwire: add a field to any
    of these four models and CI fails until you consciously put its name in a hashed or unhashed tuple.
    Read `steps_hash`'s inclusion rule before you choose."""
    from ultracua import cache as c

    for model, buckets in (
        (CachedFlow, (c._HASHED_FLOW_FIELDS, c._UNHASHED_FLOW_FIELDS, c._NESTED_FLOW_FIELDS)),
        (CachedStep, (c._HASHED_STEP_FIELDS, c._UNHASHED_STEP_FIELDS, c._NESTED_STEP_FIELDS)),
        (LocatorSpec, (c._HASHED_LOCATOR_FIELDS, (), ())),
        (StepConfirm, (c._HASHED_CONFIRM_FIELDS, (), ())),
    ):
        classified: set[str] = set()
        for bucket in buckets:
            for f in bucket:
                assert f not in classified, f"{model.__name__}.{f} classified twice"
                classified.add(f)
        actual = set(model.model_fields)
        assert actual == classified, (
            f"{model.__name__}: unclassified field(s) {sorted(actual - classified)!r}; "
            f"stale entry(ies) {sorted(classified - actual)!r}. Classify new fields in cache.py "
            f"(_HASHED_* if changing them changes what a run does or weakens a guard, _UNHASHED_* if "
            f"pure churn) — an unclassified field is silently outside the approval binding."
        )


def test_steps_hash_golden_vector() -> None:
    """GOLDEN VECTOR. Pins the exact digest bytes for a fixed flow. If this fails without an intentional
    STEPS_HASH_VERSION bump, the basis moved — and moving the basis invalidates the approval of every
    flow in every deployed fleet (they all fail loud with `stale_approval` until a human re-approves).
    That is sometimes the right call, but never an accident: bump the version and say so in the release
    notes."""
    assert STEPS_HASH_VERSION == 1
    assert steps_hash(_golden_flow()) == "9f5bbd6866fc588e"


def test_steps_hash_is_stable_and_length_capped() -> None:
    f = _golden_flow()
    assert steps_hash(f) == steps_hash(f)                        # deterministic across calls
    assert steps_hash(f) == steps_hash(f.model_copy(deep=True))  # and across object identity
    h = steps_hash(f)
    assert len(h) == 16 and all(ch in "0123456789abcdef" for ch in h)


@pytest.mark.parametrize(
    "field,value",
    [
        ("intent", "type the QUANTITY"),
        ("action", "fill"),
        ("text", "13"),
        ("coords", [10, 21]),
        ("tool", "other.tool"),
        ("args", {"a": 2}),
        ("precond_fingerprint", "fp-page-X"),
        ("precond_scope", "fp-form-X"),
        ("mutating", True),
        ("slot", "qty"),
        ("slot_domain", {"max_length": 4}),
    ],
)
def test_every_hashed_step_field_changes_the_digest(field: str, value) -> None:
    f = _golden_flow()
    base = steps_hash(f)
    steps = list(f.steps)
    steps[0] = steps[0].model_copy(update={field: value})
    assert steps_hash(f.model_copy(update={"steps": steps})) != base, f"{field} does not bind"


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "combobox"), ("name", "Qty"), ("tag", "select"), ("elem_id", "qty2"),
        ("testid", "qty-2"), ("placeholder", "1"), ("text", "13"), ("css", "#qty2"),
        ("anchor", "Other form"), ("anchor_source", "label"),
    ],
)
def test_every_hashed_locator_field_changes_the_digest(field: str, value) -> None:
    """A locator is how a step chooses its TARGET — a swapped locator under a stale approval bit is the
    write-lands-somewhere-else failure this gate exists to stop. All ten fields must bind."""
    f = _golden_flow()
    base = steps_hash(f)
    steps = list(f.steps)
    assert steps[0].locator is not None
    steps[0] = steps[0].model_copy(
        update={"locator": steps[0].locator.model_copy(update={field: value})}
    )
    assert steps_hash(f.model_copy(update={"steps": steps})) != base, f"locator.{field} does not bind"


@pytest.mark.parametrize(
    "field,value",
    [
        ("confirm_selector", ".other"), ("confirm_text_contains", "Done"),
        ("confirm_url_contains", "/x/"), ("timeout_ms", 9001), ("expects_intent", "Submit"),
    ],
)
def test_every_hashed_confirm_field_changes_the_digest(field: str, value) -> None:
    """The confirm barrier is a write's completion proof. Weakening it (or retargeting it) must invalidate
    the approval, or "approved" would cover a flow whose write is no longer verified."""
    f = _golden_flow()
    base = steps_hash(f)
    steps = list(f.steps)
    assert steps[1].confirm is not None
    steps[1] = steps[1].model_copy(
        update={"confirm": steps[1].confirm.model_copy(update={field: value})}
    )
    assert steps_hash(f.model_copy(update={"steps": steps})) != base, f"confirm.{field} does not bind"


def test_flow_identity_binds_but_bookkeeping_does_not() -> None:
    f = _golden_flow()
    base = steps_hash(f)
    for field, value in (("key", "0000000000000000"), ("goal", "other goal"),
                         ("start_url", "https://shop.example.com/other")):
        assert steps_hash(f.model_copy(update={field: value})) != base, f"{field} does not bind"
    # ...and the two bookkeeping fields must NOT bind, or a re-record with identical steps (or a schema
    # bump) would un-approve a flow whose recipe never changed.
    assert steps_hash(f.model_copy(update={"created_ts": 1.0})) == base
    assert steps_hash(f.model_copy(update={"schema_version": SCHEMA_VERSION + 7})) == base


def test_structural_edits_change_the_digest() -> None:
    """Reordering, dropping, duplicating or un-nesting steps are all recipe changes."""
    f = _golden_flow()
    base = steps_hash(f)
    a, b = f.steps
    assert steps_hash(f.model_copy(update={"steps": [b, a]})) != base       # reordered
    assert steps_hash(f.model_copy(update={"steps": [a]})) != base          # the write dropped
    assert steps_hash(f.model_copy(update={"steps": [a, b, b]})) != base    # the write duplicated
    assert steps_hash(f.model_copy(update={"steps": []})) != base           # emptied
    # dropping a nested model entirely (locator/confirm -> None) must bind too
    no_confirm = f.model_copy(update={"steps": [a, b.model_copy(update={"confirm": None})]})
    assert steps_hash(no_confirm) != base
    no_locator = f.model_copy(update={"steps": [a.model_copy(update={"locator": None}), b]})
    assert steps_hash(no_locator) != base


def test_a_new_defaulted_field_leaves_existing_digests_untouched() -> None:
    """ADDITIVE SAFETY, proven rather than asserted. `_canon` omits any field still at its declared
    default, so growing a model additively (which this codebase does often, with an explicit "NO schema
    bump needed") cannot silently re-approval-gate every flow in every fleet. Simulated here by canon'ing
    an OLD-shaped step through the CURRENT basis: the fields added since are absent from the bytes
    entirely, not present-as-null."""
    from ultracua import cache as c

    old_shaped = CachedStep(intent="click Buy", action="click",
                            locator=LocatorSpec(role="button", name="Buy", tag="button"),
                            precond_fingerprint="fp", mutating=True)
    row = c._canon(old_shaped, c._HASHED_STEP_FIELDS)
    for added_later in ("slot", "slot_domain", "precond_scope", "text", "coords", "tool", "args"):
        assert added_later not in row, f"{added_later} at its default must be OMITTED, not serialized"
    assert row == {"intent": "click Buy", "action": "click",
                   "precond_fingerprint": "fp", "mutating": True}
