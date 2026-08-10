"""MCP protocol logic backed by the remote typed SDK, safe in the base wheel."""

from __future__ import annotations

from datetime import datetime
import json
import re
import sys
from typing import Literal
from typing import TextIO
from uuid import UUID

from rememberstack import __version__
from rememberstack.model.client import PipelineReadinessReport
from rememberstack.model.documents import IngestedVersion
from rememberstack.surfaces.mcp_memory_tools import handle_memory_write_tool
from rememberstack.surfaces.mcp_memory_tools import memory_write_tool_descriptors
from rememberstack.surfaces.mcp_memory_tools import MEMORY_WRITE_TOOL_NAMES
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.mcp_tools import open_query_tool_descriptors
from rememberstack.surfaces.query_sandbox.mcp_tools import OPEN_QUERY_TOOL_NAMES
from rememberstack.surfaces.sdk import MemoryApiError
from rememberstack.surfaces.sdk import MemoryClient

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
        self, *, version_ids: tuple[UUID, ...], require_projections: bool
    ) -> PipelineReadinessReport:
        """Proxy readiness inspection through the typed HTTP SDK."""
        return self._client.pipeline_readiness(
            version_ids=version_ids, require_projections=require_projections
        )

    def max_ingest_body_bytes(self) -> int | None:
        """No capability document is served yet — do not invent a client ceiling."""
        return None


class RemoteRecipeMcpServer:
    """Render remote recipes, write tools, and open-query tools; proxy to the API."""

    def __init__(self, *, client: MemoryClient) -> None:
        self._client = client
        self._write_backend = _RemoteMemoryWriteBackend(client=client)

    def list_tools(self) -> dict[str, object]:
        """The MCP ``tools/list`` result for this remote deployment.

        Order is stable: write/readiness static tools, then recipes from
        ``GET /recipes``, then the nine open-query tools when the remote
        deployment mounts the open facade (same composition gate as local MCP
        and HTTP).
        """
        tools: list[dict[str, object]] = list(memory_write_tool_descriptors())
        tools.extend(
            {
                "name": descriptor.name,
                "description": descriptor.description,
                "inputSchema": descriptor.input_schema,
            }
            for descriptor in self._client.recipes()
        )
        if self._remote_open_query_is_composed():
            tools.extend(open_query_tool_descriptors())
        return {"tools": tools}

    def call_tool(
        self, *, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        """The MCP ``tools/call`` result containing one JSON text block."""
        if name in MEMORY_WRITE_TOOL_NAMES:
            return handle_memory_write_tool(
                name=name, arguments=arguments, backend=self._write_backend
            )
        if name in OPEN_QUERY_TOOL_NAMES:
            try:
                payload = self._client.call_open_query(name=name, arguments=arguments)
            except (
                MemoryApiError,
                SandboxRejection,
                ValueError,
                TypeError,
                KeyError,
            ) as error:
                return {
                    "content": [{"type": "text", "text": str(error)}],
                    "isError": True,
                }
            return {
                "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
                "isError": False,
            }
        try:
            envelope = self._client.run_recipe(name=name, arguments=arguments)
        except (MemoryApiError, ValueError) as error:
            return {"content": [{"type": "text", "text": str(error)}], "isError": True}
        return {
            "content": [{"type": "text", "text": envelope.model_dump_json()}],
            "isError": False,
        }

    def _remote_open_query_is_composed(self) -> bool:
        """Return whether the remote deployment exposes the open-query surface.

        ``GET /query/space`` is mounted only when ``build_api`` composes
        ``open_query``. A valid-enough discovery identity is the authority that
        the nine static tools may be advertised; missing, unavailable, empty,
        wrong-schema, or malformed-hash responses fail closed so ``tools/list``
        never claims routes that are absent or untrustworthy.
        """
        try:
            payload = self._client.describe_query_space()
        except MemoryApiError:
            return False
        return _is_authoritative_open_query_discovery(payload)


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
    server: RemoteRecipeMcpServer,
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
    *, server: RemoteRecipeMcpServer, request: dict[str, object]
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
