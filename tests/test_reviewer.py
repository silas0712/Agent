"""Reviewer and tester: the two quality gates."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentteam.agents import ReviewerAgent
from agentteam.bus import InMemoryBus
from agentteam.llm import ScriptedLLM
from agentteam.llm.mock import APPROVE_REVIEW
from agentteam.schemas import (
    ActResult,
    FileChange,
    Message,
    MessageType,
    PlanStep,
    Role,
    TaskPlan,
    make_message,
)
from agentteam.tools import Workspace

PLAN = TaskPlan(
    goal="实现 greet",
    steps=[PlanStep(id=1, title="模块", detail="写 hello.py", files=["hello.py"])],
    acceptance=["pytest 通过"],
)


def _review_of(plan: TaskPlan, act: ActResult) -> Message:
    return make_message(
        sender=Role.REVIEWER,
        recipient="broadcast",
        type=MessageType.REVIEW,
        payload={
            "plan": plan.model_dump(),
            "act": act.model_dump(),
            "approved": True,
            "summary": "审查通过",
        },
    )


# -- reviewer -------------------------------------------------------------
def test_reviewer_rejects_when_planned_files_are_missing(run: Callable[[Any], Any], workspace: Workspace) -> None:
    async def scenario():
        bus = InMemoryBus()
        reviewer = ReviewerAgent(bus=bus, llm=ScriptedLLM([APPROVE_REVIEW]), workspace=workspace)
        act = ActResult(summary="done", changes=[FileChange(path="hello.py", action="create")])
        result = await reviewer.review(PLAN, act)
        await bus.close()
        return result

    result = run(scenario())
    assert result.approved is False
    assert any("hello.py" in issue for issue in result.issues)
    assert result.score <= 4


def test_reviewer_rejects_an_empty_round(run: Callable[[Any], Any], workspace: Workspace) -> None:
    async def scenario():
        bus = InMemoryBus()
        reviewer = ReviewerAgent(bus=bus, llm=ScriptedLLM([APPROVE_REVIEW]), workspace=workspace)
        result = await reviewer.review(TaskPlan(goal="g"), ActResult())
        await bus.close()
        return result

    result = run(scenario())
    assert result.approved is False
    assert any("没有" in issue for issue in result.issues)


def test_reviewer_approves_a_real_change(run: Callable[[Any], Any], workspace: Workspace) -> None:
    workspace.write_text("hello.py", "def greet(name):\n    return f'Hello, {name}!'\n")

    async def scenario():
        bus = InMemoryBus()
        reviewer = ReviewerAgent(bus=bus, llm=ScriptedLLM([APPROVE_REVIEW]), workspace=workspace)
        act = ActResult(summary="写了 hello.py", changes=[FileChange(path="hello.py", action="create")])
        replies = await reviewer.handle(_act_message(act))
        logs = await bus.history()
        await bus.close()
        return replies, logs

    replies, logs = run(scenario())
    assert replies[0].type is MessageType.REVIEW
    assert replies[0].payload["approved"] is True
    assert replies[0].recipient == "broadcast"
    assert str(logs[0].payload["summary"]).startswith("审查结论：通过")


def test_reviewer_flags_non_code_changes(run: Callable[[Any], Any], workspace: Workspace) -> None:
    workspace.write_text("notes.txt", "hello")

    async def scenario():
        bus = InMemoryBus()
        reviewer = ReviewerAgent(bus=bus, llm=ScriptedLLM([APPROVE_REVIEW]), workspace=workspace)
        act = ActResult(summary="写了笔记", changes=[FileChange(path="notes.txt", action="create")])
        result = await reviewer.review(TaskPlan(goal="g"), act)
        await bus.close()
        return result

    result = run(scenario())
    assert result.approved is False
    assert any("代码" in issue for issue in result.issues)


def test_reviewer_uses_its_fallback_when_the_model_fails(run: Callable[[Any], Any], workspace: Workspace) -> None:
    workspace.write_text("hello.py", "x = 1\n")

    async def scenario():
        bus = InMemoryBus()
        reviewer = ReviewerAgent(bus=bus, llm=ScriptedLLM(["not json"]), workspace=workspace)
        act = ActResult(summary="ok", changes=[FileChange(path="hello.py", action="create")])
        result = await reviewer.review(TaskPlan(goal="g"), act)
        await bus.close()
        return result

    result = run(scenario())
    assert result.approved is True
    assert result.suggestions


def test_reviewer_ignores_other_message_types(run: Callable[[Any], Any], workspace: Workspace) -> None:
    async def scenario():
        bus = InMemoryBus()
        reviewer = ReviewerAgent(bus=bus, llm=ScriptedLLM([APPROVE_REVIEW]), workspace=workspace)
        task = make_message(sender=Role.USER, recipient="broadcast", type=MessageType.TASK, payload={"goal": "g"})
        replies = await reviewer.handle(task)
        await bus.close()
        return replies

    assert run(scenario()) == []


def _act_message(act: ActResult) -> Message:
    return make_message(
        sender=Role.ACTOR,
        recipient=Role.REVIEWER,
        type=MessageType.ACT,
        payload={"plan": PLAN.model_dump(), "act": act.model_dump(), "summary": act.summary},
    )
