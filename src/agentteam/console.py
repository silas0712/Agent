"""Real-time console rendering of the agent conversation."""

from __future__ import annotations

import sys
from typing import Any, TextIO

from .schemas import Message, MessageType, Role, recipient_label

ROLE_STYLE: dict[str, tuple[str, str]] = {
    Role.USER.value: ("👤", "bold white"),
    Role.ORCHESTRATOR.value: ("🎼", "bold magenta"),
    Role.PLANNER.value: ("🧭", "bold cyan"),
    Role.ACTOR.value: ("🛠", "bold green"),
    Role.REVIEWER.value: ("🔍", "bold yellow"),
    Role.TESTER.value: ("🧪", "bold blue"),
    Role.SYSTEM.value: ("⚙", "dim"),
}

TYPE_STYLE: dict[str, str] = {
    MessageType.ERROR.value: "bold red",
    MessageType.DONE.value: "bold green",
    MessageType.LOG.value: "dim",
}


class ConsolePrinter:
    """Prints every bus message as it happens (rich when available)."""

    def __init__(self, *, stream: TextIO | None = None, quiet: bool = False) -> None:
        self.stream = stream or sys.stdout
        self.quiet = quiet
        self._console: Any = None
        if not quiet:
            try:
                from rich.console import Console

                self._console = Console(file=self.stream, highlight=False, soft_wrap=True)
            except ImportError:  # pragma: no cover - rich is an optional dependency
                self._console = None

    # -- generic helpers ---------------------------------------------------
    def title(self, text: str) -> None:
        if self.quiet:
            return
        if self._console is not None:
            self._console.rule(f"[bold]{text}")
        else:
            print(f"=== {text} ===", file=self.stream)

    def info(self, text: str) -> None:
        if self.quiet:
            return
        if self._console is not None:
            self._console.print(f"[dim]{text}")
        else:
            print(text, file=self.stream)

    def error(self, text: str) -> None:
        if self._console is not None:
            self._console.print(f"[bold red]{text}")
        else:
            print(f"ERROR: {text}", file=self.stream)

    def __call__(self, message: Message) -> None:
        self.print(message)

    def print(self, message: Message) -> None:
        if self.quiet:
            return
        icon, role_style = ROLE_STYLE.get(message.sender.value, ("•", "white"))
        if message.sender == Role.ORCHESTRATOR and message.type == MessageType.DONE:
            role_style = "bold green"
        type_style = TYPE_STYLE.get(message.type.value, "white")
        head = f"{icon} {message.sender.value} → {recipient_label(message)}"
        text = message.summary(220)
        if self._console is not None:
            self._console.print(
                f"[{role_style}]{head}[/] [dim]({message.type.value} · round {message.round})[/] [{type_style}]{text}[/]"
            )
        else:
            print(f"{head} ({message.type.value} · round {message.round}) {text}", file=self.stream)
