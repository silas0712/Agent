"""FastAPI application: REST + WebSocket access to the agent team."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
import threading
import time
import webbrowser
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..config import PROVIDERS, ModelConfig, Settings
from ..orchestrator import SessionResult, new_session_id
from ..runtime import run_goal
from ..sessions import (
    SESSION_LOG,
    TRANSCRIPT_JSON,
    TRANSCRIPT_MD,
    SessionNotFound,
    list_summaries,
    load_transcript,
    messages_of,
    resolve_session,
)
from ..schemas import Message

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


class RunRequest(BaseModel):
    """Body of ``POST /api/sessions``."""

    goal: str = Field(min_length=1, description="要完成的目标")
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    max_rounds: int | None = None
    strict_first_review: bool = False


@dataclass
class LiveSession:
    """A session that is either running right now or already finished."""

    session_id: str
    goal: str
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    duration: float = 0.0
    rounds: int = 0
    reason: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    task: asyncio.Task[Any] | None = None

    # -- fan-out -----------------------------------------------------------
    def publish(self, event: dict[str, Any]) -> None:
        """Push *event* to every connected websocket (never blocks)."""

        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - slow browser
                logger.warning("dropping a websocket event for %s", self.session_id)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.subscribers.discard(queue)

    # -- snapshots ---------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.session_id,
            "goal": self.goal,
            "status": self.status,
            "rounds": self.rounds,
            "reason": self.reason,
            "duration": round(self.duration, 3),
            "started_at": self.started_at,
            "stats": self.stats,
            "messages": len(self.messages),
        }

    def finish(self, result: SessionResult) -> None:
        self.status = result.status
        self.rounds = result.rounds
        self.reason = result.reason
        self.duration = result.duration
        self.stats = dict(result.stats)
        self.publish({"kind": "status", **self.snapshot()})


class SessionManager:
    """Owns the running sessions and bridges the orchestrator to the browser."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.live: dict[str, LiveSession] = {}

    def get(self, session_id: str) -> LiveSession | None:
        return self.live.get(session_id)

    def running(self) -> list[LiveSession]:
        return [session for session in self.live.values() if session.status == "running"]

    def start(self, request: RunRequest) -> LiveSession:
        settings = self._settings_for(request)
        session = LiveSession(session_id=new_session_id(), goal=request.goal)
        self.live[session.session_id] = session

        def printer(message: Message) -> None:
            envelope = message.to_envelope()
            session.messages.append(envelope)
            session.publish({"kind": "message", "session": session.session_id, "message": envelope})

        async def runner() -> None:
            try:
                result = await run_goal(
                    settings,
                    request.goal,
                    printer=printer,
                    session_id=session.session_id,
                    strict_first_review=request.strict_first_review,
                    max_rounds=request.max_rounds,
                )
                session.finish(result)
            except asyncio.CancelledError:
                session.status = "cancelled"
                session.reason = "已被取消"
                session.publish({"kind": "status", **session.snapshot()})
                raise
            except Exception as exc:  # noqa: BLE001 - failures belong in the browser
                session.status = "failed"
                session.reason = f"{type(exc).__name__}: {exc}"
                logger.exception("web session %s failed", session.session_id)
                session.publish({"kind": "status", **session.snapshot()})
            finally:
                session.publish({"kind": "done", **session.snapshot()})
                # a finished session is served from disk from now on: memory stays bounded
                self.live.pop(session.session_id, None)

        session.task = asyncio.get_running_loop().create_task(runner())
        return session

    def cancel(self, session_id: str) -> LiveSession | None:
        session = self.live.get(session_id)
        if session is not None and session.task is not None and not session.task.done():
            session.task.cancel()
        return session

    def _settings_for(self, request: RunRequest) -> Settings:
        """Per-request copy, so overrides never leak into other sessions."""

        settings = deepcopy(self.settings)
        if request.provider or request.model or request.base_url or request.api_key:
            provider = (request.provider or settings.default_model.provider).lower()
            if provider not in PROVIDERS:
                raise ValueError(f"provider 只能是 {PROVIDERS} 之一，收到 {provider!r}")
            base = settings.default_model
            settings.default_model = ModelConfig(
                provider=provider,
                model=request.model or base.model,
                base_url=request.base_url or base.base_url,
                api_key=request.api_key or base.api_key,
                temperature=base.temperature,
                max_tokens=base.max_tokens,
            )
            settings.role_models = {}
        return settings



def _history_summary(settings: Settings, limit: int) -> list[dict[str, Any]]:
    summaries = list_summaries(settings.runs_dir)[:limit]
    return [
        {
            "id": summary.session_id,
            "goal": summary.goal,
            "status": summary.status,
            "rounds": summary.rounds,
            "reason": summary.reason,
            "duration": summary.duration,
            "messages": summary.messages,
            "stats": summary.stats,
        }
        for summary in summaries
    ]


def _read_artefact(settings: Settings, session_id: str, name: str) -> str:
    try:
        path = resolve_session(settings.runs_dir, session_id) / name
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{name} 不存在（会话 {session_id}）")
    return path.read_text(encoding="utf-8")


