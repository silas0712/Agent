"""CLI entry point.

Examples
--------
``python -m agentteam.main``                                     # offline mock demo
``python -m agentteam.main --goal "写一个斐波那契模块与测试"``
``python -m agentteam.main --provider openai --model deepseek-chat \\``
``    --base-url http://localhost:11434/v1``
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .bus import create_bus
from .config import BUS_BACKENDS, DEFAULT_GOAL, PROVIDERS, ModelConfig, Settings
from .console import ConsolePrinter
from .orchestrator import SessionResult, Orchestrator, build_team
from .schemas import Role


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentteam",
        description="多模型协作 Agent 团队：规划者 / 执行者 / 审查者 / 测试者",
    )
    parser.add_argument("--goal", "-g", default=None, help="要完成的目标；省略则使用内置示例目标")
    parser.add_argument("--workspace", "-w", default=None, help="agent 可读写的目录（默认 ./workspace）")
    parser.add_argument("--provider", choices=PROVIDERS, default=None, help="所有角色的默认模型提供者")
    parser.add_argument("--model", default=None, help="所有角色的默认模型名")
    parser.add_argument("--base-url", default=None, help="OpenAI 兼容端点，如 http://localhost:11434/v1")
    parser.add_argument("--api-key", default=None, help="API key（也可用 OPENAI_API_KEY）")
    parser.add_argument("--bus", choices=BUS_BACKENDS, default=None, help="消息总线后端")
    parser.add_argument("--redis-url", default=None, help="Redis 地址，如 redis://localhost:6379/0")
    parser.add_argument("--max-rounds", type=int, default=None, help="返工/重跑的最大轮数")
    parser.add_argument("--max-tool-steps", type=int, default=None, help="执行者每轮最多工具迭代次数")
    parser.add_argument("--session-timeout", type=float, default=None, help="整个会话的超时时间（秒）")
    parser.add_argument("--strict-first-review", action="store_true", help="mock 模式：第一次审查先否决，演示返工回路")
    parser.add_argument("--env-file", default=None, help="指定 .env 文件")
    parser.add_argument("--quiet", action="store_true", help="只输出最终结论")
    parser.add_argument("--check", action="store_true", help="只打印解析到的配置然后退出")
    parser.add_argument("--log-level", default="WARNING", help="日志级别（默认 WARNING）")
    return parser


def apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    if args.workspace:
        settings.workspace = Path(args.workspace).expanduser().resolve()
    if args.redis_url:
        settings.redis_url = args.redis_url
    if args.bus:
        settings.bus_backend = args.bus
    for name in ("max_rounds", "max_tool_steps", "session_timeout"):
        value = getattr(args, name)
        if value is not None:
            setattr(settings, name, value)

    if args.provider or args.model or args.base_url or args.api_key:
        base = settings.default_model
        settings.default_model = ModelConfig(
            provider=(args.provider or base.provider).lower(),
            model=args.model or base.model,
            base_url=args.base_url or base.base_url,
            api_key=args.api_key or base.api_key,
            temperature=base.temperature,
            max_tokens=base.max_tokens,
        )
    return settings


async def run_session(settings: Settings, goal: str, *, printer: ConsolePrinter, strict_first_review: bool = False) -> SessionResult:
    settings.ensure_dirs()
    bus = await create_bus(settings)
    printer.info(f"总线后端：{bus.backend}")
    try:
        agents, _registry, workspace, _shell = build_team(settings, bus)
        if strict_first_review:
            from .llm.mock import mock_clients

            agents[Role.REVIEWER].llm = mock_clients(strict_first_review=True)["reviewer"]
        orchestrator = Orchestrator(settings, bus, agents, printer=printer)
        return await orchestrator.run(goal)
    finally:
        await bus.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING), format="%(levelname)s %(name)s: %(message)s")

    printer = ConsolePrinter(quiet=args.quiet)
    try:
        settings = apply_overrides(Settings.from_env(env_file=args.env_file), args)
    except ValueError as exc:
        printer.error(str(exc))
        return 2

    if args.check:
        print(settings.describe())
        print(f"workspace -> {settings.workspace}")
        print(f"runs      -> {settings.runs_dir}")
        return 0

    goal = args.goal or DEFAULT_GOAL
    printer.title("agentteam 启动")
    printer.info(f"目标：{goal}")
    printer.info(f"工作区：{settings.workspace}")
    if not args.quiet:
        print(settings.describe())

    try:
        result = asyncio.run(
            run_session(settings, goal, printer=printer, strict_first_review=args.strict_first_review)
        )
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        printer.error("已中断")
        return 130

    printer.title("会话结束")
    print(result.to_text())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
