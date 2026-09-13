"""Actor: performs the real work by calling tools, one iteration at a time."""

from __future__ import annotations

import logging
from typing import Any

from ..bus import MessageBus
from ..llm import BaseLLMClient
from ..schemas import ActResult, FileChange, Message, MessageType, Role, TaskPlan
from ..tools.file_tool import Workspace
from ..tools.registry import ToolRegistry, ToolResult
from .base import AgentError, BaseAgent

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
You are the ACTOR of a small software team. You have real tools and must use them.
Analyse the plan, then reply with ONE JSON object and nothing else:
{
  "thought": "<short reasoning>",
  "actions": [{"tool": "<tool name>", "args": {...}}],
  "steps_completed": [1, 2],
  "done": false,
  "summary": "<what changed, in one or two sentences>"
}
Rules:
- Put all independent tool calls of this iteration in "actions" (run them together, it is faster).
- Set "done": true only when every plan step is finished and no more edits are needed.
- "file.write" always needs the COMPLETE file content, never a diff or a placeholder.
- Write real, working, runnable code. No TODO comments, no pseudo code.
- Paths are relative to the workspace root.
"""


class ActorAgent(BaseAgent):
    role = Role.ACTOR
    instructions = INSTRUCTIONS

    def __init__(
        self,
        *,
        bus: MessageBus,
        registry: ToolRegistry,
        llm: BaseLLMClient | None = None,
        workspace: Workspace | None = None,
        max_steps: int = 8,
        list_files_limit: int = 60,
        max_retries: int = 1,
    ) -> None:
        super().__init__(bus=bus, llm=llm, max_retries=max_retries)
        self.registry = registry
        self.workspace = workspace
        self.max_steps = max(1, max_steps)
        self.list_files_limit = list_files_limit
        self.plan: TaskPlan | None = None

    # -- protocol ----------------------------------------------------------
    async def handle(self, message: Message) -> list[Message]:
        plan = self._plan_from(message)
        if plan is None:
            return [self.error("收到的工作消息里没有可用的计划", round=message.round)]

        if message.type == MessageType.PLAN:
            return await self.work(plan, round_=message.round)
        if message.type == MessageType.RERUN:
            feedback = str(message.payload.get("feedback") or "")
            return await self.work(plan, round_=message.round, feedback=feedback)
        if message.type == MessageType.REVIEW and not message.payload.get("approved"):
            issues = message.payload.get("issues") or []
            feedback = "；".join(str(item) for item in issues) or "审查未通过"
            return await self.work(plan, round_=message.round, feedback=feedback)
        return []

    def _plan_from(self, message: Message) -> TaskPlan | None:
        raw = message.payload.get("plan")
        if isinstance(raw, dict):
            try:
                plan = TaskPlan.model_validate(raw)
            except Exception as exc:  # noqa: BLE001 - malformed plan from a peer agent
                logger.warning("actor received an invalid plan: %s", exc)
            else:
                self.plan = plan
                return plan
        return self.plan

    # -- the actual loop ---------------------------------------------------
    async def work(self, plan: TaskPlan, *, round_: int = 0, feedback: str = "") -> list[Message]:
        self.plan = plan
        result = ActResult()
        observations: list[str] = []
        done = False
        summary = ""

        for iteration in range(1, self.max_steps + 1):
            result.iterations = iteration
            prompt = self._build_prompt(plan, observations, feedback)
            try:
                answer = await self.complete_json(prompt)
            except AgentError as exc:
                if not result.changes and not result.commands:
                    return [self.error(f"actor 无法开始工作：{exc}", round=round_)]
                summary = f"{summary} 模型调用失败，提前结束本轮：{exc}".strip()
                observations.append(f"ERROR: {exc}")
                done = True
                break

            if not isinstance(answer, dict):
                observations.append("ERROR: 模型返回的不是 JSON 对象")
                continue

            actions = _as_action_list(answer)
            for action in actions:
                outcome = self.registry.execute_action(action)
                observations.append(outcome.to_text())
                self._record(result, outcome)
                await self.emit_log(
                    f"执行 {outcome.tool} → {'成功' if outcome.ok else '失败'}",
                    round=round_,
                    detail=outcome.output[:500],
                    tool=outcome.tool,
                )

            reported = str(answer.get("summary") or "").strip()
            if reported:
                summary = reported
            for step_id in answer.get("steps_completed") or []:
                try:
                    value = int(step_id)
                except (TypeError, ValueError):
                    continue
                if value not in result.steps_completed:
                    result.steps_completed.append(value)

            if answer.get("done") or not actions:
                done = True
                break

        if not done:
            note = f"已达到最大工具迭代次数（{self.max_steps}），本轮强制结束。"
            summary = f"{summary} {note}".strip()
            observations.append(f"WARN: {note}")

        result.summary = summary or "本轮没有产生改动。"
        result.observations = observations[-20:]
        await self.emit_log(
            result.summary,
            round=round_,
            changes=[change.model_dump() for change in result.changes],
            iterations=result.iterations,
        )
        return [
            self.reply(
                MessageType.ACT,
                payload={
                    "plan": plan.model_dump(),
                    "act": result.model_dump(),
                    "summary": result.summary,
                },
                recipient=Role.REVIEWER,
                round=round_,
            )
        ]

    def _record(self, result: ActResult, outcome: ToolResult) -> None:
        meta = outcome.meta or {}
        path = meta.get("path")
        if isinstance(path, str) and path:
            action = meta.get("action") or "update"
            if not any(change.path == path for change in result.changes):
                result.changes.append(FileChange(path=path, action=action, summary=outcome.tool))
        command = meta.get("command")
        if isinstance(command, str) and command:
            result.commands.append(command)

    def _build_prompt(self, plan: TaskPlan, observations: list[str], feedback: str) -> str:
        steps = "\n".join(
            f"  {step.id}. {step.title} — {step.detail}" + (f" [files: {', '.join(step.files)}]" if step.files else "")
            for step in plan.steps
        )
        parts = [
            f"目标：{plan.goal}",
            f"计划概要：{plan.summary}" if plan.summary else "",
            f"步骤：\n{steps}",
        ]
        if plan.test_commands:
            parts.append("计划中的测试命令：" + " | ".join(plan.test_commands))
        if self.workspace_files():
            parts.append("当前工作区文件：\n" + "\n".join(self.workspace_files()))
        if feedback:
            parts.append(f"需要修复的反馈（必须处理）：{feedback}")
        if observations:
            parts.append("上一次工具调用的结果：\n" + "\n\n".join(observations[-6:]))
        parts.append("可用工具：\n" + self.registry.describe())
        parts.append('现在输出本轮的 JSON（包含 "actions"、"steps_completed"、"done"、"summary"）。')
        return "\n\n".join(part for part in parts if part)

    def workspace_files(self) -> list[str]:
        if self.workspace is None:
            return []
        try:
            return self.workspace.list_files(max_files=self.list_files_limit)
        except Exception:  # noqa: BLE001 - listing is only prompt sugar
            return []


def _as_action_list(answer: dict[str, Any]) -> list[dict[str, Any]]:
    raw = answer.get("actions")
    if raw is None and ("tool" in answer or "name" in answer):
        raw = [answer]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    actions: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            actions.append(item)
        elif isinstance(item, str) and item.strip():
            actions.append({"tool": item.strip(), "args": {}})
    return actions
