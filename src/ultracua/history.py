"""H9 layer 2 — a bounded, aggregate-only rolling numeric HISTORY per flow, backing the magnitude check.

A tiny per-flow sidecar at `<cache.root>/history/<flow_key>.magnitude.json` holding, per magnitude-checked
scalar-number field, the last N CLEAN observations (numbers only) PLUS a per-field learn-time ANCHOR. The
`contracts.check_magnitude` band is computed from the ring; a clean successful replay appends to it. It is
deliberately NOT part of `FlowMeta` (no SCHEMA_VERSION bump; an absent file just means "no baseline yet →
every value passes").

THE ANCHOR (`"anchors"`) is the FIXED POINT the rolling band structurally lacks: the ring's median tracks a
slow creep (the baseline *is* the creep), so a value drifting a little each run stays inside the band forever.
The anchor is the FIRST clean observation of a field after learn and is never overwritten — only cleared by a
re-learn or an explicit `release(rebaseline=True)`. Comparing today's ring against it is how slow drift becomes
visible AT ALL, in pure Python with zero LLM calls.

NO-RAW-STRING GUARANTEE: only JSON numbers are ever written (the caller filters to `contract_type == "number"`
and coerces via `float`), and `load_history` re-filters every ring element AND every anchor to numbers-only on
read — so a corrupt or tampered file can never inject a string/PII value at rest, and a torn ring biases toward
FEWER samples (= more warm-up / advisory, never a false quarantine).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def history_path(cache, key: str) -> Path:
    return Path(cache.root) / "history" / f"{key}.magnitude.json"


def _num(x):
    """The value if it is a real (non-bool) number, else None — the single numbers-only filter."""
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def load_history(cache, key: str) -> dict:
    """Tolerant read → `{"v": 1, "fields": {path: [num, ...]}, "anchors": {path: num}}`. A missing / torn /
    corrupt / non-dict file, or any non-numeric element, is dropped — never raises, never yields a non-number
    (biases toward fewer samples / no anchor, i.e. toward advisory, never toward a false quarantine)."""
    doc = {"v": 1, "fields": {}, "anchors": {}}
    try:
        raw = json.loads(history_path(cache, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return doc
    if not isinstance(raw, dict) or not isinstance(raw.get("fields"), dict):
        return doc
    clean: dict = {}
    for path, ring in raw["fields"].items():
        if isinstance(ring, list):
            nums = [x for x in ring if _num(x) is not None]
            if nums:
                clean[str(path)] = nums
    doc["fields"] = clean
    anchors: dict = {}
    for path, val in (raw.get("anchors") or {}).items() if isinstance(raw.get("anchors"), dict) else ():
        if _num(val) is not None:
            anchors[str(path)] = float(val)
    doc["anchors"] = anchors
    return doc


def set_anchor(doc: dict, path: str, value) -> None:
    """Record a field's learn-time ANCHOR — the FIRST clean observation — and never overwrite it. Idempotent:
    once set it survives every later run, so it stays a fixed reference the rolling median can drift away from
    (that delta is the only 0-LLM signal of slow drift). Cleared only by `_reset_history` (re-learn / an
    explicit `release(rebaseline=True)`). Non-numeric values are ignored."""
    if _num(value) is None:
        return
    anchors = doc.setdefault("anchors", {})
    if path not in anchors:
        anchors[path] = float(value)


def save_history(cache, key: str, doc: dict) -> None:
    """Atomically persist the history doc (temp + os.replace, mirroring the meta sidecar's atomic save)."""
    p = history_path(cache, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    os.replace(tmp, p)
