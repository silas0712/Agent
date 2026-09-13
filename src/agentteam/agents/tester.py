"""Tester: runs the plan's test commands and reports the truth."""

from __future__ import annotations

import asyncio
import logging

from ..bus import MessageBus
from ..llm import BaseLLMClient
from ..schemas import Message, MessageType, Role, TaskPlan, TestResult
from ..tools.file_tool import Workspace
from ..tools.shell_tool import ShellTool, use_venv_python
from .base import BaseAgent

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
You are the TESTER of a small software team. Real command output is given to you.
Reply with ONE JSON object and nothing else: {"summary": "<one sentence verdict>"}
Never claim a test passed that failed; the exit codes are authoritative.
"""


class TesterAgent(BaseAgent):
    role = Role.TESTER
    instructions = INSTRUCTIONS

    def __init__(
        self,
        *,
        bus: MessageBus,
        shell: ShellTool,
        llm: BaseLLMClient | None = None,
        workspace: Workspace | None = None,
        max_output_chars: int = 6000,
        max_retries: int = 1,
    ) -> None:
        super().__init__(bus=bus, llm=llm, max_retries=max_retries)
        self.shell = shell
        self.workspace = workspace
        self.max_output_chars = max_output_chars

    async def handle(self, message: Message) -> list[Message]:
        # Only an *approved* review should be executed.
        if message.type != MessageType.REVIEW or not message.payload.get("approved"):
            return []

        plan_payload = message.payload.get("plan") or {}
        plan = TaskPlan.model_validate(plan_payload)
        commands = self.commands_for(plan)

        if not commands:
            result = TestResult(
                passed=True,
                skipped=True,
                summary="没有可执行的测试命令，跳过验证（不阻塞流程）。",
            )
            await self.emit_log(result.summary, round=message.round, skipped=True)
            return [self._report(result, plan, message.round)]

        results = []
        for command in commands:
            await self.emit_log(f"运行测试：{command}", round=message.round)
            results.append(await asyncio.to_thread(self.shell.run, command))

        failures = [f"{item.command} → exit={item.returncode}" for item in results if not item.ok]
        passed = not failures
        output = "\n\n".join(item.to_text(limit=2500) for item in results)
        if len(output) > self.max_output_chars:
            output = output[: self.max_output_chars - 3] + "..."

        summary = await self._summarize(plan, results, passed, failures)
        result = TestResult(passed=passed, commands=commands, failures=failures, output=output, summary=summary)
        await self.emit_log(_verdict_text(passed, failures, len(commands)), round=message.round, passed=passed)
        return [self._report(result, plan, message.round)]

    # -- helpers -----------------------------------------------------------
    def _report(self, result: TestResult, plan: TaskPlan, round_: int) -> Message:
        return self.reply(
            MessageType.TEST,
            payload={"plan": plan.model_dump(), "test": result.model_dump(), "summary": result.summary},
            recipient=Role.ORCHESTRATOR,
            round=round_,
        )

    def commands_for(self, plan: TaskPlan) -> list[str]:
        commands = [command.strip() for command in plan.test_commands if command and command.strip()]
        commands = list(dict.fromkeys(commands))
        return commands or self.detect_commands()

    def detect_commands(self) -> list[str]:
        """Fallback: run pytest when the workspace looks like it has tests."""

        if self.workspace is None:
            return []
        try:
            files = self.workspace.list_files()
        except Exception:  # noqa: BLE001
            return []
        has_tests = any(
            name.split("/")[-1].startswith("test_") and name.endswith(".py") for name in files
        )
        return [use_venv_python("python -m pytest -q -p no:cacheprovider")] if has_tests else []

    async def _summarize(self, plan: TaskPlan, results: list, passed: bool, failures: list[str]) -> str:
        fallback = (
            f"全部通过：{len(results)} 条命令，目标「{plan.goal}」的自动化验证成功。"
            if passed
            else f"验证失败：{'；'.join(failures)}"
        )
        if self.llm is None:
            return fallback
        prompt = (
            f"目标：{plan.goal}\n"
            f"命令结果：\n" + "\n\n".join(item.to_text(limit=1500) for item in results)
            + "\n\n请用一句中文总结验证结果（JSON: {\"summary\": \"...\"}）。"
        )
        try:
            raw = await self.complete_json(prompt)
            text = str(raw.get("summary") or "").strip() if isinstance(raw, dict) else ""
            return text or fallback
        except Exception as exc:  # noqa: BLE001 - summary is cosmetic only
            logger.debug("tester summary failed: %s", exc)
            return fallback


def _verdict_text(passed: bool, failures: list[str], command_count: int) -> str:
    if passed:
        return f"测试通过（{command_count} 条命令）"
    return "测试失败：" + "；".join(failures)
