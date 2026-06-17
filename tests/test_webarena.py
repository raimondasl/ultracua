"""WebArena-Verified offline adapter tests.

Three layers:
- PURE (always run): producer writers emit the exact contract; result parsing is correct.
- BROWSER (needs Chromium, already a test dep): BrowserSession records a valid HAR.
- INTEGRATION (skipped unless the isolated `webarena-verified` CLI is reachable): the real
  offline producer->eval round-trip scores deterministically with no key and no containers.

Run: `uv run pytest tests/test_webarena.py`. The integration tests fetch the evaluator via
`uv tool run` (cached after first download) and are skipped when uv/network is unavailable.
"""

from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

from benchmarks import webarena_env as wa
from ultracua.browser import BrowserSession

FIXTURES = Path(__file__).resolve().parent.parent / "benchmarks" / "fixtures"


# --- pure: producer writers -------------------------------------------------------------------
def test_write_agent_response_schema(tmp_path: Path) -> None:
    p = wa.write_agent_response(
        tmp_path, 108, task_type="RETRIEVE", status="SUCCESS",
        retrieved_data=[{"month": "January", "count": 12}],
    )
    assert p == tmp_path / "108" / "agent_response.json"
    # UTF-8, no BOM (a BOM makes the evaluator's json.loads fail at char 0).
    assert p.read_bytes()[:1] == b"{"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert set(data) == {"task_type", "status", "retrieved_data", "error_details"}
    assert data["task_type"] == "RETRIEVE" and data["error_details"] is None


