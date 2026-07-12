"""H3 slice-2 RISK evals — the idempotency key as the double-write / suppressed-write safety core.

Slice 2a (write templates + row-keyed idempotency) IS shipped: a parameterized WRITE flow runs each
row through one learned form-submit. The single load-bearing safety artifact is the per-write
`Idempotency-Key` header:

  - DISTINCT rows must mint DISTINCT keys — else 500 rows share ONE key and a backend dedupe layer
    silently DROPS rows 2..N (a suppressed-write: the operator believes 500 landed; 1 did).
  - the SAME row on a retry must mint the SAME key — else a transient retry mints a fresh key and
    the backend can't dedupe it, DOUBLE-WRITING the row (charge twice, ship twice).

The shipped building block underneath is `safety.idempotency_key(..., slot_values=)`: an additive
payload channel (slice 1) that canonicalizes a row into the dedupe basis, which the write actuation
gate (`flow._replay_step`) now FOLDS the run's slot values into. This module PASSES that channel's
determinism/canonicalization and its no-op-when-None guarantee (existing single-write flows keep
byte-identical keys), and verifies END-TO-END that a parameterized WRITE reaches the fixture with an
`Idempotency-Key` that DIFFERS per row and REPEATS per retry. (The batch driver `run_batch` and the
per-row resume ledger — slices 2b/2c — remain aspirational; see h03b_batch.)

Key-less: local `Fixture` (records each write's headers, lower-cased) + real headless Chromium, $0.
The SERVER's recorded header is the oracle — never what the client believes it sent.
"""

from __future__ import annotations

import inspect
import re

from evals.core import Ctx, expect, scenario
from evals.fixtures import Fixture, page

# The shared checkout shape: a REAL method=post form so the submit is classified mutating by the
# form's METHOD (the structural signal), routing it through the mutation gate + idempotency key.
_CONFIRM = page("<h1>Order placed</h1><p>Confirmation #A1</p>", title="confirm")
_CHECKOUT = page('<h1>Checkout</h1><p>cart: 1 widget, not ordered yet</p>'
                 '<form method="post" action="/order">'
                 '<input type="hidden" name="qty" value="1">'
                 '<button type="submit">Place the order</button></form>')
# A parameterized-write shape: a typed quantity field (the future slot site) + the submit button.
_QTY_CHECKOUT = page('<h1>Checkout</h1><p>cart: not ordered yet</p>'
                     '<form method="post" action="/order">'
                     '<label for="qty">quantity</label>'
                     '<input id="qty" name="qty" value="1">'
                     '<button type="submit">Place the order</button></form>')

# The base idempotency basis reused across the determinism scenarios.
_SC, _IDX, _INT = "flow:daily-signups", 4, "submit the row"


# --- (1) SHIPPED: slot_values determinism + canonicalization -----------------------------------
@scenario(
    id="h03b.idem.slot_values_determinism",
    title="idempotency_key(slot_values=...) — distinct rows -> distinct keys, same row -> same key, stable",
    group="h03b", tags=("idempotency", "slots", "writes"),
)
async def slot_values_determinism(ctx: Ctx):
    from ultracua.safety import idempotency_key

    checks = []
    # SAME row -> SAME key. Danger guarded: a wobbling key across a RETRY of one row would defeat the
    # backend dedupe and DOUBLE-WRITE that row (charge/ship twice).
    rowA = {"email": "a@x.io", "name": "Al"}
    k1 = idempotency_key(_SC, _IDX, _INT, slot_values=rowA)
    k2 = idempotency_key(_SC, _IDX, _INT, slot_values=dict(rowA))
    checks.append(expect(k1 == k2 and k1.startswith("uca-"),
                         "same row (same slot_values) -> the same stable uca- key (retry-safe, no double-write)",
                         f"{k1} vs {k2}"))
    # DISTINCT rows -> DISTINCT keys. Danger guarded: if two rows collapsed to ONE key, a backend
    # dedupe would silently SUPPRESS the second, distinct write (rows 2..N vanish).
    kB = idempotency_key(_SC, _IDX, _INT, slot_values={"email": "b@x.io", "name": "Al"})
    checks.append(expect(k1 != kB,
                         "a different row (one slot value changed) -> a different key (no suppressed write)",
                         f"{k1} vs {kB}"))
    # KEY-ORDER INDEPENDENCE: the SAME row supplied with reordered dict keys must hash identically.
    # Danger guarded: an order-sensitive digest would make ONE row hash two ways on two runs => the
    # retry looks like a new row => double-write.
    k_reordered = idempotency_key(_SC, _IDX, _INT, slot_values={"name": "Al", "email": "a@x.io"})
    checks.append(expect(k1 == k_reordered,
                         "dict-key order does not change the key (sorted canonicalization is stable)",
                         f"{k1} vs {k_reordered}"))
    # CANONICALIZATION across scalar types: {"qty": 2} and {"qty": "2"} are the SAME row (str() basis)
    # AND numbers still DISTINGUISH ({"qty": 2} != {"qty": 3}). Danger guarded: an int-vs-str split
    # would either collide two rows (suppressed-write) or fork one row (double-write) depending on how
    # the caller happened to type the value.
    k_int2 = idempotency_key(_SC, _IDX, _INT, slot_values={"qty": 2})
    k_str2 = idempotency_key(_SC, _IDX, _INT, slot_values={"qty": "2"})
    k_int3 = idempotency_key(_SC, _IDX, _INT, slot_values={"qty": 3})
    checks.append(expect(k_int2 == k_str2 and k_int2 != k_int3,
                         "numbers canonicalize via str() (2 == \"2\") yet distinct values stay distinct",
                         f"int2={k_int2} str2={k_str2} int3={k_int3}"))
    return checks


