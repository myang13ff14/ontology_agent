"""Provider-neutral LLM interface.

Conversation messages are plain dicts:
    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [ToolCall, ...], "raw": provider payload}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}
Tools are dicts: {"name", "description", "input_schema"}.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(Exception):
    """The model could not be reached or returned something unusable."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Any  # parsed JSON object, or None when the model sent invalid JSON
    raw_arguments: str = ""


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    raw: Any = None  # provider payload to replay on the next request

    def as_message(self) -> dict:
        return {"role": "assistant", "content": self.text, "tool_calls": self.tool_calls, "raw": self.raw}


class LLMClient(Protocol):
    provider: str
    model: str
    is_mock: bool

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse: ...
