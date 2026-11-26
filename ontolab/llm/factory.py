"""Choose the LLM from configuration.

    LLM_PROVIDER   mock | openai | anthropic | local      (default: openai if LLM_MODEL is set, else mock)
    LLM_MODEL      model name
    LLM_BASE_URL   base URL for openai / local (local default: http://localhost:11434/v1, i.e. Ollama)
    LLM_API_KEY    key for openai-compatible providers
    ANTHROPIC_API_KEY   key for anthropic
    LLM_TEMPERATURE     optional sampling temperature for openai / local
    LLM_REASONING_EFFORT  optional reasoning effort for openai / local (some models need 'none' to call tools)
    LLM_EFFORT          optional effort level for anthropic (default medium)
"""

from __future__ import annotations

import os

from .base import LLMClient, LLMError
from .mock import MockLLM
from .openai_compat import OpenAICompatibleClient

PROVIDERS = ("mock", "openai", "anthropic", "local")


def make_llm(provider: str | None = None, model: str | None = None) -> LLMClient:
    env = os.environ
    provider = (provider or env.get("LLM_PROVIDER") or ("openai" if env.get("LLM_MODEL") else "mock")).strip().lower()
    model = (model or env.get("LLM_MODEL") or "").strip()
    if provider == "mock":
        return MockLLM()
    temperature = float(env["LLM_TEMPERATURE"]) if env.get("LLM_TEMPERATURE") else None
    if provider == "anthropic":
        from .anthropic_client import AnthropicClient
        return AnthropicClient(model=model, api_key=env.get("ANTHROPIC_API_KEY", "").strip(),
                               effort=env.get("LLM_EFFORT", "medium").strip() or "medium")
    if provider in ("openai", "local"):
        if not model:
            raise LLMError(f"LLM_MODEL must be set for provider '{provider}'.")
        default_url = "http://localhost:11434/v1" if provider == "local" else ""
        return OpenAICompatibleClient(model=model, base_url=env.get("LLM_BASE_URL", "").strip() or default_url,
                                      api_key=env.get("LLM_API_KEY", "").strip(), provider=provider,
                                      temperature=temperature,
                                      reasoning_effort=env.get("LLM_REASONING_EFFORT", "").strip() or None)
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}'. Use one of: {', '.join(PROVIDERS)}.")


def describe(llm: LLMClient) -> dict:
    return {"provider": llm.provider, "model": llm.model, "is_mock": llm.is_mock}
