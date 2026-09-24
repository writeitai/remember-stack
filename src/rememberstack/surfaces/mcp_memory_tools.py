"""Shared MCP tools for store/write and pipeline readiness.

Backwards-compatibility re-export of remember.mcp_memory_tools.
"""

from __future__ import annotations

from remember.mcp_memory_tools import delete_document_tool_descriptor
from remember.mcp_memory_tools import DELETE_DOCUMENT_TOOL_NAME
from remember.mcp_memory_tools import DocumentDeleteBackend
from remember.mcp_memory_tools import handle_delete_document_tool
from remember.mcp_memory_tools import handle_memory_write_tool
from remember.mcp_memory_tools import INGEST_TOOL_NAME
from remember.mcp_memory_tools import map_backend_error
from remember.mcp_memory_tools import McpMemorySettings
from remember.mcp_memory_tools import memory_write_tool_descriptors
from remember.mcp_memory_tools import MEMORY_WRITE_TOOL_NAMES
from remember.mcp_memory_tools import MemoryWriteBackend
from remember.mcp_memory_tools import PIPELINE_READINESS_TOOL_NAME

__all__ = (
    "DELETE_DOCUMENT_TOOL_NAME",
    "INGEST_TOOL_NAME",
    "MEMORY_WRITE_TOOL_NAMES",
    "PIPELINE_READINESS_TOOL_NAME",
    "DocumentDeleteBackend",
    "McpMemorySettings",
    "MemoryWriteBackend",
    "delete_document_tool_descriptor",
    "handle_delete_document_tool",
    "handle_memory_write_tool",
    "map_backend_error",
    "memory_write_tool_descriptors",
)
