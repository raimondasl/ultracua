"""The LLM agent's action parser: tool/args belong only to webmcp_call.

Regression test for a leak found while running live WebArena tasks — the model occasionally
emitted raw tool-call markup into the `tool` field on `type`/`click` actions, which then got
cached into the flow step. `_parse` must drop tool/args unless the action is webmcp_call.
"""

from __future__ import annotations

from ultracua.llm.types import LLMResponse, ToolUseBlock
from ultracua.providers.llm_agent import _parse


def _resp(inp: dict) -> LLMResponse:
    return LLMResponse(blocks=[ToolUseBlock(id="t1", name="act", input=inp)])


def test_parse_drops_leaked_tool_on_type_action() -> None:
    a = _parse(_resp({
        "action": "type", "intent": "enter username", "ref": "r1", "text": "admin",
        "tool": "</antmlparameter>\n<parameter name=\"args\">", "args": "{}",
    }))
    assert a is not None and a.action == "type"
    assert a.text == "admin"
    assert a.tool is None and a.args is None  # leaked markup dropped


def test_parse_keeps_webmcp_tool_and_parses_args() -> None:
    a = _parse(_resp({
        "action": "webmcp_call", "intent": "add to cart",
        "tool": "add_to_cart", "args": '{"sku": "widget"}',
    }))
    assert a is not None and a.action == "webmcp_call"
    assert a.tool == "add_to_cart"
    assert a.args == {"sku": "widget"}  # JSON-string args decoded to a dict
