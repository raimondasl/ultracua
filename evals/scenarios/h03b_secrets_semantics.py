"""H3 slice-2 RISK evals — secret write slots + row-key semantics + subtle correctness (group h03b).

Slice 2a is the WRITE side of H3 typed templates: it LIFTED the blanket refusal of a parameterized
write and had to do so WITHOUT ever double-submitting, suppressing, mis-canonicalizing, or leaking a
secret. This module probes that danger surface. It is deliberately pure-logic (idempotency-key math,
validate_params, save_spec serialization, source inspection, the approval gate) — no browser,
deterministic, $0 — because the risks here live in the KEY DERIVATION and the INPUT CONTRACT, not on
a page.

Scoring convention (see evals/run.py): `missing` = a still-aspirational slice-2b/2c capability isn't
built yet (type-aware canonicalization, a per-run recurring-vs-retry nonce — flips to pass as it
ships); `fail` = a SHIPPED write-safety property misbehaved (a real regression — reserved, loud).
PARTIAL CREDIT goes to the building blocks the write side rides on: the additive `slot_values` channel
on `idempotency_key`, `validate_params`' secret handling, and `save_spec`'s never-serialize-a-secret rule.
"""

from __future__ import annotations

import inspect
import os
import re

from evals.core import Ctx, expect, fail, scenario

# A secret substring used across the secret-leak checks. If any of these ever appears in a minted
# key, a serialized spec, or a resolved-but-visible surface, that is a real leak (fail loud).
_SECRET = "s3cr3t-TOKEN-4f9a2b"


# --- (1) SHIPPED: a SECRET write slot — validate_params + never-serialize ------------------------
@scenario(
    id="h03b.sem.secret_write_slot_validate",
    title="secret write slot: resolves from $env, refused in params / when unset, never serialized",
    group="h03b", tags=("slots", "secret", "writes"),
)
async def secret_write_slot_validate(ctx: Ctx):
    from ultracua.flows import FlowSpec, MutateSpec, SlotSpec, FlowReplayError, save_spec

    checks = []
    env = "UCA_EVAL_SECRET_TOK"
    orig_cwd = os.getcwd()
    orig_env = os.environ.get(env)
    try:
        # A WRITE flow whose auth token is a SECRET slot (value lives ONLY in the env, mirroring
        # LoginSpec's env-only credential rule) plus a normal parameterizable money field.
        spec = FlowSpec(
            name="paysecret", start_url="http://127.0.0.1:9/pay", goal="send the payment",
            mutate=MutateSpec(confirm_text_contains="Sent"),
            slots={"amount": SlotSpec(type="number", min=0),
                   "token": SlotSpec(secret=True, secret_env=env, required=True)})

        # env set -> the secret RESOLVES into the substitution dict (and the non-secret param
        # validates alongside it). DANGER guarded: a write that can't get its credential must fail
        # pre-flight, never actuate half-authenticated.
        os.environ[env] = _SECRET
        resolved = spec_validate(spec, {"amount": 250})
        checks.append(expect(resolved.get("token") == _SECRET and resolved.get("amount") == 250,
                             "secret slot resolves from $env (non-secret param validates alongside)",
                             f"resolved={ {k: ('***' if k == 'token' else v) for k, v in resolved.items()} }"))

        # passing the secret IN params -> refused LOUD. DANGER guarded: a secret handed through the
        # param channel would get logged / serialized / substituted as plaintext.
        try:
            spec_validate(spec, {"amount": 250, "token": "leak-me"})
            checks.append(fail("secret passed in params is refused",
                               "validate_params ACCEPTED a secret in params — it can now be logged/serialized"))
        except FlowReplayError as e:
            checks.append(expect("must not be passed in params" in str(e),
                                 "secret passed in params is refused LOUD", f"raised: {e}"))

        # env UNSET + required -> refused LOUD (never replays a demo plaintext for a missing credential).
        os.environ.pop(env, None)
        try:
            spec_validate(spec, {"amount": 250})
            checks.append(fail("required secret with env unset is refused",
                               "validate_params proceeded with no credential — a half-authed write could actuate"))
        except FlowReplayError as e:
            checks.append(expect("needs env var" in str(e),
                                 "required secret with env unset is refused LOUD", f"raised: {e}"))

        # save_spec of a secret-slot flow writes the env-var NAME but NEVER the secret VALUE.
        # DANGER guarded: a serialized secret leaks to disk / version control.
        os.environ[env] = _SECRET
        os.chdir(ctx.tmp)                         # save_spec writes under cwd/.ultracua/specs
        raw = save_spec(spec).read_text(encoding="utf-8")
        checks.append(expect(_SECRET not in raw and env in raw,
                             "save_spec persists the env-var NAME, never the secret VALUE",
                             f"secret_in_json={_SECRET in raw} envname_in_json={env in raw}"))

        # params=None (frozen replay) STILL resolves the secret from the env — a secret must never
        # replay as a frozen plaintext literal captured at demo time.
        frozen = spec_validate(spec, None)
        checks.append(expect(frozen.get("token") == _SECRET,
                             "frozen replay (params=None) resolves the secret from $env (no plaintext freeze)",
                             f"token_resolved={frozen.get('token') == _SECRET}"))
    finally:
        os.chdir(orig_cwd)
        if orig_env is None:
            os.environ.pop(env, None)
        else:
            os.environ[env] = orig_env
    return checks


