"""Tool registry: what the agents may call, described for the prompt."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .file_tool import Workspace, WorkspaceError
from .search_tool import SearchTool
from .shell_tool import ShellTool

logger = logging.getLogger(__name__)

Handler = Callable[..., "ToolResult | str"]


@dataclass(frozen=True)
class ToolResult:
    tool: str
    ok: bool
    output: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_text(self, limit: int = 3000) -> str:
        head = f"[{self.tool}] {'ok' if self.ok else 'FAILED'}\n"
        if limit <= len(head):
            return head[:limit]
        budget = limit - len(head) - 3
        text = self.output if len(self.output) <= budget else self.output[:budget] + "..."
        return head + text


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: Mapping[str, str]
    handler: Handler

    def signature(self) -> str:
        return ", ".join(f"{key}: {value}" for key, value in self.args.items()) or "no arguments"

    def to_prompt_line(self) -> str:
        return f"- {self.name}({self.signature()}): {self.description}"


class ToolRegistry:
    """Name -> handler mapping with argument validation and error capture."""

    def __init__(self, tools: Iterable[ToolSpec] = ()) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def describe(self) -> str:
        return "\n".join(self._tools[name].to_prompt_line() for name in sorted(self._tools))

    def execute(self, name: str, args: Mapping[str, Any] | None = None) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(tool=name, ok=False, output=f"unknown tool {name!r}; available: {', '.join(self.names)}")
        try:
            outcome = spec.handler(**dict(args or {}))
        except WorkspaceError as exc:
            return ToolResult(tool=name, ok=False, output=f"workspace error: {exc}")
        except TypeError as exc:
            return ToolResult(tool=name, ok=False, output=f"bad arguments for {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a tool must never kill the agent
            logger.debug("tool %s crashed", name, exc_info=True)
            return ToolResult(tool=name, ok=False, output=f"{type(exc).__name__}: {exc}")
        if isinstance(outcome, ToolResult):
            return outcome
        return ToolResult(tool=name, ok=True, output=str(outcome))

    def execute_action(self, action: Mapping[str, Any]) -> ToolResult:
        """Execute ``{"tool": ..., "args": {...}}`` as produced by the model."""

        name = str(action.get("tool") or action.get("name") or "").strip()
        raw_args = action.get("args") or action.get("arguments") or {}
        if not name:
            return ToolResult(tool="<none>", ok=False, output="action without a 'tool' field")
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError:
                return ToolResult(tool=name, ok=False, output=f"args is not valid JSON: {raw_args[:200]}")
        if not isinstance(raw_args, Mapping):
            return ToolResult(tool=name, ok=False, output=f"args must be an object, got {type(raw_args).__name__}")
        return self.execute(name, raw_args)


def build_default_registry(
    workspace: Workspace,
    shell: ShellTool,
    search: SearchTool | None = None,
) -> ToolRegistry:
    """The standard tool set: files, shell and search."""

    search = search or SearchTool(workspace)

    def file_write(path: str, content: str) -> ToolResult:
        written, action = workspace.write_text(path, content)
        return ToolResult(
            tool="file.write",
            ok=True,
            output=f"{action}d {workspace.relative(written)} ({len(content)} chars)",
            meta={"path": workspace.relative(written), "action": action},
        )

    def file_append(path: str, content: str) -> ToolResult:
        written, action = workspace.append_text(path, content)
        return ToolResult(
            tool="file.append",
            ok=True,
            output=f"{action}ed {workspace.relative(written)} (+{len(content)} chars)",
            meta={"path": workspace.relative(written), "action": action},
        )

    def file_read(path: str, max_lines: int = 200) -> ToolResult:
        text = workspace.read_text(path)
        lines = text.splitlines()
        shown = "\n".join(lines[:max_lines])
        suffix = f"\n... ({len(lines) - max_lines} more lines)" if len(lines) > max_lines else ""
        return ToolResult(tool="file.read", ok=True, output=shown + suffix, meta={"path": path})

    def file_list(subdir: str = "", pattern: str = "**/*") -> ToolResult:
        files = workspace.list_files(subdir, pattern=pattern)
        return ToolResult(tool="file.list", ok=True, output="\n".join(files) or "(empty)")

    def file_delete(path: str) -> ToolResult:
        removed = workspace.delete(path)
        return ToolResult(
            tool="file.delete",
            ok=True,
            output=f"deleted {workspace.relative(removed)}",
            meta={"path": workspace.relative(removed), "action": "delete"},
        )

    def shell_run(command: str, timeout: float | None = None) -> ToolResult:
        outcome = shell.run(command, timeout=timeout)
        return ToolResult(
            tool="shell.run",
            ok=outcome.ok,
            output=outcome.to_text(),
            meta={"command": command, "returncode": outcome.returncode},
        )

    def search_grep(pattern: str, include: str = "**/*", max_results: int = 50) -> ToolResult:
        hits = search.grep(pattern, include=include, max_results=max_results)
        return ToolResult(tool="search.grep", ok=True, output=search.to_text(hits))

    return ToolRegistry(
        [
            ToolSpec(
                "file.read",
                "read a text file inside the workspace",
                {"path": "relative file path", "max_lines": "int, optional"},
                file_read,
            ),
            ToolSpec(
                "file.write",
                "create or overwrite a file (full content)",
                {"path": "relative file path", "content": "complete file content"},
                file_write,
            ),
            ToolSpec(
                "file.append",
                "append text to a file (created if missing)",
                {"path": "relative file path", "content": "text to append"},
                file_append,
            ),
            ToolSpec(
                "file.list",
                "list files in the workspace",
                {"subdir": "optional sub directory", "pattern": "glob"},
                file_list,
            ),
            ToolSpec("file.delete", "delete a file or directory", {"path": "relative path"}, file_delete),
            ToolSpec(
                "shell.run",
                "run a shell command inside the workspace",
                {"command": "command line", "timeout": "seconds, optional"},
                shell_run,
            ),
            ToolSpec(
                "search.grep",
                "regex search over workspace files",
                {"pattern": "regex", "include": "glob filter", "max_results": "int"},
                search_grep,
            ),
        ]
    )
