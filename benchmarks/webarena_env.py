"""WebArena-Verified offline adapter: drive the public deterministic evaluator from ultracua.

WebArena-Verified (ServiceNow) scores a browser agent's run *deterministically* — it reads a
per-task ``agent_response.json`` plus a captured ``network.har`` and compares against bundled
gold answers (no LLM judge, no live-site needed for the *scoring* step). This adapter lets
ultracua produce those run dirs and read back scores, mirroring the MiniWoB++ adapter pattern.

Two things make WebArena-Verified awkward to embed, both handled here:

1. **Dependency isolation.** ``webarena-verified`` hard-pins ``pydantic==2.12.0``, which
   conflicts with ultracua's ``pydantic>=2.13.4``. So we NEVER import it — we shell out to the
   pinned CLI in its own ephemeral environment via ``uv tool run --from webarena-verified==…``.
   The only coupling is argv strings, three filenames, and a handful of JSON keys.

2. **Offline vs live.** The *evaluator* runs fully offline (native Windows, no Docker). RETRIEVE
   tasks are scored from ``agent_response.json`` alone — but the evaluator still *parses* the HAR
   up front, so a **valid** ``network.har`` with **>=1 entry** must exist or the task ERRORs
   (an empty-``entries`` HAR is rejected). NAVIGATE / most MUTATE tasks additionally assert
   against real HTTP requests, so they need a genuine HAR captured against a live container.

Data (the isolated tool's package cache, scratch eval dirs) lives under ``settings.data_dir``
(default ``D:\\ultracua-data``, configurable via ``ULTRACUA_DATA_DIR``) — off the system drive.

See PLAN.md (WebArena-Verified realism layer). Verified against webarena-verified 1.2.3.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ultracua import __version__
from ultracua.config import settings

# --- contract constants (stable per the evaluator's WebArenaVerifiedConfig defaults) ----------
WEBARENA_PKG = os.getenv("ULTRACUA_WEBARENA_PKG", "webarena-verified==1.2.3")
AGENT_RESPONSE_FILE = "agent_response.json"
TRACE_FILE = "network.har"
EVAL_RESULT_FILE = "eval_result.json"
BATCH_RESULT_FILE = "eval_results.json"  # written only on a full run (no --task-ids)

# agent_response.json enums (UPPERCASE on input; the evaluator normalizes case).
TASK_TYPES = frozenset({"RETRIEVE", "MUTATE", "NAVIGATE"})
STATUSES = frozenset(
    {
        "SUCCESS",
        "ACTION_NOT_ALLOWED_ERROR",
        "PERMISSION_DENIED_ERROR",
        "NOT_FOUND_ERROR",
        "DATA_VALIDATION_ERROR",
        "UNKNOWN_ERROR",
    }
)


class WebArenaCliError(RuntimeError):
    """The isolated webarena-verified CLI exited non-zero."""


# --- isolated CLI plumbing --------------------------------------------------------------------
def _uv_bin() -> str:
    return os.getenv("ULTRACUA_UV_BIN") or shutil.which("uv") or "uv"


def _tool_env() -> dict[str, str]:
    env = dict(os.environ)
    data = Path(settings.data_dir)
    # Keep the evaluator's (heavy) package cache AND any uv-managed CPython off the system drive.
    env.setdefault("UV_CACHE_DIR", str(data / "uv-cache"))
    env.setdefault("UV_PYTHON_INSTALL_DIR", str(data / "uv-python"))
    # The evaluator's rich console logging raises UnicodeEncodeError on the Windows cp1252
    # console (box-drawing chars). Force UTF-8 so that cosmetic stderr noise can't surface as
    # a failure — we judge success by the parsed eval_result.json, never by stderr.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _scratch_root() -> Path:
    """A scratch dir under data_dir (keeps even ephemeral evaluator I/O off the system drive)."""
    p = Path(settings.data_dir) / "tmp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _reset_leaf(parent, name: str) -> Path:
    """rmtree + recreate ``parent/name``. Only ever removes the named *leaf* we construct —
    never the caller-provided ``parent`` — so a stray/parent ``scratch`` can't be nuked.
    """
    leaf = Path(parent) / name
    if leaf.exists():
        shutil.rmtree(leaf)
    leaf.mkdir(parents=True, exist_ok=True)
    return leaf


def _run_cli(args: list[str], *, check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run ``webarena-verified <args>`` in its own ephemeral uv tool environment."""
    cmd = [_uv_bin(), "tool", "run", "--from", WEBARENA_PKG, "webarena-verified", *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_tool_env(),
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise WebArenaCliError(
            f"`webarena-verified {' '.join(args)}` failed (exit {proc.returncode}).\n"
            f"stderr tail:\n{(proc.stderr or '')[-2000:]}"
        )
    return proc


def cli_available(timeout: float = 240.0) -> bool:
    """True if the isolated evaluator CLI can be invoked (uv present + package resolvable).

    The first call may download ~49 packages; subsequent calls hit the cache. Tests use this
    to skip the live integration path when uv/network/the package aren't available.
    """
    bin_ = _uv_bin()
    # Fast-skip when uv can't be found at all: not on PATH AND not an existing absolute path.
    if shutil.which(bin_) is None and not (os.path.isabs(bin_) and os.path.exists(bin_)):
        return False
    try:
        return _run_cli(["--version"], check=False, timeout=timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _output_arg(args: list[str], task_ids, sites, task_type, template_id) -> None:
    if task_ids:
        args += ["--task-ids", ",".join(str(int(t)) for t in task_ids)]
    if sites:
        args += ["--sites", ",".join(sites)]
    if task_type:
        args += ["--task-type", task_type]
    if template_id is not None:
        args += ["--template-id", str(int(template_id))]


# --- dataset / agent-input (inputs) -----------------------------------------------------------
def dataset_get(
    task_ids=None, *, sites=None, task_type=None, template_id=None, fields=None
) -> list[dict]:
    """Dataset records (incl. the ``eval`` spec with gold ``expected`` answers)."""
    with tempfile.TemporaryDirectory(dir=_scratch_root()) as td:
        out = Path(td) / "dataset.json"
        args = ["dataset-get", "--output", str(out)]
        _output_arg(args, task_ids, sites, task_type, template_id)
        if fields:
            args += ["--fields", ",".join(fields)]
        _run_cli(args)
        return json.loads(out.read_text(encoding="utf-8"))


def get_agent_input(
    task_ids=None, *, config_path=None, sites=None, task_type=None, template_id=None
) -> list[dict]:
    """Agent-facing task inputs: ``{task_id, intent_template_id, sites, start_urls, intent}``.

    Without ``config_path`` the ``start_urls`` are placeholder templates (e.g. ``__SHOPPING__``);
    with a config that maps the site to real URLs they render to those URLs.
    """
    with tempfile.TemporaryDirectory(dir=_scratch_root()) as td:
        out = Path(td) / "agent_input.json"
        args = ["agent-input-get", "--output", str(out)]
        _output_arg(args, task_ids, sites, task_type, template_id)
        if config_path:
            args += ["--config", str(config_path)]
        _run_cli(args)
        return json.loads(out.read_text(encoding="utf-8"))


def expected_answer(task_id: int) -> dict:
    """The gold ``expected`` agent-response for a task (from its ``eval[0]``).

    Useful for offline producer round-trips: write this as the agent_response to get score 1.0.
    """
    recs = dataset_get([task_id])
    if not recs:
        raise ValueError(f"task {task_id} not found in dataset")
    # Don't assume ordering: find the evaluator entry that carries a gold `expected` response.
    for ev in recs[0].get("eval") or []:
        if isinstance(ev, dict) and "expected" in ev:
            return ev["expected"]
    raise ValueError(f"task {task_id} has no expected agent-response (not response-scorable)")


# --- producer side (ultracua writes the run dir) ----------------------------------------------
def task_dir(output_root, task_id: int) -> Path:
    """``<output_root>/<task_id>`` — the bare integer name the evaluator discovers."""
    return Path(output_root) / str(int(task_id))


def write_agent_response(
    output_root,
    task_id: int,
    *,
    task_type: str,
    status: str,
    retrieved_data=None,
    error_details=None,
) -> Path:
    """Write the per-task ``agent_response.json`` (the 4-key contract, UTF-8, no BOM)."""
    if task_type not in TASK_TYPES:
        raise ValueError(f"task_type must be one of {sorted(TASK_TYPES)}, got {task_type!r}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}, got {status!r}")
    d = task_dir(output_root, task_id)
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_type": task_type,
        "status": status,
        "retrieved_data": retrieved_data,
        "error_details": error_details,
    }
    p = d / AGENT_RESPONSE_FILE
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def har_path(output_root, task_id: int) -> Path:
    """Where a live run records its HAR (pass to ``BrowserSession(record_har_path=...)``)."""
    return task_dir(output_root, task_id) / TRACE_FILE


# A minimal but VALID HAR. The evaluator parses the HAR up front and rejects empty ``entries``,
# so this carries exactly one synthetic request/response. Content is unused for RETRIEVE scoring
# (it only satisfies the discovery/parse gate). Live runs overwrite this with a real capture.
_PLACEHOLDER_HAR = {
    "log": {
        "version": "1.2",
        "creator": {"name": "ultracua", "version": __version__},
        "entries": [
            {
                "startedDateTime": "1970-01-01T00:00:00.000Z",
                "time": 0,
                "request": {
                    "method": "GET",
                    "url": "http://placeholder.invalid/",
                    "httpVersion": "HTTP/1.1",
                    "headers": [],
                    "queryString": [],
                    "cookies": [],
                    "headersSize": -1,
                    "bodySize": -1,
                },
                "response": {
                    "status": 200,
                    "statusText": "OK",
                    "httpVersion": "HTTP/1.1",
                    "headers": [],
                    "cookies": [],
                    "content": {"size": 0, "mimeType": "text/plain", "text": ""},
                    "redirectURL": "",
                    "headersSize": -1,
                    "bodySize": -1,
                },
                "cache": {},
                "timings": {"send": 0, "wait": 0, "receive": 0},
            }
        ],
    }
}


def write_placeholder_har(output_root, task_id: int) -> Path:
    """Write a minimal valid HAR so a response-only RETRIEVE task clears the parse gate.

    Only valid for RETRIEVE scoring — NAVIGATE / network-asserted MUTATE tasks need a real HAR.
    """
    d = task_dir(output_root, task_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / TRACE_FILE
    p.write_text(json.dumps(_PLACEHOLDER_HAR, indent=2), encoding="utf-8")
    return p


# --- eval side (read scores back) -------------------------------------------------------------
def run_eval(output_root, task_ids=None, *, config_path=None) -> None:
    """Score the run dirs under ``output_root`` (writes ``eval_result.json`` per task).

    Note: the CLI returns exit 0 even when individual tasks ERROR — judge each task by its
    parsed ``score``/``status`` via :func:`get_score`, not by the process exit code.
    """
    args = ["eval-tasks", "--output-dir", str(output_root)]
    if task_ids:
        args += ["--task-ids", ",".join(str(int(t)) for t in task_ids)]
    if config_path:
        args += ["--config", str(config_path)]
    _run_cli(args)


def read_eval_result(output_root, task_id: int) -> dict:
    p = task_dir(output_root, task_id) / EVAL_RESULT_FILE
    if not p.exists():
        # The evaluator writes no result when it skips a task (e.g. a missing/invalid
        # agent_response.json or network.har). Surface that clearly rather than a bare ENOENT.
        raise FileNotFoundError(
            f"no {EVAL_RESULT_FILE} for task {task_id} at {p} — the evaluator skipped it "
            f"(check that {AGENT_RESPONSE_FILE} and a valid {TRACE_FILE} exist, and that run_eval ran)"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def get_score(output_root, task_id: int) -> float:
    """Top-level binary score for a task: 1.0 (pass) or 0.0 (fail/error)."""
    return float(read_eval_result(output_root, task_id).get("score", 0.0))


def get_failure_reasons(output_root, task_id: int) -> list[str]:
    """Flatten assertion messages (+ any task error_msg) for diagnostics on a 0.0."""
    res = read_eval_result(output_root, task_id)
    msgs: list[str] = []
    for er in res.get("evaluators_results") or []:
        for a in er.get("assertions") or []:
            msgs.extend(a.get("assertion_msgs") or [])
    if res.get("error_msg"):
        msgs.append(res["error_msg"])
    return msgs


def read_batch_summary(output_root) -> dict | None:
    """The aggregate ``eval_results.json`` (full runs only; None on ``--task-ids`` runs)."""
    p = Path(output_root) / BATCH_RESULT_FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# --- offline validation entrypoints -----------------------------------------------------------
def selfcheck_roundtrip(task_id: int = 108, scratch=None) -> dict:
    """End-to-end offline producer->eval round-trip with NO browser, NO key, NO containers.

    Writes the gold answer (+ a placeholder HAR) for a RETRIEVE task and scores it (expect 1.0),
    then an empty answer (expect 0.0). Proves the full wiring deterministically and portably.
    """
    expected = expected_answer(task_id)  # raises if the task isn't response-scorable (RETRIEVE)
    base = Path(scratch) if scratch else Path(settings.data_dir) / "webarena" / "selfcheck"
    good = _reset_leaf(base, "good")
    bad = _reset_leaf(base, "bad")

    write_agent_response(
        good,
        task_id,
        task_type=expected.get("task_type", "RETRIEVE"),
        status=expected.get("status", "SUCCESS"),
        retrieved_data=expected.get("retrieved_data"),
        error_details=expected.get("error_details"),
    )
    write_placeholder_har(good, task_id)
    run_eval(good, [task_id])

    write_agent_response(bad, task_id, task_type="RETRIEVE", status="SUCCESS", retrieved_data=[])
    write_placeholder_har(bad, task_id)
    run_eval(bad, [task_id])

    return {"good": get_score(good, task_id), "bad": get_score(bad, task_id)}


def validate_demo(demo_src, scratch=None, task_ids=(107, 108)) -> dict[int, float]:
    """Re-score the bundled demo logs (107->0.0, 108->1.0) in a scratch copy.

    ``demo_src`` is the cloned repo's ``examples/agent_logs/demo`` dir (it ships the
    agent_response.json + network.har; eval-tasks would otherwise write into the source tree).
    """
    demo_src = Path(demo_src)
    base = Path(scratch) if scratch else Path(settings.data_dir) / "webarena" / "demo_eval"
    base.mkdir(parents=True, exist_ok=True)
    for tid in task_ids:
        dest = base / str(tid)
        if dest.exists():  # clear any prior copy so a stale eval_result can't linger
            shutil.rmtree(dest)
        shutil.copytree(demo_src / str(tid), dest)
    run_eval(base, list(task_ids))
    return {tid: get_score(base, tid) for tid in task_ids}
