"""Claude through the official Anthropic SDK (optional dependency: `pip install anthropic`)."""

from __future__ import annotations

from .base import LLMError, LLMResponse, ToolCall

DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicClient:
    provider = "anthropic"
    is_mock = False

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str = "", effort: str = "medium", max_tokens: int = 16000):
        try:
            import anthropic
        except ImportError:
            raise LLMError("The 'anthropic' package is not installed. Run: pip install anthropic") from None
        self._anthropic = anthropic
        self.model = model or DEFAULT_MODEL
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        anthropic = self._anthropic
        request: dict = {
            "model": self.model, "max_tokens": self.max_tokens, "system": system,
            "messages": self._wire(messages), "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
            # If a safety classifier declines a request, the API re-runs it on a fallback model.
            "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default",
        }
        if tools:
            request["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
                                for t in tools]
        try:
            response = self._client.beta.messages.create(**request)
        except anthropic.AuthenticationError:
            raise LLMError("Anthropic rejected the API key.") from None
        except anthropic.NotFoundError:
            raise LLMError(f"Anthropic model '{self.model}' was not found.") from None
        except anthropic.RateLimitError:
            raise LLMError("Anthropic is rate limiting this key.") from None
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic returned an error ({exc.status_code}): {exc.message}") from None
        except anthropic.APIConnectionError:
            raise LLMError("Could not reach the Anthropic API.") from None
        except TypeError as exc:  # the SDK raises TypeError when no credentials are configured
            if "authentication method" not in str(exc):
                raise
            raise LLMError("No Anthropic credentials found. Set ANTHROPIC_API_KEY.") from None

        text = "".join(block.text for block in response.content if block.type == "text").strip()
        calls = [ToolCall(block.id, block.name, block.input if isinstance(block.input, dict) else None)
                 for block in response.content if block.type == "tool_use"]
        if response.stop_reason == "refusal":
            text = text or "[The model declined to answer this request.]"
        return LLMResponse(text=text, tool_calls=calls, input_tokens=response.usage.input_tokens,
                           output_tokens=response.usage.output_tokens, stop_reason=response.stop_reason or "",
                           raw=response.content)  # replayed unchanged, including thinking blocks

    @staticmethod
    def _wire(messages: list[dict]) -> list[dict]:
        wire: list[dict] = []
        for message in messages:
            if message["role"] == "assistant":
                content = message.get("raw")
                if content is None:
                    content = [{"type": "text", "text": message["content"]}] if message.get("content") else []
                    content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments or {}}
                                for c in message.get("tool_calls") or []]
                wire.append({"role": "assistant", "content": content})
            elif message["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": message["content"]}
                # All results of one assistant turn go back in a single user message.
                if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list) \
                        and wire[-1]["content"] and wire[-1]["content"][0].get("type") == "tool_result":
                    wire[-1]["content"].append(block)
                else:
                    wire.append({"role": "user", "content": [block]})
            else:
                wire.append({"role": "user", "content": message["content"]})
        return wire
