"""Provider-neutral OpenAI-compatible client boundary.

Credentials and endpoints are read only from environment variables. No local
configuration files, private gateways, or machine-specific paths are used.
"""
from __future__ import annotations

import os
from typing import Any

_NO_TEMP = frozenset(
    item.strip() for item in os.getenv("OPENAI_NO_TEMPERATURE_MODELS", "").split(",")
    if item.strip()
)


def read_config_key(name: str, root=None) -> str | None:
    """Return an environment variable; ``root`` is accepted for compatibility."""
    del root
    return os.getenv(name)


def _credentials(api_key_env: str) -> tuple[str, str | None]:
    key = os.getenv(api_key_env)
    if not key:
        raise RuntimeError(f"Missing environment variable {api_key_env}")
    return key, os.getenv("OPENAI_BASE_URL")


def client_for_model(model: str, *, api_key_env: str = "OPENAI_API_KEY", root=None):
    """Create a synchronous client for an OpenAI-compatible endpoint."""
    del model, root
    from openai import OpenAI

    key, base_url = _credentials(api_key_env)
    kwargs: dict[str, Any] = {"api_key": key, "timeout": 90.0}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def chat(client, model: str, prompt: str, *, thinking=None,
         max_tokens: int | None = None,
         response_format: dict | None = None) -> str:
    """Run one deterministic chat-completion request."""
    del thinking
    request: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if model not in _NO_TEMP:
        request["temperature"] = 0.0
    if max_tokens is not None:
        request["max_tokens"] = max_tokens
    if response_format is not None:
        request["response_format"] = response_format
    try:
        response = client.chat.completions.create(**request)
    except Exception:
        request.pop("temperature", None)
        response = client.chat.completions.create(**request)
    return (response.choices[0].message.content or "").strip()


def agent_sdk_model(model: str, *, api_key_env: str = "OPENAI_API_KEY", root=None):
    """Create an OpenAI Agents SDK model over the same environment boundary."""
    del root
    from agents import OpenAIChatCompletionsModel
    from openai import AsyncOpenAI

    key, base_url = _credentials(api_key_env)
    kwargs: dict[str, Any] = {"api_key": key, "timeout": 90.0}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAIChatCompletionsModel(model, AsyncOpenAI(**kwargs))
