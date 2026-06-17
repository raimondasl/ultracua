"""Pure tests for the live WebArena runner's testable helpers (no Docker, no API key).

The end-to-end live path needs a running container + ANTHROPIC_API_KEY and is exercised by
`benchmarks.webarena_run` directly; here we only pin the static contract bits.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks import webarena_env as wa
from benchmarks import webarena_run as wr


def test_sites_spec_integrity() -> None:
    spec = wr.SITES["shopping_admin"]
    assert spec["port"] == 7780 and spec["env_ctrl_port"] == 7781
    assert spec["placeholder"] == "__SHOPPING_ADMIN__"
    # shopping_admin auto-logins via the Magento header (name without -User; value user:pass).
    assert spec["auth_header"] == {"X-M2-Admin-Auto-Login": "admin:admin1234"}


def test_write_local_config_points_at_localhost(tmp_path: Path) -> None:
    p = wr.write_local_config("shopping_admin", tmp_path / "cfg.json")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    assert cfg["environments"]["__SHOPPING_ADMIN__"]["urls"] == ["http://localhost:7780"]


def test_extract_tool_schema_matches_response_contract() -> None:
    # The extractor's structured-output enums must match the evaluator's agent_response schema.
    props = wr._EXTRACT_TOOL["input_schema"]["properties"]
    assert set(props["task_type"]["enum"]) == set(wa.TASK_TYPES)
    assert set(props["status"]["enum"]) == set(wa.STATUSES)
