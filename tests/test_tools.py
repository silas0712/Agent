"""Tool layer tests: sandboxed files, shell, search and the registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteam.tools import SearchTool, ShellTool, ToolRegistry, ToolSpec, ToolResult, Workspace, WorkspaceError
from agentteam.tools.shell_tool import use_venv_python


# -- workspace ------------------------------------------------------------
def test_write_read_append_and_list(workspace: Workspace) -> None:
    path, action = workspace.write_text("src/app.py", "x = 1\n")
    assert action == "create"
    assert path.read_text(encoding="utf-8") == "x = 1\n"

    _, action = workspace.write_text("src/app.py", "x = 2\n")
    assert action == "update"
    workspace.append_text("src/app.py", "y = 3\n")

    assert workspace.read_text("src/app.py") == "x = 2\ny = 3\n"
    assert workspace.list_files() == ["src/app.py"]
    assert workspace.exists("src/app.py")
    workspace.delete("src/app.py")
    assert not workspace.exists("src/app.py")


def test_sandbox_blocks_escapes(workspace: Workspace, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(WorkspaceError):
        workspace.write_text("../outside/evil.txt", "nope")
    with pytest.raises(WorkspaceError):
        workspace.resolve(outside / "evil.txt")
    with pytest.raises(WorkspaceError):
        workspace.read_text("missing.txt")


def test_read_only_workspace(tmp_path: Path) -> None:
    read_only = Workspace(tmp_path / "ro", allow_write=False)
    with pytest.raises(WorkspaceError, match="read-only"):
        read_only.write_text("a.txt", "x")


def test_delete_refuses_the_root(workspace: Workspace) -> None:
    with pytest.raises(WorkspaceError, match="root"):
        workspace.delete(".")


def test_is_text_file_ignores_binaries(workspace: Workspace) -> None:
    path, _ = workspace.write_text("data.bin", "\x00\x01binary")
    assert workspace.is_text_file(workspace.resolve("data.bin")) is False
    _, _ = workspace.write_text("main.py", "print()")
    assert workspace.is_text_file(workspace.resolve("main.py")) is True


# -- shell ----------------------------------------------------------------
def test_shell_runs_with_the_current_interpreter(shell: ShellTool) -> None:
    result = shell.run('python -c "print(\'hello\')"')

    assert result.ok, result.to_text()
    assert "hello" in result.stdout
    assert use_venv_python("python -m pytest -q") != "python -m pytest -q"


def test_shell_reports_failures_and_timeouts(shell: ShellTool) -> None:
    failed = shell.run('python -c "import sys; sys.exit(3)"')
    assert failed.ok is False
    assert failed.returncode == 3

    timed_out = shell.run('python -c "import time; time.sleep(5)"', timeout=0.4)
    assert timed_out.timed_out is True
    assert "TIMEOUT" in timed_out.to_text()

    assert shell.run("   ").ok is False


# -- search ---------------------------------------------------------------
def test_search_finds_matches_and_skips_ignored_dirs(workspace: Workspace) -> None:
    workspace.write_text("pkg/mod.py", "def greet(name):\n    return name\n")
    workspace.write_text(".venv/lib.py", "def greet(name):\n    return name\n")

    hits = SearchTool(workspace).grep(r"def greet", include="**/*.py")

    assert [hit.path for hit in hits] == ["pkg/mod.py"]
    assert hits[0].line == 1
    with pytest.raises(ValueError):
        SearchTool(workspace).grep("(")


# -- registry -------------------------------------------------------------
def test_registry_exposes_and_executes_tools(registry: ToolRegistry) -> None:
    assert "file.write" in registry
    assert "shell.run" in registry.names
    assert "file.write" in registry.describe()

    created = registry.execute("file.write", {"path": "a.txt", "content": "hi"})
    assert created.ok and created.meta["action"] == "create"
    assert registry.execute("file.read", {"path": "a.txt"}).output.strip() == "hi"
    assert registry.execute("file.delete", {"path": "a.txt"}).ok


def test_registry_captures_errors_instead_of_raising(registry: ToolRegistry) -> None:
    unknown = registry.execute("nope.run", {})
    assert unknown.ok is False and "unknown tool" in unknown.output

    bad_args = registry.execute("file.write", {"path": "b.txt"})
    assert bad_args.ok is False and "bad arguments" in bad_args.output

    escape = registry.execute("file.write", {"path": "../escape.txt", "content": "x"})
    assert escape.ok is False and "workspace error" in escape.output

    def boom() -> str:
        raise RuntimeError("kaboom")

    registry.register(ToolSpec("boom", "always fails", {}, boom))
    crashed = registry.execute("boom")
    assert crashed.ok is False and "kaboom" in crashed.output
    assert crashed.to_text().startswith("[boom] FAILED")


def test_execute_action_accepts_model_shaped_payloads(registry: ToolRegistry) -> None:
    assert registry.execute_action({"tool": "file.write", "args": {"path": "c.txt", "content": "1"}}).ok
    assert registry.execute_action({"name": "file.read", "arguments": '{"path": "c.txt"}'}).ok
    assert registry.execute_action({"tool": "file.read", "args": "not-json"}).ok is False
    assert registry.execute_action({"args": {}}).ok is False
    assert registry.execute_action({"tool": "file.read", "args": ["c.txt"]}).ok is False


def test_tool_result_text_is_bounded(registry: ToolRegistry) -> None:
    result = ToolResult(tool="file.read", ok=True, output="x" * 5000)
    assert len(result.to_text(limit=100)) <= 100
