"""Any OpenAI-compatible chat-completions API: OpenAI, a local Ollama or LM Studio
server, and other compatible providers. Standard library only."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import LLMError, LLMResponse, ToolCall
from .http import ssl_context

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatibleClient:
    is_mock = False

    def __init__(self, model: str, base_url: str = DEFAULT_BASE_URL, api_key: str = "", provider: str = "openai",
                 temperature: float | None = None, reasoning_effort: str | None = None, timeout: float = 120.0):
        self.provider = provider
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        body: dict = {"model": self.model, "messages": [{"role": "system", "content": system}] + [self._wire(m) for m in messages]}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                               "parameters": t["input_schema"]}} for t in tools]
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        payload = self._post(body)
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("The model provider's answer had no message in it.") from None
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                arguments = None
            calls.append(ToolCall(call.get("id") or f"call_{len(calls)}", function.get("name", ""), arguments,
                                  raw if isinstance(raw, str) else json.dumps(raw)))
        usage = payload.get("usage") or {}
        return LLMResponse(
            text=(message.get("content") or "").strip(), tool_calls=calls,
            input_tokens=int(usage.get("prompt_tokens") or 0), output_tokens=int(usage.get("completion_tokens") or 0),
            stop_reason=str(payload["choices"][0].get("finish_reason") or ""), raw=message)

    @staticmethod
    def _wire(message: dict) -> dict:
        if message["role"] == "assistant":
            if message.get("raw"):
                return message["raw"]  # replay exactly what the provider sent
            wire = {"role": "assistant", "content": message.get("content") or ""}
            if message.get("tool_calls"):
                wire["tool_calls"] = [{"id": c.id, "type": "function",
                                       "function": {"name": c.name, "arguments": c.raw_arguments or json.dumps(c.arguments)}}
                                      for c in message["tool_calls"]]
            return wire
        if message["role"] == "tool":
            return {"role": "tool", "tool_call_id": message["tool_call_id"], "content": message["content"]}
        return {"role": "user", "content": message["content"]}

    def _post(self, body: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode("utf-8"),
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=ssl_context()) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                detail = _error_detail(exc.read())
            if exc.code in (401, 403):
                raise LLMError(f"The model provider rejected the API key ({exc.code}).") from None
            if exc.code == 404:
                raise LLMError(f"Model '{self.model}' was not found at {self.base_url}.") from None
            if exc.code == 429:
                raise LLMError(f"The model provider is rate limiting or out of quota. {detail}".strip()) from None
            raise LLMError(f"The model provider returned an error ({exc.code}). {detail}".strip()) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise LLMError(f"Could not reach the model at {self.base_url} ({getattr(exc, 'reason', exc)}).") from None
        except json.JSONDecodeError:
            raise LLMError("The model provider sent an unreadable answer.") from None


def _error_detail(raw: bytes) -> str:
    try:
        error = json.loads(raw).get("error")
        message = error.get("message") if isinstance(error, dict) else error
        return str(message)[:300] if message else ""
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        return ""
