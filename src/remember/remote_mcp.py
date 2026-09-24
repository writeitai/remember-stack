"""MCP protocol logic backed by the remote typed SDK, safe in the base wheel."""

from __future__ import annotations

from datetime import datetime
import json
import re
import sys
from typing import Literal
from typing import TextIO
from uuid import UUID

from remember import __version__
from remember.client import MemoryApiError
from remember.client import MemoryClient
from remember.mcp_tools import DELETE_DOCUMENT_TOOL_NAME
from remember.mcp_tools import handle_delete_document_tool
from remember.mcp_tools import handle_memory_write_tool
from remember.mcp_tools import handle_search_documents_tool
from remember.mcp_tools import INGEST_TOOL_NAME
from remember.mcp_tools import MEMORY_WRITE_TOOL_NAMES
from remember.mcp_tools import OPEN_QUERY_TOOL_NAMES
from remember.mcp_tools import OPERATION_TOOL_NAMES
from remember.mcp_tools import PIPELINE_READINESS_TOOL_NAME
from remember.mcp_tools import render_tools_list
from remember.mcp_tools import SEARCH_DOCUMENTS_TOOL_NAME
from remember.mcp_tools import status_error_result
from remember.mcp_tools import tool
from remember.models import DocumentDeletion
from remember.models import DocumentSearchPage
from remember.models import DocumentSearchRequest
from remember.models import IngestedVersion
from remember.models import PipelineReadinessReport
from remember.models import ReadinessRequirements
from remember.models import ToolDescriptor
from remember.query_sandbox.errors import SandboxRejection

MCP_PROTOCOL_VERSION = "2025-11-25"

