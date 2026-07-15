"""H9 layer 2 — a bounded, aggregate-only rolling numeric HISTORY per flow, backing the magnitude check.

A tiny per-flow sidecar at `<cache.root>/history/<flow_key>.magnitude.json` holding, per magnitude-checked
scalar-number field, the last N CLEAN observations (numbers only). The `contracts.check_magnitude` band is
computed from this ring; a clean successful replay appends to it. It is deliberately NOT part of `FlowMeta`
(no SCHEMA_VERSION bump; an absent file just means "no baseline yet → every value passes").

NO-RAW-STRING GUARANTEE: only JSON numbers are ever written (the caller filters to `contract_type == "number"`
and coerces via `float`), and `load_history` re-filters every element to numbers-only on read — so a corrupt
or tampered file can never inject a string/PII value at rest, and a torn ring biases toward FEWER samples
(= more warm-up / advisory, never a false quarantine).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def history_path(cache, key: str) -> Path:
    return Path(cache.root) / "history" / f"{key}.magnitude.json"


def load_history(cache, key: str) -> dict:
    """Tolerant read → `{"v": 1, "fields": {path: [num, ...]}}`. A missing / torn / corrupt / non-dict file, or
    any non-numeric element, is dropped — never raises, never yields a non-number (biases toward fewer samples)."""
    doc = {"v": 1, "fields": {}}
    try:
        raw = json.loads(history_path(cache, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return doc
    if not isinstance(raw, dict) or not isinstance(raw.get("fields"), dict):
        return doc
    clean: dict = {}
    for path, ring in raw["fields"].items():
        if isinstance(ring, list):
            nums = [x for x in ring if isinstance(x, (int, float)) and not isinstance(x, bool)]
            if nums:
                clean[str(path)] = nums
    doc["fields"] = clean
    return doc


def save_history(cache, key: str, doc: dict) -> None:
    """Atomically persist the history doc (temp + os.replace, mirroring the meta sidecar's atomic save)."""
    p = history_path(cache, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    os.replace(tmp, p)