# --- (2) SHIPPED risk-framing: slot_values=None is byte-identical to the base key ---------------
@scenario(
    id="h03b.idem.none_is_base_key",
    title="idempotency_key(slot_values=None/{}) is byte-identical to the base — single-write flows unchanged",
    group="h03b", tags=("idempotency", "writes"),
)
async def none_is_base_key(ctx: Ctx):
    from ultracua.safety import idempotency_key

    checks = []
    base = idempotency_key(_SC, _IDX, _INT)
    # slot_values=None must reproduce the base key EXACTLY. Danger guarded: the additive channel must
    # not shift any existing key — every already-APPROVED single-write flow keys on this exact value,
    # so a drift here would make each next replay mint a NEW key the backend can't match => DOUBLE-WRITE
    # the whole fleet's writes on their first post-upgrade run.
    k_none = idempotency_key(_SC, _IDX, _INT, slot_values=None)
    checks.append(expect(k_none == base and base.startswith("uca-"),
                         "slot_values=None -> byte-identical to the base (scope, step, intent) key",
                         f"base={base} none={k_none}"))
    # An EMPTY dict must also collapse to the base (a 'parameterizing but no values' call is not a new row).
    k_empty = idempotency_key(_SC, _IDX, _INT, slot_values={})
    checks.append(expect(k_empty == base,
                         "slot_values={} -> also byte-identical to the base key (empty is not a new row)",
                         f"base={base} empty={k_empty}"))
    # ...but a NON-EMPTY row MUST diverge, proving the channel is REAL (not a silent no-op). Danger
    # guarded: if a filled row collapsed to the base, a parameterized write would collide with the
    # frozen single-write flow and a dedupe could SUPPRESS the real row.
    k_filled = idempotency_key(_SC, _IDX, _INT, slot_values={"qty": "1"})
    checks.append(expect(k_filled != base,
                         "a non-empty row diverges from the base (the payload channel actually fires)",
                         f"base={base} filled={k_filled}"))
    return checks


# --- (3) SHIPPED (2a): the WRITE actuation gate folds the run's slot values into the minted key --
@scenario(
    id="h03b.idem.gate_folds_slot_values",
    title="write-gate idempotency key folds the run's slot values (source-inspect) — shipped in slice 2a",
    group="h03b", tags=("idempotency", "writes"),
)
async def gate_folds_slot_values(ctx: Ctx):
    import ultracua.flow as flow_mod
    from ultracua.safety import idempotency_key

    checks = []
    src = inspect.getsource(flow_mod._replay_step)
    # SHIPPED (2a): the write actuation gate folds the run's slot values into the key it mints — the call
    # is now `idempotency_key(scope, idx, step.intent, slot_values=params)`. Danger guarded: without this
    # fold (the pre-2a shape), 500 parameterized rows would mint ONE key and a backend dedupe would silently
    # DROP rows 2..N (the suppressed-write core of the write side). This going RED is a real regression.
    calls = re.findall(r"idempotency_key\(([^)]*)\)", src)
    folds = any(("slot" in a) or ("params" in a) for a in calls)
    checks.append(expect(folds,
                         "the write-gate idempotency_key call folds slot_values/params (distinct rows -> distinct keys)",
                         f"gate mints {calls!r} — no row-value channel, so parameterized rows would share ONE key"))
    # PARTIAL CREDIT (shipped): the substitution machinery is already HALF-wired — _replay_step threads
    # `params` and substitutes a validated value at type/select sites. So the key-fold is a small
    # wire-up ALONGSIDE existing plumbing, not a rewrite (proves the gap is narrow, and that lifting the
    # replay guard without the fold is a live foot-gun rather than a far-off one).
    rs_params = "params" in inspect.signature(flow_mod._replay_step).parameters
    substitutes = "step.slot" in src and "params[step.slot]" in src
    checks.append(expect(rs_params and substitutes,
                         "shipped: _replay_step already threads params + substitutes at type/select sites",
                         f"params_param={rs_params} substitutes={substitutes}"))
    # PARTIAL CREDIT (shipped): the key derivation ALREADY accepts a slot_values channel (slice 1) —
    # the gate just doesn't pass it yet. The receiving end of the fold is built and canonicalized.
    checks.append(expect("slot_values" in inspect.signature(idempotency_key).parameters,
                         "shipped: safety.idempotency_key already accepts a slot_values channel (fold target ready)",
                         f"params={tuple(inspect.signature(idempotency_key).parameters)}"))
    # PARTIAL CREDIT (shipped): the gate already MINTS a key and SETS the Idempotency-Key header today,
    # so the frozen single-write dedupe surface is in place — slice 2 extends it, doesn't invent it.
    mints_header = "idempotency_key(" in src and "set_extra_http_headers" in src and "Idempotency-Key" in src
    checks.append(expect(mints_header,
                         "shipped: the gate mints a key and sets the Idempotency-Key header on every write",
                         f"mints_header={mints_header}"))
    return checks


