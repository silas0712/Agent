"""Planner behaviour (offline, deterministic)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentteam.agents import PlannerAgent, fallback_plan
from agentteam.bus import InMemoryBus
from agentteam.llm import ScriptedLLM
from agentteam.llm.mock import planner_response
from agentteam.schemas import MessageType, Role, TaskPlan, make_message

GOAL = "实现 greet(name) 模块并写 pytest 测试"


def _task(goal: str = GOAL):
    return make_message(sender=Role.USER, recipient=Role.PLANNER, type=MessageType.TASK, payload={"goal": goal})


def test_planner_turns_a_goal_into_an_executable_plan(run: Callable[[Any], Any]) -> None:
    async def scenario():
        bus = InMemoryBus()
        planner = PlannerAgent(bus=bus, llm=ScriptedLLM([planner_response]))
        replies = await planner.handle(_task())
        history = await bus.history()
        await bus.close()
        return replies, history

    replies, history = run(scenario())

    assert len(replies) == 1
    assert replies[0].type is MessageType.PLAN
    assert replies[0].recipient is Role.ACTOR
    plan = TaskPlan.model_validate(replies[0].payload["plan"])
    assert plan.goal == GOAL
    assert [step.id for step in plan.steps] == [1, 2]
    assert plan.test_commands
    assert [message.type.value for message in history] == ["log"]
    assert "计划已生成" in history[0].payload["summary"]


def test_planner_ignores_messages_that_are_not_addressed_to_it(run: Callable[[Any], Any]) -> None:
    async def scenario():
        bus = InMemoryBus()
        planner = PlannerAgent(bus=bus, llm=ScriptedLLM([planner_response]))
        other = make_message(sender=Role.ACTOR, recipient=Role.REVIEWER, type=MessageType.ACT, payload={})
        await bus.close()
        return planner.can_handle(other)

    assert run(scenario()) is False


def test_planner_returns_an_error_reply_for_an_empty_goal(run: Callable[[Any], Any]) -> None:
    async def scenario():
        bus = InMemoryBus()
        planner = PlannerAgent(bus=bus, llm=ScriptedLLM([planner_response]))
        replies = await planner.handle(_task(""))
        await bus.close()
        return replies

    replies = run(scenario())
    assert replies[0].type is MessageType.ERROR
    assert replies[0].recipient is Role.ORCHESTRATOR


def test_planner_falls_back_when_the_model_is_unusable(run: Callable[[Any], Any]) -> None:
    async def scenario():
        bus = InMemoryBus()
        planner = PlannerAgent(bus=bus, llm=ScriptedLLM(["I refuse to answer in JSON"]))
        replies = await planner.handle(_task())
        await bus.close()
        return TaskPlan.model_validate(replies[0].payload["plan"])

    plan = run(scenario())
    assert plan.goal == GOAL
    assert len(plan.steps) == 1
    assert plan.test_commands


def test_fallback_plan_is_usable_without_a_model() -> None:
    plan = fallback_plan("写一个 notes.txt")

    assert plan.steps and plan.summary
    assert plan.test_commands == []
    assert "模型输出不可用" in fallback_plan("x", reason="boom").steps[0].detail


def test_planner_records_the_round_it_received() -> None:
    assert make_message(sender=Role.USER, recipient=Role.PLANNER, type=MessageType.TASK, payload={}, round=2).round == 2
