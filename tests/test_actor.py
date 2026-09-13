"""Actor behaviour: real tool calls, iteration budget, rerun feedback."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentteam.agents import ActorAgent
from agentteam.bus import InMemoryBus
from agentteam.llm import ScriptedLLM
from agentteam.llm.mock import actor_response, planner_response
from agentteam.schemas import ActResult, MessageType, Role, TaskPlan, make_message
from agentteam.tools import ToolRegistry, Workspace


def _plan_message(plan: TaskPlan):
    return make_message(
        sender=Role.PLANNER,
        recipient=Role.ACTOR,
        type=MessageType.PLAN,
        payload={"plan": plan.model_dump(), "goal": plan.goal},
    )


def test_actor_calls_real_tools_and_reports_changes(
    run: Callable[[Any], Any], registry: ToolRegistry, workspace: Workspace
) -> None:
    async def scenario():
        bus = InMemoryBus()
        actor = ActorAgent(bus=bus, registry=registry, llm=ScriptedLLM([actor_response]), workspace=workspace)
        plan = TaskPlan.model_validate(planner_response())
        replies = await actor.handle(_plan_message(plan))
        logs = await bus.history()
        await bus.close()
        return replies, logs

    replies, logs = run(scenario())
    message = replies[0]
    act = ActResult.model_validate(message.payload["act"])

    assert message.type is MessageType.ACT
    assert message.recipient is Role.REVIEWER
    assert {item.path for item in act.changes} == {"hello.py", "test_hello.py"}
    assert workspace.exists("hello.py") and workspace.exists("test_hello.py")
    assert "def greet" in workspace.read_text("hello.py")
    assert act.steps_completed == [1, 2]
    assert act.iterations == 1
    assert sum(1 for item in logs if item.type is MessageType.LOG) >= 3


def test_actor_stops_after_the_iteration_budget(
    run: Callable[[Any], Any], registry: ToolRegistry, workspace: Workspace
) -> None:
    endless = {
        "actions": [{"tool": "file.write", "args": {"path": "loop.txt", "content": "again"}}],
        "done": False,
        "summary": "还没完成",
    }

    async def scenario():
        bus = InMemoryBus()
        actor = ActorAgent(bus=bus, registry=registry, llm=ScriptedLLM([endless]), workspace=workspace, max_steps=3)
        plan = TaskPlan.model_validate(planner_response())
        replies = await actor.handle(_plan_message(plan))
        await bus.close()
        return ActResult.model_validate(replies[0].payload["act"])

    act = run(scenario())
    assert act.iterations == 3
    assert "最大工具迭代次数" in act.summary


def test_actor_uses_the_feedback_of_a_rerun(
    run: Callable[[Any], Any], registry: ToolRegistry, workspace: Workspace
) -> None:
    seen: dict[str, str] = {}

    def second(messages):
        seen["prompt"] = messages[-1]["content"]
        return {
            "actions": [{"tool": "file.write", "args": {"path": "greet.py", "content": "value = 2\n"}}],
            "done": True,
            "summary": "已按反馈修复",
        }

    first = {
        "actions": [{"tool": "file.write", "args": {"path": "greet.py", "content": "value = 1\n"}}],
        "done": True,
        "summary": "第一版",
    }

    async def scenario():
        bus = InMemoryBus()
        actor = ActorAgent(bus=bus, registry=registry, llm=ScriptedLLM([first, second]), workspace=workspace)
        plan = TaskPlan.model_validate(planner_response())
        await actor.handle(_plan_message(plan))
        rerun = make_message(
            sender=Role.ORCHESTRATOR,
            recipient=Role.ACTOR,
            type=MessageType.RERUN,
            payload={"plan": plan.model_dump(), "feedback": "value 应该等于 2"},
            round=1,
        )
        replies = await actor.handle(rerun)
        await bus.close()
        return ActResult.model_validate(replies[0].payload["act"])

    act = run(scenario())
    assert "必须处理" in seen["prompt"]
    assert "value 应该等于 2" in seen["prompt"]
    assert workspace.read_text("greet.py") == "value = 2\n"
    assert act.summary == "已按反馈修复"


def test_actor_fixes_a_rejected_review(
    run: Callable[[Any], Any], registry: ToolRegistry, workspace: Workspace
) -> None:
    async def scenario():
        bus = InMemoryBus()
        actor = ActorAgent(bus=bus, registry=registry, llm=ScriptedLLM([actor_response]), workspace=workspace)
        plan = TaskPlan.model_validate(planner_response())
        rejected = make_message(
            sender=Role.REVIEWER,
            recipient="broadcast",
            type=MessageType.REVIEW,
            payload={"plan": plan.model_dump(), "approved": False, "issues": ["缺 __main__ 演示"]},
        )
        replies = await actor.handle(rejected)
        await bus.close()
        return replies

    assert run(scenario())[0].type is MessageType.ACT


def test_actor_reports_errors_to_the_orchestrator(
    run: Callable[[Any], Any], registry: ToolRegistry, workspace: Workspace
) -> None:
    async def scenario():
        bus = InMemoryBus()
        actor = ActorAgent(bus=bus, registry=registry, llm=ScriptedLLM(["not json"]), workspace=workspace)
        plan = TaskPlan.model_validate(planner_response())
        replies = await actor.handle(_plan_message(plan))
        await bus.close()
        return replies

    replies = run(scenario())
    assert replies[0].type is MessageType.ERROR
    assert replies[0].recipient is Role.ORCHESTRATOR
    assert "actor 无法开始工作" in replies[0].payload["summary"]


def test_actor_without_any_plan_asks_for_help(run: Callable[[Any], Any], registry: ToolRegistry) -> None:
    async def scenario():
        bus = InMemoryBus()
        actor = ActorAgent(bus=bus, registry=registry, llm=ScriptedLLM([actor_response]))
        message = make_message(sender=Role.ORCHESTRATOR, recipient=Role.ACTOR, type=MessageType.RERUN, payload={})
        replies = await actor.handle(message)
        await bus.close()
        return replies

    replies = run(scenario())
    assert replies[0].type is MessageType.ERROR
    assert "没有可用的计划" in replies[0].payload["summary"]
