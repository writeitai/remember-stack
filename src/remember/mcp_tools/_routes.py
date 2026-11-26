"""The route tools: one public HTTP route each, called through the SDK.

Each tool's arguments are parsed into one closed model here (the bounds of
the route's own request model), then sent with :class:`MemoryClient` and the
route's JSON answer returned unchanged. Only a host that reaches the engine
over HTTP serves them (``remember mcp``); the catalogue lists them so every
public route has a tool.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Annotated
from typing import cast
from typing import Self
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator
from pydantic import ValidationError

from remember.mcp_tools._definitions import RESOLVE_CONTEXT_MAX
from remember.mcp_tools._definitions import ROUTE_TOOL_NAMES
from remember.mcp_tools._definitions import SET_REFERENCES_MAX
from remember.mcp_tools._definitions import tool
from remember.mcp_tools._errors import error_result
from remember.mcp_tools._errors import invalid_arguments
from remember.mcp_tools._errors import map_error
from remember.mcp_tools._errors import ToolArgumentError
from remember.mcp_tools._errors import ToolError
from remember.mcp_tools._memory import reject_unknown_keys
from remember.models import DocumentStatusFilter
from remember.models import EFFECTIVE_PERIODS_MAX
from remember.models import EffectivePeriodInput
from remember.models import EffectivePeriodsRequest
from remember.models import ReferenceInput
from remember.models import SearchRequest
from remember.models import UTCInstant

if TYPE_CHECKING:
    from remember.client import MemoryClient

logger = logging.getLogger(__name__)

#: 404 details that name what was not found; the tool keeps them as its code.
_NOT_FOUND: frozenset[str] = frozenset(
    {"document_not_found", "version_not_found", "forget_not_found"}
)

_Predicate = Annotated[str, Field(min_length=1, max_length=200)]
_Cursor = Annotated[str, Field(min_length=1)]


class _Arguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _ListDocuments(_Arguments):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: _Cursor | None = None
    status: DocumentStatusFilter | None = None
    include_archived: bool = False


class _ListForgets(_Arguments):
    pass


class _GetForget(_Arguments):
    forget_id: UUID


class _ProcessingLayers(_Arguments):
    pass


class _SearchClaims(SearchRequest):
    pass


class _SearchChunks(SearchRequest):
    pass


class _ResolveEntityInContext(_Arguments):
    name: str = Field(min_length=1, max_length=512)
    context_entity_ids: tuple[UUID, ...] = Field(
        min_length=1, max_length=RESOLVE_CONTEXT_MAX
    )


class _LookupRelations(_Arguments):
    subject_entity_id: UUID | None = None
    predicate: _Predicate | None = None
    object_entity_id: UUID | None = None
    valid_at: UTCInstant | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: _Cursor | None = None


class _LookupObservations(_Arguments):
    entity_id: UUID
    property_query: str | None = Field(default=None, min_length=1, max_length=4096)
    k: int = Field(default=10, ge=1, le=400)
    limit: int = Field(default=50, ge=1, le=200)
    cursor: _Cursor | None = None

    @model_validator(mode="after")
    def _ranked_or_paged(self) -> Self:
        if self.property_query is not None and self.cursor is not None:
            raise ValueError("cursor pages lookups without property_query; drop one")
        return self


class _HydrateRelation(_Arguments):
    relation_id: UUID


class _TranscriptRelation(_Arguments):
    relation_id: UUID


class _GraphClocks(_Arguments):
    predicates: tuple[_Predicate, ...] = Field(default=(), max_length=100)
    valid_at: UTCInstant | None = None
    believed_at: UTCInstant | None = None

    @model_validator(mode="after")
    def _both_clocks_or_neither(self) -> Self:
        if (self.valid_at is None) != (self.believed_at is None):
            raise ValueError("valid_at and believed_at must be supplied together")
        return self


class _GraphNeighborhood(_GraphClocks):
    entity_id: UUID
    hops: int = Field(default=2, ge=1, le=4)
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    include_paths: bool = False


class _GraphPath(_GraphClocks):
    from_entity_id: UUID
    to_entity_id: UUID
    max_hops: int = Field(default=4, ge=1, le=6)


class _GraphCitationPath(_Arguments):
    from_doc_id: UUID
    to_doc_id: UUID
    max_hops: int = Field(default=6, ge=1, le=6)


class _ReferenceGenerations(_Arguments):
    doc_id: UUID
    version_id: UUID


class _SetReferences(_Arguments):
    doc_id: UUID
    version_id: UUID
    references: tuple[ReferenceInput, ...] = Field(max_length=SET_REFERENCES_MAX)


class _SetEffectivePeriods(_Arguments):
    doc_id: UUID
    version_id: UUID
    periods: tuple[EffectivePeriodInput, ...] = Field(max_length=EFFECTIVE_PERIODS_MAX)

    @model_validator(mode="after")
    def _distinct_starts(self) -> Self:
        EffectivePeriodsRequest(periods=self.periods)
        return self


class _ClearEffectiveTime(_Arguments):
    doc_id: UUID


_MODELS: dict[str, type[BaseModel]] = {
    "list_documents": _ListDocuments,
    "list_forgets": _ListForgets,
    "get_forget": _GetForget,
    "processing_layers": _ProcessingLayers,
    "search_claims": _SearchClaims,
    "search_chunks": _SearchChunks,
    "resolve_entity_in_context": _ResolveEntityInContext,
    "lookup_relations": _LookupRelations,
    "lookup_observations": _LookupObservations,
    "hydrate_relation": _HydrateRelation,
    "transcript_relation": _TranscriptRelation,
    "graph_neighborhood": _GraphNeighborhood,
    "graph_path": _GraphPath,
    "graph_citation_path": _GraphCitationPath,
    "reference_generations": _ReferenceGenerations,
    "set_references": _SetReferences,
    "set_effective_periods": _SetEffectivePeriods,
    "clear_effective_time": _ClearEffectiveTime,
}
assert set(_MODELS) == set(ROUTE_TOOL_NAMES)


def parse_route_tool_arguments(
    *, name: str, arguments: Mapping[str, object]
) -> BaseModel:
    """Validate one route tool's arguments into its closed model.

    Unknown keys and missing required keys are refused first, in the words
    every catalogue validator uses; then every value is checked against the
    route's bounds.
    """
    schema = tool(name).input_schema
    reject_unknown_keys(
        arguments=arguments,
        allowed=set(cast("dict[str, object]", schema["properties"])),
    )
    missing = sorted(
        set(cast("list[str]", schema.get("required", []))) - set(arguments)
    )
    if missing:
        raise ToolArgumentError(
            error=invalid_arguments(
                detail=f"Missing required arguments: {', '.join(missing)}."
            )
        )
    try:
        return _MODELS[name].model_validate(dict(arguments))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'arguments'}:"
            f" {item['msg']}"
            for item in error.errors(include_url=False)
        )
        raise ToolArgumentError(
            error=invalid_arguments(detail=f"Invalid {name} arguments: {problems}")
        ) from error


def handle_route_tool(
    *, name: str, arguments: Mapping[str, object], client: MemoryClient
) -> dict[str, object]:
    """Run one route tool through the SDK; every failure is a tool error."""
    try:
        request = parse_route_tool_arguments(name=name, arguments=arguments)
        text = _call(client=client, request=request).model_dump_json()
    except ToolArgumentError as error:
        return error_result(error.error)
    except Exception as error:  # noqa: BLE001 — mapped at the MCP wire boundary
        detail = getattr(error, "detail", None)
        if getattr(error, "status_code", None) == 404 and detail in _NOT_FOUND:
            return error_result(
                ToolError(
                    code=str(detail),
                    detail=f"{detail}: the id is unknown, deleted or forgotten.",
                    status_code=404,
                    retryable=False,
                    agent_action="Do not retry. Check the id.",
                )
            )
        mapped = map_error(error)
        if mapped.code in {"internal_error", "local_backend_error"}:
            logger.exception("MCP tool %s failed with %s", name, mapped.code)
        return error_result(mapped)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _call(*, client: MemoryClient, request: BaseModel) -> BaseModel:
    """Send the parsed arguments to the tool's route."""
    match request:
        case _ListDocuments():
            return client.list_documents(
                limit=request.limit,
                cursor=request.cursor,
                status=request.status,
                include_archived=request.include_archived,
            )
        case _ListForgets():
            return client.list_forgets()
        case _GetForget():
            return client.get_forget(forget_id=request.forget_id)
        case _ProcessingLayers():
            return client.processing_layers()
        case _SearchClaims() | _SearchChunks():
            search = (
                client.search_claims
                if isinstance(request, _SearchClaims)
                else client.search_chunks
            )
            return search(
                query=request.query,
                k=request.k,
                channel=request.channel,
                documents=request.documents,
                time=request.time,
            )
        case _ResolveEntityInContext():
            return client.resolve(
                name=request.name, context_entity_ids=request.context_entity_ids
            )
        case _LookupRelations():
            return client.lookup_relations(
                subject_entity_id=request.subject_entity_id,
                predicate=request.predicate,
                object_entity_id=request.object_entity_id,
                valid_at=request.valid_at,
                limit=request.limit,
                cursor=request.cursor,
            )
        case _LookupObservations():
            return client.lookup_observations(
                entity_id=request.entity_id,
                property_query=request.property_query,
                k=request.k,
                limit=request.limit,
                cursor=request.cursor,
            )
        case _HydrateRelation():
            return client.hydrate_relation(relation_id=request.relation_id)
        case _TranscriptRelation():
            return client.transcript_relation(relation_id=request.relation_id)
        case _GraphNeighborhood():
            return client.graph_neighborhood(
                entity_id=request.entity_id,
                hops=request.hops,
                predicates=request.predicates,
                valid_at=request.valid_at,
                believed_at=request.believed_at,
                limit=request.limit,
                cursor=request.cursor,
                include_paths=request.include_paths,
            )
        case _GraphPath():
            return client.graph_path(
                from_entity_id=request.from_entity_id,
                to_entity_id=request.to_entity_id,
                max_hops=request.max_hops,
                predicates=request.predicates,
                valid_at=request.valid_at,
                believed_at=request.believed_at,
            )
        case _GraphCitationPath():
            return client.graph_citation_path(
                from_doc_id=request.from_doc_id,
                to_doc_id=request.to_doc_id,
                max_hops=request.max_hops,
            )
        case _ReferenceGenerations():
            return client.reference_generations(
                doc_id=request.doc_id, version_id=request.version_id
            )
        case _SetReferences():
            return client.set_references(
                doc_id=request.doc_id,
                version_id=request.version_id,
                references=request.references,
            )
        case _SetEffectivePeriods():
            return client.set_effective_periods(
                doc_id=request.doc_id,
                version_id=request.version_id,
                periods=request.periods,
            )
        case _ClearEffectiveTime():
            return client.clear_effective_time(doc_id=request.doc_id)
    raise AssertionError(f"no route for {type(request).__name__}")
