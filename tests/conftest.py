"""Shared fixtures for the agentteam test suite.

``pytest-asyncio`` is deliberately not required: async code is exercised with
``asyncio.run`` through the ``run`` fixture, which keeps the dependency list
small and the tests fast.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from agentteam.config import Settings
from agentteam.tools.file_tool import Workspace
from agentteam.tools.registry import build_default_registry
from agentteam.tools.search_tool import SearchTool
from agentteam.tools.shell_tool import ShellTool

ENV_PREFIXES = ("AGENT_", "OPENAI_", "REDIS_URL")


@pytest.fixture
def run() -> Callable[[Coroutine[Any, Any, Any]], Any]:
    """Execute a coroutine inside a sync test."""

    def _run(coro: Coroutine[Any, Any, Any]) -> Any:
        return asyncio.run(coro)

    return _run


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure the developer's own .env never leaks into a test."""

    for key in list(os.environ):
        if key.startswith(ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    settings = Settings.from_env(root=tmp_path, env_file=tmp_path / "absent.env")
    settings.workspace = tmp_path / "workspace"
    settings.runs_dir = tmp_path / "runs"
    settings.bus_backend = "memory"
    settings.shell_timeout = 60.0
    settings.ensure_dirs()
    return settings


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / "ws")


@pytest.fixture
def shell(workspace: Workspace) -> ShellTool:
    return ShellTool(workspace.root, timeout=60.0)


@pytest.fixture
def search(workspace: Workspace) -> SearchTool:
    return SearchTool(workspace)


@pytest.fixture
def registry(workspace: Workspace, shell: ShellTool, search: SearchTool):
    return build_default_registry(workspace, shell, search)