def spec_validate(spec, params):
    """Local shim so the checks read cleanly (validate_params is the pure 0-LLM pre-flight)."""
    from ultracua.flows import validate_params

    return validate_params(spec, params)


# --- (2) a secret folded into a write's idempotency key must not leak ----------------------------
@scenario(
    id="h03b.sem.secret_never_leaks_into_key",
    title="a secret in slot_values hashes into the key (never appears); gate-wiring probed",
    group="h03b", tags=("slots", "secret", "idempotency", "writes"),
)
async def secret_never_leaks_into_key(ctx: Ctx):
    import ultracua.flow as flow_mod
    from ultracua.safety import idempotency_key

    checks = []
    # SHIPPED: idempotency_key returns a sha256 digest, so a secret folded through the additive
    # slot_values channel is one-way — it cannot be recovered from the key. DANGER guarded: a write's
    # Idempotency-Key travels on the wire / into logs; a plaintext secret there would leak broadly.
    k = idempotency_key("flow:pay", 2, "send the payment", slot_values={"token": _SECRET})
    checks.append(expect(_SECRET not in k and k.startswith("uca-"),
                         "a secret slot value does NOT appear in the minted key (sha256, one-way)",
                         f"key={k} contains_secret={_SECRET in k}"))
    # the digest is fixed-width regardless of the secret's length, so the key can't even leak the
    # secret's SIZE (uca- + 24 hex chars whether the token is 4 chars or 400).
    short = idempotency_key("flow:pay", 2, "send the payment", slot_values={"token": "x"})
    long = idempotency_key("flow:pay", 2, "send the payment", slot_values={"token": "z" * 400})
    checks.append(expect(len(short) == len(long) == len("uca-") + 24,
                         "key width is constant (the secret's length never leaks either)",
                         f"len_short={len(short)} len_long={len(long)}"))

    # SHIPPED (2a): the WRITE GATE (flow._replay_step) folds the run's slot values into the key it mints,
    # so a per-row write carries a per-row Idempotency-Key — and a SECRET slot routes through this SAME
    # hashed channel (never a header/log plaintext). The gate now mints
    # `idempotency_key(scope, idx, step.intent, slot_values=params)`. DANGER guarded: the pre-2a shape
    # (no slot channel) meant every parameterized row shared ONE key (silent suppressed-write) and a
    # secret would have had no one-way path — this going RED is a real regression.
    src = inspect.getsource(flow_mod._replay_step)
    m = re.search(r"idempotency_key\((.*?)\)", src, re.S)
    gate_folds = bool(m and ("slot" in m.group(1) or "param" in m.group(1)))
    checks.append(expect(gate_folds,
                         "the write gate folds the run's slot values into the minted key (hashed channel)",
                         f"gate mints idempotency_key({m.group(1).strip() if m else '?'}) — no per-row / secret channel"))
    return checks


