"""Tester: runs the real test commands and reports the truth."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentteam.agents import TesterAgent
from agentteam.bus import InMemoryBus
from agentteam.llm import ScriptedLLM
from agentteam.llm.mock import TESTER_SUMMARY
from agentteam.schemas import MessageType, Role, TaskPlan, TestResult, make_message
from agentteam.tools import ShellTool, Workspace
from agentteam.tools.shell_tool import use_venv_python

PASSING_TEST = "def test_ok():\n    assert 1 + 1 == 2\n"
FAILING_TEST = "def test_nope():\n    assert 1 + 1 == 3\n"
PYTEST = use_venv_python("python -m pytest -q -p no:cacheprovider")


def _approved_review(plan: TaskPlan):
    return make_message(
        sender=Role.REVIEWER,
        recipient="broadcast",
        type=MessageType.REVIEW,
        payload={"plan": plan.model_dump(), "approved": True, "summary": "审查通过"},
    )


def test_tester_runs_the_planned_command(
    run: Callable[[Any], Any], workspace: Workspace, shell: ShellTool
) -> None:
    workspace.write_text("test_ok.py", PASSING_TEST)
    plan = TaskPlan(goal="g", test_commands=[f"{PYTEST} test_ok.py"])

    async def scenario():
        bus = InMemoryBus()
        tester = TesterAgent(bus=bus, shell=shell, llm=ScriptedLLM([TESTER_SUMMARY]), workspace=workspace)
        replies = await tester.handle(_approved_review(plan))
        await bus.close()
        return replies

    reply = run(scenario())[0]
    result = TestResult.model_validate(reply.payload["test"])
    assert reply.type is MessageType.TEST
    assert reply.recipient is Role.ORCHESTRATOR
    assert result.passed is True and not result.failures
    assert "退出码" in result.summary


def test_tester_reports_failures_with_exit_codes(
    run: Callable[[Any], Any], workspace: Workspace, shell: ShellTool
) -> None:
    workspace.write_text("test_nope.py", FAILING_TEST)
    plan = TaskPlan(goal="g", test_commands=[f"{PYTEST} test_nope.py"])

    async def scenario():
        bus = InMemoryBus()
        tester = TesterAgent(bus=bus, shell=shell, workspace=workspace)  # no LLM on purpose
        replies = await tester.handle(_approved_review(plan))
        await bus.close()
        return replies

    result = TestResult.model_validate(run(scenario())[0].payload["test"])
    assert result.passed is False
    assert result.failures and "exit=1" in result.failures[0]
    assert "验证失败" in result.summary
    assert "test_nope" in result.output


def test_tester_skips_when_there_is_nothing_to_run(
    run: Callable[[Any], Any], workspace: Workspace, shell: ShellTool
) -> None:
    async def scenario():
        bus = InMemoryBus()
        tester = TesterAgent(bus=bus, shell=shell, workspace=workspace)
        replies = await tester.handle(_approved_review(TaskPlan(goal="写说明文档")))
        await bus.close()
        return replies

    result = TestResult.model_validate(run(scenario())[0].payload["test"])
    assert result.skipped is True
    assert result.passed is True


def test_tester_detects_pytest_on_its_own(
    run: Callable[[Any], Any], workspace: Workspace, shell: ShellTool
) -> None:
    workspace.write_text("test_detected.py", PASSING_TEST)

    async def scenario():
        bus = InMemoryBus()
        tester = TesterAgent(bus=bus, shell=shell, workspace=workspace)
        return tester.commands_for(TaskPlan(goal="g"))

    commands = run(scenario())
    assert commands == [PYTEST]


def test_tester_ignores_a_rejected_review(run: Callable[[Any], Any], workspace: Workspace, shell: ShellTool) -> None:
    async def scenario():
        bus = InMemoryBus()
        tester = TesterAgent(bus=bus, shell=shell, workspace=workspace)
        rejected = make_message(
            sender=Role.REVIEWER,
            recipient="broadcast",
            type=MessageType.REVIEW,
            payload={"plan": TaskPlan(goal="g").model_dump(), "approved": False},
        )
        replies = await tester.handle(rejected)
        await bus.close()
        return replies

    assert run(scenario()) == []


def test_tester_deduplicates_commands(run: Callable[[Any], Any], workspace: Workspace, shell: ShellTool) -> None:
    plan = TaskPlan(goal="g", test_commands=[PYTEST, PYTEST, "   "])

    async def scenario():
        return TesterAgent(bus=InMemoryBus(), shell=shell, workspace=workspace).commands_for(plan)

    assert run(scenario()) == [PYTEST]
