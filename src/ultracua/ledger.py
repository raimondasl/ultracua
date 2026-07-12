"""Durable per-row resume ledger for `run_batch` (H3 slice 2c).

A batch that dies at row 300 of 500 must, on a re-run, SKIP the rows that already COMMITTED rather than
re-firing their writes (a mass double-write). This ledger is that durable record: an append-only log,
one line per landed row, keyed by the row's `Idempotency-Key`(s).

The `Idempotency-Key` is the CORRECTNESS FLOOR — a re-fired write carries the same key, so a backend that
honors it dedupes the duplicate. This ledger is a pure OPTIMIZATION above that floor: it lets a re-run of
the SAME job SKIP the landed rows (no wasted re-fire, no visible double-attempt). Every crash window biases
toward a MISSING entry, whose only cost is a re-fire the key dedupes — never a false skip of an un-landed
write (records are written STRICTLY AFTER the write's confirm), and never a silent double-write.

The RECURRING-vs-RETRY ambiguity (a run-invariant key can't tell tomorrow's legitimate recurrence from
today's retry) is resolved by a caller-supplied `job_id` token that scopes the ledger file: the SAME token
resumes (skips landed rows); a fresh/absent token is an independent run. The token is the operator's
statement of intent — the key answers "same row?", the token answers "same job attempt?".

Per-write resume WITHIN one multi-write flow stays deliberately deferred (see `cache.StepConfirm`): the
ledger checkpoints at whole-flow-confirmed granularity only, so a multi-write row that died mid-flow is not
recorded and re-fires all its writes on resume (each write's stable per-step key dedupes at the backend).
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional


class LedgerError(Exception):
    """A ledger is unusable (bad job-id, or a file whose header doesn't match this flow). `run_batch`
    wraps this into `FlowReplayError` so a caller sees the usual typed refusal."""


# A job-id must be a safe, human-readable filename component — no path separators, no traversal. We REFUSE
# a bad id rather than mangle it (a silently-mangled id could alias two distinct jobs onto one ledger).
_SAFE_JOB = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")

_LEDGER_VERSION = 1


class RunLedger:
    """An append-only, crash-safe record of which ROWS of a `run_batch` have COMMITTED, keyed by each row's
    `Idempotency-Key` tuple, so a re-run under the same `job_id` skips landed rows.

    File: `<cache.root>/ledgers/<flow_key>.<job_id>.jsonl` — one header line then one commit line per landed
    row. Reads tolerate a torn/partial last line (a crash mid-append). Records are `flush()` + `os.fsync()`ed
    before returning, so a recorded row is durable before the next row fires."""

    def __init__(self, path: Path, flow_key: str, job_id: str, scope: str) -> None:
        self.path = path
        self.flow_key = flow_key
        self.job_id = job_id
        self.scope = scope
        self._committed: Optional[set] = None  # lazily loaded set[tuple[str, ...]]
        self._fh = None  # append handle, opened lazily on the first record()

    @classmethod
    def open(cls, cache, flow_key: str, job_id: str, scope: str) -> "RunLedger":
        """Build a ledger handle for `job_id` under `cache`'s root. Validates the id; touches no disk yet
        (the file is created lazily on the first `record`, and `committed()` tolerates its absence)."""
        if not isinstance(job_id, str) or not _SAFE_JOB.match(job_id):
            raise LedgerError(
                f"invalid resume job-id {job_id!r} — must match [A-Za-z0-9._-]{{1,64}} (no path separators). "
                f"Use a stable, human-readable token per batch job.")
        path = Path(cache.root) / "ledgers" / f"{flow_key}.{job_id}.jsonl"
        return cls(path, flow_key, job_id, scope)

    def committed(self) -> set:
        """The set of committed row-key tuples (empty if the file is absent). Skips blank/torn/partial lines
        (never fatal — a torn line just reads as not-committed, so its row safely re-fires). A parseable
        HEADER whose flow_key/scope doesn't match this flow raises `LedgerError` (a misplaced/foreign file
        must not silently authorize skips)."""
        if self._committed is not None:
            return self._committed
        done: set = set()
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    continue  # torn / partial (a crash mid-append) -> ignore, its row re-fires
                if not isinstance(rec, dict):
                    continue
                if rec.get("kind") == "header":
                    if rec.get("flow_key") != self.flow_key or rec.get("scope") != self.scope:
                        raise LedgerError(
                            f"ledger {self.path.name} belongs to a different flow "
                            f"(header flow_key/scope != {self.flow_key}/{self.scope}) — refusing to resume "
                            f"against a foreign ledger.")
                elif rec.get("kind") == "commit":
                    keys = rec.get("keys")
                    if isinstance(keys, list) and keys:
                        done.add(tuple(keys))
        self._committed = done
        return done

    def is_committed(self, keys: list) -> bool:
        """Has the row with these Idempotency-Key(s) already landed? A row with no keys (a READ) is never
        committed and never skipped."""
        if not keys:
            return False
        return tuple(keys) in self.committed()

    def record(self, index: int, keys: list, status: str) -> None:
        """Durably append one committed row (called STRICTLY AFTER its write confirms). Opens the file lazily
        (writing the header once, on a brand-new file) and `flush()`+`os.fsync()`es before returning, so the
        row is on disk before the next row fires. No-op for a keyless (READ) row."""
        if not keys:
            return
        first = self._fh is None
        if first:
            new_file = not self.path.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")
            if new_file:
                self._append({"v": _LEDGER_VERSION, "kind": "header", "flow_key": self.flow_key,
                              "job_id": self.job_id, "scope": self.scope, "created_ts": time.time()})
        self._append({"kind": "commit", "index": index, "keys": list(keys), "status": status,
                      "ts": time.time()})
        if self._committed is not None:
            self._committed.add(tuple(keys))

    def _append(self, rec: dict) -> None:
        self._fh.write(json.dumps(rec) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None

    @staticmethod
    def mint_job_id() -> str:
        """A fresh, sortable, unique job-id (UTC timestamp + short random suffix) — used by the CLI so the
        FIRST run of a `--commit` write batch is already resumable, for the unplanned-crash case."""
        return time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
