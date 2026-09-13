"""Tests for the optional FastAPI web UI (skipped without the ``web`` extra)."""

from __future__ import annotations

import time
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="需要 pip install agentteam[web]")
pytest.importorskip("httpx", reason="TestClient 需要 httpx")

from fastapi.testclient import TestClient  # noqa: E402

from agentteam.config import Settings  # noqa: E402
from agentteam.web import create_app  # noqa: E402

GOAL = "创建一个 Python 模块 hello.py 与 pytest 测试"


@pytest.fixture
def client(settings: Settings) -> Any:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def wait_for_status(client: Any, session_id: str, *, timeout: float = 120.0) -> dict[str, Any]:
    """Poll until the session leaves the ``running`` state."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/sessions/{session_id}").json()
        session = payload.get("session") or {}
        if session.get("status") != "running":
            return session
        time.sleep(0.2)
    raise AssertionError(f"会话 {session_id} 未在 {timeout}s 内结束")


def test_health_config_and_index(client: Any, settings: Settings) -> None:
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["running"] == 0

    config = client.get("/api/config").json()
    assert config["bus"] == "memory"
    assert str(settings.workspace) == config["workspace"]
    assert set(config["models"]) == {"planner", "actor", "reviewer", "tester"}
    assert config["models"]["planner"]["has_api_key"] is False
    assert config["warnings"] == []

    index = client.get("/")
    assert index.status_code == 200
    assert "agentteam" in index.text


def test_empty_session_history(client: Any) -> None:
    payload = client.get("/api/sessions").json()
    assert payload == {"running": [], "history": []}


def test_run_a_session_over_http(client: Any, settings: Settings) -> None:
    started = client.post("/api/sessions", json={"goal": GOAL})
    assert started.status_code == 202, started.text
    session_id = started.json()["id"]
    assert started.json()["status"] == "running"

    session = wait_for_status(client, session_id)

    assert session["status"] == "done", session
    assert session["stats"]["llm_calls"] > 0
    assert (settings.workspace / "hello.py").is_file()
    assert (settings.runs_dir / session_id / "session.log").is_file()

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["live"] is False
    assert detail["messages"][0]["type"] == "task"
    assert detail["messages"][-1]["type"] == "done"

    history = client.get("/api/sessions").json()
    assert [entry["id"] for entry in history["history"]] == [session_id]

    assert "## 统计" in client.get(f"/api/sessions/{session_id}/markdown").text
    assert "status=done" in client.get(f"/api/sessions/{session_id}/log").text
    assert "\"messages\"" in client.get(f"/api/sessions/{session_id}/transcript").text


def test_requests_are_validated(client: Any) -> None:
    assert client.post("/api/sessions", json={"goal": ""}).status_code == 422
    assert client.post("/api/sessions", json={}).status_code == 422

    bad_provider = client.post("/api/sessions", json={"goal": GOAL, "provider": "gemini"})
    assert bad_provider.status_code == 400
    assert "provider" in bad_provider.json()["detail"]


def test_unknown_sessions_return_404(client: Any) -> None:
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.get("/api/sessions/nope/markdown").status_code == 404
    assert client.get("/api/sessions/nope/log").status_code == 404
    assert client.delete("/api/sessions/nope").status_code == 404


def test_websocket_streams_a_live_session(client: Any) -> None:
    session_id = client.post("/api/sessions", json={"goal": GOAL}).json()["id"]

    kinds: list[str] = []
    seen: list[str] = []
    with client.websocket_connect(f"/ws/{session_id}") as socket:
        while True:
            event = socket.receive_json()
            kinds.append(event["kind"])
            if event["kind"] == "message":
                seen.append(event["message"]["type"])
            if event["kind"] == "done":
                assert event["status"] == "done"
                break

    assert "status" in kinds and "message" in kinds
    assert seen[0] == "task"
    assert seen[-1] == "done"


def test_websocket_replays_a_finished_session(client: Any) -> None:
    session_id = client.post("/api/sessions", json={"goal": GOAL}).json()["id"]
    wait_for_status(client, session_id)

    with client.websocket_connect(f"/ws/{session_id}") as socket:
        first = socket.receive_json()
        assert first["kind"] == "message"
        assert first["message"]["type"] == "task"
        types = []
        while True:
            event = socket.receive_json()
            if event["kind"] == "message":
                types.append(event["message"]["type"])
            if event["kind"] == "done":
                break

    assert types[-1] == "done"


def test_websocket_reports_unknown_sessions(client: Any) -> None:
    with client.websocket_connect("/ws/nope") as socket:
        event = socket.receive_json()
        assert event["kind"] == "error"
        assert "没有匹配的会话" in event["detail"]


def test_strict_first_review_can_be_requested_from_the_browser(client: Any) -> None:
    payload = client.post("/api/sessions", json={"goal": GOAL, "strict_first_review": True}).json()

    session = wait_for_status(client, payload["id"])

    assert session["status"] == "done"
    assert session["rounds"] == 1
    assert session["stats"]["rejections"] == 1
