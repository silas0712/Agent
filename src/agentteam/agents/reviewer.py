"""Reviewer: the quality gate between "done" and "tested"."""

from __future__ import annotations

import logging

from ..bus import MessageBus
from ..llm import BaseLLMClient
from ..schemas import ActResult, Message, MessageType, ReviewResult, Role, TaskPlan
from ..tools.file_tool import Workspace
from .base import BaseAgent

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
You are the REVIEWER of a small software team. You inspect what the actor just did.
Reply with ONE JSON object and nothing else:
{"approved": true, "score": 8, "issues": [], "suggestions": ["..."]}
Rules:
- Reject ("approved": false) when a planned file is missing, the code is a stub/placeholder,
  a plan step is clearly unfinished, imports are broken, or the tests cannot pass.
- "issues" must be concrete and actionable: the actor will fix exactly those items.
- "score" is 0-10 for overall quality; approve at 6 or above with no blocking issue.
"""


class ReviewerAgent(BaseAgent):
    role = Role.REVIEWER
    instructions = INSTRUCTIONS

    def __init__(
        self,
        *,
        bus: MessageBus,
        llm: BaseLLMClient | None = None,
        workspace: Workspace | None = None,
        max_files: int = 6,
        max_file_chars: int = 4000,
        max_retries: int = 1,
    ) -> None:
        super().__init__(bus=bus, llm=llm, max_retries=max_retries)
        self.workspace = workspace
        self.max_files = max_files
        self.max_file_chars = max_file_chars

    async def handle(self, message: Message) -> list[Message]:
        if message.type != MessageType.ACT:
            return []
        plan = TaskPlan.model_validate(message.payload.get("plan") or {})
        act = ActResult.model_validate(message.payload.get("act") or {})
        review = await self.review(plan, act)

        verdict = "通过" if review.approved else "不通过"
        await self.emit_log(
            f"审查结论：{verdict}（评分 {review.score}/10）"
            + (f"，问题 {len(review.issues)} 条" if review.issues else ""),
            round=message.round,
            issues=review.issues,
            suggestions=review.suggestions,
        )
        return [
            self.reply(
                MessageType.REVIEW,
                payload={
                    "plan": plan.model_dump(),
                    "act": act.model_dump(),
                    "approved": review.approved,
                    "score": review.score,
                    "issues": review.issues,
                    "suggestions": review.suggestions,
                    "summary": f"审查{verdict}（{review.score}/10）",
                },
                recipient="broadcast",  # actor fixes it, tester runs it, orchestrator counts rounds
                round=message.round,
            )
        ]

    async def review(self, plan: TaskPlan, act: ActResult) -> ReviewResult:
        findings = self.self_check(plan, act)
        prompt = self._build_prompt(plan, act)
        try:
            raw = await self.complete_json(prompt)
            review = ReviewResult.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - never block the pipeline on a bad answer
            logger.warning("reviewer used its deterministic fallback: %s", exc)
            review = ReviewResult(
                approved=not findings,
                score=7 if not findings else 3,
                issues=findings,
                suggestions=["模型未返回可用 JSON，已改用静态检查结论。"],
            )

        if findings:
            merged = list(dict.fromkeys([*findings, *review.issues]))
            review = ReviewResult(
                approved=False,
                score=min(review.score, 4),
                issues=merged,
                suggestions=review.suggestions,
            )
        return review

    def self_check(self, plan: TaskPlan, act: ActResult) -> list[str]:
        """Cheap deterministic checks that do not need a model."""

        issues: list[str] = []
        if not act.changes and not act.commands:
            issues.append("本轮没有任何文件改动或命令执行，计划步骤未真正落地。")
        if self.workspace is not None:
            for step in plan.steps:
                for path in step.files:
                    if not self.workspace.exists(path):
                        issues.append(f"计划中要求的文件不存在：{path}")
            for change in act.changes:
                if not self.workspace.exists(change.path):
                    issues.append(f"声明已修改但文件不存在：{change.path}")
        if act.changes and not any(_looks_like_code(change.path) for change in act.changes):
            issues.append("本轮改动里没有任何代码/配置文件。")
        return sorted(set(issues))

    def _build_prompt(self, plan: TaskPlan, act: ActResult) -> str:
        parts = [
            f"目标：{plan.goal}",
            "计划步骤：\n" + "\n".join(f"  {step.id}. {step.title} — {step.detail}" for step in plan.steps),
            f"执行者自述：{act.summary or '(无)'}",
            f"执行者完成步骤：{act.steps_completed or '(未报告)'}，迭代 {act.iterations} 次",
            "改动文件：" + (", ".join(f"{change.path}({change.action})" for change in act.changes) or "(无)"),
            "执行过的命令：" + (", ".join(act.commands) or "(无)"),
        ]
        if plan.acceptance:
            parts.append("验收标准：\n" + "\n".join(f"  - {item}" for item in plan.acceptance))
        sampled = self._read_changes(act)
        if sampled:
            parts.append("代码抽样：\n" + sampled)
        parts.append("请输出审查 JSON（approved / score / issues / suggestions）。")
        return "\n\n".join(parts)

    def _read_changes(self, act: ActResult) -> str:
        if self.workspace is None:
            return ""
        chunks: list[str] = []
        for change in act.changes[: self.max_files]:
            if change.action == "delete" or not self.workspace.exists(change.path):
                continue
            try:
                content = self.workspace.read_text(change.path, max_bytes=self.max_file_chars)
            except Exception:  # noqa: BLE001
                continue
            chunks.append(f"--- {change.path} ---\n{content}")
        return "\n\n".join(chunks)


def _looks_like_code(path: str) -> bool:
    return path.rsplit(".", 1)[-1].lower() in {
        "py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "cs", "c", "cpp",
        "h", "sh", "ps1", "toml", "yaml", "yml", "json", "html", "css", "sql",
    }
