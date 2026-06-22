"""Cross-process safety of the flow-meta read-modify-write.

Every writer of a flow's `<key>.meta.json` (run records AND operator edits — approve / learn / relearn)
goes through `_update_meta`, which holds an exclusive cross-process `_meta_lock` for the load→mutate→save.
So two scheduled processes recording runs — OR a scheduled run racing an operator edit — can't lose a
health/trust update (last-writer-wins). These tests spawn real subprocesses (not multiprocessing — avoids
the spawn + pytest import/pickling gotcha) and assert no update is dropped under genuine contention.
"""

from __future__ import annotations

import subprocess
import sys

from ultracua.cache import FlowCache
from ultracua.flows import _load_meta, _record_run


def _run(src: str):
    return subprocess.Popen([sys.executable, "-c", src])


def _record_src(root: str, key: str, n: int) -> str:
    return (
        "from ultracua.cache import FlowCache;"
        "from ultracua.flows import _record_run;"
        f"c=FlowCache(root={root!r});"
        f"[_record_run(c,{key!r},ok=True) for _ in range({n})]"
    )


def _edit_src(root: str, key: str, n: int) -> str:
    # An "operator" repeatedly toggling a trust field — a writer of the SAME meta file as the run records.
    return (
        "from ultracua.cache import FlowCache;"
        "from ultracua.flows import _update_meta;"
        f"c=FlowCache(root={root!r});"
        f"[_update_meta(c,{key!r},lambda m: setattr(m,'approved', not m.approved)) for _ in range({n})]"
    )


def test_record_run_no_lost_updates_under_heavy_contention(tmp_path) -> None:
    # 6-way contention is past the regime where the old blocking msvcrt LK_LOCK gave up (~10s) and ran
    # unlocked; the non-blocking retry loop must keep it exact.
    root, key = str(tmp_path), "flowkey"
    n_procs, per = 6, 100
    procs = [_run(_record_src(root, key, per)) for _ in range(n_procs)]
    for p in procs:
        assert p.wait(timeout=120) == 0
    meta = _load_meta(FlowCache(root=root), key)
    assert meta.runs == n_procs * per          # no read-modify-write was clobbered
    assert meta.successes == n_procs * per


def test_run_record_not_clobbered_by_concurrent_trust_edit(tmp_path) -> None:
    # A scheduled run record concurrent with an operator approve/unapprove writes the SAME meta file.
    # Both must serialise, so the run count survives the trust edits (the #2 race the lock closes).
    root, key, n = str(tmp_path), "flowkey", 300
    recorder = _run(_record_src(root, key, n))
    editor = _run(_edit_src(root, key, n))
    assert recorder.wait(timeout=120) == 0
    assert editor.wait(timeout=120) == 0
    meta = _load_meta(FlowCache(root=root), key)
    assert meta.runs == n  # not a single run lost to a concurrent trust edit clobbering the file


def test_record_run_updates_health_fields(tmp_path) -> None:
    cache = FlowCache(root=str(tmp_path))
    _record_run(cache, "k", ok=True)
    _record_run(cache, "k", ok=False, error="drift")
    _record_run(cache, "k", ok=False, error="drift again")
    meta = _load_meta(cache, "k")
    assert meta.runs == 3 and meta.successes == 1
    assert meta.consecutive_failures == 2 and meta.last_error == "drift again"
    # a success resets the failure streak
    _record_run(cache, "k", ok=True)
    meta = _load_meta(cache, "k")
    assert meta.consecutive_failures == 0 and meta.last_error is None and meta.successes == 2