# Stable open-query discovery identity discriminants (catalog authority values).
# Validated at the remote MCP composition boundary only — not a full schema
# mirror, and deliberately free of spine/query-space imports for base-wheel safety.
_OPEN_QUERY_SCHEMA = "memory_v1"
_OPEN_QUERY_SCHEMA_MAJOR = 1
_SURFACE_MANIFEST_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class _RemoteMemoryWriteBackend:
    """Adapt ``MemoryClient`` to the shared memory-write tool backend protocol.

    Body-size preflight is skipped until a served capability document is
    available on the client (O1): the deployment rejects oversized bodies and
    the tool maps the structured error.
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
        """Proxy one ingest through the typed HTTP SDK."""
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
        """Proxy readiness inspection through the typed HTTP SDK."""
        return self._client.pipeline_readiness(version_ids=version_ids, require=require)

    def max_ingest_body_bytes(self) -> int | None:
        """No capability document is served yet — do not invent a client ceiling."""
        return None

    def delete_document(self, *, doc_id: UUID) -> DocumentDeletion:
        """Proxy one document deletion through the typed HTTP SDK."""
        return self._client.delete_document(doc_id=doc_id)

    def search_documents(self, *, request: DocumentSearchRequest) -> DocumentSearchPage:
        """Proxy one document search through the typed HTTP SDK."""
        return self._client.search_documents_request(request=request)


class RemoteOperationMcpServer:
    """Render remote writes, assured operations, and open-query tools."""

    def __init__(self, *, client: MemoryClient, read_only: bool = False) -> None:
        """Bind one remote client, optionally omitting and refusing write tools."""
        self._client = client
        self._write_backend = _RemoteMemoryWriteBackend(client=client)
        self._read_only = read_only

    def list_tools(self) -> dict[str, object]:
        """List remote write tools, assured operations, then open-query tools.

        Order is stable: write/readiness tools, ``delete_document``, then
        ``search_documents`` when the origin's ``GET /deployment`` reports
        it (kept under ``--read-only``: it only reads), operations from
        ``GET /operations``, then the seven open-query tools
        when the remote deployment mounts the open facade (same composition
        gate as local MCP and HTTP). ``--read-only`` omits every tool that
        changes memory (``ingest`` and ``delete_document``).
        """
        names = [
            INGEST_TOOL_NAME,
            PIPELINE_READINESS_TOOL_NAME,
            DELETE_DOCUMENT_TOOL_NAME,
        ]
        if self._origin_serves(name=SEARCH_DOCUMENTS_TOOL_NAME):
            names.append(SEARCH_DOCUMENTS_TOOL_NAME)
        tools = render_tools_list(
            [tool(name) for name in names],
            project=False,
            path_ingest=True,
            read_only=self._read_only,
        )
        tools.extend(
            {
                "name": descriptor.name,
                "description": descriptor.description,
                "inputSchema": descriptor.input_schema,
                "annotations": _operation_annotations(descriptor),
            }
            for descriptor in self._assured_operation_descriptors()
        )
        if self._remote_open_query_is_composed():
            tools.extend(
                render_tools_list(
                    [tool(name) for name in OPEN_QUERY_TOOL_NAMES],
                    project=False,
                    path_ingest=True,
                    read_only=self._read_only,
                )
            )
        return {"tools": tools}

    def call_tool(
        self, *, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        """The MCP ``tools/call`` result containing one JSON text block."""
        if name == DELETE_DOCUMENT_TOOL_NAME:
            return handle_delete_document_tool(
                arguments=arguments,
                backend=None if self._read_only else self._write_backend,
            )
        if name == SEARCH_DOCUMENTS_TOOL_NAME:
            return handle_search_documents_tool(
                arguments=arguments, backend=self._write_backend
            )
        if name in MEMORY_WRITE_TOOL_NAMES:
            if self._read_only and tool(name).mutates:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"write tool {name!r} is disabled on this read-only MCP server",
                        }
                    ],
                    "isError": True,
                }
            return handle_memory_write_tool(
                name=name,
                arguments=arguments,
                backend=self._write_backend,
                path_ingest=True,
            )
        if name in OPEN_QUERY_TOOL_NAMES:
            try:
                payload = self._client.call_open_query(name=name, arguments=arguments)
            except MemoryApiError as error:
                return _memory_api_error_result(error=error)
            except SandboxRejection as error:
                return _sandbox_error_result(error=error)
            except (ValueError, TypeError, KeyError) as error:
                return {
                    "content": [{"type": "text", "text": str(error)}],
                    "isError": True,
                }
            return {
                "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
                "isError": False,
            }
        try:
            result = self._client.run_operation(name=name, arguments=arguments)
        except MemoryApiError as error:
            return _memory_api_error_result(error=error)
        except ValueError as error:
            return {"content": [{"type": "text", "text": str(error)}], "isError": True}
        return {
            "content": [{"type": "text", "text": result.model_dump_json()}],
            "isError": False,
        }

    def _origin_serves(self, *, name: str) -> bool:
        """Whether the origin's ``GET /deployment`` serves this tool at our version.

        A host renders a catalogue tool only when the deployment lists it at
        the **same** ``tool_version`` (one-key client surfaces §3), so an
        agent never sees an argument the deployment would reject. An origin
        that cannot answer (an older engine, a proxy without the route, a
        transport failure) is taken as not serving it: the tool is omitted,
        and nothing else fails.
        """
        try:
            served = self._client.deployment_build_info().tools
        except MemoryApiError:
            return False
        return served.get(name) == tool(name).tool_version

    def _assured_operation_descriptors(self) -> tuple[ToolDescriptor, ...]:
        """Return remote assured-operation tools, or none if the origin has no registry.

        ``GET /operations`` 404 means the deployment does not mount the
        registry (managed UMC ``/dp/v1`` today). That is not a tools/list
        failure: static write tools still exist. Auth and transport errors
        still raise so a bad token or dead origin cannot look like an empty
        registry.
        """
        try:
            return self._client.list_operations()
        except MemoryApiError as error:
            if error.status_code == 404:
                return ()
            raise

    def _remote_open_query_is_composed(self) -> bool:
        """Return whether the remote deployment exposes the open-query surface.

        ``GET /query/space`` is mounted only when ``build_api`` composes
        ``open_query``. A valid-enough discovery identity is the authority that
        the seven static tools may be advertised; missing, unavailable, empty,
        wrong-schema, or malformed-hash responses fail closed so ``tools/list``
        never claims routes that are absent or untrustworthy.
        """
        try:
            payload = self._client.describe_query_space()
        except MemoryApiError:
            return False
        return _is_authoritative_open_query_discovery(payload)


def _operation_annotations(descriptor: ToolDescriptor) -> dict[str, bool]:
    """The catalogue's annotations for an operation the deployment lists.

    An operation the catalogue does not know is marked read-only only when the
    deployment declares it non-mutating, so an unclassified one never looks safe.
    """
    if descriptor.name in OPERATION_TOOL_NAMES:
        return tool(descriptor.name).annotations
    return {"readOnlyHint": descriptor.mutates is False, "destructiveHint": False}


def _memory_api_error_result(*, error: MemoryApiError) -> dict[str, object]:
    """Preserve typed remote failure metadata inside an MCP error result."""
    return status_error_result(
        status_code=error.status_code, detail=error.detail, code=error.code
    )


def _sandbox_error_result(*, error: SandboxRejection) -> dict[str, object]:
    """Preserve a public query rejection code inside an MCP error result."""
    return status_error_result(
        status_code=None, detail=error.message, code=error.code.value
    )


def _is_authoritative_open_query_discovery(payload: object) -> bool:
    """True only for a dict carrying the stable open-query discovery identity.

    Requires ``schema == memory_v1``, ``schema_major == 1``, and a syntactically
    valid 64-character lowercase hex ``surface_manifest_hash``. Compatible newer
    manifests (different hash) still pass; wrong schema, major, empty objects,
    and malformed hashes fail closed. Does not mirror the full discovery schema.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("schema") != _OPEN_QUERY_SCHEMA:
        return False
    schema_major = payload.get("schema_major")
    # bool is a subclass of int; reject True/False explicitly.
    if type(schema_major) is not int or schema_major != _OPEN_QUERY_SCHEMA_MAJOR:
        return False
    manifest_hash = payload.get("surface_manifest_hash")
    if not isinstance(manifest_hash, str):
        return False
    return _SURFACE_MANIFEST_HASH_RE.fullmatch(manifest_hash) is not None


