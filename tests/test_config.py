"""Configuration tests: .env parsing and per-role model overrides."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteam.config import DEFAULT_GOAL, ROLES, ModelConfig, Settings, load_env_file


def test_defaults_use_the_offline_mock_provider(tmp_path: Path) -> None:
    settings = Settings.from_env(root=tmp_path, env_file=tmp_path / "absent.env")

    assert settings.default_model.provider == "mock"
    assert settings.model_for("actor").model == "mock"
    assert settings.bus_backend == "auto"
    assert settings.max_rounds == 3
    assert DEFAULT_GOAL


def test_openai_api_key_switches_the_default_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")

    settings = Settings.from_env(root=tmp_path, env_file=tmp_path / "absent.env")

    assert settings.default_model.provider == "openai"
    assert settings.default_model.base_url == "http://localhost:11434/v1"
    assert settings.default_model.requires_api_key


def test_role_overrides_are_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AGENT_ACTOR_MODEL", "qwen2.5-coder:14b")
    monkeypatch.setenv("AGENT_ACTOR_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("AGENT_REVIEWER_TEMPERATURE", "0.9")

    settings = Settings.from_env(root=tmp_path, env_file=tmp_path / "absent.env")

    assert settings.model_for("planner").model == "gpt-4o-mini"
    assert settings.model_for("actor").model == "qwen2.5-coder:14b"
    assert settings.model_for("actor").base_url == "http://localhost:11434/v1"
    assert settings.model_for("reviewer").temperature == pytest.approx(0.9)
    assert settings.model_for("tester").model == "gpt-4o-mini"
    assert "sk-test" not in settings.describe()


def test_invalid_values_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS", "kafka")
    with pytest.raises(ValueError):
        Settings.from_env(root=tmp_path, env_file=tmp_path / "absent.env")

    monkeypatch.delenv("AGENT_BUS")
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "carrier-pigeon")
    with pytest.raises(ValueError):
        Settings.from_env(root=tmp_path, env_file=tmp_path / "absent.env")


def test_overrides_can_be_injected_programmatically(tmp_path: Path) -> None:
    settings = Settings.from_env(
        root=tmp_path,
        env_file=tmp_path / "absent.env",
        bus_backend="memory",
        max_rounds=7,
        default_model=ModelConfig(provider="mock", model="unit"),
    )

    assert settings.bus_backend == "memory"
    assert settings.max_rounds == 7
    assert settings.model_for("planner").model == "unit"


def test_env_file_never_overrides_real_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('AGENT_MODEL="from-file"\nAGENT_BUS=redis\n', encoding="utf-8")
    monkeypatch.setenv("AGENT_MODEL", "from-env")

    assert load_env_file(env_file) is True
    assert Settings.from_env(root=tmp_path, env_file=env_file).default_model.model == "from-env"
    assert Settings.from_env(root=tmp_path, env_file=env_file).bus_backend == "redis"


def test_env_file_can_be_pointed_at_by_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGENT_ENV_FILE is what the packaged launchers rely on."""

    custom = tmp_path / "custom.env"
    custom.write_text("AGENT_MODEL=from-custom-file\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_ENV_FILE", str(custom))

    settings = Settings.from_env(root=tmp_path, env_file=None)

    assert settings.default_model.model == "from-custom-file"


def test_warnings_flag_every_role_missing_an_openai_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    settings = Settings.from_env(root=tmp_path, env_file=tmp_path / "absent.env")

    messages = settings.warnings()
    assert len(messages) == len(ROLES)
    assert all("API key" in message for message in messages)

    offline = Settings.from_env(
        root=tmp_path,
        env_file=tmp_path / "absent.env",
        default_model=ModelConfig(provider="mock", model="mock"),
    )
    assert offline.warnings() == []


def test_warnings_are_scoped_to_the_offending_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_ACTOR_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_ACTOR_MODEL", "gpt-4o-mini")

    settings = Settings.from_env(root=tmp_path, env_file=tmp_path / "absent.env")

    (message,) = settings.warnings()
    assert message.startswith("actor:")
    assert "OPENAI_API_KEY" in message


def test_warnings_reach_unsafe_limits(tmp_path: Path) -> None:
    settings = Settings.from_env(
        root=tmp_path,
        env_file=tmp_path / "absent.env",
        max_rounds=0,
        max_tool_steps=0,
        session_timeout=0.0,
    )

    messages = settings.warnings()

    assert len(messages) == 3
    assert any("max_rounds" in message for message in messages)
    assert any("max_tool_steps" in message for message in messages)
    assert any("session_timeout" in message for message in messages)


def test_as_dict_is_key_free_and_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")

    settings = Settings.from_env(root=tmp_path, env_file=tmp_path / "absent.env")
    snapshot = settings.as_dict()

    assert snapshot["bus"] == "auto"
    assert snapshot["workspace"] == str(settings.workspace)
    assert set(snapshot["models"]) == set(ROLES)
    assert snapshot["models"]["tester"]["has_api_key"] is True
    assert snapshot["warnings"] == []
    assert "sk-secret" not in json.dumps(snapshot)