def test_write_agent_response_rejects_bad_enums(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        wa.write_agent_response(tmp_path, 1, task_type="BROWSE", status="SUCCESS")
    with pytest.raises(ValueError):
        wa.write_agent_response(tmp_path, 1, task_type="RETRIEVE", status="ok")


def test_task_dir_is_bare_integer(tmp_path: Path) -> None:
    assert wa.task_dir(tmp_path, 7).name == "7"  # not zero-padded / prefixed


def test_placeholder_har_is_valid(tmp_path: Path) -> None:
    p = wa.write_placeholder_har(tmp_path, 108)
    assert p.read_bytes()[:1] == b"{"  # UTF-8, no BOM (same parse-gate contract as the response)
    har = json.loads(p.read_text(encoding="utf-8"))
    entries = har["log"]["entries"]
    # The evaluator parses the HAR up front and REJECTS empty entries — so the placeholder
    # must carry >=1 entry with request+response.
    assert len(entries) >= 1
    assert "request" in entries[0] and "response" in entries[0]


def test_write_agent_response_null_retrieved(tmp_path: Path) -> None:
    p = wa.write_agent_response(tmp_path, 7, task_type="NAVIGATE", status="SUCCESS")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["retrieved_data"] is None and data["error_details"] is None
    assert set(data) == {"task_type", "status", "retrieved_data", "error_details"}


# --- pure: CLI argv contract (no network — a flag typo would break every live run) -------------
def test_output_arg_builds_flags() -> None:
    args: list[str] = []
    wa._output_arg(args, [1, 2, 3], ["shopping", "gitlab"], "RETRIEVE", 5)
    assert args == [
        "--task-ids", "1,2,3", "--sites", "shopping,gitlab",
        "--task-type", "RETRIEVE", "--template-id", "5",
    ]
    empty: list[str] = []
    wa._output_arg(empty, None, None, None, None)
    assert empty == []
    zero: list[str] = []  # template_id=0 must still be emitted (guarded with `is not None`)
    wa._output_arg(zero, None, None, None, 0)
    assert zero == ["--template-id", "0"]


def test_run_eval_argv(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(wa, "_run_cli", lambda a, **k: captured.setdefault("args", a))
    wa.run_eval(tmp_path, [1, 2], config_path="cfg.json")
    a = captured["args"]
    assert a[:3] == ["eval-tasks", "--output-dir", str(tmp_path)]
    assert a[a.index("--task-ids") + 1] == "1,2"
    assert a[a.index("--config") + 1] == "cfg.json"


# --- pure: result parsing ---------------------------------------------------------------------
def test_parse_eval_result_fixture(tmp_path: Path) -> None:
    d = tmp_path / "108"
    d.mkdir()
    (d / "eval_result.json").write_text(
        (FIXTURES / "webarena_eval_result_sample.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert wa.get_score(tmp_path, 108) == 1.0
    assert wa.get_failure_reasons(tmp_path, 108) == []


def test_failure_reasons_flatten(tmp_path: Path) -> None:
    d = tmp_path / "5"
    d.mkdir()
    (d / "eval_result.json").write_text(
        json.dumps(
            {
                "score": 0.0,
                "status": "failure",
                "error_msg": None,
                "evaluators_results": [
                    {
                        "evaluator_name": "AgentResponseEvaluator",
                        "assertions": [
                            {"assertion_name": "task_type_mismatch", "assertion_msgs": ["Expected retrieve, got navigate"]}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert wa.get_score(tmp_path, 5) == 0.0
    assert "Expected retrieve, got navigate" in wa.get_failure_reasons(tmp_path, 5)


def test_get_failure_reasons_includes_error_msg(tmp_path: Path) -> None:
    d = tmp_path / "9"
    d.mkdir()
    (d / "eval_result.json").write_text(
        json.dumps({"score": 0.0, "status": "error", "error_msg": "Failed to evaluate task 9: boom",
                    "evaluators_results": []}),
        encoding="utf-8",
    )
    assert wa.get_failure_reasons(tmp_path, 9) == ["Failed to evaluate task 9: boom"]


def test_get_score_missing_key_is_zero(tmp_path: Path) -> None:
    d = tmp_path / "3"
    d.mkdir()
    (d / "eval_result.json").write_text(json.dumps({"status": "error"}), encoding="utf-8")
    assert wa.get_score(tmp_path, 3) == 0.0


def test_get_score_reads_top_level_not_nested(tmp_path: Path) -> None:
    # Top-level score is the AND across evaluators — get_score must read it, not a nested 1.0.
    d = tmp_path / "4"
    d.mkdir()
    (d / "eval_result.json").write_text(
        json.dumps({"score": 0.0, "status": "failure",
                    "evaluators_results": [{"evaluator_name": "AgentResponseEvaluator", "score": 1.0}]}),
        encoding="utf-8",
    )
    assert wa.get_score(tmp_path, 4) == 0.0


def test_read_eval_result_missing_is_clear(tmp_path: Path) -> None:
    # A skipped/unscored task has no eval_result.json — surface that, don't silently return 0.0.
    with pytest.raises(FileNotFoundError):
        wa.read_eval_result(tmp_path, 123)


def test_expected_answer_finds_and_requires_expected(monkeypatch) -> None:
    # Found regardless of position in eval[].
    monkeypatch.setattr(
        wa, "dataset_get",
        lambda ids: [{"eval": [{"evaluator": "NetworkEventEvaluator"}, {"expected": {"task_type": "RETRIEVE"}}]}],
    )
    assert wa.expected_answer(1)["task_type"] == "RETRIEVE"
    # Raises when no evaluator carries a gold answer (not response-scorable).
    monkeypatch.setattr(wa, "dataset_get", lambda ids: [{"eval": [{"evaluator": "NetworkEventEvaluator"}]}])
    with pytest.raises(ValueError):
        wa.expected_answer(2)


# --- browser: real HAR capture ----------------------------------------------------------------
class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # silence
        pass


def _serve(directory: Path):
    handler = functools.partial(_QuietHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_browser_records_valid_har(tmp_path: Path) -> None:
    httpd, base = _serve(FIXTURES)
    har = tmp_path / "108" / "network.har"
    har.parent.mkdir(parents=True)
    try:
        session = await BrowserSession(headless=True, record_har_path=str(har)).start()
        await session.goto(f"{base}/index.html")
        await session.close()  # flushes the HAR
    finally:
        httpd.shutdown()
        httpd.server_close()
    data = json.loads(har.read_text(encoding="utf-8"))
    assert data["log"]["entries"], "a real navigation should record >=1 HAR entry"
    assert "request" in data["log"]["entries"][0]


# --- integration: real offline round-trip via the isolated CLI --------------------------------
# The CLI probe is deferred to a session fixture (and cached) so plain `pytest` collection — and
# the pure/browser tests — never shell out to uv or touch the network.
@functools.lru_cache(maxsize=1)
def _cli_ready() -> bool:
    return wa.cli_available()


@pytest.fixture(scope="session")
def webarena_cli():
    if not _cli_ready():
        pytest.skip("webarena-verified CLI unavailable (need uv + network for first download)")
    return True


def test_selfcheck_roundtrip(tmp_path: Path, webarena_cli) -> None:
    # gold answer scores 1.0; empty answer scores 0.0 — proves the full producer->eval pipeline.
    scores = wa.selfcheck_roundtrip(108, scratch=tmp_path)
    assert scores["good"] == 1.0
    assert scores["bad"] == 0.0
    # the passing case is clean; the failing case carries a diagnostic (not just a 0.0 score).
    assert wa.get_failure_reasons(tmp_path / "good", 108) == []
    assert wa.get_failure_reasons(tmp_path / "bad", 108)


async def test_real_har_roundtrip_scores(tmp_path: Path, webarena_cli) -> None:
    # Capture a REAL HAR (not the placeholder) + the gold answer for task 108 -> score 1.0.
    httpd, base = _serve(FIXTURES)
    try:
        session = await BrowserSession(
            headless=True, record_har_path=str(wa.har_path(tmp_path, 108))
        ).start()
        await session.goto(f"{base}/index.html")
        await session.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    expected = wa.expected_answer(108)
    wa.write_agent_response(
        tmp_path, 108,
        task_type=expected["task_type"], status=expected["status"],
        retrieved_data=expected.get("retrieved_data"), error_details=expected.get("error_details"),
    )
    wa.run_eval(tmp_path, [108])
    assert wa.get_score(tmp_path, 108) == 1.0
