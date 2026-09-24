"""The engine's in-process MCP surface (retrieval §7, D50, D136).

Every tool renders from the shared catalogue in `remember.mcp_tools`, the same
definitions `remember mcp` renders. The four assured operations are always
listed; the seven open-query tools when an `OpenQueryFacade` is composed; the
Layer 1 write tools (`ingest`, `pipeline_readiness`) when both their ports are
composed; and `delete_document` when a deletion port is composed (D135). The
engine does not run on the caller's machine, so `ingest` never offers the local
`path` body source here. The eighteen `examples.*` identities are never
top-level tools — they run only through `run_saved_query`.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from datetime import datetime
import json
from typing import Any
from typing import cast
from typing import Literal
from uuid import UUID

from remember.mcp_tools import DELETE_DOCUMENT_TOOL_NAME
from remember.mcp_tools import error_result
from remember.mcp_tools import handle_delete_document_tool
from remember.mcp_tools import handle_memory_write_tool
from remember.mcp_tools import INGEST_TOOL_NAME
from remember.mcp_tools import invalid_arguments
from remember.mcp_tools import map_error
from remember.mcp_tools import MEMORY_WRITE_TOOL_NAMES
from remember.mcp_tools import OPEN_QUERY_TOOL_NAMES
from remember.mcp_tools import OPERATION_TOOL_NAMES
from remember.mcp_tools import PIPELINE_READINESS_TOOL_NAME
from remember.mcp_tools import render_tools_list
from remember.mcp_tools import tool
from remember.mcp_tools import ToolError
from remember.mcp_tools import validate_arguments
from rememberstack.model import ForgetInProgressError
from rememberstack.model.client import DocumentDeletion
from rememberstack.model.client import PipelineReadinessReport
from rememberstack.model.client import ReadinessRequirements
from rememberstack.model.documents import DocumentNotFoundError
from rememberstack.model.documents import DocumentUpload
from rememberstack.model.documents import IngestedVersion
from rememberstack.surfaces.http_api import DocumentDeletionPort
from rememberstack.surfaces.http_api import IngestPort
from rememberstack.surfaces.http_api import PipelineReadinessPort
from rememberstack.surfaces.operation_surface import InvalidArgumentError
from rememberstack.surfaces.operation_surface import MissingArgumentError
from rememberstack.surfaces.operation_surface import OperationSurface
from rememberstack.surfaces.operation_surface import UnknownOperationError
from rememberstack.surfaces.query_sandbox.discovery import (
    query_space_description_payload,
)
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.open_query import OpenQueryFacade


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
        self, *, version_ids: tuple[UUID, ...], require: ReadinessRequirements
    ) -> PipelineReadinessReport:
        """Inspect readiness through the composed pipeline-readiness port."""
        return self._pipeline_readiness.inspect(
            deployment_id=self._deployment_id, version_ids=version_ids, require=require
        )

    def max_ingest_body_bytes(self) -> int | None:
        """Local engine has no served body ceiling; do not invent one."""
        return None


class _LocalDocumentDeleteBackend:
    """Adapt the in-process deletion port to the shared delete tool (D135)."""

    def __init__(self, *, deletion: DocumentDeletionPort, deployment_id: UUID) -> None:
        self._deletion = deletion
        self._deployment_id = deployment_id

    def delete_document(self, *, doc_id: UUID) -> DocumentDeletion:
        """Delete through the composed port; absence surfaces as a 404 shape."""
        try:
            return self._deletion.delete_document(
                deployment_id=self._deployment_id, doc_id=doc_id
            )
        except DocumentNotFoundError as error:
            raise _DocumentNotFound() from error
        except ForgetInProgressError as error:
            # the same stable, retryable negative the HTTP route answers
            raise _ForgetInProgress() from error


class _DocumentNotFound(Exception):
    """The HTTP-shaped absence the shared tool maps to ``document_not_found``."""

    status_code = 404
    detail = "document_not_found"


class _ForgetInProgress(Exception):
    """The HTTP-shaped D74 barrier the shared tool maps to ``forget_in_progress``."""

    status_code = 503
    detail = "forget_in_progress"


class OperationMcpServer:
    """Render assured operations, optional write tools, and open-query tools."""

    def __init__(
        self,
        *,
        surface: OperationSurface,
        open_query: OpenQueryFacade | None = None,
        ingest: IngestPort | None = None,
        pipeline_readiness: PipelineReadinessPort | None = None,
        deletion: DocumentDeletionPort | None = None,
    ) -> None:
        """Bind the MCP server to the operation surface and optional ports.

        Fail closed when operation and open-query authorities are composed for
        different deployments — one MCP server is one trust domain (D50). Write
        tools require both ingest and pipeline_readiness; half-wiring either
        port alone is refused so tools/list never advertises a half-broken pair.
        Operation-only compositions omit the write tools (O2).
        `delete_document` is advertised only when `deletion` is composed.
        """
        if open_query is not None and open_query.deployment_id != surface.deployment_id:
            raise ValueError(
                "the operation surface and the open-query facade serve different"
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
        self._delete_backend: _LocalDocumentDeleteBackend | None = (
            None
            if deletion is None
            else _LocalDocumentDeleteBackend(
                deletion=deletion, deployment_id=surface.deployment_id
            )
        )

    def list_tools(self) -> dict[str, object]:
        """List the composed catalogue tools, in catalogue order.

        Write tools lead when both ports are composed, then `delete_document`
        when deletion is composed, the four assured operations, and the seven
        §3.1 tools when open query is composed. `examples.*` never appear as
        top-level tools.
        """
        names: list[str] = []
        if self._write_backend is not None:
            names.extend((INGEST_TOOL_NAME, PIPELINE_READINESS_TOOL_NAME))
        if self._delete_backend is not None:
            names.append(DELETE_DOCUMENT_TOOL_NAME)
        names.extend(OPERATION_TOOL_NAMES)
        if self._open_query is not None:
            names.extend(OPEN_QUERY_TOOL_NAMES)
        return {
            "tools": render_tools_list(
                [tool(name) for name in names],
                project=False,
                path_ingest=False,
                read_only=False,
            )
        }

    def call_tool(
        self, *, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        """Run a write, assured-operation, or open-query tool.

        Operation answers use their declared contract as JSON text. Open-query
        answers are QueryResult/v1 or discovery payloads. Every failure is the
        catalogue's one structured error envelope.
        """
        if name == DELETE_DOCUMENT_TOOL_NAME:
            return handle_delete_document_tool(
                arguments=arguments, backend=self._delete_backend
            )
        if name in MEMORY_WRITE_TOOL_NAMES:
            return handle_memory_write_tool(
                name=name,
                arguments=arguments,
                backend=self._write_backend,
                path_ingest=False,
            )
        if name in OPEN_QUERY_TOOL_NAMES:
            if self._open_query is None:
                return error_result(
                    ToolError(
                        code="tool_not_composed",
                        detail=f"open-query tool {name!r} is not composed here.",
                        status_code=None,
                        retryable=False,
                        agent_action="Use a tool from tools/list.",
                    )
                )
            try:
                payload = _run_open_query(
                    facade=self._open_query, name=name, arguments=arguments
                )
            except (SandboxRejection, ValueError) as error:
                return error_result(map_error(error))
            return {
                "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
                "isError": False,
            }
        try:
            result = self._surface.run(name=name, arguments=arguments)
        except UnknownOperationError as error:
            return error_result(
                ToolError(
                    code="unknown_tool",
                    detail=str(error),
                    status_code=None,
                    retryable=False,
                    agent_action="Call tools/list and use one of the listed tools.",
                )
            )
        except (MissingArgumentError, InvalidArgumentError) as error:
            return error_result(invalid_arguments(detail=str(error)))
        return {
            "content": [{"type": "text", "text": result.model_dump_json()}],
            "isError": False,
        }


def _run_open_query(
    *, facade: OpenQueryFacade, name: str, arguments: Mapping[str, object]
) -> object:
    """Validate one open-query call against the catalogue and run it.

    Returns a JSON-serializable payload (a QueryResult dump or description
    dicts). Raises SandboxRejection or ValueError for typed failures.
    """
    args = validate_arguments(name, arguments)
    parameters = cast("Sequence[object]", args.get("parameters", ()))
    max_rows = cast("int | None", args.get("max_rows"))
    version = cast("int | None", args.get("version"))
    namespace = cast("str | None", args.get("namespace"))
    if name == "query_sql":
        return _query_result_payload(
            facade.query_sql(
                sql=cast(str, args["sql"]), parameters=parameters, max_rows=max_rows
            )
        )
    if name == "explain_sql":
        return _query_result_payload(
            facade.explain_sql(sql=cast(str, args["sql"]), parameters=parameters)
        )
    if name == "describe_query_space":
        description = facade.describe_query_space(
            pattern=cast("str | None", args["pattern"]),
            include_examples=cast(bool, args["include_examples"]),
        )
        return query_space_description_payload(description)
    if name == "search_query_space":
        hits = facade.search_query_space(
            query=cast(str, args["query"]), k=cast(int, args["k"])
        )
        return [
            {
                "kind": hit.kind,
                "name": hit.name,
                "score": hit.score,
                "purpose": hit.purpose,
                "tags": list(hit.tags),
            }
            for hit in hits
        ]
    if name == "list_saved_queries":
        rows = facade.list_saved_queries(
            namespace=namespace, status=cast("str | None", args["status"])
        )
        return [
            {
                "query_id": str(row.query_id),
                "namespace": row.namespace,
                "name": row.name,
                "version": row.version,
                "status": row.status,
                "description": row.description,
                "origin": row.origin,
                "assurance": row.assurance,
                "query_hash": row.query_hash,
                "validated_surface_manifest_hash": row.validated_surface_manifest_hash,
            }
            for row in rows
        ]
    if name == "describe_saved_query":
        detail = facade.describe_saved_query(
            namespace=cast(str, namespace),
            name=cast(str, args["name"]),
            version=version,
        )
        return {
            "query_id": str(detail.query_id),
            "namespace": detail.namespace,
            "name": detail.name,
            "version": detail.version,
            "status": detail.status,
            "description": detail.description,
            "origin": detail.origin,
            "assurance": detail.assurance,
            "sql": detail.sql,
            "query_hash": detail.query_hash,
            "parameter_schema": detail.parameter_schema,
            "declared_result_schema": detail.declared_result_schema,
            "declared_interpretation": detail.declared_interpretation,
            "query_space_major": detail.query_space_major,
            "default_limits": detail.default_limits,
            "validated_surface_manifest_hash": detail.validated_surface_manifest_hash,
            "validation_report": detail.validation_report,
            "author_principal": detail.author_principal,
            "approver_principal": detail.approver_principal,
        }
    if name == "run_saved_query":
        return _query_result_payload(
            facade.run_saved_query(
                namespace=cast(str, namespace),
                name=cast(str, args["name"]),
                version=version,
                parameters=parameters,
                max_rows=max_rows,
            )
        )
    raise ValueError(f"unknown open-query tool {name!r}")


def _query_result_payload(result: Any) -> dict[str, Any]:
    """Serialize a QueryResult for MCP text content."""
    return cast("dict[str, Any]", result.model_dump(mode="json"))
