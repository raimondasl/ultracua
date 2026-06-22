"""CLI operator surface (`cli.py`) — the cron/fleet entry point that had zero coverage.

Covers the pure arg->spec builders, header parsing, the `_ago` humanizer, best-effort alert posting,
and `run-all`'s exit-code / JSON / alert-dispatch logic (the bits cron and on-call actually depend on).
No browser, no LLM, no network.
"""

from __future__ import annotations

import argparse
import json
import time
import types
import urllib.request

import pytest

from ultracua import cli


def _ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


# --- _parse_headers ---------------------------------------------------------------------------
def test_parse_headers_valid() -> None:
    assert cli._parse_headers(["A=1", "B=two=three"]) == {"A": "1", "B": "two=three"}


def test_parse_headers_empty() -> None:
    assert cli._parse_headers(None) == {}
    assert cli._parse_headers([]) == {}


def test_parse_headers_rejects_missing_eq() -> None:
    with pytest.raises(SystemExit):
        cli._parse_headers(["nope"])


# --- _ago -------------------------------------------------------------------------------------
def test_ago_never() -> None:
    assert cli._ago(0) == "never"


def test_ago_units() -> None:
    now = time.time()
    assert cli._ago(now - 5).endswith("s ago")
    assert cli._ago(now - 120) == "2m ago"
    assert cli._ago(now - 7200) == "2h ago"
    assert cli._ago(now - 2 * 86400) == "2d ago"


# --- arg -> spec builders ---------------------------------------------------------------------
def test_login_from_args_maps_fields() -> None:
    spec = cli._login_from_args(_ns(
        login_url="https://x/login", username_env="U", password_env="P",
        username_selector="#u", password_selector="#p", submit_selector="#s",
        success_selector="#ok", success_url_contains="/home", timeout_ms=1234,
    ))
    assert spec.url == "https://x/login"
    assert spec.username_env == "U" and spec.password_env == "P"
    assert spec.submit_selector == "#s" and spec.success_url_contains == "/home"
    assert spec.timeout_ms == 1234


def test_mutate_from_args_maps_fields() -> None:
    spec = cli._mutate_from_args(_ns(
        confirm_selector="#done", confirm_text_contains="Saved", confirm_url_contains="/ok",
        mutate_timeout_ms=999, precheck_url="https://x/p", precheck_selector="#already",
        precheck_text_contains="exists", precheck_url_contains="/exists",
    ))
    assert spec.confirm_selector == "#done" and spec.confirm_text_contains == "Saved"
    assert spec.confirm_url_contains == "/ok" and spec.timeout_ms == 999  # mutate_timeout_ms -> timeout_ms
    assert spec.precheck_url == "https://x/p" and spec.precheck_selector == "#already"


def test_has_confirm_args() -> None:
    assert cli._has_confirm_args(_ns(confirm_selector="#x", confirm_text_contains=None,
                                     confirm_url_contains=None))
    assert cli._has_confirm_args(_ns(confirm_selector=None, confirm_text_contains=None,
                                     confirm_url_contains="/ok"))
    assert not cli._has_confirm_args(_ns(confirm_selector=None, confirm_text_contains=None,
                                         confirm_url_contains=None))


# --- _post_alert (best-effort webhook) --------------------------------------------------------
class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_post_alert_posts_payload(monkeypatch) -> None:
    captured: dict = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    failed = [types.SimpleNamespace(name="f1", error="drift"),
              types.SimpleNamespace(name="f2", error="boom")]
    cli._post_alert("https://hook.example", failed)
    assert captured["url"] == "https://hook.example" and captured["method"] == "POST"
    assert captured["body"]["failed"] == [{"name": "f1", "error": "drift"},
                                          {"name": "f2", "error": "boom"}]
    assert "2 flow(s) failed" in captured["body"]["text"]


def test_post_alert_swallows_errors(monkeypatch, capsys) -> None:
    def boom(req, timeout=0):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    cli._post_alert("https://hook.example", [types.SimpleNamespace(name="f", error="e")])  # no raise
    assert "alert webhook failed" in capsys.readouterr().out


# --- _flow_run_all (exit codes + JSON record + alert dispatch) ---------------------------------
def _run_all_args(**kw) -> argparse.Namespace:
    base = dict(include_unapproved=False, include_writes=False, concurrency=4, on_drift="raise",
                provider="anthropic", json=None, alert_webhook=None)
    base.update(kw)
    return _ns(**base)


def _result(name, status, **kw):
    return types.SimpleNamespace(name=name, status=status, ms=kw.get("ms", 10.0),
                                 data=kw.get("data"), error=kw.get("error"))


def test_run_all_exit_zero_when_all_ok(monkeypatch) -> None:
    async def fake_run_all(**kw):
        return [_result("a", "ok", data={"x": 1}), _result("b", "skipped")]

    monkeypatch.setattr("ultracua.flows.run_all", fake_run_all)
    with pytest.raises(SystemExit) as ei:
        cli._flow_run_all(_run_all_args())
    assert ei.value.code == 0


def test_run_all_exit_one_writes_json_and_alerts(monkeypatch, tmp_path) -> None:
    async def fake_run_all(**kw):
        return [_result("a", "ok", data={"x": 1}), _result("b", "failed", error="drift")]

    monkeypatch.setattr("ultracua.flows.run_all", fake_run_all)
    alerts: dict = {}
    monkeypatch.setattr(cli, "_post_alert", lambda url, failed: alerts.update(url=url, n=len(failed)))
    out = tmp_path / "run.json"
    with pytest.raises(SystemExit) as ei:
        cli._flow_run_all(_run_all_args(json=str(out), alert_webhook="https://hook.example"))
    assert ei.value.code == 1
    assert alerts == {"url": "https://hook.example", "n": 1}  # only the failure is alerted
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["ok"] == 1 and rec["failed"] == 1 and rec["skipped"] == 0 and rec["total"] == 2
    assert {f["name"]: f["status"] for f in rec["flows"]} == {"a": "ok", "b": "failed"}


def test_run_all_no_alert_when_no_webhook(monkeypatch) -> None:
    async def fake_run_all(**kw):
        return [_result("b", "failed", error="drift")]

    monkeypatch.setattr("ultracua.flows.run_all", fake_run_all)
    called = {"n": 0}
    monkeypatch.setattr(cli, "_post_alert", lambda *a: called.__setitem__("n", called["n"] + 1))
    with pytest.raises(SystemExit) as ei:
        cli._flow_run_all(_run_all_args())  # alert_webhook=None
    assert ei.value.code == 1 and called["n"] == 0
