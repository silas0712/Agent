"""End-to-end tests: the whole team runs offline on the in-memory bus."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pytest

from agentteam.bus import InMemoryBus, create_bus
from agentteam.config import Settings
from agentteam.llm import BaseLLMClient, LLMError, LLMResponse, ScriptedLLM
from agentteam.llm.mock import mock_clients, planner_response
from agentteam.orchestrator import Orchestrator, build_team, new_session_id, render_markdown
from agentteam.schemas import MessageType, Role, make_message

GOAL = "创建一个 Python 模块 hello.py 与 pytest 测试"
PYTEST = 'python -m pytest -q -p no:cacheprovider'


class ExplodingLLM(BaseLLMClient):
    """Always fails, to test the error path of the orchestrator."""

    provider = "exploding"

    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        raise LLMError("no endpoint configured")


async def run_session(
    settings: Settings,
    goal: str = GOAL,
    *,
    strict: bool = False,
    max_rounds: int = 2,
    session_timeout: float = 90.0,
    planner_llm: BaseLLMClient | None = None,
    actor_llm: BaseLLMClient | None = None,
):
    bus = InMemoryBus()
    try:
        agents, _registry, workspace, _shell = build_team(settings, bus)
        if strict:
            agents[Role.REVIEWER].llm = mock_clients(strict_first_review=True)["reviewer"]
        if planner_llm is not None:
            agents[Role.PLANNER].llm = planner_llm
        if actor_llm is not None:
            agents[Role.ACTOR].llm = actor_llm
        orchestrator = Orchestrator(settings, bus, agents, max_rounds=max_rounds, session_timeout=session_timeout)
        result = await orchestrator.run(goal)
        return result, workspace
    finally:
        await bus.close()


def test_team_writes_real_code_and_passes_real_tests(settings: Settings, run: Callable[[Any], Any]) -> None:
    result, workspace = run(run_session(settings))

    assert result.status == "done", result.to_text()
    assert result.test["passed"] is True and result.test["skipped"] is False
    assert (workspace.root / "hello.py").is_file()
    assert (workspace.root / "test_hello.py").is_file()
    assert "def greet" in workspace.read_text("hello.py")

    types = [message.type.value for message in result.messages]
    assert types[0] == "task"
    for expected in ("plan", "act", "review", "test", "done"):
        assert expected in types, types
    assert types[-1] == "done"

    assert result.plan["steps"]
    assert result.review["approved"] is True
    assert result.run_dir is not None and result.run_dir.is_dir()
    assert (result.run_dir / "transcript.json").is_file()
    assert (result.run_dir / "transcript.md").is_file()
    assert GOAL in render_markdown(result)


def test_rejected_review_causes_a_real_rerun(settings: Settings, run: Callable[[Any], Any]) -> None:
    result, _workspace = run(run_session(settings, strict=True))

    assert result.status == "done", result.to_text()
    reviews = [message for message in result.messages if message.type is MessageType.REVIEW]
    assert [message.payload["approved"] for message in reviews] == [False, True]
    assert sum(1 for message in result.messages if message.type is MessageType.ACT) == 2
    assert any("缺" in str(issue) for issue in reviews[0].payload["issues"])


def test_failing_tests_are_rerun_until_the_budget_is_exhausted(settings: Settings, run: Callable[[Any], Any]) -> None:
    broken_plan = {**planner_response(), "test_commands": ['python -c "import sys; sys.exit(1)"']}

    result, _workspace = run(run_session(settings, max_rounds=1, planner_llm=ScriptedLLM([broken_plan])))

    assert result.status == "failed"
    assert result.test["passed"] is False
    assert "测试连续" in result.reason
    assert sum(1 for message in result.messages if message.type is MessageType.RERUN) == 1


def test_agent_errors_abort_the_session(settings: Settings, run: Callable[[Any], Any]) -> None:
    result, _workspace = run(run_session(settings, actor_llm=ExplodingLLM("boom")))

    assert result.status == "failed"
    assert "actor" in result.reason
    assert any(message.type is MessageType.ERROR for message in result.messages)
    assert result.messages[-1].payload["status"] == "failed"


def test_session_timeout_is_enforced(settings: Settings, run: Callable[[Any], Any]) -> None:
    result, _workspace = run(run_session(settings, session_timeout=0.0))

    assert result.status == "timeout"
    assert "超时" in result.reason


def test_rerun_carries_the_plan_and_the_test_output(settings: Settings, run: Callable[[Any], Any]) -> None:
    broken_plan = {**planner_response(), "test_commands": ['python -c "import sys; print(\'boom\'); sys.exit(2)"']}

    result, _workspace = run(run_session(settings, max_rounds=3, planner_llm=ScriptedLLM([broken_plan])))

    rerun = next(message for message in result.messages if message.type is MessageType.RERUN)
    assert rerun.sender is Role.ORCHESTRATOR
    assert rerun.recipient is Role.ACTOR
    assert rerun.payload["plan"]["goal"]
    assert "exit=2" in rerun.payload["feedback"]
    assert "boom" in rerun.payload["test_output"]


def test_build_team_wires_every_role(settings: Settings, run: Callable[[Any], Any]) -> None:
    bus = InMemoryBus()

    async def scenario():
        agents, registry, workspace, shell = build_team(settings, bus)
        await bus.close()
        return agents, registry, workspace, shell

    agents, registry, workspace, shell = run(scenario())

    assert set(agents) == {Role.PLANNER, Role.ACTOR, Role.REVIEWER, Role.TESTER}
    assert agents[Role.ACTOR].model_label == "scripted:mock-actor"
    assert workspace.root == settings.workspace
    assert shell.cwd == settings.workspace
    assert registry.get("file.write") is not None


def test_auto_bus_never_blocks_a_run(settings: Settings, run: Callable[[Any], Any]) -> None:
    settings.bus_backend = "auto"
    settings.redis_url = "redis://127.0.0.1:6399/0"

    async def scenario():
        bus = await create_bus(settings)
        backend = bus.backend
        await bus.publish(make_message(sender=Role.SYSTEM, recipient="broadcast", type=MessageType.LOG, payload={}))
        history = await bus.history()
        await bus.close()
        return backend, len(history)

    backend, count = run(scenario())
    assert backend in {"memory", "redis"}
    assert count >= 1


def test_new_session_id_is_sortable_and_unique() -> None:
    first, second = new_session_id(), new_session_id()

    assert first != second
    assert first[:8].isdigit() and "-" in first


def test_session_result_text_summarises_the_run(settings: Settings, run: Callable[[Any], Any]) -> None:
    result, _workspace = run(run_session(settings, goal="写一个 hello 模块"))
    text = result.to_text()

    assert result.ok is True
    assert "写一个 hello 模块" in text
    assert "测试：通过" in text
    assert "记录目录" in text


def test_invalid_goal_is_rejected(settings: Settings, run: Callable[[Any], Any]) -> None:
    async def scenario():
        bus = InMemoryBus()
        agents, _registry, _workspace, _shell = build_team(settings, bus)
        orchestrator = Orchestrator(settings, bus, agents, max_rounds=1, session_timeout=5)
        try:
            await orchestrator.run("   ")
        finally:
            await bus.close()

    with pytest.raises(ValueError):
        run(scenario())
