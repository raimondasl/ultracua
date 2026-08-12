"""The ONE durable rename, and the ONE durable write built on it.

WHY THIS MODULE EXISTS, in one measurement. S4 found `_save_meta`'s `os.replace` failing roughly 1 run
in 6 under full-suite load — on Windows an AV scanner or indexer holds a file for a few milliseconds
after it is written, and the rename that lands it hits `PermissionError: [WinError 5]` — and gave that
call site three attempts with a backoff. Nothing was wrong with the fix. What was wrong is that there
were SEVEN renames in this package and it fixed one; the other six kept the identical bug, in files that
lose a recipe (`cache.put`), a run log (`history`), an evidence artifact (`audit`), a refusal memory and
a refreshed auth state when they lose the same race. Guard on one path, none on its siblings, is this
register's most-repeated defect shape, and transcribing the retry loop six more times would have been
the same mistake with better coverage.

So the invariant is enforced ONCE, here, and `tests/test_boundary_truth.py` makes a bare `os.replace`
outside this module fail an AST scan — a rename added tomorrow is durable by construction rather than by
whoever writes it remembering a register entry.

WHAT THIS DOES NOT DO. It does not swallow. A rename that never lands still raises, because retrying
must not become suppressing (inviolable #2) — callers that legitimately treat persistence as best-effort
already say so at their own call site, and that decision stays theirs, visible, one line from the thing
it affects.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Union

from .obs import get_logger

_log = get_logger(__name__)

_ATTEMPTS = 3
_BACKOFF = 0.05          # seconds, multiplied by the attempt number — 50ms then 100ms

_PathLike = Union[str, "os.PathLike[str]"]


def durable_rename(src: _PathLike, dest: _PathLike, *, cleanup_src: bool = False) -> None:
    """`os.replace`, retried through the transient sharing violation that measurement says is ordinary.

    `cleanup_src=True` removes the source if every attempt failed, which is what a temp-file writer
    wants: a stray `.tmp` beside a sidecar is indistinguishable from a torn write to whoever looks next.
    A preserve-aside rename (moving a corrupt file out of the way) passes False — there the source IS
    the evidence, and deleting it would destroy the only copy.
    """
    for attempt in range(_ATTEMPTS):
        try:
            os.replace(src, dest)
            if attempt:
                _log.info("durable rename of %s landed on attempt %d (transient sharing violation)",
                          dest, attempt + 1)
            return
        except OSError:
            if attempt == _ATTEMPTS - 1:
                if cleanup_src:
                    try:
                        os.unlink(src)
                    except OSError:  # noqa: BLE001 — best effort; the raise below is load-bearing
                        pass
                raise
            time.sleep(_BACKOFF * (attempt + 1))


def durable_write_text(dest: _PathLike, text: str, *, encoding: str = "utf-8") -> None:
    """Write `text` so that a reader sees either the whole old file or the whole new one, and so that a
    host crash cannot leave a zero-length or NUL-filled file behind.

    temp + `os.replace` alone gives atomicity but not durability: the rename can land while the temp
    file's BYTES are still only in the page cache. `fsync` before the rename closes that window, which
    matters most for the hot files — the meta is rewritten by every replay — because a torn sidecar is
    exactly the input `_load_meta` then has to quarantine on.

    The temp name carries the PID so two processes writing the same destination cannot collide on it.

    A DURABLE WRITE THAT FAILS LEAVES NO TEMP, WHICHEVER HALF FAILED. The rename half has always cleaned
    up; the WRITE half did not, so ENOSPC on a full disk, EIO on a network share or a quota hit left a
    stray `.tmp` beside the destination — and the caller's error message then said it had been removed.
    That is a fail-loud message with a false clause, which is the exact defect the `_META_CORRUPT` /
    `_META_UNREADABLE` split exists to have fixed once already. Enforcing it HERE rather than in the
    caller's message is the point: seven writers share this helper, and conditionalising one sentence in
    one of them would leave the other six stranding temps.
    """
    p = Path(dest)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:  # noqa: BLE001 — best effort; the re-raise is the load-bearing part
            pass
        raise
    durable_rename(tmp, p, cleanup_src=True)
