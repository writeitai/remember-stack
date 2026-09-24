"""The memory tool catalogue every MCP host renders (D136).

``remember.mcp_tools`` defines each memory tool once — name, description,
input schema, permission, version and engine route — together with argument
validation and the structured error envelopes, so the engine's in-process MCP
server, ``remember mcp`` and any other Python MCP host describe and check the
same tools identically. A host may add tools of its own; it never redefines a
memory tool.

It has no engine imports and performs no I/O at import time, so it works with
the base ``remember`` install.
"""

from __future__ import annotations

from remember.mcp_tools._definitions import DELETE_DOCUMENT_TOOL_NAME
from remember.mcp_tools._definitions import INGEST_TOOL_NAME
from remember.mcp_tools._definitions import memory_tools
from remember.mcp_tools._definitions import MEMORY_WRITE_TOOL_NAMES
from remember.mcp_tools._definitions import OPEN_QUERY_TOOL_NAMES
from remember.mcp_tools._definitions import OPERATION_TOOL_NAMES
from remember.mcp_tools._definitions import Permission
from remember.mcp_tools._definitions import PIPELINE_READINESS_TOOL_NAME
from remember.mcp_tools._definitions import PROJECT_ARGUMENT
from remember.mcp_tools._definitions import render_tools_list
from remember.mcp_tools._definitions import SEARCH_DOCUMENTS_TOOL_NAME
from remember.mcp_tools._definitions import tool
from remember.mcp_tools._definitions import ToolDefinition
from remember.mcp_tools._documents import DocumentSearchBackend
from remember.mcp_tools._documents import handle_search_documents_tool
from remember.mcp_tools._errors import error_result
from remember.mcp_tools._errors import map_error
from remember.mcp_tools._errors import status_error_result
from remember.mcp_tools._errors import ToolArgumentError
from remember.mcp_tools._errors import ToolError
from remember.mcp_tools._memory import DocumentDeleteBackend
from remember.mcp_tools._memory import handle_delete_document_tool
from remember.mcp_tools._memory import handle_memory_write_tool
from remember.mcp_tools._memory import McpMemorySettings
from remember.mcp_tools._memory import MemoryWriteBackend
from remember.mcp_tools._query import validate_saved_query_identifier
from remember.mcp_tools._validate import validate_arguments

__all__ = (
    "DELETE_DOCUMENT_TOOL_NAME",
    "INGEST_TOOL_NAME",
    "MEMORY_WRITE_TOOL_NAMES",
    "OPEN_QUERY_TOOL_NAMES",
    "OPERATION_TOOL_NAMES",
    "PIPELINE_READINESS_TOOL_NAME",
    "PROJECT_ARGUMENT",
    "SEARCH_DOCUMENTS_TOOL_NAME",
    "DocumentDeleteBackend",
    "DocumentSearchBackend",
    "McpMemorySettings",
    "MemoryWriteBackend",
    "Permission",
    "ToolArgumentError",
    "ToolDefinition",
    "ToolError",
    "error_result",
    "handle_delete_document_tool",
    "handle_memory_write_tool",
    "handle_search_documents_tool",
    "map_error",
    "memory_tools",
    "render_tools_list",
    "status_error_result",
    "tool",
    "validate_arguments",
    "validate_saved_query_identifier",
)
