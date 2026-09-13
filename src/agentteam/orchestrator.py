"""The conductor: wires the agents, routes the rounds and bounds the loops."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .agents import ActorAgent, BaseAgent, PlannerAgent, ReviewerAgent, TesterAgent
from .bus import MessageBus
from .config import Settings
from .llm.factory import build_clients
from .schemas import Message, MessageType, Role, TestResult, make_message, recipient_label
from .tools.file_tool import Workspace
from .tools.registry import ToolRegistry, build_default_registry
from .tools.search_tool import SearchTool
from .tools.shell_tool import ShellTool

logger = logging.getLogger(__name__)


@dataclass
class SessionResult:
    session_id: str
    goal: str
    status: str  # done | failed | timeout
    rounds: int = 0
    reason: str = ""
    plan: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    test: dict[str, Any] | None = None
    messages: list[Message] = field(default_factory=list)
    run_dir: Path | None = None
    duration: float = 0.0
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "done"

    def stats_line(self) -> str:
        """One human readable line summarising the run counters."""

        return (
            f"模型调用 {self.stats.get('llm_calls', 0)} 次 · "
            f"工具调用 {self.stats.get('tool_calls', 0)} 次 · "
            f"tokens {self.stats.get('tokens', 0)} · "
            f"审查退回 {self.stats.get('rejections', 0)} 次 · "
            f"测试失败 {self.stats.get('test_failures', 0)} 次"
        )

    def to_text(self) -> str:
        lines = [
            f"会话 {self.session_id}: {self.status.upper()}（{self.duration:.1f}s，{self.rounds} 轮，{len(self.messages)} 条消息）",
            f"目标：{self.goal}",
            f"结论：{self.reason or '(无)'}",
            f"统计：{self.stats_line()}",
        ]
        if self.plan:
            steps = self.plan.get("steps") or []
            lines.append("计划：" + " / ".join(str(step.get("title")) for step in steps))
        if self.test:
            lines.append(f"测试：{'通过' if self.test.get('passed') else '失败'}")
            for failure in self.test.get("failures") or []:
                lines.append(f"  - {failure}")
        if self.run_dir:
            lines.append(f"记录目录：{self.run_dir}")
        return "\n".join(lines)


def build_team(
    settings: Settings,
    bus: MessageBus,
) -> tuple[dict[Role, BaseAgent], ToolRegistry, Workspace, ShellTool]:
    """Wire workspace + tools + one LLM client per agent."""

    settings.ensure_dirs()
    workspace = Workspace(settings.workspace)
    shell = ShellTool(workspace.root, timeout=settings.shell_timeout)
    registry = build_default_registry(workspace, shell, SearchTool(workspace))
    clients = build_clients(settings)

    agents: dict[Role, BaseAgent] = {
        Role.PLANNER: PlannerAgent(bus=bus, llm=clients["planner"]),
        Role.ACTOR: ActorAgent(
            bus=bus,
            registry=registry,
            llm=clients["actor"],
            workspace=workspace,
            max_steps=settings.max_tool_steps,
        ),
        Role.REVIEWER: ReviewerAgent(bus=bus, llm=clients["reviewer"], workspace=workspace),
        Role.TESTER: TesterAgent(bus=bus, shell=shell, llm=clients["tester"], workspace=workspace),
    }
    return agents, registry, workspace, shell


class Orchestrator:
    """Runs the planner -> actor -> reviewer -> tester loop until it converges."""

    def __init__(
        self,
        settings: Settings,
        bus: MessageBus,
        agents: dict[Role, BaseAgent],
        *,
        printer: Any = None,
        max_rounds: int | None = None,
        session_timeout: float | None = None,
    ) -> None:
        self.settings = settings
        self.bus = bus
        self.agents = agents
        self.printer = printer
        self.max_rounds = max_rounds if max_rounds is not None else settings.max_rounds
        self.session_timeout = session_timeout if session_timeout is not None else settings.session_timeout
        self.messages: list[Message] = []

    # -- public entry point ------------------------------------------------
    async def run(self, goal: str, *, session_id: str | None = None) -> SessionResult:
        goal = goal.strip()
        if not goal:
            raise ValueError("goal must not be empty")

        session_id = session_id or new_session_id()
        run_dir = self.settings.runs_dir / session_id
        run_dir.mkdir(parents=True, exist_ok=True)

        self.messages = []
        rounds = rejections = test_failures = 0
        status, reason = "failed", "会话在完成前结束"
        plan_payload: dict[str, Any] | None = None
        review_payload: dict[str, Any] | None = None
        test_payload: dict[str, Any] | None = None
        started = time.perf_counter()
        started_wall = time.time()
        deadline = time.monotonic() + self.session_timeout

        inboxes = {role: self.bus.inbox(role) for role in self.agents}
        firehose = self.bus.firehose()
        tasks = [
            asyncio.create_task(agent.run(inboxes[role]), name=f"agent-{role.value}")
            for role, agent in self.agents.items()
        ]
        await asyncio.sleep(0)  # let every agent subscribe before the first message

        try:
            await self.bus.publish(
                make_message(
                    sender=Role.USER,
                    recipient=Role.PLANNER,
                    type=MessageType.TASK,
                    payload={"goal": goal},
                    round=0,
                )
            )
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status, reason = "timeout", f"会话超时（{self.session_timeout:.0f}s）"
                    break
                try:
                    message = await firehose.get(timeout=remaining)
                except asyncio.TimeoutError:
                    status, reason = "timeout", f"会话超时（{self.session_timeout:.0f}s）"
                    break
                if message is None:
                    break

                self._observe(message)
                if message.type is MessageType.PLAN:
                    plan_payload = message.payload.get("plan") or plan_payload
                elif message.type is MessageType.REVIEW:
                    review_payload = message.payload
                    if not message.payload.get("approved"):
                        rejections += 1
                        rounds = max(rounds, rejections)  # a rejection costs the actor a rework round
                        if rejections > self.max_rounds:
                            status = "failed"
                            reason = f"审查连续 {rejections} 次未通过，已放弃"
                            await self._finish(status, reason, rounds)
                            break
                elif message.type is MessageType.TEST:
                    test_payload = message.payload.get("test") or test_payload
                    outcome = TestResult.model_validate(test_payload or {})
                    rounds = max(rounds, message.round)
                    if outcome.passed:
                        status, reason = "done", outcome.summary or "测试通过"
                        await self._finish(status, reason, rounds)
                        break
                    test_failures += 1
                    if test_failures > self.max_rounds:
                        status = "failed"
                        reason = f"测试连续 {test_failures} 次失败：" + "；".join(outcome.failures)
                        await self._finish(status, reason, rounds)
                        break
                    rounds = test_failures
                    await self.bus.publish(
                        make_message(
                            sender=Role.ORCHESTRATOR,
                            recipient=Role.ACTOR,
                            type=MessageType.RERUN,
                            payload={
                                "plan": plan_payload,
                                "feedback": "；".join(outcome.failures) or outcome.summary,
                                "test_output": outcome.output[-3000:],
                            },
                            round=rounds,
                        )
                    )
                elif message.type is MessageType.ERROR:
                    status = "failed"
                    reason = str(message.payload.get("summary") or "agent 报错")
                    await self._finish(status, reason, rounds)
                    break
        finally:
            await self._shutdown(tasks, [*inboxes.values(), firehose])
            for pending in firehose.drain_nowait():  # keep the final DONE in the transcript
                self._observe(pending)

        duration = time.perf_counter() - started
        result = SessionResult(
            session_id=session_id,
            goal=goal,
            status=status,
            rounds=rounds,
            reason=reason,
            plan=plan_payload,
            review=review_payload,
            test=test_payload,
            messages=list(self.messages),
            run_dir=run_dir,
            duration=duration,
        )
        result.stats = self._stats(
            rounds=rounds,
            rejections=rejections,
            test_failures=test_failures,
            duration=duration,
            started_at=started_wall,
        )
        self._persist(result)
        return result

    # -- internals ---------------------------------------------------------
    def _stats(
        self,
        *,
        rounds: int,
        rejections: int,
        test_failures: int,
        duration: float,
        started_at: float | None = None,
    ) -> dict[str, Any]:
        """Aggregate the counters every role contributed to this session."""

        llm_calls = tokens = 0
        models: dict[str, int] = {}
        for role, agent in self.agents.items():
            client = getattr(agent, "llm", None)
            if client is None:
                continue
            calls = int(getattr(client, "requests", 0) or 0)
            llm_calls += calls
            models.setdefault(role.value, calls)
            usage = getattr(client, "usage_total", None) or {}
            tokens += int(usage.get("total_tokens", 0) or 0)
        tool_calls = sum(
            1
            for message in self.messages
            if message.type is MessageType.LOG and message.payload.get("tool")
        )
        return {
            "started_at": round(started_at if started_at is not None else time.time(), 3),
            "messages": len(self.messages),
            "rounds": rounds,
            "llm_calls": llm_calls,
            "llm_calls_by_role": models,
            "tool_calls": tool_calls,
            "tokens": tokens,
            "rejections": rejections,
            "test_failures": test_failures,
            "duration": round(duration, 3),
        }

    async def _finish(self, status: str, reason: str, rounds: int) -> None:
        await self.bus.publish(
            make_message(
                sender=Role.ORCHESTRATOR,
                recipient="broadcast",
                type=MessageType.DONE,
                payload={"status": status, "reason": reason, "rounds": rounds},
                round=rounds,
            )
        )

    async def _shutdown(self, tasks: list[asyncio.Task[None]], subscriptions: list[Any]) -> None:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for subscription in subscriptions:
            subscription.close()

    def _observe(self, message: Message) -> None:
        self.messages.append(message)
        if callable(self.printer):
            try:
                self.printer(message)
            except Exception:  # noqa: BLE001 - rendering must never break a run
                logger.debug("printer failed", exc_info=True)

    def _persist(self, result: SessionResult) -> None:
        if result.run_dir is None:
            return
        try:
            (result.run_dir / "transcript.json").write_text(
                json.dumps(
                    {
                        "session": {
                            "id": result.session_id,
                            "goal": result.goal,
                            "status": result.status,
                            "rounds": result.rounds,
                            "reason": result.reason,
                            "duration": round(result.duration, 3),
                            "stats": result.stats,
                        },
                        "messages": [message.to_envelope() for message in result.messages],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
                newline="\n",
            )
            (result.run_dir / "transcript.md").write_text(render_markdown(result), encoding="utf-8", newline="\n")
            (result.run_dir / "session.log").write_text(render_session_log(result), encoding="utf-8", newline="\n")
        except OSError as exc:  # pragma: no cover - disk problems should not fail a run
            logger.warning("cannot write run artefacts to %s: %s", result.run_dir, exc)


def new_session_id() -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def render_markdown(result: SessionResult) -> str:
    """Human readable transcript of one session."""

    lines = [
        f"# Agent team session `{result.session_id}`",
        "",
        f"- **目标**：{result.goal}",
        f"- **状态**：{result.status}",
        f"- **轮次**：{result.rounds}",
        f"- **耗时**：{result.duration:.1f}s",
        f"- **结论**：{result.reason or '(无)'}",
        "",
        "## 统计",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 消息数 | {result.stats.get('messages', len(result.messages))} |",
        f"| 模型调用 | {result.stats.get('llm_calls', 0)} |",
        f"| 工具调用 | {result.stats.get('tool_calls', 0)} |",
        f"| tokens | {result.stats.get('tokens', 0)} |",
        f"| 审查退回 | {result.stats.get('rejections', 0)} |",
        f"| 测试失败 | {result.stats.get('test_failures', 0)} |",
        "",
        "## 消息流水",
        "",
    ]
    for message in result.messages:
        lines.append(f"### [{message.round}] {message.sender.value} → {recipient_label(message)} · {message.type.value}")
        lines.append("")
        lines.append(f"_{message.summary(200)}_")
        lines.append("")
        payload = json.dumps(message.payload, ensure_ascii=False, indent=2)
        if len(payload) > 4000:
            payload = payload[:4000] + "\n... (truncated)"
        lines.append("```json")
        lines.append(payload)
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def render_session_log(result: SessionResult) -> str:
    """Compact, timestamped log line per message (``runs/<id>/session.log``)."""

    lines = [
        f"# session {result.session_id}",
        f"# goal: {result.goal}",
        f"# started: {datetime.fromtimestamp(result.stats.get('started_at', time.time())).strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for message in result.messages:
        stamp = datetime.fromtimestamp(message.created_at).strftime("%H:%M:%S")
        target = recipient_label(message)
        text = message.summary(200).replace("\n", " ")
        lines.append(f"[{stamp}] r{message.round} {message.sender.value:<12} -> {target:<12} {message.type.value:<8} {text}")
    lines.append("")
    lines.append(
        f"# finished: status={result.status} rounds={result.rounds} duration={result.duration:.2f}s "
        f"messages={len(result.messages)} reason={result.reason}"
    )
    lines.append(f"# stats: {result.stats_line()}")
    return "\n".join(lines) + "\n"

