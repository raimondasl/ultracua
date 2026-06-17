"""Canonical, provider-neutral LLM types (content-block based)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ThinkingBlock:
    thinking: str = ""
    type: str = "thinking"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    type: str = "tool_use"


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str = ""
    is_error: bool = False
    type: str = "tool_result"


Block = Union[TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock]


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: list[Block]


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict
    strict: bool = False


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class LLMRequest:
    model: str = ""
    system: str = ""
    tools: list[ToolDef] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    force_tool: Optional[str] = None  # force the model to call this tool
    max_tokens: int = 512
    cache: bool = True  # enable prompt caching on the stable prefix (provider-permitting)
    thinking: bool = False

    def with_model(self, model: str) -> "LLMRequest":
        from dataclasses import replace

        return replace(self, model=model)


@dataclass
class LLMResponse:
    blocks: list[Block] = field(default_factory=list)
    model: str = ""
    stop_reason: str = ""
    usage: Usage = field(default_factory=Usage)
    ttft_ms: Optional[float] = None

    def tool_use(self, name: Optional[str] = None) -> Optional[ToolUseBlock]:
        for b in self.blocks:
            if isinstance(b, ToolUseBlock) and (name is None or b.name == name):
                return b
        return None

    def text(self) -> str:
        return "".join(b.text for b in self.blocks if isinstance(b, TextBlock))
