"""Pydantic models shared by every agent, the bus and the orchestrator."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Role(str, Enum):
    """Who is speaking / who owns a message."""

    USER = "user"
    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    ACTOR = "actor"
    REVIEWER = "reviewer"
    TESTER = "tester"
    SYSTEM = "system"


class MessageType(str, Enum):
    """Protocol between the agents (see README for the full state machine)."""

    TASK = "task"        # user            -> planner
    PLAN = "plan"        # planner         -> actor
    ACT = "act"          # actor           -> reviewer
    REVIEW = "review"    # reviewer        -> tester (approved) | actor (rejected)
    TEST = "test"        # tester          -> orchestrator
    RERUN = "rerun"      # orchestrator    -> actor (new round, with feedback)
    DONE = "done"        # orchestrator    -> broadcast
    LOG = "log"          # any             -> broadcast
    ERROR = "error"      # any             -> orchestrator


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Message(BaseModel):
    """A single event travelling over the message bus."""

    id: str = Field(default_factory=_new_id)
    sender: Role
    recipient: Role | Literal["broadcast"] = "broadcast"
    type: MessageType
    payload: dict[str, Any] = Field(default_factory=dict)
    round: int = 0
    created_at: float = Field(default_factory=time.time)

    def to_envelope(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from": self.sender.value,
            "to": self.recipient if isinstance(self.recipient, str) else self.recipient.value,
            "type": self.type.value,
            "round": self.round,
            "payload": self.payload,
            "created_at": self.created_at,
        }

    def summary(self, limit: int = 160) -> str:
        text = str(self.payload.get("summary") or self.payload.get("goal") or "")
        if not text:
            keys = ",".join(sorted(self.payload)) or "-"
            text = f"({keys})"
        text = " ".join(text.split())
        return text[: limit - 1] + "…" if len(text) > limit else text


class PlanStep(BaseModel):
    id: int
    title: str
    detail: str = ""
    files: list[str] = Field(default_factory=list)


class TaskPlan(BaseModel):
    """Output of the planner."""

    goal: str
    summary: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)


class FileChange(BaseModel):
    path: str
    action: Literal["create", "update", "delete", "append"] = "create"
    summary: str = ""


class ActResult(BaseModel):
    """Output of the actor (one round of real work)."""

    summary: str = ""
    changes: list[FileChange] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    steps_completed: list[int] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    iterations: int = 0


class ReviewResult(BaseModel):
    """Output of the reviewer."""

    approved: bool = False
    score: int = 0
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class TestResult(BaseModel):
    """Output of the tester."""

    passed: bool = False
    skipped: bool = False
    commands: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    output: str = ""
    summary: str = ""


def recipient_label(message: Message) -> str:
    """``Role`` is a ``str`` subclass, so ``str()`` would print ``Role.X``."""

    recipient = message.recipient
    return recipient.value if isinstance(recipient, Role) else str(recipient)


def make_message(
    *,
    sender: Role,
    recipient: Role | Literal["broadcast"],
    type: MessageType,
    payload: dict[str, Any] | None = None,
    round: int = 0,
) -> Message:
    """Small convenience helper used by every agent."""

    return Message(sender=sender, recipient=recipient, type=type, payload=payload or {}, round=round)