# --- (3) recurring write (run-invariant key) vs parameterized write (per-row key) ----------------
@scenario(
    id="h03b.sem.recurring_vs_row_keys",
    title="base key is run-invariant (recurring write) while slot values vary it (per row); nonce probed",
    group="h03b", tags=("slots", "idempotency", "writes"),
)
async def recurring_vs_row_keys(ctx: Ctx):
    from ultracua.safety import idempotency_key

    checks = []
    # SHIPPED: the base (scope, step_index, intent) is run-INVARIANT — a RECURRING write (same flow,
    # no params, run daily) mints the SAME key every day.  DANGER: this is exactly what a same-row
    # RETRY needs (a backend dedupe drops the duplicate), but for a legitimately recurring write it is
    # TTL-dependent — a server dedupe window longer than the recurrence would SUPPRESS tomorrow's real
    # write. ultracua keeps no durable "already committed" ledger by design (MutateSpec docstring), so
    # the mint stays run-invariant and the SERVER's dedupe TTL must be short. Assert the invariance.
    day1 = idempotency_key("flow:reorder", 4, "place the daily reorder")
    day2 = idempotency_key("flow:reorder", 4, "place the daily reorder")
    checks.append(expect(day1 == day2 and day1.startswith("uca-"),
                         "no-slot key is run-invariant (recurring/retry reuse the SAME key)",
                         f"day1={day1} day2={day2}"))

    # SHIPPED: distinct slot values -> distinct keys, so N parameterized rows mint N keys. DANGER
    # guarded: without this, 500 rows would share one key and a dedupe layer silently drops rows 2..N
    # (the silent suppressed-write H3 risk).
    row_a = idempotency_key("flow:reorder", 4, "place the reorder", slot_values={"sku": "A-1"})
    row_b = idempotency_key("flow:reorder", 4, "place the reorder", slot_values={"sku": "B-2"})
    checks.append(expect(row_a != row_b,
                         "distinct slot values -> distinct per-row keys (no cross-row dedupe collision)",
                         f"row_a={row_a} row_b={row_b}"))

    # SHIPPED: None and {} both collapse to the BASE key, byte-identical — an existing single-write
    # flow is unchanged when the payload channel is added (no accidental key churn = no double-write).
    base = idempotency_key("flow:reorder", 4, "place the reorder")
    checks.append(expect(base
                         == idempotency_key("flow:reorder", 4, "place the reorder", slot_values=None)
                         == idempotency_key("flow:reorder", 4, "place the reorder", slot_values={}),
                         "slot_values None/{} are byte-identical to the base key (single-write flows unchanged)"))

    # ASPIRATIONAL: a run-invariant key cannot tell a LEGITIMATE recurring write (run again tomorrow)
    # apart from a RETRY (must dedupe) — they are the same three inputs. Slice 2 must answer this
    # (a per-run nonce / date-stamped basis / attempt token). Probe the derivation for any such
    # channel; today the basis is frozen at (scope, step_index, intent, slot_values) -> missing.
    sig = inspect.signature(idempotency_key)
    nonce_names = {"nonce", "date", "day", "run_id", "run", "attempt", "ts", "epoch", "occurrence"}
    has_nonce = bool(nonce_names & set(sig.parameters)) or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    checks.append(expect(has_nonce,
                         "a per-run nonce/date channel distinguishes a recurring write from a retry",
                         f"basis frozen at {tuple(sig.parameters)} — recurrence and retry are indistinguishable",
                         aspirational=True))
    return checks


# --- (4) canonicalization robustness of the row key across type / format -------------------------
@scenario(
    id="h03b.sem.key_canonicalization",
    title="row-key canonicalization is deterministic across type/dict-order; type-distinction probed",
    group="h03b", tags=("slots", "idempotency", "writes", "correctness"),
)
async def key_canonicalization(ctx: Ctx):
    from ultracua.safety import idempotency_key

    checks = []
    # SHIPPED: values are canonicalized (sorted keys, str() values) so the digest CANNOT WOBBLE across
    # runs. DANGER guarded: a wobble (the SAME logical row hashing to a DIFFERENT key on retry) would
    # defeat the server dedupe and DOUBLE-WRITE. Prove the two wobble vectors are pinned:
    # (a) dict insertion order is irrelevant.
    order_1 = idempotency_key("flow:pay", 2, "pay", slot_values={"amount": "10", "sku": "A"})
    order_2 = idempotency_key("flow:pay", 2, "pay", slot_values={"sku": "A", "amount": "10"})
    checks.append(expect(order_1 == order_2,
                         "dict insertion order does NOT change the key (no order-wobble double-write)",
                         f"{order_1} vs {order_2}"))
    # (b) the same row hashed twice is byte-identical (the retry-dedupe guarantee).
    retry_1 = idempotency_key("flow:pay", 2, "pay", slot_values={"amount": "10"})
    retry_2 = idempotency_key("flow:pay", 2, "pay", slot_values={"amount": "10"})
    checks.append(expect(retry_1 == retry_2,
                         "same row hashed twice is byte-identical (a retry dedupes, never double-writes)",
                         f"{retry_1} vs {retry_2}"))
    # (c) str() coercion is stable: an int 1 and a str "1" canonicalize identically, so a value that
    #     arrives once as JSON int and once as string for the SAME row still mints one key (no wobble).
    coerce_int = idempotency_key("flow:pay", 2, "pay", slot_values={"amount": 1})
    coerce_str = idempotency_key("flow:pay", 2, "pay", slot_values={"amount": "1"})
    checks.append(expect(coerce_int == coerce_str,
                         "int 1 and str '1' canonicalize to ONE key (stable str() coercion, no wobble)",
                         f"{coerce_int} vs {coerce_str}"))
    # (d) INJECTIVITY: the encoding must be one-to-one across the '|'/'=' delimiters. A naive
    #     "|".join(f"{k}={v}") collides two DISTINCT free-text rows (a memo/note slot admits '|' and '='),
    #     minting ONE key for two rows -> a backend dedupe silently DROPS the second (a suppressed write).
    #     DANGER guarded: this is the canonicalization-collision suppressed-write vector.
    collide_a = idempotency_key("flow:pay", 2, "pay", slot_values={"memo": "a|payee=b", "payee": "c"})
    collide_b = idempotency_key("flow:pay", 2, "pay", slot_values={"memo": "a", "payee": "b|payee=c"})
    checks.append(expect(collide_a != collide_b,
                         "delimiter-bearing distinct rows mint DISTINCT keys (injective, no collision-suppress)",
                         f"two distinct rows collided to one key: {collide_a} vs {collide_b}"))

    # ASPIRATIONAL (the flip side of (c)): str() coercion kills the wobble but COLLAPSES type-distinct
    # values — 1 and "1" are indistinguishable. A slice-2 row-key layer folding multiple slots must
    # decide whether two rows differing ONLY by JSON type deserve distinct keys; if not, one silently
    # SUPPRESSES the other. Probe for a type-aware / canonical-JSON derivation that keeps them
    # distinct. Today they collide -> the type-preserving canonicalization isn't built (missing).
    checks.append(expect(coerce_int != coerce_str,
                         "row-key canonicalization is type-aware (int 1 vs str '1' -> distinct keys)",
                         "int 1 and str '1' collide today — a type-distinct row could be suppressed",
                         aspirational=True))
    return checks


