"""Run shell commands for the agents (tests, linters, builds...)."""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_PYTHON_PREFIXES = ("python ", "python3 ", "python.exe ", "python3.exe ", "py ")


def use_venv_python(command: str) -> str:
    """Rewrite a leading ``python`` to the interpreter running the agent.

    Keeps ``python -m pytest`` working even when the venv is not activated.
    """

    for prefix in _PYTHON_PREFIXES:
        if command.startswith(prefix):
            return f'"{sys.executable}" ' + command[len(prefix) :]
    return command


@dataclass(frozen=True)
class ShellResult:
    command: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def to_text(self, limit: int = 4000) -> str:
        parts = [f"$ {self.command}", f"exit={self.returncode} ({self.duration:.2f}s)"]
        if self.timed_out:
            parts.append("TIMEOUT")
        if self.stdout.strip():
            parts.append("stdout:\n" + self.stdout.strip())
        if self.stderr.strip():
            parts.append("stderr:\n" + self.stderr.strip())
        text = "\n".join(parts)
        return text if len(text) <= limit else text[: limit - 3] + "..."


class ShellTool:
    """Thin, time-bounded wrapper around ``subprocess``."""

    def __init__(self, cwd: Path | str, *, timeout: float = 120.0, use_venv_python: bool = True) -> None:
        self.cwd = Path(cwd).resolve()
        self.timeout = timeout
        self.rewrite_python = use_venv_python

    def run(self, command: str, *, timeout: float | None = None, cwd: Path | str | None = None) -> ShellResult:
        command = command.strip()
        if not command:
            return ShellResult(command=command, returncode=2, stderr="empty command")
        effective = use_venv_python(command) if self.rewrite_python else command
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                effective,
                shell=True,
                cwd=str(cwd or self.cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return ShellResult(
                command=command,
                returncode=-1,
                stdout=_as_text(exc.stdout),
                stderr=f"command timed out after {timeout or self.timeout}s",
                duration=time.perf_counter() - started,
                timed_out=True,
            )
        except OSError as exc:
            return ShellResult(
                command=command,
                returncode=-2,
                stderr=f"cannot start command: {exc}",
                duration=time.perf_counter() - started,
            )
        return ShellResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration=time.perf_counter() - started,
        )


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
