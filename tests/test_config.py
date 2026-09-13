"""Configuration tests: .env parsing and per-role model overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteam.config import DEFAULT_GOAL, ModelConfig, Settings, load_env_file


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
