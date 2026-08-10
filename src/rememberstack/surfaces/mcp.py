"""The MCP surface (retrieval §7, D50 + open query space §3.1).

Recipe tools still render from the recipe registry. When an `OpenQueryFacade`
is composed, the nine static infrastructure tools are listed and dispatched
alongside them. When both ingest and pipeline-readiness ports are composed,
the Layer 1 write tools (`ingest`, `pipeline_readiness`) are advertised first.
The seventeen `examples.*` identities are never top-level tools — they run only
through `run_saved_query`.
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import Literal
from uuid import UUID

from rememberstack.model.client import PipelineReadinessReport
from rememberstack.model.documents import DocumentUpload
from rememberstack.model.documents import IngestedVersion
from rememberstack.surfaces.http_api import IngestPort
from rememberstack.surfaces.http_api import PipelineReadinessPort
from rememberstack.surfaces.mcp_memory_tools import handle_memory_write_tool
from rememberstack.surfaces.mcp_memory_tools import memory_write_tool_descriptors
from rememberstack.surfaces.mcp_memory_tools import MEMORY_WRITE_TOOL_NAMES
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.mcp_tools import dispatch_open_query_tool
from rememberstack.surfaces.query_sandbox.mcp_tools import open_query_tool_descriptors
from rememberstack.surfaces.query_sandbox.mcp_tools import OPEN_QUERY_TOOL_NAMES
from rememberstack.surfaces.query_sandbox.open_query import OpenQueryFacade
from rememberstack.surfaces.recipe_surface import InvalidArgumentError
from rememberstack.surfaces.recipe_surface import MissingArgumentError
from rememberstack.surfaces.recipe_surface import RecipeSurface
from rememberstack.surfaces.recipe_surface import UnknownRecipeError


class _LocalMemoryWriteBackend:
    """Adapt in-process ingest and readiness ports to the shared tool backend."""

    def __init__(
        self,
        *,
        ingest: IngestPort,
        pipeline_readiness: PipelineReadinessPort,
        deployment_id: UUID,
    ) -> None:
        self._ingest = ingest
        self._pipeline_readiness = pipeline_readiness
        self._deployment_id = deployment_id

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
        """Accept one body through the composed E0 ingest port."""
        upload = DocumentUpload(
            filename=filename, mime=mime, content=content, title=title
        )
        if source_kind is None or source_ref is None:
            return self._ingest.ingest(deployment_id=self._deployment_id, upload=upload)
        return self._ingest.ingest_observed(
            deployment_id=self._deployment_id,
            source_kind=source_kind,
            source_ref=source_ref,
            upload=upload,
            versioning_mode=versioning_mode,
            source_modified_at=source_modified_at,
            source_version_ref=source_version_ref,
            sync_cycle_id=None,
        )

    def pipeline_readiness(
        self, *, version_ids: tuple[UUID, ...], require_projections: bool
    ) -> PipelineReadinessReport:
        """Inspect readiness through the composed pipeline-readiness port."""
        return self._pipeline_readiness.inspect(
            deployment_id=self._deployment_id,
            version_ids=version_ids,
            require_projections=require_projections,
        )

    def max_ingest_body_bytes(self) -> int | None:
        """Local engine has no served body ceiling; do not invent one."""
        return None


class RecipeMcpServer:
    """Render and dispatch recipes, optional write tools, and open-query tools."""

    def __init__(
        self,
        *,
        surface: RecipeSurface,
        open_query: OpenQueryFacade | None = None,
        ingest: IngestPort | None = None,
        pipeline_readiness: PipelineReadinessPort | None = None,
    ) -> None:
        """Bind the MCP server to the shared recipe surface and optional ports.

        Fail closed when both recipe and open-query authorities are composed for
        different deployments — one MCP server is one trust domain (D50). Write
        tools require both ingest and pipeline_readiness; half-wiring either
        port alone is refused so tools/list never advertises a half-broken pair.
        Recipe-only compositions omit the write tools (O2).
        """
        if open_query is not None and open_query.deployment_id != surface.deployment_id:
            raise ValueError(
                "the recipe surface and the open-query facade serve different"
                " deployments — one deployment is one trust domain (D50)"
            )
        if (ingest is None) != (pipeline_readiness is None):
            raise ValueError(
                "ingest and pipeline_readiness must both be composed or both"
                " omitted — half-wired write tools are not allowed"
            )
        self._surface = surface
        self._open_query = open_query
        self._write_backend: _LocalMemoryWriteBackend | None = None
        if ingest is not None and pipeline_readiness is not None:
            self._write_backend = _LocalMemoryWriteBackend(
                ingest=ingest,
                pipeline_readiness=pipeline_readiness,
                deployment_id=surface.deployment_id,
            )

    def list_tools(self) -> dict[str, object]:
        """The `tools/list` result: write tools (when composed), recipes, open-query.

        Each recipe tool carries its name, description, and JSON-Schema
        `inputSchema` — the recipe registry rendered verbatim. Write tools lead
        when both ports are present. When open query is composed, the nine §3.1
        tools are appended. `examples.*` never appear as tools.
        """
        tools: list[dict[str, object]] = []
        if self._write_backend is not None:
            tools.extend(memory_write_tool_descriptors())
        tools.extend(
            {
                "name": descriptor.name,
                "description": descriptor.description,
                "inputSchema": descriptor.input_schema,
            }
            for descriptor in self._surface.descriptors()
        )
        if self._open_query is not None:
            tools.extend(open_query_tool_descriptors())
        return {"tools": tools}

    def call_tool(
        self, *, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        """The `tools/call` result: write, recipe, or open-query infrastructure tool.

        Recipe answers are the D49 envelope as JSON text. Open-query answers
        are QueryResult/v1 or discovery payloads as JSON text. Write tools use
        the structured error envelope. Unknown tools and typed failures are
        protocol error results (`isError`), never exceptions across the wire.
        """
        if name in MEMORY_WRITE_TOOL_NAMES:
            return handle_memory_write_tool(
                name=name, arguments=arguments, backend=self._write_backend
            )
        if name in OPEN_QUERY_TOOL_NAMES:
            if self._open_query is None:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"open-query tool {name!r} is not composed",
                        }
                    ],
                    "isError": True,
                }
            try:
                payload = dispatch_open_query_tool(
                    facade=self._open_query, name=name, arguments=arguments
                )
            except (SandboxRejection, ValueError, TypeError, KeyError) as error:
                return {
                    "content": [{"type": "text", "text": str(error)}],
                    "isError": True,
                }
            return {
                "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
                "isError": False,
            }
        try:
            envelope = self._surface.run(name=name, arguments=arguments)
        except (
            UnknownRecipeError,
            MissingArgumentError,
            InvalidArgumentError,
        ) as error:
            return {"content": [{"type": "text", "text": str(error)}], "isError": True}
        return {
            "content": [{"type": "text", "text": envelope.model_dump_json()}],
            "isError": False,
        }
