"""Provider protocol and the canonical action-tool schema.

`ACTION_TOOL` is expressed once here as a provider-neutral JSON Schema. The Anthropic
adapter passes it as a tool; future OpenAI/Gemini adapters will translate the same
schema into their own tool shapes (PLAN.md §5).
"""

from __future__ import annotations

from typing import Optional, Protocol

from ..types import Action, Observation

# JSON Schema for the single "act" decision. `strict` + additionalProperties:false make
# the emitted arguments schema-guaranteed valid, so the replay path never needs a retry.
ACTION_TOOL: dict = {
    "name": "act",
    "description": (
        "Choose the single best next browser action to make progress on the GOAL, "
        "using the interactable elements in the observation."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "click",
                    "type",
                    "press",
                    "scroll",
                    "navigate",
                    "done",
                    "give_up",
                    "webmcp_call",
                    "need_vision",
                ],
            },
            "intent": {
                "type": "string",
                "description": "Why — the sub-goal this step accomplishes. Stored to replay the step later without an LLM.",
            },
            "ref": {
                "type": "string",
                "description": "Target element ref from the observation (e.g. 'e12'). Required for click/type.",
            },
            "text": {
                "type": "string",
                "description": "Text to type, key name to press (e.g. 'Enter'), or URL to navigate to.",
            },
            "tool": {
                "type": "string",
                "description": "WebMCP tool name to invoke (only with action='webmcp_call').",
            },
            "args": {
                "type": "string",
                "description": "JSON object of arguments for the WebMCP tool (only with action='webmcp_call').",
            },
        },
        "required": ["action", "intent"],
    },
}


class Provider(Protocol):
    async def decide(
        self, goal: str, obs: Observation, history: list[str]
    ) -> tuple[Action, Optional[float]]:
        """Return the next action and, if measured, the time-to-first-token in ms."""
        ...
