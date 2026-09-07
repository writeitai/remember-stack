"""Typed failures for the experimental Workspace-Bench adapter."""

from __future__ import annotations


class WorkspaceBenchError(RuntimeError):
    """An adapter, preflight, or protocol boundary failed closed."""


class LiveGateError(WorkspaceBenchError):
    """The requested step needs operator-authorized live assets or credentials."""
