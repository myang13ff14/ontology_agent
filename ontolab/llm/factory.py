"""Choose the model from configuration. The lab uses OpenAI models.

    LLM_MODEL             OpenAI model name (must support tool calling)
    LLM_API_KEY           OpenAI API key
    LLM_PROVIDER          openai | mock   (default: openai when LLM_MODEL is set, otherwise mock)
    LLM_TEMPERATURE       optional sampling temperature
    LLM_REASONING_EFFORT  optional reasoning effort (some models need 'none' to call tools)
    LLM_BASE_URL          optional override of the API address (default https://api.openai.com/v1)

"mock" is not a model: it is a fixed policy used by the tests and when no key is
configured, so the pipeline can run without credentials.
"""

from __future__ import annotations

import os

from .base import LLMClient, LLMError
from .mock import MockLLM
from .openai_compat import OpenAICompatibleClient

PROVIDERS = ("openai", "mock")


def make_llm(provider: str | None = None, model: str | None = None) -> LLMClient:
    env = os.environ
    provider = (provider or env.get("LLM_PROVIDER") or ("openai" if env.get("LLM_MODEL") else "mock")).strip().lower()
    if provider == "mock":
        return MockLLM()
    if provider != "openai":
        raise LLMError(f"Unknown LLM_PROVIDER '{provider}'. Use one of: {', '.join(PROVIDERS)}.")
    model = (model or env.get("LLM_MODEL") or "").strip()
    if not model:
        raise LLMError("LLM_MODEL must be set to an OpenAI model name.")
    return OpenAICompatibleClient(
        model=model, base_url=env.get("LLM_BASE_URL", "").strip(), api_key=env.get("LLM_API_KEY", "").strip(),
        provider="openai", temperature=float(env["LLM_TEMPERATURE"]) if env.get("LLM_TEMPERATURE") else None,
        reasoning_effort=env.get("LLM_REASONING_EFFORT", "").strip() or None)


def describe(llm: LLMClient) -> dict:
    return {"provider": llm.provider, "model": llm.model, "is_mock": llm.is_mock}