# --- (4) SHIPPED behavioral (2a): parameterized WRITE runs row-keyed — per-row distinct, per-retry stable --
@scenario(
    id="h03b.idem.parameterized_write_row_keyed",
    title="parameterized WRITE runs row-keyed: distinct rows -> distinct Idempotency-Key, retry -> same key",
    group="h03b", tags=("idempotency", "writes", "slots"),
)
async def parameterized_write_row_keyed(ctx: Ctx):
    from ultracua.cache import flow_key
    from ultracua.flows import (FlowReplayError, FlowSpec, MutateSpec, SlotSpec, approve,
                                record, replay, validate_params)

    checks = []
    fx = Fixture({"/checkout": _QTY_CHECKOUT, "/confirm": _CONFIRM}, post_redirect="/confirm")
    with fx.serve() as base:
        cache = ctx.cache()
        # A parameterized-write template in shape: a WRITE flow (mutate set) with a typed `qty` slot.
        spec = FlowSpec(name="paramorder", start_url=f"{base}/checkout", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Order placed"),
                        slots={"qty": SlotSpec(type="string", pattern="[0-9]{1,3}")}, headless=True)

        async def _demo(pw_page) -> None:  # scripted 'human' demonstration — key-less
            await pw_page.fill("#qty", "7")
            await pw_page.locator("#qty").blur()               # change fires on blur -> a `type` step
            await pw_page.get_by_role("button", name="Place the order").click()
            await pw_page.get_by_text("Order placed").wait_for()

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        # PARTIAL CREDIT (shipped): the recorder captures the write flow with the fill FROZEN — the exact
        # slot SITE a future write-template will substitute over (its existence is the precondition slice 2
        # rides on).
        typed = next((s for s in res.steps if s.action == "type"), None)
        checks.append(expect(res.cached and res.is_write and typed is not None and typed.text == "7",
                             "shipped: recorder captures the write flow with the frozen slot-able fill site",
                             f"cached={res.cached} is_write={res.is_write} typed={typed.text if typed else None}"))
        # Best-effort: mark the fill step as the `qty` slot so the 'once built' branch below would actually
        # substitute. Write-slot auto-mining is (correctly) refused, so we mark it explicitly here.
        try:
            flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
            for s in flow.steps:
                if s.action == "type":
                    s.slot = "qty"
            cache.put(flow)
        except Exception:  # noqa: BLE001 — the guard-refused path below does not depend on this
            pass
        approve(spec, cache=cache)  # writes are approval-gated

        # PARTIAL CREDIT (shipped): the 0-LLM pre-flight validator ALREADY accepts a well-formed row for a
        # WRITE spec (validate_params is pure and mutate-agnostic) — the READ-side contract is ready; only
        # the write ACTUATION is gated. This is the surface slice 2 unlocks, proven safe in isolation.
        resolved = validate_params(spec, {"qty": "9"})
        checks.append(expect(resolved == {"qty": "9"},
                             "shipped: pre-flight validate_params accepts a valid row for the write spec (0-LLM)",
                             f"resolved={resolved}"))

        writes_before = len(fx.writes)   # after the demo's one write; each replay must add EXACTLY one

        async def _row_key(row):
            """Replay one row (2a: it RUNS) and return the SERVER-recorded Idempotency-Key for its write."""
            await replay(spec, params=row, cache=cache)
            return fx.writes[-1].headers.get("idempotency-key")

        kA = await _row_key({"qty": "9"})
        kB = await _row_key({"qty": "8"})
        kA2 = await _row_key({"qty": "9"})   # a re-run of the SAME row

        # SHIPPED (2a): every parameterized write reached the server carrying a uca- Idempotency-Key — the
        # header a backend dedupe keys on. (The SERVER's recorded header is the oracle, not the client.)
        checks.append(expect(all(isinstance(k, str) and k.startswith("uca-") for k in (kA, kB, kA2)),
                             "each parameterized write reached the server with a uca- Idempotency-Key",
                             f"kA={kA!r} kB={kB!r} kA2={kA2!r}"))
        # SHIPPED — no SUPPRESSED write: DISTINCT rows send DISTINCT keys, so a backend dedupe layer cannot
        # collapse them and silently drop the distinct row.
        checks.append(expect(kA != kB,
                             "DISTINCT rows send DISTINCT Idempotency-Key headers (no suppressed write)",
                             f"rowA={kA} rowB={kB}"))
        # SHIPPED — no DOUBLE write: a re-run of the SAME row repeats the SAME key, so a retry dedupes at
        # the backend instead of writing the row a second time.
        checks.append(expect(kA == kA2,
                             "a re-run of the SAME row repeats the SAME Idempotency-Key (no double-write)",
                             f"first={kA} rerun={kA2}"))
        # SHIPPED — exactly ONE write per replay: the mutation gate never double-fires, and each row DID
        # actuate (no silently-suppressed row). Three replays -> exactly three writes on the wire.
        checks.append(expect(len(fx.writes) - writes_before == 3,
                             "each of the 3 row replays sent exactly ONE write (no double-fire, no dropped row)",
                             f"writes grew by {len(fx.writes) - writes_before}, expected 3"))
    return checks