# --- (5) SHIPPED (2a): the blanket parameterized-WRITE refusal is LIFTED; approval now guards ------
@scenario(
    id="h03b.sem.parameterized_write_no_blanket_refusal",
    title="the blanket parameterized-WRITE refusal is lifted (2a); a declared write row validates, approval gates",
    group="h03b", tags=("slots", "writes", "fail-loud"),
)
async def parameterized_write_no_blanket_refusal(ctx: Ctx):
    from ultracua.flows import FlowSpec, MutateSpec, SlotSpec, FlowReplayError, replay, validate_params

    checks = []
    cache = ctx.cache()
    wspec = FlowSpec(
        name="wpay", start_url="http://127.0.0.1:9/pay", goal="send the payment",
        mutate=MutateSpec(confirm_text_contains="Sent"),
        slots={"amount": SlotSpec(type="number", min=0)})

    # SHIPPED (2a): the slice-1 BLANKET refusal of a parameterized write is LIFTED. A well-formed row for
    # a DECLARED write slot resolves through the pure 0-LLM pre-flight — there is no "writes aren't
    # supported" ban any more. DANGER guarded: the lift must be REAL (this is the write side going live),
    # not a silent no-op.
    resolved = validate_params(wspec, {"amount": 250})
    checks.append(expect(resolved == {"amount": 250},
                         "a declared write row validates 0-LLM (no blanket parameterized-write refusal)",
                         f"resolved={resolved!r}"))

    # SHIPPED (2a): what actually gates THIS unlearned flow is the standard APPROVAL gate (a write is
    # human-verified before an unattended run) — NOT a param ban. DANGER guarded: lifting the param
    # refusal must not open an UNAPPROVED write path; the write still cannot run un-approved.
    raised = "NONE"
    try:
        await replay(wspec, params={"amount": 250}, cache=cache)
    except FlowReplayError as e:
        raised = str(e)
    except Exception as e:  # noqa: BLE001 — a WRONG exception type is itself a regression
        raised = f"__WRONG__ {type(e).__name__}: {e}"
    lo = raised.lower()
    checks.append(expect("not approved" in lo,
                         "an unlearned/unapproved parameterized write is refused by the APPROVAL gate (not a param ban)",
                         f"expected the approval gate, got: {raised[:180]}"))
    # DANGER guarded: the refusal must NOT be the retired blanket 'writes aren't supported' message — its
    # resurfacing would mean the lift silently regressed.
    checks.append(expect("aren't supported" not in lo and "next slice" not in lo,
                         "the refusal is the approval gate, not the retired blanket param-write ban",
                         f"a stale blanket-refusal message resurfaced: {raised[:180]}"))

    # SHIPPED: a READ template with params is likewise not blocked by any phantom write refusal (it fails
    # later for its own reason — no learned flow). DANGER guarded: an over-broad refusal would freeze the
    # legitimate slice-1 READ templates.
    rspec = FlowSpec(name="rread", start_url="http://127.0.0.1:9/x", goal="read the total",
                     slots={"amount": SlotSpec(type="number")})
    hit_blanket = False
    try:
        await replay(rspec, params={"amount": 250}, cache=cache)
    except FlowReplayError as e:
        hit_blanket = "aren't supported" in str(e).lower() or "next slice" in str(e).lower()
    checks.append(expect(not hit_blanket,
                         "a READ template with params hits no phantom write refusal (slice-1 reads unaffected)",
                         "a read flow hit a parameterized-WRITE blanket refusal — a stale ban resurfaced"))
    return checks
