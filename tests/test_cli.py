"""CLI tests: history listing, replay, export and the web hook-up."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agentteam import __version__
from agentteam.config import Settings
from agentteam.main import main
from agentteam.runtime import run_goal

GOAL = "创建一个 Python 模块 hello.py 与 pytest 测试"


@pytest.fixture
def args(settings: Settings) -> list[str]:
    """CLI arguments that keep the test off the developer's own directories."""

    return [
        "--workspace",
        str(settings.workspace),
        "--runs-dir",
        str(settings.runs_dir),
        "--env-file",
        str(settings.root / "absent.env"),
    ]


@pytest.fixture
def session_id(settings: Settings, run: Callable[[Any], Any]) -> str:
    return run(run_goal(settings, GOAL)).session_id


def test_version_flag_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_check_prints_the_resolved_configuration(
    args: list[str], settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*args, "--check"]) == 0

    out = capsys.readouterr().out
    assert f"workspace -> {settings.workspace}" in out
    assert f"runs      -> {settings.runs_dir}" in out
    assert f"version   -> {__version__}" in out
    assert "planner" in out


def test_check_surfaces_configuration_warnings(
    args: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openai")

    assert main([*args, "--check"]) == 0

    out = capsys.readouterr().out
    assert "警告：" in out
    assert "缺少 API key" in out


def test_list_sessions_prints_a_cjk_aligned_table(
    args: list[str], session_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*args, "--list-sessions"]) == 0

    out = capsys.readouterr().out
    assert "会话" in out and "目标" in out
    assert session_id in out
    assert "done" in out


def test_list_sessions_accepts_a_limit(
    args: list[str], session_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*args, "--list-sessions", "1"]) == 0
    assert session_id in capsys.readouterr().out


def test_list_sessions_copes_with_an_empty_runs_dir(
    args: list[str], settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*args, "--list-sessions", "--runs-dir", str(settings.root / "nothing-here")]) == 0
    assert "(还没有会话记录)" in capsys.readouterr().out


def test_replay_accepts_a_session_id_prefix(
    args: list[str], session_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*args, "--replay", session_id[:8]]) == 0

    out = capsys.readouterr().out
    assert "回放结束" in out
    assert f"目录：{args[args.index('--runs-dir') + 1]}" in out
    assert "tester" in out


def test_replay_reports_unknown_sessions(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main([*args, "--replay", "does-not-exist"]) == 1
    assert "没有匹配的会话" in capsys.readouterr().out


def test_export_writes_a_single_markdown_file(
    args: list[str], session_id: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "handover.md"

    assert main([*args, "--export", session_id, "--export-to", str(target)]) == 0

    assert target.read_text(encoding="utf-8").startswith("# Agent team session")
    assert "已导出" in capsys.readouterr().out


def test_export_defaults_to_an_exports_directory(
    args: list[str], session_id: str, settings: Settings
) -> None:
    assert main([*args, "--export", session_id]) == 0

    exported = settings.runs_dir.parent / "exports" / session_id
    assert (exported / "transcript.json").is_file()
    assert (exported / "session.log").is_file()


def test_serve_flag_delegates_to_the_web_app(
    args: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentteam import web

    captured: dict[str, Any] = {}
    monkeypatch.setattr(web, "serve", lambda settings, **kwargs: captured.update(kwargs))

    assert main([*args, "--serve", "--host", "0.0.0.0", "--port", "9000"]) == 0

    assert captured == {"host": "0.0.0.0", "port": 9000, "open_browser": False}
    assert "9000" in capsys.readouterr().out


def test_invalid_environment_is_reported_not_raised(
    args: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENT_BUS", "kafka")

    assert main([*args, "--list-sessions"]) == 2
    assert "AGENT_BUS" in capsys.readouterr().out


def test_the_default_goal_runs_offline(
    args: list[str], settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*args, "--quiet"]) == 0

    assert (settings.workspace / "hello.py").is_file()
    out = capsys.readouterr().out
    assert "统计：" in out
    assert "会话结束" not in out  # --quiet keeps the conversation off stdout
