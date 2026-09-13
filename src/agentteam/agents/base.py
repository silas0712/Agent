"""Common behaviour for every agent: inbox loop, prompts, message helpers."""

from __future__ import annotations

import abc
import asyncio
import logging
from typing import Any

from ..bus import MessageBus, Subscription
from ..llm import BaseLLMClient, LLMError
from ..schemas import Message, MessageType, Role, make_message

logger = logging.getLogger(__name__)


class AgentError(RuntimeError):
    """Raised when an agent cannot do its job."""


class BaseAgent(abc.ABC):
    """An agent owns a role, an inbox subscription and (optionally) an LLM."""

    role: Role = Role.SYSTEM
    instructions: str = ""

    def __init__(
        self,
        *,
        bus: MessageBus,
        llm: BaseLLMClient | None = None,
        max_retries: int = 1,
    ) -> None:
        self.bus = bus
        self.llm = llm
        self.max_retries = max_retries
        self.processed = 0

    # -- identity ----------------------------------------------------------
    @property
    def name(self) -> str:
        return self.role.value

    @property
    def model_label(self) -> str:
        if self.llm is None:
            return "no-llm"
        return f"{self.llm.provider}:{self.llm.model}"

    @property
    def system_prompt(self) -> str:
        return self.instructions.strip()

    def can_handle(self, message: Message) -> bool:
        from ..bus import addressed_to

        return addressed_to(message, self.role) and message.sender != self.role

    # -- protocol ----------------------------------------------------------
    @abc.abstractmethod
    async def handle(self, message: Message) -> list[Message]:
        """React to *message* and return the messages to publish."""

    async def run(self, subscription: Subscription, *, stop: asyncio.Event | None = None) -> None:
        """Consume the inbox until the subscription is closed or *stop* is set."""

        try:
            while stop is None or not stop.is_set():
                try:
                    message = await subscription.get(timeout=0.25 if stop is not None else None)
                except asyncio.TimeoutError:
                    continue
                if message is None:
                    break
                if not self.can_handle(message):
                    continue
                self.processed += 1
                try:
                    replies = await self.handle(message)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - one bad message must not kill the team
                    logger.exception("%s crashed while handling %s", self.name, message.type.value)
                    replies = [self.error(f"{type(exc).__name__}: {exc}", round=message.round)]
                for reply in replies:
                    await self.bus.publish(reply)
        finally:
            subscription.close()

    # -- message helpers ---------------------------------------------------
    def reply(
        self,
        type: MessageType,
        *,
        payload: dict[str, Any] | None = None,
        recipient: Role | str = "broadcast",
        round: int = 0,
    ) -> Message:
        return make_message(sender=self.role, recipient=recipient, type=type, payload=payload, round=round)

    def log(self, text: str, *, round: int = 0, **extra: Any) -> Message:
        payload: dict[str, Any] = {"summary": text}
        payload.update(extra)
        return self.reply(MessageType.LOG, payload=payload, recipient="broadcast", round=round)

    def error(self, text: str, *, round: int = 0) -> Message:
        return self.reply(MessageType.ERROR, payload={"summary": text, "agent": self.name}, recipient=Role.ORCHESTRATOR, round=round)

    # -- streaming ---------------------------------------------------------
    async def emit(self, message: Message) -> None:
        """Publish a message *immediately* (progress feedback while working)."""

        await self.bus.publish(message)

    async def emit_log(self, text: str, *, round: int = 0, **extra: Any) -> None:
        """Publish a human readable progress line right away."""

        await self.emit(self.log(text, round=round, **extra))

    # -- LLM helpers -------------------------------------------------------
    async def complete_json(self, prompt: str, *, system_extra: str = "") -> Any:
        if self.llm is None:
            raise AgentError(f"{self.name} has no LLM configured")
        system = "\n\n".join(part for part in (self.system_prompt, system_extra.strip()) if part)
        try:
            return await self.llm.complete_json(prompt, system=system or None, retries=self.max_retries)
        except LLMError as exc:
            raise AgentError(f"{self.name} model call failed: {exc}") from exc
