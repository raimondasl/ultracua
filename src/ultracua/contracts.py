"""H9 layer 1 — deterministic per-field VALUE contracts (fail loud on a same-shape-but-WRONG value).

Today replay checks the extracted data's SHAPE (structure) but not its VALUES, so a same-shape but wrong
value — a price that went 129 -> 0, a date field that became null, a list of 500 rows that collapsed to 3 —
is returned as if correct. That is the one thing this project forbids ("shape-drift can't see wrong-but-present
values"). This module is the PURE, 0-LLM, key-less predicate layer: it auto-derives a conservative contract
from ONE learned extraction (`seed_contracts`) and checks a replayed value against the effective contract
(`check_contracts`). flows.py owns the wiring (persisted quarantine, the health status, `flow release`).

DESIGN GUARANTEES:
- ZERO LLM: every predicate is pure Python (inviolable #1 — replay never calls an LLM).
- VALUE-FREE reasons: a violation reason carries only type names, counts, bounds, and rates — NEVER an
  extracted value — because it is persisted into `<key>.meta.json` (no secrets / PII at rest).
- SINGLE-SAMPLE-SAFE seeding: only checks with ~zero legitimate variance from one learn run are seeded
  (type, non-null presence, positive sign, a high-confidence format, a generous list count-floor). Numeric
  min/max range and rolling-median delta are NOT seeded (meaningless from one sample) — deferred to 1b.

FIELD-PATH SCHEME (mirrors flows._shape_of's shallow, depth-1 traversal):
  ""          the root value (a root scalar's contract, OR a root list's min_count/null-rate)
  "<key>"     data[key] for a root DICT with a scalar value
  "[]"        each item of a root LIST of scalars (common type)
  "[].<key>"  item[key] across a root LIST of dicts
A root dict whose value is itself a list/dict is out of scope for slice 1 (documented depth-1 limit).
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class FieldContract:
    """The closed vocabulary of per-field predicates (also the allow-list for a CLI `--set`). Seeds are stored
    as plain dicts (not instances) so the hot path reads dict keys directly — this is the schema + doc."""

    type: Optional[str] = None          # "number" | "string" | "bool" (the _shape_of primitive vocabulary)
    pattern: Optional[str] = None       # a full-match regex the string value must satisfy
    nullable: bool = True               # False (seeded for a learned-non-null single value) => a null trips
    positive: bool = False              # True (seeded when a learned number > 0) => 0/negative trips
    min: Optional[float] = None         # numeric lower bound (HUMAN-set in slice 1; not auto-seeded)
    max: Optional[float] = None         # numeric upper bound (HUMAN-set in slice 1; not auto-seeded)
    min_count: Optional[int] = None     # list count floor (a >50% collapse trips)
    null_rate_max: Optional[float] = None  # ceiling on the null fraction across list items
    enabled: bool = True                # per-field off switch (the narrow escape hatch vs a blanket release)
    # H9 layer 2 — deterministic MAGNITUDE defense (scalar numbers): a value too far from the field's own
    # rolling numeric baseline fails loud (catches a wrong-but-same-sign 129→40). All human-overridable.
    max_delta_frac: Optional[float] = None  # fractional tolerance floor vs the rolling median (override)
    delta_k: Optional[float] = None     # MAD multiplier — the band self-calibrates to the field's spread (override)
    warmup_runs: Optional[int] = None   # clean samples before ENFORCING (advisory until then) (override)
    delta_advisory: bool = False        # log-only forever for this field (never quarantines on magnitude)
    delta_enabled: Optional[bool] = None  # per-field magnitude off-switch (distinct from `enabled` = all checks)


CONTRACT_ATTRS = frozenset(f.name for f in dataclasses.fields(FieldContract))

# H9 layer-2 magnitude defaults (module consts; per-field overrides live in the approval-hashed overlay).
DELTA_K = 5.0            # ≈5σ band (via the MAD→σ rescale below) — passes ordinary jitter, catches gross moves
DELTA_FLOOR_FRAC = 0.25  # a value must move >25% of |median| to trip a near-constant field (closes 129→40)
DELTA_WARMUP = 5         # clean samples accrued before the check ENFORCES (advisory before that)
DELTA_RING = 20          # rolling window size (bounded; robust median/MAD, resists single-sample poisoning)
DELTA_ABS_EPS = 1e-9     # a floor for the degenerate zero-centered field (no magnitude scale to compare)
_MAD_SIGMA = 1.4826      # rescales the median-absolute-deviation to a normal-consistent σ estimate

# High-confidence string recognizers only — a pattern is seeded ONLY when EVERY learned value matches one of
# these (never overfit an arbitrary string). Ordered specific-first.
_RECOGNIZERS = (
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",  # ISO datetime
    r"\d{4}-\d{2}-\d{2}",                       # ISO date
    r"[^@\s]+@[^@\s]+\.[^@\s]+",                 # email
    r"\d+",                                      # all digits
    r"-?\d+\.\d+",                               # decimal
)


def contract_type(v: Any) -> Optional[str]:
    """The primitive type name (mirrors flows._shape_of). None for null / dict / list (not a leaf scalar).
    bool is checked before int since bool is an int subclass."""
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    return None


def safe_pattern(strings: list) -> Optional[str]:
    """A high-confidence regex that ALL `strings` fully match, else None. Anchored on return."""
    vals = [s for s in strings if isinstance(s, str)]
    if not vals or len(vals) != len(strings):
        return None
    for rx in _RECOGNIZERS:
        if all(re.fullmatch(rx, s) for s in vals):
            return rx
    return None


def _is_scalar(v: Any) -> bool:
    return not isinstance(v, (dict, list))


def _seed_single(value: Any) -> Optional[dict]:
    """Contract for a SINGLE learned scalar (a root scalar or a root-dict key): strict on null (one value, a
    null is unambiguously wrong), plus sign / type / a high-confidence format."""
    t = contract_type(value)
    if t is None:  # null or non-scalar at learn -> nothing safe to assert
        return None
    c: dict = {"type": t, "nullable": False}
    if t == "number" and value > 0:
        c["positive"] = True
    if t == "string":
        p = safe_pattern([value])
        if p:
            c["pattern"] = p
    return c


def _seed_items(values: list, *, truncated: bool) -> Optional[dict]:
    """Contract for the ITEMS of a list (scalar items, or one object-key across rows): tolerant on nulls (a
    single missing row shouldn't trip) via a null-rate ceiling, plus a consistent type / sign / format over
    the non-null items. A truncated learn seeds NO null-rate (the invisible tail makes it unrepresentative)."""
    non_null = [v for v in values if v is not None]
    if not non_null:
        return None
    c: dict = {}
    if not truncated:
        c["null_rate_max"] = 0.2
    types = {contract_type(v) for v in non_null}
    if len(types) == 1 and None not in types:
        t = next(iter(types))
        c["type"] = t
        if t == "number" and all(v > 0 for v in non_null):
            c["positive"] = True
        if t == "string":
            p = safe_pattern(non_null)
            if p:
                c["pattern"] = p
    return c or None


def _count_floor(n: int, truncated: bool) -> Optional[int]:
    """A generous list count-floor: only a >50% collapse trips (day-to-day jitter passes). NONE from a
    truncated learn (a floor from a cut list is too low, or would bless a later short list)."""
    if truncated or n < 1:
        return None
    if n >= 5:
        return max(1, n // 2)   # 0.5 * len
    return 1                    # small list -> presence only (also closes the _shape_matches empty-array hole)


def seed_contracts(data: Any, *, truncated: bool = False) -> Optional[dict]:
    """Auto-derive the machine seed `{field_path: {attr: value}}` from ONE learned extraction. Returns None
    when nothing safe can be asserted. Single-sample-safe only (no numeric range / delta). Truncation-aware."""
    contracts: dict = {}
    if isinstance(data, list):
        floor = _count_floor(len(data), truncated)
        if floor is not None:
            contracts[""] = {"min_count": floor}
        if data and all(_is_scalar(it) for it in data):
            item_c = _seed_items(data, truncated=truncated)
            if item_c:
                contracts["[]"] = item_c
        elif data and all(isinstance(it, dict) for it in data):
            keys: set = set()
            for it in data:
                keys |= set(it)
            for k in sorted(keys):
                item_c = _seed_items([it.get(k) for it in data], truncated=truncated)
                if item_c:
                    contracts["[]." + str(k)] = item_c
    elif isinstance(data, dict):
        for k in sorted(data, key=str):
            if _is_scalar(data[k]):
                c = _seed_single(data[k])
                if c:
                    contracts[str(k)] = c
    else:  # root scalar
        c = _seed_single(data)
        if c:
            contracts[""] = c
    return contracts or None


def effective_contracts(spec_contracts: Optional[dict], meta_contracts: Optional[dict]) -> dict:
    """The effective contract per field = the machine SEED overlaid PER-ATTRIBUTE by the human's sparse dict
    (a human relaxes exactly one predicate, e.g. `{"price": {"positive": false}}`, without wiping the rest)."""
    seed = meta_contracts or {}
    human = spec_contracts or {}
    eff: dict = {}
    for p in set(seed) | set(human):
        merged = dict(seed.get(p) or {})
        merged.update(human.get(p) or {})
        eff[p] = merged
    return eff


def check_contracts(eff: dict, data: Any, *, truncated: bool = False) -> Optional[str]:
    """Pure, 0-LLM. Return a VALUE-FREE reason for the FIRST violation (deterministic order), else None.

    STRICT about structure: a value that DEGRADED from the seeded shape fails loud — a scalar field that
    became a list/dict (its `type` predicate trips), or a list-of-objects that grew a non-object item. Only a
    path that structurally does not apply (a dict-key path over non-dict data, an item path over a non-list)
    is skipped — that is genuine shape drift, which the shape gate owns."""
    for path in sorted(eff):
        c = eff[path]
        if not c.get("enabled", True):
            continue
        reason = _check_path(path, c, data, truncated=truncated)
        if reason is not None:
            return reason
    return None


def _check_path(path: str, c: dict, data: Any, *, truncated: bool) -> Optional[str]:
    lbl = f"field {path!r}" if path else "the extracted value"
    if path == "[]" or path.startswith("[]."):
        if not isinstance(data, list):
            return None                    # not a list at all -> shape gate's concern, not ours
        if path == "[]":
            values = data
        else:                              # "[].<key>": each item MUST be an object (it was at learn)
            key = path[3:]
            values = []
            for it in data:
                if not isinstance(it, dict):
                    return f"{lbl}: expected a list of objects, got a non-object item"
                values.append(it.get(key))
        return _check_list(lbl, c, values, truncated=truncated)
    # a scalar-valued path: "" (a root scalar OR a root list) or "<key>" (a dict key).
    if path == "":
        val = data
    else:
        if not isinstance(data, dict) or path not in data:
            return None                    # the key vanished / non-dict root -> shape gate's concern
        val = data[path]
    # A root-list "" contract carries a list predicate (min_count / null_rate) -> treat the value as a list.
    if isinstance(val, list) and ("min_count" in c or "null_rate_max" in c):
        return _check_list(lbl, c, val, truncated=truncated)
    # Otherwise this path expects a SCALAR: a list/dict value here is a real degradation (its `type` trips).
    return _check_scalar(lbl, c, val)


def _check_list(lbl: str, c: dict, values: list, *, truncated: bool) -> Optional[str]:
    if "min_count" in c and len(values) < c["min_count"]:
        # ENFORCED even under truncation: a short truncated replay over a floor is exactly the partial data
        # we must not silently bless.
        return f"{lbl}: expected at least {c['min_count']} items, got {len(values)}"
    if "null_rate_max" in c and not truncated:
        total = len(values)
        if total:
            nulls = sum(1 for v in values if v is None)
            if nulls / total > c["null_rate_max"]:
                return f"{lbl}: null rate {nulls}/{total} exceeds the max {c['null_rate_max']}"
    for v in values:                       # per-item scalar predicates (nullable defaults True -> item nulls
        reason = _check_scalar(lbl, c, v)  # are governed by null_rate_max, not by a per-item non-null rule)
        if reason is not None:
            return reason
    return None


def _check_scalar(lbl: str, c: dict, v: Any) -> Optional[str]:
    if v is None:
        if not c.get("nullable", True):
            return f"{lbl}: expected a non-null value, got null"
        return None
    vt = contract_type(v)                  # None for a list/dict -> a degraded scalar trips the type check
    if "type" in c and vt != c["type"]:
        return f"{lbl}: expected type {c['type']}, got {vt or 'a non-scalar'}"
    if c.get("positive") and vt == "number" and not (v > 0):
        return f"{lbl}: expected a positive number, got a non-positive number"
    if "min" in c and vt == "number" and v < c["min"]:
        return f"{lbl}: value below the minimum {c['min']}"
    if "max" in c and vt == "number" and v > c["max"]:
        return f"{lbl}: value above the maximum {c['max']}"
    if "pattern" in c and isinstance(v, str) and re.fullmatch(c["pattern"], v) is None:
        return f"{lbl}: value did not match the learned format {c['pattern']!r}"
    return None


# --- H9 layer 2: deterministic magnitude defense (pure, 0-LLM, scalar numbers) ----------------
def _median(xs: list) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def magnitude_fields(eff: dict, data: Any) -> dict:
    """The in-scope SCALAR-NUMBER fields for the magnitude check: `{path: float}`. Scope = a root scalar (path
    "") or a root-dict key (path "<key>", not a list-item path) whose EFFECTIVE contract's `type` is "number"
    AND whose runtime value is a number (bool excluded — it's an int subclass), with neither `enabled` nor
    `delta_enabled` disabled. List-item paths ("[]" / "[].<key>") are out of scope this slice."""
    out: dict = {}
    for path, c in eff.items():
        if path.startswith("["):
            continue
        if c.get("type") != "number":
            continue
        if c.get("enabled", True) is False or c.get("delta_enabled") is False:
            continue
        if path == "":
            v = data
        elif isinstance(data, dict) and path in data:
            v = data[path]
        else:
            continue
        if contract_type(v) == "number":
            out[path] = float(v)
    return out


def check_magnitude(c: dict, value: float, ring: list) -> Optional[str]:
    """Warm-up-AGNOSTIC magnitude band check (pure, 0-LLM). Returns a VALUE-FREE reason if `value` deviates too
    far from the ring's rolling baseline, else None. The CALLER decides advisory-vs-enforce from `len(ring)`.

    Band: `tol = max(delta_k·1.4826·MAD, max_delta_frac·|median|, ε)`; a violation is `|value - median| > tol`.
    The MAD term self-calibrates the band WIDE for a genuinely volatile field (so it never habituates); the
    fractional-floor term catches a wrong-but-same-sign move on a near-constant field (the 129→40 gap) and stops
    a zero-variance field tripping on legit jitter. A zero-centered field (|median|≈0 and MAD≈0) has no
    magnitude scale — skipped (layer-1 sign/null already guards it)."""
    n = len(ring)
    if n == 0:
        return None
    med = _median(ring)
    mad = _median([abs(h - med) for h in ring])
    if abs(med) <= DELTA_ABS_EPS and mad <= DELTA_ABS_EPS:
        return None
    k = DELTA_K if c.get("delta_k") is None else c["delta_k"]
    frac = DELTA_FLOOR_FRAC if c.get("max_delta_frac") is None else c["max_delta_frac"]
    tol = max(k * _MAD_SIGMA * mad, frac * abs(med), DELTA_ABS_EPS)
    if abs(value - med) > tol:
        # VALUE-FREE (persists to the sidecar / meta.json): only ratios and n — never the raw datum or the
        # median. When |median| is non-zero, a delta-% + tolerance-% reads best; when the median is exactly 0
        # (a legitimate sign-oscillating field, e.g. a net-change/P&L with median 0 but non-zero spread — the
        # zero-centered skip above only fires when the SPREAD is also ~0), express the excursion as a multiple
        # of the tolerance band instead (`tol` is always >= DELTA_ABS_EPS, so this never divides by zero).
        if abs(med) > DELTA_ABS_EPS:
            return (f"magnitude: value deviates {100 * abs(value - med) / abs(med):.0f}% from the rolling "
                    f"baseline (tolerance +/-{100 * tol / abs(med):.0f}%, n={n})")
        return (f"magnitude: value is {abs(value - med) / tol:.1f}x the allowed deviation from the rolling "
                f"baseline (n={n})")
    return None


def accrue_ring(ring: list, value: float, *, ring_size: int = DELTA_RING) -> list:
    """Append one clean numeric observation to a rolling ring, keeping only the newest `ring_size` (numbers)."""
    return (list(ring) + [float(value)])[-ring_size:]
