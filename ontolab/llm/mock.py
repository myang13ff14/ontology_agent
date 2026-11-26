"""Mock LLM: exercises the whole tool-calling pipeline with no API credentials.

It is a fixed, deterministic policy, NOT a language model. It picks tools by
word overlap between the question and tool descriptions, fills arguments from the
question and from ids seen in earlier results, and "answers" by listing what the
tools returned. It knows nothing about any benchmark. Results produced with it
measure the plumbing only and are always labelled as mock.
"""

from __future__ import annotations

import json
import re

from ..text import overlap, tokens
from .base import LLMResponse, ToolCall

MAX_MOCK_CALLS = 3
_TEXT_ARGS = ("query", "term", "customer_text")


class MockLLM:
    provider = "mock"
    model = "mock-policy-v1"
    is_mock = True

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        question = next((m["content"] for m in messages if m["role"] == "user"), "")
        results = []
        for m in messages:
            if m["role"] == "tool":
                try:
                    results.append((m["name"], json.loads(m["content"])))
                except json.JSONDecodeError:
                    results.append((m["name"], {}))
        called = [name for name, _ in results]
        usage = {"input_tokens": sum(len(str(m.get("content", "")).split()) for m in messages) + len(system.split()),
                 "output_tokens": 0}

        call = None
        if tools and len(called) < MAX_MOCK_CALLS:
            call = self._first_call(question, tools) if not called else self._follow_up(question, tools, results, called)
        if call:
            name, arguments = call
            usage["output_tokens"] = len(json.dumps(arguments).split()) + 2
            return LLMResponse(text="", tool_calls=[ToolCall(f"mock_{len(called) + 1}", name, arguments, json.dumps(arguments))],
                               stop_reason="tool_use", **usage)
        text = self._answer(results)
        usage["output_tokens"] = len(text.split())
        return LLMResponse(text=text, stop_reason="end_turn", **usage)

    @staticmethod
    def _required(tool: dict) -> list[str]:
        return list(tool["input_schema"].get("required", []))

    def _first_call(self, question: str, tools: list[dict]):
        """A tool that needs only free text, else one that needs nothing, else a metric query."""
        ranked = sorted(tools, key=lambda t: (-overlap(question, t["name"].replace("_", " ") + " " + t["description"][:200]), t["name"]))
        for tool in ranked:
            required = self._required(tool)
            if len(required) == 1 and required[0] in _TEXT_ARGS:
                return tool["name"], {required[0]: question}
        for tool in ranked:
            if "metrics" in tool["input_schema"].get("properties", {}):
                return tool["name"], self._metric_query(question, tool)
        for tool in ranked:
            if not self._required(tool):
                return tool["name"], {}
        return None

    def _follow_up(self, question: str, tools: list[dict], results: list, called: list[str]):
        """A not-yet-called tool whose single required id can be filled from the latest result."""
        latest = json.dumps(results[-1][1])
        ranked = sorted(tools, key=lambda t: (-overlap(question, t["name"].replace("_", " ") + " " + t["description"][:200]), t["name"]))
        for tool in ranked:
            required = self._required(tool)
            if tool["name"] in called or len(required) != 1 or not required[0].endswith("_id"):
                continue
            keys = [required[0], "node_id"] if required[0] not in ("object_id", "start_node_id") else ["node_id"]
            for key in keys:
                found = re.search(rf'"{key}": "([^"]+)"', latest)
                if found:
                    return tool["name"], {required[0]: found.group(1)}
        for tool in ranked:
            if tool["name"] not in called and "metrics" in tool["input_schema"].get("properties", {}):
                return tool["name"], self._metric_query(question, tool)
        return None

    @staticmethod
    def _metric_query(question: str, tool: dict) -> dict:
        names = re.findall(r"- `([a-z_]+)` \[", tool["description"])
        words = set(tokens(question))
        chosen = [n for n in names if set(tokens(n.replace("_", " "))) <= words]
        return {"metrics": chosen[:1] or names[:1]}

    @staticmethod
    def _answer(results: list) -> str:
        if not results:
            return "[MOCK LLM] No tool was called, so there is nothing to report."
        names: list[str] = []
        for _, result in results:
            for found in re.findall(r'"(?:canonical_name|name|title)": "([^"]+)"', json.dumps(result)):
                if found not in names:
                    names.append(found)
        rows = [row for _, result in results if isinstance(result, dict) for row in (result.get("rows") or [])[:5]]
        parts = ["[MOCK LLM] This is not a model answer; it lists what the tools returned."]
        if names:
            parts.append("Objects: " + "; ".join(names[:20]) + ".")
        if rows:
            parts.append("Rows: " + json.dumps(rows, default=str))
        errors = [result["error"]["message"] for _, result in results if isinstance(result, dict) and "error" in result]
        if errors:
            parts.append("Errors: " + " | ".join(errors))
        return " ".join(parts)
