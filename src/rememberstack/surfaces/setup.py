"""Sentry-like AI harness bootstrapper.

Backwards-compatibility re-export of remember.setup.
"""

from __future__ import annotations

from remember.setup import *  # noqa: F403
from remember.setup import configure_antigravity
from remember.setup import configure_claude_code
from remember.setup import configure_claude_desktop
from remember.setup import configure_codex
from remember.setup import configure_cursor
from remember.setup import get_claude_desktop_config_path
from remember.setup import resolve_launcher
from remember.setup import run_setup

__all__ = (
    "configure_antigravity",
    "configure_claude_code",
    "configure_claude_desktop",
    "configure_codex",
    "configure_cursor",
    "get_claude_desktop_config_path",
    "resolve_launcher",
    "run_setup",
)
