"""Runtime configuration: read from ``.env`` / environment variables.

Every agent (role) can be pointed at its own model / endpoint, which is what
turns a single LLM into a *team* of cooperating models.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ROLES: tuple[str, ...] = ("planner", "actor", "reviewer", "tester")
PROVIDERS: tuple[str, ...] = ("mock", "openai")
BUS_BACKENDS: tuple[str, ...] = ("auto", "memory", "redis")

DEFAULT_GOAL = (
    "创建一个 Python 模块 hello.py，提供 greet(name) 函数，返回 'Hello, <name>!'；"
    "并编写 pytest 测试 test_hello.py 覆盖它。"
)


def load_env_file(path: Path | str | None) -> bool:
    """Load ``KEY=VALUE`` pairs from *path* without overwriting real env vars."""

    if path is None:
        return False
    path = Path(path)
    if not path.is_file():
        return False

    try:  # python-dotenv is optional: fall back to a tiny built-in parser
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
        return True
    except ImportError:
        pass

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    return True


def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _get_int(name: str, default: int) -> int:
    try:
        return int(str(_get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _get_int_or_none(name: str) -> int | None:
    raw = _get(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _get_float(name: str, default: float) -> float:
    try:
        return float(str(_get(name, str(default))))
    except (TypeError, ValueError):
        return default



@dataclass
class ModelConfig:
    """How one agent talks to a model."""

    provider: str = "mock"
    model: str = "mock"
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.2
    max_tokens: int | None = None

    @property
    def requires_api_key(self) -> bool:
        return self.provider == "openai"

    def describe(self) -> str:
        """Human readable label that never leaks the key."""

        if self.base_url:
            return f"{self.provider}:{self.model} @ {self.base_url}"
        return f"{self.provider}:{self.model}"


@dataclass
class Settings:
    root: Path
    workspace: Path
    runs_dir: Path
    bus_backend: str = "auto"
    redis_url: str = "redis://localhost:6379/0"
    default_model: ModelConfig = field(default_factory=ModelConfig)
    role_models: dict[str, ModelConfig] = field(default_factory=dict)
    max_rounds: int = 3
    max_tool_steps: int = 8
    session_timeout: float = 300.0
    shell_timeout: float = 120.0

    def model_for(self, role: str) -> ModelConfig:
        return self.role_models.get(role, self.default_model)

    def ensure_dirs(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def describe(self) -> str:
        lines = [f"bus      -> {self.bus_backend} ({self.redis_url})"]
        for role in ROLES:
            lines.append(f"{role:8s} -> {self.model_for(role).describe()}")
        return "\n".join(lines)

    def warnings(self) -> list[str]:
        """Configuration smells worth telling the user about *before* a run."""

        messages: list[str] = []
        for role in ROLES:
            config = self.model_for(role)
            if config.requires_api_key and not config.api_key:
                messages.append(
                    f"{role}: provider=openai 但缺少 API key"
                    f"（设置 OPENAI_API_KEY 或 AGENT_{role.upper()}_API_KEY）"
                )
        if self.max_rounds < 1:
            messages.append("max_rounds < 1，将按 1 轮执行")
        if self.session_timeout <= 0:
            messages.append("session_timeout <= 0，将使用默认 300s")
        if self.max_tool_steps < 1:
            messages.append("max_tool_steps < 1，执行者将不会调用任何工具")
        return messages

    def as_dict(self) -> dict[str, object]:
        """Key-free snapshot, used by ``--check`` and the web UI."""

        def model(role: str) -> dict[str, object]:
            config = self.model_for(role)
            return {
                "provider": config.provider,
                "model": config.model,
                "base_url": config.base_url,
                "has_api_key": bool(config.api_key),
            }

        return {
            "bus": self.bus_backend,
            "redis_url": self.redis_url,
            "workspace": str(self.workspace),
            "runs_dir": str(self.runs_dir),
            "max_rounds": self.max_rounds,
            "max_tool_steps": self.max_tool_steps,
            "session_timeout": self.session_timeout,
            "models": {role: model(role) for role in ROLES},
            "warnings": self.warnings(),
        }

    @classmethod
    def from_env(
        cls,
        *,
        root: Path | str | None = None,
        env_file: Path | str | None = None,
        **overrides: object,
    ) -> "Settings":
        root_path = Path(root).resolve() if root else PROJECT_ROOT
        if env_file is None:
            # AGENT_ENV_FILE lets a packaged install keep its .env outside the
            # (read-only) installation directory.
            env_file = os.environ.get("AGENT_ENV_FILE") or root_path / ".env"
        load_env_file(env_file)

        provider = (_get("AGENT_LLM_PROVIDER") or ("openai" if _get("OPENAI_API_KEY") else "mock")).lower()
        if provider not in PROVIDERS:
            raise ValueError(
                f"AGENT_LLM_PROVIDER 只能是 {PROVIDERS} 之一，当前为 {provider!r}"
                "（留空时会根据是否有 OPENAI_API_KEY 自动选择 mock / openai）"
            )

        default = ModelConfig(
            provider=provider,
            model=_get("AGENT_MODEL") or _get("OPENAI_MODEL") or ("gpt-4o-mini" if provider == "openai" else "mock"),
            base_url=_get("OPENAI_BASE_URL"),
            api_key=_get("OPENAI_API_KEY"),
            temperature=_get_float("AGENT_TEMPERATURE", 0.2),
            max_tokens=_get_int_or_none("AGENT_MAX_TOKENS"),
        )

        role_models: dict[str, ModelConfig] = {}
        for role in ROLES:
            prefix = f"AGENT_{role.upper()}_"
            explicit = [
                _get(prefix + suffix)
                for suffix in ("PROVIDER", "MODEL", "BASE_URL", "API_KEY", "TEMPERATURE", "MAX_TOKENS")
            ]
            if not any(explicit):
                continue
            role_provider = (_get(prefix + "PROVIDER") or provider).lower()
            if role_provider not in PROVIDERS:
                raise ValueError(
                    f"{prefix}PROVIDER 只能是 {PROVIDERS} 之一，当前为 {role_provider!r}"
                )
            role_models[role] = ModelConfig(
                provider=role_provider,
                model=_get(prefix + "MODEL") or default.model,
                base_url=_get(prefix + "BASE_URL") or default.base_url,
                api_key=_get(prefix + "API_KEY") or default.api_key,
                temperature=_get_float(prefix + "TEMPERATURE", default.temperature),
                max_tokens=_get_int_or_none(prefix + "MAX_TOKENS") or default.max_tokens,
            )

        bus_backend = (_get("AGENT_BUS") or "auto").lower()
        if bus_backend not in BUS_BACKENDS:
            raise ValueError(f"AGENT_BUS must be one of {BUS_BACKENDS}, got {bus_backend!r}")

        settings = cls(
            root=root_path,
            workspace=Path(_get("AGENT_WORKSPACE") or root_path / "workspace").resolve(),
            runs_dir=Path(_get("AGENT_RUNS_DIR") or root_path / "runs").resolve(),
            bus_backend=bus_backend,
            redis_url=_get("REDIS_URL") or "redis://localhost:6379/0",
            default_model=default,
            role_models=role_models,
            max_rounds=_get_int("AGENT_MAX_ROUNDS", 3),
            max_tool_steps=_get_int("AGENT_MAX_TOOL_STEPS", 8),
            session_timeout=_get_float("AGENT_SESSION_TIMEOUT", 300.0),
            shell_timeout=_get_float("AGENT_SHELL_TIMEOUT", 120.0),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(settings, key, value)
        return settings
