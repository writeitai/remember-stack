"""MCP protocol logic backed by the remote typed SDK, safe in the base wheel.

Backwards-compatibility re-export of remember.remote_mcp.
"""

from __future__ import annotations

from remember.remote_mcp import *  # noqa: F403
from remember.remote_mcp import RemoteMcpMode
from remember.remote_mcp import RemoteMcpServerConfig
from remember.remote_mcp import RemoteOperationMcpServer
from remember.remote_mcp import serve_mcp_stdio

__all__ = (
    "RemoteMcpMode",
    "RemoteMcpServerConfig",
    "RemoteOperationMcpServer",
    "serve_mcp_stdio",
)
