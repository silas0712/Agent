"""CLI entry point.

Examples
--------
``python -m agentteam.main``                                     # offline mock demo
``python -m agentteam.main --goal "写一个斐波那契模块与测试"``
``python -m agentteam.main --provider openai --model deepseek-chat \\``
``    --base-url http://localhost:11434/v1``
``python -m agentteam.main --list-sessions``
``python -m agentteam.main --replay 20260101-120000-ab12cd``
``python -m agentteam.main --serve``                             # web UI
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from . import __version__
from .config import BUS_BACKENDS, DEFAULT_GOAL, PROVIDERS, ModelConfig, Settings
from .console import ConsolePrinter
from .orchestrator import SessionResult
from .runtime import run_goal
from .sessions import (
    SessionNotFound,
    export_session,
    format_summaries,
    list_summaries,
    load_transcript,
    messages_of,
    resolve_session,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentteam",
        description="多模型协作 Agent 团队：规划者 / 执行者 / 审查者 / 测试者",
    )
    parser.add_argument("--goal", "-g", default=None, help="要完成的目标；省略则使用内置示例目标")
    parser.add_argument("--workspace", "-w", default=None, help="agent 可读写的目录（默认 ./workspace）")
    parser.add_argument("--runs-dir", default=None, help="会话记录目录（默认 ./runs）")
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
    parser.add_argument("--version", action="version", version=f"agentteam {__version__}", help="打印版本并退出")

    history = parser.add_argument_group("会话记录")
    history.add_argument("--list-sessions", nargs="?", type=int, const=30, default=None, metavar="N", help="列出最近的 N 个会话（默认 30）")
    history.add_argument("--replay", default=None, metavar="ID", help="回放某个会话的消息流水（支持 id 前缀）")
    history.add_argument("--export", default=None, metavar="ID", help="导出某个会话的产物")
    history.add_argument("--export-to", default=None, help="导出目标：目录，或 *.md / *.json / *.log 文件")

    web = parser.add_argument_group("Web UI")
    web.add_argument("--serve", action="store_true", help="启动 Web 界面（需要安装 [web] 可选依赖）")
    web.add_argument("--host", default="127.0.0.1", help="Web 监听地址（默认 127.0.0.1）")
    web.add_argument("--port", type=int, default=8765, help="Web 监听端口（默认 8765）")
    web.add_argument("--open-browser", action="store_true", help="启动 Web 后自动打开浏览器")

    parser.add_argument("--log-level", default="WARNING", help="日志级别（默认 WARNING）")
    return parser


def apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    if args.workspace:
        settings.workspace = Path(args.workspace).expanduser().resolve()
    if getattr(args, "runs_dir", None):
        settings.runs_dir = Path(args.runs_dir).expanduser().resolve()
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


async def run_session(
    settings: Settings,
    goal: str,
    *,
    printer: ConsolePrinter,
    strict_first_review: bool = False,
) -> SessionResult:
    """Thin CLI wrapper around :func:`agentteam.runtime.run_goal`."""

    return await run_goal(
        settings,
        goal,
        printer=printer,
        strict_first_review=strict_first_review,
        on_bus=lambda bus: printer.info(f"总线后端：{bus.backend}"),
    )


def _replay(settings: Settings, args: argparse.Namespace, printer: ConsolePrinter) -> int:
    """Re-print a stored session exactly as it happened."""

    try:
        transcript = load_transcript(settings.runs_dir, args.replay)
    except SessionNotFound as exc:
        printer.error(str(exc))
        return 1

    session = transcript.get("session") or {}
    printer.title(f"回放 {session.get('id') or args.replay}")
    printer.info(f"目标：{session.get('goal', '')}")
    printer.info(
        f"状态：{session.get('status', '?')} · 轮次：{session.get('rounds', 0)} · "
        f"耗时：{float(session.get('duration') or 0.0):.1f}s"
    )
    for message in messages_of(transcript):
        printer.print(message)

    printer.title("回放结束")
    stats = session.get("stats") or {}
    print(f"共 {len(transcript.get('messages') or [])} 条消息")
    if stats:
        print(
            f"统计：模型调用 {stats.get('llm_calls', 0)} 次 · 工具调用 {stats.get('tool_calls', 0)} 次 · "
            f"tokens {stats.get('tokens', 0)}"
        )
    print(f"目录：{resolve_session(settings.runs_dir, args.replay)}")
    return 0


def _export(settings: Settings, args: argparse.Namespace, printer: ConsolePrinter) -> int:
    """Copy one session's artefacts somewhere else."""

    destination = Path(args.export_to) if args.export_to else settings.runs_dir.parent / "exports"
    try:
        written = export_session(settings.runs_dir, args.export, destination)
    except SessionNotFound as exc:
        printer.error(str(exc))
        return 1
    printer.info(f"已导出：{written}")
    return 0


def _serve(settings: Settings, args: argparse.Namespace, printer: ConsolePrinter) -> int:
    """Start the optional FastAPI web UI."""

    try:
        from .web import serve
    except ImportError as exc:  # pragma: no cover - depends on optional deps
        printer.error(f"Web 依赖未安装：{exc}\n安装方式：pip install \"agentteam[web]\"")
        return 2

    printer.title("agentteam Web UI")
    printer.info(f"打开 http://{args.host}:{args.port}")
    serve(settings, host=args.host, port=args.port, open_browser=args.open_browser)
    return 0


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
        print(f"version   -> {__version__}")
        warnings = settings.warnings()
        if warnings:
            print("警告：")
            for item in warnings:
                print(f"  - {item}")
        return 0

    if args.list_sessions is not None:
        printer.title(f"最近 {args.list_sessions} 个会话（{settings.runs_dir}）")
        print(format_summaries(list_summaries(settings.runs_dir), limit=args.list_sessions))
        return 0

    if args.replay:
        return _replay(settings, args, printer)

    if args.export:
        return _export(settings, args, printer)

    if args.serve:
        return _serve(settings, args, printer)

    goal = args.goal or DEFAULT_GOAL
    printer.title("agentteam 启动")
    printer.info(f"目标：{goal}")
    printer.info(f"工作区：{settings.workspace}")
    for warning in settings.warnings():
        printer.info(f"⚠ {warning}")
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
