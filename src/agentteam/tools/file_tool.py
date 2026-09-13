"""Sandboxed file system access for the agents."""

from __future__ import annotations

from pathlib import Path

TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg",
    ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sh", ".ps1", ".sql", ".env",
}

DEFAULT_IGNORES = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules", "dist", "build", ".idea", ".vscode",
    "runs", "*.egg-info",
}


class WorkspaceError(RuntimeError):
    """Raised when a path escapes the workspace or is not usable."""


class Workspace:
    """A directory (and only that directory) the agents may read and write."""

    def __init__(self, root: Path | str, *, allow_write: bool = True, create: bool = True) -> None:
        self.root = Path(root).resolve()
        self.allow_write = allow_write
        if create:
            self.root.mkdir(parents=True, exist_ok=True)

    # -- paths -------------------------------------------------------------
    def resolve(self, relative: str | Path) -> Path:
        candidate = (self.root / str(relative)).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspaceError(f"path escapes the workspace: {relative!r}")
        return candidate

    def relative(self, path: Path | str) -> str:
        resolved = Path(path).resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError:
            return resolved.as_posix()

    def _require_write(self) -> None:
        if not self.allow_write:
            raise WorkspaceError("this workspace is read-only")

    # -- reading -----------------------------------------------------------
    def exists(self, relative: str | Path) -> bool:
        return self.resolve(relative).exists()

    def read_text(self, relative: str | Path, *, max_bytes: int = 400_000) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise WorkspaceError(f"file not found: {relative}")
        raw = path.read_bytes()[:max_bytes]
        return raw.decode("utf-8", errors="replace")

    def list_files(
        self,
        subdir: str | Path = "",
        *,
        pattern: str = "**/*",
        max_files: int = 300,
    ) -> list[str]:
        base = self.resolve(subdir)
        if not base.exists():
            return []
        found: list[str] = []
        for path in sorted(base.glob(pattern)):
            if not path.is_file() or self._ignored(path):
                continue
            found.append(self.relative(path))
            if len(found) >= max_files:
                break
        return found

    # -- writing -----------------------------------------------------------
    def write_text(self, relative: str | Path, content: str) -> tuple[Path, str]:
        """Write *content*, returning ``(path, "create" | "update")``."""

        self._require_write()
        path = self.resolve(relative)
        if path.exists() and path.is_dir():
            raise WorkspaceError(f"is a directory: {relative}")
        action = "update" if path.is_file() else "create"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return path, action

    def append_text(self, relative: str | Path, content: str) -> tuple[Path, str]:
        self._require_write()
        path = self.resolve(relative)
        action = "append" if path.is_file() else "create"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        return path, action

    def delete(self, relative: str | Path) -> Path:
        self._require_write()
        path = self.resolve(relative)
        if path == self.root:
            raise WorkspaceError("refusing to delete the workspace root")
        if path.is_file():
            path.unlink()
            return path
        if path.is_dir():
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            path.rmdir()
            return path
        raise WorkspaceError(f"nothing to delete: {relative}")

    # -- helpers -----------------------------------------------------------
    def is_text_file(self, path: Path) -> bool:
        if path.suffix.lower() in TEXT_SUFFIXES:
            return True
        if path.suffix:
            return False
        try:
            path.read_bytes()[:2048].decode("utf-8")
        except (UnicodeDecodeError, OSError):
            return False
        return True

    @staticmethod
    def _ignored(path: Path) -> bool:
        return any(part in DEFAULT_IGNORES for part in path.parts)