def serve_mcp_stdio(
    *,
    server: RemoteOperationMcpServer,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> int:
    """Serve the minimal MCP JSON-RPC lifecycle over newline-delimited stdio."""
    for line in input_stream:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as error:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(error)},
            }
        else:
            if not isinstance(request, dict):
                response = _rpc_error(
                    request_id=None, code=-32600, message="request is not an object"
                )
            else:
                try:
                    response = _dispatch(server=server, request=request)
                except (MemoryApiError, ValueError, TypeError) as error:
                    response = _rpc_error(
                        request_id=request.get("id"), code=-32603, message=str(error)
                    )
        if response is not None:
            output_stream.write(json.dumps(response) + "\n")
            output_stream.flush()
    return 0


def _dispatch(
    *, server: RemoteOperationMcpServer, request: dict[str, object]
) -> dict[str, object] | None:
    """Dispatch one MCP request; notifications deliberately have no response."""
    request_id = request.get("id")
    method = request.get("method")
    if request.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return _rpc_error(
            request_id=request_id, code=-32600, message="invalid JSON-RPC request"
        )
    if "id" not in request:
        return None
    if method == "initialize":
        params = request.get("params")
        if not isinstance(params, dict) or not isinstance(
            params.get("protocolVersion"), str
        ):
            return _rpc_error(
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
        params = request.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _rpc_error(request_id=request_id, code=-32602, message="bad params")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return _rpc_error(
                request_id=request_id, code=-32602, message="bad arguments"
            )
        result = server.call_tool(name=params["name"], arguments=arguments)
    else:
        return _rpc_error(
            request_id=request_id, code=-32601, message=f"unknown method {method!r}"
        )
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(*, request_id: object, code: int, message: str) -> dict[str, object]:
    """Build one JSON-RPC error response."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
