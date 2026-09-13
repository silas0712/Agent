"""Regex search over the workspace (poor man's ripgrep, zero dependencies)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .file_tool import Workspace

DEFAULT_IGNORES = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", "runs"}


@dataclass(frozen=True)
class SearchHit:
    path: str
    line: int
    text: str

    def to_text(self) -> str:
        return f"{self.path}:{self.line}: {self.text.strip()}"


class SearchTool:
    def __init__(self, workspace: Workspace, *, max_file_bytes: int = 400_000) -> None:
        self.workspace = workspace
        self.max_file_bytes = max_file_bytes

    def grep(
        self,
        pattern: str,
        *,
        include: str = "**/*",
        max_results: int = 50,
        ignore_case: bool = False,
    ) -> list[SearchHit]:
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc

        hits: list[SearchHit] = []
        for relative in self.workspace.list_files(pattern=include, max_files=2000):
            path = self.workspace.resolve(relative)
            if not self.workspace.is_text_file(path):
                continue
            if _under_ignored_dir(path):
                continue
            try:
                content = self.workspace.read_text(relative, max_bytes=self.max_file_bytes)
            except OSError:
                continue
            for number, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    hits.append(SearchHit(path=relative, line=number, text=line[:200]))
                    if len(hits) >= max_results:
                        return hits
        return hits

    def to_text(self, hits: list[SearchHit], *, empty: str = "no matches") -> str:
        return "\n".join(hit.to_text() for hit in hits) if hits else empty


def _under_ignored_dir(path: Path) -> bool:
    return any(part in DEFAULT_IGNORES for part in path.parts)