async def _replay_finished(
    websocket: WebSocket,
    session_id: str,
    settings: Settings,
    live: LiveSession | None,
) -> None:
    """Send a finished session to the browser (disk first, memory as fallback)."""

    messages: list[dict[str, Any]] = list(live.messages) if live is not None else []
    info: dict[str, Any] = live.snapshot() if live is not None else {}
    try:
        transcript = load_transcript(settings.runs_dir, session_id)
    except SessionNotFound as exc:
        if live is None:
            await websocket.send_json({"kind": "error", "detail": str(exc)})
            await websocket.close()
            return
    else:
        messages = list(transcript.get("messages") or [])
        info = dict(transcript.get("session") or {})
    info.setdefault("id", session_id)
    for envelope in messages:
        await websocket.send_json({"kind": "message", "session": session_id, "message": envelope})
    await websocket.send_json({"kind": "status", **info})
    await websocket.send_json({"kind": "done", **info})
    await websocket.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application (also used by the tests)."""

    settings = settings or Settings.from_env()
    manager = SessionManager(settings)
    app = FastAPI(title="agentteam", version=__version__, description="多模型协作 Agent 团队 Web UI")
    app.state.settings = settings
    app.state.manager = manager

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "running": len(manager.running())}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return {"version": __version__, **settings.as_dict()}

    @app.get("/api/sessions")
    def sessions(limit: int = 30) -> dict[str, Any]:
        history = _history_summary(settings, limit)
        return {"running": [session.snapshot() for session in manager.running()], "history": history}

    @app.post("/api/sessions", status_code=202)
    async def start_session(request: RunRequest) -> dict[str, Any]:
        # async on purpose: the session runs as a background task on the app loop
        try:
            session = manager.start(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return session.snapshot()

    @app.get("/api/sessions/{session_id}")
    def session_detail(session_id: str) -> dict[str, Any]:
        live = manager.get(session_id)
        if live is not None and live.status == "running":
            return {"live": True, "session": live.snapshot(), "messages": live.messages}
        try:
            transcript = load_transcript(settings.runs_dir, session_id)
        except SessionNotFound as exc:
            if live is not None:  # finished, but the artefacts are not readable
                return {"live": False, "session": live.snapshot(), "messages": live.messages}
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "live": False,
            "session": transcript.get("session") or {},
            "messages": [message.to_envelope() for message in messages_of(transcript)],
        }

    @app.delete("/api/sessions/{session_id}", status_code=202)
    def cancel_session(session_id: str) -> dict[str, Any]:
        session = manager.cancel(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"没有正在运行的会话：{session_id}")
        return session.snapshot()

    @app.get("/api/sessions/{session_id}/markdown", response_class=PlainTextResponse)
    def session_markdown(session_id: str) -> str:
        return _read_artefact(settings, session_id, TRANSCRIPT_MD)

    @app.get("/api/sessions/{session_id}/log", response_class=PlainTextResponse)
    def session_log(session_id: str) -> str:
        return _read_artefact(settings, session_id, SESSION_LOG)

    @app.get("/api/sessions/{session_id}/transcript", response_class=PlainTextResponse)
    def session_transcript(session_id: str) -> str:
        return _read_artefact(settings, session_id, TRANSCRIPT_JSON)

    @app.websocket("/ws/{session_id}")
    async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        live = manager.get(session_id)
        if live is None or live.status != "running":
            await _replay_finished(websocket, session_id, settings, live)
            return

        queue = live.subscribe()
        try:
            for envelope in list(live.messages):
                await websocket.send_json({"kind": "message", "session": session_id, "message": envelope})
            await websocket.send_json({"kind": "status", **live.snapshot()})
            while True:
                event = await queue.get()
                await websocket.send_json(event)
                if event.get("kind") == "done":
                    break
        except WebSocketDisconnect:  # pragma: no cover - the browser went away
            logger.debug("websocket client for %s disconnected", session_id)
        finally:
            live.unsubscribe(queue)
            with contextlib.suppress(Exception):
                await websocket.close()

    return app



def serve(
    settings: Settings | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    """Run the web UI with uvicorn (blocking)."""

    import uvicorn  # imported lazily: it is an optional dependency

    app = create_app(settings)
    url = f"http://{host}:{port}"
    if open_browser:
        timer = threading.Timer(1.0, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    print(f"agentteam web UI -> {url}（Ctrl+C 停止）")
    uvicorn.run(app, host=host, port=port, log_level="info")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentteam-web", description="agentteam Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    parser.add_argument("--workspace", default=None, help="agent 可读写的目录")
    parser.add_argument("--runs-dir", default=None, help="会话记录目录")
    parser.add_argument("--open-browser", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--log-level", default="WARNING", help="日志级别（默认 WARNING）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    if args.workspace:
        settings.workspace = Path(args.workspace).expanduser().resolve()
    if args.runs_dir:
        settings.runs_dir = Path(args.runs_dir).expanduser().resolve()

    try:
        serve(settings, host=args.host, port=args.port, open_browser=args.open_browser)
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        print("已停止")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())

