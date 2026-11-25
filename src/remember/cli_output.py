"""Command failures and their terminal and JSON renderings."""

from __future__ import annotations

from dataclasses import replace
import json
import sys

from remember.mcp_tools._errors import invalid_arguments
from remember.mcp_tools._errors import ToolError


class CommandFailure(Exception):
    """A command has an actionable diagnostic and a deliberate exit status."""

    def __init__(self, *, error: ToolError, exit_code: int = 1) -> None:
        """Keep the diagnostic independent of how stderr will render it."""
        super().__init__(error.detail)
        self.error = error
        self.exit_code = exit_code


def usage_failure(detail: str) -> CommandFailure:
    """Report invalid CLI input using the shared MCP argument error code."""
    return CommandFailure(
        error=replace(
            invalid_arguments(detail=detail),
            agent_action="Fix the command arguments and rerun. See remember --help.",
        ),
        exit_code=2,
    )


def failure(
    *, code: str, detail: str, action: str, exit_code: int = 1
) -> CommandFailure:
    """Create a local failure that requires an explicit corrective action."""
    return CommandFailure(
        error=ToolError(
            code=code,
            detail=detail,
            status_code=None,
            retryable=False,
            agent_action=action,
        ),
        exit_code=exit_code,
    )


def report_error(*, error: ToolError, machine: bool) -> None:
    """Write an actionable failure to stderr, using the MCP envelope for JSON."""
    if machine:
        print(json.dumps(error.as_dict()), file=sys.stderr)
    else:
        status = f"API {error.status_code}: " if error.status_code is not None else ""
        print(
            f"error: {status}{error.detail} "
            f"[{error.code}, retryable={str(error.retryable).lower()}]\n"
            f"Next action: {error.agent_action}",
            file=sys.stderr,
        )
