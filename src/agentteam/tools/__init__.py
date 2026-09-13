"""Tool layer: everything an agent can *do* to the real world.

All file access is confined to a :class:`~agentteam.tools.file_tool.Workspace`
so an agent can never touch anything outside the project directory.
"""

from __future__ import annotations

from .file_tool import Workspace, WorkspaceError
from .registry import ToolRegistry, ToolResult, ToolSpec
from .search_tool import SearchHit, SearchTool
from .shell_tool import ShellResult, ShellTool

__all__ = [
    "SearchHit",
    "SearchTool",
    "ShellResult",
    "ShellTool",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "Workspace",
    "WorkspaceError",
]
