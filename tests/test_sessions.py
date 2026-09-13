"""Tests for the session history helpers (list / replay / export)."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agentteam.config import Settings
from agentteam.runtime import run_goal
from agentteam.sessions import (
    SESSION_LOG,
    TRANSCRIPT_JSON,
    TRANSCRIPT_MD,
    SessionNotFound,
    export_session,
    format_summaries,
    list_summaries,
    load_transcript,
    messages_of,
    resolve_session,
)

GOAL = "创建一个 Python 模块 hello.py 与 pytest 测试"


def _session(settings: Settings, run: Callable[[Any], Any], *, goal: str = GOAL) -> Any:
    return run(run_goal(settings, goal))


def test_run_goal_writes_stats_and_every_artefact(settings: Settings, run: Callable[[Any], Any]) -> None:
    result = _session(settings, run)

    assert result.status == "done", result.to_text()
    assert result.run_dir is not None
    assert result.stats["llm_calls"] > 0
    assert result.stats["messages"] == len(result.messages)

    log = (result.run_dir / SESSION_LOG).read_text(encoding="utf-8")
    assert "# session " in log
    assert "status=done" in log
    assert "planner" in log and "tester" in log

    markdown = (result.run_dir / TRANSCRIPT_MD).read_text(encoding="utf-8")
    assert "## 统计" in markdown
    assert "| 模型调用 |" in markdown

    assert "统计：" in result.to_text()


def test_list_summaries_reports_the_newest_session_first(settings: Settings, run: Callable[[Any], Any]) -> None:
    result = _session(settings, run)

    summaries = list_summaries(settings.runs_dir)

    assert [summary.session_id for summary in summaries] == [result.session_id]
    summary = summaries[0]
    assert summary.ok is True
    assert summary.rounds == result.rounds
    assert summary.messages == len(result.messages)
    assert summary.has_log is True
    assert summary.goal == GOAL


def test_list_summaries_is_empty_without_a_runs_directory(settings: Settings) -> None:
    assert list_summaries(settings.runs_dir / "missing") == []
    assert format_summaries([]) == "(还没有会话记录)"


def test_resolve_session_accepts_a_unique_prefix(settings: Settings, run: Callable[[Any], Any]) -> None:
    result = _session(settings, run)

    assert resolve_session(settings.runs_dir, result.session_id) == result.run_dir
    assert resolve_session(settings.runs_dir, result.session_id[:8]) == result.run_dir


def test_resolve_session_rejects_unknown_ids(settings: Settings) -> None:
    with pytest.raises(SessionNotFound, match="没有匹配的会话"):
        resolve_session(settings.runs_dir, "nope")

    with pytest.raises(SessionNotFound, match="没有找到会话目录"):
        resolve_session(settings.runs_dir / "missing", "nope")


def test_replay_rebuilds_typed_messages(settings: Settings, run: Callable[[Any], Any]) -> None:
    result = _session(settings, run)

    transcript = load_transcript(settings.runs_dir, result.session_id)
    messages = messages_of(transcript)

    assert [message.id for message in messages] == [message.id for message in result.messages]
    assert messages[0].type.value == "task"
    assert messages[-1].type.value == "done"
    assert messages_of({"messages": [{"from": "nobody"}]}) == []


def test_export_session_copies_the_whole_run_directory(
    settings: Settings, run: Callable[[Any], Any], tmp_path: Path
) -> None:
    result = _session(settings, run)

    target = export_session(settings.runs_dir, result.session_id, tmp_path / "exports")

    assert target == tmp_path / "exports" / result.session_id
    for name in (TRANSCRIPT_JSON, TRANSCRIPT_MD, SESSION_LOG):
        assert (target / name).is_file()


def test_export_session_can_write_a_single_file(
    settings: Settings, run: Callable[[Any], Any], tmp_path: Path
) -> None:
    result = _session(settings, run)

    markdown = export_session(settings.runs_dir, result.session_id, tmp_path / "report.md")
    log = export_session(settings.runs_dir, result.session_id, tmp_path / "nested" / "session.log")

    assert markdown.read_text(encoding="utf-8").startswith("# Agent team session")
    assert "# session " in log.read_text(encoding="utf-8")


def test_export_session_rejects_unknown_ids(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(SessionNotFound):
        export_session(settings.runs_dir, "nope", tmp_path / "exports")


def test_summary_table_is_cjk_aware(settings: Settings, run: Callable[[Any], Any]) -> None:
    _session(settings, run)

    table = format_summaries(list_summaries(settings.runs_dir))
    header, row = table.splitlines()[0], table.splitlines()[1]

    def width(text: str) -> int:
        return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)

    assert header.startswith("会话")
    assert "hello.py" in row
    # the 目标 column starts at the same *display* column in the header and the row
    assert width(header[: header.index("目标")]) == width(row[: row.index("创建")])


def test_broken_session_directories_are_reported_not_raised(settings: Settings) -> None:
    broken = settings.runs_dir / "20260101-000000-broken"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / TRANSCRIPT_JSON).write_text("{not json", encoding="utf-8")

    (summary,) = list_summaries(settings.runs_dir)
    assert summary.status == "unreadable"

    empty = settings.runs_dir / "20260101-000001-empty"
    empty.mkdir(parents=True, exist_ok=True)
    assert {item.status for item in list_summaries(settings.runs_dir)} == {"unreadable", "incomplete"}
