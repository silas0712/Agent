"""Planner: turns a raw goal into a concrete, testable plan."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from ..schemas import Message, MessageType, PlanStep, Role, TaskPlan
from ..tools.shell_tool import use_venv_python
from .base import AgentError, BaseAgent

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
You are the PLANNER of a small software team (planner / actor / reviewer / tester).
Break the user goal into 2-6 concrete, verifiable steps.
Reply with ONE JSON object, no markdown:
{
  "goal": "<restated goal>",
  "summary": "<one sentence strategy>",
  "steps": [{"id": 1, "title": "<short title>", "detail": "<what to build>", "files": ["relative/path.py"]}],
  "test_commands": ["<shell command that proves it works>"],
  "acceptance": ["<observable acceptance criterion>"]
}
Rules:
- Prefer the smallest design that satisfies the goal; do not invent extra features.
- Every file you mention is a *relative* path inside the project workspace.
- test_commands must be runnable from the workspace root (pytest for Python).
"""


class PlannerAgent(BaseAgent):
    role = Role.PLANNER
    instructions = INSTRUCTIONS

    async def handle(self, message: Message) -> list[Message]:
        if message.type != MessageType.TASK:
            return []
        goal = str(message.payload.get("goal") or "").strip()
        if not goal:
            return [self.error("task message without a goal", round=message.round)]

        extra = str(message.payload.get("feedback") or "").strip()
        plan = await self.create_plan(goal, feedback=extra)
        await self.emit_log(
            f"计划已生成：{len(plan.steps)} 个步骤，测试命令 {len(plan.test_commands)} 条",
            round=message.round,
            plan=plan.model_dump(),
        )
        return [
            self.reply(
                MessageType.PLAN,
                payload={"plan": plan.model_dump(), "goal": plan.goal},
                recipient=Role.ACTOR,
                round=message.round,
            )
        ]

    async def create_plan(self, goal: str, *, feedback: str = "") -> TaskPlan:
        prompt = f"目标：{goal}\n"
        if feedback:
            prompt += f"上一轮反馈（必须纳入计划）：{feedback}\n"
        prompt += "请输出计划 JSON。"

        try:
            raw = await self.complete_json(prompt)
            plan = TaskPlan.model_validate(raw)
            if not plan.steps:
                raise ValueError("plan contains no steps")
            if not plan.goal:
                plan.goal = goal
            return plan
        except (ValidationError, ValueError, TypeError, AgentError) as exc:
            logger.warning("planner fell back to a single-step plan: %s", exc)
            return fallback_plan(goal, reason=str(exc))


def fallback_plan(goal: str, *, reason: str = "") -> TaskPlan:
    """Used when the model is unavailable or returns something unusable."""

    detail = goal if not reason else f"{goal}（模型输出不可用：{reason[:120]}）"
    plan = TaskPlan(
        goal=goal,
        summary="单步兜底计划：直接实现目标并交给审查者/测试者验证。",
        steps=[PlanStep(id=1, title="实现目标", detail=detail)],
    )
    if _looks_like_python(goal):
        plan.test_commands = [use_venv_python("python -m pytest -q -p no:cacheprovider")]
    return plan


def _looks_like_python(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in (".py", "python", "pytest", "函数", "模块", "类"))
