"""Optional FastAPI web UI: run the team from a browser.

Install with ``pip install "agentteam[web]"`` and start it either with
``agentteam --serve`` or with the dedicated ``agentteam-web`` command.
"""

from __future__ import annotations

from .app import create_app, main, serve

__all__ = ["create_app", "serve", "main"]