# --- (5) SHIPPED baseline: a frozen single-write replay carries a stable Idempotency-Key header --
@scenario(
    id="h03b.idem.frozen_write_carries_key",
    title="a non-parameterized single-write replay carries a stable Idempotency-Key header (retry-safe)",
    group="h03b", tags=("idempotency", "writes", "baseline"),
)
async def frozen_write_carries_key(ctx: Ctx):
    from ultracua.flows import FlowSpec, MutateSpec, approve, record

    checks = []
    fx = Fixture({"/checkout": _CHECKOUT, "/confirm": _CONFIRM}, post_redirect="/confirm")
    with fx.serve() as base:
        cache = ctx.cache()
        spec = FlowSpec(name="frozenorder", start_url=f"{base}/checkout", goal="place the order",
                        mutate=MutateSpec(confirm_text_contains="Order placed"), headless=True)

        async def _demo(pw_page) -> None:
            await pw_page.get_by_role("button", name="Place the order").click()
            await pw_page.get_by_text("Order placed").wait_for()

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        # The demo lands EXACTLY one write (a write is never verify-by-replayed — that would double-submit).
        n0 = len(fx.writes)
        checks.append(expect(res.cached and res.is_write and n0 == 1 and fx.writes[0].path == "/order",
                             "record lands exactly one confirmed write (write is not re-fired to verify)",
                             f"cached={res.cached} is_write={res.is_write} writes={n0}"))
        approve(spec, cache=cache)

        from ultracua.flows import replay
        await replay(spec, cache=cache)
        n1 = len(fx.writes)
        key1 = fx.writes[-1].headers.get("idempotency-key")
        await replay(spec, cache=cache)
        n2 = len(fx.writes)
        key2 = fx.writes[-1].headers.get("idempotency-key")

        # The replayed write carried the Idempotency-Key the gate mints — the header a server dedupe keys on.
        checks.append(expect((key1 or "").startswith("uca-"),
                             "the replayed write carried a uca- Idempotency-Key header (dedupe-able)",
                             f"key1={key1!r}"))
        # EXACTLY one write per replay — the mutation gate never double-fires a single-write flow.
        checks.append(expect(n1 - n0 == 1 and n2 - n1 == 1,
                             "each replay sends exactly ONE write (no double-fire)",
                             f"delta1={n1 - n0} delta2={n2 - n1}"))
        # Two replays of the SAME frozen row mint the SAME key — the retry-dedupe property (basis is the
        # run-INVARIANT scope/step/intent). Danger guarded: a per-run-random key would make a retried write
        # look brand-new to the backend and DOUBLE-WRITE it.
        checks.append(expect(key1 is not None and key1 == key2,
                             "a second replay repeats the SAME Idempotency-Key (retry-safe, dedupe-able)",
                             f"key1={key1!r} key2={key2!r}"))
    return checks
