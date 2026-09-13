"""Create the per-role LLM clients, so each agent may use a different model."""

from __future__ import annotations

from ..config import ROLES, Settings
from .client import BaseLLMClient, LLMError, OpenAICompatibleClient, ScriptedLLM
from .mock import mock_clients


def build_client(settings: Settings, role: str) -> BaseLLMClient:
    config = settings.model_for(role)
    if config.provider == "mock":
        clients = mock_clients()
        return clients.get(role) or ScriptedLLM([{"summary": f"mock {role}"}], model=f"mock-{role}")
    if config.provider == "openai":
        return OpenAICompatibleClient(
            config.model,
            base_url=config.base_url,
            api_key=config.api_key,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            timeout=settings.shell_timeout,
        )
    raise LLMError(f"unsupported provider {config.provider!r} for role {role}")


def build_clients(settings: Settings) -> dict[str, BaseLLMClient]:
    return {role: build_client(settings, role) for role in ROLES}
