"""
LLM provider with fallback chain.

Tries providers in order: OpenAI → DeepSeek → Gemini.
Falls back to next provider if one fails (rate limit, error, etc.).

All three providers use OpenAI-compatible API, so the LangChain
ChatOpenAI class works for all of them.
"""

from __future__ import annotations

import logging
from typing import Any

from tradebot.config import settings

LOG = logging.getLogger("tradebot.agents.llm")

# Provider config: (env_key, base_url, default_model)
PROVIDERS: list[dict[str, str]] = [
    {
        "name": "groq",
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
    },
    {
        "name": "openai",
        "env_key": "OPENAI_API_KEY",
        "base_url": "",  # default
        "default_model": "gpt-4o-mini",
    },
    {
        "name": "deepseek",
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
    },
    {
        "name": "gemini",
        "env_key": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.0-flash",
    },
    {
        "name": "grok",
        "env_key": "GROK_API_KEY",
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-2-latest",
    },
]


def get_llm(
    model: str | None = None,
    temperature: float = 0.1,
    prefer: str | None = None,
) -> Any:
    """Get an LLM instance with automatic fallback.

    Tries providers in order (or starts with `prefer` if given).
    Returns a LangChain ChatModel that's compatible with all three.

    Args:
        model: Specific model name (e.g. "gpt-4o"). Uses default if None.
        temperature: Sampling temperature (0 = deterministic).
        prefer: Provider name to try first ("openai", "deepseek", "gemini").

    Returns:
        A LangChain ChatModel instance.

    Raises:
        RuntimeError: If no provider is configured.
    """
    from langchain_openai import ChatOpenAI

    # Reorder providers if `prefer` is given
    providers = list(PROVIDERS)
    if prefer:
        for i, p in enumerate(providers):
            if p["name"] == prefer:
                providers.insert(0, providers.pop(i))
                break

    last_error: Exception | None = None

    for provider in providers:
        api_key = settings.__dict__.get(provider["env_key"], "") or ""
        if not api_key:
            LOG.debug(f"Skipping {provider['name']}: no API key")
            continue

        try:
            kwargs: dict[str, Any] = {
                "model": model or provider["default_model"],
                "temperature": temperature,
                "api_key": api_key,
            }
            if provider["base_url"]:
                kwargs["base_url"] = provider["base_url"]

            llm = ChatOpenAI(**kwargs)
            LOG.info(
                f"✓ LLM ready: {provider['name']}/{kwargs['model']} "
                f"(base_url={kwargs.get('base_url', 'default')})"
            )
            return llm
        except Exception as e:
            LOG.warning(f"Failed to init {provider['name']}: {e}")
            last_error = e
            continue

    raise RuntimeError(
        f"No LLM provider available. Set one of: "
        f"{', '.join(p['env_key'] for p in PROVIDERS)}. "
        f"Last error: {last_error}"
    )


def list_available_providers() -> list[str]:
    """List providers that have API keys configured."""
    available = []
    for p in PROVIDERS:
        key = settings.__dict__.get(p["env_key"], "") or ""
        if key:
            available.append(p["name"])
    return available
