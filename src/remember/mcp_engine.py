"""``remember mcp`` in engine mode: the catalogue served against one engine (D136 §5.2).

:class:`EngineMcpServer` renders the memory tools of ``remember.mcp_tools``
and runs each call through the engine's HTTP API with :class:`MemoryClient`.
A tool is listed only when the deployment's ``GET /deployment`` reports it at
the catalogue's ``tool_version``, so an agent never sees a tool or argument
the deployment would not handle.

:func:`dispatch` answers one JSON-RPC message; the stdio loop here and the
Streamable HTTP listener (:mod:`remember.mcp_http`) both use it.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
import sys
from typing import Literal
from typing import TextIO
from uuid import UUID

from remember import __version__
from remember.client import MemoryApiError
from remember.client import MemoryClient
from remember.mcp_tools import DELETE_DOCUMENT_TOOL_NAME
from remember.mcp_tools import error_result
from remember.mcp_tools import handle_delete_document_tool
from remember.mcp_tools import handle_memory_write_tool
from remember.mcp_tools import handle_search_documents_tool
from remember.mcp_tools import map_error
from remember.mcp_tools import memory_tools
from remember.mcp_tools import MEMORY_WRITE_TOOL_NAMES
from remember.mcp_tools import OPEN_QUERY_TOOL_NAMES
from remember.mcp_tools import render_tools_list
from remember.mcp_tools import SEARCH_DOCUMENTS_TOOL_NAME
from remember.mcp_tools import tool
from remember.mcp_tools import ToolArgumentError
from remember.mcp_tools import ToolError
from remember.mcp_tools import validate_arguments
from remember.models import DocumentDeletion
from remember.models import DocumentSearchPage
from remember.models import DocumentSearchRequest
from remember.models import IngestedVersion
from remember.models import PipelineReadinessReport
from remember.models import ReadinessRequirements

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-11-25"


class _ClientBackend:
    """The write, readiness, delete and document-search tools' backend.

    The deployment enforces its body-size limit; the tool maps the refusal.
    """

    def __init__(self, *, client: MemoryClient) -> None:
        self._client = client

    def ingest(
        self,
        *,
        content: bytes,
        filename: str,
        mime: str,
        title: str | None,
        source_kind: str | None,
        source_ref: str | None,
        source_modified_at: datetime | None,
        versioning_mode: Literal["snapshot", "living"],
        source_version_ref: str | None,
    ) -> IngestedVersion:
        """Send one ingest through the HTTP SDK."""
        return self._client.ingest(
            content,
            filename=filename,
            mime=mime,
            title=title,
            source_kind=source_kind,
            source_ref=source_ref,
            source_modified_at=source_modified_at,
            versioning_mode=versioning_mode,
            source_version_ref=source_version_ref,
        )

    def pipeline_readiness(
        self, *, version_ids: tuple[UUID, ...], require: ReadinessRequirements
    ) -> PipelineReadinessReport:
        """Inspect readiness through the HTTP SDK."""
        return self._client.pipeline_readiness(version_ids=version_ids, require=require)

    def max_ingest_body_bytes(self) -> int | None:
        """No client-side ceiling: the deployment refuses an oversized body."""
        return None

    def delete_document(self, *, doc_id: UUID) -> DocumentDeletion:
        """Delete one document through the HTTP SDK."""
        return self._client.delete_document(doc_id=doc_id)

    def search_documents(self, *, request: DocumentSearchRequest) -> DocumentSearchPage:
        """Run one document search through the HTTP SDK (D134)."""
        return self._client.search_documents_request(request=request)


class EngineMcpServer:
    """The memory tool catalogue, served against one engine's HTTP API."""

    def __init__(
        self, *, client: MemoryClient, read_only: bool = False, path_ingest: bool
    ) -> None:
        """Bind one engine client.

        ``read_only`` omits and refuses every ``memory:write`` tool.
        ``path_ingest`` offers ``ingest``'s local ``path`` body (stdio only:
        the caller runs on this machine).
        """
        self._client = client
        self._backend = _ClientBackend(client=client)
        self._read_only = read_only
        self._path_ingest = path_ingest

    def list_tools(self) -> dict[str, object]:
        """The catalogue tools this deployment serves at the same version.

        Raises ``MemoryApiError`` when the deployment cannot be read, so a bad
        key or a dead engine never looks like an empty tool list.
        """
        served = self._client.deployment_build_info().tools
        definitions = [
            definition
            for definition in memory_tools()
            if served.get(definition.name) == definition.tool_version
        ]
        return {
            "tools": render_tools_list(
                definitions,
                project=False,
                path_ingest=self._path_ingest,
                read_only=self._read_only,
            )
        }

    def call_tool(
        self, *, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        """Run one tool; every failure is a tool error in the one envelope."""
        try:
            definition = tool(name)
        except KeyError:
            return _refusal(
                code="unknown_tool",
                detail=f"{name!r} is not a memory tool of this server.",
                agent_action="Call tools/list and use one of the listed tools.",
            )
        if "project" in arguments:
            return _refusal(
                code="project_routing_unavailable",
                detail=(
                    "this server serves one deployment; it takes no `project` argument."
                ),
                agent_action="Call the tool again without `project`.",
            )
        if self._read_only and definition.mutates:
            return _refusal(
                code="read_only",
                detail=f"{name!r} changes memory; this MCP server is read-only.",
                agent_action="Tell the user this server cannot change memory.",
            )
        if name == DELETE_DOCUMENT_TOOL_NAME:
            return handle_delete_document_tool(
                arguments=arguments, backend=self._backend
            )
        if name == SEARCH_DOCUMENTS_TOOL_NAME:
            return handle_search_documents_tool(
                arguments=arguments, backend=self._backend
            )
        if name in MEMORY_WRITE_TOOL_NAMES:
            return handle_memory_write_tool(
                name=name,
                arguments=arguments,
                backend=self._backend,
                path_ingest=self._path_ingest,
            )
        try:
            if name in OPEN_QUERY_TOOL_NAMES:
                text = json.dumps(
                    self._client.call_open_query(name=name, arguments=arguments),
                    default=str,
                )
            else:
                validate_arguments(name, arguments)
                text = self._client.run_operation(
                    name=name, arguments=arguments
                ).model_dump_json()
        except ToolArgumentError as error:
            return error_result(error.error)
        except Exception as error:  # noqa: BLE001 — mapped at the MCP wire boundary
            mapped = map_error(error)
            if mapped.code in {"internal_error", "local_backend_error"}:
                logger.exception("MCP tool %s failed with %s", name, mapped.code)
            return error_result(mapped)
        return {"content": [{"type": "text", "text": text}], "isError": False}


def _refusal(*, code: str, detail: str, agent_action: str) -> dict[str, object]:
    """A call this server refuses without sending it to the engine."""
    return error_result(
        ToolError(
            code=code,
            detail=detail,
            status_code=None,
            retryable=False,
            agent_action=agent_action,
        )
    )


def dispatch(*, server: EngineMcpServer, message: object) -> dict[str, object] | None:
    """Answer one JSON-RPC message; ``None`` for a notification or a response."""
    if not isinstance(message, dict):
        return rpc_error(
            request_id=None, code=-32600, message="request is not an object"
        )
    request_id = message.get("id")
    method = message.get("method")
    if method is None and "id" in message:
        return None  # a response to a request this server never sends
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return rpc_error(
            request_id=request_id, code=-32600, message="invalid JSON-RPC request"
        )
    if "id" not in message:
        return None
    params = message.get("params")
    try:
        if method == "initialize":
            if not isinstance(params, dict) or not isinstance(
                params.get("protocolVersion"), str
            ):
                return rpc_error(
                    request_id=request_id, code=-32602, message="bad initialize params"
                )
            result: dict[str, object] = {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "rememberstack", "version": __version__},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = server.list_tools()
        elif method == "tools/call":
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return rpc_error(
                    request_id=request_id, code=-32602, message="bad params"
                )
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                return rpc_error(
                    request_id=request_id, code=-32602, message="bad arguments"
                )
            result = server.call_tool(name=params["name"], arguments=arguments)
        else:
            return rpc_error(
                request_id=request_id, code=-32601, message=f"unknown method {method!r}"
            )
    except (MemoryApiError, ValueError, TypeError) as error:
        return rpc_error(request_id=request_id, code=-32603, message=str(error))
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(*, request_id: object, code: int, message: str) -> dict[str, object]:
    """One JSON-RPC error response."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def serve_stdio(
    *,
    server: EngineMcpServer,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> int:
    """Serve newline-delimited JSON-RPC on stdio until the input closes."""
    for line in input_stream:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as error:
            response = rpc_error(request_id=None, code=-32700, message=str(error))
        else:
            response = dispatch(server=server, message=message)
        if response is not None:
            output_stream.write(json.dumps(response) + "\n")
            output_stream.flush()
    return 0
