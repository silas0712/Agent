"""Read back what happened: list, load, replay and export finished sessions.

Every run leaves three artefacts under ``runs/<session-id>/``:

* ``transcript.json`` - the full machine readable message stream
* ``transcript.md``   - a human readable timeline with statistics
* ``session.log``     - one timestamped line per message

This module is the read-only counterpart of :mod:`agentteam.orchestrator`.
"""

from __future__ import annotations

import json
import logging
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schemas import Message, MessageType, Role

logger = logging.getLogger(__name__)

TRANSCRIPT_JSON = "transcript.json"
TRANSCRIPT_MD = "transcript.md"
SESSION_LOG = "session.log"
ARTEFACTS = (TRANSCRIPT_JSON, TRANSCRIPT_MD, SESSION_LOG)


class SessionNotFound(LookupError):
    """Raised when a session id (or prefix) does not match any run directory."""


@dataclass
class SessionSummary:
    """One line of ``agentteam --list-sessions``."""

    session_id: str
    goal: str = ""
    status: str = "unknown"
    rounds: int = 0
    reason: str = ""
    duration: float = 0.0
    messages: int = 0
    stats: dict[str, Any] = field(default_factory=dict)
    run_dir: Path | None = None

    @property
    def ok(self) -> bool:
        return self.status == "done"

    @property
    def has_log(self) -> bool:
        return bool(self.run_dir) and (self.run_dir / SESSION_LOG).is_file()


def session_dir(runs_dir: Path, session_id: str) -> Path:
    return Path(runs_dir) / session_id


def resolve_session(runs_dir: Path, reference: str) -> Path:
    """Return the run directory for *reference* (exact id or unique prefix)."""

    runs_dir = Path(runs_dir)
    exact = runs_dir / reference
    if (exact / TRANSCRIPT_JSON).is_file():
        return exact
    if not runs_dir.is_dir():
        raise SessionNotFound(f"没有找到会话目录：{runs_dir}")

    matches = sorted(
        path
        for path in runs_dir.iterdir()
        if path.is_dir() and path.name.startswith(reference) and (path / TRANSCRIPT_JSON).is_file()
    )
    if not matches:
        raise SessionNotFound(f"没有匹配的会话：{reference}（目录 {runs_dir}）")
    if len(matches) > 1:
        names = "、".join(path.name for path in matches[:5])
        raise SessionNotFound(f"会话前缀 {reference!r} 不唯一，匹配到：{names}")
    return matches[0]


def load_transcript(runs_dir: Path, reference: str) -> dict[str, Any]:
    """Load the raw ``transcript.json`` of one session."""

    path = resolve_session(runs_dir, reference) / TRANSCRIPT_JSON
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionNotFound(f"无法读取 {path}：{exc}") from exc


def messages_of(transcript: dict[str, Any]) -> list[Message]:
    """Rebuild :class:`~agentteam.schemas.Message` objects from a transcript."""

    messages: list[Message] = []
    for raw in transcript.get("messages") or []:
        try:
            messages.append(
                Message(
                    id=str(raw.get("id") or ""),
                    sender=Role(str(raw.get("from"))),
                    recipient=raw.get("to") or "broadcast",
                    type=MessageType(str(raw.get("type"))),
                    payload=dict(raw.get("payload") or {}),
                    round=int(raw.get("round") or 0),
                    created_at=float(raw.get("created_at") or 0.0),
                )
            )
        except (ValueError, TypeError) as exc:  # pragma: no cover - defensive
            logger.debug("skipping malformed transcript entry: %s", exc)
    return messages


def transcript_entries(path: Path) -> list[dict[str, Any]]:
    """The raw message envelopes stored in one ``transcript.json``."""

    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("messages") or [])
    except (OSError, json.JSONDecodeError):
        return []


def _summary_from(path: Path) -> SessionSummary:
    summary = SessionSummary(session_id=path.name, run_dir=path)
    transcript_path = path / TRANSCRIPT_JSON
    if not transcript_path.is_file():
        summary.status = "incomplete"
        return summary
    try:
        session = json.loads(transcript_path.read_text(encoding="utf-8")).get("session") or {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("cannot read %s: %s", transcript_path, exc)
        summary.status = "unreadable"
        return summary
    summary.goal = str(session.get("goal") or "")
    summary.status = str(session.get("status") or "unknown")
    summary.rounds = int(session.get("rounds") or 0)
    summary.reason = str(session.get("reason") or "")
    summary.duration = float(session.get("duration") or 0.0)
    summary.stats = dict(session.get("stats") or {})
    entries = transcript_entries(transcript_path)
    summary.messages = int(summary.stats.get("messages") or len(entries))
    return summary


def list_summaries(runs_dir: Path) -> list[SessionSummary]:
    """Every session under *runs_dir*, newest first."""

    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return []
    directories = sorted(
        (path for path in runs_dir.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    return [_summary_from(path) for path in directories]


def _display_width(text: str) -> int:
    """Terminal columns of *text* (CJK glyphs are two columns wide)."""

    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def format_summaries(summaries: list[SessionSummary], *, limit: int = 30) -> str:
    """Fixed width table for the CLI (CJK aware)."""

    if not summaries:
        return "(还没有会话记录)"
    rows: list[tuple[str, ...]] = [("会话", "状态", "轮次", "耗时", "消息", "目标")]
    for summary in summaries[:limit]:
        rows.append(
            (
                summary.session_id,
                summary.status,
                str(summary.rounds),
                f"{summary.duration:.1f}s",
                str(summary.messages),
                " ".join(summary.goal.split())[:52],
            )
        )
    widths = [max(_display_width(row[index]) for row in rows) for index in range(len(rows[0]))]
    lines = []
    for row in rows:
        cells = [cell + " " * (widths[index] - _display_width(cell)) for index, cell in enumerate(row)]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def export_session(runs_dir: Path, reference: str, destination: Path) -> Path:
    """Copy every artefact of one session to *destination*.

    When *destination* ends with ``.md``, ``.json`` or ``.log`` a single file is
    written, otherwise the whole run directory is copied into
    ``destination/<session-id>/``.
    """

    run_dir = resolve_session(runs_dir, reference)
    destination = Path(destination)
    suffix = destination.suffix.lower()
    if suffix in {".md", ".json", ".log"}:
        source = {".md": TRANSCRIPT_MD, ".json": TRANSCRIPT_JSON, ".log": SESSION_LOG}[suffix]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(run_dir / source, destination)
        return destination

    target = destination / run_dir.name
    target.mkdir(parents=True, exist_ok=True)
    for name in ARTEFACTS:
        source = run_dir / name
        if source.is_file():
            shutil.copyfile(source, target / name)
    return target
