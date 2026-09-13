"""Run one goal end to end - shared by the CLI, the web UI and the tests."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .bus import MessageBus, create_bus
from .config import Settings
from .llm.mock import mock_clients
from .orchestrator import Orchestrator, SessionResult, build_team
from .schemas import Message, Role

logger = logging.getLogger(__name__)

Printer = Callable[[Message], None]


async def run_goal(
    settings: Settings,
    goal: str,
    *,
    printer: Printer | None = None,
    session_id: str | None = None,
    strict_first_review: bool = False,
    max_rounds: int | None = None,
    session_timeout: float | None = None,
    on_bus: Callable[[MessageBus], Any] | None = None,
) -> SessionResult:
    """Build a fresh team + bus, run one goal and always close the bus.

    ``on_bus`` is called once the bus is up (useful to report whether Redis or
    the in-memory fallback is in use).
    """

    settings.ensure_dirs()
    bus = await create_bus(settings)
    if on_bus is not None:
        on_bus(bus)
    try:
        agents, _registry, _workspace, _shell = build_team(settings, bus)
        if strict_first_review:
            agents[Role.REVIEWER].llm = mock_clients(strict_first_review=True)["reviewer"]
        orchestrator = Orchestrator(
            settings,
            bus,
            agents,
            printer=printer,
            max_rounds=max_rounds,
            session_timeout=session_timeout,
        )
        return await orchestrator.run(goal, session_id=session_id)
    finally:
        await bus.close()
