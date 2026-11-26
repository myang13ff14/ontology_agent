"""Agent runtime: the tool-calling loop.

1. the question and the available tool definitions go to the LLM
2. the LLM returns structured tool calls
3. the runtime validates each call (name, arguments) and executes it
4. structured results go back to the LLM
5. repeat until the LLM answers, the tool-call budget is spent, or time runs out

Nothing here routes questions to tools: the LLM chooses. Every invocation is
recorded with arguments, result, timing, errors and ontology version.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from ..llm.base import LLMClient, LLMError
from ..llm.factory import describe, make_llm
from ..services import Services
from .configs import CONFIGS, system_prompt

MAX_RESULT_CHARS = 24000


class AgentRuntime:
    def __init__(self, services: Services, llm: LLMClient | None = None):
        self.services = services
        self._llm = llm

    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = make_llm()
        return self._llm

    def tools_for(self, config_key: str, disabled_tools: list[str] | None = None,
                  extra_tools: list[str] | None = None) -> list[str]:
        names = list(CONFIGS[config_key].tools) + [t for t in extra_tools or [] if t not in CONFIGS[config_key].tools]
        return [n for n in names if self.services.registry.has(n) and n not in set(disabled_tools or [])]

    def run(self, question: str, config_key: str, *, llm: LLMClient | None = None,
            disabled_tools: list[str] | None = None, extra_tools: list[str] | None = None,
            description_overrides: dict[str, str] | None = None, max_tool_calls: int | None = None,
            tool_timeout_s: float | None = None, run_timeout_s: float | None = None,
            history: list[dict] | None = None) -> dict:
        """Answer one question. `history` is earlier turns to replay first (see agents/memory.py).

        The returned trace has a private "_turn_messages" entry: the messages this turn
        added, for the caller to store as memory. It is not JSON and must not be sent out.
        """
        if config_key not in CONFIGS:
            raise KeyError(f"Unknown agent configuration '{config_key}'. Valid: {sorted(CONFIGS)}")
        settings, registry = self.services.settings, self.services.registry
        config = CONFIGS[config_key]
        llm = llm or self.llm()
        max_tool_calls = settings.max_tool_calls if max_tool_calls is None else max_tool_calls
        run_timeout_s = run_timeout_s or settings.run_timeout_s
        tool_names = self.tools_for(config_key, disabled_tools, extra_tools)
        tools = registry.llm_tools(tool_names, description_overrides)
        terminology = self.services.terminology.render_for_prompt() if config.uses_terminology_prompt else ""
        system = system_prompt(config, terminology)

        started = time.perf_counter()
        history = list(history or [])
        messages: list[dict] = history + [{"role": "user", "content": question}]
        tool_calls: list[dict] = []
        llm_turns: list[dict] = []
        final_answer, stop_reason, error = "", "answered", None

        def remaining() -> float:
            return run_timeout_s - (time.perf_counter() - started)

        while True:
            budget_left = max_tool_calls - len(tool_calls)
            if remaining() <= 0:
                stop_reason = "timeout"
                break
            offered = tools if budget_left > 0 else []
            if budget_left <= 0:
                messages.append({"role": "user", "content":
                                 "The tool-call budget is used up. Answer now from the results above, and say what "
                                 "you could not establish."})
            turn_started = time.perf_counter()
            try:
                response = llm.complete(system, messages, offered)
            except LLMError as exc:
                stop_reason, error = "llm_error", str(exc)
                break
            llm_turns.append({"latency_ms": round((time.perf_counter() - turn_started) * 1000, 1),
                              "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
                              "tool_calls_requested": len(response.tool_calls)})
            messages.append(response.as_message())

            if not response.tool_calls or budget_left <= 0:
                final_answer = response.text
                if budget_left <= 0:
                    stop_reason = "max_tool_calls"
                break

            for call in response.tool_calls:
                if len(tool_calls) >= max_tool_calls:
                    payload = {"error": {"code": "tool_budget_exhausted", "message": "Tool-call budget is used up.", "details": {}}}
                    messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": json.dumps(payload)})
                    continue
                if call.arguments is None:
                    invocation = registry.execute(call.name, call.raw_arguments, allowed=tool_names)
                else:
                    invocation = registry.execute(call.name, call.arguments, allowed=tool_names,
                                                  timeout_s=min(tool_timeout_s or settings.tool_timeout_s, max(remaining(), 0.1)))
                record = invocation.to_dict()
                record["index"] = len(tool_calls) + 1
                tool_calls.append(record)
                content = json.dumps(invocation.payload(), default=str)
                if len(content) > MAX_RESULT_CHARS:
                    content = content[:MAX_RESULT_CHARS] + '... [result truncated by the runtime]'
                    record["truncated_for_llm"] = True
                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content})

        node_ids, edge_ids = [], []
        for record in tool_calls:
            for node_id in record["provenance"].get("node_ids", []):
                if node_id not in node_ids:
                    node_ids.append(node_id)
            for edge_id in record["provenance"].get("edge_ids", []):
                if edge_id not in edge_ids:
                    edge_ids.append(edge_id)
        return {
            "run_id": uuid.uuid4().hex[:12],
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "question": question, "config": config_key, "config_name": config.name,
            "llm": describe(llm), "is_mock": llm.is_mock,
            "ontology_version": self.services.store.version(),
            "terminology_version": self.services.terminology.version if config.uses_terminology_prompt else None,
            "tools_available": tool_names, "final_answer": final_answer, "stop_reason": stop_reason, "error": error,
            "tool_calls": tool_calls, "llm_turns": llm_turns,
            "generated_sql": [r["generated_sql"] for r in tool_calls if r.get("generated_sql")],
            "subgraph": {"node_ids": node_ids, "edge_ids": edge_ids},
            "metrics": {
                "tool_call_count": len(tool_calls),
                "tool_error_count": sum(1 for r in tool_calls if not r["ok"]),
                "llm_turns": len(llm_turns),
                "input_tokens": sum(t["input_tokens"] for t in llm_turns),
                "output_tokens": sum(t["output_tokens"] for t in llm_turns),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "tool_time_ms": round(sum(r["duration_ms"] for r in tool_calls), 1),
            },
            "limits": {"max_tool_calls": max_tool_calls, "run_timeout_s": run_timeout_s},
            "memory": {"replayed_messages": len(history)},
            "_turn_messages": messages[len(history):],
        }
